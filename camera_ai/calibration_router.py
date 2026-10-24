import os
import json
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
import cv2
import numpy as np
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse, Response

router = APIRouter()

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = WORKSPACE_DIR / "road_segmentation" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# =====================================================================
# PYDANTIC SCHEMAS
# =====================================================================

class TripwireItem(BaseModel):
    id: str = Field(..., description="ID định danh cho đường ranh giới (VD: line_1)")
    name: str = Field(..., description="Tên nhánh hoặc hướng lưu thông (VD: Nhánh rẽ trái)")
    p1: List[int] = Field(..., description="Toạ độ điểm đầu [x, y]")
    p2: List[int] = Field(..., description="Toạ độ điểm cuối [x, y]")


class RoadROIRequest(BaseModel):
    camera_id: str = Field(..., description="Mã định danh camera cố định (VD: CAM_FLOOD_G0)")
    road_polygons: List[List[List[int]]] = Field(..., description="Tập hợp các đa giác toạ độ [x, y] tạo thành lòng đường")
    image_size: Optional[List[int]] = Field(default=[1344, 768], description="Kích thước ảnh gốc [width, height]")
    curb_buffer_px: Optional[int] = Field(default=35, description="Bán kính đệm bó vỉa hè (px)")


class TripwiresRequest(BaseModel):
    camera_id: str = Field(..., description="Mã định danh camera cố định (VD: CAM_FLOOD_G0)")
    tripwires: List[TripwireItem] = Field(..., description="Danh sách các đường ranh giới đếm xe cho các ngã/nhánh")
    image_size: Optional[List[int]] = Field(default=None, description="Kích thước khung hình [width, height]")


class CalibrationOverviewResponse(BaseModel):
    camera_id: str
    has_road_roi: bool
    polygons_count: int
    road_area_ratio: float
    road_area_percentage: float
    image_size: List[int]
    has_tripwires: bool
    tripwires_count: int
    tripwires: List[TripwireItem]
    mask_files: Dict[str, bool]


# =====================================================================
# CORE LOGIC HELPERS
# =====================================================================

def _generate_and_save_masks(
    camera_id: str,
    image_size: List[int],
    road_polygons: List[List[List[int]]],
    curb_buffer_px: int = 35
) -> Tuple[float, int, int]:
    w, h = image_size[0], image_size[1]
    road_mask = np.zeros((h, w), dtype=np.uint8)

    for poly in road_polygons:
        if len(poly) >= 3:
            poly_np = np.array(poly, dtype=np.int32)
            cv2.fillPoly(road_mask, [poly_np], 255)

    # Sinh dải đệm bó vỉa hè bao quanh mép đường phục vụ đo ngập tầng 3
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (curb_buffer_px, curb_buffer_px))
    dilated = cv2.dilate(road_mask, kernel)
    sidewalk_mask = cv2.subtract(dilated, road_mask)
    sidewalk_mask[max(0, h - 10):, :] = 0

    mask_file = CACHE_DIR / f"{camera_id}_road_mask.png"
    side_file = CACHE_DIR / f"{camera_id}_sidewalk_mask.png"
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"

    cv2.imwrite(str(mask_file), road_mask)
    cv2.imwrite(str(side_file), sidewalk_mask)

    # Đọc tripwires hiện tại nếu đã có để không bị ghi đè mất
    existing_tws = []
    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as f:
                existing_tws = json.load(f).get("tripwires", [])
        except Exception:
            pass

    roi_data = {
        "source_id": camera_id,
        "image_size": [w, h],
        "road_polygons": road_polygons,
        "manual_calibration": True,
        "curb_buffer_px": curb_buffer_px,
        "tripwires": existing_tws
    }

    with open(roi_file, "w", encoding="utf-8") as f:
        json.dump(roi_data, f, indent=2, ensure_ascii=False)

    road_pixels = int(np.sum(road_mask > 0))
    total_pixels = h * w
    road_ratio = float(road_pixels) / float(total_pixels) if total_pixels > 0 else 0.0

    return road_ratio, w, h


# =====================================================================
# ROUTER ENDPOINTS
# =====================================================================

