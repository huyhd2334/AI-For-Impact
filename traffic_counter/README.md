# Module: Traffic Counter & Congestion Analyzer

Module đảm nhận phân tích luồng camera giao thông:
1. **Nhận diện & đếm số lượng phương tiện** theo chủng loại đặc thù Việt Nam (xe máy, ô tô con, xe tải, xe buýt, xe đạp).
2. **Đo lưu lượng qua vạch ảo (Virtual Tripwire)** và tính tốc độ xe/phút (Flow VPM).
3. **Phát hiện ùn tắc giao thông thực sự**: Kết hợp số lượng xe và vận tốc trung bình di chuyển (< 15 km/h).
4. **Phát hiện có nước trên mặt đường (`has_water: bool`)**: Nhận diện vùng đọng nước/ẩm ướt trên mặt đường phản quang qua HSV & Laplacian (nhận diện trạng thái Có/Không, không đo độ sâu).
5. **Xuất kết quả tự động**: Lưu ảnh snapshot HUD (`.jpg`) và tệp chỉ số cấu trúc (`.json`) vào thư mục `outputs/`.

---

## Cấu trúc thư mục

```
traffic_counter/
├── schemas.py              # Định nghĩa schema chuẩn TrafficIncidentEvent
├── vehicle_counter.py      # Nhận diện YOLOv8, ByteTrack, vạch ảo Tripwire & Density Heatmap
├── water_detector.py       # Phát hiện mặt đường có nước (HSV phản quang + Laplacian)
├── event_emitter.py        # Xuất sự kiện JSON & Webhook
├── analyzer.py             # Pipeline phân tích video/ảnh và xuất kết quả
├── generate_mock_video.py  # Script tạo video giả lập chạy thử
├── main.py                 # Điểm khởi chạy CLI
├── weights/                # Chứa weights (vn_traffic_surveillance.pt)
└── requirements.txt        # Thư viện phụ thuộc
```

---

## Hướng dẫn chạy thử nghiệm

### 1. Cài đặt thư viện
```bash
pip install -r traffic_counter/requirements.txt
```

### 2. Chạy với ảnh tĩnh
```bash
python -m traffic_counter.main --source data_test/sample_day.jpg
```

### 3. Chạy với video camera hoặc luồng RTSP
```bash
python -m traffic_counter.main --source path/to/video.mp4 --preview --heatmap
```
- Phím `Q` hoặc `ESC`: Thoát xem trực tiếp.
- Phím `H`: Bật/Tắt bản đồ nhiệt mật độ giao thông (Heatmap).

---

## Định dạng JSON Output (`outputs/<source_id>_traffic.json`)

```json
{
  "timestamp": "2026-10-15T21:55:43.518286",
  "camera_id": "CAM_BINHTHANH_01",
  "traffic_status": "CROWDED",
  "congestion_level": 2,
  "congestion_description": "Giao thông: Đường đông nhưng lưu thông tốt...",
  "total_vehicles_present": 46,
  "vehicle_breakdown": {
    "motorcycle": 41,
    "car": 4,
    "bus": 0,
    "truck": 0,
    "bicycle": 1
  },
  "tripwire_counting": {
    "total_crossed": 0,
    "flow_vehicles_per_min": 0.0
  },
  "average_speed_kmh": 0.0,
  "is_jammed": false,
  "has_water": false,
  "water_confidence": 0.0,
  "alert_level": "INFO"
}
```
