"""
Road & Drivable Area Segmentation Module
Uses TwinLiteNet (by Che Quang Huy & Pham Van Khanh)
Pretrained on BDD100K for Drivable Area & Lane Line Segmentation.
"""

from .segmenter import RoadSegmenter

__all__ = ["RoadSegmenter"]
