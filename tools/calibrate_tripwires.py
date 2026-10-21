import os
import sys
import json
import argparse
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
import cv2
import numpy as np

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = WORKSPACE_DIR / "road_segmentation" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FLOOD_CAMERAS = [
    ("CAM_FLOOD_G0", "data_test/flood_g0_0133_a41b3e13.mp4", "G0: Nga tu pho ven bien (Co the ve 1 hoac 2 duong dem cho cac nhanh)"),
    ("CAM_FLOOD_G1", "data_test/flood_g1_0282_ac68cb9b.mp4", "G1: Pho giua 2 toa nha (1 duong cat ngang long duong)"),
    ("CAM_FLOOD_G2", "data_test/flood_g2_0190_3e225b42.mp4", "G2: Dai lo ven song (Ve duong cat ngang lan xe chay)"),
    ("CAM_FLOOD_G3", "data_test/flood_g3_0040_e5b38c34.mp4", "G3: Duong dem ngap sau (Ve duong cat ngang hang xe chay)"),
]

LINE_COLORS = [
    (0, 255, 255),   # Yellow
    (0, 165, 255),   # Orange
    (255, 0, 255),   # Magenta
    (0, 255, 0),     # Lime Green
    (255, 215, 0),   # Cyan/Gold
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


def load_road_mask(camera_id: str) -> Optional[np.ndarray]:
    mask_file = CACHE_DIR / f"{camera_id}_road_mask.png"
    if mask_file.exists():
        return cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
    return None


def load_existing_tripwires(camera_id: str) -> List[Dict[str, Any]]:
    tw_file = CACHE_DIR / f"{camera_id}_tripwires.json"
    if tw_file.exists():
        try:
            with open(tw_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("tripwires", [])
        except Exception:
            pass

    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"
    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("tripwires", [])
        except Exception:
            pass
    return []


def save_tripwires(
    camera_id: str,
    image_shape: Tuple[int, int],
    tripwires: List[Dict[str, Any]]
):
    h, w = image_shape

    # 1. Save separate tripwires JSON
    tw_file = CACHE_DIR / f"{camera_id}_tripwires.json"
    tw_data = {
        "source_id": camera_id,
        "image_size": [w, h],
        "tripwires": tripwires
    }
    with open(tw_file, "w", encoding="utf-8") as f:
        json.dump(tw_data, f, indent=2, ensure_ascii=False)

    # 2. Also update road_roi.json so it's all in one place
    roi_file = CACHE_DIR / f"{camera_id}_road_roi.json"
    if roi_file.exists():
        try:
            with open(roi_file, "r", encoding="utf-8") as f:
                roi_data = json.load(f)
            roi_data["tripwires"] = tripwires
            with open(roi_file, "w", encoding="utf-8") as f:
                json.dump(roi_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Warning updating road_roi.json: {e}")

    print(f"\n[+] Da luu {len(tripwires)} duong ranh gioi dem xe cho camera '{camera_id}':")
    for tw in tripwires:
        print(f"    - [{tw['id']}] {tw['name']}: {tw['p1']} -> {tw['p2']}")
    print(f"    File: {tw_file.resolve()}")


def interactive_tripwire_editor(
    source_path: str,
    camera_id: str,
    cam_index: int = 1,
    total_cams: int = 1,
    description: str = ""
) -> bool:
    """
    Giao diện vẽ đường ranh giới ảo (Tripwire Counting Lines):
    - Click điểm 1, click điểm 2 -> Tạo 1 đường ranh giới (Nhánh 1)
    - Click điểm 3, click điểm 4 -> Tạo đường ranh giới thứ 2 (Nhánh 2 nếu có)
    - Chuột phải: Undo điểm vừa click
    - Phím 'R': Xoá các đường để vẽ lại camera này
    - Phím 'S': Lưu các đường đếm và sang camera kế tiếp
    - Phím 'Q' / ESC: Bỏ qua
    """
    frame, w, h = extract_first_frame(source_path)
    road_mask = load_road_mask(camera_id)
    window_name = f"SafeRoute Tripwire Calibrator - [{cam_index}/{total_cams}] {camera_id}"

    # Load existing tripwires if available
    existing = load_existing_tripwires(camera_id)
    tripwires: List[Dict[str, Any]] = [tw for tw in existing] if existing else []

    pending_point: Optional[List[int]] = None
    mouse_hover_pos: Optional[Tuple[int, int]] = None

    def mouse_callback(event, x, y, flags, param):
        nonlocal pending_point, mouse_hover_pos, tripwires
        mouse_hover_pos = (int(x), int(y))

        if event == cv2.EVENT_LBUTTONDOWN:
            if pending_point is None:
                pending_point = [int(x), int(y)]
            else:
                p1 = pending_point
                p2 = [int(x), int(y)]
                line_idx = len(tripwires) + 1
                tw_entry = {
                    "id": f"line_{line_idx}",
                    "name": f"Nhanh {line_idx}",
                    "p1": p1,
                    "p2": p2
                }
                tripwires.append(tw_entry)
                pending_point = None
                print(f"[+] Da tao duong dem xe #{line_idx}: {p1} -> {p2}")

        elif event == cv2.EVENT_RBUTTONDOWN:
            if pending_point is not None:
                pending_point = None
                print("[*] Undo diem dau.")
            elif tripwires:
                removed = tripwires.pop()
                print(f"[*] Da xoa duong dem xe: {removed['name']}")

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, min(1280, w), min(720, h))
    cv2.setMouseCallback(window_name, mouse_callback)

    saved = False

    while True:
        display = frame.copy()

        # 1. Overlay road mask in subtle translucent green for context
        if road_mask is not None and np.any(road_mask > 0):
            road_idx = road_mask > 0
            green_tint = np.full_like(display, (0, 180, 80))
            display[road_idx] = cv2.addWeighted(display[road_idx], 0.75, green_tint[road_idx], 0.25, 0)
            # Outline
            contours, _ = cv2.findContours(road_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(display, contours, -1, (0, 255, 100), 1, cv2.LINE_AA)

        # 2. Draw completed tripwires
        for i, tw in enumerate(tripwires):
            p1 = tuple(tw["p1"])
            p2 = tuple(tw["p2"])
            color = LINE_COLORS[i % len(LINE_COLORS)]

            # Draw thick tripwire line
            cv2.line(display, p1, p2, color, 3, cv2.LINE_AA)
            cv2.circle(display, p1, 7, (255, 255, 255), -1)
            cv2.circle(display, p1, 5, color, -1)
            cv2.circle(display, p2, 7, (255, 255, 255), -1)
            cv2.circle(display, p2, 5, color, -1)

            # Midpoint label
            mid_x = (p1[0] + p2[0]) // 2
            mid_y = (p1[1] + p2[1]) // 2
            label = f"{tw['name']} [{tw['id']}]"

            # Label badge
            (lbl_w, lbl_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            cv2.rectangle(
                display,
                (mid_x - 6, mid_y - lbl_h - 8),
                (mid_x + lbl_w + 6, mid_y + 4),
                (20, 20, 20),
                -1
            )
            cv2.rectangle(
                display,
                (mid_x - 6, mid_y - lbl_h - 8),
                (mid_x + lbl_w + 6, mid_y + 4),
                color,
                1
            )
            cv2.putText(
                display,
                label,
                (mid_x, mid_y - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
                cv2.LINE_AA
            )

        # 3. Draw active in-progress pending line
        if pending_point is not None:
            cv2.circle(display, tuple(pending_point), 7, (0, 0, 255), -1)
            if mouse_hover_pos is not None:
                cv2.line(display, tuple(pending_point), mouse_hover_pos, (0, 165, 255), 2, cv2.LINE_AA)
                cv2.circle(display, mouse_hover_pos, 5, (0, 255, 255), -1)

        # 4. Top Banner HUD
        cv2.rectangle(display, (0, 0), (w, 60), (20, 20, 20), -1)
        cv2.putText(
            display,
            f"[{cam_index}/{total_cams}] {camera_id}: VE DUONG RANH GIOI DEM XE (Tripwires: {len(tripwires)})",
            (15, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2,
            cv2.LINE_AA
        )
        status_sub = "Dang cho click diem 2..." if pending_point is not None else "Click 2 diem de tao 1 duong dem xe"
        cv2.putText(
            display,
            f"CHUOT TRAI: Chon 2 diem de tao duong | CHUOT PHAI: Undo | PHIM S: LUU & TIEP TUC | PHIM R: Xoa lam lai | PHIM Q: Thoat [{status_sub}]",
            (15, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (255, 255, 255),
            1,
            cv2.LINE_AA
        )

        cv2.imshow(window_name, display)
        key = cv2.waitKey(20) & 0xFF

        if key in [ord('s'), ord('S')]:
            if tripwires:
                save_tripwires(camera_id, (h, w), tripwires)
                saved = True
                break
            else:
                print("[!] Chua co duong dem xe nao! Hay click 2 diem tren man hinh truoc khi bam 'S'!")
        elif key in [ord('r'), ord('R')]:
            tripwires = []
            pending_point = None
            print("[*] Da xoa toan bo duong dem xe de ve lai.")
        elif key in [ord('q'), ord('Q'), 27]:
            print(f"[*] Bo qua camera {camera_id}.")
            break

    cv2.destroyAllWindows()
    return saved


def run_batch_tripwires():
    print("=" * 80)
    print("   SAFEROUTE AI - CONG CU VE DUONG RANH GIOI DEM XE (TRIPWIRE COUNTING LINES)")
    print("   Ho tro: Moi nga 1 duong, 1 anh co the ve 2 hoac nhieu duong cho cac nhanh")
    print("=" * 80)
    print("Huong dan:")
    print("  1. Anh camera se hien len voi lop phu mat duong ban da ve truoc do.")
    print("  2. Dung CHUOT TRAI click 2 diem de tao mot duong ranh gioi cat qua lan xe.")
    print("  3. Neu co nga re hoac 2 nhanh: tiep tuc click them 2 diem nua de tao duong so 2.")
    print("  4. Chuot phai de Undo neu bam lech.")
    print("  5. Nhan phim 'S' de Luu va chuyen sang camera tiep theo.")
    print("=" * 80)

    total = len(FLOOD_CAMERAS)
    for idx, (cam_id, vpath, desc) in enumerate(FLOOD_CAMERAS):
        print(f"\n>> Dang mo [{idx + 1}/{total}] {cam_id}...")
        interactive_tripwire_editor(
            source_path=vpath,
            camera_id=cam_id,
            cam_index=idx + 1,
            total_cams=total,
            description=desc
        )

    print("\n[+] Da hoan tat ve duong ranh gioi dem xe cho tat ca cac camera!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool ve duong ranh gioi dem xe (Tripwires) cho tung nhanh")
    parser.add_argument("--source", type=str, default=None, help="Video hoac anh")
    parser.add_argument("--cam", type=str, default=None, help="Camera ID")
    parser.add_argument("--all-floods", action="store_true", help="Chay lan luot 4 camera ngap G0 -> G3")
    args = parser.parse_args()

    if args.all_floods or (args.source is None and args.cam is None):
        run_batch_tripwires()
    else:
        interactive_tripwire_editor(args.source, args.cam)
