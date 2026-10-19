from typing import Dict, Any, Tuple, Optional, List
import cv2
import numpy as np


class WaterDepthEstimator:
    """
    Evaluates water depth and depth credibility implementing:
    Frame mới -> Flood detector -> độ sâu có đáng tin không?
    """

    def __init__(self, yolo_detector=None):
        self.yolo_detector = yolo_detector

    def estimate_depth(
        self,
        frame: np.ndarray,
        water_mask: np.ndarray,
        road_water_ratio: float,
        detected_vehicles: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Calculates estimated water depth (cm), depth confidence, and credibility method.
        """
        orig_h, orig_w = frame.shape[:2]

        # 1. No significant water on road
        if road_water_ratio < 0.035:
            return {
                "water_depth_cm": None,
                "depth_confidence": 1.0,
                "depth_method": None,
                "is_depth_credible": True,
                "depth_description": "Không có ngập nước trên mặt đường (0 cm)."
            }

        water_binary = (water_mask > 0).astype(np.uint8)

        # 2. Check automotive submersion if vehicles detected in scene
        vehicles_in_water = []
        if detected_vehicles:
            for v in detected_vehicles:
                x1, y1, x2, y2 = v["box"]
                cname = v.get("class", "vehicle")
                bw = x2 - x1
                bh = y2 - y1
                bottom_y = min(orig_h - 1, y2)
                
                # Sample bottom area (tire / wheel contact area)
                y_sample_start = max(0, bottom_y - max(4, int(bh * 0.12)))
                y_sample_end = min(orig_h, bottom_y + max(4, int(bh * 0.08)))
                x_sample_start = max(0, x1)
                x_sample_end = min(orig_w, x2)

                contact_water = water_binary[y_sample_start:y_sample_end, x_sample_start:x_sample_end]
                submersion_ratio = float(np.mean(contact_water == 1)) if contact_water.size > 0 else 0.0

                if submersion_ratio > 0.25:
                    vehicles_in_water.append({
                        "class": cname,
                        "box": [x1, y1, x2, y2],
                        "submersion_ratio": submersion_ratio,
                        "bh": bh,
                        "bottom_y": bottom_y
                    })

        # Anthropometric or Automotive Submersion Calculation
        if vehicles_in_water:
            # Sort by largest vehicle contact
            vehicles_in_water.sort(key=lambda x: x["submersion_ratio"], reverse=True)
            deepest_v = vehicles_in_water[0]
            cname = deepest_v["class"]
            ratio = deepest_v["submersion_ratio"]

            if cname in ["motorcycle", "bicycle"]:
                if ratio > 0.65:
                    depth_cm = round(32.0 + ratio * 15.0, 1)
                    desc = "Bánh xe máy ngập sâu qua trục bánh xe."
                    conf = 0.88
                else:
                    depth_cm = round(12.0 + ratio * 16.0, 1)
                    desc = "Nước ngập nửa bánh xe máy / chạm mép vành."
                    conf = 0.85
            else: # car, bus, truck
                if ratio > 0.70:
                    depth_cm = round(40.0 + ratio * 20.0, 1)
                    desc = "Nước ngập qua gầm ô tô, chạm cản trước."
                    conf = 0.90
                else:
                    depth_cm = round(18.0 + ratio * 18.0, 1)
                    desc = "Nước ngập quanh lốp ô tô, chưa chạm gầm."
                    conf = 0.84

            return {
                "water_depth_cm": depth_cm,
                "depth_confidence": conf,
                "depth_method": "automotive",
                "is_depth_credible": True,
                "depth_description": f"[Độ sâu mốc xe] {desc} (~{depth_cm:.1f} cm)"
            }

        # Physical Constraint: If vehicles are present on the road, and NONE of them show tire submersion,
        # then the road CANNOT be significantly flooded! Ground truth from tires proves pavement is dry/damp only.
        if detected_vehicles and len(detected_vehicles) >= 2 and len(vehicles_in_water) == 0:
            return {
                "water_depth_cm": None,
                "depth_confidence": 0.95,
                "depth_method": "automotive_dry_ground_truth",
                "is_depth_credible": True,
                "depth_description": "Không có ngập sâu (bánh xe các phương tiện trên đường tiếp xúc mặt đất khô ráo, không bị chìm nước)."
            }

        # 3. Geometric Planar Inundation Estimation (when no vehicle directly submerged)
        if road_water_ratio < 0.10:
            depth_cm = round(4.0 + road_water_ratio * 40.0, 1)
            conf = 0.68
            desc = "Nước đọng xâm xấp mép đường."
        elif road_water_ratio < 0.25:
            depth_cm = round(10.0 + road_water_ratio * 45.0, 1)
            conf = 0.74
            desc = "Nước ngập vừa, loang rộng trên mặt đường."
        elif road_water_ratio < 0.50:
            depth_cm = round(22.0 + road_water_ratio * 35.0, 1)
            conf = 0.78
            desc = "Ngập sâu trên phần lớn diện tích lòng đường."
        else:
            depth_cm = round(40.0 + road_water_ratio * 25.0, 1)
            conf = 0.82
            desc = "Ngập sâu nghiêm trọng toàn bộ hành lang đường."

        return {
            "water_depth_cm": depth_cm,
            "depth_confidence": conf,
            "depth_method": "geometric_water_coverage",
            "is_depth_credible": conf >= 0.70,
            "depth_description": f"[Ước lượng diện tích ngập] {desc} (~{depth_cm:.1f} cm)"
        }
