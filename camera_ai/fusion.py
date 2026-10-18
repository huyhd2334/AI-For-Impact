import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional, List
from pathlib import Path
import cv2
import numpy as np

from .schemas import (
    CameraObservationEvent,
    FloodStatus,
    FinalFloodStatus,
    FloodSeverity,
    TrafficCongestionStatus
)


class CameraResultFusion:
    """
    Fuses outputs from:
    1. Road Segmentation (cached road_mask)
    2. Flood Reasoning (Gemini Flash / CV reasoning)
    3. Water Depth Estimator (depth & credibility)
    4. Traffic Counter (vehicle flow, stopped count, speed, jam detection)
    
    Produces standard CameraObservationEvent (SE Contract v1) and evidence capture.
    """

    def __init__(self, evidence_dir: Optional[str] = None):
        curr_dir = Path(__file__).resolve().parent
        self.evidence_dir = Path(evidence_dir) if evidence_dir else curr_dir.parent / "evidence_captures"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def fuse(
        self,
        camera_id: str,
        frame: np.ndarray,
        road_mask: np.ndarray,
        sidewalk_mask: np.ndarray,
        road_cached: bool,
        flood_reasoning: Dict[str, Any],
        depth_estimation: Dict[str, Any],
        traffic_data: Dict[str, Any],
        source_revision: int = 1,
        observed_at: Optional[str] = None,
        save_evidence: bool = True
    ) -> Tuple[CameraObservationEvent, np.ndarray]:
        now_utc = datetime.now(timezone.utc).isoformat()
        obs_time = observed_at or now_utc
        event_id = f"{camera_id.lower()}_rev{source_revision:04d}_{uuid.uuid4().hex[:6]}"

        # Check thumbnail image quality (blur / extreme darkness / occlusion)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        mean_brightness = float(np.mean(gray))
        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        is_low_quality = (mean_brightness < 18.0) or (laplacian_var < 30.0)

        # 1. Fuse Flood Decision (Mapping to: UNKNOWN, Không ngập, Ngập đi được, Ngập nhiều)
        is_flooded_reason = bool(flood_reasoning.get("is_flooded", False))
        reason_severity = str(flood_reasoning.get("severity", "none")).lower()
        reason_conf = float(flood_reasoning.get("confidence", 0.90))
        road_water_ratio = float(flood_reasoning.get("road_water_ratio", 0.0))
        depth_cm = depth_estimation.get("water_depth_cm")
        depth_conf = depth_estimation.get("depth_confidence")
        depth_method = depth_estimation.get("depth_method")

        if is_low_quality:
            flood_status = FloodStatus.UNCERTAIN
            flood_status_vn = FinalFloodStatus.UNKNOWN
            severity = FloodSeverity.NONE
            safe_for_bikes = True
            confidence = 0.35
        elif not is_flooded_reason and road_water_ratio < 0.035:
            flood_status = FloodStatus.NOT_FLOODED
            flood_status_vn = FinalFloodStatus.NOT_FLOODED
            severity = FloodSeverity.NONE
            safe_for_bikes = True
            confidence = max(0.92, reason_conf)
        elif reason_conf >= 0.70 or road_water_ratio >= 0.08:
            flood_status = FloodStatus.FLOODED
            confidence = min(0.98, max(reason_conf, depth_conf or 0.80))
            if (depth_cm is not None and depth_cm >= 25.0) or road_water_ratio >= 0.25 or reason_severity == "severe":
                severity = FloodSeverity.SEVERE
                safe_for_bikes = False
                flood_status_vn = FinalFloodStatus.HEAVY_FLOOD
            elif (depth_cm is not None and depth_cm >= 15.0) or road_water_ratio >= 0.12 or reason_severity == "moderate":
                severity = FloodSeverity.MODERATE
                safe_for_bikes = False
                flood_status_vn = FinalFloodStatus.HEAVY_FLOOD
            else:
                severity = FloodSeverity.MINOR
                safe_for_bikes = bool(flood_reasoning.get("safe_for_bikes", True))
                flood_status_vn = FinalFloodStatus.PASSABLE_FLOOD
        else:
            flood_status = FloodStatus.UNCERTAIN
            flood_status_vn = FinalFloodStatus.UNKNOWN
            severity = FloodSeverity.MINOR
            safe_for_bikes = True
            confidence = round(reason_conf, 2)

        # 2. Fuse Traffic Congestion Decision for Thumbnail/Snapshot (Mapping to: UNKNOWN, CLEAR, CROWDED, HEAVY_CONGESTION)
        total_vehicles = int(traffic_data.get("total_vehicles", 0))
        vehicle_breakdown = traffic_data.get("breakdown", {})
        stopped_vehicles = int(traffic_data.get("stopped_vehicles", 0))
        slow_vehicles = int(traffic_data.get("slow_vehicles", 0))
        avg_speed_kmh = traffic_data.get("avg_speed_kmh")
        flow_vpm = float(traffic_data.get("flow_vpm", 0.0))
        is_jammed = bool(traffic_data.get("is_jammed", False))

        # Calculate Road Occupancy Ratio on snapshot
        boxes = traffic_data.get("boxes", [])
        road_pixels = int(np.count_nonzero(road_mask)) if (road_mask is not None and np.any(road_mask > 0)) else int(frame.shape[0] * frame.shape[1] * 0.40)
        total_veh_area = 0.0
        for b in boxes:
            if len(b) == 4:
                bw = max(0, b[2] - b[0])
                bh = max(0, b[3] - b[1])
                total_veh_area += (bw * bh)
        road_occupancy_ratio = min(1.0, round(total_veh_area / max(1, road_pixels), 4))

        if is_low_quality:
            traffic_status = TrafficCongestionStatus.UNKNOWN
        elif is_jammed or road_occupancy_ratio >= 0.45 or (total_vehicles >= 20 and road_occupancy_ratio >= 0.32):
            traffic_status = TrafficCongestionStatus.HEAVY_CONGESTION
        elif road_occupancy_ratio >= 0.14 or total_vehicles >= 8 or (avg_speed_kmh is not None and avg_speed_kmh < 22.0):
            traffic_status = TrafficCongestionStatus.CROWDED
        else:
            traffic_status = TrafficCongestionStatus.CLEAR

        # 3. Construct Unified Reasoning String
        depth_str = f", độ sâu ước tính ~{depth_cm:.1f} cm ({depth_method})" if depth_cm else ""
        spd_str = f", tốc độ TB: {avg_speed_kmh:.1f} km/h" if avg_speed_kmh is not None else ""
        occ_str = f", chiếm dụng: {int(road_occupancy_ratio * 100)}% lòng đường"
        reason_text = (
            f"Trạng thái ngập: {flood_status_vn.value} ({flood_status.value.upper()}[{severity.value.upper()}]{depth_str}). "
            f"Giao thông: {traffic_status.value} ({total_vehicles} xe{occ_str}, {stopped_vehicles} xe dừng{spd_str}). "
            f"{flood_reasoning.get('reason', '')}"
        )

        # 4. Render Composite Visual Evidence
        annotated = frame.copy()
        h, w = annotated.shape[:2]

        # Draw road boundary outline from cached road mask
        if road_mask is not None and np.any(road_mask > 0):
            contours, _ = cv2.findContours((road_mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(annotated, contours, -1, (255, 165, 0), 2, cv2.LINE_AA)

        # Overlay detected water mask with translucent blue
        water_mask = flood_reasoning.get("water_mask")
        if water_mask is not None and np.any(water_mask > 0):
            water_idx = water_mask > 0
            water_color = np.full_like(annotated, (255, 120, 0)) # BGR cyan-blue
            annotated[water_idx] = cv2.addWeighted(annotated, 0.65, water_color, 0.35, 0)[water_idx]

        # Blend traffic counter annotations if present
        annotated_traffic = traffic_data.get("annotated_frame")
        if annotated_traffic is not None and annotated_traffic.shape == annotated.shape:
            annotated = cv2.addWeighted(annotated, 0.50, annotated_traffic, 0.50, 0)

        # Render Professional HUD Dashboard Banner
        hud_scale = max(0.65, min(1.8, w / 950.0))
        hud_thick = max(1, int(hud_scale * 1.8))
        banner_h = int(105 * hud_scale)

        hud_color = (0, 0, 255) if (flood_status_vn == FinalFloodStatus.HEAVY_FLOOD or traffic_status == TrafficCongestionStatus.HEAVY_CONGESTION) else (
            (0, 165, 255) if (flood_status_vn == FinalFloodStatus.PASSABLE_FLOOD or traffic_status == TrafficCongestionStatus.CROWDED) else (0, 255, 0)
        )

        cv2.rectangle(annotated, (10, 10), (min(w - 10, int(860 * hud_scale)), banner_h), (20, 20, 20), -1)
        cv2.rectangle(annotated, (10, 10), (min(w - 10, int(860 * hud_scale)), banner_h), hud_color, 2)

        flood_badge = f"NGAP: {flood_status_vn.value.upper()}"
        if depth_cm is not None:
            flood_badge += f" | ~{depth_cm:.1f}cm"

        cv2.putText(
            annotated,
            f"CAMERA: {camera_id} | {flood_badge}",
            (20, int(30 * hud_scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            hud_scale * 0.70,
            (255, 255, 255),
            hud_thick,
            cv2.LINE_AA
        )

        speed_text = f"{avg_speed_kmh:.1f} km/h" if avg_speed_kmh is not None else "N/A"
        cv2.putText(
            annotated,
            f"GIAO THONG: {traffic_status.value} | {total_vehicles} VEH (OCC: {int(road_occupancy_ratio*100)}%) | SPD: {speed_text}",
            (20, int(60 * hud_scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            hud_scale * 0.65,
            (255, 215, 0),
            hud_thick,
            cv2.LINE_AA
        )

        road_status_str = "REUSED_CACHE (0ms)" if road_cached else "CALIBRATED_NEW"
        bikes_str = "DI DUOC" if safe_for_bikes else "NGUY HIEM"
        cv2.putText(
            annotated,
            f"ROAD MASK: {road_status_str} | XE MAY: {bikes_str} | CONF: {int(confidence*100)}%",
            (20, int(88 * hud_scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            hud_scale * 0.60,
            hud_color,
            hud_thick,
            cv2.LINE_AA
        )

        # Save evidence file if requested
        evidence_ref = None
        if save_evidence:
            filename = f"obs_{camera_id}_{event_id}.jpg"
            evidence_path = self.evidence_dir / filename
            cv2.imwrite(str(evidence_path), annotated)
            evidence_ref = str(evidence_path)

        # Build CameraObservationEvent (SE Contract v1)
        event = CameraObservationEvent(
            event_id=event_id,
            camera_id=camera_id,
            observed_at=obs_time,
            source_updated_at=now_utc,
            source_revision=source_revision,
            flood_status=flood_status,
            flood_status_vn=flood_status_vn,
            confidence=confidence,
            model_version="saferoute-fusion-v1.0",
            severity=severity,
            water_depth_cm=depth_cm,
            depth_confidence=depth_conf,
            depth_method=depth_method,
            safe_for_bikes=safe_for_bikes,
            road_water_ratio=round(road_water_ratio, 4),
            traffic_status=traffic_status,
            is_jammed=is_jammed,
            road_occupancy_ratio=road_occupancy_ratio,
            total_vehicles=total_vehicles,
            vehicle_breakdown=vehicle_breakdown,
            stopped_vehicles=stopped_vehicles,
            slow_vehicles=slow_vehicles,
            avg_speed_kmh=round(avg_speed_kmh, 1) if avg_speed_kmh is not None else None,
            flow_vpm=round(flow_vpm, 1),
            road_mask_cached=road_cached,
            evidence_ref=evidence_ref,
            reason=reason_text
        )

        return event, annotated
