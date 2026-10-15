import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class TrafficDensityLevel(str, Enum):
    LOW = "low"            # Lưu thông thông thoáng
    MODERATE = "moderate"  # Mật độ vừa phải
    HEAVY = "heavy"        # Đông đúc, di chuyển chậm
    CONGESTED = "congested" # Kẹt cứng / ùn tắc kéo dài


class HazardType(str, Enum):
    WATER_DETECTED = "water_detected"              # Phát hiện có nước trên mặt đường
    TRAFFIC_CONGESTION = "traffic_congestion"      # Ùn tắc giao thông
    WATER_AND_CONGESTION = "water_and_congestion"  # Mặt đường có nước kết hợp ùn tắc
    CLEAR = "clear"


class TrafficIncidentEvent(BaseModel):
    """
    Sự kiện chuẩn được emit về hệ thống SafeRoute AI (Webhook / DB / Message Queue).
    Tập trung vào: Đếm xe + Phát hiện có nước đọng/ngập trên đường.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_type: str = "camera_stream"
    camera_id: str
    location: Dict[str, float] = Field(
        ..., 
        description="Toạ độ GPS camera: {'latitude': float, 'longitude': float}"
    )
    
    # 1. Thống kê phương tiện & Vận tốc
    vehicle_count: int = Field(..., description="Tổng số xe đếm được")
    vehicle_breakdown: Dict[str, int] = Field(
        default_factory=dict, 
        description="Phân loại xe: motorcycle, car, bus, truck"
    )
    is_congested: bool = Field(False, description="Mật độ xe cao và xe dừng/chạy chậm (Ùn tắc thực sự)")
    avg_speed_kmh: Optional[float] = Field(None, description="Vận tốc trung bình dòng xe (km/h) ước tính từ tracking")
    total_crossed_tripwire: Optional[int] = Field(None, description="Tổng số lượt xe đã vượt qua vạch ảo")
    flow_vehicles_per_min: Optional[float] = Field(None, description="Lưu lượng dòng xe qua vạch (xe/phút)")

    # 2. Phát hiện nước
    has_water: bool = Field(False, description="Mặt đường có xuất hiện nước đọng / ngập")
    water_confidence: float = Field(0.0, description="Độ tin cậy phát hiện nước (0.0 -> 1.0)")

    # 3. Sự kiện tổ hợp & Hành động
    alert_type: str = Field(..., description="FLOOD_AND_CONGESTION | FLOOD_DETECTED | TRAFFIC_JAM | NORMAL")
    alert_level: str = Field("INFO", description="CRITICAL | WARNING | INFO")
    evidence_frame_path: Optional[str] = None
    message: str = Field(..., description="Mô tả sự kiện gửi đến CSGT và Bản đồ")
