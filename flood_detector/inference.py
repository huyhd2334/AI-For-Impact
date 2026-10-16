"""
Hệ thống Đo lường & Phân đoạn Ngập lụt Đô thị (V-FloodNet Inference Engine)
-------------------------------------------------------------------------
Input: 1 ảnh (đường dẫn tới ảnh bất kỳ)
Output:
  1. Ảnh mask phân đoạn vùng ngập nước (*_water_mask.png)
  2. Ảnh gốc được đè thêm mask bán trong suốt + viền neon + HUD cảnh báo (*_water_overlay.jpg)
  3. Kết quả mức độ sâu ngập ước lượng được (In ra console + File JSON *_flood_result.json)
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

# Xử lý tương thích stdout tiếng Việt trên Windows console
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import cv2
import numpy as np
import torch
import torch.nn as nn
from PIL import Image
import torchvision.transforms as tf

# Tương thích ngược khi unpickle mô hình LinkNet EfficientNet-B4
try:
    import segmentation_models_pytorch as smp
    if hasattr(smp, 'decoders') and hasattr(smp.decoders, 'linknet'):
        sys.modules.setdefault('segmentation_models_pytorch.linknet', smp.decoders.linknet)
        sys.modules.setdefault('segmentation_models_pytorch.linknet.model', smp.decoders.linknet.model)
except ImportError:
    pass

try:
    import efficientnet_pytorch.utils
    if not hasattr(efficientnet_pytorch.utils, 'Identity'):
        efficientnet_pytorch.utils.Identity = nn.Identity
except ImportError:
    pass


class FloodDetector:
    """
    Bộ suy luận nhận diện ngập lụt & ước lượng độ sâu ngập từ 1 bức ảnh.
    """
    # Bảng phân loại mức độ ngập và tác động giao thông
    SEVERITY_LEVELS = [
        {
            "code": 0,
            "name": "LEVEL_0_DRY",
            "title": "Mức 0: Khô ráo / Không ngập",
            "depth_range": [0.0, 5.0],
            "action": "Đường khô ráo, các phương tiện lưu thông an toàn bình thường."
        },
        {
            "code": 1,
            "name": "LEVEL_1_SHALLOW",
            "title": "Mức 1: Đọng nước / Ngập nhẹ (Nước xâm xấp)",
            "depth_range": [5.0, 15.0],
            "action": "Nước ngập mắt cá chân, xâm xấp mép đường. Xe máy cần giảm tốc, tránh trơn trượt."
        },
        {
            "code": 2,
            "name": "LEVEL_2_MODERATE",
            "title": "Mức 2: Ngập vừa (Nửa bánh xe máy / Chạm gầm ô tô)",
            "depth_range": [15.0, 35.0],
            "action": "Nguy cơ nước vào ống pô xe máy và gầm ô tô con. Ô tô gầm thấp hạn chế di chuyển."
        },
        {
            "code": 3,
            "name": "LEVEL_3_DEEP",
            "title": "Mức 3: Ngập sâu (Quá ống xả / Ngập bánh xe ô tô)",
            "depth_range": [35.0, 70.0],
            "action": "Xe máy tê liệt hoàn toàn, nguy cơ thủy kích ô tô rất cao. Cấm ô tô con và xe máy qua lại."
        },
        {
            "code": 4,
            "name": "LEVEL_4_CRITICAL",
            "title": "Mức 4: Ngập nghiêm trọng (Ngập nắp capo / Ngang hông người)",
            "depth_range": [70.0, 150.0],
            "action": "Nước ngập sâu nguy hiểm tính mạng và tài sản. Cần cứu hộ khẩn cấp và sơ tán phương tiện."
        }
    ]

    def __init__(
        self,
        model_path: Optional[str] = None,
        yolo_path: Optional[str] = None,
        device: Optional[str] = None
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        curr_dir = Path(__file__).resolve().parent

        # 1. Tải mô hình Phân đoạn nước (LinkNet EfficientNet-B4)
        if model_path is None:
            default_weights = [
                curr_dir.parent / "weights" / "link_efficientb4_model.pth",
                curr_dir / "weights" / "link_efficientb4_model.pth",
                Path("weights/link_efficientb4_model.pth"),
                curr_dir / "records" / "link_efficientb4_model.pth",
                Path("flood_detector/records/link_efficientb4_model.pth")
            ]
            for w in default_weights:
                if w.exists():
                    model_path = str(w)
                    break

        if not model_path or not os.path.exists(model_path):
            raise FileNotFoundError(f"[FloodDetector] Không tìm thấy file trọng số: {model_path}")

        print(f"[FloodDetector] Đang tải mô hình phân đoạn nước từ: {model_path} ({self.device})...")
        try:
            self.model = torch.load(model_path, weights_only=False, map_location=self.device)
        except TypeError:
            self.model = torch.load(model_path, map_location=self.device)
        
        if hasattr(self.model, "eval"):
            self.model.eval()
        print("[FloodDetector] Mô hình phân đoạn nước đã sẵn sàng!")

        # Chuẩn hóa ảnh theo ImageNet (mean & std)
        self.mean = torch.tensor([0.485, 0.456, 0.406])
        self.std = torch.tensor([0.229, 0.224, 0.225])
        self.input_dims = (416, 416)
        self.transform_norm = tf.Compose([
            tf.Resize([self.input_dims[0], self.input_dims[1]]),
            tf.ToTensor(),
            tf.Normalize(self.mean, self.std)
        ])

        # 2. Tải mô hình YOLO để nhận diện mốc tham chiếu (người, ô tô, xe tải, xe máy)
        self.yolo_model = None
        try:
            from ultralytics import YOLO
            yolo_candidates = [
                yolo_path,
                str(curr_dir.parent / "weights" / "yolov8n.pt"),
                "weights/yolov8n.pt",
                "yolov8n.pt"
            ]
            yolo_candidate = "yolov8n.pt"
            for yc in yolo_candidates:
                if yc and os.path.exists(yc):
                    yolo_candidate = yc
                    break
            self.yolo_model = YOLO(yolo_candidate)
            print(f"[FloodDetector] Đã nạp YOLO ({yolo_candidate}) để đo đạc mốc vật thể chìm trong nước.")
        except Exception as e:
            print(f"[FloodDetector] Không dùng YOLO tham chiếu ({e}). Sẽ đo đạc theo hình học phân đoạn không gian.")

    def _postprocess_mask(self, raw_pred: np.ndarray) -> np.ndarray:
        """
        Lọc nhiễu và trích xuất thành phần liên thông lớn nhất của mặt nước.
        """
        pred_bin = (raw_pred > 0.5).astype(np.uint8)
        num_labels, labels = cv2.connectedComponents(pred_bin)
        if num_labels <= 1:
            return np.zeros_like(pred_bin)

        if num_labels == 2:
            return (labels == 1).astype(np.uint8)

        # Lấy nhãn chiếm diện tích lớn nhất (bỏ qua nhãn nền 0)
        max_label = 1
        max_count = 0
        for lbl in range(1, num_labels):
            count = int(np.sum(labels == lbl))
            if count > max_count:
                max_count = count
                max_label = lbl

        # Nếu có các vùng nước phụ đáng kể (> 5% diện tích vùng chính), giữ lại
        combined_mask = (labels == max_label).astype(np.uint8)
        threshold_count = max_count * 0.05
        for lbl in range(1, num_labels):
            if lbl != max_label:
                count = int(np.sum(labels == lbl))
                if count >= threshold_count:
                    combined_mask |= (labels == lbl).astype(np.uint8)

        # Đóng hình thái học để làm mịn viền
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        refined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
        return refined_mask

    def segment_water(self, img_bgr: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Phân đoạn vùng nước từ ảnh BGR gốc.
        Trả về:
            - water_mask: mảng nhị phân uint8 (0 hoặc 1) cùng kích thước ảnh gốc.
            - water_ratio: tỷ lệ diện tích ngập nước trên toàn khung hình (0.0 đến 1.0).
        """
        orig_h, orig_w = img_bgr.shape[:2]
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(img_rgb)

        img_tensor = self.transform_norm(img_pil).unsqueeze(0).to(self.device)

        with torch.no_grad():
            if hasattr(self.model, "predict"):
                pred_tensor = self.model.predict(img_tensor)
            else:
                pred_tensor = self.model(img_tensor)

        pred_resize = tf.Compose([tf.Resize([orig_h, orig_w])])
        pred_upscaled = pred_resize(pred_tensor)
        raw_pred = pred_upscaled.squeeze().cpu().numpy()

        water_mask = self._postprocess_mask(raw_pred)
        water_pixels = int(np.sum(water_mask == 1))
        total_pixels = orig_h * orig_w
        water_ratio = water_pixels / float(total_pixels) if total_pixels > 0 else 0.0

        return water_mask, water_ratio

    def estimate_depth(
        self,
        img_bgr: np.ndarray,
        water_mask: np.ndarray,
        water_ratio: float
    ) -> Dict[str, Any]:
        """
        Ước lượng độ sâu ngập (cm), khoảng ngập và cấp độ nguy hại:
        1. Ưu tiên dựa trên mức độ chìm của Người (Anthropometric Submersion).
        2. Dựa trên mức độ chìm của Xe ô tô / Xe tải / Xe máy (Automotive Submersion).
        3. Dựa trên mô hình bao phủ mặt phẳng nước & hình học không gian (Inundation Coverage).
        """
        orig_h, orig_w = img_bgr.shape[:2]
        ref_objects: List[Dict[str, Any]] = []

        # Nếu hầu như không có nước (< 1% diện tích)
        if water_ratio < 0.01:
            return {
                "estimated_depth_cm": 0.0,
                "depth_range_cm": [0.0, 2.0],
                "severity_level": "LEVEL_0_DRY",
                "severity_code": 0,
                "title": self.SEVERITY_LEVELS[0]["title"],
                "action": self.SEVERITY_LEVELS[0]["action"],
                "reference_source": "NO_WATER_DETECTED",
                "submersion_detail": "Không phát hiện vùng nước ngập đáng kể trên mặt đường.",
                "ref_objects": []
            }

        # Dò tìm vật thể trong cảnh bằng YOLO nếu khả dụng
        if self.yolo_model is not None:
            try:
                results = self.yolo_model(img_bgr, conf=0.25, verbose=False)[0]
                for box in results.boxes:
                    cls_id = int(box.cls[0].item())
                    name = self.yolo_model.names.get(cls_id, "")
                    conf = float(box.conf[0].item())
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                    if name in ["person", "car", "truck", "bus", "motorcycle"]:
                        ref_objects.append({
                            "class": name,
                            "box": [x1, y1, x2, y2],
                            "conf": conf,
                            "w": x2 - x1,
                            "h": y2 - y1
                        })
            except Exception as e:
                print(f"[FloodDetector] Lỗi phát hiện vật thể YOLO: {e}")

        # Kiểm tra sự tương tác giữa vật thể và mặt nước
        valid_refs = []
        for obj in ref_objects:
            x1, y1, x2, y2 = obj["box"]
            bw, bh = obj["w"], obj["h"]
            cx = int((x1 + x2) / 2)
            bottom_y = min(orig_h - 1, y2)

            # Lấy vùng kiểm tra xung quanh chân/đáy của vật thể (dưới đáy 15px)
            y_check_start = max(0, bottom_y - 10)
            y_check_end = min(orig_h, bottom_y + 20)
            x_check_start = max(0, cx - 15)
            x_check_end = min(orig_w, cx + 15)

            local_water = water_mask[y_check_start:y_check_end, x_check_start:x_check_end]
            water_presence = float(np.mean(local_water == 1)) if local_water.size > 0 else 0.0

            # Hoặc kiểm tra xem chân vật thể có nằm sát mặt nước không
            if water_presence > 0.20 or (bottom_y < orig_h and np.mean(water_mask[bottom_y:min(orig_h, bottom_y + 15), x1:x2] == 1) > 0.3):
                obj["in_water"] = True
                valid_refs.append(obj)
            else:
                obj["in_water"] = False

        # --- CHIẾN LƯỢC 1: Ước lượng qua NGƯỜI (Anthropometric Submersion) ---
        people_in_water = [o for o in valid_refs if o["class"] == "person"]
        if people_in_water:
            # Chọn người rõ nhất (chiều cao lớn nhất)
            target = max(people_in_water, key=lambda x: x["h"])
            w_body, h_vis = target["w"], target["h"]

            # Tỷ lệ chiều cao/chiều rộng người đứng thẳng chuẩn là ~3.8
            h_estimated_full = max(float(h_vis), float(w_body) * 3.8)
            vis_ratio = min(1.0, float(h_vis) / h_estimated_full)
            submerged_ratio = max(0.0, 1.0 - vis_ratio)

            # Chiều cao người trưởng thành trung bình ~ 170 cm
            est_depth = round(submerged_ratio * 170.0, 1)

            # Điều chỉnh giải phẫu học cơ thể
            if submerged_ratio < 0.12:
                desc = f"Phát hiện người lội nước, ngập mắt cá chân ({est_depth:.0f} cm)"
                depth_range = [max(5.0, est_depth - 4), est_depth + 6]
            elif submerged_ratio < 0.28:
                desc = f"Phát hiện người lội nước, ngập bắp chân/dưới đầu gối ({est_depth:.0f} cm)"
                depth_range = [max(12.0, est_depth - 6), est_depth + 8]
            elif submerged_ratio < 0.45:
                desc = f"Phát hiện người lội nước, ngập ngang đầu gối/đùi ({est_depth:.0f} cm)"
                depth_range = [max(25.0, est_depth - 8), est_depth + 10]
            elif submerged_ratio < 0.65:
                desc = f"Phát hiện người lội nước, ngập ngang hông/thắt lưng ({est_depth:.0f} cm)"
                depth_range = [max(45.0, est_depth - 10), est_depth + 12]
            else:
                desc = f"Phát hiện người lội nước, ngập đến ngực/nách ({est_depth:.0f} cm)"
                depth_range = [max(70.0, est_depth - 15), est_depth + 20]

            est_depth = max(8.0, min(140.0, est_depth))
            severity = self._classify_severity(est_depth)

            return {
                "estimated_depth_cm": est_depth,
                "depth_range_cm": [round(depth_range[0], 1), round(depth_range[1], 1)],
                "severity_level": severity["name"],
                "severity_code": severity["code"],
                "title": severity["title"],
                "action": severity["action"],
                "reference_source": "PERSON_SUBMERSION",
                "submersion_detail": desc,
                "ref_objects": valid_refs
            }

        # --- CHIẾN LƯỢC 2: Ước lượng qua Ô TÔ / XE CỘ (Automotive Submersion) ---
        vehicles_in_water = [o for o in valid_refs if o["class"] in ["car", "truck", "bus", "motorcycle"]]
        if vehicles_in_water:
            target = max(vehicles_in_water, key=lambda x: x["w"] * x["h"])
            v_name = target["class"]
            w_veh, h_veh = target["w"], target["h"]

            # Chiều cao xe con chuẩn: ~145 cm. Tỷ lệ ngang/dọc ~ 1.3 (nhìn trước) đến ~ 2.8 (nhìn bên)
            aspect = float(w_veh) / max(1.0, float(h_veh))
            if aspect > 1.9:
                # Nhìn ngang/chéo
                expected_h = float(w_veh) / 2.7
            else:
                # Nhìn trực diện trước/sau
                expected_h = float(w_veh) / 1.25

            expected_h = max(float(h_veh), expected_h)
            submerged_ratio = max(0.0, min(0.85, 1.0 - (float(h_veh) / expected_h)))

            # Tính độ sâu theo chuẩn ô tô con 145cm hoặc xe máy
            if v_name == "motorcycle":
                est_depth = round(15.0 + submerged_ratio * 75.0, 1)
                desc = f"Phát hiện xe máy ngập trong nước (Ước tính ~{est_depth:.0f} cm)"
            else:
                # Ô tô: Gầm xe ~20cm, Nửa bánh ~35cm, Đầy bánh ~65cm, Nắp capo ~90cm
                base_depth = 20.0 + (submerged_ratio * 90.0)
                # Bù tỷ lệ mặt nước xung quanh
                if water_ratio > 0.45:
                    base_depth = max(base_depth, 42.0)
                est_depth = round(base_depth, 1)
                desc = f"Phát hiện {v_name} ngập bánh xe/gầm xe (Ước tính ~{est_depth:.0f} cm)"

            est_depth = max(10.0, min(130.0, est_depth))
            depth_range = [max(5.0, est_depth - 8), est_depth + 10]
            severity = self._classify_severity(est_depth)

            return {
                "estimated_depth_cm": est_depth,
                "depth_range_cm": [round(depth_range[0], 1), round(depth_range[1], 1)],
                "severity_level": severity["name"],
                "severity_code": severity["code"],
                "title": severity["title"],
                "action": severity["action"],
                "reference_source": "VEHICLE_SUBMERSION",
                "submersion_detail": desc,
                "ref_objects": valid_refs
            }

        # --- CHIẾN LƯỢC 3: Ước lượng qua TỶ LỆ DIỆN TÍCH MẶT NƯỚC (Inundation Coverage Model) ---
        # Khi trong ảnh không có người hoặc xe trực tiếp, ước tính theo độ phủ và phân bố không gian
        pct = water_ratio * 100.0
        if pct < 3.0:
            est_depth = 3.0
            depth_range = [0.0, 5.0]
            desc = f"Vết nước nông rải rác trên bề mặt ({pct:.1f}% khung hình)."
        elif pct < 15.0:
            est_depth = round(8.0 + (pct / 15.0) * 10.0, 1)
            depth_range = [5.0, 18.0]
            desc = f"Ngập nước xâm xấp mép đường cục bộ ({pct:.1f}% khung hình)."
        elif pct < 35.0:
            est_depth = round(18.0 + ((pct - 15.0) / 20.0) * 15.0, 1)
            depth_range = [15.0, 35.0]
            desc = f"Nước ngập diện rộng lòng đường, mấp mé nửa bánh xe ({pct:.1f}% khung hình)."
        elif pct < 60.0:
            est_depth = round(35.0 + ((pct - 35.0) / 25.0) * 25.0, 1)
            depth_range = [32.0, 65.0]
            desc = f"Nước ngập sâu ngập quá bánh xe và tràn lề đường ({pct:.1f}% khung hình)."
        else:
            est_depth = round(60.0 + min(40.0, ((pct - 60.0) / 40.0) * 35.0), 1)
            depth_range = [55.0, 95.0]
            desc = f"Ngập lụt nghiêm trọng diện rộng, nước ngập sâu toàn cảnh ({pct:.1f}% khung hình)."

        severity = self._classify_severity(est_depth)

        return {
            "estimated_depth_cm": est_depth,
            "depth_range_cm": [round(depth_range[0], 1), round(depth_range[1], 1)],
            "severity_level": severity["name"],
            "severity_code": severity["code"],
            "title": severity["title"],
            "action": severity["action"],
            "reference_source": "INUNDATION_COVERAGE",
            "submersion_detail": desc,
            "ref_objects": []
        }

    def _classify_severity(self, depth_cm: float) -> Dict[str, Any]:
        """Phân loại mức độ ngập dựa trên độ sâu cm."""
        for level in self.SEVERITY_LEVELS:
            low, high = level["depth_range"]
            if low <= depth_cm < high:
                return level
        return self.SEVERITY_LEVELS[-1]

    def generate_overlay(
        self,
        img_bgr: np.ndarray,
        water_mask: np.ndarray,
        depth_info: Dict[str, Any],
        alpha: float = 0.45
    ) -> np.ndarray:
        """
        Tạo ảnh overlay gồm:
        - Vùng nước được tô màu xanh ngọc (cyan neon) bán trong suốt.
        - Đường viền ranh giới ngập nước sắc nét.
        - Bounding box các vật thể tham chiếu (nếu có).
        - Bảng điều khiển HUD hiển thị đầy đủ thông số độ sâu, cấp độ ngập và cảnh báo an toàn.
        """
        overlay = img_bgr.copy()
        orig_h, orig_w = img_bgr.shape[:2]

        # 1. Phủ màu nước bán trong suốt (Màu xanh ngọc BGR: 235, 175, 30)
        water_color = np.array([235, 175, 30], dtype=np.uint8)
        water_idx = (water_mask == 1)
        if np.any(water_idx):
            color_layer = np.full_like(overlay, water_color)
            overlay[water_idx] = cv2.addWeighted(
                overlay[water_idx], 1.0 - alpha, color_layer[water_idx], alpha, 0
            )

            # 2. Vẽ viền ranh giới mặt nước
            mask_uint8 = (water_mask * 255).astype(np.uint8)
            contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, (255, 255, 0), 2, cv2.LINE_AA)

        # 3. Vẽ bounding box các vật thể tham chiếu ngập
        for ref in depth_info.get("ref_objects", []):
            if ref.get("in_water", False):
                rx1, ry1, rx2, ry2 = ref["box"]
                cname = ref["class"]
                cv2.rectangle(overlay, (rx1, ry1), (rx2, ry2), (0, 255, 255), 2)
                tag = f"REF: {cname.upper()}"
                cv2.putText(overlay, tag, (rx1, max(15, ry1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

        # 4. Vẽ bảng điều khiển HUD Dashboard ở góc trên
        hud_scale = max(0.55, min(1.2, orig_w / 900.0))
        hud_w = int(min(orig_w - 20, 680 * hud_scale))
        hud_h = int(115 * hud_scale)

        code = depth_info.get("severity_code", 0)
        if code == 0:
            border_color = (0, 255, 0)      # Xanh lá
        elif code == 1:
            border_color = (0, 255, 255)    # Vàng
        elif code == 2:
            border_color = (0, 140, 255)    # Cam
        else:
            border_color = (0, 0, 255)      # Đỏ cảnh báo

        # Khung nền mờ
        sub_img = overlay[10:10 + hud_h, 10:10 + hud_w]
        black_rect = np.zeros_like(sub_img)
        overlay[10:10 + hud_h, 10:10 + hud_w] = cv2.addWeighted(sub_img, 0.25, black_rect, 0.75, 0)
        cv2.rectangle(overlay, (10, 10), (10 + hud_w, 10 + hud_h), border_color, 2)

        # Dòng 1: Tiêu đề & Độ sâu ước lượng
        depth_val = depth_info.get("estimated_depth_cm", 0.0)
        range_val = depth_info.get("depth_range_cm", [0, 0])
        hud_text_1 = f"FLOOD DEPTH: ~{depth_val:.0f} cm  [{range_val[0]:.0f} - {range_val[1]:.0f} cm]"
        cv2.putText(
            overlay, hud_text_1, (20, int(35 * hud_scale) + 5),
            cv2.FONT_HERSHEY_SIMPLEX, hud_scale * 0.75, (255, 255, 255), max(1, int(hud_scale * 2)), cv2.LINE_AA
        )

        # Dòng 2: Cấp độ cảnh báo
        hud_text_2 = f"{depth_info.get('title', '')}"
        cv2.putText(
            overlay, hud_text_2, (20, int(65 * hud_scale) + 5),
            cv2.FONT_HERSHEY_SIMPLEX, hud_scale * 0.65, border_color, max(1, int(hud_scale * 2)), cv2.LINE_AA
        )

        # Dòng 3: Mốc tham chiếu
        ref_text = f"Moc do: {depth_info.get('reference_source', 'N/A')} | {depth_info.get('submersion_detail', '')[:45]}..."
        cv2.putText(
            overlay, ref_text, (20, int(95 * hud_scale) + 5),
            cv2.FONT_HERSHEY_SIMPLEX, hud_scale * 0.52, (200, 200, 200), 1, cv2.LINE_AA
        )

        return overlay

    def predict(
        self,
        image_path: str,
        out_dir: Optional[str] = None,
        save_files: bool = True
    ) -> Dict[str, Any]:
        """
        Hàm chính: Nhận vào 1 ảnh, thực hiện phân đoạn nước, tính toán độ sâu ngập và xuất các tệp kết quả.
        """
        t0 = time.time()
        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"[FloodDetector] Không tìm thấy file ảnh: {image_path}")

        img_bgr = cv2.imread(str(p))
        if img_bgr is None:
            raise ValueError(f"[FloodDetector] Không thể đọc định dạng ảnh: {image_path}")

        orig_h, orig_w = img_bgr.shape[:2]
        source_id = p.stem

        # 1. Phân đoạn mặt nước
        water_mask, water_ratio = self.segment_water(img_bgr)

        # 2. Ước lượng mức độ sâu ngập
        depth_info = self.estimate_depth(img_bgr, water_mask, water_ratio)

        # 3. Tạo ảnh Overlay
        overlay_img = self.generate_overlay(img_bgr, water_mask, depth_info)

        dt_ms = (time.time() - t0) * 1000.0

        # Chuẩn bị cấu trúc kết quả
        result = {
            "image_source": p.name,
            "resolution": [orig_w, orig_h],
            "water_detected": bool(water_ratio >= 0.01),
            "water_area_ratio": round(water_ratio, 4),
            "water_area_percentage": round(water_ratio * 100.0, 2),
            "estimated_depth_cm": depth_info["estimated_depth_cm"],
            "depth_range_cm": depth_info["depth_range_cm"],
            "severity_level": depth_info["severity_level"],
            "severity_code": depth_info["severity_code"],
            "severity_title": depth_info["title"],
            "safety_action": depth_info["action"],
            "reference_source": depth_info["reference_source"],
            "submersion_analysis": depth_info["submersion_detail"],
            "processing_time_ms": round(dt_ms, 1),
            "output_mask_path": None,
            "output_overlay_path": None
        }

        # Lưu ảnh và JSON ra đĩa
        if save_files:
            if out_dir is None:
                out_dir = str(Path(__file__).resolve().parent / "outputs")
            out_path = Path(out_dir)
            out_path.mkdir(parents=True, exist_ok=True)

            mask_file = out_path / f"{source_id}_water_mask.png"
            overlay_file = out_path / f"{source_id}_water_overlay.jpg"
            json_file = out_path / f"{source_id}_flood_result.json"

            # Lưu ảnh mask: 0 (đen) cho khô ráo, 255 (trắng) cho vùng ngập nước
            mask_visual = (water_mask * 255).astype(np.uint8)
            cv2.imwrite(str(mask_file), mask_visual)

            # Lưu ảnh overlay
            cv2.imwrite(str(overlay_file), overlay_img)

            result["output_mask_path"] = str(mask_file)
            result["output_overlay_path"] = str(overlay_file)

            # Lưu file JSON
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            result["output_json_path"] = str(json_file)

        return result, mask_visual, overlay_img


def main():
    parser = argparse.ArgumentParser(description="Flood Detector & Water Depth Estimation (Single Image)")
    parser.add_argument("--image", "--source", dest="image", type=str, required=True,
                        help="Đường dẫn tới file ảnh đầu vào (.jpg, .png, ...)")
    parser.add_argument("--model-path", type=str, default=None,
                        help="Đường dẫn file trọng số LinkNet EfficientNet-B4")
    parser.add_argument("--out-dir", type=str, default="flood_detector/outputs",
                        help="Thư mục xuất ảnh mask, overlay và file JSON kết quả")
    parser.add_argument("--preview", action="store_true",
                        help="Hiển thị cửa sổ xem ảnh overlay trực tiếp")
    args = parser.parse_args()

    detector = FloodDetector(model_path=args.model_path)
    result, mask_img, overlay_img = detector.predict(args.image, out_dir=args.out_dir)

    # In kết quả định lượng ra màn hình console
    print("\n" + "=" * 70)
    print("           KẾT QUẢ PHÂN ĐOẠN & ƯỚC LƯỢNG NGẬP LỤT ĐÔ THỊ           ")
    print("=" * 70)
    print(f"  Ảnh nguồn               : {result['image_source']} ({result['resolution'][0]}x{result['resolution'][1]})")
    print(f"  Phát hiện vùng ngập nước: {'[CÓ]' if result['water_detected'] else '[KHÔNG]'}")
    print(f"  Tỷ lệ diện tích ngập    : {result['water_area_percentage']}% diện tích khung hình")
    print(f"  MỨC ĐỘ SÂU ƯỚC LƯỢNG    : ~{result['estimated_depth_cm']} cm  (Khoảng: {result['depth_range_cm'][0]} - {result['depth_range_cm'][1]} cm)")
    print(f"  Cấp độ cảnh báo         : {result['severity_title']}")
    print(f"  Mốc tham chiếu đo đạc   : {result['reference_source']}")
    print(f"  Phân tích chi tiết      : {result['submersion_analysis']}")
    print(f"  Khuyến nghị an toàn     : {result['safety_action']}")
    print(f"  Thời gian xử lý         : {result['processing_time_ms']} ms")
    print("-" * 70)
    print(f"  [1] Ảnh Mask phân đoạn  : {result.get('output_mask_path')}")
    print(f"  [2] Ảnh Overlay đè mask : {result.get('output_overlay_path')}")
    print(f"  [3] Tệp JSON kết quả    : {result.get('output_json_path')}")
    print("=" * 70 + "\n")

    if args.preview:
        h, w = overlay_img.shape[:2]
        scale = min(1280.0 / w, 760.0 / h, 1.0)
        preview_disp = cv2.resize(overlay_img, (int(w * scale), int(h * scale)))
        cv2.imshow("Flood Detector Preview - Press any key to exit", preview_disp)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
