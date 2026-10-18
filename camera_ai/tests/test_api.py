from fastapi.testclient import TestClient
from camera_ai.api import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "camera-ai"
    assert "device" in data


def test_calibrate_endpoint():
    response = client.post(
        "/api/v1/camera/calibrate",
        json={
            "camera_id": "TEST_CAM_01",
            "image_path": "data_test/sample_day.jpg",
            "force": True
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["camera_id"] == "TEST_CAM_01"
    assert data["status"] == "calibrated"
    assert data["cached"] is True
    assert data["road_ratio"] > 0


def test_analyze_frame_dry():
    response = client.post(
        "/api/v1/camera/analyze-frame",
        json={
            "camera_id": "TEST_CAM_01",
            "image_path": "data_test/sample_day.jpg",
            "use_reasoning_llm": False,
            "save_evidence": True
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["camera_id"] == "TEST_CAM_01"
    assert data["flood_status"] in ["not_flooded", "flooded", "uncertain"]
    assert "traffic_status" in data
    assert "evidence_ref" in data
    assert data["road_mask_cached"] is True


def test_analyze_frame_flooded():
    response = client.post(
        "/api/v1/camera/analyze-frame",
        json={
            "camera_id": "TEST_CAM_FLOOD",
            "image_path": "images/high_1.png",
            "use_reasoning_llm": True,
            "save_evidence": True
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["camera_id"] == "TEST_CAM_FLOOD"
    assert data["flood_status"] == "flooded"
    assert data["severity"] == "severe"
    assert data["safe_for_bikes"] is False


def test_get_camera_state():
    response = client.get("/api/v1/camera/TEST_CAM_01/state")
    assert response.status_code == 200
    data = response.json()
    assert data["camera_id"] == "TEST_CAM_01"
    assert "latest_event_id" in data


def test_get_camera_events():
    response = client.get("/api/v1/camera/TEST_CAM_01/events?limit=10")
    assert response.status_code == 200
    events = response.json()
    assert isinstance(events, list)
    assert len(events) >= 1
