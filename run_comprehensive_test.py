import os
import sys
import glob
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, List

# Windows console UTF-8 support
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import cv2
import numpy as np

from camera_ai.pipeline import UnifiedCameraAnalyzer


def make_grid_collage(image_paths: List[Path], output_path: Path, titles: List[str]):
    """
    Tạo ảnh ghép 2x2 hoặc 1xN tổng hợp các snapshot HUD để xem nhanh toàn bộ kết quả.
    """
    images = []
    for p in image_paths:
        if p.exists():
            img = cv2.imread(str(p))
            if img is not None:
                images.append(img)
    if not images:
        return

    target_w = 640
    target_h = 360
    resized = [cv2.resize(im, (target_w, target_h), interpolation=cv2.INTER_AREA) for im in images]

    # Pad to 4 if less
    while len(resized) < 4:
        blank = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        resized.append(blank)

    top_row = np.hstack([resized[0], resized[1]])
    bot_row = np.hstack([resized[2], resized[3]])
    grid = np.vstack([top_row, bot_row])

    cv2.imwrite(str(output_path), grid)
    print(f"[+] Da tao anh collage tong hop 4 camera tai: {output_path}")


def run_test_suite(
    target_mode: str = "floods",
    target_fps: float = 8.0,
    use_llm: bool = False
):
    print("=" * 80)
    print("   SAFEROUTE AI - BO TEST KIEM THU TOAN DIEN HE THONG (COMPREHENSIVE TEST SUITE)")
    print("   Models: SegFormer (Road) | LinkNet-B4 + Spectral (Water) | YOLOv8 (Traffic)")
    print("           4-Tier Depth Estimator (Submersion + Curb + Geometric)")
    print("=" * 80)

    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n[*] Khoi tao UnifiedCameraAnalyzer...")
    analyzer = UnifiedCameraAnalyzer()

    all_results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "videos": [],
        "images": []
    }

    # -------------------------------------------------------------
    # PHAN 1: KIEM THU 4 VIDEO NGAP G0 -> G3
    # -------------------------------------------------------------
    if target_mode in ["all", "floods"]:
        print("\n" + "=" * 80)
        print(">> PHAN 1: CHAY KIEM THU 4 VIDEO CAMERA NGAP (G0 -> G3)")
        print("=" * 80)

        flood_videos = [
            ("CAM_FLOOD_G0", "data_test/flood_g0_0133_a41b3e13.mp4", "result_flood_g0.mp4"),
            ("CAM_FLOOD_G1", "data_test/flood_g1_0282_ac68cb9b.mp4", "result_flood_g1.mp4"),
            ("CAM_FLOOD_G2", "data_test/flood_g2_0190_3e225b42.mp4", "result_flood_g2.mp4"),
            ("CAM_FLOOD_G3", "data_test/flood_g3_0040_e5b38c34.mp4", "result_flood_g3.mp4"),
        ]

        snapshot_paths = []
        for idx, (cam_id, vid_path_str, out_name) in enumerate(flood_videos):
            vid_path = Path(vid_path_str)
            if not vid_path.exists():
                print(f"[!] Khong tim thay {vid_path_str}, bo qua.")
                continue

            out_vid_path = out_dir / out_name
            print(f"\n[{idx+1}/4] Dang xu ly video: {vid_path.name} (Camera: {cam_id})...")
            t0 = time.time()
            events = analyzer.process_video(
                camera_id=cam_id,
                video_source=str(vid_path),
                target_fps=target_fps,
                sample_interval_sec=2.0,
                use_llm_on_alert=use_llm,
                output_video_path=str(out_vid_path)
            )
            elapsed = time.time() - t0
            latest = events[-1] if events else None

            # Capture snapshot from output video at frame 40
            cap = cv2.VideoCapture(str(out_vid_path))
            total_f = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(total_f - 1, max(0, 30)))
            ret, snap_frame = cap.read()
            cap.release()

            snap_path = out_dir / f"snapshot_{cam_id}.jpg"
            if ret and snap_frame is not None:
                cv2.imwrite(str(snap_path), snap_frame)
                snapshot_paths.append(snap_path)

            v_res = {
                "category": "flood_video",
                "camera_id": cam_id,
                "input_video": str(vid_path),
                "output_video": str(out_vid_path),
                "snapshot_image": str(snap_path) if snap_path.exists() else None,
                "elapsed_sec": round(elapsed, 2),
                "events_count": len(events),
                "flood_status": latest.flood_status_vn.value if latest else "UNKNOWN",
                "severity": latest.severity.value if latest else "none",
                "water_depth_cm": latest.water_depth_cm if latest else None,
                "depth_method": latest.depth_method if latest else None,
                "safe_for_bikes": latest.safe_for_bikes if latest else True,
                "road_water_ratio": latest.road_water_ratio if latest else 0.0,
                "traffic_status": latest.traffic_status.value if latest else "UNKNOWN",
                "total_vehicles": latest.total_vehicles if latest else 0,
                "reason": latest.reason if latest else ""
            }
            all_results["videos"].append(v_res)

            status_color = "🔴" if latest and latest.flood_status.value == "flooded" else "🟢"
            print(f"  {status_color} Ket qua: {latest.flood_status_vn.value.upper()} | Do sau: {latest.water_depth_cm}cm | Xe may: {'CO' if latest.safe_for_bikes else 'NGUY HIEM'}")
            print(f"  ⏱ Thoi gian: {elapsed:.1f}s ({len(events)} events) | Output: {out_vid_path}")

        # Tao collage 2x2 cho 4 video ngap
        if len(snapshot_paths) >= 4:
            make_grid_collage(
                snapshot_paths[:4],
                out_dir / "flood_g_benchmark_collage.jpg",
                ["G0", "G1", "G2", "G3"]
            )

    # -------------------------------------------------------------
    # PHAN 2: KIEM THU TAP ANH BENCHMARK CHUAN (IMAGES)
    # -------------------------------------------------------------
    if target_mode in ["all", "images"]:
        print("\n" + "=" * 80)
        print(">> PHAN 2: CHAY KIEM THU TAP ANH BENCHMARK (NGAP SAU, NGAP NHE, DUONG KHO, KET XE)")
        print("=" * 80)

        test_images = [
            ("high_1.png", "CAM_BENCH_HIGH1", "Ngap sau nghiem trong"),
            ("high_2.png", "CAM_BENCH_HIGH2", "Ngap sau dien rong"),
            ("low_1.png", "CAM_BENCH_LOW1", "Ngap nhe xam xap"),
            ("no_1.png", "CAM_BENCH_DRY1", "Duong kho rao"),
            ("no_2.png", "CAM_BENCH_DRY2", "Duong kho rao khong ngap"),
            ("traffic_jams.jpg", "CAM_BENCH_JAM", "Ket xe un tac do thi")
        ]

        for img_name, cam_id, desc in test_images:
            img_path = Path("images") / img_name
            if not img_path.exists():
                continue

            frame = cv2.imread(str(img_path))
            if frame is None:
                continue

            t0 = time.time()
            event, annotated = analyzer.analyze_frame(
                camera_id=cam_id,
                frame=frame,
                use_llm=use_llm,
                save_evidence=True,
                source_hint=img_name
            )
            elapsed = time.time() - t0

            out_img_path = out_dir / f"result_{img_name}"
            cv2.imwrite(str(out_img_path), annotated)

            img_res = {
                "category": "benchmark_image",
                "test_case": desc,
                "image_file": str(img_path),
                "output_image": str(out_img_path),
                "camera_id": cam_id,
                "elapsed_sec": round(elapsed, 3),
                "flood_status": event.flood_status_vn.value,
                "severity": event.severity.value,
                "water_depth_cm": event.water_depth_cm,
                "depth_method": event.depth_method,
                "safe_for_bikes": event.safe_for_bikes,
                "road_water_ratio": event.road_water_ratio,
                "traffic_status": event.traffic_status.value,
                "total_vehicles": event.total_vehicles,
                "is_jammed": event.is_jammed,
                "reason": event.reason
            }
            all_results["images"].append(img_res)

            icon = "🔴" if event.flood_status.value == "flooded" else ("🟠" if event.is_jammed else "🟢")
            print(f"  {icon} [{img_name:16s}] -> Ngap: {event.flood_status_vn.value:15s} | Do sau: {str(event.water_depth_cm):>5s}cm | Giao thong: {event.traffic_status.value:12s} ({event.total_vehicles:2d} xe)")

    # -------------------------------------------------------------
    # TONG KET & XUAT FILE BAO CAO
    # -------------------------------------------------------------
    summary_path = out_dir / "comprehensive_test_report.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 80)
    print("   TONG KET PHIEN KIEM THU")
    print("=" * 80)
    print(f"• Tong so video da kiem thu : {len(all_results['videos'])}")
    print(f"• Tong so anh da kiem thu   : {len(all_results['images'])}")
    print(f"• File JSON ket qua chi tiet: {summary_path.resolve()}")
    print(f"• Thu muc luu video & anh   : {out_dir.resolve()}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SafeRoute AI Comprehensive Test Suite")
    parser.add_argument("--mode", type=str, default="all", choices=["all", "floods", "images"], help="Test mode")
    parser.add_argument("--fps", type=float, default=8.0, help="Target FPS for video testing")
    parser.add_argument("--use-llm", action="store_true", help="Enable Gemini Flash API for cross verification")

    args = parser.parse_args()
    run_test_suite(target_mode=args.mode, target_fps=args.fps, use_llm=args.use_llm)
