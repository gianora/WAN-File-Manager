# File Station - Docker Setup

Panduan menjalankan aplikasi **File Station** menggunakan Docker & Docker Compose.

---

## 🚀 Cara Menjalankan

1. Masuk ke folder `project-docker`:
   ```bash
   cd project-docker
   ```

2. Jalankan container di background:
   ```bash
   docker compose up -d --build
   ```

3. Buka browser dan akses:
   ```
   http://localhost:5000
   ```

---

## 📁 Mengatur Folder yang Ingin Di-share

Buka file `docker-compose.yml`, lalu sesuaikan bagian `volumes:`

```yaml
volumes:
  # Ubah "D:/FolderAnda" sesuai lokasi folder di komputer Anda
  - "D:/FolderAnda:/storage"
```

Lalu restart container:
```bash
docker compose up -d
```

---

## ⚙️ Perintah Berguna Lainnya

* **Melihat log container:**
  ```bash
  docker compose logs -f
  ```

* **Menghentikan container:**
  ```bash
  docker compose down
  ```

* **Restart container:**
  ```bash
  docker compose restart
  ```
