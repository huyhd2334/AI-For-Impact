# SafeRoute Camera AI Service (Contract v1)

Dịch vụ AI xử lý hình ảnh & video camera giao thông cố định, tuân thủ tài liệu kiến trúc **SafeRoute AI - Software Design Document (SDD)** và **Software Requirements Document (SRD)**.

---

## 1. Kiến trúc luồng tích hợp (Pipeline Architecture)

Được thiết kế tối ưu hóa cho cả **Ảnh Thumbnail Camera (Snapshot 1 frame)** và **Video Stream liên tục**:

```text
Ảnh Thumbnail / Video Camera + camera_id + timestamp
│
├── [Quality Gate] Kiểm tra chất lượng ảnh (độ sáng, độ mờ Laplacian, che khuất)
│   └── Nếu ảnh lỗi / quá tối / mất nét ──► Flood: UNKNOWN | Traffic: UNKNOWN
│
├── [Nhánh 1] Road Segmentation ──► road_mask (cache vĩnh viễn, tái sử dụng 0ms)
│
├── [Nhánh 2] Phân tích Ngập nước:
│   ├── 2A. Flood Reasoning (Gemini Flash VLM + CV specular) ──► Có nước không? Tỷ lệ nước?
│   └── 2B. Water Depth Estimator (chìm bánh xe / chassis)   ──► Độ sâu (cm), Xe máy đi được không?
│   └── Quy tắc phân loại Ngập: UNKNOWN | Không ngập | Ngập đi được | Ngập nhiều
│
├── [Nhánh 3] Phân tích Giao thông & Kẹt xe:
│   ├── 3A. YOLOv8 Vehicle Detection (vn_traffic_surveillance) ──► Đếm xe trên lòng đường
│   ├── 3B. Road Occupancy Ratio (Đặc thù ảnh Thumbnail)       ──► % diện tích lòng đường bị chiếm
│   └── 3C. Multi-frame Tracking (Đặc thù Video)                ──► Đo vận tốc km/h, xe dừng, kẹt cứng
│   └── Quy tắc phân loại Tắc: UNKNOWN | CLEAR | CROWDED | HEAVY_CONGESTION
│
▼
[Nhánh 4] Bộ Kết Hợp Kết Quả (Result Fusion Gate)
├── Chuẩn hóa CameraObservationEvent (Contract v1) & CameraCurrentState
└── Vẽ HUD Dashboard trực quan + lưu ảnh bằng chứng (Evidence Capture)
```

---

## 2. Chi Tiết Đầu Vào (Input) & Đầu Ra (Output) của Từng Nhánh

### 🔍 Nhánh 0: Tiền Xử Lý & Đánh Giá Chất Lượng Ảnh (Quality Check Gate)

* **Mục tiêu:** Ngăn chặn việc dự đoán sai khi camera bị mất tín hiệu, chập chờn, ban đêm không có đèn đường hoặc nước đọng mờ tịt ống kính.
* **Input:**
  - `frame` (`np.ndarray` BGR): Ảnh thumbnail snapshot hoặc frame video từ camera.
  - `camera_id` (`str`): Mã định danh camera trong hệ thống (ví dụ: `CAM_BINHTHANH_01`).
* **Quá trình tính toán:**
  - Độ sáng trung bình: $\text{mean\_brightness} = \text{mean}(\text{gray})$.
  - Độ sắc nét bằng phương sai toán tử Laplacian: $\text{laplacian\_var} = \text{Var}(\text{Laplacian}(\text{gray}))$.
  - Điều kiện bất thường: $\text{mean\_brightness} < 18.0$ (quá tối) HOẶC $\text{laplacian\_var} < 30.0$ (mờ tịt).
* **Output:**
  - `is_low_quality` (`bool`): `True` nếu ảnh không đủ điều kiện phân tích.
  - Phán định sớm: Nếu `is_low_quality = True` $\rightarrow$ gán thẳng `flood_status_vn = "UNKNOWN"` và `traffic_status = "UNKNOWN"`.

---

### 🛣️ Nhánh 1: Phân Đoạn Mặt Đường & Cache ROI (`RoadMaskManager`)

* **Mục tiêu:** Cô lập chính xác vùng lòng đường xe chạy (**Drivable Area**), loại bỏ hoàn toàn nhiễu từ vỉa hè, nhà dân, cây cối, bầu trời. Tái sử dụng với độ trễ **0ms** cho camera cố định.
* **Input:**
  - `camera_id` (`str`): Mã camera.
  - `current_frame` (`np.ndarray`): Khung hình chuẩn hoặc frame đầu tiên.
  - `auto_calibrate_if_missing` (`bool`): Tự động hiệu chuẩn nếu chưa có cache (mặc định `True`).
  - `force` (`bool`): Bắt buộc tính lại và ghi đè cache (mặc định `False`).
