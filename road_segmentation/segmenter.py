import os
import json
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any
import cv2
import numpy as np
import torch
from PIL import Image

try:
    from .model import TwinLiteNet
except ImportError:
    from model import TwinLiteNet


class RoadSegmenter:
    SUPPORTED_MODELS = {
        "twinlite": "weights/best.pth",
        "b0": "nvidia/segformer-b0-finetuned-cityscapes-512-1024",
        "b1": "nvidia/segformer-b1-finetuned-cityscapes-1024-1024",
        "b2": "nvidia/segformer-b2-finetuned-cityscapes-1024-1024",
        "mask2former": "facebook/mask2former-swin-small-cityscapes-semantic",
    }

    def __init__(
        self,
        model_type: str = "mask2former",
        weights_path: Optional[str] = None,
        device: Optional[str] = None,
        smooth_factor: float = 0.7,
        enable_corridor_fusion: bool = True,
        cache_dir: Optional[str] = None
    ):
        self.model_type = model_type.lower()
        self.smooth_factor = smooth_factor
        self.enable_corridor_fusion = enable_corridor_fusion
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        
        curr_dir = Path(__file__).resolve().parent
        self.cache_dir = Path(cache_dir) if cache_dir else curr_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.calibrated_road_mask: Optional[np.ndarray] = None
        self.calibrated_sidewalk_mask: Optional[np.ndarray] = None
        self.calibrated_polygons: List[List[List[int]]] = []

        self.smooth_road_mask: Optional[np.ndarray] = None
        self.smooth_sidewalk_mask: Optional[np.ndarray] = None

        if "twinlite" in self.model_type:
            self._init_twinlite(weights_path)
        elif "mask2former" in self.model_type:
            self._init_mask2former()
        else:
            self._init_segformer()

    def _init_twinlite(self, weights_path: Optional[str]):
        if weights_path is None:
            curr_dir = Path(__file__).resolve().parent
            weights_path = str(curr_dir / "weights" / "best.pth")

        if not os.path.exists(weights_path):
            raise FileNotFoundError(f"[RoadSegmenter] Missing TwinLiteNet weights: {weights_path}")

        self.model = TwinLiteNet()
        state = torch.load(weights_path, map_location=self.device)
        clean_state = {k[7:] if k.startswith("module.") else k: v for k, v in state.items()}
        self.model.load_state_dict(clean_state)
        self.model.to(self.device).eval()
        self.engine = "twinlite"
        print(f"[RoadSegmenter] Loaded TwinLiteNet on {self.device}")

    def _init_segformer(self):
        from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation
        hf_id = self.SUPPORTED_MODELS.get(self.model_type, self.SUPPORTED_MODELS["b2"])
        print(f"[RoadSegmenter] Loading SegFormer: {hf_id} on {self.device}...")
        self.processor = SegformerImageProcessor.from_pretrained(hf_id)
        self.model = SegformerForSemanticSegmentation.from_pretrained(hf_id).to(self.device).eval()
        self.engine = "segformer"
        print(f"[RoadSegmenter] SegFormer Ready ({self.model_type.upper()})")

    def _init_mask2former(self):
        from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation
        hf_id = self.SUPPORTED_MODELS["mask2former"]
        print(f"[RoadSegmenter] Loading Meta Mask2Former ({hf_id}) on {self.device}...")
        self.processor = AutoImageProcessor.from_pretrained(hf_id)
        self.model = Mask2FormerForUniversalSegmentation.from_pretrained(hf_id).to(self.device).eval()
        self.engine = "mask2former"
        print("[RoadSegmenter] Meta Mask2Former Ready (68.7M Parameters)")

    def calibrate(self, reference_frame: np.ndarray, source_id: str = "camera", force: bool = False):
        mask_file = self.cache_dir / f"{source_id}_road_mask.png"
        side_file = self.cache_dir / f"{source_id}_sidewalk_mask.png"
        roi_file = self.cache_dir / f"{source_id}_road_roi.json"

        orig_h, orig_w = reference_frame.shape[:2]

        if not force and mask_file.exists() and roi_file.exists():
            print(f"[RoadSegmenter] Found existing calibration cache for '{source_id}'. Loading...")
            self.calibrated_road_mask = cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
            if self.calibrated_road_mask.shape != (orig_h, orig_w):
                self.calibrated_road_mask = cv2.resize(self.calibrated_road_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
            
            if side_file.exists():
                self.calibrated_sidewalk_mask = cv2.imread(str(side_file), cv2.IMREAD_GRAYSCALE)
                if self.calibrated_sidewalk_mask.shape != (orig_h, orig_w):
                    self.calibrated_sidewalk_mask = cv2.resize(self.calibrated_sidewalk_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
            else:
                self.calibrated_sidewalk_mask = np.zeros((orig_h, orig_w), dtype=np.uint8)

            with open(roi_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.calibrated_polygons = data.get("road_polygons", [])
            print("[RoadSegmenter] Calibration cache loaded successfully (0 ms overhead)")
            return

        print(f"[RoadSegmenter] Running Mask2Former Calibration on {source_id}...")
        road_mask, sidewalk_mask = self._infer_mask2former(reference_frame, orig_w, orig_h)
        self.calibrated_road_mask = road_mask
        self.calibrated_sidewalk_mask = sidewalk_mask
        self.calibrated_polygons = self.extract_road_polygons(road_mask)

        cv2.imwrite(str(mask_file), road_mask)
        cv2.imwrite(str(side_file), sidewalk_mask)
        with open(roi_file, "w", encoding="utf-8") as f:
            json.dump({
                "source_id": source_id,
                "image_size": [orig_w, orig_h],
                "road_polygons": self.calibrated_polygons
            }, f, indent=2)
        print(f"[RoadSegmenter] Calibration completed and saved to {self.cache_dir}")

    def segment(
        self,
        frame: np.ndarray,
        alpha: float = 0.40,
        draw_road: bool = True,
        draw_sidewalk: bool = True,
        use_calibration: bool = True
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        orig_h, orig_w = frame.shape[:2]

        if use_calibration and self.calibrated_road_mask is not None:
            final_road = self.calibrated_road_mask
            final_sidewalk = self.calibrated_sidewalk_mask if self.calibrated_sidewalk_mask is not None else np.zeros_like(final_road)
        else:
            if self.engine == "twinlite":
                road_mask, sidewalk_mask = self._infer_twinlite(frame, orig_w, orig_h)
            elif self.engine == "mask2former":
                road_mask, sidewalk_mask = self._infer_mask2former(frame, orig_w, orig_h)
            else:
                road_mask, sidewalk_mask = self._infer_segformer(frame, orig_w, orig_h)

            final_road = (road_mask > 0).astype(np.uint8) * 255
            final_sidewalk = (sidewalk_mask > 0).astype(np.uint8) * 255

        road_ratio = float(np.sum(final_road > 0)) / float(orig_h * orig_w)

        # High-speed GPU tensor blending for 4K/2K resolution
        if self.device == "cuda":
            frame_t = torch.from_numpy(frame).cuda()
            if draw_road:
                r_idx_t = torch.from_numpy(final_road > 0).cuda()
                if r_idx_t.any():
                    color_r = torch.tensor([255, 140, 0], dtype=torch.float32, device="cuda")
                    frame_t[r_idx_t] = (frame_t[r_idx_t].float() * (1.0 - alpha) + color_r * alpha).byte()

            if draw_sidewalk:
                s_idx_t = torch.from_numpy(final_sidewalk > 0).cuda()
                if s_idx_t.any():
                    color_s = torch.tensor([180, 50, 180], dtype=torch.float32, device="cuda")
                    frame_t[s_idx_t] = (frame_t[s_idx_t].float() * (1.0 - alpha * 0.8) + color_s * (alpha * 0.8)).byte()

            annotated = frame_t.cpu().numpy()
        else:
            annotated = frame.copy()
            if draw_road:
                r_idx = final_road > 0
                if np.any(r_idx):
                    color_road = np.full_like(annotated, (255, 140, 0))
                    annotated[r_idx] = cv2.addWeighted(annotated, 1.0 - alpha, color_road, alpha, 0)[r_idx]
            if draw_sidewalk:
                s_idx = final_sidewalk > 0
                if np.any(s_idx):
                    color_side = np.full_like(annotated, (180, 50, 180))
                    annotated[s_idx] = cv2.addWeighted(annotated, 1.0 - (alpha * 0.8), color_side, alpha * 0.8, 0)[s_idx]

        return annotated, final_road, final_sidewalk, road_ratio

    def _infer_mask2former(self, frame: np.ndarray, orig_w: int, orig_h: int) -> Tuple[np.ndarray, np.ndarray]:
        infer_w = min(orig_w, 1024)
        infer_h = int(orig_h * (infer_w / float(orig_w)))
        resized = cv2.resize(frame, (infer_w, infer_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model(**inputs)
            results = self.processor.post_process_semantic_segmentation(outputs, target_sizes=[(orig_h, orig_w)])[0]
            pred = results.cpu().numpy()

        road = (pred == 0)
        sidewalk = (pred == 1)

        if self.enable_corridor_fusion:
            vehicles = np.isin(pred, [11, 12, 13, 14, 15, 17, 18])
            corridor = road | vehicles
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
            road = cv2.morphologyEx(corridor.astype(np.uint8), cv2.MORPH_CLOSE, kernel) > 0

        return road.astype(np.uint8) * 255, sidewalk.astype(np.uint8) * 255

    def _infer_segformer(self, frame: np.ndarray, orig_w: int, orig_h: int) -> Tuple[np.ndarray, np.ndarray]:
        infer_w = min(orig_w, 1024)
        infer_h = int(orig_h * (infer_w / float(orig_w)))
        infer_w = (infer_w // 32) * 32
        infer_h = (infer_h // 32) * 32

        resized = cv2.resize(frame, (infer_w, infer_h), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        
        inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            out = self.model(**inputs)
            up = torch.nn.functional.interpolate(
                out.logits, size=(orig_h, orig_w), mode="bilinear", align_corners=False
            )
            pred = up.argmax(dim=1)[0].cpu().numpy()

        road = (pred == 0)
        sidewalk = (pred == 1)

        if self.enable_corridor_fusion:
            vehicles = np.isin(pred, [11, 12, 13, 14, 15, 17, 18])
            corridor = road | vehicles
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
            road = cv2.morphologyEx(corridor.astype(np.uint8), cv2.MORPH_CLOSE, kernel) > 0

        return road.astype(np.uint8) * 255, sidewalk.astype(np.uint8) * 255

    def _infer_twinlite(self, frame: np.ndarray, orig_w: int, orig_h: int) -> Tuple[np.ndarray, np.ndarray]:
        resized = cv2.resize(frame, (640, 360))
        inp = resized[:, :, ::-1].transpose(2, 0, 1)
        inp = np.ascontiguousarray(inp)
        inp = torch.from_numpy(inp).unsqueeze(0).to(self.device).float() / 255.0

        with torch.no_grad():
            outputs = self.model(inp)
            da_pred = outputs[0]
            ll_pred = outputs[1]

        _, da_cls = torch.max(da_pred, 1)
        _, ll_cls = torch.max(ll_pred, 1)

        da_small = (da_cls.byte().cpu().numpy()[0] * 255)
        ll_small = (ll_cls.byte().cpu().numpy()[0] * 255)

        da_mask = cv2.resize(da_small, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
        ll_mask = cv2.resize(ll_small, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
        return da_mask, ll_mask

    def extract_road_polygons(self, road_mask: np.ndarray, min_area: float = 2000.0) -> List[List[List[int]]]:
        contours, _ = cv2.findContours(road_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        polygons = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area >= min_area:
                epsilon = 0.005 * cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, epsilon, True)
                pts = approx.reshape(-1, 2).tolist()
                polygons.append(pts)
        return polygons