@router.post("/road-roi", tags=["Calibration: Vẽ Mặt Đường (Road ROI)"])
def set_road_roi(payload: RoadROIRequest):
    """
    Cập nhật hoặc lưu mới polygon mặt đường cho camera cố định.
    Tự động sinh mask nhị phân `road_mask.png`, `sidewalk_mask.png` và cập nhật `road_roi.json`.
    """
    if not payload.road_polygons or len(payload.road_polygons) == 0:
        raise HTTPException(status_code=400, detail="Cần cung cấp ít nhất một polygon có tối thiểu 3 điểm.")

    for idx, poly in enumerate(payload.road_polygons):
        if len(poly) < 3:
            raise HTTPException(status_code=400, detail=f"Polygon #{idx+1} phải có tối thiểu 3 toạ độ [x, y].")

    road_ratio, w, h = _generate_and_save_masks(
        camera_id=payload.camera_id,
        image_size=payload.image_size or [1344, 768],
        road_polygons=payload.road_polygons,
        curb_buffer_px=payload.curb_buffer_px or 35
    )

    return {
        "status": "success",
        "camera_id": payload.camera_id,
        "action": "road_roi_saved",
        "polygons_count": len(payload.road_polygons),
        "road_area_ratio": round(road_ratio, 4),
        "road_area_percentage": round(road_ratio * 100.0, 2),
        "image_dimensions": [w, h],
        "cache_files": {
            "road_mask": str(CACHE_DIR / f"{payload.camera_id}_road_mask.png"),
            "sidewalk_mask": str(CACHE_DIR / f"{payload.camera_id}_sidewalk_mask.png"),
            "roi_json": str(CACHE_DIR / f"{payload.camera_id}_road_roi.json"),
        }
    }


