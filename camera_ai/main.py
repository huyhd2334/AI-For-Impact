import os
import sys
import json
import argparse
from pathlib import Path
import cv2
import uvicorn

from camera_ai.pipeline import UnifiedCameraAnalyzer


def main():
    parser = argparse.ArgumentParser(description="SafeRoute Camera AI Unified Service (FastAPI + Pipeline CLI)")
    parser.add_argument("--serve", action="store_true", help="Start the FastAPI REST server")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="API server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="API server port (default: 8000)")
    parser.add_argument("--camera-id", type=str, default="CAM_BINHTHANH_01", help="Camera ID to process")
    parser.add_argument("--image", type=str, default=None, help="Path to single test frame")
    parser.add_argument("--video", type=str, default=None, help="Path to test video")
    parser.add_argument("--fps", type=float, default=5.0, help="Target FPS for video processing")
    parser.add_argument("--out-video", type=str, default=None, help="Path to save annotated output video (e.g. outputs/result.mp4)")
    parser.add_argument("--calibrate", action="store_true", help="Only run road mask calibration and exit")
    parser.add_argument("--force-calibrate", action="store_true", help="Force recalculation of cached road mask")
    parser.add_argument("--no-llm", action="store_true", help="Disable Gemini Flash reasoning (use local CV only)")

    args = parser.parse_args()

    # Mode 1: Start FastAPI REST Server
    if args.serve:
        print(f"[Main] Starting SafeRoute Camera AI Service on {args.host}:{args.port}...")
        uvicorn.run("camera_ai.api:app", host=args.host, port=args.port, reload=False)
        return

    # Mode 2: Run CLI Pipeline
    analyzer = UnifiedCameraAnalyzer()

    # Image Calibration or Analysis
    if args.image:
        img_path = Path(args.image)
        if not img_path.exists():
            print(f"[Error] Image file not found: {args.image}")
            sys.exit(1)

        frame = cv2.imread(str(img_path))
        if frame is None:
            print(f"[Error] Failed to decode image: {args.image}")
            sys.exit(1)

        if args.calibrate:
            print(f"[*] Calibrating road mask for {args.camera_id} from {args.image}...")
            _, _, ratio, polys = analyzer.calibrate_camera(args.camera_id, frame, force=args.force_calibrate)
            print(f"[+] Calibration complete! Road ratio: {ratio*100:.2f}%, Polygons: {len(polys)}")
            return

        print(f"[*] Running Unified Camera Pipeline on {args.image} for camera {args.camera_id}...")
        event, _ = analyzer.analyze_frame(
            camera_id=args.camera_id,
            frame=frame,
            use_llm=not args.no_llm,
            save_evidence=True,
            source_hint=img_path.name
        )

        print("\n" + "=" * 65)
        print("[CAMERA OBSERVATION EVENT v1 - RESULT]")
        print("=" * 65)
        print(json.dumps(event.model_dump(), indent=2, ensure_ascii=False))
        return

    # Video Analysis
    if args.video:
        vid_path = Path(args.video)
        if not vid_path.exists():
            print(f"[Error] Video file not found: {args.video}")
            sys.exit(1)

        print(f"[*] Processing video {args.video} at {args.fps} FPS for camera {args.camera_id}...")
        out_vid = args.out_video
        if not out_vid:
            out_dir = Path("outputs")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_vid = str(out_dir / f"result_{vid_path.stem}.mp4")

        events = analyzer.process_video(
            camera_id=args.camera_id,
            video_source=str(vid_path),
            target_fps=args.fps,
            sample_interval_sec=2.0,
            use_llm_on_alert=not args.no_llm,
            output_video_path=out_vid
        )
        print(f"\n[+] Video processing finished. Total observation events emitted: {len(events)}")
        if events:
            print(f"Latest state: Flood={events[-1].flood_status_vn.value} | Traffic={events[-1].traffic_status.value}")
        print(f"[+] Output video is ready at: {out_vid}")
        return

    # If no argument, print help
    parser.print_help()


if __name__ == "__main__":
    main()
