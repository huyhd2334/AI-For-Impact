# Hướng Dẫn Vận Hành & Khai Thác Hệ Thống (Quick Operation & I/O Guide)

Tài liệu này tập trung hướng dẫn **Cách Chạy (Execution)**, **Cách Cung Cấp Đầu Vào (Input)**, **Vị Trí Lưu Kết Quả (Output Storage)** và **Định Dạng Dữ Liệu Chi Tiết (Output Formats & Schemas)** cho toàn bộ các module trong hệ thống.

---

## 📌 Bảng Tra Cứu Nhanh Các Module

| Tác vụ / Module | Lệnh Chạy Cốt Lõi | Dữ Liệu Input | Thư Mục Lưu Output | Định Dạng Output Xuất Ra |
| :--- | :--- | :--- | :--- | :--- |
| **1. Phân đoạn mặt đường**<br>`road_segmentation` | `python -m road_segmentation.main --source <input>` | Ảnh (`.jpg`, `.png`)<br>hoặc Video (`.mp4`) | `road_segmentation/outputs/` | • `*_segmented.jpg`<br>• `*_road_mask.png`<br>• `*_segmentation.json` |
| **2. Đếm xe & Đo kẹt xe**<br>`traffic_counter` | `python -m traffic_counter.main --source <input>` | Video (`.mp4`), Ảnh,<br>hoặc Luồng `rtsp://` | • `traffic_counter/outputs/`<br>• `events_outbox/`<br>• `evidence_captures/` | • `*_traffic.jpg`<br>• `*_traffic.json`<br>• `incident_*.json` / `telemetry_*.json`<br>• `evidence_*.jpg` |
| **3. Phân đoạn nước & Đo độ sâu**<br>`flood_detector` | `python -m flood_detector.main --image <input>` | Ảnh chụp cảnh ngập (`.jpg`, `.png`) | `flood_detector/outputs/` | • `*_water_mask.png`<br>• `*_water_overlay.jpg`<br>• `*_flood_result.json` |
| **4. Suy luận mức độ ngập đường**<br>`flood_reasoning.py` | `python flood_reasoning.py --image <input> --auto-run-road` | Ảnh chụp đường phố (`.jpg`, `.png`) | `outputs/` | • `*_flood_mask.png`<br>• `*_flooded_road_mask.png`<br>• `*_flood_reasoning_overlay.jpg`<br>• `*_flood_reasoning.json` |
| **5. Hợp nhất AI Camera (Contract v1)**<br>`camera_ai` (FastAPI / CLI) | `python -m camera_ai.main --camera-id <id> --image <input>`<br>hoặc `--serve` | Ảnh Thumbnail (`.jpg`),<br>Video hoặc REST API | `evidence_captures/`<br>`road_segmentation/cache/` | • `CameraObservationEvent` (JSON)<br>• `obs_<id>_<event_id>.jpg` (Ảnh HUD)<br>• `*_road_mask.png` (Cache 0ms) |

---

## 0. Chuẩn Bị Trọng Số (Weights)

Trước khi chạy, đảm bảo các file weights đã nằm trong thư mục `weights/` tại thư mục gốc:

```
weights/
├── vn_traffic_surveillance.pt        # YOLOv8 đếm xe giao thông VN (44.8 MB)
├── link_efficientb4_model.pth        # Phân đoạn mặt nước (72.3 MB)
├── best.pth                          # TwinLiteNet phân đoạn đường siêu nhẹ (1.8 MB)
└── yolov8n.pt                        # YOLOv8 nano dự phòng (6.5 MB)
```
*(Riêng mô hình **Meta Mask2Former** và **SegFormer** sẽ tự động tải từ Hugging Face Hub khi chạy lần đầu).*

---

## 1. Phân Đoạn Mặt Đường & Vỉa Hè (`road_segmentation`)

Module xác định phạm vi lòng đường xe chạy được (**Drivable Area**) và vỉa hè (**Sidewalk**), trích xuất tọa độ đa giác và mặt nạ nhị phân.

### 1.1. Cách chạy (Command)

```bash
# [Khuyến nghị] Chạy trên ẢNH dùng Meta Mask2Former SOTA (độ chính xác cao nhất)
python -m road_segmentation.main --source images/no_3.png --model mask2former --preview

# Chạy trên VIDEO dùng SegFormer-B2 (cân bằng chất lượng & tốc độ 35-45 FPS)
python -m road_segmentation.main --source data_test/day_1.mp4 --model b2 --preview

# Chạy với mô hình siêu nhẹ TwinLiteNet (cho máy yếu / Raspberry Pi / Jetson)
python -m road_segmentation.main --source images/no_1.png --model twinlite --preview
```

