import os
import json
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List
import cv2
import numpy as np

from road_segmentation.segmenter import RoadSegmenter


class RoadMaskManager:
    """
    Manages road mask calibration and caching per fixed camera ID.
    Implements: Frame chuẩn -> Road segmentation -> road_mask (lưu để tái sử dụng).
    """

    def __init__(self, cache_dir: Optional[str] = None, model_type: str = "twinlite"):
        curr_dir = Path(__file__).resolve().parent
        self.cache_dir = Path(cache_dir) if cache_dir else curr_dir.parent / "road_segmentation" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.model_type = model_type
        self._segmenter: Optional[RoadSegmenter] = None

        # In-memory fast cache: camera_id -> (road_mask, sidewalk_mask, (orig_w, orig_h))
        self._memory_cache: Dict[str, Dict[str, Any]] = {}

    def _get_segmenter(self) -> RoadSegmenter:
        if self._segmenter is None:
            self._segmenter = RoadSegmenter(
                model_type=self.model_type,
                cache_dir=str(self.cache_dir)
            )
        return self._segmenter

    def is_calibrated(self, camera_id: str) -> bool:
        if camera_id in self._memory_cache:
            return True
        mask_file = self.cache_dir / f"{camera_id}_road_mask.png"
        roi_file = self.cache_dir / f"{camera_id}_road_roi.json"
        return mask_file.exists() and roi_file.exists()

    def calibrate(
        self, 
        camera_id: str, 
        reference_frame: np.ndarray, 
        force: bool = False
    ) -> Tuple[np.ndarray, np.ndarray, float, List[List[List[int]]]]:
        """
        Calibrate road mask for a specific camera_id using reference frame.
        Saves cache to disk and in-memory for instant reuse on subsequent frames.
        """
        orig_h, orig_w = reference_frame.shape[:2]
        segmenter = self._get_segmenter()
        
        segmenter.calibrate(reference_frame, source_id=camera_id, force=force)
        
        road_mask = segmenter.calibrated_road_mask
        sidewalk_mask = segmenter.calibrated_sidewalk_mask
        polygons = segmenter.calibrated_polygons
        
        road_ratio = float(np.sum(road_mask > 0)) / float(orig_h * orig_w) if (orig_h * orig_w) > 0 else 0.0

        self._memory_cache[camera_id] = {
            "road_mask": road_mask,
            "sidewalk_mask": sidewalk_mask,
            "polygons": polygons,
            "dims": (orig_w, orig_h),
            "road_ratio": road_ratio
        }

        return road_mask, sidewalk_mask, road_ratio, polygons

    def get_road_mask(
        self, 
        camera_id: str, 
        current_frame: np.ndarray, 
        auto_calibrate_if_missing: bool = True
    ) -> Tuple[np.ndarray, np.ndarray, bool]:
        """
        Retrieves the cached road_mask for camera_id.
        Returns: (road_mask, sidewalk_mask, is_reused_from_cache)
        """
        orig_h, orig_w = current_frame.shape[:2]

        # 1. Check in-memory cache
        if camera_id in self._memory_cache:
            entry = self._memory_cache[camera_id]
            r_mask = entry["road_mask"]
            s_mask = entry["sidewalk_mask"]
            if r_mask.shape != (orig_h, orig_w):
                r_mask = cv2.resize(r_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
                s_mask = cv2.resize(s_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
            return r_mask, s_mask, True

        # 2. Check disk cache
        mask_file = self.cache_dir / f"{camera_id}_road_mask.png"
        side_file = self.cache_dir / f"{camera_id}_sidewalk_mask.png"
        roi_file = self.cache_dir / f"{camera_id}_road_roi.json"

        if mask_file.exists():
            r_mask = cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
            s_mask = cv2.imread(str(side_file), cv2.IMREAD_GRAYSCALE) if side_file.exists() else np.zeros((orig_h, orig_w), dtype=np.uint8)

            if r_mask.shape != (orig_h, orig_w):
                r_mask = cv2.resize(r_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
                s_mask = cv2.resize(s_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)

            polygons = []
            if roi_file.exists():
                try:
                    with open(roi_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        polygons = data.get("road_polygons", [])
                except Exception:
                    pass

            road_ratio = float(np.sum(r_mask > 0)) / float(orig_h * orig_w) if (orig_h * orig_w) > 0 else 0.0

            self._memory_cache[camera_id] = {
                "road_mask": r_mask,
                "sidewalk_mask": s_mask,
                "polygons": polygons,
                "dims": (orig_w, orig_h),
                "road_ratio": road_ratio
            }
            return r_mask, s_mask, True

        # 3. Not calibrated yet -> run auto-calibration on this first frame
        if auto_calibrate_if_missing:
            r_mask, s_mask, _, _ = self.calibrate(camera_id, current_frame, force=False)
            return r_mask, s_mask, False

        # Fallback to empty mask
        empty_mask = np.zeros((orig_h, orig_w), dtype=np.uint8)
        return empty_mask, empty_mask, False
