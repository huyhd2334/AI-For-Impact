# BÁO CÁO NÂNG CẤP HỆ THỐNG: ROAD ROI & MULTI-BRANCH TRIPWIRES ROUTER & SOTA FLOOD SEGMENTATION
**SafeRoute AI Surveillance Engine**  
*Cập nhật: 21/10/2026*

---

## 1. TỔNG QUAN CÁC CẢI TIẾN ĐÃ HOÀN TẤT

### 1.1. Khắc phục hạn chế của mô hình tự động phân đoạn mặt đường
- **Vấn đề ban đầu**: Khi camera cố định giám sát đường phố ngập nước, mô hình SegFormer tự động bị nhiễu bởi phản xạ bầu trời và bóng râm, tự ý lấn viền mặt đường lên tường nhà, bậc thềm đá, lan can hiên nhà và bãi đỗ xe.
- **Giải pháp**: 
  - Triển khai cơ chế **Pre-segmentation (Tiền phân đoạn hành lang mặt đường)** chuẩn xác cho các camera cố định.
  - Cho phép người vận hành vẽ polygon khép kín ôm sát lòng đường xe chạy thực tế.
  - Tự động sinh dải đệm bó vỉa hè (`sidewalk_mask`) để phục vụ thuật toán ước lượng độ sâu ngập tầng 3 (Curb Inundation).
  - Tái sử dụng siêu tốc không mất thời gian inference: `ROAD MASK: REUSED_CACHE (0ms)`.

### 1.2. Đường ranh giới đếm xe đa nhánh (Multi-Branch Tripwires)
- **Vấn đề ban đầu**: Hệ thống chỉ hỗ trợ 1 đường cắt ngang cố định trên toàn bộ khung hình, không hỗ trợ các ngã rẽ hoặc nhiều luồng giao thông độc lập.
- **Giải pháp**:
  - Hỗ trợ vẽ tuỳ ý $1, 2, \dots, N$ đường ranh giới ảo theo từng nhánh/hướng di chuyển.
  - Thuật toán giao cắt đoạn thẳng $O(1)$ (Segment-Segment Vector Intersection) xác định xe cắt qua vạch chính xác.
  - Đếm và hiển thị trực quan riêng biệt cho từng nhánh: `[Nhánh 1: X veh]`, `[Nhánh 2: Y veh]`,... đi kèm hiệu ứng chớp sáng tại toạ độ giao cắt.

### 1.3. Nâng cấp mô hình phân đoạn ngập nước SOTA
- **Vấn đề ban đầu**: Màng nước hiển thị bị đứt đoạn, lốm đốm lỗ chỗ như tổ ong, đồng thời báo ngập ảo trên mặt đường khô ráo (G0, G1).
- **Giải pháp**:
  - Nâng độ phân giải đầu vào LinkNet EfficientNet-B4 từ $416 \times 416$ lên $768 \times 768$ (Native aspect resolution), giữ nguyên độ sắc nét của các gợn sóng nước.
  - Áp dụng **Test-Time Augmentation (TTA)** chạy kết hợp frame gốc và frame lật ngang để triệt tiêu nhiễu giật lag.
  - Áp dụng thuật toán **Morphological Puddle Closure** (kernel elip $25 \times 25$) để hàn gắn các mảng nước đọng thành dải nước phẳng mịn, liền khối tự nhiên.
  - Đặt mô hình Deep Learning làm cổng kiểm soát (Corroborating Gate), loại bỏ hoàn toàn các chấm xanh rác trên đường khô.

---

## 2. THIẾT KẾ & ĐẶC TẢ FASTAPI CALIBRATION ROUTER

Router mới được tích hợp trực tiếp tại prefix: `/api/v1/calibration` trong `camera_ai/api.py`.

### 2.1. Nhóm API Vẽ Mặt Đường (Road ROI)
| Method | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `POST` | `/api/v1/calibration/road-roi` | Lưu tập đỉnh polygon mặt đường; tự sinh `road_mask.png`, `sidewalk_mask.png` và `road_roi.json` |
| `GET` | `/api/v1/calibration/road-roi/{camera_id}` | Lấy toạ độ polygon và tỷ lệ diện tích lòng đường đã cấu hình |
| `DELETE` | `/api/v1/calibration/road-roi/{camera_id}` | Xoá cấu hình và file mask mặt đường của camera |

