# SafeRoute Camera AI Service (Contract v1)

Dịch vụ AI xử lý hình ảnh & video camera giao thông cố định, tuân thủ tài liệu kiến trúc **SafeRoute AI - Software Design Document (SDD)** và **Software Requirements Document (SRD)**.

---

## 1. Kiến trúc luồng tích hợp (Pipeline Architecture)

Được thiết kế tối ưu hóa theo quy trình đề xuất cho camera cố định:

```text
Video camera / Frame + camera_id + thời điểm
│
├── [1] Frame chuẩn  ───► Road segmentation ───► road_mask (lưu cache để tái sử dụng 0ms)
│
├── [2] Frame mới + road_mask ───► Flood reasoning ───► Có nước trên đường? Bao nhiêu %? (Gemini Flash / CV)
├── [3] Frame mới             ───► Flood detector  ───► Đo độ sâu ngập (cm) & tính xác thực (Credibility)
└── [4] Nhiều frame liên tiếp ───► Traffic counter ───► Đếm xe, đo vận tốc, phát hiện xe dừng & kẹt xe
│
▼
[5] Bộ kết hợp kết quả (Result Fusion Gate)
├── Trạng thái ngập: flood_status, severity, water_depth_cm, safe_for_bikes, confidence
└── Mức ùn tắc: traffic_status, is_jammed, total_vehicles, avg_speed_kmh, flow_vpm
```

---

## 2. API Endpoints (FastAPI)

Khởi động server:
```bash
uvicorn camera_ai.api:app --host 0.0.0.0 --port 8000
```
Swagger UI tài liệu tương tác: `http://localhost:8000/docs`

### Các Endpoints chính:

| Phương thức | Đường dẫn | Chức năng | Input | Output |
|---|---|---|---|---|
| `GET` | `/health` hoặc `/api/v1/health` | Kiểm tra tình trạng dịch vụ, GPU và danh sách camera đã cache | Không | `status: "healthy"`, device, cached cameras |
| `POST` | `/api/v1/camera/calibrate` | Hiệu chuẩn & tạo `road_mask` cho camera cố định (lưu cache) | JSON (`camera_id`, `image_path` / `image_base64`) | `CalibrateResponse` |
| `POST` | `/api/v1/camera/calibrate/upload` | Hiệu chuẩn camera bằng cách tải trực tiếp file ảnh | Multipart Form (`camera_id`, `file`) | `CalibrateResponse` |
| `POST` | `/api/v1/camera/analyze-frame` | Phân tích 1 frame với camera_id (tái sử dụng road_mask đã cache) | JSON (`camera_id`, `image_path` / `image_base64`, `timestamp`) | `CameraObservationEvent` (Contract v1) |
| `POST` | `/api/v1/camera/analyze-frame/upload` | Phân tích 1 frame bằng cách tải trực tiếp file ảnh | Multipart Form (`camera_id`, `file`, `timestamp`) | `CameraObservationEvent` (Contract v1) |
| `POST` | `/api/v1/camera/analyze-video` | Xử lý video clip / stream, theo dõi phương tiện liên tục | JSON (`camera_id`, `video_path`, `target_fps`) | Danh sách `CameraObservationEvent` |
| `GET` | `/api/v1/camera/{camera_id}/state` | Lấy trạng thái mới nhất của camera (`camera_current_state`) | Path: `camera_id` | `CameraCurrentState` |
| `GET` | `/api/v1/camera/{camera_id}/events` | Đồng bộ lịch sử sự kiện theo cursor revision (`camera_state_events`) | Path: `camera_id`, Query: `since_revision`, `limit` | Danh sách `CameraObservationEvent` |
| `GET` | `/api/v1/camera/{camera_id}/road-mask` | Tải ảnh PNG mặt nạ lòng đường đã cache | Path: `camera_id` | Image file (PNG) |

---

## 3. Cấu trúc Hợp đồng Dữ liệu (Observation Event Contract v1)

```json
{
  "event_id": "cam_binhthanh_01_rev0001_8a12bc",
  "camera_id": "CAM_BINHTHANH_01",
  "observed_at": "2026-10-18T07:15:00.000000+00:00",
  "source_updated_at": "2026-10-18T07:15:01.000000+00:00",
  "source_revision": 1,
  "flood_status": "flooded",
  "confidence": 0.95,
  "model_version": "saferoute-fusion-v1.0",
  "severity": "severe",
  "water_depth_cm": 37.8,
  "depth_confidence": 0.78,
  "depth_method": "geometric_water_coverage",
  "safe_for_bikes": false,
  "road_water_ratio": 0.45,
  "traffic_status": "CROWDED",
  "is_jammed": false,
  "total_vehicles": 5,
  "vehicle_breakdown": {
    "motorcycle": 1,
    "car": 3,
    "bus": 0,
    "truck": 1,
    "bicycle": 0
  },
  "stopped_vehicles": 0,
  "slow_vehicles": 0,
  "avg_speed_kmh": 0.0,
  "flow_vpm": 0.0,
  "road_mask_cached": true,
  "evidence_ref": "evidence_captures/obs_CAM_BINHTHANH_01_rev0001_8a12bc.jpg",
  "reason": "Trạng thái ngập: FLOODED [SEVERE], độ sâu ước tính ~37.8 cm (geometric_water_coverage)..."
}
```

---

## 4. Chạy qua dòng lệnh (CLI)

1. **Khởi chạy API Server:**
   ```bash
   python -m camera_ai.main --serve --port 8000
   ```

2. **Chạy hiệu chuẩn Road Mask cho camera:**
   ```bash
   python -m camera_ai.main --calibrate --camera-id CAM_D1_001 --image data_test/sample_day.jpg
   ```

3. **Chạy phân tích ảnh:**
   ```bash
   python -m camera_ai.main --camera-id CAM_D1_001 --image images/high_1.png
   ```

4. **Chạy phân tích video:**
   ```bash
   python -m camera_ai.main --camera-id CAM_D1_001 --video data_test/day_1.mp4 --fps 5
   ```
