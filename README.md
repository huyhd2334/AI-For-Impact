# AI Road Intelligence & Traffic Surveillance

Hệ thống thị giác máy tính (Computer Vision) toàn diện phục vụ quản lý giao thông và giám sát an toàn đô thị Việt Nam, tối ưu hóa cho camera CCTV độ phân giải cao (lên đến 4K).

Dự án được cấu trúc thành 2 module độc lập, chuyên sâu:

```
AI_solution/
├── road_segmentation/         # Module 1: Phân đoạn mặt đường & vỉa hè (Road Segmentation)
│   ├── main.py                # Điểm khởi chạy pipeline phân đoạn
│   ├── model.py               # Mask2Former (SOTA) & TwinLiteNet (Realtime)
│   ├── segmenter.py           # Auto-calibration & Corridor Fusion
│   ├── weights/               # Thư mục chứa weights (best.pth)
│   │   └── README.md
│   ├── outputs/               # Kết quả xuất tự động: ảnh segmentation + file .json
│   └── requirements.txt
│
├── traffic_counter/           # Module 2: Nhận diện, đếm xe & đánh giá ùn tắc (Traffic Counter)
│   ├── main.py                # Điểm khởi chạy phân tích camera giao thông
│   ├── analyzer.py            # Quản lý luồng video, tính toán vận tốc & kết luận kẹt xe
│   ├── vehicle_counter.py     # YOLOv8 Tracking (ByteTrack), vạch ảo Tripwire & Density Heatmap
│   ├── water_detector.py      # Phát hiện mặt đường có nước (HSV phản quang + Laplacian)
│   ├── schemas.py             # Schema chuẩn hóa dữ liệu Pydantic
│   ├── weights/               # Thư mục chứa weights (vn_traffic_surveillance.pt)
│   │   └── README.md
│   ├── outputs/               # Kết quả xuất tự động: ảnh snapshot + file .json
│   └── requirements.txt
│
└── .gitignore                 # Bỏ qua weights lớn (*.pt, *.pth), video test (*.mp4), cache
```

---

## Cài đặt Weights Pretrained

Tải các file weights từ mục [GitHub Releases](https://github.com/huyhd2334/AI-For-Impact/releases) và đặt vào đúng vị trí:

1. **`traffic_counter/weights/vn_traffic_surveillance.pt`** (44.8 MB, YOLOv8 Medium huấn luyện riêng cho giao thông VN).
2. **`road_segmentation/weights/best.pth`** (1.8 MB, TwinLiteNet cho chế độ realtime).
3. *(Riêng Mask2Former SOTA sẽ tự động tải từ Hugging Face trong lần chạy đầu tiên, không cần nạp thủ công).*

---

## 4 Lệnh Chạy Mạnh Nhất (Strongest Recommended Commands)

### 1. Phân đoạn đường trên ẢNH (Dùng Meta Mask2Former SOTA 68.7M Params):
```bash
python -m road_segmentation.main --source data_test/sample_day.jpg --model mask2former --preview
```
> Trích xuất toàn bộ diện tích lòng đường, vỉa hè với độ chính xác cao nhất bằng mô hình Swin-Transformer, tự động loại bỏ bóng râm và lọc nhiễu.

### 2. Đếm xe & kiểm tra nước trên ẢNH (Độ phân giải cao 1280px):
```bash
python -m traffic_counter.main --source data_test/sample_day.jpg --model vn_traffic_surveillance.pt --imgsz 1280
```
> Nhận diện chi tiết từng loại xe (xe máy, ô tô, xe buýt, xe tải, xe đạp) kể cả các xe nhỏ li ti ở góc xa camera CCTV.

### 3. Phân đoạn đường trên VIDEO (Chế độ Hybrid SOTA Real-Time):
```bash
python -m road_segmentation.main --source data_test/day_1.mp4 --model mask2former --preview
```
> Tự động hiệu chuẩn hành lang mặt đường ở frame đầu tiên, sau đó kích hoạt tăng tốc GPU PyTorch CUDA chạy mượt mà 60–100+ FPS, xem trực tiếp trên màn hình (bấm `Q` để dừng).

### 4. Đếm xe, đo lưu lượng vạch ảo & Bản đồ nhiệt (Heatmap) trên VIDEO:
```bash
python -m traffic_counter.main --source data_test/day_1.mp4 --model vn_traffic_surveillance.pt --imgsz 1280 --preview --heatmap
```
> - Xem trực tiếp video `--preview` với bảng HUD vận tốc thời gian thực.
> - Bật bản đồ nhiệt `--heatmap` thể hiện mật độ tích tụ ùn tắc (bấm phím `H` để bật/tắt).
> - Đo lưu lượng xe vượt vạch ảo (Flow VPM - xe/phút) và cảnh báo kẹt xe chuẩn xác.

---

## Định dạng Dữ liệu Đầu ra (Outputs)

Tất cả các lệnh khi chạy xong đều tự động lưu file ảnh kết quả `.jpg` kèm tệp dữ liệu JSON vào thư mục `outputs/` tương ứng:

- **`road_segmentation/outputs/`**: Chứa ảnh mask phân đoạn và file JSON gồm `road_area_ratio`, `road_area_percentage`, `sidewalk_area_ratio`, `road_polygons`.
- **`traffic_counter/outputs/`**: Chứa ảnh chụp HUD và file JSON gồm `traffic_status`, `congestion_level`, `vehicle_breakdown`, `tripwire_counting`, `average_speed_kmh`, `has_water`, `water_confidence`.