**Bảng tham số CLI:**
- `--source` hoặc `--image`: Đường dẫn file ảnh (`.jpg`, `.png`) hoặc video (`.mp4`). Tự động quét trong thư mục hiện tại hoặc `data_test/`.
- `--model`: Lựa chọn mô hình AI:
  - `mask2former` (Mặc định cho ảnh, chính xác nhất).
  - `b2` (SegFormer-B2, tối ưu cho video CCTV).
  - `b0` (SegFormer-B0, siêu nhẹ 70-85 FPS).
  - `twinlite` (TwinLiteNet 1.8 MB).
- `--preview`: Mở cửa sổ trực tiếp để xem kết quả.
- `--out-dir`: Thư mục lưu kết quả (Mặc định: `road_segmentation/outputs`).
- `--alpha`: Độ trong suốt của lớp màu phủ lên đường (Mặc định: `0.38`).

---

### 1.2. Cách input
- **Định dạng file:** 
  - Ảnh: `.jpg`, `.jpeg`, `.png`, `.bmp`, `.webp`.
  - Video: `.mp4`, `.avi`, `.mkv`, `.mov`.
- **Ví dụ đường dẫn:** `--source images/no_1.png` hoặc `--source D:/data/camera_01.mp4`.

---

### 1.3. Output lưu ở đâu?
Tất cả kết quả tự động xuất vào: **`road_segmentation/outputs/`**
- Ảnh trực quan hóa: `<tên_file_gốc>_segmented.jpg`
- Ảnh mặt nạ nhị phân: `<tên_file_gốc>_road_mask.png`
- Tệp dữ liệu JSON: `<tên_file_gốc>_segmentation.json`

---

### 1.4. Định dạng Output chi tiết

#### A. File ảnh Mask (`*_road_mask.png`):
- Ảnh thang độ xám 1 kênh (Grayscale uint8), cùng độ phân giải với ảnh gốc.
- **Giá trị pixel = 255 (Trắng):** Vùng lòng đường xe chạy.
- **Giá trị pixel = 0 (Đen):** Vỉa hè, nền, công trình, bầu trời.

#### B. File ảnh trực quan (`*_segmented.jpg`):
- Lòng đường được phủ màu xanh lá neon trong suốt.
- Vỉa hè được phủ màu tím hồng.

#### C. File JSON số liệu (`*_segmentation.json`):
```json
{
  "timestamp": "2026-10-16T11:45:00.123456",
  "source": "no_3.png",
  "model_used": "MASK2FORMER",
  "image_size": [1280, 720],
  "road_area_ratio": 0.4872,
  "road_area_percentage": 48.72,
  "sidewalk_area_ratio": 0.0835,
  "sidewalk_area_percentage": 8.35,
  "processing_time_ms": 320.5,
  "total_road_polygons": 1,
  "road_polygons": [
    [
      [120, 720],
      [450, 310],
      [780, 310],
      [1150, 720]
    ]
  ]
}
```
**Ý nghĩa các trường JSON:**
- `road_area_percentage`: Tỷ lệ phần trăm diện tích lòng đường chiếm trong toàn khung hình (%).
- `sidewalk_area_percentage`: Tỷ lệ vỉa hè chiếm trong khung hình (%).
- `road_polygons`: Mảng danh sách các đỉnh tọa độ $[x, y]$ bao bọc lòng đường (phục vụ làm vùng ROI tĩnh cho các hệ thống giám sát khác).

---

## 2. Nhận Diện, Đếm Xe & Đánh Giá Ùn Tắc (`traffic_counter`)

Module theo dõi xe cộ đa đối tượng (YOLOv8 + ByteTrack), đếm lưu lượng qua vạch ảo (**Virtual Tripwire**), tính vận tốc di chuyển (km/h) và phát hiện kẹt xe.

### 2.1. Cách chạy (Command)

