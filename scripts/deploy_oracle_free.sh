#!/bin/bash
# ==============================================================================
# Oracle Cloud Free Tier - 1-Click Deployment Script for AI Road Solution
# Run on Oracle Linux / Ubuntu (ARM64 Ampere A1 or AMD64)
# ==============================================================================

set -e

echo "🚀 [1/5] Cập nhật hệ thống & Cài đặt công cụ cơ bản..."
sudo apt-get update -y
sudo apt-get install -y curl git ufw iptables-persistent netfilter-persistent

echo "🔓 [2/5] Mở cổng Firewall trên máy chủ Oracle (8000, 80, 443)..."
# Oracle Cloud Ubuntu mặc định chặn mọi cổng ngoài port 22 bằng iptables nội bộ
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT || true
sudo netfilter-persistent save || true

# Mở UFW nếu ufw đang bật
if sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 8000/tcp
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
    sudo ufw reload
fi

echo "🐳 [3/5] Kiểm tra và cài đặt Docker & Docker Compose..."
if ! command -v docker &> /dev/null; then
    echo "Đang cài đặt Docker chính thức..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
fi

echo "📁 [4/5] Chuẩn bị thư mục chứa weights và cache..."
mkdir -p weights road_segmentation/cache outputs

if [ ! -f "weights/linknet_water_clean.pth" ]; then
    echo "⚠️ CHÚ Ý: Chưa tìm thấy file weights/linknet_water_clean.pth!"
    echo "Vui lòng copy file từ máy local lên server bằng lệnh:"
    echo "scp D:\\AI_road\\AI_solution\\weights\\linknet_water_clean.pth $USER@<IP_SERVER>:$(pwd)/weights/"
fi

echo "🏗️ [5/5] Build và khởi chạy Docker Container..."
sudo docker compose up -d --build

echo "=========================================================="
echo "🎉 DEPLOY THÀNH CÔNG!"
echo "Kiểm tra log bằng lệnh: sudo docker compose logs -f"
echo "Truy cập API Docs tại: http://<IP_PUBLIC_ORACLE>:8000/docs"
echo "=========================================================="
