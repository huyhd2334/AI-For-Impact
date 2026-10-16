# AI Road Intelligence & Traffic Surveillance

Hệ thống thị giác máy tính (Computer Vision) toàn diện phục vụ quản lý giao thông và giám sát ngập lụt đô thị Việt Nam, tối ưu hóa cho camera CCTV độ phân giải cao (lên đến 4K).

Dự án được cấu trúc thành các module chuyên sâu và thư mục weights dùng chung:

```
AI_solution/
├── weights/                   # Thư mục tập trung toàn bộ trọng số mô hình AI (*.pt, *.pth)
│   ├── link_efficientb4_model.pth             # Phân đoạn mặt nước (72 MB)
│   ├── video_seg_checkpoint_20200212-001734.pth # Phân đoạn video AFB-URR (397 MB)
│   ├── level2_YouTubeVOS.pth                  # Base checkpoint YouTubeVOS (397 MB)
│   ├── best.pth / twinlite_best.pth           # TwinLiteNet phân đoạn đường (1.8 MB)
│   ├── vn_traffic_surveillance.pt             # YOLOv8 giao thông Việt Nam (44.8 MB)
│   └── yolov8n.pt                             # YOLOv8 nano đa mục đích (6.5 MB)
│
├── road_segmentation/         # Module 1: Phân đoạn mặt đường & vỉa hè (Road Segmentation)
│   ├── main.py                # Điểm khởi chạy pipeline phân đoạn
│   ├── segmenter.py           # Bộ phân đoạn đa mô hình (Mask2Former, SegFormer, TwinLiteNet)
│   ├── model.py               # Kiến trúc mạng TwinLiteNet (Realtime)
│   ├── outputs/               # Kết quả xuất tự động: ảnh segmentation + file .json
│   └── requirements.txt
│
├── traffic_counter/           # Module 2: Nhận diện, đếm lưu lượng xe & đánh giá ùn tắc (Traffic Counter)
│   ├── main.py                # Điểm khởi chạy phân tích camera giao thông
│   ├── analyzer.py            # Quản lý luồng video, tính toán vận tốc & kết luận kẹt xe
│   ├── vehicle_counter.py     # YOLOv8 Tracking, vạch ảo Tripwire & Density Heatmap
│   ├── water_detector.py      # Phát hiện mặt đường ướt / đọng nước ngập
│   ├── schemas.py             # Schema chuẩn hóa dữ liệu
│   ├── outputs/               # Kết quả xuất tự động: ảnh snapshot + file .json
│   └── requirements.txt
│
├── flood_detector/            # Module 3: Phân đoạn vùng ngập & ước lượng độ sâu ngập (Flood Detector)
│   ├── main.py                # Điểm khởi chạy CLI
│   ├── inference.py           # Pipeline phân đoạn nước (LinkNet EfficientNet-B4) & tính độ sâu
│   ├── outputs/               # Kết quả xuất tự động: ảnh mask, ảnh overlay + file .json
│   └── requirements.txt
│
├── flood_reasoning.py         # Pipeline suy luận mức độ ngập kết hợp kết quả từ 2 module
├── road_flood_analyzer.py     # Module giao thoa Road & Flood trực tiếp
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

---

## 3. Module 3: Flood Detector & Water Depth Estimation (`flood_detector/`)

Phân đoạn mặt nước ngập lụt đô thị và ước lượng mức độ sâu ngập (cm) từ một bức ảnh đơn lẻ.

### Tính năng chính:
- **Phân đoạn nước bằng Deep Learning:** Tích hợp mô hình LinkNet với backbone EfficientNet-B4 chuyên dụng cho vùng nước ngập.
- **Ước lượng độ sâu ngập đa phương pháp (Hierarchical Depth Estimation):**
  - **Mốc người lội nước (Anthropometric Submersion):** Đo mức độ ngập của cơ thể người (mắt cá chân, đầu gối, ngang hông, ngực) dựa trên tỷ lệ giải phẫu học chuẩn.
  - **Mốc phương tiện (Automotive Submersion):** Đo mức độ ngập của bánh xe, khoảng sáng gầm xe và thân ô tô con, xe tải, xe máy.
  - **Mô hình bao phủ không gian (Inundation Coverage):** Đo lường diện tích và tỷ lệ ngập nước trên toàn khung hình khi không có vật thể tham chiếu.
- **Phân cấp cảnh báo nguy cơ 5 mức:** Từ Mức 0 (khô ráo) đến Mức 4 (ngập nghiêm trọng > 70 cm), đưa ra khuyến nghị an toàn giao thông cho từng loại phương tiện.

### Output tự động (`flood_detector/outputs/`):
- `<source_id>_water_mask.png`: Ảnh mask phân đoạn nhị phân (vùng nước ngập màu trắng 255, khô ráo màu đen 0).
- `<source_id>_water_overlay.jpg`: Ảnh gốc được phủ mask màu xanh ngọc (cyan neon) trong suốt, viền ranh giới mặt nước và bảng HUD thông số độ sâu.
- `<source_id>_flood_result.json`: Toàn bộ chỉ số định lượng:
  - `water_detected`: `true` / `false`
  - `water_area_percentage`: Tỷ lệ diện tích ngập (%)
  - `estimated_depth_cm`: Độ sâu ước lượng trung bình (cm)
  - `depth_range_cm`: Khoảng độ sâu dao động `[min, max]` (cm)
  - `severity_level` & `severity_title`: Cấp độ ngập và tên mức độ
  - `reference_source`: Mốc tham chiếu đo đạc (`PERSON_SUBMERSION` / `VEHICLE_SUBMERSION` / `INUNDATION_COVERAGE`)
  - `safety_action`: Khuyến nghị an toàn giao thông

### Cách chạy:
```bash
# Chạy nhận diện và đo độ sâu từ 1 ảnh
python -m flood_detector.main --image path/to/image.jpg

