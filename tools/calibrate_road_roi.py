import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional
import cv2
import numpy as np

# Ensure root workspace is in sys.path
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = WORKSPACE_DIR / "road_segmentation" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FLOOD_CAMERAS = [
    ("CAM_FLOOD_G0", "data_test/flood_g0_0133_a41b3e13.mp4", "Camera G0: Nga tu pho ven bien"),
    ("CAM_FLOOD_G1", "data_test/flood_g1_0282_ac68cb9b.mp4", "Camera G1: Pho giua 2 toa nha cao tang"),
    ("CAM_FLOOD_G2", "data_test/flood_g2_0190_3e225b42.mp4", "Camera G2: Dai lo ven song / palm promenade"),
    ("CAM_FLOOD_G3", "data_test/flood_g3_0040_e5b38c34.mp4", "Camera G3: Duong dem ngap sau un tac"),
]


def extract_first_frame(source_path: str) -> Tuple[np.ndarray, int, int]:
    p = Path(source_path)
    if not p.is_absolute():
        p = WORKSPACE_DIR / source_path
    if not p.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    ext = p.suffix.lower()
    if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
        frame = cv2.imread(str(p))
    else:
        cap = cv2.VideoCapture(str(p))
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            raise RuntimeError(f"Could not read frame from video: {source_path}")

    h, w = frame.shape[:2]
    return frame, w, h


def load_existing_roi(camera_id: str) -> List[List[List[int]]]:
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"
    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("road_polygons", [])
        except Exception:
            pass
    return []


def save_calibrated_roi(
    camera_id: str,
    image_shape: Tuple[int, int],
    polygons: List[List[List[int]]],
    curb_buffer_px: int = 35
):
    h, w = image_shape
    road_mask = np.zeros((h, w), dtype=np.uint8)

    for poly in polygons:
        poly_np = np.array(poly, dtype=np.int32)
        cv2.fillPoly(road_mask, [poly_np], 255)

    # Generate sidewalk curb strip (buffer zone right outside road polygon)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (curb_buffer_px, curb_buffer_px))
    dilated = cv2.dilate(road_mask, kernel)
    sidewalk_mask = cv2.subtract(dilated, road_mask)

    # Exclude viewport borders
    sidewalk_mask[h - 10:, :] = 0

    mask_file = CACHE_DIR / f"{camera_id}_road_mask.png"
    side_file = CACHE_DIR / f"{camera_id}_sidewalk_mask.png"
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"

    cv2.imwrite(str(mask_file), road_mask)
    cv2.imwrite(str(side_file), sidewalk_mask)

    roi_data = {
        "source_id": camera_id,
        "image_size": [w, h],
        "road_polygons": polygons,
        "manual_calibration": True,
        "curb_buffer_px": curb_buffer_px
    }

    with open(roi_file, "w", encoding="utf-8") as f:
        json.dump(roi_data, f, indent=2)

    print(f"\n[+] Da luu calibration cho camera '{camera_id}':")
    print(f"    - Road Mask: {mask_file.resolve()}")
    print(f"    - Sidewalk Mask: {side_file.resolve()}")
    print(f"    - ROI JSON: {roi_file.resolve()}")