```bash
# 1. Chạy trên ẢNH tĩnh (xuất snapshot HUD và JSON tức thì)
python -m traffic_counter.main --source images/traffic_jams.jpg --preview

# 2. Chạy trên VIDEO camera giao thông kèm Bản đồ nhiệt (Heatmap)
python -m traffic_counter.main --source data_test/traffic_sample.mp4 --preview --heatmap

# 3. Chạy với luồng CAMERA TRỰC TIẾP (RTSP IP Camera)
python -m traffic_counter.main --source rtsp://admin:12345@192.168.1.100:554/live --cam-id CAM_D1_01 --preview

# 4. Bật SAHI Slicing cho camera 4K / góc siêu rộng (bắt xe nhỏ ở xa)
python -m traffic_counter.main --source data_test/cctv_4k.mp4 --sahi --preview

# 5. Chạy giả lập VIDEO TEST (tự tạo video xe chạy và vũng nước để test mà không cần dữ liệu)
python -m traffic_counter.main --mock --preview
```

*Phím tương tác khi đang mở màn hình xem trực tiếp (`--preview`):*
- Bấm **`Q`** hoặc **`ESC`**: Thoát chương trình.
- Bấm **`H`**: Bật / Tắt lớp phủ bản đồ nhiệt mật độ giao thông (Heatmap).

**Bảng tham số CLI:**
- `--source`: File video (`.mp4`), ảnh (`.jpg`), link RTSP stream (`rtsp://...`).
- `--cam-id`: Tên định danh camera (Mặc định: `CAM_BINHTHANH_01`).
- `--interval`: Chu kỳ xuất bản tin sự kiện JSON và ảnh chụp bằng chứng (Mặc định: `5.0` giây/lần).
- `--conf`: Ngưỡng tin cậy nhận diện phương tiện (Mặc định: `0.18`).
- `--jam-speed`: Ngưỡng vận tốc (km/h) dưới mức này thì xác định là kẹt xe (Mặc định: `15.0`).
- `--tripwire-y`: Vị trí đặt vạch đếm ảo theo trục dọc (Mặc định: `0.58`, tương đương 58% chiều cao ảnh).
- `--heatmap`: Bật tính năng tích lũy bản đồ nhiệt mật độ xe di chuyển.
- `--sahi`: Bật chế độ chia nhỏ ảnh (tiling 768x768) nhận diện xe siêu nhỏ cho camera 4K.

---

### 2.2. Cách input
- **File video cục bộ:** `data_test/video.mp4`
- **File ảnh chụp:** `images/traffic_jams.jpg`
- **Luồng stream thời gian thực:** `rtsp://<user>:<pass>@<ip>:<port>/stream` hoặc webcam `0`.

---

### 2.3. Output lưu ở đâu?

Hệ thống ghi kết quả tự động vào 3 thư mục:

1. **`traffic_counter/outputs/`**: Lưu kết quả phân tích định kỳ hoặc khi chạy ảnh đơn:
   - `<source_id>_traffic.jpg`: Ảnh chụp kèm khung bounding box xe, vạch ảo, vận tốc và thông số HUD.
   - `<source_id>_traffic.json`: File JSON kết quả tổng hợp.
2. **`events_outbox/`**: Hộp thư chứa các gói tin sự kiện thời gian thực (Event Telemetry & Incidents):
   - `telemetry_<cam_id>_<uuid>.json`: Phát sinh định kỳ mỗi `--interval` giây khi giao thông bình thường.
   - `incident_<cam_id>_<uuid>.json`: Phát sinh ngay lập tức khi phát hiện **Ùn tắc giao thông** (`TRAFFIC_JAM`) hoặc **Mặt đường đọng nước** (`WATER_DETECTED`).
3. **`evidence_captures/`**: Lưu ảnh chụp bằng chứng độ nét cao tại thời điểm xảy ra sự cố (`evidence_<cam_id>_<uuid>.jpg`).

---

### 2.4. Định dạng Output chi tiết

#### File JSON Sự kiện Giao thông (`events_outbox/*.json` & `outputs/*_traffic.json`):
```json
{
  "event_id": "c1f9b763-92b1-4f16-8bc5-3e284db6183a",
  "timestamp": "2026-10-16T12:00:05.123456Z",
  "source_type": "camera_stream",
  "camera_id": "CAM_BINHTHANH_01",
  "location": {
    "latitude": 10.7769,
    "longitude": 106.7009
  },
  "vehicle_count": 46,
  "vehicle_breakdown": {
    "motorcycle": 41,
    "car": 4,
    "bus": 0,
    "truck": 0,
    "bicycle": 1
  },
  "is_congested": true,
  "avg_speed_kmh": 8.5,
  "total_crossed_tripwire": 128,
  "flow_vehicles_per_min": 54.2,
  "has_water": false,
  "water_confidence": 0.0,
  "alert_type": "TRAFFIC_JAM",
  "alert_level": "WARNING",
  "evidence_frame_path": "evidence_captures/evidence_CAM_BINHTHANH_01_c1f9b763.jpg",
  "message": "Giao thông: Ùn tắc nghiêm trọng! Vận tốc trung bình 8.5 km/h (< 15 km/h)."
}
```