# Xem trước ảnh overlay trực tiếp
python -m flood_detector.main --image path/to/image.jpg --preview
```

---

## 4. Pipeline Suy Luận Mức Độ Ngập Độc Lập (`flood_reasoning.py`)

Kiến trúc tách rời module (Decoupled Flood Reasoning Engine) được thiết kế nhằm tối ưu hiệu năng tính toán bằng cách phân tách công đoạn nhận diện mặt đường và công đoạn phân đoạn ngập lụt thành 2 bước độc lập.

### 4.1. Công dụng chính:
- **Tận dụng kết quả phân đoạn đường có sẵn:** Nhận diện và nạp Road Mask từ output của module `road_segmentation` (hỗ trợ cả ảnh nhị phân `<source_id>_road_mask.png` lẫn tự động tái tạo đa giác từ `<source_id>_segmentation.json`) mà không cần suy luận lại mặt đường.
- **Phân đoạn vùng nước ngập chuyên sâu:** Ứng dụng mô hình LinkNet với backbone EfficientNet-B4 đã huấn luyện riêng cho bối cảnh ngập lụt, kết hợp kỹ thuật lọc vùng liên thông (Connected Components) và đóng hình thái học (Morphological Closing) để loại bỏ nhiễu nước giả.
- **Tính toán giao thoa không gian ($M_{road} \cap M_{water}$):** Phân biệt chính xác giữa nước ngập trên lòng đường (gây nguy hiểm giao thông) và nước đọng ngoài lề đường, ao hồ, rãnh thoát nước.
- **Hiệu chỉnh thông minh trong tình huống ngập sâu (Edge Cases):** Khi nước ngập trắng xóa lòng đường khiến mô hình phân đoạn đường chỉ nhận diện được một phần viền lề hoặc bị mất dấu, hệ thống tự động bù trừ tỷ lệ ngập hiệu dụng (`effective_flood_ratio`) để đảm bảo không bỏ sót nguy cơ ngập nghiêm trọng.
- **Phân loại và kết luận 3 cấp độ ngập chuẩn:**
  - 🟢 **Mức 0 ("Không có"):** Tỷ lệ lòng đường bị ngập $< 5\%$ (hoặc diện tích nước toàn cảnh $< 1.5\%$). Mặt đường khô ráo, các phương tiện lưu thông an toàn.
  - 🟡 **Mức 1 ("Có ít"):** Tỷ lệ lòng đường bị ngập từ $5\%$ đến $50\%$. Nước ngập một phần mặt đường, các phương tiện cần chú ý giảm tốc độ.
  - 🔴 **Mức 2 ("Ngập toàn bộ"):** Tỷ lệ lòng đường bị ngập $\ge 50\%$. Toàn bộ hoặc phần lớn lòng đường ngập sâu, nguy cơ chết máy và sự cố rất cao.

### 4.2. Output tự động xuất ra (`outputs/`):
- `<source_id>_flood_mask.png`: Ảnh mask nhị phân thể hiện toàn bộ diện tích mặt nước trong khung hình.
- `<source_id>_flooded_road_mask.png`: Ảnh mask nhị phân chỉ riêng phần nước ngập nằm trên lòng đường.
- `<source_id>_flood_reasoning_overlay.jpg`: Ảnh trực quan hóa đa màu:
  - Vùng đường khô ráo: Phủ màu xanh lá.
  - Nước ngoài phạm vi đường: Phủ màu xanh dương.
  - Nước ngập trên lòng đường: Phủ màu đỏ cam viền vàng neon nổi bật.
  - Bảng điều khiển HUD trên góc ảnh hiển thị cấp độ ngập, tỷ lệ phần trăm và chú thích màu sắc.
- `<source_id>_flood_reasoning.json`: File dữ liệu tổng hợp phục vụ tích hợp hệ thống backend/dashboard:
  ```json
  {
    "image_source": "1.jpg",
    "resolution": [739, 415],
    "flood_level": "Ngập toàn bộ",
    "level_code": 2,
    "flooded_road_percentage": 91.08,
    "road_segment_source": "road_segmentation/outputs/1_segmentation.json",
    "road_area_percentage": 52.19,
    "water_area_percentage": 54.49,
    "description": "Ngập toàn bộ: Toàn bộ hoặc phần lớn mặt đường bị ngập sâu (91.1% lòng đường). Nguy cơ chết máy rất cao.",
    "processing_time_ms": 462.9,
    "outputs": {
      "flood_mask": "outputs/1_flood_mask.png",
      "flooded_road_mask": "outputs/1_flooded_road_mask.png",
      "reasoning_overlay": "outputs/1_flood_reasoning_overlay.jpg",
      "reasoning_json": "outputs/1_flood_reasoning.json"
    }
  }
  ```

### 4.3. Bảng tham số dòng lệnh (CLI Arguments):
| Tham số | Kiểu | Mặc định | Ý nghĩa & Mô tả |
| :--- | :---: | :---: | :--- |
| `--image` / `--source` | `str` | *(Bắt buộc)* | Đường dẫn file ảnh đầu vào (`.jpg`, `.png`,...) |
| `--road-output` | `str` | `None` | Đường dẫn trực tiếp file mask (`.png`) hoặc file json (`.json`) từ `road_segmentation` |
| `--auto-run-road` | `flag` | `False` | Tự động gọi module `road_segmentation` nếu chưa có sẵn output cho ảnh này |
| `--out-dir` | `str` | `"outputs"` | Thư mục lưu kết quả các file mask, overlay và json |
| `--thresh-minor` | `float` | `0.05` | Ngưỡng ngập xác định mức "Có ít" (0.05 tương đương 5%) |
| `--thresh-full` | `float` | `0.50` | Ngưỡng ngập xác định mức "Ngập toàn bộ" (0.50 tương đương 50%) |
| `--preview` | `flag` | `False` | Mở cửa sổ GUI xem trực tiếp ảnh overlay kết quả sau khi suy luận |

### 4.4. Hướng dẫn sử dụng:

#### Cách 1: Quy trình 2 bước tiêu chuẩn (Khuyến nghị cho pipeline batch)
```bash
# Bước 1: Chạy phân đoạn mặt đường (chỉ chạy 1 lần)
python -m road_segmentation.main --image data/demo.jpg