def interactive_roi_editor(
    source_path: str,
    camera_id: str,
    cam_index: int = 1,
    total_cams: int = 1,
    description: str = ""
) -> bool:
    """
    Giao diện tương tác vẽ polygon mặt đường cho camera:
    - Click chuột trái: Thêm điểm đỉnh polygon
    - Click chuột phải: Undo điểm vừa thêm
    - Phím 'C': Đóng đa giác hiện tại
    - Phím 'R': Xoá toàn bộ điểm để vẽ lại từ đầu
    - Phím 'S': Lưu polygon camera này và tiếp tục
    - Phím 'Q' / ESC: Bỏ qua / thoát
    """
    frame, w, h = extract_first_frame(source_path)
    window_name = f"SafeRoute Road Calibrator - [{cam_index}/{total_cams}] {camera_id}"

    # Load existing polygon if available as starting reference
    existing = load_existing_roi(camera_id)
    polygons: List[List[List[int]]] = [p for p in existing] if existing else []
    current_poly: List[List[int]] = []

    def mouse_callback(event, x, y, flags, param):
        nonlocal current_poly
        if event == cv2.EVENT_LBUTTONDOWN:
            current_poly.append([int(x), int(y)])
        elif event == cv2.EVENT_RBUTTONDOWN:
            if current_poly:
                current_poly.pop()

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, min(1280, w), min(720, h))
    cv2.setMouseCallback(window_name, mouse_callback)

    saved = False

    while True:
        display = frame.copy()

        # 1. Draw completed polygons in transparent blue/green
        for p in polygons:
            p_arr = np.array(p, dtype=np.int32)
            cv2.polylines(display, [p_arr], isClosed=True, color=(0, 255, 0), thickness=2)
            mask_poly = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(mask_poly, [p_arr], 255)
            display[mask_poly > 0] = cv2.addWeighted(
                display[mask_poly > 0], 0.6,
                np.full_like(display[mask_poly > 0], (0, 255, 120)), 0.4, 0
            )

        # 2. Draw current active polygon points and lines in yellow/orange
        if len(current_poly) > 0:
            for pt in current_poly:
                cv2.circle(display, tuple(pt), 5, (0, 255, 255), -1)
            if len(current_poly) > 1:
                cv2.polylines(
                    display, [np.array(current_poly, dtype=np.int32)],
                    isClosed=False, color=(0, 165, 255), thickness=2
                )

        # 3. Instruction Top Banner
        cv2.rectangle(display, (0, 0), (w, 55), (20, 20, 20), -1)
        cv2.putText(
            display,
            f"[{cam_index}/{total_cams}] CAMERA: {camera_id} ({description})",
            (15, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2,
            cv2.LINE_AA
        )
        cv2.putText(
            display,
            "CHUOT TRAI: Cham diem | CHUOT PHAI: Undo | PHIM C: Dong da giac | PHIM S: LUU & TIEP TUC | PHIM R: Ve lai tu dau | PHIM Q: Thoat",
            (15, 46),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
            cv2.LINE_AA
        )

        cv2.imshow(window_name, display)
        key = cv2.waitKey(20) & 0xFF

        if key in [ord('c'), ord('C')]:
            if len(current_poly) >= 3:
                polygons.append(current_poly)
                current_poly = []
                print(f"[+] Da dong da giac ({len(polygons[-1])} diem). Nhan 'S' de Luu.")
        elif key in [ord('r'), ord('R')]:
            polygons = []
            current_poly = []
            print("[*] Da reset ve lai tu dau.")
        elif key in [ord('s'), ord('S')]:
            if len(current_poly) >= 3:
                polygons.append(current_poly)
                current_poly = []
            if polygons:
                save_calibrated_roi(camera_id, (h, w), polygons)
                saved = True
                break
            else:
                print("[!] Vui long cham cac diem va bam 'C' de dong polygon truoc khi 'S' (Luu)!")
        elif key in [ord('q'), ord('Q'), 27]:
            print(f"[*] Bo qua camera {camera_id}.")
            break

    cv2.destroyAllWindows()
    return saved


def run_batch_floods(auto_run_after: bool = True):
    print("=" * 80)
    print("   SAFEROUTE AI - CONG CU VE POLYGON MAT DUONG CHO 4 CAMERA NGAP (G0 -> G3)")
    print("=" * 80)
    print("Huong dan:")
    print("  1. Cua so anh cua tung camera se lan luot hien len tren man hinh.")
    print("  2. Dung CHUOT TRAI click xung quanh long duong xe chay.")
    print("  3. Nhan phim 'C' de dong da giac.")
    print("  4. Nhan phim 'S' de luu va chuyen sang camera tiep theo.")
    print("  5. Neu muon ve lai camera do: nhan phim 'R'.")
    print("=" * 80)

    total = len(FLOOD_CAMERAS)
    for idx, (cam_id, vpath, desc) in enumerate(FLOOD_CAMERAS):
        print(f"\n>> Dang mo [{idx + 1}/{total}] {cam_id}...")
        interactive_roi_editor(
            source_path=vpath,
            camera_id=cam_id,
            cam_index=idx + 1,
            total_cams=total,
            description=desc
        )

    print("\n[+] Hoan tat calibrate cho cac camera!")

    if auto_run_after:
        print("\n[*] Dang tu dong chay lai bo test kiem thu toan dien 4 video ngap...")
        cmd = [sys.executable, "run_comprehensive_test.py", "--mode", "floods", "--fps", "8.0"]
        subprocess.run(cmd, cwd=str(WORKSPACE_DIR))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool calibrate va pre-segment road ROI cho camera co dinh")
    parser.add_argument("--source", type=str, default=None, help="Video hoac anh mau cua camera")
    parser.add_argument("--cam", type=str, default=None, help="ID camera (VD: CAM_FLOOD_G0)")
    parser.add_argument("--all-floods", action="store_true", help="Calibrate lan luot ca 4 camera G0 -> G3")
    parser.add_argument("--no-auto-run", action="store_true", help="Khong tu dong chay lai test sau khi ve xong")
    args = parser.parse_args()

    if args.all_floods or (args.source is None and args.cam is None):
        run_batch_floods(auto_run_after=not args.no_auto_run)
    else:
        interactive_roi_editor(args.source, args.cam)
