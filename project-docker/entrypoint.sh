#!/bin/sh
set -e

# Pastikan folder data dan storage ada
mkdir -p /app/data /storage

# Jika file settings.db belum ada di persistent storage (/app/data)
if [ ! -f /app/data/settings.db ]; then
    # Jika belum ada config.ini di data, buat default config khusus Docker agar root direktori otomatis /storage
    if [ ! -f /app/data/config.ini ] && [ ! -f /app/config.ini ]; then
        cat << 'EOF' > /app/config.ini
[server]
host = 0.0.0.0
port = 5000
directory = /storage
upload_pin = 1234
allowed_extensions = .txt,.jpg,.png,.pdf,.exe,.dll,.mp4,.7z,.zip,.rar,.xls,.xlsx,.docx,.mp3,.ini
login_pin = 4321
edit_pin = 5678

[telegram]
bot_token = 
chat_id = 
enable_notification = False
EOF
    fi
    # Buat file kosong di /app/data agar symlink valid
    touch /app/data/settings.db
fi

# Pastikan file JSON shared links ada di persistent storage
if [ ! -f /app/data/shared_links.json ]; then
    echo "{}" > /app/data/shared_links.json
fi

if [ ! -f /app/data/shared_upload_links.json ]; then
    echo "{}" > /app/data/shared_upload_links.json
fi

# Hubungkan file kerja /app/ ke persistent volume /app/data via symlink
ln -sf /app/data/settings.db /app/settings.db
ln -sf /app/data/shared_links.json /app/shared_links.json
ln -sf /app/data/shared_upload_links.json /app/shared_upload_links.json

# Jalankan server
exec python app.py