import os
import io
import base64
from typing import Optional, List
from pathlib import Path
import cv2
import numpy as np
import torch
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .schemas import (
    CameraObservationEvent,
    CameraCurrentState,
    CalibrateRequest,
    CalibrateResponse,
    AnalyzeFrameRequest,
    AnalyzeVideoRequest
)
from .pipeline import UnifiedCameraAnalyzer
from .calibration_router import router as calibration_router
from .task_router import task_router

app = FastAPI(
    title="SafeRoute Camera AI Service",
    description="Intelligent traffic surveillance & flood assessment microservice conforming to SafeRoute SE Contract v1",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calibration_router, prefix="/api/v1/calibration")
app.include_router(task_router)

# Mount thư mục giao diện Web Demo và outputs video
static_dir = Path(__file__).resolve().parent / "static"
outputs_dir = Path(__file__).resolve().parent.parent / "outputs"
outputs_dir.mkdir(parents=True, exist_ok=True)

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

if outputs_dir.exists():
    app.mount("/outputs", StaticFiles(directory=str(outputs_dir)), name="outputs")


@app.get("/", response_class=HTMLResponse, tags=["Web UI Demo"])
@app.get("/demo", response_class=HTMLResponse, tags=["Web UI Demo"])
def serve_demo_ui():
    """Giao diện Web trực quan để demo tương tác với Groq LLM Agent và theo dõi tiến độ video"""
    html_file = static_dir / "index.html"
    if html_file.exists():
        with open(html_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>SafeRoute AI Service is Running. Visit /docs for API.</h1>"

# Global unified pipeline instance
analyzer: Optional[UnifiedCameraAnalyzer] = None


def get_analyzer() -> UnifiedCameraAnalyzer:
    global analyzer
    if analyzer is None:
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        print(f"[Camera AI API] Initializing UnifiedCameraAnalyzer (Gemini Key present: {bool(gemini_key)})...")
        analyzer = UnifiedCameraAnalyzer(
            gemini_api_key=gemini_key,
            road_model_type=os.getenv("ROAD_MODEL_TYPE", "b0"),
            vehicle_model_name="vn_traffic_surveillance.pt"
        )
    return analyzer


@app.on_event("startup")
def startup_event():
    get_analyzer()
    print("[Camera AI API] Initialization complete and ready to serve requests.")


def _decode_image_from_bytes(image_bytes: bytes) -> np.ndarray:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Could not decode image from provided payload.")
    return img


def _decode_image_from_base64(b64_str: str) -> np.ndarray:
    if "," in b64_str:
        b64_str = b64_str.split(",", 1)[1]
    image_bytes = base64.b64decode(b64_str)
    return _decode_image_from_bytes(image_bytes)


@app.get("/health", tags=["System"])
@app.get("/api/v1/health", tags=["System"])
def health_check():
    ai = get_analyzer()
    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    cached_cameras = [
        f.stem.replace("_road_mask", "")
        for f in ai.road_manager.cache_dir.glob("*_road_mask.png")
    ]

    return {
        "status": "healthy",
        "service": "camera-ai",
        "version": "1.0.0",
        "device": device_name,
        "cuda_available": torch.cuda.is_available(),
        "calibrated_cameras": list(set(cached_cameras)),
        "gemini_reasoning_enabled": bool(ai.flood_reasoner.api_key)
    }


@app.post("/api/v1/camera/calibrate", response_model=CalibrateResponse, tags=["Camera Calibration"])
def calibrate_camera_json(payload: CalibrateRequest):
    """
    Calibrate reference road mask for a fixed camera using JSON payload (image_path or image_base64).
    """
    ai = get_analyzer()
    img: Optional[np.ndarray] = None
    if payload.image_base64:
        img = _decode_image_from_base64(payload.image_base64)
    elif payload.image_path:
        p = Path(payload.image_path)
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Image file not found: {payload.image_path}")
        img = cv2.imread(str(p))

    if img is None:
        raise HTTPException(status_code=400, detail="Must provide image_base64 or image_path.")

    road_mask, sidewalk_mask, road_ratio, polygons = ai.calibrate_camera(
        payload.camera_id, img, force=payload.force
    )
    orig_h, orig_w = img.shape[:2]

    return CalibrateResponse(
        camera_id=payload.camera_id,
        status="calibrated",
        cached=True,
        road_ratio=round(road_ratio, 4),
        image_dimensions=[orig_w, orig_h],
        polygons_count=len(polygons),
        cache_path=str(ai.road_manager.cache_dir / f"{payload.camera_id}_road_mask.png")
    )


@app.post("/api/v1/camera/calibrate/upload", response_model=CalibrateResponse, tags=["Camera Calibration"])
async def calibrate_camera_upload(
    camera_id: str = Form(...),
    force: bool = Form(False),
    file: UploadFile = File(...)
):
    """
    Calibrate reference road mask for a fixed camera by uploading an image file directly.
    """
    ai = get_analyzer()
    content = await file.read()
    img = _decode_image_from_bytes(content)

    road_mask, sidewalk_mask, road_ratio, polygons = ai.calibrate_camera(
        camera_id, img, force=force
    )
    orig_h, orig_w = img.shape[:2]

    return CalibrateResponse(
        camera_id=camera_id,
        status="calibrated",
        cached=True,
        road_ratio=round(road_ratio, 4),
        image_dimensions=[orig_w, orig_h],
        polygons_count=len(polygons),
        cache_path=str(ai.road_manager.cache_dir / f"{camera_id}_road_mask.png")
    )


@app.post("/api/v1/camera/analyze-frame", response_model=CameraObservationEvent, tags=["Frame Inference"])
def analyze_frame_json(payload: AnalyzeFrameRequest):
    """
    Executes the unified analysis pipeline on a single camera frame via JSON payload:
    - Reuses cached road_mask
    - Runs flood reasoning on road corridor
    - Evaluates water depth and credibility
    - Counts and tracks vehicles & congestion
    - Emits fused CameraObservationEvent (SE Contract v1)
    """
    ai = get_analyzer()
    img: Optional[np.ndarray] = None
    source_hint = ""
    if payload.image_base64:
        img = _decode_image_from_base64(payload.image_base64)
    elif payload.image_path:
        p = Path(payload.image_path)
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Image file not found: {payload.image_path}")
        img = cv2.imread(str(p))
        source_hint = p.name

    if img is None:
        raise HTTPException(status_code=400, detail="Must provide image_base64 or image_path.")

    event, _ = ai.analyze_frame(
        camera_id=payload.camera_id,
        frame=img,
        timestamp=payload.timestamp,
        use_llm=payload.use_reasoning_llm,
        save_evidence=payload.save_evidence,
        source_hint=source_hint
    )

    return event


@app.post("/api/v1/camera/analyze-frame/upload", response_model=CameraObservationEvent, tags=["Frame Inference"])
async def analyze_frame_upload(
    camera_id: str = Form(...),
    timestamp: Optional[float] = Form(None),
    use_reasoning_llm: bool = Form(True),
    save_evidence: bool = Form(True),
    file: UploadFile = File(...)
):
    """
    Executes the unified analysis pipeline on a single camera frame uploaded as multipart file.
    """
    ai = get_analyzer()
    content = await file.read()
    img = _decode_image_from_bytes(content)

    event, _ = ai.analyze_frame(
        camera_id=camera_id,
        frame=img,
        timestamp=timestamp,
        use_llm=use_reasoning_llm,
        save_evidence=save_evidence,
        source_hint=file.filename or ""
    )

    return event


@app.post("/api/v1/camera/analyze-video", response_model=List[CameraObservationEvent], tags=["Video Stream"])
def analyze_video(request: AnalyzeVideoRequest):
    """
    Processes an entire video clip or camera stream file and returns emitted observation events.
    """
    ai = get_analyzer()
    video_path = Path(request.video_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video file not found: {request.video_path}")

    events = ai.process_video(
        camera_id=request.camera_id,
        video_source=str(video_path),
        target_fps=request.target_fps,
        sample_interval_sec=request.sample_interval_sec,
        use_llm_on_alert=request.use_reasoning_llm
    )
    return events


@app.get("/api/v1/camera/{camera_id}/state", response_model=CameraCurrentState, tags=["State Storage"])
def get_camera_state(camera_id: str):
    """
    Retrieves the latest active state row for this camera in schema camera_ai (current table).
    """
    ai = get_analyzer()
    state = ai.get_current_state(camera_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"No observations recorded for camera: {camera_id}")
    return state


@app.get("/api/v1/camera/{camera_id}/events", response_model=List[CameraObservationEvent], tags=["State Storage"])
def get_camera_events(
    camera_id: str,
    limit: int = Query(50, ge=1, le=500),
    since_revision: int = Query(0, ge=0)
):
    """
    Cursor-based retrieval of state events history for SafeRoute Worker sync (event table).
    """
    ai = get_analyzer()
    return ai.get_events(camera_id, limit=limit, since_revision=since_revision)


@app.get("/api/v1/camera/{camera_id}/road-mask", tags=["Camera Calibration"])
def get_road_mask_image(camera_id: str):
    """
    Returns the cached road mask PNG file for the camera.
    """
    ai = get_analyzer()
    mask_file = ai.road_manager.cache_dir / f"{camera_id}_road_mask.png"
    if not mask_file.exists():
        raise HTTPException(status_code=404, detail=f"No cached road mask found for camera: {camera_id}")
    return FileResponse(str(mask_file), media_type="image/png")
