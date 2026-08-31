# Gunakan base image Python yang ringan
FROM python:3.11-slim

# Set timezone & environment variable
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Jakarta

# Buat direktori kerja di dalam container
WORKDIR /app

# Salin file requirements terlebih dahulu untuk optimasi Docker layer caching
COPY requirements.txt .

# Install dependensi (package khusus Windows akan otomatis diabaikan karena marker di requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Salin seluruh source code aplikasi ke dalam container
COPY app.py favicon.svg* icon.ico* background.jpg* image.jpg* source_icon.png* ./

# Salin script entrypoint untuk inisialisasi persistensi data & konfigurasi
COPY project-docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Buat direktori default untuk storage data dan persistent data
RUN mkdir -p /storage /app/data

# Expose port server
EXPOSE 5000

# Jalankan entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