@router.get("/road-roi/{camera_id}", tags=["Calibration: Vẽ Mặt Đường (Road ROI)"])
def get_road_roi(camera_id: str):
    """
    Lấy thông tin toạ độ các polygon mặt đường đã cấu hình của camera.
    """
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"
    mask_file = CACHE_DIR / f"{camera_id}_road_mask.png"

    if not roi_file.exists():
        raise HTTPException(status_code=404, detail=f"Chưa có cấu hình Road ROI cho camera: {camera_id}")

    with open(roi_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    polygons = data.get("road_polygons", [])
    w, h = data.get("image_size", [1344, 768])
    road_area_pct = 0.0

    if mask_file.exists():
        mask = cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
        if mask is not None:
            road_area_pct = round(float(np.sum(mask > 0)) / float(mask.shape[0] * mask.shape[1]) * 100.0, 2)

    return {
        "camera_id": camera_id,
        "image_size": [w, h],
        "polygons_count": len(polygons),
        "road_area_percentage": road_area_pct,
        "road_polygons": polygons,
        "is_calibrated": True
    }


@router.delete("/road-roi/{camera_id}", tags=["Calibration: Vẽ Mặt Đường (Road ROI)"])
def clear_road_roi(camera_id: str):
    """
    Xoá cấu hình và file mask mặt đường của camera để reset hoặc vẽ lại.
    """
    files_to_remove = [
        CACHE_DIR / f"{camera_id}_road_mask.png",
        CACHE_DIR / f"{camera_id}_sidewalk_mask.png",
    ]
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"

    removed = []
    for f in files_to_remove:
        if f.exists():
            f.unlink()
            removed.append(f.name)

    # Giữ lại tripwires nếu có trong roi.json
    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as rf:
                d = json.load(rf)
            if "tripwires" in d and len(d["tripwires"]) > 0:
                d["road_polygons"] = []
                with open(roi_file, "w", encoding="utf-8") as wf:
                    json.dump(d, wf, indent=2, ensure_ascii=False)
            else:
                roi_file.unlink()
                removed.append(roi_file.name)
        except Exception:
            pass

    return {
        "status": "success",
        "camera_id": camera_id,
        "action": "road_roi_cleared",
        "removed_files": removed
    }


@router.post("/tripwires", tags=["Calibration: Kẻ Đường Đếm Xe (Tripwires)"])
def set_tripwires(payload: TripwiresRequest):
    """
    Cập nhật hoặc lưu danh sách các đường ranh giới ảo (Tripwires) đếm xe theo từng nhánh/hướng lưu thông.
    """
    if not payload.tripwires or len(payload.tripwires) == 0:
        raise HTTPException(status_code=400, detail="Cần cung cấp ít nhất một đường ranh giới (Tripwire).")

    tw_dict_list = [tw.model_dump() for tw in payload.tripwires]

    # 1. Lưu vào file {camera_id}_tripwires.json
    tw_file = CACHE_DIR / f"{payload.camera_id}_tripwires.json"
    w, h = payload.image_size or [1344, 768]
    data = {
        "source_id": payload.camera_id,
        "image_size": [w, h],
        "tripwires": tw_dict_list
    }
    with open(tw_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # 2. Cập nhật đồng bộ vào {camera_id}_road_roi.json
    roi_file = CACHE_DIR / f"{payload.camera_id}_road_roi.json"
    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as f:
                roi_data = json.load(f)
            roi_data["tripwires"] = tw_dict_list
            with open(roi_file, "w", encoding="utf-8") as f:
                json.dump(roi_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Warning updating tripwires into road_roi.json: {e}")

    return {
        "status": "success",
        "camera_id": payload.camera_id,
        "action": "tripwires_saved",
        "tripwires_count": len(payload.tripwires),
        "tripwires": tw_dict_list,
        "file_path": str(tw_file.resolve())
    }


@router.get("/tripwires/{camera_id}", tags=["Calibration: Kẻ Đường Đếm Xe (Tripwires)"])
def get_tripwires(camera_id: str):
    """
    Lấy danh sách các đường ranh giới đếm xe đã cấu hình của camera.
    """
    tw_file = CACHE_DIR / f"{camera_id}_tripwires.json"
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"

    tripwires = []
    if tw_file.exists():
        with open(tw_file, "r", encoding="utf-8") as f:
            tripwires = json.load(f).get("tripwires", [])
    elif roi_file.exists():
        with open(roi_file, "r", encoding="utf-8") as f:
            tripwires = json.load(f).get("tripwires", [])

    if not tripwires:
        raise HTTPException(status_code=404, detail=f"Chưa có đường ranh giới đếm xe cho camera: {camera_id}")

    return {
        "camera_id": camera_id,
        "tripwires_count": len(tripwires),
        "tripwires": tripwires
    }


@router.delete("/tripwires/{camera_id}", tags=["Calibration: Kẻ Đường Đếm Xe (Tripwires)"])
def clear_tripwires(camera_id: str):
    """
    Xoá toàn bộ các đường ranh giới đếm xe đã cấu hình của camera.
    """
    tw_file = CACHE_DIR / f"{camera_id}_tripwires.json"
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"

    removed = False
    if tw_file.exists():
        tw_file.unlink()
        removed = True

    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            d["tripwires"] = []
            with open(roi_file, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    return {
        "status": "success",
        "camera_id": camera_id,
        "action": "tripwires_cleared",
        "file_removed": removed
    }


@router.get("/summary/{camera_id}", response_model=CalibrationOverviewResponse, tags=["Calibration Overview"])
def get_camera_calibration_overview(camera_id: str):
    """
    Xem tổng quan toàn bộ cấu hình hiệu chuẩn (Road ROI + Tripwires) của 1 camera.
    """
    mask_file = CACHE_DIR / f"{camera_id}_road_mask.png"
    side_file = CACHE_DIR / f"{camera_id}_sidewalk_mask.png"
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"
    tw_file = CACHE_DIR / f"{camera_id}_tripwires.json"

    polygons = []
    tripwires = []
    image_size = [1344, 768]
    road_area_ratio = 0.0

    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                polygons = d.get("road_polygons", [])
                image_size = d.get("image_size", image_size)
                tripwires = [TripwireItem(**tw) for tw in d.get("tripwires", [])]
        except Exception:
            pass

    if not tripwires and tw_file.exists():
        try:
            with open(tw_file, "r", encoding="utf-8") as f:
                tripwires = [TripwireItem(**tw) for tw in json.load(f).get("tripwires", [])]
        except Exception:
            pass

    if mask_file.exists():
        mask = cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
        if mask is not None:
            road_area_ratio = float(np.sum(mask > 0)) / float(mask.shape[0] * mask.shape[1])

    return CalibrationOverviewResponse(
        camera_id=camera_id,
        has_road_roi=mask_file.exists() and len(polygons) > 0,
        polygons_count=len(polygons),
        road_area_ratio=round(road_area_ratio, 4),
        road_area_percentage=round(road_area_ratio * 100.0, 2),
        image_size=image_size,
        has_tripwires=len(tripwires) > 0,
        tripwires_count=len(tripwires),
        tripwires=tripwires,
        mask_files={
            "road_mask": mask_file.exists(),
            "sidewalk_mask": side_file.exists(),
            "road_roi_json": roi_file.exists(),
            "tripwires_json": tw_file.exists()
        }
    )


@router.get("/summary", tags=["Calibration Overview"])
def list_all_calibrations():
    """
    Liệt kê danh sách tất cả các camera đã được cấu hình trong hệ thống cùng trạng thái ROI và Tripwires.
    """
    camera_ids = set()
    for f in CACHE_DIR.glob("*_road_mask.png"):
        camera_ids.add(f.stem.replace("_road_mask", ""))
    for f in CACHE_DIR.glob("*_road_roi.json"):
        camera_ids.add(f.stem.replace("_road_roi", ""))
    for f in CACHE_DIR.glob("*_tripwires.json"):
        camera_ids.add(f.stem.replace("_tripwires", ""))

    result = []
    for cam_id in sorted(list(camera_ids)):
        try:
            overview = get_camera_calibration_overview(cam_id)
            result.append(overview.model_dump())
        except Exception:
            pass

    return {
        "total_calibrated_cameras": len(result),
        "cameras": result
    }


# =====================================================================
# FRAME EXTRACTION FOR WEB CALIBRATION TOOL
# =====================================================================

TEMP_FRAME_PATH = CACHE_DIR / "temp_calibration_frame.jpg"


@router.post("/extract-frame", tags=["Calibration Tool"])
async def extract_first_frame(
    video_file: Optional[UploadFile] = File(None),
    video_path: Optional[str] = Form(None)
):
    """
    Trích xuất frame đầu tiên từ video tải lên hoặc từ đường dẫn video trên máy để phục vụ vẽ ROI và Vạch đếm xe.
    """
    source_to_open = None
    suggested_cam_id = "CAM_NEW"

    if video_file and video_file.filename:
        # Lưu file tạm để OpenCV đọc
        temp_video = CACHE_DIR / f"temp_upload_{video_file.filename}"
        with open(temp_video, "wb") as f:
            content = await video_file.read()
            f.write(content)
        source_to_open = str(temp_video)
        suggested_cam_id = f"CAM_{Path(video_file.filename).stem.upper()}"
    elif video_path and video_path.strip():
        # Kiểm tra đường dẫn video trên máy
        v_str = video_path.strip()
        candidate = Path(v_str)
        if not candidate.is_absolute():
            candidate = WORKSPACE_DIR / v_str
        if not candidate.exists() and not v_str.startswith(("rtsp://", "http://", "https://")):
            raise HTTPException(status_code=404, detail=f"Không tìm thấy file video tại: {v_str}")
        source_to_open = str(candidate) if candidate.exists() else v_str
        suggested_cam_id = f"CAM_{Path(v_str).stem.upper()}"
    else:
        raise HTTPException(status_code=400, detail="Vui lòng tải lên tệp video hoặc cung cấp đường dẫn video.")

    cap = cv2.VideoCapture(source_to_open)
    if not cap.isOpened():
        raise HTTPException(status_code=400, detail="Không thể mở tệp video bằng OpenCV.")

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise HTTPException(status_code=400, detail="Không đọc được khung hình đầu tiên của video.")

    h, w = frame.shape[:2]
    # Lưu frame dưới dạng JPEG chất lượng cao
    cv2.imwrite(str(TEMP_FRAME_PATH), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

    return {
        "status": "success",
        "image_url": f"/api/v1/calibration/temp-frame?t={int(os.path.getmtime(TEMP_FRAME_PATH))}",
        "width": w,
        "height": h,
        "suggested_camera_id": suggested_cam_id
    }


@router.get("/temp-frame", tags=["Calibration Tool"])
def get_extracted_temp_frame():
    """Trả về ảnh frame đầu tiên vừa trích xuất để hiển thị trên Canvas trình duyệt."""
    if not TEMP_FRAME_PATH.exists():
        raise HTTPException(status_code=404, detail="Chưa có frame nào được trích xuất.")
    return FileResponse(str(TEMP_FRAME_PATH), media_type="image/jpeg")
