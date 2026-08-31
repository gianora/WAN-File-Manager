# Catatan Perubahan (Changelog) File Station

Berikut adalah rekap dari seluruh perubahan dan peningkatan fitur yang telah dilakukan pada aplikasi **File Station** melalui sesi modifikasi UI:

## 9. Migrasi Database Pengaturan (Web Settings Dashboard)
* **SQLite Backend**: Meninggalkan konfigurasi kuno berbasis file teks (`config.ini`) sepenuhnya dan beralih menggunakan Database SQLite (`settings.db`) yang jauh lebih modern, aman, dan tanpa instalasi tambahan.
* **Auto-Migration Script**: Sistem kini dibekali logika pintar yang mampu menyedot konfigurasi lama milik pengguna dan memindahkannya ke Database SQLite secara otomatis tanpa risiko kehilangan data.
* **Dashboard Settings UI**: Menambahkan halaman khusus berdesain *Glassmorphism* yang elegan, yang hanya dapat diakses menggunakan *Edit PIN*. Halaman ini memungkinkan Admin untuk mengganti Folder tujuan, mengubah Port, PIN Akses, Ekstensi File, serta Setup Bot Telegram secara visual dan *real-time* langsung dari peramban web (tanpa perlu membuka SSH/Terminal Server).

## 8. Pembaruan Ikon & Estetika (Favicon)
* Mengganti logo *globe/bola dunia* bawaan peramban web pada bagian *tab browser* (*Favicon*) menjadi logo kardus elegan (*Box Seam Fill*).
* Melakukan penyesuaian warna ikon secara mendetail menggunakan palet "Discord Blurple" (`#5865f2`) agar senada dan harmonis dengan palet *UI Glassmorphism* aplikasi.


## 7. Glassmorphism Menyeluruh & macOS Aesthetic (Update Terbaru)
* **Sistem Wallpaper Global**: Aplikasi sekarang merender gambar lokal `image.jpg` sebagai latar belakang *fullscreen* yang memukau untuk seluruh halaman (Dashboard, Login, Upload Request, Share File, Share Folder).
* **Efek Transparansi Kaca**: Komponen-komponen utama kini menggunakan CSS `backdrop-filter: blur(25px)` dengan *background* tembus pandang yang elegan. Ditambah lapisan pelindung (*overlay*) di atas wallpaper agar teks tetap terbaca kontras walau dengan wallpaper terang.
* **macOS Custom Scrollbar**: *Scrollbar* bawaan OS yang tebal dihilangkan, diganti dengan *scrollbar* kustom tipis yang melayang menyerupai milik perangkat Apple.
* **Ilustrasi Folder Kosong (*Empty State*)**: Menambahkan ilustrasi grafis ikon kardus terbuka transparan ketika suatu direktori tidak memiliki file, menggantikan baris tabel kosong yang membosankan.
* **Auto-Download Paksa**: Memperbaiki rute tautan *Share* agar file (seperti gambar/PDF/video) tidak terbuka langsung di *tab browser*, namun langsung dipaksa terunduh (*Save As*) melalui implementasi `as_attachment=True`.
* **Navigasi Breadcrumbs**: Mengganti teks statis pada *path bar* menjadi tombol navigasi *breadcrumbs* berlapis layaknya struktur direktori macOS Finder (dimulai dengan label "Shared Root"), memungkinkan navigasi mundur seketika tanpa harus klik tombol kembali berkali-kali.
* **Glassmorphic Lightbox Preview**: Menambahkan fitur penampil gambar dan PDF *native*. Mengklik file gambar atau PDF dari dalam aplikasi utama tidak lagi memaksanya terunduh, melainkan memunculkan pratinjau (*preview*) ukuran penuh di tengah layar (*overlay*) dengan latar belakang *blur*. Fitur ini diamankan dengan parameter khusus (`?preview=1`) dan sengaja dikecualikan dari halaman "Share" agar pengunjung publik tetap diwajibkan mengunduh file.
* **Penyelarasan Desain Modal**: Menyempurnakan tampilan antarmuka jendela *popup* (seperti menu System Resources dan Network) agar sejalan dengan tema global, mengubahnya dari warna *solid* menjadi panel tembus pandang (*glassmorphism*) yang elegan.


