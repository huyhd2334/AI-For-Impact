import argparse
import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np

curr_dir = Path(__file__).resolve().parent
if str(curr_dir) not in sys.path:
    sys.path.insert(0, str(curr_dir))
if str(curr_dir.parent) not in sys.path:
    sys.path.insert(0, str(curr_dir.parent))

from segmenter import RoadSegmenter


def resize_maintain_aspect(img, max_h=760, max_w=1280):
    h, w = img.shape[:2]
    scale = min(max_w / float(w), max_h / float(h), 1.0)
    if scale < 1.0:
        return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def resolve_source(source_str: str) -> Path:
    p = Path(source_str)
    if p.exists():
        return p
    for base in [Path.cwd(), curr_dir, curr_dir.parent]:
        candidate = base / "data_test" / source_str
        if candidate.exists():
            return candidate
        candidate2 = base / source_str
        if candidate2.exists():
            return candidate2
    return p


def main():
    parser = argparse.ArgumentParser(description="Road & Sidewalk Segmentation (Output Image + JSON)")
    parser.add_argument("--source", "--image", dest="source", type=str, default="day_1.mp4", help="Video or image input path")
    parser.add_argument("--model", type=str, default="mask2former", choices=["mask2former", "b2", "b0", "b1", "twinlite"], 
                        help="Model engine: mask2former (SOTA Hybrid), b2 (SegFormer-B2), b0 (Lightweight), twinlite (Realtime)")
    parser.add_argument("--preview", action="store_true", help="Display live visual preview window")
    parser.add_argument("--out-dir", type=str, default=str(curr_dir / "outputs"), help="Output directory for annotated image and JSON")
    parser.add_argument("--recalibrate", action="store_true", help="Force recalibrating road mask using Mask2Former")
    parser.add_argument("--alpha", type=float, default=0.38, help="Mask overlay transparency (0.0 to 1.0)")
    parser.add_argument("--fps", type=float, default=30.0, help="Target playback FPS")

    args = parser.parse_args()

    video_path = resolve_source(args.source)
    if not video_path.exists():
        print(f"[Error] Source file not found: {args.source}")
        return

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = video_path.suffix.lower()
    is_image = ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]
    source_id = video_path.stem

    segmenter = RoadSegmenter(
        model_type=args.model,
        enable_corridor_fusion=True
    )

    # 1. Image Mode
    if is_image:
        img = cv2.imread(str(video_path))
        t0 = time.time()
        annotated, road_mask, sidewalk_mask, ratio = segmenter.segment(
            img, alpha=args.alpha, use_calibration=False
        )
        dt = (time.time() - t0) * 1000.0

        polygons = segmenter.extract_road_polygons(road_mask)
        sidewalk_ratio = float(np.sum(sidewalk_mask > 0)) / float(img.shape[0] * img.shape[1])

        out_img_path = out_dir / f"{source_id}_segmented.jpg"
        out_json_path = out_dir / f"{source_id}_segmentation.json"

        cv2.imwrite(str(out_img_path), annotated)

        data = {
            "timestamp": datetime.now().isoformat(),
            "source": video_path.name,
            "model_used": args.model.upper(),
            "image_size": [img.shape[1], img.shape[0]],
            "road_area_ratio": round(ratio, 4),
            "road_area_percentage": round(ratio * 100.0, 2),
            "sidewalk_area_ratio": round(sidewalk_ratio, 4),
            "sidewalk_area_percentage": round(sidewalk_ratio * 100.0, 2),
            "processing_time_ms": round(dt, 1),
            "total_road_polygons": len(polygons),
            "road_polygons": polygons
        }

        with open(out_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"\n[RoadSegmenter] Successfully Processed!")
        print(f" -> Output Image: {out_img_path}")
        print(f" -> Output JSON:  {out_json_path}\n")

        if args.preview:
            cv2.imshow("Road Segmentation Preview", resize_maintain_aspect(annotated))
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        return

    # 2. Video Mode
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[Error] Cannot open video: {video_path}")
        return

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"\n=======================================================")
    print(f"  SMART TRAFFIC ROAD SEGMENTATION (HYBRID SOTA)")
    print(f"  Source: {video_path.name} ({w}x{h}, {total_frames} frames)")
    print(f"  Model Engine: {args.model.upper()}")
    print(f"=======================================================\n")

    use_calibration = (args.model == "mask2former")
    if use_calibration:
        ret, first_frame = cap.read()
        if ret:
            print("[1/2] Calibrating SOTA Road Corridor using Meta Mask2Former...")
            segmenter.calibrate(first_frame, source_id=source_id, force=args.recalibrate)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        print("[2/2] Calibration Ready! Starting High-Speed Stream...\n")

    delay_ms = max(1, int(1000.0 / args.fps))
    frame_idx = 0
    last_annotated = None
    last_ratio = 0.0
    last_road_mask = None
    last_sidewalk_mask = None

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1

            t0 = time.time()
            annotated, road_mask, sidewalk_mask, ratio = segmenter.segment(
                frame, alpha=args.alpha, use_calibration=use_calibration
            )
            fps_live = 1.0 / max(0.0001, (time.time() - t0))

            last_annotated = annotated
            last_ratio = ratio
            last_road_mask = road_mask
            last_sidewalk_mask = sidewalk_mask

            engine_tag = "MASK2FORMER SOTA" if use_calibration else args.model.upper()
            hud_text = f"{engine_tag} | ROAD: {ratio * 100:.1f}%% | FPS: {fps_live:.1f} | Frame: {frame_idx}"
            cv2.putText(annotated, hud_text, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 4)
            cv2.putText(annotated, hud_text, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 2)

            if args.preview:
                preview_frame = resize_maintain_aspect(annotated)
                cv2.imshow("Road Segmentation (Press 'Q' to quit)", preview_frame)
                key = cv2.waitKey(delay_ms) & 0xFF
                if key in [ord('q'), ord('Q'), 27]:
                    print("\n[RoadSegmenter] Stopped by user.")
                    break
            else:
                if frame_idx % 30 == 0 or frame_idx == 1:
                    print(f"[{source_id}] Frame {frame_idx}/{total_frames} | {fps_live:.1f} FPS | Road: {ratio * 100:.1f}%%")

    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()

        if last_annotated is not None:
            out_img_path = out_dir / f"{source_id}_segmented.jpg"
            out_json_path = out_dir / f"{source_id}_segmentation.json"

            cv2.imwrite(str(out_img_path), last_annotated)
            polygons = segmenter.extract_road_polygons(last_road_mask) if last_road_mask is not None else []
            sidewalk_ratio = float(np.sum(last_sidewalk_mask > 0)) / float(w * h) if last_sidewalk_mask is not None else 0.0

            data = {
                "timestamp": datetime.now().isoformat(),
                "source": video_path.name,
                "model_used": args.model.upper(),
                "video_resolution": [w, h],
                "total_frames_processed": frame_idx,
                "road_area_ratio": round(last_ratio, 4),
                "road_area_percentage": round(last_ratio * 100.0, 2),
                "sidewalk_area_ratio": round(sidewalk_ratio, 4),
                "sidewalk_area_percentage": round(sidewalk_ratio * 100.0, 2),
                "total_road_polygons": len(polygons),
                "road_polygons": polygons
            }

            with open(out_json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            print(f"\n[RoadSegmenter] Completed!")
            print(f" -> Output Image: {out_img_path}")
            print(f" -> Output JSON:  {out_json_path}\n")


if __name__ == "__main__":
    main()
