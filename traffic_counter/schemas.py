import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class TrafficDensityLevel(str, Enum):
    LOW = "low"            # Smooth traffic flow
    MODERATE = "moderate"  # Moderate vehicle density
    HEAVY = "heavy"        # High density, slow moving
    CONGESTED = "congested" # Severe congestion / gridlock


class HazardType(str, Enum):
    WATER_DETECTED = "water_detected"              # Standing water detected on road surface
    TRAFFIC_CONGESTION = "traffic_congestion"      # Traffic congestion
    WATER_AND_CONGESTION = "water_and_congestion"  # Traffic congestion compounded with wet surface
    CLEAR = "clear"                                # Clear and dry road


class TrafficIncidentEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_type: str = "camera_stream"
    camera_id: str
    location: Dict[str, float] = Field(
        ..., 
        description="Camera GPS coordinates: {'latitude': float, 'longitude': float}"
    )
    
    # Vehicle statistics & speed
    vehicle_count: int = Field(..., description="Total number of visible vehicles (Min: 0)")
    vehicle_breakdown: Dict[str, int] = Field(
        default_factory=dict, 
        description="Vehicle classification counts: motorcycle, car, bus, truck, bicycle"
    )
    is_congested: bool = Field(False, description="True if vehicle count >= threshold and avg speed < speed threshold")
    avg_speed_kmh: Optional[float] = Field(None, description="Average vehicle speed in km/h (Min: 0.0)")
    total_crossed_tripwire: Optional[int] = Field(None, description="Total vehicle count crossing the tripwire (Min: 0)")
    flow_vehicles_per_min: Optional[float] = Field(None, description="Vehicle throughput per minute (Min: 0.0)")

    # Water presence detection
    has_water: bool = Field(False, description="Water presence detected on road surface (True/False)")
    water_confidence: float = Field(0.0, description="Detection confidence for water presence (Range: 0.0 to 1.0)")

    # Alert severity and messaging
    alert_type: str = Field(..., description="WATER_AND_CONGESTION | WATER_DETECTED | TRAFFIC_JAM | NORMAL")
    alert_level: str = Field("INFO", description="Alert severity: INFO | WARNING | CRITICAL")
    evidence_frame_path: Optional[str] = None
    message: str = Field(..., description="Human-readable event summary")
