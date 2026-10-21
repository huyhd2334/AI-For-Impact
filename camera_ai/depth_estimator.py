from typing import Dict, Any, Tuple, Optional, List
import cv2
import numpy as np


class WaterDepthEstimator:
    """
    Hệ Thống Ước Lượng Độ Sâu & Mực Nước Đô Thị (SOTA Urban Water Depth Estimator)
    Áp dụng 4 tầng suy luận:
      1. Anthropometric Submersion (Độ chìm người đi bộ / dắt xe)
      2. Automotive Submersion (Độ chìm bánh xe máy, ô tô, xe tải)
      3. Sidewalk Curb Inundation (Mốc bó vỉa hè đô thị 15-20cm)
      4. Geometric Surface Planar Spread (Mô hình diện mạo ngập hình học)
    """

    # Bảng phân cấp 4 mức độ ngập chuẩn đô thị SafeRoute
    SEVERITY_LEVELS = {
        0: {"name": "LEVEL_0_DRY", "title": "Mức 0: Khô ráo", "depth_range": [0.0, 5.0], "safe_for_bikes": True},
        1: {"name": "LEVEL_1_SHALLOW", "title": "Mức 1: Ngập nhẹ (Xâm xấp)", "depth_range": [5.0, 15.0], "safe_for_bikes": True},
        2: {"name": "LEVEL_2_MODERATE", "title": "Mức 2: Ngập vừa (Nửa bánh xe)", "depth_range": [15.0, 35.0], "safe_for_bikes": False},
        3: {"name": "LEVEL_3_DEEP", "title": "Mức 3: Ngập sâu (Nguy hiểm / Chết máy)", "depth_range": [35.0, 80.0], "safe_for_bikes": False},
    }

    def __init__(self, yolo_detector=None):
        self.yolo_detector = yolo_detector

    def estimate_depth(
        self,
        frame: np.ndarray,
        water_mask: np.ndarray,
        road_water_ratio: float,
        detected_vehicles: Optional[List[Dict[str, Any]]] = None,
        sidewalk_mask: Optional[np.ndarray] = None
    ) -> Dict[str, Any]:
        """
        Ước lượng mực nước (cm), độ tin cậy và phương pháp đo đạc.
        """
        orig_h, orig_w = frame.shape[:2]

        # 1. Trường hợp lòng đường khô ráo (< 3.5% diện tích mặt đường)
        if road_water_ratio < 0.035:
            return {
                "water_depth_cm": None,
                "depth_confidence": 0.98,
                "depth_method": "NONE",
                "is_depth_credible": True,
                "severity_code": 0,
                "severity_name": "LEVEL_0_DRY",
                "safe_for_bikes": True,
                "depth_description": "Mặt đường khô ráo hoàn toàn (0 cm)."
            }

        water_binary = (water_mask > 0).astype(np.uint8)

        # 2. Phân tích tương tác giữa vật thể và mặt nước
        submerged_people = []
        submerged_vehicles = []

        if detected_vehicles:
            for v in detected_vehicles:
                box = v.get("box")
                if not box or len(box) != 4:
                    continue
                x1, y1, x2, y2 = map(int, box)
                cname = str(v.get("class", "car")).lower()
                bw = max(1, x2 - x1)
                bh = max(1, y2 - y1)
                bottom_y = min(orig_h - 1, y2)

                # Vùng lấy mẫu chân vật thể tiếp xúc mặt nước (Tire-ground contact area)
                y_sample_start = max(0, bottom_y - max(4, int(bh * 0.14)))
                y_sample_end = min(orig_h, bottom_y + max(4, int(bh * 0.08)))
                x_sample_start = max(0, x1)
                x_sample_end = min(orig_w, x2)

                contact_water = water_binary[y_sample_start:y_sample_end, x_sample_start:x_sample_end]
                submersion_ratio = float(np.mean(contact_water == 1)) if contact_water.size > 0 else 0.0

                if submersion_ratio > 0.20:
                    info = {
                        "class": cname,
                        "box": [x1, y1, x2, y2],
                        "submersion_ratio": submersion_ratio,
                        "bw": bw,
                        "bh": bh,
                        "bottom_y": bottom_y
                    }
                    if cname in ["person", "pedestrian"]:
                        submerged_people.append(info)
                    else:
                        submerged_vehicles.append(info)

        # -------------------------------------------------------------
        # TẦNG 1: ƯỚC LƯỢNG QUA NGƯỜI LỘI NƯỚC (Anthropometric Submersion)
        # -------------------------------------------------------------
        if submerged_people:
            # Chọn người có chiều cao hiển thị lớn nhất (gần camera nhất)
            target_p = max(submerged_people, key=lambda x: x["bh"])
            ratio = target_p["submersion_ratio"]
            bh = target_p["bh"]
            bw = target_p["bw"]

            # Chiều cao chuẩn người Việt Nam đứng thẳng ~ 168 cm, tỷ lệ cao/rộng ~ 3.8
            est_full_h = max(float(bh), float(bw) * 3.8)
            sub_height_ratio = max(ratio * 0.35, 1.0 - min(1.0, float(bh) / est_full_h))

            if sub_height_ratio < 0.15:
                depth_cm = round(max(6.0, sub_height_ratio * 120.0), 1)
                desc = "Người lội nước ngập xâm xấp mắt cá chân."
                conf = 0.88
            elif sub_height_ratio < 0.32:
                depth_cm = round(15.0 + (sub_height_ratio - 0.15) * 80.0, 1)
                desc = "Người lội nước ngập bắp chân dưới đầu gối."
                conf = 0.90
            elif sub_height_ratio < 0.50:
                depth_cm = round(32.0 + (sub_height_ratio - 0.32) * 75.0, 1)
                desc = "Người lội nước ngập ngang đầu gối / đùi."
                conf = 0.92
            else:
                depth_cm = round(50.0 + min(40.0, (sub_height_ratio - 0.50) * 100.0), 1)
                desc = "Người lội nước ngập ngang hông / thắt lưng (cực kỳ nguy hiểm)."
                conf = 0.94

            depth_cm = float(min(120.0, max(5.0, depth_cm)))
            safe_bikes = depth_cm < 18.0
            sev_code = 3 if depth_cm >= 35.0 else (2 if depth_cm >= 15.0 else 1)

            return {
                "water_depth_cm": depth_cm,
                "depth_confidence": conf,
                "depth_method": "ANTHROPOMETRIC_SUBMERSION",
                "is_depth_credible": True,
                "severity_code": sev_code,
                "severity_name": self.SEVERITY_LEVELS[sev_code]["name"],
                "safe_for_bikes": safe_bikes,
                "depth_description": f"[Mốc người lội nước] {desc} (~{depth_cm:.1f} cm)"
            }

        # -------------------------------------------------------------
        # TẦNG 2: ƯỚC LƯỢNG QUA PHƯƠNG TIỆN (Automotive Submersion)
        # -------------------------------------------------------------
        if submerged_vehicles:
            # Sắp xếp theo tỷ lệ ngập sâu nhất
            submerged_vehicles.sort(key=lambda x: x["submersion_ratio"], reverse=True)
            deepest_v = submerged_vehicles[0]
            cname = deepest_v["class"]
            ratio = deepest_v["submersion_ratio"]

            if cname in ["motorcycle", "bicycle"]:
                # Bánh xe máy tiêu chuẩn: đường kính 55-60cm, vành 43cm, cổ pô 25cm
                if ratio > 0.65 and road_water_ratio >= 0.22:
                    depth_cm = round(30.0 + ratio * 15.0, 1)
                    desc = "Bánh xe máy ngập sâu vượt cổ pô / ngập trục bánh xe."
                    conf = 0.92
                elif ratio > 0.35 or road_water_ratio >= 0.12:
                    depth_cm = round(16.0 + ratio * 12.0, 1)
                    desc = "Nước ngập nửa bánh xe máy, mấp mé gầm xe."
                    conf = 0.88
                else:
                    depth_cm = round(8.0 + ratio * 8.0, 1)
                    desc = "Nước ngập quanh lốp xe máy, chưa ngập trục bánh."
                    conf = 0.85
            else:
                # Ô tô con, xe buýt, xe tải: đường kính lốp 65cm, gầm xe 18-22cm, nắp capo 100cm
                if ratio > 0.65 and road_water_ratio >= 0.35:
                    depth_cm = round(38.0 + ratio * 24.0, 1)
                    desc = "Nước ngập qua gầm ô tô, chạm cản trước (nguy cơ thủy kích)."
                    conf = 0.93
                elif ratio > 0.35 and road_water_ratio >= 0.18:
                    depth_cm = round(18.0 + ratio * 14.0, 1)
                    desc = "Nước ngập quanh lốp ô tô, mấp mé chạm gầm xe."
                    conf = 0.89
                else:
                    depth_cm = round(7.0 + ratio * 9.0, 1)
                    desc = "Lốp ô tô lăn qua vệt nước mép đường."
                    conf = 0.84

            depth_cm = float(min(120.0, max(5.0, depth_cm)))
            safe_bikes = depth_cm < 18.0
            sev_code = 3 if depth_cm >= 35.0 else (2 if depth_cm >= 15.0 else 1)

            return {
                "water_depth_cm": depth_cm,
                "depth_confidence": conf,
                "depth_method": "AUTOMOTIVE_SUBMERSION",
                "is_depth_credible": True,
                "severity_code": sev_code,
                "severity_name": self.SEVERITY_LEVELS[sev_code]["name"],
                "safe_for_bikes": safe_bikes,
                "depth_description": f"[Mốc xe lội nước] {desc} (~{depth_cm:.1f} cm)"
            }

        # -------------------------------------------------------------
        # TẦNG 3: ƯỚC LƯỢNG QUA MỐC BÓ VỈA HÈ ĐÔ THỊ (Sidewalk Curb Inundation)
        # -------------------------------------------------------------
        if sidewalk_mask is not None:
            sidewalk_binary = (sidewalk_mask > 0).astype(np.uint8)
            sw_total = int(np.sum(sidewalk_binary))
            if sw_total > 500:
                water_on_sidewalk = cv2.bitwise_and(water_binary, water_binary, mask=sidewalk_binary)
                sw_water_ratio = float(np.sum(water_on_sidewalk > 0)) / float(sw_total)

                # Nếu nước đã tràn lên vỉa hè (> 5% diện tích vỉa hè)
                if sw_water_ratio > 0.05 and road_water_ratio > 0.20:
                    # Bó vỉa đô thị Đà Nẵng cao 15-20cm => Mặt đường ngập tối thiểu 20-30cm
                    curb_depth = round(20.0 + min(25.0, sw_water_ratio * 40.0), 1)
                    return {
                        "water_depth_cm": curb_depth,
                        "depth_confidence": 0.88,
                        "depth_method": "SIDEWALK_CURB",
                        "is_depth_credible": True,
                        "severity_code": 2 if curb_depth < 35.0 else 3,
                        "severity_name": "LEVEL_2_MODERATE" if curb_depth < 35.0 else "LEVEL_3_DEEP",
                        "safe_for_bikes": False,
                        "depth_description": f"[Mốc bó vỉa hè] Nước ngập tràn qua vỉa hè đô thị (độ sâu tối thiểu ~{curb_depth:.1f} cm)."
                    }

        # -------------------------------------------------------------
        # TẦNG 4: ƯỚC LƯỢNG MẶT PHẲNG NƯỚC HÌNH HỌC (Geometric Spread)
        # -------------------------------------------------------------
        if road_water_ratio < 0.15:
            depth_cm = round(5.0 + (road_water_ratio - 0.035) / 0.115 * 9.0, 1)
            conf = 0.83
            desc = "Nước đọng xâm xấp mép/rãnh đường, xe máy lưu thông an toàn."
            sev_code = 1
            safe_bikes = True
        elif road_water_ratio < 0.45:
            depth_cm = round(15.0 + (road_water_ratio - 0.15) / 0.30 * 18.0, 1)
            conf = 0.87
            desc = "Nước ngập diện rộng lòng đường, xe máy dễ chết máy nếu đi nhanh."
            sev_code = 2
            safe_bikes = False
        else:
            depth_cm = round(33.0 + min(1.0, (road_water_ratio - 0.45) / 0.35) * 25.0, 1)
            conf = 0.92
            desc = "Ngập sâu tê liệt phần lớn mặt đường, cấm xe máy và ô tô con."
            sev_code = 3
            safe_bikes = False

        return {
            "water_depth_cm": depth_cm,
            "depth_confidence": conf,
            "depth_method": "GEOMETRIC_SPREAD",
            "is_depth_credible": True,
            "severity_code": sev_code,
            "severity_name": self.SEVERITY_LEVELS[sev_code]["name"],
            "safe_for_bikes": safe_bikes,
            "depth_description": f"[Mô hình diện tích ngập] {desc} (~{depth_cm:.1f} cm)"
        }