* **Quá trình tính toán:**
  - Kiểm tra file cache tại `road_segmentation/cache/{camera_id}_road_mask.png`.
  - Nếu đã có cache $\rightarrow$ đọc trực tiếp (chi phí ~0ms CPU/GPU).
  - Nếu chưa có $\rightarrow$ đưa qua mạng `TwinLiteNet` (hoặc `Mask2Former`), trích xuất đa giác đường và lưu ảnh mặt nạ PNG.
* **Output:**
  - `road_mask` (`np.ndarray` uint8 shape `(H, W)`): Mặt nạ nhị phân lòng đường ($255$ = đường, $0$ = nền).
  - `sidewalk_mask` (`np.ndarray` uint8 shape `(H, W)`): Mặt nạ vỉa hè.
  - `is_cached` (`bool`): `True` nếu đọc từ cache, `False` nếu vừa calibrate mới.
  - `road_ratio` (`float`): Tỷ lệ diện tích lòng đường trên toàn ảnh (ví dụ `0.452` = 45.2%).

---

### 🌊 Nhánh 2: Phân Tích Ngập Nước (`FloodReasoner` & `WaterDepthEstimator`)

Nhánh này chia làm 2 hợp phần bổ trợ nhau để đưa ra kết luận toàn diện:

#### Hợp phần 2A: Suy luận diện tích nước (`FloodReasoner`)
* **Input:**
  - `frame` (`np.ndarray`): Ảnh chụp hiện tại.
  - `road_mask` (`np.ndarray`): Mặt nạ lòng đường từ Nhánh 1.
  - `use_llm` (`bool`): Cho phép gọi Gemini Flash VLM (mặc định `True`).
* **Quá trình tính toán:**
  - **Local CV:** Chuyển đổi màu HSV, lọc vùng phản chiếu ánh sáng mặt nước (specular water reflections) và đọng nước chỉ trong phạm vi `road_mask`.
  - **Gemini Flash Reasoning:** Gửi crop mặt đường qua API Gemini (2.5 / 2.0 / 1.5 Flash) với Prompt chuyên gia và Response Schema có cấu trúc JSON.
* **Output 2A:**
  - `water_mask` (`np.ndarray` uint8): Mặt nạ nhị phân vùng ngập nước trên lòng đường.
  - `road_water_ratio` (`float`): Tỷ lệ diện tích ngập trên mặt đường ($0.0 \rightarrow 1.0$).
  - `is_flooded` (`bool`): `True` nếu có nước ngập mặt đường.
  - `severity` (`str`): Mức độ ngập (`"none"`, `"minor"`, `"moderate"`, `"severe"`).
  - `confidence` (`float`): Độ tin cậy suy luận ($0.0 \rightarrow 1.0$).
  - `reason` (`str`): Lời giải thích trực quan từ AI.

#### Hợp phần 2B: Ước lượng độ sâu ngập nước (`WaterDepthEstimator`)
* **Input:**
  - `frame` (`np.ndarray`): Ảnh chụp.
  - `water_mask` (`np.ndarray`): Vùng nước từ Hợp phần 2A.
  - `road_water_ratio` (`float`): Tỷ lệ phủ nước.
  - `detected_vehicles` (`List[Dict]`): Tọa độ bounding box các phương tiện trên đường.
* **Quá trình tính toán:**
  - **Automotive Submersion:** Đo tỷ lệ phần dưới của bánh xe/thân xe máy và ô tô bị chìm trong `water_mask`.
  - **Geometric Coverage:** Nếu không có xe, ước lượng hình học dựa trên độ lan tỏa mặt nước.
* **Output 2B:**
  - `water_depth_cm` (`Optional[float]`): Độ sâu ước tính tính bằng cm (ví dụ `18.5`). Trả về `None` nếu đường khô ráo.
  - `depth_confidence` (`float`): Độ tin cậy của phép đo độ sâu ($0.0 \rightarrow 1.0$).
  - `depth_method` (`str`): Phương pháp đo (`"automotive"`, `"geometric_water_coverage"`).
  - `safe_for_bikes` (`bool`): `True` nếu xe máy/xe đạp còn đi qua an toàn được.

