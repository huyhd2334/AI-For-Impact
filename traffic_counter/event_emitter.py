import os
import json
import logging
import requests
from typing import Optional, Callable
from .schemas import TrafficIncidentEvent

logger = logging.getLogger("EventEmitter")


class IncidentEventEmitter:
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
        # Save JSON event file (incident vs periodic telemetry)
        prefix = "incident" if event.alert_level in ["WARNING", "CRITICAL"] else "telemetry"
        file_path = os.path.join(self.output_dir, f"{prefix}_{event.camera_id}_{event.event_id[:8]}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(event.model_dump_json(indent=2))

        # Log summary to console
        self._log_console(event, file_path)

        # Dispatch webhook if configured
        if self.webhook_url:
            self._send_webhook(event)

        # Custom callback hook
        if self.custom_handler:
            self.custom_handler(event)

    def _log_console(self, event: TrafficIncidentEvent, json_path: Optional[str] = None) -> None:
        if event.alert_level in ["WARNING", "CRITICAL"]:
            print("\n" + "=" * 65)
            print(f"[{event.alert_level}] EVENT: {event.alert_type} | CAMERA: {event.camera_id}")
            print(f"- Timestamp: {event.timestamp}")
            flow_tag = "[!] CONGESTED" if event.is_congested else "[OK] SMOOTH FLOW"
            print(f"- Vehicle Density: {event.vehicle_count} vehicles ({flow_tag})")
            if event.avg_speed_kmh is not None and event.avg_speed_kmh > 0:
                speed_status = "[!] SLOW SPEED" if event.is_congested else "[OK] NORMAL SPEED"
                print(f"- Average Speed: {event.avg_speed_kmh:.1f} km/h ({speed_status})")
            if event.has_water:
                print(f"- Water Status: [!] WATER DETECTED ({int(event.water_confidence * 100)}%)")
            else:
                print(f"- Water Status: [OK] DRY SURFACE")
            print(f"- Message: {event.message}")
            if event.evidence_frame_path:
                print(f"- Snapshot: {event.evidence_frame_path}")
            if json_path:
                print(f"- JSON: {json_path}")
            print("=" * 65 + "\n")
        else:
            json_name = os.path.basename(json_path) if json_path else ""
            img_name = os.path.basename(event.evidence_frame_path) if event.evidence_frame_path else ""
            print(f"   [OK] PERIODIC RECORD: JSON [{json_name}] | Image [{img_name}]")

    def _send_webhook(self, event: TrafficIncidentEvent) -> None:
        try:
            headers = {"Content-Type": "application/json"}
            resp = requests.post(self.webhook_url, data=event.model_dump_json(), headers=headers, timeout=3.0)
            if resp.status_code == 200:
                print(f"[EventEmitter] Webhook sent successfully to {self.webhook_url}")
            else:
                print(f"[EventEmitter] Webhook returned status code {resp.status_code}")
        except Exception as e:
            print(f"[EventEmitter] Failed to send webhook: {e}")
