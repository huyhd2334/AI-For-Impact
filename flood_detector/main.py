"""
Điểm khởi chạy CLI cho Flood Detector
Cho phép chạy:
    python -m flood_detector.main --image path/to/image.jpg
hoặc:
    python flood_detector/main.py --image path/to/image.jpg
"""

import sys
from pathlib import Path

curr_dir = Path(__file__).resolve().parent
if str(curr_dir) not in sys.path:
    sys.path.insert(0, str(curr_dir))
if str(curr_dir.parent) not in sys.path:
    sys.path.insert(0, str(curr_dir.parent))

from .inference import main

if __name__ == "__main__":
    main()