#### 🎯 Quy tắc ánh xạ Tình trạng Ngập (`flood_status_vn`):
| Trạng thái Ngập (`flood_status_vn`) | Tiêu chí kích hoạt | Ý nghĩa thực tế |
| :--- | :--- | :--- |
| **`UNKNOWN`** | Ảnh lỗi, quá tối, mờ tịt (`is_low_quality = True`) hoặc `confidence < 0.45` | Không xác định được, cần kiểm tra camera |
| **`Không ngập`** | `is_flooded = False` VÀ `road_water_ratio < 0.035` (dưới 3.5%) | Đường khô ráo, lưu thông hoàn toàn bình thường |
| **`Ngập đi được`** | Có nước, độ sâu $< 20\text{ cm}$, ngập mắt cá chân/dưới nửa bánh xe máy, `safe_for_bikes = True` | Xe máy, ô tô vẫn lội qua được an toàn |
| **`Ngập nhiều`** | Độ sâu $\ge 20 - 25\text{ cm}$, hoặc `road_water_ratio \ge 0.25`, hoặc ngập quá nửa bánh/lốc máy/pô xe, `safe_for_bikes = False` | Xe chết máy, nguy hiểm, khuyến cáo không đi qua |

---

### 🚦 Nhánh 3: Phân Tích Giao Thông & Kẹt Xe (`VehicleDetector` & Congestion Engine)

Nhánh này hỗ trợ cả **Ảnh Thumbnail đơn lẻ** và **Video stream nhiều frame**:

#### Hợp phần 3A: Phát hiện phương tiện (`VehicleDetector`)
* **Input:** `frame` (`np.ndarray`) + `road_mask`.
* **Quá trình tính toán:**
  - Mô hình YOLOv8 (`vn_traffic_surveillance.pt`, 1280px) phát hiện 5 lớp: xe máy, ô tô, xe buýt, xe tải, xe đạp.
  - Lọc bỏ các xe đậu trên vỉa hè hoặc trong nhà (chỉ giữ xe nằm trên `road_mask`).
* **Output 3A:**
  - `total_vehicles` (`int`): Tổng số xe trên lòng đường.
  - `vehicle_breakdown` (`Dict[str, int]`): Phân loại chi tiết (ví dụ `{"motorcycle": 38, "car": 4, ...}`).
  - `boxes` (`List[Tuple]`): Danh sách bounding box `[x1, y1, x2, y2]`.

#### Hợp phần 3B: Tỷ lệ chiếm dụng lòng đường (Road Occupancy - Dành cho Ảnh Thumbnail)
* **Input:** Danh sách `boxes` các xe + Diện tích pixel của `road_mask`.
* **Quá trình tính toán:**
  $$\text{Road Occupancy Ratio} = \frac{\sum \text{Diện tích Bounding Box các xe trên đường}}{\text{Tổng diện tích Road Mask}}$$
* **Output 3B:**
  - `road_occupancy_ratio` (`float`): Giá trị từ $0.0 \rightarrow 1.0$ (ví dụ `0.35` = xe cộ chiếm 35% diện tích mặt đường).

#### Hợp phần 3C: Vận tốc & Dòng xe (Dành cho Video nhiều frame)
* **Input:** Chuỗi frame liên tiếp + timestamp video.
* **Output 3C:**
  - `avg_speed_kmh` (`Optional[float]`): Vận tốc trung bình của dòng xe (km/h).
  - `stopped_vehicles` (`int`): Số xe đứng yên tại chỗ ($0\text{ km/h}$).
  - `slow_vehicles` (`int`): Số xe bò chậm chạp ($< 15\text{ km/h}$).
  - `flow_vpm` (`float`): Lưu lượng xe vượt qua vạch ảo tripwire (xe/phút).
  - `is_jammed` (`bool`): `True` nếu kẹt cứng kéo dài.

#### 🎯 Quy tắc ánh xạ Tình trạng Tắc (`traffic_status`):
| Trạng thái Tắc (`traffic_status`) | Tiêu chí kích hoạt | Ý nghĩa thực tế |
| :--- | :--- | :--- |
| **`UNKNOWN`** | Ảnh lỗi, quá tối, mờ tịt (`is_low_quality = True`) | Không xác định được |
| **`CLEAR`** | $\text{Occupancy} < 14\%$ VÀ số xe ít ($< 8$ xe), đường vắng | Đường thông thoáng, chạy đúng tốc độ |
| **`CROWDED`** | $\text{Occupancy} \in [14\%, 45\%]$ HOẶC $\ge 8$ xe, HOẶC vận tốc $15 - 22\text{ km/h}$ | Xe cộ đông đúc nhưng vẫn lưu thông bình thường |
| **`HEAVY_CONGESTION`** | $\text{Occupancy} \ge 45\%$, HOẶC $\ge 20$ xe ken đặc lòng đường, HOẶC vận tốc $< 8\text{ km/h}$, `is_jammed = True` | Kẹt xe nghiêm trọng, xe dừng nối đuôi nhau |

