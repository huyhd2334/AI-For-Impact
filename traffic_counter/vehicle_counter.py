from typing import Dict, List, Tuple, Optional
import math
import cv2
import numpy as np
import os


class VehicleDetector:
    VEHICLE_CLASSES = {
        2: "car",
        3: "motorcycle",
        5: "bus",
        7: "truck",
        1: "bicycle"
    }

    def __init__(
        self, 
        model_name: str = "vn_traffic_surveillance.pt", 
        confidence_thresh: float = 0.18,
        imgsz: int = 1280,
        px_to_meter: float = 0.05,
        use_sahi: bool = False,
        enable_heatmap: bool = False,
        tripwire_y_ratio: float = 0.58
    ):
        self.confidence_thresh = confidence_thresh
        self.imgsz = imgsz
        self.px_to_meter = px_to_meter
        self.yolo_model = None
        self.use_yolo = False
        self.use_sahi = use_sahi
        self.sahi_model = None
        self.track_history = {}
        
        # Traffic Density Heatmap
        self.enable_heatmap = enable_heatmap
        self.heatmap_acc = None
        
        # Virtual Tripwire Counting Line
        self.tripwire_y_ratio = tripwire_y_ratio
        self.tripwire_total = 0
        self.tripwire_breakdown = {}
        self.crossing_timestamps = []
        self.recent_crossings = []
        self.tripwire_start_time = None
        self.flow_vpm = 0.0

        # Tìm kiếm đường dẫn file model trong weights/
        model_path = model_name
        if not os.path.exists(model_path):
            candidates = [
                os.path.join("weights", model_name),
                os.path.join(os.path.dirname(__file__), "..", "weights", model_name),
                os.path.join("weights", "vn_traffic_surveillance.pt"),
                os.path.join(os.path.dirname(__file__), "..", "weights", "vn_traffic_surveillance.pt"),
                os.path.join("weights", "yolov8n.pt"),
                os.path.join(os.path.dirname(__file__), "..", "weights", "yolov8n.pt"),
            ]
            for c in candidates:
                if os.path.exists(c):
                    model_path = c
                    break

        try:
            import torch
            from ultralytics import YOLO
            self.device = 0 if torch.cuda.is_available() else "cpu"
            device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
            self.yolo_model = YOLO(model_path)
            self.use_yolo = True
            print(f"[VehicleDetector] Model: {model_path} | Device: {device_name} (device={self.device}) | imgsz={self.imgsz} | conf={self.confidence_thresh}")

            if self.use_sahi:
                from sahi import AutoDetectionModel
                self.sahi_model = AutoDetectionModel.from_pretrained(
                    model_type="ultralytics",
                    model_path=model_path,
                    confidence_threshold=confidence_thresh,
                    device=f"cuda:{self.device}" if self.device == 0 else "cpu"
                )
                print(f"[VehicleDetector] SAHI Slicing: ENABLED (768x768 tiles for small distant vehicles)")
        except Exception as e:
            print(f"[VehicleDetector] Failed to load model ({e}). Using OpenCV Background Subtractor fallback.")
            self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=True)

    @staticmethod
    def _calc_iou_ios(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> Tuple[float, float]:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        if inter == 0:
            return 0.0, 0.0
        areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        iou = inter / float(areaA + areaB - inter)
        ios = inter / float(min(areaA, areaB))
        return iou, ios

    def detect_and_count(
        self, 
        frame: np.ndarray, 
        timestamp: Optional[float] = None
    ) -> Tuple[int, Dict[str, int], np.ndarray, float, Dict]:
        if timestamp is None:
            import time
            timestamp = time.time()

        annotated_frame = frame.copy()
        breakdown = {"motorcycle": 0, "car": 0, "bus": 0, "truck": 0, "bicycle": 0}
        total_count = 0
        active_speeds = []
        stopped_vehicles = 0
        slow_vehicles = 0

        if self.use_yolo and self.yolo_model:
            raw_vehicles = []
            raw_persons = []

            # 1. Object detection via SAHI Slicing or standard batch inference
            if self.use_sahi and self.sahi_model:
                from sahi.predict import get_sliced_prediction
                sahi_result = get_sliced_prediction(
                    frame,
                    self.sahi_model,
                    slice_height=768,
                    slice_width=768,
                    overlap_height_ratio=0.2,
                    overlap_width_ratio=0.2,
                    verbose=0
                )
                for p in sahi_result.object_prediction_list:
                    x1, y1, x2, y2 = map(int, p.bbox.to_xyxy())
                    conf = float(p.score.value)
                    raw_name = str(p.category.name).lower()
                    if any(k in raw_name for k in ["motor", "bike"]):
                        raw_vehicles.append({"class": "motorcycle", "box": (x1, y1, x2, y2), "conf": conf})
                    elif "car" in raw_name or "auto" in raw_name:
                        raw_vehicles.append({"class": "car", "box": (x1, y1, x2, y2), "conf": conf})
                    elif "bus" in raw_name:
                        raw_vehicles.append({"class": "bus", "box": (x1, y1, x2, y2), "conf": conf})
                    elif "truck" in raw_name or "lcv" in raw_name:
                        raw_vehicles.append({"class": "truck", "box": (x1, y1, x2, y2), "conf": conf})
                    elif "bicycle" in raw_name or "cycle" in raw_name:
                        raw_vehicles.append({"class": "bicycle", "box": (x1, y1, x2, y2), "conf": conf})
                    elif "person" in raw_name:
                        raw_persons.append({"box": (x1, y1, x2, y2), "conf": conf})
            else:
                results = self.yolo_model(
                    frame, 
                    imgsz=self.imgsz, 
                    conf=self.confidence_thresh, 
                    device=self.device,
                    verbose=False
                )
                for r in results:
                    boxes = r.boxes
                    names_map = getattr(self.yolo_model, 'names', {})
                    for box in boxes:
                        cls_id = int(box.cls[0].item())
                        conf = float(box.conf[0].item())
                        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                        raw_name = str(names_map.get(cls_id, "")).lower()
                        if any(k in raw_name for k in ["motor", "bike"]):
                            raw_vehicles.append({"class": "motorcycle", "box": (x1, y1, x2, y2), "conf": conf})
                        elif "car" in raw_name or "auto" in raw_name:
                            raw_vehicles.append({"class": "car", "box": (x1, y1, x2, y2), "conf": conf})
                        elif "bus" in raw_name:
                            raw_vehicles.append({"class": "bus", "box": (x1, y1, x2, y2), "conf": conf})
                        elif "truck" in raw_name or "lcv" in raw_name:
                            raw_vehicles.append({"class": "truck", "box": (x1, y1, x2, y2), "conf": conf})
                        elif "bicycle" in raw_name or "cycle" in raw_name:
                            raw_vehicles.append({"class": "bicycle", "box": (x1, y1, x2, y2), "conf": conf})
                        elif "person" in raw_name:
                            raw_persons.append({"box": (x1, y1, x2, y2), "conf": conf})

            raw_vehicles.sort(key=lambda x: x["conf"], reverse=True)
            raw_persons.sort(key=lambda x: x["conf"], reverse=True)

            # Associate orphan riders when camera view cuts off vehicle chassis
            img_h, img_w = frame.shape[:2]
            for p in raw_persons:
                px1, py1, px2, py2 = p["box"]
                p_conf = p["conf"]
                pbh = py2 - py1
                
                if py2 > int(img_h * 0.35) and pbh >= 160:
                    matched = False
                    for v in raw_vehicles:
                        iou, ios = self._calc_iou_ios(p["box"], v["box"])
                        if iou > 0.10 or ios > 0.20:
                            matched = True
                            break
                    if not matched:
                        raw_vehicles.append({"class": "motorcycle", "box": (px1, py1, px2, py2), "conf": p_conf})

            raw_vehicles.sort(key=lambda x: x["conf"], reverse=True)

            # Remove oversize anomalies and duplicate detections
            clean_vehicles = []
            max_allowed_w = int(img_w * 0.45)
            max_allowed_h = int(img_h * 0.38)
            max_allowed_area = int(img_w * img_h * 0.15)

            for v in raw_vehicles:
                cname = v["class"]
                x1, y1, x2, y2 = v["box"]
                bw, bh = (x2 - x1), (y2 - y1)

                if cname in ["motorcycle", "bicycle"]:
                    if bw > max_allowed_w or bh > max_allowed_h or (bw * bh) > max_allowed_area:
                        continue
                    is_near_bottom_edge = (y2 >= img_h - 150)
                    if not is_near_bottom_edge and (bw / float(max(1, bh)) > 1.95):
                        continue

                # Strict NMS overlap suppression to eliminate duplicate bounding boxes
                is_duplicate = False
                for cv_item in clean_vehicles:
                    iou, ios = self._calc_iou_ios(v["box"], cv_item["box"])
                    if iou > 0.42 or ios > 0.58:
                        is_duplicate = True
                        break
                            
                if not is_duplicate:
                    clean_vehicles.append(v)

            # Centroid & IoU tracking with windowed displacement speed estimation
            if not hasattr(self, 'next_track_id'):
                self.next_track_id = 1
            if not hasattr(self, 'track_details'):
                self.track_details = {}

            line_thick = max(2, int(img_h / 850))
            font_scale = max(0.45, img_h / 3200.0)
            font_thick = max(1, int(img_h / 1800))

            line_y = int(img_h * self.tripwire_y_ratio)
            line_x1 = int(img_w * 0.05)
            line_x2 = int(img_w * 0.95)

            active_render_vehicles = []
            matched_tids = set()

            for v in clean_vehicles:
                cname = v["class"]
                x1, y1, x2, y2 = v["box"]
                conf = v["conf"]
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                bw, bh = (x2 - x1), (y2 - y1)

                y_factor = max(1.0, cy / (float(img_h) * 0.55))
                best_id = None
                best_match_score = -1.0

                for tid, td in list(self.track_details.items()):
                    if tid in matched_tids:
                        continue
                    if timestamp - td['last_t'] < 1.5:
                        prev_box = td.get('last_box', (td['last_pos'][0]-bw/2, td['last_pos'][1]-bh/2, td['last_pos'][0]+bw/2, td['last_pos'][1]+bh/2))
                        iou, ios = self._calc_iou_ios(v["box"], prev_box)
                        dist = math.hypot(cx - td['last_pos'][0], cy - td['last_pos'][1])
                        max_allowed_dist = max(55.0, float(bh) * 0.65) * y_factor

                        if iou > 0.25:
                            score = 100.0 + iou * 50.0 - (dist / 100.0)
                            if score > best_match_score:
                                best_match_score = score
                                best_id = tid
                        elif dist < max_allowed_dist:
                            score = 50.0 - (dist / max_allowed_dist) * 30.0
                            if score > best_match_score:
                                best_match_score = score
                                best_id = tid

                speed_kmh = None
                is_vehicle_stopped = False

                if best_id is not None:
                    matched_tids.add(best_id)
                    td = self.track_details[best_id]
                    dt = timestamp - td['last_t']
                    
                    if 'pos_history' not in td:
                        td['pos_history'] = [(td['last_pos'][0], td['last_pos'][1], td['last_t'])]
                    td['pos_history'].append((cx, cy, timestamp))
                    # Keep only positions within the last 1.0 second
                    td['pos_history'] = [(hx, hy, ht) for (hx, hy, ht) in td['pos_history'] if (timestamp - ht) <= 1.0]

                    td['prev_pos'] = td['last_pos']
                    td['last_pos'] = (cx, cy)
                    td['last_box'] = (x1, y1, x2, y2)
                    td['last_t'] = timestamp
                    td['hit_count'] += 1

                    # Measure speed over window (at least 0.20s) to eliminate frame jitter
                    if len(td['pos_history']) >= 2:
                        hist_x, hist_y, hist_t = td['pos_history'][0]
                        w_dt = timestamp - hist_t
                        w_disp = math.hypot(cx - hist_x, cy - hist_y)

                        if w_dt >= 0.20:
                            # If vehicle moved less than 16px over the window, it is completely stopped
                            if w_disp < 16.0:
                                speed_kmh = 0.0
                                is_vehicle_stopped = True
                            else:
                                persp_scale = max(0.35, cy / float(img_h))
                                m_per_px = (self.px_to_meter * 0.32) / persp_scale
                                raw_spd = (w_disp * m_per_px / w_dt) * 3.6
                                if raw_spd < 5.0:
                                    speed_kmh = 0.0
                                    is_vehicle_stopped = True
                                else:
                                    speed_kmh = min(80.0, max(0.0, raw_spd))
                            td['prev_spd'] = speed_kmh
                        else:
                            speed_kmh = td['prev_spd']
                            is_vehicle_stopped = (speed_kmh == 0.0)
                    else:
                        speed_kmh = 0.0
                        is_vehicle_stopped = True

                    tid_show = best_id
                else:
                    tid_show = self.next_track_id
                    self.track_details[tid_show] = {
                        'start_pos': (cx, cy),
                        'start_t': timestamp,
                        'prev_pos': (cx, cy),
                        'last_pos': (cx, cy),
                        'last_box': (x1, y1, x2, y2),
                        'pos_history': [(cx, cy, timestamp)],
                        'last_t': timestamp,
                        'hit_count': 1,
                        'is_stationary': False,
                        'prev_spd': 0.0,
                        'crossed_tripwire': False
                    }
                    matched_tids.add(tid_show)
                    self.next_track_id += 1
                    speed_kmh = None

                td_curr = self.track_details[tid_show]

                if is_vehicle_stopped or (speed_kmh is not None and speed_kmh == 0.0):
                    stopped_vehicles += 1
                elif speed_kmh is not None and speed_kmh < 12.0:
                    slow_vehicles += 1

                # Check virtual tripwire crossing
                prev_y = td_curr.get('prev_pos', (cx, cy))[1]
                if (line_x1 <= cx <= line_x2) and (prev_y < line_y <= cy):
                    if not td_curr.get('crossed_tripwire', False):
                        td_curr['crossed_tripwire'] = True
                        self.tripwire_total += 1
                        self.tripwire_breakdown[cname] = self.tripwire_breakdown.get(cname, 0) + 1
                        self.crossing_timestamps.append(timestamp)
                        self.recent_crossings.append((int(cx), int(line_y), timestamp))

                if cname in breakdown:
                    breakdown[cname] += 1
                else:
                    breakdown[cname] = 1
                total_count += 1
                if speed_kmh is not None:
                    active_speeds.append(speed_kmh)

                active_render_vehicles.append((x1, y1, x2, y2, cname, tid_show, speed_kmh))

            # Compute flow rate (Vehicles Per Minute - VPM)
            if self.tripwire_start_time is None:
                self.tripwire_start_time = timestamp
            self.crossing_timestamps = [t for t in self.crossing_timestamps if (timestamp - t) <= 60.0]
            elapsed_sec = max(5.0, min(60.0, timestamp - self.tripwire_start_time))
            self.flow_vpm = (len(self.crossing_timestamps) / elapsed_sec) * 60.0

            # Traffic Density Heatmap accumulation
            if self.enable_heatmap:
                grid_h, grid_w = img_h // 4, img_w // 4
                if self.heatmap_acc is None or self.heatmap_acc.shape != (grid_h, grid_w):
                    self.heatmap_acc = np.zeros((grid_h, grid_w), dtype=np.float32)

                self.heatmap_acc *= 0.992

                for (x1, y1, x2, y2, cname, tid, spd) in active_render_vehicles:
                    hx1 = max(0, int(x1 * 0.25))
                    hy1 = max(0, int(y1 * 0.25))
                    hx2 = min(grid_w, int(x2 * 0.25))
                    hy2 = min(grid_h, int(y2 * 0.25))
                    if hx2 > hx1 and hy2 > hy1:
                        heat_val = 3.6 if (spd is not None and spd < 20.0) else 1.5
                        self.heatmap_acc[hy1:hy2, hx1:hx2] += heat_val

                np.clip(self.heatmap_acc, 0, 100.0, out=self.heatmap_acc)

                norm_heat = np.clip((self.heatmap_acc / 22.0) * 255.0, 0, 255).astype(np.uint8)
                color_heat = cv2.applyColorMap(norm_heat, cv2.COLORMAP_JET)
                color_heat_full = cv2.resize(color_heat, (img_w, img_h), interpolation=cv2.INTER_LINEAR)
                heat_mask = cv2.resize((norm_heat > 16).astype(np.uint8), (img_w, img_h), interpolation=cv2.INTER_NEAREST)
                
                heat_idx = heat_mask > 0
                annotated_frame[heat_idx] = cv2.addWeighted(
                    annotated_frame, 0.65, color_heat_full, 0.35, 0
                )[heat_idx]

                heat_badge = "[ HEATMAP: ON (Key 'H' to toggle) ]"
                cv2.putText(
                    annotated_frame, 
                    heat_badge, 
                    (max(20, img_w - int(480 * font_scale)), int(35 * font_scale)), 
                    cv2.FONT_HERSHEY_SIMPLEX, 
                    font_scale * 0.65, 
                    (0, 255, 255), 
                    font_thick, 
                    cv2.LINE_AA
                )

            # Draw virtual tripwire line
            trip_color = (255, 205, 0)
            trip_thick = max(2, int(img_h / 950))
            cv2.line(annotated_frame, (line_x1, line_y), (line_x2, line_y), trip_color, trip_thick, cv2.LINE_AA)
            cv2.circle(annotated_frame, (line_x1, line_y), trip_thick * 2, (0, 255, 255), -1)
            cv2.circle(annotated_frame, (line_x2, line_y), trip_thick * 2, (0, 255, 255), -1)

            trip_label = f"[ TRIPWIRE: {self.tripwire_total} crossed | {int(self.flow_vpm)} veh/min ]"
            cv2.putText(
                annotated_frame, 
                trip_label, 
                (line_x1 + 10, max(30, line_y - 12)), 
                cv2.FONT_HERSHEY_SIMPLEX, 
                font_scale * 0.68, 
                trip_color, 
                font_thick, 
                cv2.LINE_AA
            )

            # Crossing flash pulse
            surviving_crossings = []
            for rx, ry, rt in self.recent_crossings:
                dt = timestamp - rt
                if dt < 1.0:
                    rad = int((1.0 + dt * 2.5) * trip_thick * 2.2)
                    cv2.circle(annotated_frame, (rx, ry), rad, (0, 255, 255), 2, cv2.LINE_AA)
                    surviving_crossings.append((rx, ry, rt))
            self.recent_crossings = surviving_crossings

            # Render bounding boxes and tracking IDs
            for (x1, y1, x2, y2, cname, tid_show, speed_kmh) in active_render_vehicles:
                color = (0, 255, 0) if cname in ["motorcycle", "bicycle"] else (0, 215, 255)
                cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, line_thick)
                
                if speed_kmh is not None:
                    spd_str = f" | {int(speed_kmh)}km/h" if speed_kmh > 0 else " | 0km/h"
                else:
                    spd_str = ""
                label = f"#{tid_show} {cname}{spd_str}"
                
                cv2.putText(
                    annotated_frame, 
                    label, 
                    (x1, max(y1 - 8, int(18 * font_scale))), 
                    cv2.FONT_HERSHEY_SIMPLEX, 
                    font_scale, 
                    color, 
                    font_thick, 
                    cv2.LINE_AA
                )

        avg_speed_kmh = float(np.mean(active_speeds)) if active_speeds else 0.0

        tripwire_info = {
            "total_crossed": self.tripwire_total,
            "flow_vpm": self.flow_vpm,
            "breakdown": self.tripwire_breakdown,
            "stopped_vehicles": stopped_vehicles,
            "slow_vehicles": slow_vehicles,
            "speed_measured_count": len(active_speeds)
        }

        return total_count, breakdown, annotated_frame, avg_speed_kmh, tripwire_info
