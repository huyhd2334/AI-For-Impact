import os
import json
import logging
import requests
from typing import Optional, Callable
from .schemas import TrafficIncidentEvent

logger = logging.getLogger("EventEmitter")

class IncidentEventEmitter:
    """
    Quản lý việc phát tín hiệu (Emit Event) khi phát hiện sự cố giao thông hoặc ngập lụt.
    """

    def __init__(
        self, 
        webhook_url: Optional[str] = None, 
        output_dir: str = "events_outbox",
        custom_handler: Optional[Callable[[TrafficIncidentEvent], None]] = None
    ):
        self.webhook_url = webhook_url or os.getenv("BACKEND_WEBHOOK_URL")
        self.output_dir = output_dir
        self.custom_handler = custom_handler
        os.makedirs(self.output_dir, exist_ok=True)

    def emit(self, event: TrafficIncidentEvent) -> None:
        """Phát sự kiện qua các kênh cấu hình."""
        # 1. Lưu file JSON outbox (phân biệt sự cố incident và dữ liệu định kỳ telemetry)
        prefix = "incident" if event.alert_level in ["WARNING", "CRITICAL"] else "telemetry"
        file_path = os.path.join(self.output_dir, f"{prefix}_{event.camera_id}_{event.event_id[:8]}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(event.model_dump_json(indent=2))

        # 2. In ra Terminal / Console
        self._log_console(event, file_path)

        # 3. Gửi Webhook nếu có cấu hình
        if self.webhook_url:
            self._send_webhook(event)

        # 4. Custom Callback handler nếu có
        if self.custom_handler:
            self.custom_handler(event)

    def _log_console(self, event: TrafficIncidentEvent, json_path: Optional[str] = None) -> None:
        if event.alert_level in ["WARNING", "CRITICAL"]:
            print("\n" + "=" * 65)
            print(f"[{event.alert_level}] SỰ KIỆN: {event.alert_type} | CAMERA: {event.camera_id}")
            print(f"- Thời gian: {event.timestamp}")
            flow_tag = "[!] ÙN TẮC" if event.is_congested else "[✔] LƯU THÔNG TỐT"
            print(f"- Mật độ xe: {event.vehicle_count} phương tiện ({flow_tag})")
            if event.avg_speed_kmh is not None and event.avg_speed_kmh > 0:
                speed_status = "[!] DI CHUYỂN CHẬM" if event.is_congested else "[✔] LƯU THÔNG ỔN ĐỊNH"
                print(f"- Vận tốc trung bình: {event.avg_speed_kmh:.1f} km/h ({speed_status})")
            if event.has_water:
                print(f"- Tình trạng nước: [!] CÓ NƯỚC NGẬP / TRƠN TRƯỢT ({int(event.water_confidence * 100)}%)")
            else:
                print(f"- Tình trạng nước: [✔] KHÔ RÁO")
            print(f"- Chi tiết thông báo: {event.message}")
            if event.evidence_frame_path:
                print(f"- Ảnh snapshot: {event.evidence_frame_path}")
            if json_path:
                print(f"- File JSON: {json_path}")
            print("=" * 65 + "\n")
        else:
            json_name = os.path.basename(json_path) if json_path else ""
            img_name = os.path.basename(event.evidence_frame_path) if event.evidence_frame_path else ""
            print(f"   [✔] ĐÃ LƯU ĐỊNH KỲ: JSON [{json_name}] | Ảnh [{img_name}]")

    def _send_webhook(self, event: TrafficIncidentEvent) -> None:
        try:
            headers = {"Content-Type": "application/json"}
            resp = requests.post(self.webhook_url, data=event.model_dump_json(), headers=headers, timeout=3.0)
            if resp.status_code == 200:
                print(f"[EventEmitter] Đã push webhook thành công tới {self.webhook_url}")
            else:
                print(f"[EventEmitter] Webhook trả về status {resp.status_code}")
        except Exception as e:
            print(f"[EventEmitter] Không thể gửi Webhook: {e}")
