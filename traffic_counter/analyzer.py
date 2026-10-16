import os
import sys
import cv2
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from .schemas import TrafficIncidentEvent
from .vehicle_counter import VehicleDetector
from .water_detector import SimpleWaterDetector
from .event_emitter import IncidentEventEmitter


class VideoTrafficAnalyzer:
    def __init__(
        self,
        camera_id: str = "CAM_D1_001",
        location: Optional[Dict[str, float]] = None,
        sample_interval_sec: float = 5.0,
        congested_vehicle_threshold: int = 10,
        output_evidence_dir: str = "evidence_captures",
        webhook_url: Optional[str] = None,
        model_name: str = "vn_traffic_surveillance.pt",
        confidence_thresh: float = 0.18,
        imgsz: int = 1280,
        water_ratio_thresh: float = 0.05,
        jam_speed_thresh_kmh: float = 15.0,
        use_sahi: bool = False,
        enable_heatmap: bool = False,
        tripwire_y_ratio: float = 0.58
    ):
        self.camera_id = camera_id
        self.location = location or {"latitude": 10.7769, "longitude": 106.7009}
        self.sample_interval_sec = sample_interval_sec
        self.congested_threshold = congested_vehicle_threshold
        self.jam_speed_thresh_kmh = jam_speed_thresh_kmh
        self.output_evidence_dir = output_evidence_dir
        os.makedirs(self.output_evidence_dir, exist_ok=True)

        self.vehicle_detector = VehicleDetector(
            model_name=model_name,
            confidence_thresh=confidence_thresh,
            imgsz=imgsz,
            use_sahi=use_sahi,
            enable_heatmap=enable_heatmap,
            tripwire_y_ratio=tripwire_y_ratio
        )
        self.water_detector = SimpleWaterDetector(min_water_ratio=water_ratio_thresh)
        self.emitter = IncidentEventEmitter(webhook_url=webhook_url)

    def _resize_maintain_aspect(self, img: cv2.Mat, max_h: int = 760, max_w: int = 1280) -> cv2.Mat:
        h, w = img.shape[:2]
        scale = min(max_w / float(w), max_h / float(h), 1.0)
        if scale < 1.0:
            new_w = int(w * scale)
            new_h = int(h * scale)
            return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        return img

    def process_video(
        self, 
        video_source: str, 
        max_duration_sec: Optional[float] = None, 
        display_preview: bool = False,
        target_fps: float = 10.0
    ):
        cap = cv2.VideoCapture(video_source)
        if not cap.isOpened():
            print(f"[VideoTrafficAnalyzer] ERROR: Cannot open video source: {video_source}")
            return

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step_frames = max(1, int(source_fps / target_fps)) if target_fps and target_fps > 0 else 1
        if 0 < total_frames <= 2:
            step_frames = 1
        delay_ms = max(1, int(1000.0 / target_fps)) if target_fps and target_fps > 0 else 30

        print(f"[VideoTrafficAnalyzer] Video Source: {video_source}")
        print(f"[VideoTrafficAnalyzer] Source FPS: {source_fps:.1f} | Target Display FPS: {target_fps} (Step: {step_frames} frames)")
        print(f"[VideoTrafficAnalyzer] Congestion Speed Threshold: < {self.jam_speed_thresh_kmh} km/h")

        frame_idx = 0
        start_time = time.time()
        last_emit_time = 0.0

        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if frame_idx % step_frames != 0:
                    continue

                curr_time = time.time()
                video_time_sec = frame_idx / source_fps if source_fps > 0 else (curr_time - start_time)
                is_emit_turn = (video_time_sec - last_emit_time) >= self.sample_interval_sec

                annotated_frame, alert_info = self._analyze_single_frame(
                    frame=frame, 
                    frame_idx=frame_idx, 
                    should_emit=is_emit_turn,
                    timestamp=video_time_sec
                )

                if is_emit_turn:
                    last_emit_time = video_time_sec

                if display_preview:
                    preview_img = self._resize_maintain_aspect(annotated_frame)
                    cv2.imshow(f"Traffic Surveillance - {self.camera_id}", preview_img)
                    
                    key = cv2.waitKey(delay_ms) & 0xFF
                    if key in [ord('q'), 27]:
                        break
                    elif key in [ord('h'), ord('H')]:
                        self.vehicle_detector.enable_heatmap = not self.vehicle_detector.enable_heatmap
                        state_str = "ENABLED" if self.vehicle_detector.enable_heatmap else "DISABLED"
                        print(f"\n[TrafficSurveillance] Heatmap toggled: {state_str}")

                if max_duration_sec and (curr_time - start_time) > max_duration_sec:
                    break

        finally:
            cap.release()
            if display_preview:
                cv2.destroyAllWindows()

            # Export summary image and JSON report to outputs/
            if hasattr(self, '_last_summary') and self._last_summary is not None:
                outputs_dir = Path(__file__).resolve().parent / "outputs"
                outputs_dir.mkdir(parents=True, exist_ok=True)
                source_name = Path(video_source).stem

                out_img_path = outputs_dir / f"{source_name}_traffic.jpg"
                out_json_path = outputs_dir / f"{source_name}_traffic.json"

                if self._last_summary.get("annotated_frame") is not None:
                    cv2.imwrite(str(out_img_path), self._last_summary["annotated_frame"])

                json_payload = self._last_summary.get("data", {})
                with open(out_json_path, "w", encoding="utf-8") as f:
                    import json
                    json.dump(json_payload, f, indent=2, ensure_ascii=False)

                print(f"\n[TrafficCounter] Summary Results Exported:")
                print(f" -> Output Image: {out_img_path}")
                print(f" -> Output JSON:  {out_json_path}\n")

            print("[VideoTrafficAnalyzer] Processing completed.")

    def _analyze_single_frame(self, frame, frame_idx: int, should_emit: bool, timestamp: Optional[float] = None):
        total_vehicles, breakdown, annotated_frame, avg_speed_kmh, tripwire_info = self.vehicle_detector.detect_and_count(
            frame, timestamp=timestamp
        )
        total_crossed = tripwire_info.get("total_crossed", 0)
        flow_vpm = tripwire_info.get("flow_vpm", 0.0)
        stopped_vehicles = tripwire_info.get("stopped_vehicles", 0)
        slow_vehicles = tripwire_info.get("slow_vehicles", 0)
        speed_measured_count = tripwire_info.get("speed_measured_count", 0)
        has_speed_data = (speed_measured_count >= 2)

        has_water, water_confidence, water_mask = self.water_detector.detect_water(frame)

        # Smart Multi-factor Traffic Congestion Analysis
        if has_speed_data:
            # Video mode with active speed measurements
            is_low_speed = (avg_speed_kmh < self.jam_speed_thresh_kmh)
            slow_or_stopped_ratio = (stopped_vehicles + slow_vehicles) / float(max(1, total_vehicles))
            is_high_stop_ratio = (slow_or_stopped_ratio >= 0.35 and total_vehicles >= self.congested_threshold)
            
            is_jammed = (
                (total_vehicles >= self.congested_threshold and (is_low_speed or is_high_stop_ratio))
                or (total_vehicles >= 25)
            )
            is_crowded = (total_vehicles >= self.congested_threshold or avg_speed_kmh < 22.0)
            speed_desc = f"{avg_speed_kmh:.1f} km/h"
        else:
            # Static image or initial tracking warmup: evaluate via spatial vehicle density
            is_jammed = (total_vehicles >= 20)
            is_crowded = (total_vehicles >= self.congested_threshold)
            speed_desc = "Static Scene" if frame_idx <= 2 else "Initializing..."

        if is_jammed:
            congestion_level = 3
            congestion_status = "HEAVY_CONGESTION"
        elif is_crowded:
            congestion_level = 2
            congestion_status = "CROWDED"
        else:
            congestion_level = 1
            congestion_status = "CLEAR"

        alert_type = "NORMAL"
        alert_level = "INFO"

        if is_jammed and has_water:
            alert_type = "WATER_AND_CONGESTION"
            alert_level = "CRITICAL"
            message = f"CRITICAL: Road surface water detected with heavy traffic congestion ({total_vehicles} vehicles, Speed: {speed_desc})."
        elif is_jammed:
            alert_type = "TRAFFIC_JAM"
            alert_level = "WARNING"
            message = f"TRAFFIC CONGESTION: Heavy vehicle bottleneck ({total_vehicles} vehicles, Speed: {speed_desc}, Stopped: {stopped_vehicles})."
        elif has_water:
            alert_type = "WATER_DETECTED"
            alert_level = "WARNING"
            message = f"Wet road surface / standing water detected (Vehicle count: {total_vehicles}, Speed: {speed_desc})."
        else:
            flow_status = "Smooth Flow" if not is_crowded else "Heavy Density, Flowing"
            message = f"Traffic: {flow_status} ({total_vehicles} vehicles, Speed: {speed_desc}, Flow: {int(flow_vpm)} veh/min)."

        flow_tag = "[!] JAMMED" if is_jammed else ("[OK] FLOWING" if (has_speed_data and avg_speed_kmh >= self.jam_speed_thresh_kmh) else "[...] MEASURING...")
        water_tag = f"[!] WATER ({int(water_confidence * 100)}%)" if has_water else "[OK] DRY"
        status_tag = "[!]" if (is_jammed or has_water) else "[OK]"
        ts_str = f"{timestamp:05.1f}s" if timestamp is not None else "00.0s"
        print(f"[{self.camera_id} | {ts_str}] {status_tag} Total: {total_vehicles:2d} veh (Moto:{breakdown.get('motorcycle',0):2d}, Car:{breakdown.get('car',0):2d}, Stopped:{stopped_vehicles:2d}) | Flow: {int(flow_vpm):2d} vpm | Speed: {speed_desc} {flow_tag} | Status: {congestion_status}")

        # Render HUD Dashboard
        img_h, img_w = annotated_frame.shape[:2]
        hud_scale = max(0.65, min(1.8, img_w / 900.0))
        hud_thick = max(1, int(hud_scale * 1.8))
        banner_h = int(108 * hud_scale)
        hud_color = (0, 0, 255) if (alert_level == "CRITICAL" or is_jammed) else ((0, 165, 255) if (alert_level == "WARNING" or is_crowded) else (0, 255, 0))

        cv2.rectangle(annotated_frame, (10, 10), (min(img_w - 10, int(760 * hud_scale)), banner_h), (20, 20, 20), -1)
        cv2.rectangle(annotated_frame, (10, 10), (min(img_w - 10, int(760 * hud_scale)), banner_h), hud_color, 2)
        
        status_speed_text = "JAMMED" if is_jammed else ("FLOWING" if (has_speed_data and avg_speed_kmh >= self.jam_speed_thresh_kmh) else ("CROWDED" if is_crowded else "CLEAR"))
        
        cv2.putText(
            annotated_frame, 
            f"VEHICLES: {total_vehicles} | SPEED: {speed_desc} ({status_speed_text})", 
            (20, int(32 * hud_scale)), 
            cv2.FONT_HERSHEY_SIMPLEX, 
            hud_scale * 0.72, 
            (255, 255, 255), 
            hud_thick, 
            cv2.LINE_AA
        )
        
        cv2.putText(
            annotated_frame, 
            f"TRIPWIRE FLOW: {total_crossed} CROSSED | {int(flow_vpm)} VEH/MIN", 
            (20, int(62 * hud_scale)), 
            cv2.FONT_HERSHEY_SIMPLEX, 
            hud_scale * 0.70, 
            (255, 215, 0), 
            hud_thick, 
            cv2.LINE_AA
        )
        
        cv2.putText(
            annotated_frame, 
            f"STATUS: {alert_type} [{alert_level}] | WATER: {'YES' if has_water else 'NO'}", 
            (20, int(92 * hud_scale)), 
            cv2.FONT_HERSHEY_SIMPLEX, 
            hud_scale * 0.68, 
            hud_color, 
            hud_thick, 
            cv2.LINE_AA
        )

        if should_emit:
            prefix = "alert" if alert_level in ["WARNING", "CRITICAL"] else "snapshot"
            sec_str = f"{timestamp:05.1f}s" if timestamp is not None else f"{int(time.time())}s"
            evidence_filename = f"{prefix}_{self.camera_id}_frame{frame_idx}_{sec_str}.jpg"
            evidence_path = os.path.join(self.output_evidence_dir, evidence_filename)
            cv2.imwrite(evidence_path, annotated_frame)

            event = TrafficIncidentEvent(
                camera_id=self.camera_id,
                location=self.location,
                vehicle_count=total_vehicles,
                vehicle_breakdown=breakdown,
                is_congested=is_jammed,
                avg_speed_kmh=avg_speed_kmh if has_speed_data else None,
                total_crossed_tripwire=total_crossed,
                flow_vehicles_per_min=flow_vpm,
                has_water=has_water,
                water_confidence=water_confidence,
                alert_type=alert_type,
                alert_level=alert_level,
                evidence_frame_path=evidence_path,
                message=message
            )
            self.emitter.emit(event)

        self._last_summary = {
            "annotated_frame": annotated_frame.copy(),
            "data": {
                "timestamp": datetime.now().isoformat(),
                "camera_id": self.camera_id,
                "location": self.location,
                "frame_index": frame_idx,
                "traffic_status": congestion_status,
                "congestion_level": congestion_level,
                "congestion_description": message,
                "total_vehicles_present": total_vehicles,
                "vehicle_breakdown": breakdown,
                "tripwire_counting": {
                    "total_crossed": total_crossed,
                    "flow_vehicles_per_min": round(flow_vpm, 1)
                },
                "average_speed_kmh": round(avg_speed_kmh, 1) if (has_speed_data and avg_speed_kmh is not None) else None,
                "stopped_vehicles": stopped_vehicles,
                "slow_vehicles": slow_vehicles,
                "is_jammed": is_jammed,
                "has_water": has_water,
                "water_confidence": round(water_confidence, 2),
                "alert_level": alert_level
            }
        }

        return annotated_frame, {"alert_type": alert_type, "alert_level": alert_level, "avg_speed_kmh": avg_speed_kmh}