**Ý nghĩa các trường JSON:**
- `vehicle_count`: Tổng số lượng phương tiện đang có mặt trong khung hình.
- `vehicle_breakdown`: Phân loại chi tiết số lượng từng loại xe: `motorcycle` (xe máy), `car` (ô tô), `truck` (xe tải), `bus` (xe buýt), `bicycle` (xe đạp).
- `is_congested`: `true` nếu giao thông bị kẹt xe thực sự (mật độ đông $\ge$ 10 xe và vận tốc trung bình $< 15\text{ km/h}$).
- `avg_speed_kmh`: Vận tốc trung bình di chuyển của toàn bộ luồng xe (km/h).
- `total_crossed_tripwire`: Tổng số xe đã đi cắt qua vạch ảo từ đầu phiên chạy.
- `flow_vehicles_per_min`: Lưu lượng thông hành (số xe lướt qua vạch mỗi phút - Flow VPM).
- `has_water`: `true` nếu phát hiện vũng nước / mặt đường trơn ướt phản quang.
- `alert_level`: Mức cảnh báo: `"INFO"` (bình thường), `"WARNING"` (ùn tắc/nước đọng), `"CRITICAL"` (ngập sâu và tê liệt).

---

## 3. Phân Đoạn Nước & Đo Độ Sâu Ngập (`flood_detector`)

Module ứng dụng mạng Deep Learning LinkNet (EfficientNet-B4) bóc tách toàn bộ diện tích ngập nước và ước lượng độ sâu ngập (cm) dựa trên mốc vật thể ngập nước.

### 3.1. Cách chạy (Command)

```bash
# Chạy nhận diện ngập và ước lượng độ sâu từ 1 ảnh
python -m flood_detector.main --image images/high_1.png

# Xem trước ảnh overlay trực tiếp trên màn hình
python -m flood_detector.main --image images/high_1.png --preview

# Chỉ định thư mục lưu kết quả tùy ý
python -m flood_detector.main --image images/low_1.png --out-dir my_custom_folder/ --preview
```

**Bảng tham số CLI:**
- `--image` hoặc `--source`: *(Bắt buộc)* Đường dẫn tới ảnh cần phân tích (`.jpg`, `.png`).
- `--model-path`: Đường dẫn file weights LinkNet (Mặc định tự tìm trong `weights/link_efficientb4_model.pth`).
- `--out-dir`: Thư mục lưu kết quả (Mặc định: `flood_detector/outputs`).
- `--preview`: Mở giao diện xem trực tiếp.

---

### 3.2. Cách input
- **Định dạng file:** Ảnh tĩnh `.jpg`, `.png`, `.jpeg`.
- **Ví dụ đường dẫn:** `--image images/high_1.png` hoặc `--image D:/floods/quan8_ngap.jpg`.

---

### 3.3. Output lưu ở đâu?
Tất cả kết quả tự động xuất vào: **`flood_detector/outputs/`**
- Ảnh mask nước nhị phân: `<source_id>_water_mask.png`
- Ảnh overlay HUD trực quan: `<source_id>_water_overlay.jpg`
- Tệp JSON chỉ số ngập và độ sâu: `<source_id>_flood_result.json`

---

### 3.4. Định dạng Output chi tiết

#### A. File ảnh Mask nước (`*_water_mask.png`):
- Ảnh thang xám 1 kênh (Grayscale uint8):
  - **Giá trị 255 (Trắng):** Vùng có mặt nước ngập.
  - **Giá trị 0 (Đen):** Vùng cạn / khô ráo.

#### B. File ảnh trực quan HUD (`*_water_overlay.jpg`):
- Mặt nước ngập được phủ lớp màu xanh ngọc (Cyan neon) bán trong suốt.
- Đường bao viền ranh giới mặt nước màu vàng/xanh neon.
- Bảng thông số HUD trên góc ảnh hiển thị cấp độ ngập, độ sâu ước lượng (cm) và khuyến nghị an toàn.

