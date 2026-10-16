"""
Gemini Flash Flood Detection - AI-For-Impact
Kiem tra ngap lut tu hinh anh camera giao thong su dung Google Gemini Flash API (Mien phi).
Su dung truc tiep thu vien requests, khong can cai dat them SDK phuc tap.
"""

import os
import sys
import json
import base64
import argparse
from pathlib import Path
import requests

# Dam bao hien thi ky tu tren console Windows khong bi loi ma hoa
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# Danh sach cac model Flash du phong neu model chinh bi qua tai (503)
DEFAULT_MODEL = "gemini-3.7-flash"
FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.8-flash"]
GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def encode_image_to_base64(image_path: Path) -> tuple[str, str]:
    """Doc anh va chuyen sang base64 kem mime type."""
    if not image_path.exists():
        raise FileNotFoundError(f"Khong tim thay file anh tai: {image_path}")

    suffix = image_path.suffix.lower()
    if suffix in [".jpg", ".jpeg"]:
        mime_type = "image/jpeg"
    elif suffix == ".png":
        mime_type = "image/png"
    elif suffix == ".webp":
        mime_type = "image/webp"
    else:
        mime_type = "image/jpeg"

    with open(image_path, "rb") as f:
        encoded_data = base64.b64encode(f.read()).decode("utf-8")

    return encoded_data, mime_type


def call_gemini_vision(image_base64: str, mime_type: str, api_key: str, model: str = DEFAULT_MODEL) -> dict:
    """
    Goi Gemini Vision API voi cau hinh Structured Output (JSON Schema).
    Tu dong chuyen doi sang model Flash du phong neu gap loi qua tai (503).
    """
    candidate_models = [model] + [m for m in FALLBACK_MODELS if m != model]

    prompt_text = (
        "Ban la chuyen gia phan tich giao thong do thi thong minh. "
        "Hay quan sat anh camera giao thong duoc cung cap va xac dinh tinh trang ngap nuoc mat duong. "
        "Chi tra ve ket qua theo dung dinh dang JSON Schema duoc yeu cau."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_base64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "OBJECT",
                "properties": {
                    "is_flooded": {
                        "type": "BOOLEAN",
                        "description": "Mat duong hoac lan duong co dang bi ngap nuoc hoac dong nuoc sau khong",
                    },
                    "severity": {
                        "type": "STRING",
                        "enum": ["none", "minor", "severe"],
                        "description": "none: kho rao hoac mua nhe; minor: dong nuoc nhe; severe: ngap sau anh huong xe",
                    },
                    "safe_for_bikes": {
                        "type": "BOOLEAN",
                        "description": "Duong con an toan cho xe may va xe dap luu thong khong",
                    },
                    "confidence": {
                        "type": "NUMBER",
                        "description": "Do tin cay cua danh gia tu 0.0 den 1.0",
                    },
                    "reason": {
                        "type": "STRING",
                        "description": "Giai thich ngan gon dau hieu nhan biet (nuoc ngap den dau, phuong tien, mat duong)",
                    },
                },
                "required": ["is_flooded", "severity", "safe_for_bikes", "confidence", "reason"],
            },
        },
    }

    headers = {"Content-Type": "application/json"}
    last_error = None

    for m in candidate_models:
        clean_model = m.replace("models/", "")
        url = f"{GEMINI_API_ENDPOINT.format(model=clean_model)}?key={api_key}"
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 503:
                print(f"[!] Model {clean_model} dang ban (503), thu model du phong...")
                continue
            if not response.ok:
                raise RuntimeError(f"Loi goi Gemini API ({response.status_code}): {response.text}")

            resp_json = response.json()
            raw_text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw_text)
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Khong the goi thanh cong Gemini API sau khi thu cac model. Loi cuoi cung: {last_error}")


def simulate_mock_response() -> dict:
    """Gia lap ket qua de test code khi chua co API key."""
    return {
        "is_flooded": True,
        "severity": "severe",
        "safe_for_bikes": False,
        "confidence": 0.95,
        "reason": "Mat duong ngap sau tren dien rong, nuoc ngap qua nua banh xe cua cac phuong tien.",
    }


def process_gate_decision(result: dict):
    """
    Co che Cong dieu huong (Confidence Gate) tuong tu nhu Jev.
    """
    print("\n" + "=" * 60)
    print("[RESULT] KET QUA PHAN TICH TU GOOGLE GEMINI FLASH:")
    print("=" * 60)

    is_flooded = result.get("is_flooded")
    severity = result.get("severity")
    safe_for_bikes = result.get("safe_for_bikes")
    confidence = result.get("confidence", 0.0)
    reason = result.get("reason", "")

    print(f"- Co ngap lut khong? : {is_flooded} (Do tin cay: {confidence:.2%})")
    print(f"- Muc do ngap        : {severity}")
    print(f"- An toan cho xe may : {safe_for_bikes}")
    print(f"- Giai thich chi tiet: {reason}")

    print("\n" + "-" * 60)
    print("[GATE] LOGIC CONG DIEU HUONG (CONFIDENCE GATE):")
    print("-" * 60)

    if is_flooded is True and confidence >= 0.85:
        print(f"[!] [CANH BAO TU DONG - MUC CAO] Phat hien ngap lut ({severity})!")
        print("    -> Hanh dong: Phat canh bao khan cap den ban do & he thong loa giao thong.")
    elif is_flooded is True and confidence < 0.85:
        print("[?] [XAC MINH] Nghi ngo co ngap nhung do tin cay chua dat nguong 85%.")
        print("    -> Hanh dong: Chuyen anh vao hang doi xac minh boi con nguoi.")
    else:
        print("[OK] [BINH THUONG] Doan duong kho rao, an toan luu thong.")


def main():
    parser = argparse.ArgumentParser(description="Test Google Gemini Flash for Flood Detection")
    parser.add_argument(
        "--image",
        type=str,
        default="images/high_1.png",
        help="Duong dan anh kiem tra (mac dinh: images/high_1.png)",
    )
    parser.add_argument(
        "--key",
        type=str,
        default=None,
        help="Gemini API Key (hoac set bien moi truong GEMINI_API_KEY)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Ten model Gemini (mac dinh: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Chay o che do Gia lap (Mock) khong can API key",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    image_path = Path(args.image)
    if not image_path.is_absolute():
        image_path = project_root / image_path

    print(f"[*] Dang tai anh: {image_path}")
    if not image_path.exists():
        print(f"[X] Khong tim thay file: {image_path}")
        sys.exit(1)

    api_key = args.key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if args.mock or not api_key:
        if not api_key:
            print("[i] Khong tim thay GEMINI_API_KEY.")
            print("    -> Dang chay o che do GIA LAP (MOCK).")
            print("    -> Lay key mien phi tai: https://aistudio.google.com")
            print("    -> Sau do chay: python gemini_flood_test.py --key YOUR_KEY")
        else:
            print("[i] Dang chay o che do MOCK theo tham so --mock.")
        result = simulate_mock_response()
    else:
        print(f"[*] Dang ma hoa anh va goi model {args.model} qua Google AI Studio API...")
        b64_data, mime = encode_image_to_base64(image_path)
        try:
            result = call_gemini_vision(b64_data, mime, api_key, model=args.model)
        except Exception as e:
            print(f"[X] Loi khi goi Gemini API: {e}")
            sys.exit(1)

    process_gate_decision(result)


if __name__ == "__main__":
    main()
