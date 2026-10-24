import time
import json
from camera_ai.task_router import task_manager, llm_agent
from camera_ai.llm_agent import GroqLLMAgent

def test_llm_agent_dispatch_and_progress():
    print("=== [TEST 1] LLM Phân tích ý định & Giao Task ===")
    user_msg_1 = "Hãy phân tích video data_test/flood_g2_0190_3e225b42.mp4 xem đoạn đường này có bị ngập không"
    
    # 1. LLM nhận diện ý định
    analysis = llm_agent.analyze_user_intent(user_msg_1, task_manager.list_tasks())
    print("LLM Analysis Result:", json.dumps(analysis, ensure_ascii=False, indent=2))
    assert analysis["intent"] == "DISPATCH_TASK"

    # 2. Khởi chạy task ngầm
    task = task_manager.dispatch_task(
        video_source="data_test/flood_g2_0190_3e225b42.mp4",
        camera_id="CAM_FLOOD_G2",
        instruction=user_msg_1,
        sample_interval=3
    )
    task_id = task["task_id"]
    print(f"Task đã khởi chạy thành công: {task_id}")

    # 3. Đợi vài giây để worker xử lý một số frame
    print("Đang đợi worker xử lý video (3 giây)...")
    time.sleep(3.5)

    # 4. Người dùng hỏi tiến độ
    print("\n=== [TEST 2] Người dùng hỏi: 'Task làm tới đâu rồi?' ===")
    user_msg_2 = "Task làm tới đâu rồi bot, có phát hiện ngập chưa?"
    
    analysis_2 = llm_agent.analyze_user_intent(user_msg_2, task_manager.list_tasks())
    print("LLM Analysis 2:", json.dumps(analysis_2, ensure_ascii=False, indent=2))
    assert analysis_2["intent"] == "CHECK_PROGRESS"

    current_task = task_manager.get_task(task_id)
    print("Trạng thái thực tế từ Task Manager:", {
        "status": current_task["status"],
        "progress_pct": current_task["progress_pct"],
        "frames_processed": current_task["frames_processed"],
        "current_metrics": current_task["current_metrics"]
    })

    # 5. Dùng Groq LLM sinh câu trả lời tự nhiên
    llm_reply = llm_agent.format_progress_response(current_task, user_msg_2)
    print("\n--- [CÂU TRẢ LỜI CỦA GROQ LLM] ---")
    print(llm_reply.encode('ascii', 'ignore').decode())
    print("----------------------------------\n")

if __name__ == "__main__":
    test_llm_agent_dispatch_and_progress()
