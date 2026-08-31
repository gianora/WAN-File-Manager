# 🛠️ Laporan Perbaikan Bug & Rencana Pengembangan Fitur (Roadmap)
**Aplikasi**: File Station Web Server  
**Tanggal**: 7 Agustus 2026  

---

## 📌 1. Laporan Perbaikan Bug (Point #1: Inline Rename)

### **Masalah Utama**
Fitur ubah nama berkas/folder secara langsung (*inline rename*) melalui antarmuka web mengalami kegagalan. Ketika pengguna mencoba mengubah nama file/folder, sistem mengembalikan error `"Operation not allowed"` atau `"File or directory not found"`.

### **Akar Penyebab (Root Cause)**
1. **Front-End (`triggerRename`)**:
   - Fungsi `triggerRename` mengambil jalur berkas dari atribut `href` elemen `<a>` (misalnya `/subfolder/file.txt`).
   - Kode awal mencoba menghapus string `/?req_path=`, namun format ini tidak ada di URL bawaan Flask. Akibatnya, `filePath` yang dikirim ke server diawali dengan karakter garis miring `/`.
2. **Back-End (`rename_file` & Python Windows Behavior)**
   - Di sistem operasi Windows, fungsi `os.path.join("C:/shared_files", "/subfolder/file.txt")` membuang path pertama (`C:/shared_files`) dan langsung mengarah ke `C:\subfolder\file.txt` di tingkat root drive.
   - Hal ini membuat sistem menganggap operasi mencoba mengakses di luar folder `directory` yang diizinkan (`startswith` bernilai `False`).

3. **Redirect Folder Terhapus/Ter-rename (Trailing Slash Bug)**:
   - Ketika mengubah nama folder (misal `folder_A/`), `os.path.dirname("folder_A/")` mengembalikan `"folder_A"` (folder itu sendiri), bukan folder induknya (`""`).
   - Hal ini membuat server meredirect ke jalur lama yang sudah tidak ada sehingga tampilan web tidak langsung ter-refresh otomatis saat menekan Enter.

### **Perubahan & Perbaikan yang Diterapkan**
* **Front-End (`app.py`)**:
  - Memperbarui fungsi `handleRename` dan `triggerRename` untuk secara otomatis membersihkan awalan garis miring (`filePath.replace(/^\\/+/, '')`).
  - Memperbaiki escape sequence regex (`^\\/+`) untuk menghilangkan `SyntaxWarning` pada Python 3.12+.
* **Back-End (`app.py`)**:
  - Memperbarui route `@app.route("/rename")` dan `@app.route("/delete")` dengan pembersihan garis miring ganda di awal dan di akhir parameter `file_path`:
    ```python
    old_file_rel = (request.form.get("file_path") or "").lstrip("/\\").rstrip("/\\")
    ```
  - Memperbaiki perhitungan folder induk (`current_path_rel = os.path.dirname(old_file_rel).replace(os.sep, "/")`) agar pengalihan halaman (*redirect path*) kembali ke folder induk yang tepat sehingga antarmuka web langsung ter-refresh seketika saat tombol Enter ditekan.

---

## 📌 2. Laporan Perbaikan Bug (Point #3: Filter Ekstensi di Shared Folder Download)

### **Masalah Utama**
Sebelumnya, ketika sebuah folder dibagikan secara publik (`/share/<token>`), berkas di dalam subfolder dapat diunduh langsung via `/download/<token>/<path:filename>` atau diunduh sebagai file `.zip` tanpa pengecekan `ALLOWED_EXTENSIONS`. Pengguna publik dapat mengunduh file dengan ekstensi terlarang (seperti `.py`, `.env`, `.ini`, dll).

### **Perubahan & Perbaikan yang Diterapkan**
1. **Penyaringan Tampilan (`public_share`)**:
   - Berkas di dalam folder yang dibagikan yang tidak sesuai dengan `ALLOWED_EXTENSIONS` otomatis **disembunyikan** dari tampilan UI publik.
