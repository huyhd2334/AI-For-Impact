import argparse
import os
from pathlib import Path
from .analyzer import VideoTrafficAnalyzer


def main():
    parser = argparse.ArgumentParser(description="Traffic Surveillance & Counter CLI")
    parser.add_argument("--source", type=str, default=None, help="Path to video file (.mp4) or image (.jpg) or RTSP stream URL")
    parser.add_argument("--cam-id", type=str, default="CAM_BINHTHANH_01", help="Camera identifier")
    parser.add_argument("--interval", type=float, default=5.0, help="Snapshot and event emission interval in seconds (default: 5.0s)")
    parser.add_argument("--preview", action="store_true", help="Display visual live preview window")
    parser.add_argument("--fps", type=float, default=10.0, help="Target processing/display FPS (default: 10.0)")
    curr_dir = Path(__file__).resolve().parent

    def resolve_model_path(name: str) -> str:
        candidates = [
            Path(name),
            curr_dir / "weights" / name,
            Path("traffic_counter/weights") / name,
            Path("weights") / name,
            Path(f"traffic_counter/weights/{name}"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return name

    default_model = resolve_model_path("vn_traffic_surveillance.pt")
    if not os.path.exists(default_model):
        for fallback in ["rtdetr-x.pt", "yolov8s.pt", "yolov8n.pt"]:
            fb_path = resolve_model_path(fallback)
            if os.path.exists(fb_path):
                default_model = fb_path
                break

    parser.add_argument("--model", type=str, default=default_model, help=f"Detection AI model weight path (default: {default_model})")
    parser.add_argument("--conf", type=float, default=0.18, help="Detection confidence threshold (default: 0.18)")
    parser.add_argument("--imgsz", type=int, default=1280, help="Inference image resolution (default: 1280 for distant vehicles)")
    parser.add_argument("--water-thresh", type=float, default=0.05, help="Area ratio threshold to trigger water presence (default: 0.05 = 5%%)")
    parser.add_argument("--jam-speed", type=float, default=15.0, help="Average speed threshold (km/h) below which traffic is congested (default: 15.0)")
    parser.add_argument("--sahi", action="store_true", help="Enable SAHI slicing for ultra-dense / 4K small vehicles")
    parser.add_argument("--heatmap", action="store_true", help="Enable traffic density accumulation heatmap")
    parser.add_argument("--tripwire-y", type=float, default=0.58, help="Virtual tripwire vertical position ratio (default: 0.58 = 58%% of frame height)")
    parser.add_argument("--mock", action="store_true", help="Generate and run on synthetic traffic video")

    args = parser.parse_args()

    model_path = resolve_model_path(args.model)

    video_source = args.source
    if video_source and not os.path.exists(video_source):
        test_path = os.path.join("data_test", video_source)
        if os.path.exists(test_path):
            video_source = test_path

    if args.mock or not video_source:
        mock_file = "mock_traffic_flood.mp4"
        if not os.path.exists(mock_file):
            print("[Main] No input video found. Generating synthetic mock video...")
            generate_mock_traffic_video(mock_file)
        video_source = mock_file

    analyzer = VideoTrafficAnalyzer(
        camera_id=args.cam_id,
        sample_interval_sec=args.interval,
        model_name=model_path,
        confidence_thresh=args.conf,
        imgsz=args.imgsz,
        water_ratio_thresh=args.water_thresh,
        jam_speed_thresh_kmh=args.jam_speed,
        use_sahi=args.sahi,
        enable_heatmap=args.heatmap,
        tripwire_y_ratio=args.tripwire_y
    )

    analyzer.process_video(
        video_source=video_source, 
        display_preview=args.preview,
        target_fps=args.fps
    )


if __name__ == "__main__":
    main()