#### C. File JSON kết quả (`*_flood_result.json`):
```json
{
  "image_source": "high_1.png",
  "resolution": [1280, 720],
  "water_detected": true,
  "water_area_ratio": 0.3462,
  "water_area_percentage": 34.62,
  "estimated_depth_cm": 42.5,
  "depth_range_cm": [35.0, 70.0],
  "severity_level": "LEVEL_3_DEEP",
  "severity_code": 3,
  "severity_title": "Mức 3: Ngập sâu (Quá ống xả / Ngập bánh xe ô tô)",
  "safety_action": "Xe máy tê liệt hoàn toàn, nguy cơ thủy kích ô tô rất cao. Cấm ô tô con và xe máy qua lại.",
  "reference_source": "VEHICLE_SUBMERSION",
  "submersion_analysis": "Phat hien 2 phuong tien bi ngap tren 50% banh xe.",
  "processing_time_ms": 185.4,
  "output_mask_path": "flood_detector/outputs/high_1_water_mask.png",
  "output_overlay_path": "flood_detector/outputs/high_1_water_overlay.jpg",
  "output_json_path": "flood_detector/outputs/high_1_flood_result.json"
}
```

**Thang 5 cấp độ cảnh báo ngập (`severity_code`):**
- **0 - LEVEL_0_DRY** ($0 - 5\text{ cm}$): Khô ráo, lưu thông an toàn.
- **1 - LEVEL_1_SHALLOW** ($5 - 15\text{ cm}$): Nước xâm xấp mắt cá chân. Xe máy giảm tốc độ.
- **2 - LEVEL_2_MODERATE** ($15 - 35\text{ cm}$): Ngập nửa bánh xe máy, chạm gầm ô tô. Ô tô gầm thấp hạn chế đi.
- **3 - LEVEL_3_DEEP** ($35 - 70\text{ cm}$): Vượt ống xả xe máy, ngập bánh ô tô con. Cấm xe máy và ô tô con.
- **4 - LEVEL_4_CRITICAL** ($> 70\text{ cm}$): Ngập nắp capo, ngang hông người. Cần cứu hộ khẩn cấp.

---

## 4. Pipeline Suy Luận Mức Độ Ngập Mặt Đường (`flood_reasoning.py`)

Đây là **Engine suy luận tích hợp mạnh nhất**: Kết hợp Road Mask từ Module 1 và Water Mask từ Module 3 để tính diện tích giao thoa lòng đường bị ngập ($M_{road} \cap M_{water}$), phân loại thành 3 mức ngập chuẩn phục vụ cảnh báo giao thông đô thị.

### 4.1. Cách chạy (Command)

#### Cách 1: [Tiện lợi nhất] Tự động trọn gói 1 lệnh (`--auto-run-road`)
Nếu ảnh chưa từng chạy qua phân đoạn đường, lệnh này sẽ tự động gọi `road_segmentation` trước rồi tự động tính toán suy luận ngập ngay sau đó:
```bash
python flood_reasoning.py --image images/high_2.png --auto-run-road --preview
```

#### Cách 2: Quy trình 2 bước tiêu chuẩn (Tối ưu cho camera CCTV cố định)
Đối với camera cố định, mặt đường chỉ cần phân đoạn 1 lần duy nhất:
```bash
# Bước 1: Tạo Road Mask cho góc camera đó (chỉ chạy 1 lần)
python -m road_segmentation.main --source images/high_2.png

# Bước 2: Chạy suy luận ngập (tự động nhận diện output của Bước 1)
python flood_reasoning.py --image images/high_2.png --preview
```

#### Cách 3: Nạp file Road Mask hoặc file JSON đa giác tùy chọn
```bash
# Nạp file Mask PNG có sẵn
python flood_reasoning.py --image images/no_1.png --road-output road_segmentation/outputs/no_1_road_mask.png

# Nạp file JSON đa giác có sẵn
python flood_reasoning.py --image images/no_1.png --road-output road_segmentation/outputs/no_1_segmentation.json
```

**Bảng tham số CLI:**
- `--image` hoặc `--source`: *(Bắt buộc)* Đường dẫn ảnh cần phân tích (`.jpg`, `.png`).
- `--road-output`: Đường dẫn tới file mask `.png` hoặc file `.json` của mặt đường đã phân đoạn từ trước.
- `--auto-run-road`: Cờ tự động kích hoạt Module `road_segmentation` nếu chưa tìm thấy kết quả của ảnh này.
- `--out-dir`: Thư mục lưu kết quả suy luận (Mặc định: `outputs/`).
- `--thresh-minor`: Ngưỡng bắt đầu tính là "Có ít" (Mặc định: `0.05` = 5% lòng đường).
- `--thresh-full`: Ngưỡng bắt đầu tính là "Ngập toàn bộ" (Mặc định: `0.50` = 50% lòng đường).
- `--preview`: Mở cửa sổ trực tiếp xem ảnh overlay kết quả.

