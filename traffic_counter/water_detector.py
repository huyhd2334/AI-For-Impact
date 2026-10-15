from typing import Dict, Any, Tuple
import cv2
import numpy as np


class SimpleWaterDetector:
    def __init__(self, roi_bottom_ratio: float = 0.5, min_water_ratio: float = 0.08):
        # Scan only the lower half of the frame (roadway) to avoid sky and buildings
        self.roi_bottom_ratio = roi_bottom_ratio
        self.min_water_ratio = min_water_ratio

    def detect_water(self, frame: np.ndarray) -> Tuple[bool, float, np.ndarray]:
        h, w = frame.shape[:2]
        roi_start_y = int(h * self.roi_bottom_ratio)
        roi = frame[roi_start_y:h, 0:w]

        # Estimate ambient brightness to distinguish day vs night
        ambient_brightness = float(np.mean(frame[:max(1, int(h * 0.4)), :, :]))
        is_night = ambient_brightness < 75.0

        # Color and gradient transforms
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        s_channel = hsv[:, :, 1]
        v_channel = hsv[:, :, 2]
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)

        if is_night:
            specular = (s_channel < 35) & (v_channel > 230)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
            cleaned_mask = cv2.morphologyEx(np.uint8(specular) * 255, cv2.MORPH_OPEN, kernel)
            kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
            cleaned_mask = cv2.morphologyEx(cleaned_mask, cv2.MORPH_CLOSE, kernel_close)
        else:
            water_reflection_mask = (s_channel < 50) & (v_channel > 135)
            smooth_mask = np.abs(laplacian) < 12
            water_candidates = np.uint8(water_reflection_mask & smooth_mask) * 255
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11))
            cleaned_mask = cv2.morphologyEx(water_candidates, cv2.MORPH_OPEN, kernel)

        # Compute ratio of water candidate pixels to total ROI area
        total_roi_pixels = roi.shape[0] * roi.shape[1]
        water_pixels = cv2.countNonZero(cleaned_mask)
        water_ratio = water_pixels / float(total_roi_pixels) if total_roi_pixels > 0 else 0.0

        # Binary decision and normalized confidence score (0.0 to 1.0)
        has_water = bool(water_ratio >= self.min_water_ratio)
        confidence = float(min(1.0, max(0.0, water_ratio * 5.0)))

        return has_water, confidence, cleaned_mask
