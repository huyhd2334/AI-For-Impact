# Module: Traffic Camera & Flood Analyzer (SafeRoute AI)

Module này đảm nhận vai trò **Agent 1: Camera Monitoring** trong hệ thống **SafeRoute AI**, chuyên phân tích luồng video camera giao thông để:
1. **Đếm và ước lượng mật độ phương tiện** (`low`, `moderate`, `heavy`, `congested`).
2. **Nhận diện ngập nước & đo mức ngập ước lượng** qua vật mốc (bánh xe, pů xe, chân người).
3. **Phát hiện tổ hợp rủi ro kép (Compound Hazard)**: Khi mặt đường vừa ngập sâu vừa đông đúc xe cộ $\rightarrow$ Phát cảnh báo khẩn cấp và đề xuất điều phối CSGT.
4. **Emit sự kiện chuẩn hóa (Pydantic Schema)**: Xuất file JSON outbox, lưu ảnh bằng chứng (`evidence`) và push Webhook sang Backend FastAPI.

---

## Cấu trúc thư mục

```
traffic_camera_analyzer/
├── schemas.py              # Định nghĩa Pydantic schema chuẩn TrafficIncidentEvent
├── vehicle_counter.py      # Module đếm xe (YOLOv8 + Fallback Background Subtraction)
├── water_detector.py       # Module phân tích nước ngập (Heuristic CV + VLM Prompting)
├── event_emitter.py        # Module phát tín hiệu (Console, Lưu JSON, Gửi Webhook)
├── analyzer.py             # Pipeline chính kết nối các module
├── generate_mock_video.py  # Script tạo video giả lập để test ngay lập tức
├── main.py                 # CLI Runner
└── requirements.txt        # Các thư viện phụ thuộc
```

---

## Hướng dẫn chạy thử nghiệm

### 1. Cài đặt thư viện
```bash
pip install -r traffic_camera_analyzer/requirements.txt
```

### 2. Chạy thử với video tự động giả lập (Không cần chuẩn bị video trước)
```bash
python -m traffic_camera_analyzer.main --mock
```

### 3. Chạy với video camera thực tế hoặc luồng RTSP
```bash
python -m traffic_camera_analyzer.main --source "duong_ngap.mp4" --cam-id "CAM_NGATU_HANGXANH" --interval 2.0 --preview
```

---

## Logic Tổ hợp Sự kiện (Compound Event Logic)

| Mật độ xe | Tình trạng nước | Loại sự cố (`HazardType`) | Mức độ (`Severity`) | Hành động đề xuất CSGT |
| :--- | :--- | :--- | :--- | :--- |
| **Đông / Kẹt cứng** | **Ngập vừa / sâu** | `flood_and_congestion` | 🚨 **CRITICAL** | Cử CSGT phân luồng khẩn cấp, chặn đầu đường cảnh báo xe máy |
| Bình thường | Ngập sâu (>30cm) | `flood` | ⚠️ **HIGH** | Đặt biển cảnh báo điểm ngập sâu |
| Kẹt cứng | Không ngập | `traffic_congestion` | 🟡 **MEDIUM** | Điều tiết nhịp đèn giao thông |
| Thông thoáng | Khô ráo | `clear` | 🟢 **LOW** | Không cần xử lý |