---

### 4.2. Cách input
- File ảnh đường phố: `.jpg`, `.png`.
- Tùy chọn nạp kèm file `*_road_mask.png` hoặc `*_segmentation.json`.

---

### 4.3. Output lưu ở đâu?
Tất cả kết quả lưu tại thư mục gốc: **`outputs/`**
- Mask toàn bộ nước trong khung hình: `outputs/<source_id>_flood_mask.png`
- Mask chỉ riêng phần nước ngập trên lòng đường: `outputs/<source_id>_flooded_road_mask.png`
- Ảnh trực quan hóa 3 vùng màu: `outputs/<source_id>_flood_reasoning_overlay.jpg`
- Báo cáo tổng hợp JSON: `outputs/<source_id>_flood_reasoning.json`

---

### 4.4. Định dạng Output chi tiết

#### A. File ảnh Mask giao thoa (`outputs/*_flooded_road_mask.png`):
- Mask nhị phân biểu diễn $M_{road} \cap M_{water}$.
- **Trắng (255):** Điểm nước ngập nằm trực tiếp trên mặt đường gây nguy cơ cho xe cộ.
- **Đen (0):** Phần đường khô ráo hoặc nước nằm ngoài lề đường (ao hồ, mương rãnh).

#### B. File ảnh trực quan 3 vùng màu (`outputs/*_flood_reasoning_overlay.jpg`):
- 🟢 **Màu xanh lá:** Lòng đường khô ráo, an toàn di chuyển.
- 🔵 **Màu xanh dương:** Nước nằm ngoài phạm vi đường (hồ nước, rãnh xả, cây cỏ ven đường).
- 🔴 **Màu đỏ cam viền vàng neon:** Vùng nước ngập trực tiếp trên lòng đường.
- ⬛ **Bảng điều khiển HUD:** Thể hiện cấp độ ngập, tỷ lệ % ngập và chú thích màu sắc.

#### C. File JSON tổng hợp (`outputs/*_flood_reasoning.json`):
```json
{
  "image_source": "high_2.png",
  "resolution": [1280, 720],
  "flood_level": "Ngập toàn bộ",
  "level_code": 2,
  "flooded_road_percentage": 91.08,
  "road_segment_source": "road_segmentation/outputs/high_2_segmentation.json",
  "road_area_percentage": 52.19,
  "water_area_percentage": 54.49,
  "description": "Ngập toàn bộ: Toàn bộ hoặc phần lớn mặt đường bị ngập sâu (91.1% lòng đường). Nguy cơ chết máy rất cao.",
  "processing_time_ms": 462.9,
  "outputs": {
    "flood_mask": "outputs/high_2_flood_mask.png",
    "flooded_road_mask": "outputs/high_2_flooded_road_mask.png",
    "reasoning_overlay": "outputs/high_2_flood_reasoning_overlay.jpg",
    "reasoning_json": "outputs/high_2_flood_reasoning.json"
  }
}
```

**Quy chuẩn 3 mức độ ngập (`level_code`):**
- 🟢 **0 - "Không có":** Tỷ lệ lòng đường bị ngập $< 5\%$. Đường khô ráo, các phương tiện lưu thông an toàn.
- 🟡 **1 - "Có ít":** Tỷ lệ lòng đường bị ngập từ $5\%$ đến $50\%$. Nước ngập một phần mặt đường, các xe cần chú ý giảm tốc tránh trơn trượt hoặc nước vào bô.
- 🔴 **2 - "Ngập toàn bộ":** Tỷ lệ lòng đường bị ngập $\ge 50\%$. Phần lớn hoặc toàn bộ lòng đường ngập sâu, nguy cơ chết máy hoặc sự cố rất cao.

---

## 5. Dịch Vụ AI Camera Hợp Nhất 5 Tầng (`camera_ai` - Contract v1)

Module tích hợp toàn diện theo tài liệu SDD/SRD SE, chạy dưới dạng microservice FastAPI độc lập (hoặc CLI), xử lý cả **ảnh Thumbnail tĩnh (1 frame snapshot)** và **video stream liên tục**.

