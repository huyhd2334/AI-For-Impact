import argparse
import os
from .analyzer import VideoTrafficAnalyzer
from .generate_mock_video import generate_mock_traffic_video


def main():
    parser = argparse.ArgumentParser(description="Traffic & Flood Camera Analyzer CLI")
    parser.add_argument("--source", type=str, default=None, help="Đường dẫn file video mp4 hoặc RTSP stream URL")
    parser.add_argument("--cam-id", type=str, default="CAM_BINHTHANH_01", help="Mã định danh camera")
    parser.add_argument("--interval", type=float, default=5.0, help="Chu kỳ lưu dữ liệu JSON và ảnh snapshot (giây, mặc định 5.0s)")
    parser.add_argument("--preview", action="store_true", help="Hiển thị cửa sổ xem trực tiếp có vẽ Bounding Box")
    parser.add_argument("--fps", type=float, default=10.0, help="Tốc độ khung hình xử lý/hiển thị (chạy fps thấp: 5-10 fps để quan sát và không lag)")
    default_model = "vn_traffic_surveillance.pt" if os.path.exists("vn_traffic_surveillance.pt") else ("rtdetr-x.pt" if os.path.exists("rtdetr-x.pt") else "yolov8s.pt")
    parser.add_argument("--model", type=str, default=default_model, help=f"Mô hình AI nhận diện (mặc định: {default_model})")
    parser.add_argument("--conf", type=float, default=0.18, help="Ngưỡng tin cậy (Confidence threshold, mặc định 0.18)")
    parser.add_argument("--imgsz", type=int, default=1280, help="Kích thước ảnh inference (1280 giúp bắt xe nhỏ ở xa góc CCTV)")
    parser.add_argument("--water-thresh", type=float, default=0.05, help="Ngưỡng diện tích nước để kích hoạt cảnh báo ngập (mặc định 0.05 = 5%%)")
    parser.add_argument("--jam-speed", type=float, default=15.0, help="Vận tốc trung bình dưới ngưỡng này (km/h) mới báo ùn tắc (mặc định 15.0)")
    parser.add_argument("--sahi", action="store_true", help="Kích hoạt chế độ SAHI Slicing (chia nhỏ ảnh 4K để bắt trọn xe nhỏ ở xa)")
    parser.add_argument("--heatmap", action="store_true", help="VIP 3: Bật bản đồ nhiệt mật độ giao thông (Traffic Density Heatmap)")
    parser.add_argument("--tripwire-y", type=float, default=0.58, help="VIP 1: Tỉ lệ vị trí vạch ảo đếm xe (mặc định 0.58 = 58%% chiều cao khung hình)")
    parser.add_argument("--mock", action="store_true", help="Tự động tạo video giả lập để chạy thử")

    args = parser.parse_args()

    video_source = args.source
    if video_source and not os.path.exists(video_source):
        test_path = os.path.join("data_test", video_source)
        if os.path.exists(test_path):
            video_source = test_path

    if args.mock or not video_source:
        mock_file = "mock_traffic_flood.mp4"
        if not os.path.exists(mock_file):
            print("[Main] No input video found. Generating mock video...")
            generate_mock_traffic_video(mock_file)
        video_source = mock_file

    analyzer = VideoTrafficAnalyzer(
        camera_id=args.cam_id,
        sample_interval_sec=args.interval,
        model_name=args.model,
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
