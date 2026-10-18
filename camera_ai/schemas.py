import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class FloodStatus(str, Enum):
    FLOODED = "flooded"
    NOT_FLOODED = "not_flooded"
    UNCERTAIN = "uncertain"


class FinalFloodStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    NOT_FLOODED = "Không ngập"
    PASSABLE_FLOOD = "Ngập đi được"
    HEAVY_FLOOD = "Ngập nhiều"


class FloodSeverity(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"


class TrafficCongestionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    CLEAR = "CLEAR"
    CROWDED = "CROWDED"
    HEAVY_CONGESTION = "HEAVY_CONGESTION"


class CameraObservationEvent(BaseModel):
    """
    Standard Camera Observation Contract v1 (SafeRoute AI - System Specification 5.2 & SDD 7.1).
    Produced by the Camera AI deployable and read by the SafeRoute Worker.
    """
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    camera_id: str = Field(..., description="Unique camera ID in SafeRoute registry (e.g. CAM_BINHTHANH_01)")
    observed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC ISO-8601 timestamp when frame was captured"
    )
    source_updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC ISO-8601 timestamp when observation was generated"
    )
    source_revision: int = Field(1, description="Monotonically increasing sequence revision for this camera")
    
    # Flood Assessment (from Road Mask + Flood Reasoning + Water Depth Estimator)
    flood_status: FloodStatus = Field(
        FloodStatus.NOT_FLOODED,
        description="Overall flood state: 'flooded', 'not_flooded', or 'uncertain'"
    )
    flood_status_vn: FinalFloodStatus = Field(
        FinalFloodStatus.NOT_FLOODED,
        description="User-facing Vietnamese flood status: 'UNKNOWN' | 'Không ngập' | 'Ngập đi được' | 'Ngập nhiều'"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Overall confidence score (0.0 to 1.0)")
    model_version: str = Field("saferoute-fusion-v1.0", description="Model & pipeline version identifier")
    severity: FloodSeverity = Field(
        FloodSeverity.NONE,
        description="Observed severity category: 'none', 'minor', 'moderate', or 'severe'"
    )
    water_depth_cm: Optional[float] = Field(
        None, 
        ge=0.0,
        description="Estimated water depth in centimeters (null if not calibrated or dry)"
    )
    depth_confidence: Optional[float] = Field(
        None, 
        ge=0.0, 
        le=1.0,
        description="Credibility score for the estimated water depth"
    )
    depth_method: Optional[str] = Field(
        None,
        description="Method used for depth: 'anthropometric' | 'automotive' | 'geometric_water_coverage' | None"
    )
    safe_for_bikes: bool = Field(
        True,
        description="True if road conditions are passable for motorcycles and bicycles"
    )
    road_water_ratio: float = Field(
        0.0,
        description="Percentage of the calibrated road mask currently covered by water"
    )

    # Traffic Congestion Assessment (from Traffic Counter tracking across frames or snapshot occupancy)
    traffic_status: TrafficCongestionStatus = Field(
        TrafficCongestionStatus.CLEAR,
        description="Traffic state: 'UNKNOWN' | 'CLEAR' | 'CROWDED' | 'HEAVY_CONGESTION'"
    )
    is_jammed: bool = Field(False, description="True if vehicle bottleneck / gridlock is confirmed")
    road_occupancy_ratio: float = Field(
        0.0,
        description="Ratio of road mask occupied by detected vehicles (0.0 to 1.0) for snapshot congestion analysis"
    )
    total_vehicles: int = Field(0, ge=0, description="Total active vehicles detected on the road")
    vehicle_breakdown: Dict[str, int] = Field(
        default_factory=dict,
        description="Classification breakdown (motorcycle, car, bus, truck, bicycle)"
    )
    stopped_vehicles: int = Field(0, ge=0, description="Number of stationary vehicles (0 km/h)")
    slow_vehicles: int = Field(0, ge=0, description="Number of slow crawling vehicles (< 15 km/h)")
    avg_speed_kmh: Optional[float] = Field(None, ge=0.0, description="Average speed in km/h of moving traffic")
    flow_vpm: float = Field(0.0, ge=0.0, description="Vehicles per minute crossing virtual tripwire")

    # Meta & Evidence
    road_mask_cached: bool = Field(
        True, 
        description="True if pre-calibrated road mask was reused without re-segmentation overhead"
    )
    evidence_ref: Optional[str] = Field(
        None, 
        description="URI or relative file path to the annotated evidence image"
    )
    reason: str = Field(..., description="Concise explanation of detection cues and gate decision")


class CameraCurrentState(BaseModel):
    """
    Represents the latest active state row for a specific camera in camera_ai schema.
    """
    camera_id: str
    latest_event_id: str
    latest_revision: int
    observed_at: str
    source_updated_at: str
    flood_status: FloodStatus
    flood_status_vn: FinalFloodStatus = FinalFloodStatus.NOT_FLOODED
    severity: FloodSeverity
    water_depth_cm: Optional[float] = None
    confidence: float
    traffic_status: TrafficCongestionStatus
    is_jammed: bool
    road_occupancy_ratio: float = 0.0
    total_vehicles: int
    avg_speed_kmh: Optional[float] = None
    safe_for_bikes: bool = True
    evidence_ref: Optional[str] = None
    reason: str


class CalibrateRequest(BaseModel):
    camera_id: str = Field(..., description="Unique camera ID to calibrate (e.g. CAM_BINHTHANH_01)")
    image_base64: Optional[str] = Field(None, description="Base64 encoded image string (optional if file uploaded)")
    image_path: Optional[str] = Field(None, description="Server-local path to reference image")
    force: bool = Field(False, description="Force recalibration even if cache already exists")


class CalibrateResponse(BaseModel):
    camera_id: str
    status: str
    cached: bool
    road_ratio: float
    image_dimensions: List[int]
    polygons_count: int
    cache_path: str


class AnalyzeFrameRequest(BaseModel):
    camera_id: str = Field(..., description="Camera ID matching existing calibration or new stream")
    image_base64: Optional[str] = Field(None, description="Base64 encoded image string")
    image_path: Optional[str] = Field(None, description="Path to image on server")
    timestamp: Optional[float] = Field(None, description="Video timestamp in seconds or Unix timestamp")
    use_reasoning_llm: bool = Field(True, description="Enable Gemini Flash reasoning if available")
    save_evidence: bool = Field(True, description="Save annotated frame with HUD dashboard")


class AnalyzeVideoRequest(BaseModel):
    camera_id: str
    video_path: str
    target_fps: float = Field(5.0, description="Processing frame rate (e.g. 5.0 FPS)")
    sample_interval_sec: float = Field(2.0, description="Interval in seconds to emit observation events")
    save_evidence: bool = Field(True, description="Save alert evidence frames to disk")
    use_reasoning_llm: bool = Field(False, description="Run Gemini Flash on periodic keyframes")
