import os
import sys
import time
import uuid
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
import cv2
import numpy as np

from .pipeline import UnifiedCameraAnalyzer

class VideoTaskManager:
    """
    Quản lý các tác vụ phân tích Video / Livestream chạy ngầm (Asynchronous Background Worker).
    Cho phép LLM Agent giao task và người dùng theo dõi tiến độ thời gian thực.
    """

    def __init__(self, output_dir: Optional[str] = None):
        curr_dir = Path(__file__).resolve().parent
        self.output_dir = Path(output_dir) if output_dir else curr_dir.parent / "outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._analyzer: Optional[UnifiedCameraAnalyzer] = None

    def _get_analyzer(self) -> UnifiedCameraAnalyzer:
        if self._analyzer is None:
            self._analyzer = UnifiedCameraAnalyzer()
        return self._analyzer

    def dispatch_task(
        self,
        video_source: str,
        camera_id: str = "CAM_FLOOD_G2",
        max_duration_sec: int = 60,
        sample_interval: int = 2,
        action: str = "both",
        instruction: str = "Phân tích tình trạng ngập lụt và đếm xe"
    ) -> Dict[str, Any]:
        """
        Khởi tạo một task phân tích video/stream mới chạy ngầm.
        """
        task_id = f"TASK_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4].upper()}"

        # Resolve video path nếu là file cục bộ
        src_path = Path(video_source)
        if not src_path.is_absolute() and not video_source.startswith(("rtsp://", "http://", "https://")):
            base_dir = Path(__file__).resolve().parent.parent
            candidate = base_dir / video_source
            if candidate.exists():
                video_source = str(candidate)

        task_record = {
            "task_id": task_id,
            "status": "PENDING",  # PENDING -> RUNNING -> COMPLETED | FAILED | STOPPED
            "instruction": instruction,
            "video_source": str(video_source),
            "camera_id": camera_id,
            "action": action,
            "progress_pct": 0.0,
            "frames_processed": 0,
            "total_frames": 0,
            "fps_processing": 0.0,
            "stop_requested": False,
            "start_time": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "duration_elapsed_sec": 0.0,
            "current_metrics": {
                "flood_status": "CHƯA XÁC ĐỊNH",
                "estimated_depth_cm": 0.0,
                "water_ratio": 0.0,
                "total_vehicles": 0,
                "branch_counts": {},
                "is_jammed": False
            },
            "output_video_path": str(self.output_dir / f"result_{task_id}.mp4"),
            "error": None
        }

        with self._lock:
            self._tasks[task_id] = task_record

        # Khởi chạy thread xử lý video chạy ngầm
        thread = threading.Thread(
            target=self._process_video_worker,
            args=(task_id, video_source, camera_id, max_duration_sec, sample_interval),
            daemon=True
        )
        thread.start()

        return task_record

    def _process_video_worker(
        self,
        task_id: str,
        video_source: str,
        camera_id: str,
        max_duration_sec: int,
        sample_interval: int
    ):
        """Worker thực thi xử lý video theo từng khung hình"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "RUNNING"

        analyzer = self._get_analyzer()
        cap = cv2.VideoCapture(video_source)

        if not cap.isOpened():
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "FAILED"
                    self._tasks[task_id]["error"] = f"Không thể mở nguồn video/stream: {video_source}"
            return

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            total_frames = 300  # Fallback ước lượng cho livestream (ví dụ 10 giây @ 30fps)

        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["total_frames"] = total_frames

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720

        out_path = str(self.output_dir / f"result_{task_id}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(out_path, fourcc, fps / sample_interval, (width, height))

        frame_idx = 0
        processed_count = 0
        t_start = time.time()

        last_flood_status = "CHƯA XÁC ĐỊNH"
        last_depth_cm = 0.0
        last_water_ratio = 0.0
        total_veh = 0
        tripwire_counts = {}
        obs_event = None

        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if frame_idx % sample_interval != 0:
                    continue

                processed_count += 1
                elapsed = time.time() - t_start

                # Kiểm tra cờ yêu cầu dừng từ người dùng
                with self._lock:
                    if self._tasks.get(task_id, {}).get("stop_requested"):
                        print(f"[TaskManager] Tác vụ {task_id} đã nhận lệnh dừng từ người dùng.")
                        break

                # Dừng nếu vượt quá thời lượng cho phép đối với livestream
                if elapsed > max_duration_sec:
                    break

                # Thực hiện phân tích khung hình bằng Unified Analyzer
                # (Chạy offline mode để tốc độ cao nhất)
                obs_event, annotated_frame = analyzer.analyze_frame(
                    camera_id=camera_id,
                    frame=frame,
                    timestamp=frame_idx / fps,
                    use_llm=False,
                    save_evidence=False
                )

                if obs_event:
                    last_flood_status = getattr(obs_event, 'flood_status_vn', getattr(obs_event, 'flood_status', 'CHƯA XÁC ĐỊNH'))
                    if hasattr(last_flood_status, 'value'):
                        last_flood_status = last_flood_status.value
                    last_depth_cm = getattr(obs_event, 'water_depth_cm', 0.0) or 0.0
                    last_water_ratio = getattr(obs_event, 'road_water_ratio', 0.0) or 0.0

                # Lấy số liệu đếm xe theo nhánh tripwire
                tripwire_counts = {}
                v_detector = getattr(analyzer, 'vehicle_detector', None)
                if v_detector:
                    tripwire_counts = dict(getattr(v_detector, 'tripwire_counts', {}))
                    if tripwire_counts:
                        total_veh = sum(tripwire_counts.values())
                    else:
                        total_veh = len(getattr(v_detector, 'tracked_vehicles', {}))
                else:
                    total_veh = 0

                writer.write(annotated_frame)

                # Cập nhật tiến độ vào task record (tần suất cập nhật mỗi 5 frame)
                if processed_count % 5 == 0 or frame_idx >= total_frames:
                    pct = min(99.0, round((frame_idx / max(1, total_frames)) * 100.0, 1))
                    cur_fps = round(processed_count / max(0.001, elapsed), 1)

                    with self._lock:
                        if task_id in self._tasks:
                            self._tasks[task_id]["progress_pct"] = pct
                            self._tasks[task_id]["frames_processed"] = frame_idx
                            self._tasks[task_id]["duration_elapsed_sec"] = round(elapsed, 1)
                            self._tasks[task_id]["fps_processing"] = cur_fps
                            self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                            self._tasks[task_id]["current_metrics"] = {
                                "flood_status": last_flood_status,
                                "estimated_depth_cm": round(last_depth_cm, 1),
                                "water_ratio": round(last_water_ratio, 3),
                                "total_vehicles": total_veh,
                                "branch_counts": tripwire_counts,
                                "is_jammed": getattr(obs_event, 'congestion_level', 'LOW') in ['HIGH', 'JAMMED'] if obs_event else False
                            }

        except Exception as e:
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "FAILED"
                    self._tasks[task_id]["error"] = str(e)
        finally:
            cap.release()
            writer.release()
            elapsed_final = time.time() - t_start

            # Tự động convert sang H.264 web format để trình duyệt phát mượt mà
            self._convert_to_web_h264(out_path)

            with self._lock:
                if task_id in self._tasks:
                    curr_st = self._tasks[task_id]["status"]
                    if curr_st not in ["FAILED", "STOPPED"]:
                        self._tasks[task_id]["status"] = "COMPLETED"
                        self._tasks[task_id]["progress_pct"] = 100.0
                    self._tasks[task_id]["duration_elapsed_sec"] = round(elapsed_final, 1)
                    self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                    self._tasks[task_id]["current_metrics"] = {
                        "flood_status": last_flood_status,
                        "estimated_depth_cm": round(last_depth_cm, 1),
                        "water_ratio": round(last_water_ratio, 3),
                        "total_vehicles": total_veh,
                        "branch_counts": tripwire_counts,
                        "is_jammed": getattr(obs_event, 'congestion_level', 'LOW') in ['HIGH', 'JAMMED'] if obs_event else False
                    }

    def stop_task(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        """Dừng tác vụ đang chạy theo task_id hoặc dừng tác vụ đang chạy gần nhất"""
        with self._lock:
            target = None
            if task_id and task_id in self._tasks:
                target = self._tasks[task_id]
            else:
                for t in reversed(list(self._tasks.values())):
                    if t.get("status") in ["RUNNING", "PENDING"]:
                        target = t
                        break

            if not target:
                return {"success": False, "message": "Hiện không có tác vụ nào đang chạy để dừng."}

            target["stop_requested"] = True
            target["status"] = "STOPPED"
            target["updated_at"] = datetime.now().isoformat()
            return {
                "success": True,
                "task_id": target["task_id"],
                "message": f"Đã dừng tác vụ {target['task_id']} thành công."
            }

    @staticmethod
    def _convert_to_web_h264(video_path: str):
        """Chuyển đổi video sang H.264 (yuv420p) để trình duyệt web phát được qua HTML5 <video>"""
        try:
            import subprocess
            try:
                import imageio_ffmpeg
                ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                ffmpeg_exe = "ffmpeg"
            
            src = Path(video_path)
            if not src.exists() or src.stat().st_size == 0:
                return

            temp_path = src.parent / f"web_{src.name}"
            cmd = [
                ffmpeg_exe, "-y", "-i", str(src),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "ultrafast", "-movflags", "+faststart",
                str(temp_path)
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0 and temp_path.exists():
                temp_path.replace(src)
        except Exception as e:
            print(f"[TaskManager] Cảnh báo convert H.264 web video: {e}")

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_latest_task(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._tasks:
                return None
            return list(self._tasks.values())[-1]

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._tasks.values())