---

### 🛡️ Nhánh 4: Bộ Kết Hợp Kết Quả & Ảnh Bằng Chứng (`CameraResultFusion`)

* **Input:**
  - `camera_id` (`str`), `source_revision` (`int`), `observed_at` (`str`).
  - Toàn bộ kết quả đầu ra từ Nhánh 0, Nhánh 1, Nhánh 2 (2A, 2B), và Nhánh 3 (3A, 3B, 3C).
  - Cờ `save_evidence` (`bool`).
* **Quá trình tính toán:**
  - Hợp nhất phán quyết theo ma trận độ tin cậy.
  - Vẽ lớp phủ trực quan: contour lòng đường (viền cam), lớp nước ngập (xanh dương trong suốt), bounding box phương tiện.
  - Vẽ banner HUD Dashboard chuyên nghiệp ở cạnh trên ảnh.
  - Lưu file ảnh bằng chứng vào thư mục `evidence_captures/obs_{camera_id}_{event_id}.jpg`.
* **Output:**
  - **`CameraObservationEvent` (Contract v1)**: JSON payload gửi về cho SafeRoute Worker (xem mục 3).
  - **`annotated_composite` (`np.ndarray`)**: Ảnh bằng chứng hoàn chỉnh có HUD dashboard.
  - Cập nhật bảng tra cứu bộ nhớ:
    - `camera_current_state`: Bản ghi trạng thái mới nhất của camera.
    - `camera_state_events`: Lịch sử các sự kiện phục vụ đồng bộ phân trang.

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
  "flood_status_vn": "Ngập đi được",
  "confidence": 0.95,
  "model_version": "saferoute-fusion-v1.0",
  "severity": "minor",
  "water_depth_cm": 14.5,
  "depth_confidence": 0.85,
  "depth_method": "automotive",
  "safe_for_bikes": true,
  "road_water_ratio": 0.18,

  "traffic_status": "CROWDED",
  "is_jammed": false,
  "road_occupancy_ratio": 0.285,
  "total_vehicles": 14,
  "vehicle_breakdown": {
    "motorcycle": 11,
    "car": 3,
    "bus": 0,
    "truck": 0,
    "bicycle": 0
  },
  "stopped_vehicles": 0,
  "slow_vehicles": 2,
  "avg_speed_kmh": 19.5,
  "flow_vpm": 0.0,

  "road_mask_cached": true,
  "evidence_ref": "evidence_captures/obs_CAM_BINHTHANH_01_rev0001_8a12bc.jpg",
  "reason": "Trạng thái ngập: Ngập đi được (FLOODED[MINOR], độ sâu ước tính ~14.5 cm (automotive)). Giao thông: CROWDED (14 xe, chiếm dụng: 28% lòng đường, 0 xe dừng, tốc độ TB: 19.5 km/h). Nước xâm xấp mặt đường, các phương tiện xe máy vẫn lưu thông được."
}
```

---

## 4. API Endpoints (FastAPI)

Khởi động server:
```bash
uvicorn camera_ai.api:app --host 0.0.0.0 --port 8000
```
Swagger UI tài liệu tương tác: `http://localhost:8000/docs`

### Bảng tra cứu Endpoints:

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

## 5. Chạy qua dòng lệnh (CLI)

1. **Khởi chạy API Server:**
   ```bash
   python -m camera_ai.main --serve --port 8000
   ```

2. **Chạy hiệu chuẩn Road Mask cho camera:**
   ```bash
   python -m camera_ai.main --calibrate --camera-id CAM_D1_001 --image data_test/sample_day.jpg
   ```

3. **Chạy phân tích ảnh snapshot thumbnail:**
   ```bash
   python -m camera_ai.main --camera-id CAM_TEST_THUMB --image data_test/sample_day.jpg
   ```

4. **Chạy phân tích video stream:**
   ```bash
   python -m camera_ai.main --camera-id CAM_D1_001 --video data_test/day_1.mp4 --fps 5
   ```