2. **Validasi Unduhan Langsung (`download_from_folder`)**:
   - Menambahkan pengecekan `ALLOWED_EXTENSIONS` pada route `/download/<token>/<path:filename>`. Jika ekstensi tidak diizinkan, akses ditolak dengan HTTP 403 Forbidden.
3. **Penyaringan Kompresi (`zip_folder`)**:
   - Berkas dengan ekstensi terlarang otomatis dilewati (*skipped*) dan tidak dimasukkan ke dalam arsip `.zip` saat publik mengunduh *Shared Folder* sebagai ZIP.

---

## 📌 3. Laporan Perbaikan Bug (Point #6: Pembersihan Otomatis Disk Leak Chunked Upload)

### **Masalah Utama**
Proses pengunggahan file berukuran besar dilakukan dengan cara memecah file menjadi pecahan-*chunk* 5MB di folder `.upload_temp/<upload_id>`. Jika pengunggahan terhenti di tengah jalan (misal karena jaringan terputus atau pengguna menutup halaman web), berkas `.part` sementara tersebut akan tertinggal selamanya dan memenuhi ruang penyimpanan disk (*disk leak*).

### **Perubahan & Perbaikan yang Diterapkan**
1. **Background Scheduler Thread (`start_temp_cleanup_scheduler`)**:
   - Menambahkan *daemon thread* yang berjalan otomatis secara berkala di latar belakang setiap 1 jam.
2. **Pembersihan Berkas Usang (`cleanup_temp_uploads`)**:
   - Memeriksa folder sementara `.upload_temp` dan secara otomatis menghapus folder unggahan yang tidak rampung jika usianya sudah melebihi 12 jam.

---

## 🗺️ 4. Rencana Pengembangan & Pembaruan Fitur Kedepannya (Roadmap)

### **Fase 1: Perbaikan Keamanan & Stabilitas Utama (Next Priority)**
- [x] **Validasi Strict Path Boundary** (Selesai pada 7 Agustus 2026):
  - Mengganti semua pengecekan `abs_path.startswith(directory)` menggunakan `os.path.commonpath([abs_path, directory]) == directory` untuk mencegah kerentanan Path Traversal pada folder berawalan serupa.
- [x] **Filter Ekstensi di Shared Folder Download** (Selesai pada 7 Agustus 2026):
  - Validasi `ALLOWED_EXTENSIONS` pada route `/download/<token>/<path:filename>`, listing folder publik, dan pengarsipan `.zip`.
- [x] **Pembersihan Otomatis Berkas Sampah Chunked Upload** (Selesai pada 7 Agustus 2026):
  - Menambahkan *background scheduler thread* yang membersihkan folder `.upload_temp` usang (>12 jam) secara berkala.
- [x] **Proteksi Exception pada `get_file_info`** (Selesai pada 7 Agustus 2026):
  - Menambahkan penanganan `try-except` pada pemanggilan `os.path.getsize` untuk mencegah HTTP 500 jika ada file yang sedang dikunci (*locked*) oleh OS.

### **Fase 2: Peningkatan Pengalaman Pengguna (UX & Features)**
- [ ] **Sistem Batch / Multi-Select Operations**:
  - Memungkinkan pengguna memilih (*checkbox*) beberapa berkas/folder sekaligus untuk dihapus, dipindahkan, atau dikompresi menjadi satu file `.zip`.
- [ ] **Media Preview Modal**:
  - Menambahkan preview lansung di browser untuk gambar (`.png`, `.jpg`), dokumen PDF, dan pemutar video (`.mp4`) tanpa perlu mengunduh terlebih dahulu.
- [ ] **Batas Kuota & Kecepatan Unduh (Rate Limiting)**:
  - Fitur pengatur kecepatan unduh untuk pembagian tautan publik agar tidak menghabiskan bandwidth server.
- [ ] **Audit Trail Log Aktivitas Web**:
  - Menyimpan catatan riwayat aktivitas pengguna (Upload, Delete, Rename, Share) ke dalam file log terstruktur di dashboard admin.

---
