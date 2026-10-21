import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import cv2
import numpy as np

from .schemas import (
    CameraObservationEvent,
    CameraCurrentState,
    FloodStatus,
    FinalFloodStatus,
    FloodSeverity,
    TrafficCongestionStatus
)
from .road_manager import RoadMaskManager
from .flood_reasoner import FloodReasoner
from .depth_estimator import WaterDepthEstimator
from .fusion import CameraResultFusion
from traffic_counter.vehicle_counter import VehicleDetector


class UnifiedCameraAnalyzer:
    """
    Unified Camera AI Pipeline integrating:
    1. Fixed camera road segmentation & ROI caching
    2. Flood reasoning on road corridor (Gemini Flash + CV)
    3. Flood detector & depth credibility estimator
    4. Multi-frame traffic counter & congestion analyzer
    5. Result Fusion (SE Camera Observation Contract v1)
    """

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        road_model_type: str = "b0",
        vehicle_model_name: str = "vn_traffic_surveillance.pt",
        cache_dir: Optional[str] = None,
        evidence_dir: Optional[str] = None
    ):
        curr_dir = Path(__file__).resolve().parent
        self.cache_dir = Path(cache_dir) if cache_dir else curr_dir.parent / "road_segmentation" / "cache"
        self.evidence_dir = Path(evidence_dir) if evidence_dir else curr_dir.parent / "evidence_captures"

        # Initialize Sub-Modules
        self.road_manager = RoadMaskManager(cache_dir=str(self.cache_dir), model_type=road_model_type)
        self.flood_reasoner = FloodReasoner(api_key=gemini_api_key)
        self.depth_estimator = WaterDepthEstimator()
        self.vehicle_detector = VehicleDetector(model_name=vehicle_model_name, confidence_thresh=0.18)
        self.fusion = CameraResultFusion(evidence_dir=str(self.evidence_dir))

        # Memory tables matching SE specification (schema: camera_ai)
        # camera_current_state: latest state per camera_id
        self._current_states: Dict[str, CameraCurrentState] = {}
        # camera_state_events: history event log per camera_id
        self._event_history: Dict[str, List[CameraObservationEvent]] = {}
        self._camera_revisions: Dict[str, int] = {}

    def calibrate_camera(
        self, 
        camera_id: str, 
        reference_frame: np.ndarray, 
        force: bool = False
    ) -> Tuple[np.ndarray, np.ndarray, float, List[List[List[int]]]]:
        """
        Calibrate road mask for a camera_id and cache it for all subsequent frames.
        """
        return self.road_manager.calibrate(camera_id, reference_frame, force=force)

    def _get_tripwires(self, camera_id: str) -> Optional[List[Dict[str, Any]]]:
        if not hasattr(self, '_camera_tripwires_cache'):
            self._camera_tripwires_cache = {}
        if camera_id in self._camera_tripwires_cache:
            return self._camera_tripwires_cache[camera_id]

        cache_dir = Path(__file__).resolve().parent.parent / "road_segmentation" / "cache"
        tw_file = cache_dir / f"{camera_id}_tripwires.json"
        roi_file = cache_dir / f"{camera_id}_road_roi.json"

        tripwires = []
        if tw_file.exists():
            try:
                with open(tw_file, "r", encoding="utf-8") as f:
                    tripwires = json.load(f).get("tripwires", [])
            except Exception:
                pass
        elif roi_file.exists():
            try:
                with open(roi_file, "r", encoding="utf-8") as f:
                    tripwires = json.load(f).get("tripwires", [])
            except Exception:
                pass

        self._camera_tripwires_cache[camera_id] = tripwires
        return tripwires

    def analyze_frame(
        self,
        camera_id: str,
        frame: np.ndarray,
        timestamp: Optional[float] = None,
        use_llm: bool = True,
        save_evidence: bool = True,
        source_hint: str = ""
    ) -> Tuple[CameraObservationEvent, np.ndarray]:
        """
        Executes the integrated analysis pipeline on a single frame.
        """
        h, w = frame.shape[:2]
        cur_rev = self._camera_revisions.get(camera_id, 0) + 1
        self._camera_revisions[camera_id] = cur_rev

        # Step 1: Road Segmentation (Retrieve cached road_mask or auto-calibrate)
        road_mask, sidewalk_mask, is_cached = self.road_manager.get_road_mask(
            camera_id=camera_id,
            current_frame=frame,
            auto_calibrate_if_missing=True
        )

        # Step 2: Traffic Counter (Vehicles, speed, stopped vehicles, congestion)
        camera_tripwires = self._get_tripwires(camera_id)
        total_vehicles, breakdown, annotated_traffic, avg_speed_kmh, tripwire_info = self.vehicle_detector.detect_and_count(
            frame, timestamp=timestamp, tripwires=camera_tripwires
        )

        stopped_vehicles = tripwire_info.get("stopped_vehicles", 0)
        slow_vehicles = tripwire_info.get("slow_vehicles", 0)
        parked_vehicles = tripwire_info.get("parked_vehicles", 0)
        active_vehicles = tripwire_info.get("active_vehicles", 0)
        flow_vpm = tripwire_info.get("flow_vpm", 0.0)

        # Intelligent Multi-factor Congestion Analysis on active moving traffic only
        is_jammed = False
        speed_measured_count = tripwire_info.get("speed_measured_count", 0)
        if avg_speed_kmh is not None and speed_measured_count >= 2 and avg_speed_kmh < 12.0 and active_vehicles >= 6:
            slow_or_stopped = (stopped_vehicles + slow_vehicles) / float(max(1, active_vehicles))
            if slow_or_stopped >= 0.45 or avg_speed_kmh < 6.0:
                is_jammed = True

        detected_boxes = [v.get("box") for v in getattr(self.vehicle_detector, 'last_detected_vehicles', []) if "box" in v]
        if not detected_boxes:
            detected_boxes = [td['last_box'] for tid, td in getattr(self.vehicle_detector, 'track_details', {}).items() if 'last_box' in td]

        traffic_data = {
            "total_vehicles": total_vehicles,
            "active_vehicles": active_vehicles,
            "parked_vehicles": parked_vehicles,
            "breakdown": breakdown,
            "stopped_vehicles": stopped_vehicles,
            "slow_vehicles": slow_vehicles,
            "avg_speed_kmh": avg_speed_kmh,
            "flow_vpm": flow_vpm,
            "is_jammed": is_jammed,
            "tripwire_info": tripwire_info,
            "boxes": detected_boxes,
            "annotated_frame": annotated_traffic
        }

        # Step 3: Flood Reasoning (Frame mới + road_mask -> có nước trên đường? bao nhiêu?)
        flood_reasoning, water_mask, road_water_ratio = self.flood_reasoner.reason(
            frame=frame,
            road_mask=road_mask,
            use_llm=use_llm,
            source_hint=source_hint,
            camera_id=camera_id
        )
        flood_reasoning["road_water_ratio"] = road_water_ratio
        flood_reasoning["water_mask"] = water_mask

        # Step 4: SOTA Flood Detector & Water Depth Estimator (Đo mực nước đa tầng)
        detected_objects = []
        detector_objs = getattr(self.vehicle_detector, 'last_detected_objects', None) or getattr(self.vehicle_detector, 'last_detected_vehicles', [])
        for obj in detector_objs:
            if 'box' in obj:
                detected_objects.append({
                    'box': obj['box'],
                    'class': obj.get('class', 'car'),
                    'conf': obj.get('conf', 0.6)
                })
        if not detected_objects:
            for tid, td in getattr(self.vehicle_detector, 'track_details', {}).items():
                if 'last_box' in td:
                    detected_objects.append({
                        'box': td['last_box'],
                        'class': td.get('class', 'car'),
                        'conf': 0.6
                    })
        depth_estimation = self.depth_estimator.estimate_depth(
            frame=frame,
            water_mask=water_mask,
            road_water_ratio=road_water_ratio,
            detected_vehicles=detected_objects,
            sidewalk_mask=sidewalk_mask
        )
        # Step 5: Result Fusion (Bộ kết hợp kết quả)
        event, annotated_composite = self.fusion.fuse(
            camera_id=camera_id,
            frame=frame,
            road_mask=road_mask,
            sidewalk_mask=sidewalk_mask,
            road_cached=is_cached,
            flood_reasoning=flood_reasoning,
            depth_estimation=depth_estimation,
            traffic_data=traffic_data,
            source_revision=cur_rev,
            observed_at=datetime.now(timezone.utc).isoformat(),
            save_evidence=save_evidence
        )

        # Update in-memory state tables
        if camera_id not in self._event_history:
            self._event_history[camera_id] = []
        self._event_history[camera_id].append(event)
        # Keep last 500 events
        if len(self._event_history[camera_id]) > 500:
            self._event_history[camera_id] = self._event_history[camera_id][-500:]

        self._current_states[camera_id] = CameraCurrentState(
            camera_id=camera_id,
            latest_event_id=event.event_id,
            latest_revision=event.source_revision,
            observed_at=event.observed_at,
            source_updated_at=event.source_updated_at,
            flood_status=event.flood_status,
            flood_status_vn=event.flood_status_vn,
            severity=event.severity,
            water_depth_cm=event.water_depth_cm,
            confidence=event.confidence,
            traffic_status=event.traffic_status,
            is_jammed=event.is_jammed,
            road_occupancy_ratio=event.road_occupancy_ratio,
            total_vehicles=event.total_vehicles,
            avg_speed_kmh=event.avg_speed_kmh,
            safe_for_bikes=event.safe_for_bikes,
            evidence_ref=event.evidence_ref,
            reason=event.reason
        )

        return event, annotated_composite

    def process_video(
        self,
        camera_id: str,
        video_source: str,
        target_fps: float = 5.0,
        sample_interval_sec: float = 2.0,
        max_duration_sec: Optional[float] = None,
        use_llm_on_alert: bool = True,
        output_video_path: Optional[str] = None
    ) -> List[CameraObservationEvent]:
        """
        Streams a video file through the camera analyzer pipeline.
        Emits periodic observation events and optionally saves an annotated output video.
        """
        cap = cv2.VideoCapture(video_source)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video source: {video_source}")

        source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step_frames = max(1, int(source_fps / target_fps))

        writer = None
        if output_video_path:
            out_p = Path(output_video_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            writer = cv2.VideoWriter(str(out_p), fourcc, target_fps, (width, height))

        if hasattr(self.vehicle_detector, "reset"):
            self.vehicle_detector.reset()
        if hasattr(self.flood_reasoner, "reset_temporal_state"):
            self.flood_reasoner.reset_temporal_state()

        emitted_events: List[CameraObservationEvent] = []
        frame_idx = 0
        last_emit_time = -999.0
        start_wall_time = time.time()

        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if frame_idx % step_frames != 0:
                    continue

                video_time_sec = frame_idx / source_fps
                if max_duration_sec and video_time_sec > max_duration_sec:
                    break

                is_emit_turn = (video_time_sec - last_emit_time) >= sample_interval_sec

                # Analyze frame
                event, annotated_composite = self.analyze_frame(
                    camera_id=camera_id,
                    frame=frame,
                    timestamp=video_time_sec,
                    use_llm=(use_llm_on_alert and is_emit_turn),
                    save_evidence=is_emit_turn
                )

                if writer is not None:
                    writer.write(annotated_composite)

                if is_emit_turn:
                    last_emit_time = video_time_sec
                    emitted_events.append(event)
                    print(
                        f"[{camera_id} | {video_time_sec:05.1f}s] "
                        f"Flood: {event.flood_status_vn.value} ({event.flood_status.value.upper()}) | "
                        f"Traffic: {event.traffic_status.value} ({event.total_vehicles} veh)"
                    )
        finally:
            cap.release()
            if writer is not None:
                writer.release()
                print(f"[+] Output video saved successfully: {output_video_path}")

        return emitted_events

    def get_current_state(self, camera_id: str) -> Optional[CameraCurrentState]:
        return self._current_states.get(camera_id)

    def get_events(self, camera_id: str, limit: int = 50, since_revision: int = 0) -> List[CameraObservationEvent]:
        events = self._event_history.get(camera_id, [])
        filtered = [e for e in events if e.source_revision > since_revision]
        return filtered[-limit:]