# Bước 2: Chạy suy luận ngập lụt (tự động khớp output của bước 1)
python flood_reasoning.py --image data/demo.jpg --preview
```

#### Cách 2: Tự động chạy trọn gói 1 lệnh (End-to-End tự động)
Nếu ảnh chưa từng qua phân đoạn đường, thêm cờ `--auto-run-road` để engine tự động gọi `road_segmentation`:
```bash
python flood_reasoning.py --image data/demo.jpg --auto-run-road --preview
```

#### Cách 3: Chỉ định đường dẫn Road Mask tùy ý
Khi bạn có sẵn file mask ảnh hoặc file JSON phân đoạn đường ở vị trí bất kỳ:
```bash
# Với file mask ảnh PNG
python flood_reasoning.py --image data/demo.jpg --road-output path/to/custom_road_mask.png

# Với file JSON đa giác
python flood_reasoning.py --image data/demo.jpg --road-output path/to/custom_segmentation.json
```

#### Cách 4: Tích hợp vào mã nguồn Python
```python
from flood_reasoning import FloodReasoningEngine

# Khởi tạo engine (tự động nạp model lên CUDA nếu khả dụng)
engine = FloodReasoningEngine()

# Thực hiện suy luận
result, overlay_img = engine.reason(
    image_path="data/demo.jpg",
    auto_run_road=True,
    out_dir="outputs"
)

print(f"Mức độ ngập: {result['flood_level']} ({result['flooded_road_percentage']}%)")
print(f"Đánh giá: {result['description']}")
```
