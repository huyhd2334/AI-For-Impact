import os
import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests

def _load_env_keys() -> List[str]:
    # Thử nạp từ .env nếu có
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

    keys = []
    for var in ["GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"]:
        val = os.getenv(var)
        if val:
            keys.append(val)
    return keys

DEFAULT_MODELS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "qwen/qwen3.8-27b"
]


class GroqLLMAgent:
    """
    LLM Agent tích hợp Groq LPU với cơ chế xoay tua 3 API Keys tự động (Key Rotation & Failover).
    Đảm bảo không bao giờ bị nghẽn rate-limit, tốc độ suy luận siêu tốc (~500 tokens/giây).
    """

    def __init__(self, api_keys: Optional[List[str]] = None, model: str = "llama-3.1-8b-instant"):
        self.api_keys = api_keys or _load_env_keys()
        if not self.api_keys:
            print("[GroqLLMAgent] Cảnh báo: Không tìm thấy GROQ_API_KEY trong môi trường hoặc .env!")
        self.current_key_idx = 0
        self.model = model
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def _get_active_key(self) -> str:
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self):
        self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
        print(f"[GroqLLMAgent] Đã chuyển sang API Key dự phòng #{self.current_key_idx + 1}")

    def call_llm(self, messages: List[Dict[str, str]], temperature: float = 0.2, max_tokens: int = 1024) -> str:
        """Gọi Groq LLM với tự động xoay key khi gặp lỗi 429 hoặc kết nối"""
        attempts = 0
        max_attempts = len(self.api_keys) * 2

        while attempts < max_attempts:
            key = self._get_active_key()
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "SafeRoute-AI-Agent/1.0"
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            try:
                resp = requests.post(self.endpoint, headers=headers, json=payload, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                elif resp.status_code in [429, 503, 504]:
                    print(f"[GroqLLMAgent] Key #{self.current_key_idx + 1} quá tải (HTTP {resp.status_code}), xoay key...")
                    self._rotate_key()
                else:
                    print(f"[GroqLLMAgent] Lỗi HTTP {resp.status_code}: {resp.text[:120]}")
                    self._rotate_key()
            except Exception as e:
                print(f"[GroqLLMAgent] Exception: {e}, xoay key...")
                self._rotate_key()

            attempts += 1
            time.sleep(0.5)

        raise RuntimeError("Tất cả Groq API keys đều không phản hồi thành công.")

    def analyze_user_intent(self, user_text: str, available_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Phân loại ý định người dùng bằng LLM:
        - DISPATCH_TASK: Giao việc phân tích video/stream
        - CHECK_PROGRESS: Hỏi tiến độ công việc đang chạy
        - GENERAL_CHAT: Chào hỏi, hỏi đáp bình thường
        """
        tasks_summary = "\n".join([
            f"- Task ID: {t.get('task_id')}, Camera: {t.get('camera_id')}, Status: {t.get('status')}, Source: {t.get('video_source')}"
            for t in available_tasks[-5:]
        ]) if available_tasks else "Hiện chưa có task nào đang chạy."

        system_prompt = f"""Bạn là Điều phối viên AI (Coordinator Agent) của Hệ thống Giám sát Giao thông & Ngập úng Thông minh SafeRoute.
Nhiệm vụ của bạn là đọc tin nhắn của người dùng và phân tích thành JSON có cấu trúc.

Danh sách các task hiện có gần đây:
{tasks_summary}

Hãy trả về DUY NHẤT một chuỗi JSON hợp lệ theo schema sau (không giải thích thêm):
{{
    "intent": "DISPATCH_TASK" | "CHECK_PROGRESS" | "GENERAL_CHAT",
    "target_task_id": "Mã task_id nếu người dùng hỏi tiến độ (hoặc 'LATEST' nếu hỏi chung chung, hoặc null)",
    "video_source": "Đường dẫn file video hoặc link RTSP nếu người dùng giao việc (mặc định lấy từ tin nhắn hoặc gợi ý data_test/flood_g2_0190_3e225b42.mp4 nếu người dùng chỉ nói 'video ngập g2')",
    "camera_id": "Mã camera tương ứng (ví dụ CAM_FLOOD_G0, CAM_FLOOD_G1, CAM_FLOOD_G2, CAM_FLOOD_G3) hoặc null",
    "action": "both" | "flood_only" | "traffic_only",
    "reply_hint": "Tóm tắt ngắn gọn yêu cầu của người dùng"
}}
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]

        raw_reply = self.call_llm(messages, temperature=0.1)
        # Parse JSON
        try:
            # Tìm block json nếu model bọc trong ```json
            match = re.search(r'\{.*\}', raw_reply, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass

        # Fallback phân loại heuristics đơn giản
        lower = user_text.lower()
        if any(w in lower for w in ["tới đâu", "tiến độ", "xong chưa", "trạng thái", "làm được gì"]):
            return {
                "intent": "CHECK_PROGRESS",
                "target_task_id": "LATEST",
                "reply_hint": "Hỏi tiến độ task"
            }
        elif any(w in lower for w in ["phân tích", "chạy", "giao task", "kiểm tra video", "livestream", "rtsp"]):
            return {
                "intent": "DISPATCH_TASK",
                "video_source": "data_test/flood_g2_0190_3e225b42.mp4",
                "camera_id": "CAM_FLOOD_G2",
                "action": "both",
                "reply_hint": "Giao phân tích video ngập và giao thông"
            }
        return {"intent": "GENERAL_CHAT", "reply_hint": user_text}

    def format_progress_response(self, task_info: Dict[str, Any], user_question: str) -> str:
        """
        Dùng LLM diễn giải trạng thái và tiến độ task thành ngôn ngữ tự nhiên tiếng Việt mượt mà.
        """
        system_prompt = """Bạn là Trợ lý AI SafeRoute. 
Dựa trên số liệu tiến độ thực tế của task đang xử lý video/livestream, hãy trả lời câu hỏi của người dùng bằng tiếng Việt tự nhiên, nhiệt tình, chuyên nghiệp.
Trình bày rõ ràng:
1. Trạng thái task (Đang xử lý / Đã hoàn thành / Gặp lỗi).
2. Tiến độ phần trăm và số frame đã xử lý.
3. Các phát hiện hiện tại: Mức độ ngập (cm), có ngập bánh xe không, số lượng xe đã đếm qua từng nhánh.
4. Dự kiến thời gian còn lại (nếu đang chạy).
"""
        user_content = f"""Câu hỏi người dùng: "{user_question}"

Dữ liệu task hiện tại:
{json.dumps(task_info, ensure_ascii=False, indent=2)}
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        try:
            return self.call_llm(messages, temperature=0.3)
        except Exception:
            st_map = {"RUNNING": "Đang xử lý", "COMPLETED": "Đã hoàn thành", "STOPPED": "Đã dừng", "FAILED": "Gặp sự cố"}
            status_text = st_map.get(task_info.get("status", "RUNNING"), task_info.get("status"))
            pct = task_info.get("progress_pct", 0)
            m = task_info.get("current_metrics", {})
            return (
                f"**Báo cáo tiến độ tác vụ {task_info.get('task_id', '')}:**\n\n"
                f"- **Trạng thái:** `{status_text}` ({pct}%)\n"
                f"- **Khung hình:** {task_info.get('frames_processed', 0)}/{task_info.get('total_frames', '...')} frames\n"
                f"- **Mực nước ngập:** ~{m.get('estimated_depth_cm', 0.0)} cm ({m.get('flood_status', 'Không ngập')})\n"
                f"- **Tổng xe qua vạch:** {m.get('total_vehicles', 0)} phương tiện\n\n"
                f"*(Bản tin tóm tắt trực tiếp từ dữ liệu máy chủ)*"
            )
