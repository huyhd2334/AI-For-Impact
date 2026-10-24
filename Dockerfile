# ==============================================================================
# Dockerfile for AI Road Intelligence
# Compatible with both Oracle Cloud ARM64 (Ampere A1) and x86_64 (Intel/AMD)
# Optimized for CPU Inference & Microservices
# ==============================================================================

FROM python:3.10-slim

# Ngăn python tạo .pyc và buffer log output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Cài đặt các gói hệ thống cần thiết cho OpenCV, hình ảnh & video
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Cài đặt PyTorch CPU (Tối ưu kích thước nhẹ, tương thích cả x86_64 và aarch64 ARM)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 2. Copy requirements và cài đặt các dependencies còn lại
COPY requirements.txt .

# Thay thế opencv-python bằng opencv-python-headless trong môi trường container không màn hình
RUN sed -i 's/opencv-python>=/opencv-python-headless>=/g' requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

# 3. Copy toàn bộ source code vào container
COPY . .

# 4. Đảm bảo các thư mục dữ liệu, cache và weights luôn tồn tại
RUN mkdir -p weights road_segmentation/cache outputs

# Cổng mặc định
EXPOSE 8000

# Khởi chạy FastAPI với Uvicorn (nhận biến $PORT từ Cloud như Render/Koyeb nếu có, mặc định 8000)
CMD ["sh", "-c", "uvicorn camera_ai.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
