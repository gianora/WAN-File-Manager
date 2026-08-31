#!/bin/bash
# Skrip Otomatis Deployment WAN File Station di Ubuntu Server

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
TARGET_DIR="/opt/file-station"

echo "=================================================="
echo "        Deployment WAN File Station               "
echo "=================================================="

# 1. Update paket & instal dependensi dasar
echo "[1/4] Menginstal dependensi Python dan mengonfigurasi firewall..."
sudo apt update -y
sudo apt install -y python3 python3-pip python3-venv

# 2. Siapkan direktori aplikasi di /opt/file-station
echo "[2/4] Menyiapkan direktori aplikasi dari $CURRENT_DIR ke $TARGET_DIR..."
sudo mkdir -p $TARGET_DIR
sudo cp -r $CURRENT_DIR/* $TARGET_DIR/

# 3. Setup Virtual Environment & Install Requirements
echo "[3/4] Menyiapkan Python Virtual Environment..."
cd $TARGET_DIR
sudo python3 -m venv venv
sudo ./venv/bin/pip install -r requirements.txt

# 4. Pasang Systemd Service agar berjalan otomatis di latar belakang & saat reboot
echo "[4/4] Mengonfigurasi Systemd Service (Autostart)..."
sudo cp $TARGET_DIR/file-station.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable file-station
sudo systemctl restart file-station

# Konfigurasi UFW (Firewall) untuk mengizinkan port 5000
sudo ufw allow 5000/tcp > /dev/null 2>&1

# 5. Verifikasi Hasil
IP_ADDR=$(hostname -I | tr ' ' '\n' | grep -E '^(192\.168\.|10\.)' | head -n 1)
if [ -z "$IP_ADDR" ]; then
    IP_ADDR=$(hostname -I | awk '{print $1}')
fi
echo "=================================================="
echo " STATUS SERVICE: $(systemctl is-active file-station)"
echo " AKSES DASHBOARD LAN: http://${IP_ADDR}:5000"
echo "=================================================="