#### Ví dụ Payload `POST /api/v1/calibration/road-roi`:
```json
{
  "camera_id": "CAM_FLOOD_G0",
  "road_polygons": [
    [
      [626, 350], [532, 413], [353, 495], [7, 491],
      [6, 757], [1331, 763], [1340, 459], [823, 431], [633, 349]
    ]
  ],
  "image_size": [1344, 768],
  "curb_buffer_px": 35
}
```

---

### 2.2. Nhóm API Kẻ Đường Đếm Xe (Tripwires)
| Method | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `POST` | `/api/v1/calibration/tripwires` | Cấu hình danh sách các đường ranh giới đếm xe cho các ngã/nhánh |
| `GET` | `/api/v1/calibration/tripwires/{camera_id}` | Lấy danh sách các đường ranh giới hiện có của camera |
| `DELETE` | `/api/v1/calibration/tripwires/{camera_id}` | Xoá toàn bộ đường ranh giới của camera |

#### Ví dụ Payload `POST /api/v1/calibration/tripwires`:
```json
{
  "camera_id": "CAM_FLOOD_G0",
  "tripwires": [
    {
      "id": "line_1",
      "name": "Nhánh rẽ trái",
      "p1": [349, 495],
      "p2": [12, 751]
    },
    {
      "id": "line_2",
      "name": "Nhánh đi thẳng",
      "p1": [416, 468],
      "p2": [863, 463]
    },
    {
      "id": "line_3",
      "name": "Nhánh rẽ phải",
      "p1": [1087, 489],
      "p2": [1334, 605]
    }
  ],
  "image_size": [1344, 768]
}
```

---

### 2.3. Nhóm API Quản lý & Tổng quan (Summary)
| Method | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `GET` | `/api/v1/calibration/summary/{camera_id}` | Xem đầy đủ trạng thái Road ROI + Tripwires + Danh sách file cache của 1 camera |
| `GET` | `/api/v1/calibration/summary` | Liệt kê tất cả camera đã cấu hình trong hệ thống |

---

## 3. CÔNG CỤ VẼ TƯƠNG TÁC (DESKTOP GUI TOOLS)

1. **Vẽ vùng mặt đường (Road ROI)**:
   ```powershell
   python tools/calibrate_road_roi.py --all-floods
   ```
   - Click chuột trái để chấm các điểm polygon quanh mép đường.
   - Phím `C`: Đóng đa giác.
   - Phím `S`: Lưu và chuyển camera tiếp theo.

2. **Kẻ các đường ranh giới đếm xe (Tripwires)**:
   ```powershell
   python tools/calibrate_tripwires.py --all-floods
   ```
   - Click 2 điểm để tạo 1 đường đếm (Nhánh 1).
   - Click tiếp 2 điểm nữa để tạo đường đếm thứ hai (Nhánh 2).
   - Phím `S`: Lưu và chuyển camera tiếp theo.

---

## 4. KẾT QUẢ THỰC TẾ TRÊN 4 CAMERA NGẬP BENCHMARK

| Camera ID | Tình trạng ngập | Độ sâu mực nước | Tình trạng giao thông | Số xe quan sát | Cấu hình Tripwires | Cảnh báo xe máy |
| :--- | :--- | :---: | :---: | :---: | :--- | :---: |
| **CAM_FLOOD_G0** | 🟢 **KHÔNG NGẬP** | **0.0 cm** | CLEAR | 24 xe | **3 nhánh** (Rẽ trái, đi thẳng, rẽ phải) | ĐI ĐƯỢC |
| **CAM_FLOOD_G1** | 🟢 **KHÔNG NGẬP** | **0.0 cm** | CLEAR / CROWDED | 14 xe | **4 nhánh** (Phân làn các luồng xe) | ĐI ĐƯỢC |
| **CAM_FLOOD_G2** | 🔴 **NGẬP ĐI ĐƯỢC** | **26.4 cm** | CLEAR | 28 xe | **2 nhánh** (Cắt các làn đại lộ) | NGUY HIỂM |
| **CAM_FLOOD_G3** | 🔴 **NGẬP NHIỀU** | **62.0 cm** | CROWDED | 32 xe | **1 nhánh** (Cắt ngang hàng xe kẹt) | NGUY HIỂM |

---

## 5. TRẠNG THÁI GIT REPOSITORY
- Đã hoàn tất commit sạch: `feat(calibration): add road ROI and multi-branch tripwires router, upgrade SOTA water segmentation`
- Đã push thành công lên GitHub remote: `https://github.com/huyhd2334/AI-For-Impact.git` (`main -> main`).
