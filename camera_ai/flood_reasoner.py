import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import json
import base64
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import cv2
import numpy as np
import requests

import torch
import torchvision.transforms as tf
from PIL import Image

# Ensure compatibility for smp LinkNet unpickling if needed
try:
    import segmentation_models_pytorch as smp
    if hasattr(smp, "decoders") and hasattr(smp.decoders, "linknet"):
        sys.modules.setdefault("segmentation_models_pytorch.linknet", smp.decoders.linknet)
        sys.modules.setdefault("segmentation_models_pytorch.linknet.model", smp.decoders.linknet.model)
except ImportError:
    smp = None


DEFAULT_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-3.7-flash"
]
GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class FloodReasoner:
    """
    SOTA Flood Reasoning & Water Surface Segmentation Engine:
    Tầng 1: Deep Learning SOTA Water Segmenter (LinkNet EfficientNet-B4)
    Tầng 2: Road-Corridor Geometric Filtering (M_road ∩ M_water)
    Tầng 3: Multi-modal Vision-Language Cross Validation (Google Gemini Flash)
    Tầng 4: Heuristic Specular Reflection & Sun Glare Filter (Offline Fallback)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "gemini-2.5-flash",
        weights_path: Optional[str] = None,
        device: Optional[str] = None
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.default_model = default_model
        self._temporal_ratios: Dict[str, float] = {}
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        # -------------------------------------------------------------
        # KHỞI TẠO MÔ HÌNH DEEP LEARNING SOTA PHÂN ĐOẠN NƯỚC (LinkNet-B4)
        # -------------------------------------------------------------
        self.dl_model = None
        self.input_dims = (768, 768)
        self.transform_norm = tf.Compose([
            tf.Resize(self.input_dims),
            tf.ToTensor(),
            tf.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])

        curr_dir = Path(__file__).resolve().parent
        candidate_weights = [
            weights_path,
            str(curr_dir.parent / "weights" / "linknet_water_clean.pth"),
            "weights/linknet_water_clean.pth",
            str(curr_dir.parent / "weights" / "link_efficientb4_model.pth"),
            str(curr_dir.parent / "flood_detector" / "records" / "link_efficientb4_model.pth"),
        ]

        resolved_weight = None
        for w in candidate_weights:
            if w and Path(w).exists():
                resolved_weight = str(Path(w).resolve())
                break

        if resolved_weight and smp is not None:
            try:
                print(f"[FloodReasoner] Loading SOTA Deep Learning Water Segmenter: {resolved_weight} ({self.device})...")
                self.dl_model = smp.Linknet(encoder_name="efficientnet-b4", classes=1, in_channels=3).to(self.device)
                loaded_data = torch.load(resolved_weight, weights_only=False, map_location=self.device)
                if isinstance(loaded_data, dict):
                    self.dl_model.load_state_dict(loaded_data)
                elif hasattr(loaded_data, "state_dict"):
                    self.dl_model.load_state_dict(loaded_data.state_dict())
                self.dl_model.eval()
                print("[FloodReasoner] LinkNet SOTA Water Segmenter initialized successfully!")
            except Exception as e:
                print(f"[FloodReasoner] Warning loading LinkNet DL: {e}. Using Heuristic CV fallback.")
                self.dl_model = None
        else:
            print("[FloodReasoner] LinkNet weights not found. Using optimized Heuristic CV mode.")

    def reset_temporal_state(self, camera_id: Optional[str] = None):
        if camera_id:
            self._temporal_ratios.pop(camera_id, None)
        else:
            self._temporal_ratios.clear()

    def _encode_cv2_image_to_base64(self, img_bgr: np.ndarray, quality: int = 85) -> str:
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        success, buffer = cv2.imencode(".jpg", img_bgr, encode_params)
        if not success:
            raise RuntimeError("Failed to encode image to JPEG buffer.")
        return base64.b64encode(buffer).decode("utf-8")

    def _call_gemini_vision(self, img_bgr: np.ndarray) -> Dict[str, Any]:
        """
        Gọi Gemini Vision với cấu trúc JSON Schema chuẩn.
        """
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")

        b64_img = self._encode_cv2_image_to_base64(img_bgr)
        prompt_text = (
            "Bạn là chuyên gia phân tích camera giao thông đô thị và ngập lụt đường phố.\n"
            "Hãy quan sát vùng mặt đường được cung cấp và xác định tình trạng ngập nước mặt đường.\n"
            "LƯU Ý CỰC KỲ QUAN TRỌNG ĐỂ TRÁNH DƯƠNG TÍNH GIẢ (FALSE POSITIVE):\n"
            "1. Vệt ánh sáng mặt trời chiếu chói (sun glare / phản quang nắng) hoặc mặt đường nhựa chỉ hơi ẩm sau mưa KHÔNG PHẢI là ngập nước. "
            "Nếu các vạch kẻ sơn đường (vạch vàng, vạch đi bộ, chữ viết) vẫn nhìn thấy rõ nét trên mặt đường thì mặt đường KHÔNG NGẬP (is_flooded = false, severity = 'none').\n"
            "2. Nhìn vào lốp xe của các ô tô, xe máy đang đỗ hoặc chạy: nếu bánh xe tiếp xúc mặt đường bình thường, không bị chìm nước thì KHÔNG PHẢI ngập nước.\n"
            "3. Chỉ đánh giá is_flooded = true khi có vũng nước sâu đọng lại, có gợn sóng nước hoặc bánh xe bị ngập chìm rõ rệt.\n"
            "Chỉ trả về kết quả theo đúng định dạng JSON Schema được yêu cầu."
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": b64_img,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "OBJECT",
                    "properties": {
                        "is_flooded": {
                            "type": "BOOLEAN",
                            "description": "Mặt đường hoặc làn đường có đang bị ngập nước hoặc đọng nước sâu không",
                        },
                        "severity": {
                            "type": "STRING",
                            "enum": ["none", "minor", "moderate", "severe"],
                            "description": "none: khô ráo; minor: nước xâm xấp mép; moderate: ngập nửa bánh xe; severe: ngập sâu nguy hiểm",
                        },
                        "safe_for_bikes": {
                            "type": "BOOLEAN",
                            "description": "Đường còn an toàn cho xe máy và xe đạp lưu thông không",
                        },
                        "confidence": {
                            "type": "NUMBER",
                            "description": "Độ tin cậy của đánh giá từ 0.0 đến 1.0",
                        },
                        "reason": {
                            "type": "STRING",
                            "description": "Giải thích ngắn gọn dấu hiệu nhận biết (mặt nước, độ sâu ngập đến đâu, phương tiện)",
                        },
                    },
                    "required": ["is_flooded", "severity", "safe_for_bikes", "confidence", "reason"],
                },
            },
        }

        candidate_models = [self.default_model] + [m for m in DEFAULT_GEMINI_MODELS if m != self.default_model]
        headers = {"Content-Type": "application/json"}
        last_err = None

        for m in candidate_models:
            clean_m = m.replace("models/", "")
            url = f"{GEMINI_API_ENDPOINT.format(model=clean_m)}?key={self.api_key}"
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=20)
                if resp.status_code == 503 or resp.status_code == 404:
                    continue
                if not resp.ok:
                    last_err = f"HTTP {resp.status_code}: {resp.text}"
                    continue
                data = resp.json()
                raw_json = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(raw_json)
            except Exception as e:
                last_err = str(e)
                continue

        raise RuntimeError(f"Gemini API request failed across models: {last_err}")

    def _dl_water_segmentation(self, frame: np.ndarray) -> np.ndarray:
        """
        Chạy mạng nơ-ron LinkNet (EfficientNet-B4 SOTA) với Test-Time Augmentation (TTA)
        để phân đoạn mặt nước mịn màng, liền mạch trên toàn khung hình.
        """
        orig_h, orig_w = frame.shape[:2]
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(img_rgb)

        t1 = self.transform_norm(img_pil).unsqueeze(0).to(self.device)
        t2 = self.transform_norm(img_pil.transpose(Image.FLIP_LEFT_RIGHT)).unsqueeze(0).to(self.device)

        with torch.no_grad():
            p1 = torch.sigmoid(self.dl_model(t1))
            p1 = tf.Resize([orig_h, orig_w])(p1).squeeze().cpu().numpy()
            p2 = torch.sigmoid(self.dl_model(t2))
            p2 = tf.Resize([orig_h, orig_w])(p2).squeeze().cpu().numpy()[:, ::-1]

        prob = (p1 + p2) / 2.0
        binary_water = (prob > 0.32).astype(np.uint8) * 255

        # Tái cấu trúc màng nước liền mạch (Morphological Puddle Closure)
        kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
        closed = cv2.morphologyEx(binary_water, cv2.MORPH_CLOSE, kernel_close)

        # Lọc sạch hoàn toàn các đốm nhiễu lốm đốm nhỏ (< 500 px)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(closed)
        cleaned = np.zeros_like(closed)
        min_puddle_area = max(500, int(orig_h * orig_w * 0.0006))
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] >= min_puddle_area:
                cleaned[labels == i] = 255

        return cleaned

    def _local_cv_road_reasoning(
        self,
        frame: np.ndarray,
        road_mask: np.ndarray
    ) -> Tuple[Dict[str, Any], np.ndarray, float]:
        """
        Suy luận ngập kết hợp giữa Deep Learning và Heuristic Computer Vision
        bên trong hành lang lòng đường (Drivable Road Corridor).
        """
        h, w = frame.shape[:2]
        road_binary = (road_mask > 0).astype(np.uint8)
        road_pixel_count = int(np.sum(road_binary))

        if road_pixel_count == 0:
            road_binary = np.zeros((h, w), dtype=np.uint8)
            road_binary[int(h * 0.45):, :] = 1
            road_pixel_count = int(np.sum(road_binary))

        # 1. NHÁNH DEEP LEARNING SOTA (LinkNet-B4)
        dl_water_on_road = np.zeros((h, w), dtype=np.uint8)
        if self.dl_model is not None:
            try:
                dl_water_full = self._dl_water_segmentation(frame)
                dl_water_on_road = cv2.bitwise_and(dl_water_full, dl_water_full, mask=road_binary)
            except Exception as e:
                print(f"[FloodReasoner] Deep Learning inference error: {e}. Falling back to Spectral CV.")

        # 2. NHÁNH QUANG PHỔ & ĐỘ PHẲNG MẶT NƯỚC (Spectral & Ripple Reasoner)
        b, g, r = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h_c, s_c, v_c = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        lap = np.abs(cv2.Laplacian(gray, cv2.CV_64F))

        ambient_brightness = float(np.mean(frame[:max(1, int(h * 0.35)), :, :]))
        is_night = ambient_brightness < 48.0

        if is_night:
            night_water = (v_c > 50) & (s_c < 160) & (b.astype(int) + g.astype(int) + r.astype(int) > 120) & (v_c < 252)
            spec_candidates = np.uint8(night_water) * 255
        else:
            # Mặt nước phản xạ bầu trời (Sky reflection)
            c1_sky = (b.astype(int) > r.astype(int) + 3) & (s_c < 80) & (v_c > 65) & (v_c < 245)
            # Nước mưa đô thị / nước bùn ngập đường (Murky urban stormwater floodwater)
            c2_murky = (h_c >= 10) & (h_c <= 48) & (s_c >= 25) & (s_c <= 160) & (v_c >= 45) & (v_c <= 200) & (lap < 38)
            # Vệt sóng bọt trắng quanh bánh xe rẽ nước (Wake ripples around moving tires)
            c3_wake = (v_c > 120) & (s_c < 70) & (lap >= 18) & (lap <= 80)
            # Phản quang mặt đường ướt (Specular wet pavement reflections)
            c4_specular = (s_c >= 50) & (v_c >= 65) & (lap >= 10) & (lap <= 60)
            # Lọc chói nắng trên mặt đường nhựa khô ráo (Sun glare filter on dry asphalt)
            is_dry_glare = (s_c < 20) & (v_c > 110) & (~c3_wake)

            spec_water = (c1_sky | c2_murky | c3_wake | c4_specular) & (~is_dry_glare)
            spec_candidates = np.uint8(spec_water) * 255

        spec_on_road = cv2.bitwise_and(spec_candidates, spec_candidates, mask=road_binary)

        # 3. HỢP NHẤT ENSEMBLE: Deep Learning là tín hiệu quyết định, Spectral chỉ mở rộng màng nước
        if np.any(dl_water_on_road > 0):
            # Mở rộng vùng nước DL bằng phổ phản xạ lân cận
            dilated_dl = cv2.dilate(dl_water_on_road, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
            spec_confirmed = cv2.bitwise_and(spec_on_road, dilated_dl)
            fused_water_mask = cv2.bitwise_or(dl_water_on_road, spec_confirmed)
        else:
            # Nếu Deep Learning không phát hiện nước (đường khô), không để spectral tự ý tô xanh
            fused_water_mask = np.zeros((h, w), dtype=np.uint8)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        cleaned_water_mask = cv2.morphologyEx(fused_water_mask, cv2.MORPH_CLOSE, kernel)

        water_pixels = int(np.sum(cleaned_water_mask > 0))
        road_water_ratio = float(water_pixels) / float(road_pixel_count) if road_pixel_count > 0 else 0.0

        if road_water_ratio < 0.035:
            res = {
                "is_flooded": False,
                "severity": "none",
                "safe_for_bikes": True,
                "confidence": 0.96,
                "reason": f"Mặt đường khô ráo, không phát hiện đọng nước ngập ({road_water_ratio*100:.1f}% diện tích mặt đường).",
            }
        elif road_water_ratio < 0.18:
            res = {
                "is_flooded": True,
                "severity": "minor",
                "safe_for_bikes": True,
                "confidence": float(min(0.94, 0.82 + road_water_ratio * 1.2)),
                "reason": f"Có nước xâm xấp / đọng nước cục bộ bao phủ {road_water_ratio*100:.1f}% mặt đường (mép/rãnh đường, xe máy đi được an toàn).",
            }
        elif road_water_ratio < 0.45:
            res = {
                "is_flooded": True,
                "severity": "moderate",
                "safe_for_bikes": False,
                "confidence": float(min(0.96, 0.86 + road_water_ratio)),
                "reason": f"Ngập nước vừa bao phủ {road_water_ratio*100:.1f}% hành lang đường, ô tô lội nước tạo sóng, xe máy cần thận trọng.",
            }
        else:
            res = {
                "is_flooded": True,
                "severity": "severe",
                "safe_for_bikes": False,
                "confidence": float(min(0.98, 0.90 + road_water_ratio * 0.2)),
                "reason": f"Ngập sâu diện rộng bao phủ {road_water_ratio*100:.1f}% toàn bộ lòng đường, nước ngập sâu bánh xe nguy hiểm.",
            }

        return res, cleaned_water_mask, road_water_ratio

    def reason(
        self,
        frame: np.ndarray,
        road_mask: np.ndarray,
        use_llm: bool = True,
        source_hint: str = "",
        camera_id: str = "default"
    ) -> Tuple[Dict[str, Any], np.ndarray, float]:
        """
        Thực hiện toàn bộ quy trình suy luận ngập:
        1. Phân đoạn nước (LinkNet Deep Learning / Heuristic)
        2. Tích lũy làm mượt theo thời gian (temporal smoothing)
        3. Lý giải ngữ nghĩa bằng Gemini Flash nếu được yêu cầu
        """
        # Phân đoạn nước chuẩn xác trong lòng đường
        cv_res, water_mask, raw_water_ratio = self._local_cv_road_reasoning(frame, road_mask)

        # Áp dụng cơ chế peak retention / temporal smoothing theo camera ID
        prev_ratio = self._temporal_ratios.get(camera_id, 0.0)
        road_water_ratio = max(raw_water_ratio, prev_ratio * 0.95)
        self._temporal_ratios[camera_id] = road_water_ratio

        # Cập nhật lại phân loại sau khi làm mượt
        if road_water_ratio < 0.035:
            cv_res["is_flooded"] = False
            cv_res["severity"] = "none"
            cv_res["safe_for_bikes"] = True
            cv_res["reason"] = f"Mặt đường khô ráo, không phát hiện đọng nước ngập ({road_water_ratio*100:.1f}% diện tích mặt đường)."
        elif road_water_ratio < 0.18:
            cv_res["is_flooded"] = True
            cv_res["severity"] = "minor"
            cv_res["safe_for_bikes"] = True
            cv_res["reason"] = f"Có nước xâm xấp / đọng nước cục bộ bao phủ {road_water_ratio*100:.1f}% mặt đường (mép/rãnh đường, xe máy đi được an toàn)."
        elif road_water_ratio < 0.45:
            cv_res["is_flooded"] = True
            cv_res["severity"] = "moderate"
            cv_res["safe_for_bikes"] = False
            cv_res["reason"] = f"Ngập nước vừa bao phủ {road_water_ratio*100:.1f}% hành lang đường, ô tô lội nước tạo sóng, xe máy cần thận trọng."
        else:
            cv_res["is_flooded"] = True
            cv_res["severity"] = "severe"
            cv_res["safe_for_bikes"] = False
            cv_res["reason"] = f"Ngập sâu diện rộng bao phủ {road_water_ratio*100:.1f}% toàn bộ lòng đường, nước ngập sâu bánh xe nguy hiểm."

        # Nếu có API key và yêu cầu LLM, gọi Gemini Flash để kiểm định chéo
        if use_llm and self.api_key:
            try:
                road_binary = (road_mask > 0).astype(np.uint8)
                masked_view = cv2.bitwise_and(frame, frame, mask=road_binary)
                llm_input = cv2.addWeighted(frame, 0.25, masked_view, 0.75, 0)

                llm_res = self._call_gemini_vision(llm_input)
                fused_confidence = float(round((llm_res.get("confidence", 0.9) * 0.6 + cv_res["confidence"] * 0.4), 2))
                return {
                    "is_flooded": bool(llm_res.get("is_flooded", cv_res["is_flooded"])),
                    "severity": str(llm_res.get("severity", cv_res["severity"])),
                    "safe_for_bikes": bool(llm_res.get("safe_for_bikes", cv_res["safe_for_bikes"])),
                    "confidence": fused_confidence,
                    "reason": f"[Gemini Flash] {llm_res.get('reason', '')} (Nước ngập chiếm {road_water_ratio*100:.1f}% mặt đường).",
                }, water_mask, road_water_ratio
            except Exception as e:
                cv_res["reason"] += f" (Deep Learning: {e})"
                return cv_res, water_mask, road_water_ratio

        return cv_res, water_mask, road_water_ratio