### 5.1. Bảng Chi Tiết Input & Output Của Từng Nhánh

| Nhánh / Thành Phần | Chức Năng Cốt Lõi | Dữ Liệu Đầu Vào (Input) | Dữ Liệu Đầu Ra (Output) | Quy Tắc Phán Quyết |
| :--- | :--- | :--- | :--- | :--- |
| **Nhánh 0: Quality Check Gate** | Kiểm tra ảnh lỗi, mờ tịt, ban đêm tối om, che khuất | • Ảnh Thumbnail (`np.ndarray`)<br>• `camera_id` | • `is_low_quality` (boolean)<br>• `mean_brightness`<br>• `laplacian_var` | Nếu Độ sáng $< 18$ hoặc Laplacian $< 30$ $\rightarrow$ Gán thẳng **Flood = `UNKNOWN`**, **Traffic = `UNKNOWN`**. |
| **Nhánh 1: Road Segmentation** (`RoadMaskManager`) | Tách lòng đường, lưu cache vĩnh viễn tái sử dụng 0ms | • Frame chuẩn / snapshot<br>• `camera_id` | • `road_mask` (uint8 0/255)<br>• `sidewalk_mask`<br>• `is_cached` (bool)<br>• `road_ratio` (float) | Đọc file cache `.png` nếu có sẵn (0ms). Nếu chưa có, tự động chạy `TwinLiteNet` để trích xuất và lưu cache. |
| **Nhánh 2A: Flood Reasoning** (`FloodReasoner`) | Xác định có nước trên lòng đường không & diện tích nước | • Frame mới (`np.ndarray`)<br>• `road_mask` từ Nhánh 1 | • `water_mask` (uint8)<br>• `road_water_ratio` (0-1)<br>• `is_flooded` (bool)<br>• `severity` (none/minor/mod/sev)<br>• `reason` (text) | Kết hợp CV lọc vùng phản chiếu ánh sáng gương nước + Gemini Flash VLM reasoning theo schema JSON. |
| **Nhánh 2B: Depth Estimator** (`WaterDepthEstimator`) | Ước lượng độ sâu chìm bánh xe máy & ô tô | • Frame mới<br>• `water_mask`<br>• Bounding boxes các xe | • `water_depth_cm` (cm hoặc null)<br>• `depth_confidence` (0-1)<br>• `safe_for_bikes` (bool)<br>• `depth_method` | Đo tỷ lệ chìm bánh xe/gầm xe (automotive submersion). Nếu không có xe, dùng ước lượng hình học (geometric coverage). |
| **Nhánh 3A: Vehicle Detector** (`VehicleDetector`) | Nhận diện & đếm số lượng xe trên lòng đường | • Frame mới<br>• `road_mask` | • `total_vehicles` (int)<br>• `vehicle_breakdown` (dict)<br>• `boxes` ([x1,y1,x2,y2]) | YOLOv8 nhận diện 5 lớp phương tiện Việt Nam (xe máy, ô tô, buýt, tải, xe đạp), chỉ lấy xe nằm trong lòng đường. |
| **Nhánh 3B: Road Occupancy** *(Đặc thù Thumbnail)* | Đo tỷ lệ chiếm dụng lòng đường trên 1 ảnh tĩnh | • `boxes` các xe trên đường<br>• Diện tích `road_mask` | • `road_occupancy_ratio` (0.0 - 1.0) | $\text{Occupancy} = \frac{\sum \text{Diện tích bbox xe}}{\text{Diện tích Road Mask}}$. Dùng để phát hiện kẹt xe khi chỉ có 1 ảnh snapshot. |
| **Nhánh 3C: Speed & Tripwire** *(Đặc thù Video)* | Đo vận tốc di chuyển & lưu lượng xe vượt vạch | • Chuỗi frame liên tục<br>• Timestamp video | • `avg_speed_kmh`<br>• `stopped_vehicles`<br>• `slow_vehicles`<br>• `flow_vpm` | Theo dõi quỹ đạo (tracking), tính $\Delta x / \Delta t$ để ra vận tốc thực tế của từng xe và phát hiện kẹt xe cứng. |
| **Nhánh 4: Result Fusion Gate** (`CameraResultFusion`) | Hợp nhất kết quả thành hợp đồng chuẩn & bằng chứng | • Đầu ra Nhánh 0, 1, 2, 3<br>• `camera_id`, `source_revision` | • `CameraObservationEvent` (Contract v1)<br>• `CameraCurrentState`<br>• Ảnh bằng chứng HUD | Tổng hợp theo 4 mức Ngập và 4 mức Tắc chuẩn, vẽ banner HUD, ghi đè state mới nhất vào bộ nhớ/DB. |

