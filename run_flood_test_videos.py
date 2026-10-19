import os
import sys
import glob
import json
import time
from pathlib import Path

# Fix Windows console UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from camera_ai.pipeline import UnifiedCameraAnalyzer


def main():
    print("=" * 70)
    print("[*] BAT DAU CHAY KIEM THU 4 VIDEO FLOOD G0 -> G3 VA XUAT VIDEO KET QUA")
    print("=" * 70)

    # Đảm bảo thư mục lưu output tồn tại
    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Tìm 4 video flood_g... trong data_test/
    video_patterns = [
        "data_test/flood_g0*.mp4",
        "data_test/flood_g1*.mp4",
        "data_test/flood_g2*.mp4",
        "data_test/flood_g3*.mp4"
    ]

    target_videos = []
    for pat in video_patterns:
        matched = sorted(glob.glob(pat))
        if matched:
            target_videos.append(matched[0])

    if not target_videos:
        print("[!] Không tìm thấy video flood_g... nào trong data_test/!")
        sys.exit(1)

    print(f"[*] Tìm thấy {len(target_videos)} video kiểm thử:")
    for v in target_videos:
        print(f"  - {v}")
    print()

    # Khởi tạo pipeline AI
    print("[*] Đang khởi tạo UnifiedCameraAnalyzer...")
    analyzer = UnifiedCameraAnalyzer()

    summary_results = []
    total_start = time.time()

    for idx, vid_file in enumerate(target_videos):
        vid_path = Path(vid_file)
        cam_id = f"CAM_FLOOD_G{idx}"
        out_vid_name = f"result_flood_g{idx}.mp4"
        out_vid_path = out_dir / out_vid_name

        print("-" * 70)
        print(f"[{idx+1}/{len(target_videos)}] ĐANG XỬ LÝ: {vid_path.name}")
        print(f"    Camera ID   : {cam_id}")
        print(f"    Output Video: {out_vid_path}")
        print("-" * 70)

        vid_start = time.time()
        events = analyzer.process_video(
            camera_id=cam_id,
            video_source=str(vid_path),
            target_fps=8.0,            # 8.0 FPS cho kết quả mượt mà và chạy nhanh
            sample_interval_sec=2.0,   # 2 giây xuất 1 sự kiện quan sát
            use_llm_on_alert=False,    # Dùng local CV + Depth + YOLO siêu tốc (bật Gemini nếu có API key)
            output_video_path=str(out_vid_path)
        )
        elapsed = time.time() - vid_start

        latest_event = events[-1] if events else None

        result_item = {
            "index": idx,
            "input_video": str(vid_path),
            "output_video": str(out_vid_path),
            "camera_id": cam_id,
            "processing_time_sec": round(elapsed, 2),
            "total_events_emitted": len(events),
            "latest_flood_status": latest_event.flood_status_vn.value if latest_event else "UNKNOWN",
            "latest_flood_raw": latest_event.flood_status.value if latest_event else "unknown",
            "latest_severity": latest_event.severity.value if latest_event else "none",
            "water_depth_cm": latest_event.water_depth_cm if latest_event else None,
            "safe_for_bikes": latest_event.safe_for_bikes if latest_event else True,
            "latest_traffic_status": latest_event.traffic_status.value if latest_event else "UNKNOWN",
            "total_vehicles": latest_event.total_vehicles if latest_event else 0,
            "reason": latest_event.reason if latest_event else ""
        }
        summary_results.append(result_item)

        print(f"\n[+] Hoan thanh video {vid_path.name} trong {elapsed:.1f}s!")
        if latest_event:
            print(f"    -> Trang thai ngap  : {latest_event.flood_status_vn.value.upper()} (Do sau: {latest_event.water_depth_cm}cm)")
            print(f"    -> Xe may di duoc   : {'CO' if latest_event.safe_for_bikes else 'NGUY HIEM'}")
            print(f"    -> Tinh trang tac   : {latest_event.traffic_status.value} ({latest_event.total_vehicles} xe)")
            print(f"    -> Video ket qua    : {out_vid_path}")
        print()

    # Xuất file tổng hợp JSON
    summary_json_path = out_dir / "flood_g_test_summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_results, f, ensure_ascii=False, indent=2)

    total_time = time.time() - total_start
    print("=" * 70)
    print(f"[+] DA XU LY XONG TOAN BO 4 VIDEO TRONG {total_time:.1f} GIAY!")
    print(f"[*] Thu muc chua 4 video ket qua: {out_dir.resolve()}")
    print(f"[*] Bao cao tong hop JSON: {summary_json_path.resolve()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
