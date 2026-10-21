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


def test_calibration_router_road_roi():
    cam_id = "TEST_CAM_ROUTER"
    # 1. Set Road ROI
    payload = {
        "camera_id": cam_id,
        "road_polygons": [
            [[100, 200], [500, 200], [500, 600], [100, 600]]
        ],
        "image_size": [1344, 768],
        "curb_buffer_px": 30
    }
    res = client.post("/api/v1/calibration/road-roi", json=payload)
    assert res.status_code == 200
    d = res.json()
    assert d["status"] == "success"
    assert d["camera_id"] == cam_id
    assert d["polygons_count"] == 1
    assert d["road_area_ratio"] > 0

    # 2. Get Road ROI
    res_get = client.get(f"/api/v1/calibration/road-roi/{cam_id}")
    assert res_get.status_code == 200
    d_get = res_get.json()
    assert d_get["camera_id"] == cam_id
    assert len(d_get["road_polygons"]) == 1

    # 3. Clean up
    res_del = client.delete(f"/api/v1/calibration/road-roi/{cam_id}")
    assert res_del.status_code == 200


def test_calibration_router_tripwires():
    cam_id = "TEST_CAM_ROUTER"
    # 1. Set Tripwires
    payload = {
        "camera_id": cam_id,
        "tripwires": [
            {"id": "line_1", "name": "Nhanh 1", "p1": [100, 400], "p2": [600, 400]},
            {"id": "line_2", "name": "Nhanh 2", "p1": [700, 400], "p2": [1200, 400]}
        ],
        "image_size": [1344, 768]
    }
    res = client.post("/api/v1/calibration/tripwires", json=payload)
    assert res.status_code == 200
    d = res.json()
    assert d["status"] == "success"
    assert d["tripwires_count"] == 2

    # 2. Get Tripwires
    res_get = client.get(f"/api/v1/calibration/tripwires/{cam_id}")
    assert res_get.status_code == 200
    d_get = res_get.json()
    assert d_get["tripwires_count"] == 2

    # 3. Summary
    res_sum = client.get(f"/api/v1/calibration/summary/{cam_id}")
    assert res_sum.status_code == 200
    d_sum = res_sum.json()
    assert d_sum["has_tripwires"] is True
    assert d_sum["tripwires_count"] == 2

    # 4. Clean up
    res_del = client.delete(f"/api/v1/calibration/tripwires/{cam_id}")
    assert res_del.status_code == 200


def test_calibration_router_list_summary():
    res = client.get("/api/v1/calibration/summary")
    assert res.status_code == 200
    d = res.json()
    assert "total_calibrated_cameras" in d
    assert isinstance(d["cameras"], list)
