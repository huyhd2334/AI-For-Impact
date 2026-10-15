# AI Road Intelligence & Traffic Surveillance

Hệ thống thị giác máy tính (Computer Vision) toàn diện phục vụ quản lý giao thông và giám sát ngập lụt đô thị Việt Nam, tối ưu hóa cho camera CCTV độ phân giải cao (lên đến 4K).

Dự án được cấu trúc thành 2 module độc lập, chuyên sâu:

```
AI_solution/
├── road_segmentation/         # Module 1: Phân đoạn mặt đường & vỉa hè (Road Segmentation)
│   ├── main.py                # Điểm khởi chạy pipeline phân đoạn
│   ├── model.py               # Mask2Former (SOTA) & TwinLiteNet (Realtime)
│   ├── hybrid_segmenter.py    # Thuật toán tự cân chỉnh hành lang đường và lọc nhiễu ban đêm
│   ├── corridor_fusion.py     # Hợp nhất hành lang đường
│   ├── outputs/               # Kết quả xuất tự động: ảnh segmentation + file .json
│   └── requirements.txt
│
├── traffic_counter/           # Module 2: Nhận diện, đếm lưu lượng xe & đánh giá ùn tắc (Traffic Counter)
│   ├── main.py                # Điểm khởi chạy phân tích camera giao thông
│   ├── analyzer.py            # Quản lý luồng video, tính toán vận tốc & kết luận kẹt xe
│   ├── vehicle_counter.py     # YOLOv8 Tracking (ByteTrack), vạch ảo Tripwire & Density Heatmap
│   ├── water_detector.py      # Phát hiện mặt đường ướt / đọng nước ngập
│   ├── schemas.py             # Schema chuẩn hóa dữ liệu
│   ├── outputs/               # Kết quả xuất tự động: ảnh snapshot + file .json
│   └── requirements.txt
│
└── .gitignore                 # Bỏ qua weights lớn (*.pt, *.pth), video test (*.mp4), cache
```

---

## 1. Module 1: Road Segmentation (`road_segmentation/`)

Phân đoạn mặt đường và vỉa hè đạt độ chính xác cao cả ngày lẫn đêm (xử lý bóng cây, ánh đèn đường ban đêm).

### Tính năng chính:
- **Mô hình SOTA:** Tích hợp `facebook/mask2former-swin-small-cityscapes-semantic` kết hợp tự động tinh chỉnh hành lang (Corridor Fusion).
- **Tăng tốc 4K bằng PyTorch CUDA:** Xử lý blending trực tiếp trên GPU VRAM (~11ms/frame 4K thay vì 500ms+ trên CPU).
- **Hỗ trợ Realtime:** Tùy chọn TwinLiteNet siêu nhẹ (>100 FPS).

### Output tự động (`road_segmentation/outputs/`):
- `<source_id>_segmented.jpg`: Ảnh phân đoạn trực quan (Mặt đường xanh lá, vỉa hè tím neon).
- `<source_id>_segmentation.json`: Toàn bộ thông số định lượng gồm:
  - `road_area_ratio` & `road_area_percentage`: Tỷ lệ diện tích đường trên toàn khung hình.
  - `sidewalk_area_ratio`: Tỷ lệ vỉa hè.
  - `road_polygons`: Tọa độ đa giác hành lang mặt đường.
  - `processing_time_ms`: Thời gian xử lý.

### Cách chạy:
```bash
python -m road_segmentation.main --image path/to/image.jpg
```

---

## 2. Module 2: Traffic Counter & Congestion Analysis (`traffic_counter/`)

Nhận diện phương tiện giao thông đặc thù Việt Nam, đếm lưu lượng, ước lượng vận tốc và cảnh báo kẹt xe chuẩn xác.

### Tính năng chính:
- **AI Model chuyên biệt giao thông VN:** Hỗ trợ mô hình huấn luyện riêng cho xe máy, ô tô con, xe buýt, xe tải, xe đạp (`vn_traffic_surveillance.pt`).
- **Đếm lưu lượng vạch ảo (Virtual Tripwire):** Đếm chính xác số lượt xe vượt qua vạch cắt ngang đường theo hướng di chuyển và tính tốc độ xe/phút (Flow VPM).
- **Đánh giá kẹt xe đa tiêu chí:** Không chỉ dựa vào số lượng xe (vì đường VN lúc nào cũng đông), hệ thống chỉ báo `HEAVY_CONGESTION` khi số lượng xe lớn kết hợp vận tốc trung bình dòng xe giảm mạnh (< 15 km/h).
- **Bản đồ nhiệt (Traffic Density Heatmap):** Nhấn phím `H` khi xem preview để bật/tắt heatmap tích lũy điểm nghẽn.

### Output tự động (`traffic_counter/outputs/`):
- `<source_id>_traffic.jpg`: Ảnh chụp kèm Bounding Box, Track ID, vạch ảo Tripwire và HUD bảng điều khiển.
- `<source_id>_traffic.json`: Dữ liệu phân tích gồm:
  - `traffic_status`: `CLEAR` / `CROWDED` / `HEAVY_CONGESTION`
  - `congestion_level`: Mức độ từ 1 đến 3
  - `total_vehicles_present`: Tổng số xe đang có trong khung hình
  - `vehicle_breakdown`: Chi tiết từng loại phương tiện (`motorcycle`, `car`, `bus`, `truck`, `bicycle`)
  - `tripwire_counting`: `total_crossed` và `flow_vehicles_per_min`
  - `average_speed_kmh`: Vận tốc trung bình của các xe đang di chuyển
  - `is_jammed`: Boolean cảnh báo ùn tắc
  - `has_water`: Boolean phát hiện mặt đường có nước (`true`/`false`, không đo độ sâu)
  - `water_confidence`: Độ tin cậy phát hiện nước

### Cách chạy:
```bash
# Phân tích video hoặc ảnh camera
python -m traffic_counter.main --source path/to/video.mp4 --preview

# Chạy với ảnh tĩnh
python -m traffic_counter.main --source path/to/image.jpg
```
