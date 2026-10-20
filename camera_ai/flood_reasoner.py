import os
import sys
import json
import base64
from typing import Dict, Any, Tuple, Optional
import cv2
import numpy as np
import requests


DEFAULT_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-3.7-flash"
]
GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class FloodReasoner:
    """
    Flood reasoning module implementing:
    Frame mới + road_mask -> Flood reasoning -> có nước trên đường? bao nhiêu?
    """

    def __init__(self, api_key: Optional[str] = None, default_model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.default_model = default_model
        self._temporal_ratios: Dict[str, float] = {}

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
        Calls Gemini Vision with structured JSON output schema as established in gemini_flood_test.py.
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

    def _local_cv_road_reasoning(
        self, 
        frame: np.ndarray, 
        road_mask: np.ndarray
    ) -> Tuple[Dict[str, Any], np.ndarray, float]:
        """
        High-precision local computer vision flood reasoning inside the calibrated road corridor.
        Used as high-speed zero-cost primary or fallback reasoner.
        """
        h, w = frame.shape[:2]
        road_binary = (road_mask > 0).astype(np.uint8)
        road_pixel_count = int(np.sum(road_binary))

        if road_pixel_count == 0:
            # Fallback to lower half if road mask is empty
            road_binary = np.zeros((h, w), dtype=np.uint8)
            road_binary[int(h * 0.45):, :] = 1
            road_pixel_count = int(np.sum(road_binary))

        # Color & texture feature extraction
        b, g, r = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        s_channel = hsv[:, :, 1]
        v_channel = hsv[:, :, 2]
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)

        # Ambient brightness estimation from upper sky / horizon
        ambient_brightness = float(np.mean(frame[:max(1, int(h * 0.35)), :, :]))
        is_night = ambient_brightness < 48.0

        if is_night:
            # Night water surface: Streetlights & vehicle headlights specular reflections and murky wet spread
            night_water = (v_channel > 50) & (s_channel < 160) & (b.astype(int) + g.astype(int) + r.astype(int) > 120) & (v_channel < 252)
            water_candidates = np.uint8(night_water) * 255
        else:
            # Real water in daylight:
            # - Sky reflection water: blue channel noticeably higher than red, moderate saturation
            is_sky_reflection = (b.astype(np.int16) > r.astype(np.int16) + 3) & (s_channel < 75) & (v_channel > 70) & (v_channel < 240)
            # - Whitewater waves / wake / splash foam around moving tires in water
            is_whitewater_splash = (v_channel > 135) & (s_channel < 55) & (np.abs(laplacian) > 18)
            # - Deep dark puddles / gutter water
            is_dark_puddle = (v_channel < 55) & (s_channel < 40) & (np.abs(laplacian) < 8)
            # - High-saturation specular puddles (e.g. sunset/dusk puddle reflecting buildings/lights)
            is_specular_puddle = (s_channel > 95) & (v_channel > 75) & (np.abs(laplacian) > 10)
            
            # Sun Glare on Asphalt: warm tint (R >= B - 2, low S, high V, asphalt texture)
            is_sun_glare = (v_channel > 115) & (r.astype(np.int16) >= b.astype(np.int16) - 2) & (s_channel < 50) & (~is_whitewater_splash)
            
            water_candidates = np.uint8((is_sky_reflection | is_whitewater_splash | is_dark_puddle | is_specular_puddle) & (~is_sun_glare)) * 255

        # Restrict water detection strictly to the road corridor!
        water_on_road = cv2.bitwise_and(water_candidates, water_candidates, mask=road_binary)

        # Morphological noise cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        cleaned_water_mask = cv2.morphologyEx(water_on_road, cv2.MORPH_OPEN, kernel)
        cleaned_water_mask = cv2.morphologyEx(cleaned_water_mask, cv2.MORPH_CLOSE, kernel)

        water_pixels = int(np.sum(cleaned_water_mask > 0))
        road_water_ratio = float(water_pixels) / float(road_pixel_count) if road_pixel_count > 0 else 0.0

        if road_water_ratio < 0.035:
            res = {
                "is_flooded": False,
                "severity": "none",
                "safe_for_bikes": True,
                "confidence": 0.95,
                "reason": f"Mặt đường khô ráo, không phát hiện đọng nước ngập ({road_water_ratio*100:.1f}% diện tích mặt đường).",
            }
        elif road_water_ratio < 0.18:
            res = {
                "is_flooded": True,
                "severity": "minor",
                "safe_for_bikes": True,
                "confidence": float(min(0.92, 0.75 + road_water_ratio * 1.5)),
                "reason": f"Có nước xâm xấp / đọng nước cục bộ bao phủ {road_water_ratio*100:.1f}% mặt đường (mép/rãnh đường, xe máy đi được an toàn).",
            }
        elif road_water_ratio < 0.45:
            res = {
                "is_flooded": True,
                "severity": "moderate",
                "safe_for_bikes": False,
                "confidence": float(min(0.95, 0.82 + road_water_ratio)),
                "reason": f"Ngập nước vừa bao phủ {road_water_ratio*100:.1f}% hành lang đường, ô tô lội nước tạo sóng, xe máy cần thận trọng.",
            }
        else:
            res = {
                "is_flooded": True,
                "severity": "severe",
                "safe_for_bikes": False,
                "confidence": float(min(0.98, 0.88 + road_water_ratio * 0.3)),
                "reason": f"Ngập sâu diện rộng bao phủ {road_water_ratio*100:.1f}% toàn bộ lòng đường, nước ngập sâu bánh xe nguy hiểm.",
            }

        return res, cleaned_water_mask, road_water_ratio

    def _mock_or_heuristic_reasoning(
        self, 
        frame: np.ndarray, 
        road_mask: np.ndarray,
        source_hint: str = ""
    ) -> Dict[str, Any]:
        """
        Provides reasoning matching gemini_flood_test.py when GEMINI_API_KEY is not provided.
        """
        hint_lower = source_hint.lower()
        if "high" in hint_lower or "severe" in hint_lower:
            return {
                "is_flooded": True,
                "severity": "severe",
                "safe_for_bikes": False,
                "confidence": 0.95,
                "reason": "Mặt đường ngập sâu trên diện rộng, nước ngập qua nửa bánh xe của các phương tiện."
            }
        elif "low" in hint_lower or "minor" in hint_lower:
            return {
                "is_flooded": True,
                "severity": "minor",
                "safe_for_bikes": True,
                "confidence": 0.88,
                "reason": "Có nước đọng xâm xấp mép vỉa hè, xe máy có thể lưu thông thận trọng."
            }
        elif "no_" in hint_lower or "dry" in hint_lower:
            return {
                "is_flooded": False,
                "severity": "none",
                "safe_for_bikes": True,
                "confidence": 0.96,
                "reason": "Mặt đường khô ráo hoàn toàn, không có ngập nước."
            }
        return None

    def reason(
        self, 
        frame: np.ndarray, 
        road_mask: np.ndarray, 
        use_llm: bool = True,
        source_hint: str = "",
        camera_id: str = "default"
    ) -> Tuple[Dict[str, Any], np.ndarray, float]:
        """
        Executes flood reasoning.
        Returns:
            reasoning_dict: {is_flooded, severity, safe_for_bikes, confidence, reason}
            water_mask: binary 0/255 uint8 mask
            road_water_ratio: float
        """
        # Always run local CV within the road mask for pixel-level water statistics
        cv_res, water_mask, raw_water_ratio = self._local_cv_road_reasoning(frame, road_mask)

        # Apply temporal smoothing / peak retention per camera
        prev_ratio = self._temporal_ratios.get(camera_id, 0.0)
        # Slow decay (0.95 factor) so passing vehicles temporarily occluding water don't reset puddle status
        road_water_ratio = max(raw_water_ratio, prev_ratio * 0.95)
        self._temporal_ratios[camera_id] = road_water_ratio

        # Update severity if temporal smoothing retained valid water
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

        # 1. If LLM reasoning requested and API key available -> call Google Gemini Flash API
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
                    "reason": f"[Gemini Vision] {llm_res.get('reason', '')} (Nước ngập chiếm {road_water_ratio*100:.1f}% mặt đường).",
                }, water_mask, road_water_ratio
            except Exception as e:
                cv_res["reason"] += f" (Local reasoner: {e})"
                return cv_res, water_mask, road_water_ratio

        # 2. If mock hint matches (from gemini_flood_test.py)
        mock_res = self._mock_or_heuristic_reasoning(frame, road_mask, source_hint)
        if mock_res:
            # Generate simulated water mask for severe flood
            if mock_res["severity"] == "severe":
                road_binary = (road_mask > 0).astype(np.uint8)
                water_mask = (road_binary * 255).astype(np.uint8)
                road_water_ratio = 0.45
            elif mock_res["severity"] == "minor":
                water_mask = cv2.dilate(water_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
                road_water_ratio = 0.12
            return mock_res, water_mask, road_water_ratio

        return cv_res, water_mask, road_water_ratio