---

### 5.2. Chuẩn Hóa 4 Trạng Thái Ngập & 4 Trạng Thái Tắc

#### 🌊 Trạng thái Ngập (`flood_status_vn`):
1. **`UNKNOWN`**: Ảnh lỗi, quá tối, mờ tịt hoặc độ tin cậy mô hình $< 0.45$.
2. **`Không ngập`**: Mặt đường khô ráo, tỷ lệ nước trên đường $\text{road\_water\_ratio} < 3.5\%$, độ sâu = 0 cm.
3. **`Ngập đi được`**: Nước ngập xăm xắp mắt cá chân / dưới nửa bánh xe máy ($< 20\text{ cm}$), xe máy và ô tô vẫn qua được an toàn (`safe_for_bikes = True`).
4. **`Ngập nhiều`**: Nước ngập sâu ($> 20 - 25\text{ cm}$), ngập quá nửa bánh xe máy / lốc máy / pô xe, nguy hiểm, xe dễ chết máy (`safe_for_bikes = False`).

#### 🚦 Trạng thái Tắc đường (`traffic_status`):
1. **`UNKNOWN`**: Ảnh lỗi, mờ hoặc góc camera không thấy đường.
2. **`CLEAR`**: Đường thông thoáng, tỷ lệ chiếm dụng $\text{Occupancy} < 14\%$, số lượng xe ít ($< 8$ xe).
3. **`CROWDED`**: Đường đông đúc, $\text{Occupancy} \in [14\%, 45\%]$ hoặc $\ge 8$ xe, các xe vẫn di chuyển bình thường.
4. **`HEAVY_CONGESTION`**: Tắc đường / kẹt xe nghiêm trọng, xe ken đặc $\text{Occupancy} \ge 45\%$ hoặc $\ge 20$ xe dồn ứ, hoặc xe dừng hẳn.

---

### 5.3. Cách Chạy Module Hợp Nhất

```bash
# 1. Khởi động REST API Server (port 8000)
python -m camera_ai.main --serve --port 8000

# 2. Phân tích 1 ảnh thumbnail snapshot
python -m camera_ai.main --camera-id CAM_TEST_01 --image data_test/sample_day.jpg

# 3. Phân tích video stream
python -m camera_ai.main --camera-id CAM_TEST_01 --video data_test/day_1.mp4 --fps 5
```

---

## 6. Bảng Tổng Hợp Vị Trí File Output Toàn Dự Án

```
AI-For-Impact/
│
├── road_segmentation/outputs/
│   ├── <id>_segmented.jpg                   # Ảnh trực quan lòng đường & vỉa hè
│   ├── <id>_road_mask.png                   # Mặt nạ nhị phân lòng đường
│   └── <id>_segmentation.json               # JSON tọa độ polygon & diện tích đường
│
├── traffic_counter/outputs/
│   ├── <id>_traffic.jpg                     # Snapshot xe, bounding box & HUD
│   └── <id>_traffic.json                    # JSON đếm xe, vận tốc & kẹt xe
│
├── events_outbox/
│   ├── telemetry_<cam_id>_<event_id>.json   # Bản tin định kỳ bình thường
│   └── incident_<cam_id>_<event_id>.json    # Bản tin sự cố kẹt xe / mặt đường có nước
│
├── evidence_captures/
│   └── evidence_<cam_id>_<event_id>.jpg     # Ảnh bằng chứng khi xảy ra sự cố
│
├── flood_detector/outputs/
│   ├── <id>_water_mask.png                  # Mặt nạ nhị phân mặt nước ngập
│   ├── <id>_water_overlay.jpg               # Ảnh overlay neon & HUD độ sâu
│   └── <id>_flood_result.json               # JSON độ sâu (cm) & 5 cấp độ ngập
│
└── outputs/
    ├── <id>_flood_mask.png                  # Toàn bộ mặt nước trong ảnh
    ├── <id>_flooded_road_mask.png           # Chỉ riêng nước ngập trên lòng đường
    ├── <id>_flood_reasoning_overlay.jpg     # Ảnh trực quan 3 màu (Đường khô, Nước ngoài, Nước ngập)
    └── <id>_flood_reasoning.json            # JSON kết luận mức ngập (Không có / Có ít / Ngập toàn bộ)
```