## 1. Tema Glassmorphism & Responsivitas Tampilan
* **Desain macOS Glassmorphism:** Merombak total tampilan antarmuka (UI) menggunakan tema *dark mode glassmorphism* yang elegan. Memanfaatkan efek blur transparan (*backdrop-filter*) yang menawan dengan warna *background* yang lembut dan premium.
* **Tampilan Mobile:** Melakukan optimasi responsivitas seluruh *layout* (mulai dari list tabel, *toolbar*, input, hingga menu-menu modal) agar tampil sempurna saat diakses menggunakan layar smartphone.

## 2. Menu Aksi Radial (Pie Menu) untuk Perangkat Mobile
* Mengganti deretan tombol aksi (buka, download, ganti nama, kompres, hapus) yang boros tempat di layar HP dengan satu tombol *trigger* bergaya minimalis (titik tiga vertikal).
* **Efek Animasi:** Ketika tombol *trigger* diklik, tombol-tombol aksi akan memancar ke luar dengan animasi dinamis membentuk setengah lingkaran (gaya *pie menu*).
* **Perbaikan *Clipping* Menu:** Memperbaiki insiden animasi menu yang terpotong oleh batasan wadah *scroll* (`overflow`). Kini menu mengambang di atas semua elemen (`position: fixed`) dan mendeteksi kordinat layar secara otomatis.
* **Tutup Otomatis:** Menu pie akan menutup otomatis apabila pengguna menggeser (*scroll*) daftar file ke atas atau ke bawah.

## 3. Penghapusan Ikon Tombol macOS Traffic Light
* Tiga buah ikon peluru hiasan bergaya macOS (merah, kuning, hijau) di pojok kiri atas judul telah dihilangkan secara global di seluruh tampilan (desktop maupun seluler) demi menciptakan nuansa yang lebih bersih.

## 4. Perbaikan Fungsionalitas Klik & Fitur Rename
* **Anti Salah Klik:** Menonaktifkan pemicu otomatis ubah nama (*auto-rename*) ketika pengguna secara tidak sengaja mengklik area teks nama folder atau file. Klik kini hanya akan mengeksekusi navigasi (membuka folder/file).
* **Tombol Eksekusi Spesifik:** Prose penamaan ulang (*rename*) sekarang wajib diinisiasi melalui tombol aksi spesifik bergambar ikon pensil/edit.

## 5. Rombak Total Fitur Statistik Folder (Stats)
* **Perbaikan *Bug* Perhitungan Direktori:** Memperbaiki sistem *error* yang mencegah grafik muncul saat berada di *sub-folder*. Sistem kini dengan cerdas mengabaikan tombol direktori mundur (`.. Back`) dalam kalkulasinya.
* **Tiga Kartu Ringkasan:** Mengimplementasikan kartu-kartu ringkasan visual yang menarik di sisi atas yang mengkalkulasi dan menyajikan statistik instan untuk: **Total Files**, **Total Folders**, dan **Total Size (MB)**.
* **Tampilan Grafik yang Cantik:** Merombak rasio desain *Doughnut Chart* (berbasis `Chart.js`) menjadi lebih modern (`cutout: '75%'`), meniadakan batas antar potongan pie, dan mengganti warna bawaan dengan 10 warna cerah (*vibrant neon palette*) yang tampak sangat premium di atas kanvas bermode gelap. Posisi legenda kini juga disusun rapi ke sebelah kanan (atau di bawah, di layar HP).

## 6. Implementasi Pencarian Pintar (*Live Smart Search*)
* Merubah input "Filter files..." statis menjadi sebuah pencarian pintar berbasis acara (event).
* Daftar file dan folder kini bereaksi seketika (*live feedback*) tanpa me-muat ulang halaman saat pengguna mengetik huruf apa pun. Teks atau item yang tidak relevan akan menghilang secara instan.
