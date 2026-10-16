"""
Pipeline Suy luận Ngập lụt Mặt đường (Decoupled Flood Reasoning Engine)
=======================================================================
Pipeline hoạt động theo mô hình 2 công đoạn độc lập:
  - Công đoạn 1: Road Segmentation được thực hiện riêng bởi module `road_segmentation`.
                 (Lưu kết quả tại road_segmentation/outputs/<source_id>_road_mask.png
                  hoặc <source_id>_segmentation.json)
  - Công đoạn 2 (File này):
      + Nhận ảnh input -> CHỈ thực hiện Flood Segmentation.
      + Đọc kết quả Road Mask từ output của `road_segmentation` tương ứng với input đó.
      + Giao thoa 2 phần mask để SUY LUẬN mức độ ngập thành 3 mức:
          1. "Không có"      (Tỷ lệ ngập trên đường < 5%)
          2. "Có ít"         (Tỷ lệ ngập trên đường 5% - 50%)
          3. "Ngập toàn bộ"  (Tỷ lệ ngập trên đường >= 50%)
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

# Tương thích UTF-8 cho Windows console
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import cv2
import numpy as np
import torch
import torch.nn as nn
from PIL import Image
import torchvision.transforms as tf

# Tự động nạp đường dẫn project
curr_dir = Path(__file__).resolve().parent
if str(curr_dir) not in sys.path:
    sys.path.insert(0, str(curr_dir))

# Backward compatibility shims cho mô hình phân đoạn LinkNet EfficientNet
try:
    import segmentation_models_pytorch as smp
    if hasattr(smp, 'decoders') and hasattr(smp.decoders, 'linknet'):
        sys.modules.setdefault('segmentation_models_pytorch.linknet', smp.decoders.linknet)
        sys.modules.setdefault('segmentation_models_pytorch.linknet.model', smp.decoders.linknet.model)
except ImportError:
    pass

try:
    import efficientnet_pytorch.utils
    if not hasattr(efficientnet_pytorch.utils, 'Identity'):
        efficientnet_pytorch.utils.Identity = nn.Identity
except ImportError:
    pass


class FloodReasoningEngine:
    """
    Bộ phân đoạn nước và suy luận mức độ ngập dựa trên output từ Road Segmentation.
    """
    LEVEL_NONE = "Không có"
    LEVEL_MINOR = "Có ít"
    LEVEL_FULL = "Ngập toàn bộ"

    def __init__(
        self,
        flood_weights_path: Optional[str] = None,
        device: Optional[str] = None
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        print(f"[FloodReasoning] Khởi tạo mô hình Flood Segmentation trên {self.device}...")

        # Tìm trọng số mô hình Flood
        if flood_weights_path is None:
            candidates = [
                curr_dir / "weights" / "link_efficientb4_model.pth",
                Path("weights/link_efficientb4_model.pth"),
                curr_dir / "flood_detector" / "records" / "link_efficientb4_model.pth",
                curr_dir / "records" / "link_efficientb4_model.pth",
                Path("flood_detector/records/link_efficientb4_model.pth")
            ]
            for c in candidates:
                if c.exists():
                    flood_weights_path = str(c)
                    break

        if not flood_weights_path or not os.path.exists(flood_weights_path):
            raise FileNotFoundError(f"[FloodReasoning] Không tìm thấy file trọng số: {flood_weights_path}")

        try:
            self.flood_model = torch.load(flood_weights_path, weights_only=False, map_location=self.device)
            if hasattr(self.flood_model, "eval"):
                self.flood_model.eval()
            print(f"[FloodReasoning] Mô hình phân đoạn nước đã sẵn sàng!")
        except Exception as e:
            print(f"[FloodReasoning] Lỗi khi nạp mô hình Flood: {e}")
            raise e

        # Chuẩn hóa ảnh theo ImageNet (416x416)
        self.mean = torch.tensor([0.485, 0.456, 0.406])
        self.std = torch.tensor([0.229, 0.224, 0.225])
        self.transform_norm = tf.Compose([
            tf.Resize([416, 416]),
            tf.ToTensor(),
            tf.Normalize(self.mean, self.std)
        ])

    def segment_flood(self, img_bgr: np.ndarray) -> np.ndarray:
        """
        Thực hiện phân đoạn nước ngập từ ảnh input.
        Trả về mask nhị phân (0 hoặc 1) cùng kích thước ảnh gốc.
        """
        orig_h, orig_w = img_bgr.shape[:2]
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(img_rgb)

        img_tensor = self.transform_norm(img_pil).unsqueeze(0).to(self.device)

        with torch.no_grad():
            if hasattr(self.flood_model, "predict"):
                pred_tensor = self.flood_model.predict(img_tensor)
            else:
                pred_tensor = self.flood_model(img_tensor)

        pred_resize = tf.Compose([tf.Resize([orig_h, orig_w])])
        pred_upscaled = pred_resize(pred_tensor)
        raw_pred = pred_upscaled.squeeze().cpu().numpy()

        # Xử lý hình thái học và lấy vùng liên thông lớn
        pred_bin = (raw_pred > 0.5).astype(np.uint8)
        num_labels, labels = cv2.connectedComponents(pred_bin)
        if num_labels <= 1:
            return np.zeros_like(pred_bin)

        max_label = 1
        max_count = 0
        for lbl in range(1, num_labels):
            count = int(np.sum(labels == lbl))
            if count > max_count:
                max_count = count
                max_label = lbl

        water_mask = (labels == max_label).astype(np.uint8)
        threshold_count = max_count * 0.05
        for lbl in range(1, num_labels):
            if lbl != max_label and np.sum(labels == lbl) >= threshold_count:
                water_mask |= (labels == lbl).astype(np.uint8)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        water_mask = cv2.morphologyEx(water_mask, cv2.MORPH_CLOSE, kernel)
        return water_mask

    def load_road_mask_from_output(
        self,
        source_id: str,
        orig_size: Tuple[int, int],
        road_output_path: Optional[str] = None,
        auto_run_if_missing: bool = False,
        original_image_path: Optional[str] = None
    ) -> Tuple[np.ndarray, str]:
        """
        Đọc Road Mask từ output của module road_segmentation:
          1. Đọc từ file mask ảnh (<source_id>_road_mask.png)
          2. Hoặc tái tạo từ file JSON (<source_id>_segmentation.json) từ 'road_polygons'
        """
        orig_h, orig_w = orig_size

        # 1. Nếu người dùng chỉ định đường dẫn cụ thể
        if road_output_path and os.path.exists(road_output_path):
            p = Path(road_output_path)
            if p.suffix.lower() == ".json":
                mask = self._reconstruct_mask_from_json(str(p), orig_size)
                return mask, str(p)
            else:
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img.shape != (orig_h, orig_w):
                    img = cv2.resize(img, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
                return (img > 127).astype(np.uint8), str(p)

        # 2. Tự động dò tìm trong các thư mục output mặc định của road_segmentation
        candidate_paths = [
            curr_dir / "road_segmentation" / "outputs" / f"{source_id}_road_mask.png",
            curr_dir / "road_segmentation" / "outputs" / f"{source_id}_segmentation.json",
            curr_dir / "outputs" / f"{source_id}_road_mask.png",
            curr_dir / "outputs" / f"{source_id}_segmentation.json",
            curr_dir / "road_segmentation" / "cache" / f"{source_id}_road_mask.png",
            curr_dir / "cache_road_masks" / f"{source_id}_road_mask.png"
        ]

        for cand in candidate_paths:
            if cand.exists():
                if cand.suffix.lower() == ".json":
                    mask = self._reconstruct_mask_from_json(str(cand), orig_size)
                    return mask, str(cand)
                else:
                    img = cv2.imread(str(cand), cv2.IMREAD_GRAYSCALE)
                    if img.shape != (orig_h, orig_w):
                        img = cv2.resize(img, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
                    return (img > 127).astype(np.uint8), str(cand)

        # 3. Nếu chưa có output và bật cờ tự động chạy road_segmentation
        if auto_run_if_missing and original_image_path and os.path.exists(original_image_path):
            print(f"[FloodReasoning] Chưa tìm thấy output của road_segmentation cho '{source_id}'. Đang tự động gọi road_segmentation...")
            from road_segmentation.main import main as run_road_main
            old_argv = sys.argv
            try:
                sys.argv = ["main.py", "--image", original_image_path]
                from road_segmentation.segmenter import RoadSegmenter
                seg = RoadSegmenter(model_type="twinlite", device=self.device)
                img = cv2.imread(original_image_path)
                _, r_mask, _, _ = seg.segment(img, use_calibration=False)
                out_p = curr_dir / "road_segmentation" / "outputs" / f"{source_id}_road_mask.png"
                out_p.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out_p), r_mask)
                return (r_mask > 0).astype(np.uint8), f"auto_generated ({out_p})"
            finally:
                sys.argv = old_argv

        # Nếu không tìm thấy bất kỳ output nào
        err_msg = (
            f"\n[LỖI] Không tìm thấy output của road_segmentation cho ảnh '{source_id}'!\n"
            f"Vui lòng thực hiện một trong các cách sau:\n"
            f"  1. Chạy road_segmentation cho ảnh trước:\n"
            f"     python -m road_segmentation.main --image {original_image_path or source_id}\n"
            f"  2. Hoặc chỉ định trực tiếp output bằng tham số: --road-output <đường_dẫn_file_mask_hoặc_json>\n"
            f"  3. Hoặc thêm cờ '--auto-run-road' để tự động kích hoạt road_segmentation.\n"
        )
        raise FileNotFoundError(err_msg)

    def _reconstruct_mask_from_json(self, json_path: str, orig_size: Tuple[int, int]) -> np.ndarray:
        """Tái tạo mặt nạ nhị phân từ file segmentation.json (trích xuất từ polygons)."""
        orig_h, orig_w = orig_size
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        mask = np.zeros((orig_h, orig_w), dtype=np.uint8)
        polygons = data.get("road_polygons", [])
        for poly in polygons:
            pts = np.array(poly, dtype=np.int32)
            if len(pts) >= 3:
                cv2.fillPoly(mask, [pts], 1)

        print(f"[FloodReasoning] Đã tái tạo Road Mask từ {len(polygons)} polygons trong file JSON: {json_path}")
        return mask

    def reason(
        self,
        image_path: str,
        road_output_path: Optional[str] = None,
        auto_run_road: bool = False,
        thresh_minor: float = 0.05,
        thresh_full: float = 0.50,
        out_dir: str = "outputs"
    ) -> Dict[str, Any]:
        """
        Thực hiện pipeline:
          1. Segment flood trên ảnh input.
          2. Đọc road mask từ output của road_segmentation.
          3. Giao thoa 2 mask và suy luận ra 3 mức độ ngập: Không có / Có ít / Ngập toàn bộ.
        """
        t0 = time.time()
        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"Không tìm thấy file ảnh: {image_path}")

        img_bgr = cv2.imread(str(p))
        if img_bgr is None:
            raise ValueError(f"Không thể đọc file ảnh: {image_path}")

        orig_h, orig_w = img_bgr.shape[:2]
        source_id = p.stem

        # BƯỚC 1: Segment Flood trên ảnh input
        print(f"[FloodReasoning] 1. Đang thực hiện Flood Segmentation trên '{p.name}'...")
        water_mask = self.segment_flood(img_bgr)
        water_pixels = int(np.sum(water_mask == 1))
        total_pixels = orig_h * orig_w
        water_ratio_total = water_pixels / float(total_pixels)

        # BƯỚC 2: Đọc Road Segment Output từ road_segmentation
        print(f"[FloodReasoning] 2. Đang đọc Road Segment output từ road_segmentation...")
        road_mask, road_source = self.load_road_mask_from_output(
            source_id=source_id,
            orig_size=(orig_h, orig_w),
            road_output_path=road_output_path,
            auto_run_if_missing=auto_run_road,
            original_image_path=str(p)
        )
        road_pixels = int(np.sum(road_mask == 1))
        road_ratio_total = road_pixels / float(total_pixels)

        # BƯỚC 3: Tính toán phần giao thoa (Overlap: M_road ∩ M_water)
        intersection_mask = (road_mask == 1) & (water_mask == 1)
        intersection_pixels = int(np.sum(intersection_mask))

        if road_pixels > 0:
            direct_flood_ratio = intersection_pixels / float(road_pixels)
        else:
            direct_flood_ratio = 0.0

        # Xử lý trường hợp nước ngập trắng xóa lòng đường (road segment ban đầu chỉ thấy phần viền)
        if water_ratio_total >= 0.35 and direct_flood_ratio > 0.30:
            effective_flood_ratio = max(direct_flood_ratio, min(1.0, water_ratio_total * 1.5))
        elif road_pixels == 0 and water_ratio_total >= 0.30:
            effective_flood_ratio = min(1.0, water_ratio_total * 1.6)
        else:
            effective_flood_ratio = direct_flood_ratio

        # BƯỚC 4: SUY LUẬN 3 MỨC ĐỘ NGẬP
        if effective_flood_ratio < thresh_minor or water_ratio_total < 0.015:
            flood_level = self.LEVEL_NONE
            level_code = 0
            description = "Không có: Mặt đường khô ráo, không có nước ngập. Lưu thông bình thường."
            status_color = (0, 255, 0)      # Xanh lá
        elif effective_flood_ratio < thresh_full:
            flood_level = self.LEVEL_MINOR
            level_code = 1
            description = f"Có ít: Nước ngập một phần mặt đường ({effective_flood_ratio * 100:.1f}% lòng đường). Chú ý giảm tốc độ."
            status_color = (0, 215, 255)    # Vàng
        else:
            flood_level = self.LEVEL_FULL
            level_code = 2
            description = f"Ngập toàn bộ: Toàn bộ hoặc phần lớn mặt đường bị ngập sâu ({effective_flood_ratio * 100:.1f}% lòng đường). Nguy cơ chết máy rất cao."
            status_color = (0, 0, 255)      # Đỏ

        # BƯỚC 5: Tạo ảnh trực quan Overlay và lưu kết quả
        overlay_img = self._create_overlay(
            img_bgr=img_bgr,
            road_mask=road_mask,
            water_mask=water_mask,
            intersection_mask=intersection_mask,
            flood_level=flood_level,
            effective_ratio=effective_flood_ratio,
            status_color=status_color
        )

        dt_ms = (time.time() - t0) * 1000.0

        out_folder = Path(out_dir)
        out_folder.mkdir(parents=True, exist_ok=True)

        mask_water_path = out_folder / f"{source_id}_flood_mask.png"
        mask_inter_path = out_folder / f"{source_id}_flooded_road_mask.png"
        overlay_path = out_folder / f"{source_id}_flood_reasoning_overlay.jpg"
        json_path = out_folder / f"{source_id}_flood_reasoning.json"

        cv2.imwrite(str(mask_water_path), (water_mask * 255).astype(np.uint8))
        cv2.imwrite(str(mask_inter_path), (intersection_mask.astype(np.uint8) * 255))
        cv2.imwrite(str(overlay_path), overlay_img)

        result = {
            "image_source": p.name,
            "resolution": [orig_w, orig_h],
            "flood_level": flood_level,
            "level_code": level_code,
            "flooded_road_percentage": round(effective_flood_ratio * 100.0, 2),
            "road_segment_source": road_source,
            "road_area_percentage": round(road_ratio_total * 100.0, 2),
            "water_area_percentage": round(water_ratio_total * 100.0, 2),
            "description": description,
            "processing_time_ms": round(dt_ms, 1),
            "outputs": {
                "flood_mask": str(mask_water_path),
                "flooded_road_mask": str(mask_inter_path),
                "reasoning_overlay": str(overlay_path),
                "reasoning_json": str(json_path)
            }
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        return result, overlay_img

    def _create_overlay(
        self,
        img_bgr: np.ndarray,
        road_mask: np.ndarray,
        water_mask: np.ndarray,
        intersection_mask: np.ndarray,
        flood_level: str,
        effective_ratio: float,
        status_color: Tuple[int, int, int]
    ) -> np.ndarray:
        """Vẽ ảnh trực quan gồm mặt đường khô (xanh lá), nước ngoài đường (xanh dương) và nước ngập lòng đường (đỏ cam)."""
        orig_h, orig_w = img_bgr.shape[:2]
        overlay = img_bgr.copy()

        # 1. Đường khô ráo
        dry_road = (road_mask == 1) & (water_mask == 0)
        if np.any(dry_road):
            c_road = np.full_like(overlay, (0, 200, 80))
            overlay[dry_road] = cv2.addWeighted(overlay[dry_road], 0.65, c_road[dry_road], 0.35, 0)

        # 2. Nước ngoài đường
        outer_water = (water_mask == 1) & (road_mask == 0)
        if np.any(outer_water):
            c_water = np.full_like(overlay, (220, 140, 20))
            overlay[outer_water] = cv2.addWeighted(overlay[outer_water], 0.65, c_water[outer_water], 0.35, 0)

        # 3. Nước ngập trực tiếp trên mặt đường (Giao thoa)
        if np.any(intersection_mask):
            c_flood = np.full_like(overlay, (30, 40, 240))
            overlay[intersection_mask] = cv2.addWeighted(overlay[intersection_mask], 0.45, c_flood[intersection_mask], 0.55, 0)
            inter_u8 = (intersection_mask.astype(np.uint8) * 255)
            cnts, _ = cv2.findContours(inter_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, cnts, -1, (0, 255, 255), 2, cv2.LINE_AA)

        # Bảng HUD thông số
        hud_scale = max(0.60, min(1.2, orig_w / 900.0))
        hud_w = int(min(orig_w - 20, 680 * hud_scale))
        hud_h = int(120 * hud_scale)

        sub_bg = overlay[10:10 + hud_h, 10:10 + hud_w]
        black_box = np.zeros_like(sub_bg)
        overlay[10:10 + hud_h, 10:10 + hud_w] = cv2.addWeighted(sub_bg, 0.25, black_box, 0.75, 0)
        cv2.rectangle(overlay, (10, 10), (10 + hud_w, 10 + hud_h), status_color, 2)

        cv2.putText(
            overlay, f"MUC DO NGAP: {flood_level.upper()}", (20, int(35 * hud_scale) + 5),
            cv2.FONT_HERSHEY_SIMPLEX, hud_scale * 0.82, status_color, max(1, int(hud_scale * 2)), cv2.LINE_AA
        )
        cv2.putText(
            overlay, f"Ty le mat duong bi ngap: {effective_ratio * 100:.1f}%", (20, int(68 * hud_scale) + 5),
            cv2.FONT_HERSHEY_SIMPLEX, hud_scale * 0.65, (255, 255, 255), max(1, int(hud_scale * 1.8)), cv2.LINE_AA
        )
        cv2.putText(
            overlay, "[Xanh: Duong kho] | [Do: Nuoc ngap tren duong]", (20, int(98 * hud_scale) + 5),
            cv2.FONT_HERSHEY_SIMPLEX, hud_scale * 0.50, (200, 200, 200), 1, cv2.LINE_AA
        )

        return overlay


def main():
    parser = argparse.ArgumentParser(description="Pipeline Suy Luận Mức Độ Ngập từ Flood Seg & Road Seg Output")
    parser.add_argument("--image", "--source", dest="image", type=str, required=True,
                        help="Đường dẫn tới file ảnh đầu vào (.jpg, .png, ...)")
    parser.add_argument("--road-output", type=str, default=None,
                        help="Tùy chọn: Đường dẫn tới file mask (.png) hoặc file json (.json) từ road_segmentation")
    parser.add_argument("--auto-run-road", action="store_true",
                        help="Tự động gọi road_segmentation nếu chưa tìm thấy output của ảnh này")
    parser.add_argument("--out-dir", type=str, default="outputs",
                        help="Thư mục lưu các file kết quả (mặc định: outputs)")
    parser.add_argument("--thresh-minor", type=float, default=0.05,
                        help="Ngưỡng tính 'Có ít' (mặc định: 0.05 = 5%%)")
    parser.add_argument("--thresh-full", type=float, default=0.50,
                        help="Ngưỡng tính 'Ngập toàn bộ' (mặc định: 0.50 = 50%%)")
    parser.add_argument("--preview", action="store_true",
                        help="Hiển thị cửa sổ xem trực tiếp ảnh overlay")
    args = parser.parse_args()

    engine = FloodReasoningEngine()
    result, overlay_img = engine.reason(
        image_path=args.image,
        road_output_path=args.road_output,
        auto_run_road=args.auto_run_road,
        thresh_minor=args.thresh_minor,
        thresh_full=args.thresh_full,
        out_dir=args.out_dir
    )

    print("\n" + "=" * 70)
    print("      KẾT QUẢ SUY LUẬN MỨC ĐỘ NGẬP MẶT ĐƯỜNG (FLOOD REASONING)      ")
    print("=" * 70)
    print(f"  Ảnh nguồn                  : {result['image_source']} ({result['resolution'][0]}x{result['resolution'][1]})")
    print(f"  MỨC ĐỘ NGẬP                : >>> {result['flood_level'].upper()} <<<")
    print(f"  Tỷ lệ mặt đường bị ngập    : {result['flooded_road_percentage']}%")
    print(f"  Nguồn Road Segment Output  : {result['road_segment_source']}")
    print(f"  Mô tả chi tiết             : {result['description']}")
    print(f"  Diện tích đường            : {result['road_area_percentage']}% diện tích khung hình")
    print(f"  Diện tích nước trong cảnh  : {result['water_area_percentage']}% diện tích khung hình")
    print(f"  Thời gian xử lý            : {result['processing_time_ms']} ms")
    print("-" * 70)
    print("  Các file kết quả xuất ra:")
    for k, v in result["outputs"].items():
        print(f"   -> {k:20s}: {v}")
    print("=" * 70 + "\n")

    if args.preview:
        h, w = overlay_img.shape[:2]
        scale = min(1280.0 / w, 760.0 / h, 1.0)
        disp = cv2.resize(overlay_img, (int(w * scale), int(h * scale)))
        cv2.imshow("Flood Reasoning Preview - Press any key to exit", disp)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
