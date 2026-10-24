from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from .llm_agent import GroqLLMAgent
from .task_manager import VideoTaskManager

task_router = APIRouter(prefix="/api/v1", tags=["LLM Agent & Video Task Management"])

# Khởi tạo singleton manager và LLM agent
task_manager = VideoTaskManager()
llm_agent = GroqLLMAgent()


class ChatRequest(BaseModel):
    message: str = Field(..., example="Hãy phân tích video data_test/flood_g2_0190_3e225b42.mp4 xem có ngập không")
    session_id: Optional[str] = Field("default_session", description="Mã phiên hội thoại")


class DispatchTaskRequest(BaseModel):
    video_source: str = Field(..., example="data_test/flood_g2_0190_3e225b42.mp4")
    camera_id: str = Field("CAM_FLOOD_G2", example="CAM_FLOOD_G2")
    instruction: Optional[str] = Field("Phân tích tình trạng ngập và đếm xe", example="Phân tích tình trạng ngập và đếm xe")
    max_duration_sec: Optional[int] = Field(60, example=60)
    sample_interval: Optional[int] = Field(2, example=2)


@task_router.post("/agent/chat", summary="Chat thông minh với Groq LLM: Giao task video/stream và hỏi tiến độ")
def chat_with_agent(req: ChatRequest):
    """
    Điểm chạm tương tác ngôn ngữ tự nhiên:
    1. Giao task: "Phân tích camera CAM_FLOOD_G2", "Kiểm tra video ngập...", "Theo dõi livestream rtsp://..."
    2. Hỏi tiến độ: "Task làm tới đâu rồi?", "Tiến độ task TASK_xxx ra sao?", "Đường đó ngập chưa?"
    3. Trò chuyện và giải đáp thắc mắc.
    """
    recent_tasks = task_manager.list_tasks()
    analysis = llm_agent.analyze_user_intent(req.message, recent_tasks)
    intent = analysis.get("intent", "GENERAL_CHAT")

    if intent == "DISPATCH_TASK":
        video_src = analysis.get("video_source") or "data_test/flood_g2_0190_3e225b42.mp4"
        cam_id = analysis.get("camera_id") or "CAM_FLOOD_G2"
        action = analysis.get("action") or "both"
        instruction = analysis.get("reply_hint") or req.message

        # Khởi chạy task chạy ngầm
        task_info = task_manager.dispatch_task(
            video_source=video_src,
            camera_id=cam_id,
            action=action,
            instruction=instruction
        )

        from pathlib import Path
        v_name = Path(task_info['video_source']).name

        reply_text = (
            f"**Đã khởi chạy tác vụ phân tích:**\n\n"
            f"- **Mã tác vụ:** `{task_info['task_id']}`\n"
            f"- **Tệp nguồn:** `{v_name}`\n"
            f"- **Camera ID:** `{task_info['camera_id']}`\n"
            f"- **Trạng thái:** Đang phân tích khung hình ngầm...\n\n"
            f"*Bạn có thể theo dõi tiến độ trực tiếp trên bảng điều khiển bên phải hoặc gửi tin nhắn 'Tiến độ task' bất cứ lúc nào.*"
        )

        return {
            "intent": intent,
            "reply": reply_text,
            "task": task_info
        }

    elif intent == "CHECK_PROGRESS":
        target_id = analysis.get("target_task_id")
        target_task = None

        if target_id and target_id != "LATEST":
            target_task = task_manager.get_task(target_id)
        
        if not target_task:
            target_task = task_manager.get_latest_task()

        if not target_task:
            return {
                "intent": intent,
                "reply": "Hiện tại chưa có tác vụ phân tích video nào được giao. Bạn có thể gửi lệnh: *'Hãy phân tích video data_test/flood_g2_0190_3e225b42.mp4'* để bắt đầu nhé!",
                "task": None
            }

        # Dùng Groq LLM để trả lời tự nhiên theo đúng ngữ cảnh thực tế
        llm_reply = llm_agent.format_progress_response(target_task, req.message)
        return {
            "intent": intent,
            "reply": llm_reply,
            "task": target_task
        }

    else:
        # General chat
        messages = [
            {"role": "system", "content": "Bạn là Trợ lý AI SafeRoute thông minh, chuyên hỗ trợ giám sát giao thông đô thị và ngập lụt. Hãy trả lời thân thiện, súc tích bằng tiếng Việt."},
            {"role": "user", "content": req.message}
        ]
        reply = llm_agent.call_llm(messages)
        return {
            "intent": "GENERAL_CHAT",
            "reply": reply,
            "task": None
        }


@task_router.post("/tasks/dispatch", summary="Giao tác vụ phân tích video/stream trực tiếp (Programmatic API)")
def dispatch_task_direct(req: DispatchTaskRequest):
    task = task_manager.dispatch_task(
        video_source=req.video_source,
        camera_id=req.camera_id,
        instruction=req.instruction,
        max_duration_sec=req.max_duration_sec or 60,
        sample_interval=req.sample_interval or 2
    )
    return task


@task_router.get("/tasks/{task_id}", summary="Tra cứu trạng thái và tiến độ chi tiết của một Task")
def get_task_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy Task với ID: {task_id}")
    return task


@task_router.get("/tasks", summary="Lấy danh sách tất cả các Task gần đây")
def list_all_tasks():
    return {
        "total_tasks": len(task_manager.list_tasks()),
        "tasks": task_manager.list_tasks()
    }
