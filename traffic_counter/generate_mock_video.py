import cv2
import numpy as np


def generate_mock_traffic_video(output_path: str = "mock_traffic_flood.mp4", duration_sec: int = 6, fps: int = 20):
    width, height = 640, 360
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    total_frames = duration_sec * fps
    print(f"[MockVideo] Generating test video ({total_frames} frames)...")

    # Generate synthetic moving vehicles
    num_vehicles = 18
    vehicles = []
    for _ in range(num_vehicles):
        vehicles.append({
            "x": np.random.randint(50, width - 100),
            "y": np.random.randint(100, height - 120),
            "speed": np.random.randint(2, 6),
            "w": np.random.randint(30, 70),
            "h": np.random.randint(20, 40),
            "color": (np.random.randint(50, 255), np.random.randint(50, 255), np.random.randint(50, 255))
        })

    for f in range(total_frames):
        # Asphalt background
        frame = np.full((height, width, 3), 50, dtype=np.uint8)

        # Road lanes
        cv2.line(frame, (0, 180), (width, 180), (255, 255, 255), 2)
        cv2.line(frame, (0, 270), (width, 270), (255, 255, 255), 2)

        # Water puddle overlay
        overlay = frame.copy()
        cv2.ellipse(overlay, (width // 2, height - 80), (220, 70), 0, 0, 360, (140, 120, 90), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        # Draw moving vehicles
        for v in vehicles:
            v["x"] = (v["x"] + v["speed"]) % (width - 40)
            cv2.rectangle(frame, (v["x"], v["y"]), (v["x"] + v["w"], v["y"] + v["h"]), v["color"], -1)
            cv2.circle(frame, (v["x"] + 8, v["y"] + v["h"]), 5, (10, 10, 10), -1)
            cv2.circle(frame, (v["x"] + v["w"] - 8, v["y"] + v["h"]), 5, (10, 10, 10), -1)

        cv2.putText(frame, f"SIMULATED CAMERA - FRAME {f}", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        out.write(frame)

    out.release()
    print(f"[MockVideo] Test video created successfully: {output_path}")


if __name__ == "__main__":
    generate_mock_traffic_video()
