# 📂 WAN File Station

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Framework](https://img.shields.io/badge/framework-Flask%20%7C%20Waitress-informational.svg)](https://flask.palletsprojects.com/)
[![Editor](https://img.shields.io/badge/editor-Monaco%20Editor-purple.svg)](https://microsoft.github.io/monaco-editor/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey.svg)]()

A lightweight, self-hosted, and secure **Web File Manager** built with **Python (Flask)** and **Tailwind CSS**. Designed for local NAS, home labs, and remote office servers, WAN File Station provides a native desktop-like file management experience in the browser with dark-themed aesthetics and responsive mobile support.

> 🚀 **Single-File Architecture** — The entire application is packaged within a single `app.py` script without external template directories. All HTML templates, stylesheets, and client-side logic are embedded as Python constants.

---

## 📑 Table of Contents

- [Key Features](#-key-features)
- [Architecture & Tech Stack](#-architecture--tech-stack)
- [Dependencies](#-dependencies)
- [Security Features](#-security--access-control)
- [Telegram Bot Integration](#-telegram-bot-integration-2fa--alerts)
- [API Endpoints & Routes](#-api-endpoints--routes)
- [Installation & Quick Start](#-installation--quick-start)
  - [Option 1: Windows Portable Executable (.exe)](#option-1-windows-portable-exe-no-python-required)
  - [Option 2: Running from Python Source](#option-2-running-from-python-source)
  - [Option 3: Linux (Ubuntu Server) Service](#option-3-linux-ubuntu-server-systemd-service)
  - [Option 4: Compiling Standalone .exe (PyInstaller)](#option-4-compiling-standalone-exe-via-pyinstaller)
- [Initial Setup Guide](#-initial-setup-guide)
- [Roadmap](#-future-roadmap)
- [License & Credits](#-license--credits)

---

## ✨ Key Features

### 📁 Advanced File Management
- **Core Operations**: Upload, download, delete, inline rename, move files between subdirectories, and create folders.
- **Chunked Uploading (5MB Partitions)**: Effortlessly upload multi-gigabyte files. Bypasses the **100MB Cloudflare Tunnel limit** (`HTTP 413 Entity Too Large`) and handles network interruptions smoothly.
- **ZIP Archive Management**: Compress selected files into ZIP archives or extract ZIP files directly on the server.
- **Bulk Operations**: Multi-select files (with `SHIFT-click` support) to perform bulk deletion or bulk compression via an interactive Floating Action Bar.
- **Automated Versioning Backup**: Uploading a file with an identical name automatically archives the existing copy with timestamp suffix `.BAK_YYYYMMDD_HHMMSS`.
- **Live Search & Filter**: Filter files inside any directory in real-time without reloading the webpage.
- **Sortable Columns**: Sort files instantly by Name, Size, or Date Modified.

### 💻 In-Browser Monaco Code Editor
- **Integrated VS Code Engine**: View, edit, and save source code and text files directly inside the browser using **Microsoft Monaco Editor**.
- **Syntax Highlighting**: Supports 14+ file extensions out-of-the-box:
  `.txt`, `.py`, `.js`, `.html`, `.css`, `.json`, `.md`, `.ini`, `.yml`, `.sh`, `.conf`, `.sql`, `.sp`, `.trigger`.

### 🎬 Media Player & Lightbox Preview
- **Integrated Media Viewer**: Preview images (`.jpg`, `.png`, `.gif`, `.webp`), stream video (`.mp4`, `.mov`, `.webm`), play audio (`.mp3`, `.wav`), and read PDF documents in theater lightbox mode without third-party plugins.

### 🔒 Security & Access Control
- **3-Tier PIN Architecture**:
  1. `Login PIN`: Required to access and view files.
  2. `Upload PIN`: Required to upload files.
  3. `Edit PIN`: Required to delete, rename, move, extract, or edit files (Edit Mode).
- **Public Sharing with QR Codes**: Generate secure, time-expiring share links for individual files or entire folders with automatic QR code generation.
- **Path Traversal & Zip Slip Protection**: Strict validation prevents path traversal attacks and extraction of malicious archives beyond the designated root directory.
- **Real Client IP Detection**: Fully compatible with reverse proxies and Cloudflare Tunnels (reads `CF-Connecting-IP`, `X-Real-IP`, and `X-Forwarded-For`).
- **File Extension Whitelisting**: Restrict allowed file extensions via settings, or leave empty to allow all file types.

### 🤖 Telegram Bot Integration (2FA & Alerts)
- **Two-Factor Authentication (OTP Login)**: Request a 6-digit one-time password delivered directly to your private Telegram chat (5-minute expiry).
- **Login Activity Alerts**: Receive real-time security alerts on Telegram for every login attempt (includes status, real IP address, browser/OS user agent, and timestamp).
- **Download Notifications**: Get notified whenever a shared public file or ZIP folder is downloaded.

### 📊 Real-Time Server Monitoring
- **System Resources Modal**: Monitor real-time CPU utilization, RAM usage, and disk partition capacities via interactive doughnut charts.
- **Network Interface Telemetry**: Inspect network adapter configurations (IPv4, MAC, Subnet Mask, CIDR, and Default Gateway).
- **Active Network Connections**: View active network sockets and connected remote hosts.
- **Windows Event Log**: Inspect system shutdown/reboot history (Event ID 1074 on Windows).

### ⚙️ Web Settings Dashboard (SQLite)
- Built-in management interface to modify port, storage directories, security PINs, extension whitelists, and Telegram bot credentials with real-time hot-reloading.

---

## 🏗️ Architecture & Tech Stack

| Component | Technology / Library | Description |
|-----------|----------------------|-------------|
| **Backend** | Python 3.9+, Flask, Waitress | High-performance WSGI production server |
| **Database** | SQLite 3 (`settings.db`) | Zero-configuration local database for settings |
| **Frontend** | Tailwind CSS (CDN), Bootstrap Icons | Modern responsive dark UI |
| **Code Editor** | Monaco Editor v0.46.0 (CDN) | In-browser VS Code syntax highlighting |
| **Charts** | Chart.js (CDN) | Interactive storage and telemetry charts |
| **System Telemetry**| `psutil` | CPU, RAM, disk, and network connection metrics |
| **System Tray** | `pystray` + `Pillow` | Windows taskbar tray icon integration |
| **Notification** | Telegram Bot API | Asynchronous HTTP notification dispatch |

---

## 📦 Dependencies

All required Python libraries are listed in `requirements.txt`:

```text
flask>=3.0.0
waitress>=2.1.2
psutil>=5.9.0
pillow>=10.0.0
pystray>=0.19.5
pywin32>=306; sys_platform == 'win32'
```

---

## 🤖 Telegram Bot Integration (2FA & Alerts)

### Features
1. **Login Security Alerts**: Sends an instant alert with user device details, IP address, and status whenever someone tries to log in.
2. **Telegram 2FA OTP**: Enables passwordless / secure login via a 6-digit OTP code sent to your Telegram account.
3. **Download Alerts**: Instant notification when a public file link is accessed.

### Setup Instructions
1. Open Telegram and search for **`@BotFather`**.
2. Send `/newbot`, follow the prompts, and copy the generated **Bot Token** (e.g., `123456789:ABCdefGhIJKlmNoPQRstuVWXyz`).
3. Search for **`@userinfobot`** or **`@getidsbot`** to retrieve your numerical **Chat ID** (e.g., `987654321`).
4. Log in to File Station, toggle **Edit Mode**, and click the **Settings (⚙️)** icon in the top header.
5. Enable **Telegram Notifications**, enter your `Bot Token` and `Chat ID`, and click **Save Settings**.

---

## 📡 API Endpoints & Routes

| HTTP Method | Route | Access | Description |
|-------------|-------|--------|-------------|
| `GET, POST` | `/login` | Public | Authentication endpoint (supports PIN & Telegram OTP) |
| `GET` | `/logout` | Public | Clears session and redirects to login |
| `POST` | `/request_telegram_otp` | Public (AJAX) | Generates and sends a 6-digit OTP via Telegram |
| `GET` | `/` or `/<path:req_path>` | `@login_required` | File list, directory browser, and file preview handler |
| `POST` | `/upload` | `@login_required` | Direct single-file upload with PIN verification |
| `POST` | `/upload_chunk` | `@login_required` | 5MB chunked upload handler for large files |
| `POST` | `/delete` | `@login_required` | Delete file or directory |
| `POST` | `/bulk_delete` | `@login_required` | Batch delete selected files |
| `POST` | `/rename` | `@login_required` | Rename file or folder |
| `POST` | `/new_folder` | `@login_required` | Create new directory |
| `POST` | `/move` | `@login_required` | Move file/folder to another destination |
| `POST` | `/compress` | `@login_required` | Compress file/folder into a ZIP archive |
| `POST` | `/bulk_compress` | `@login_required` | Compress selected items into a single ZIP archive |
| `POST` | `/extract` | `@login_required` | Extract ZIP archive with Zip-Slip protection |
| `POST` | `/generate_share_link` | `@login_required` | Create public tokenized share link for file/folder |
| `GET` | `/share/<token>` | Public | Browse or preview a shared file/folder |
| `GET` | `/download/<token>` | Public | Direct download for shared file |
| `GET` | `/zip_folder/<token>` | Public | Download entire shared folder as ZIP |
| `POST` | `/api/save_text` | `@login_required` | Save file content from Monaco Editor |
| `GET` | `/system_stats` | `@login_required` | Returns CPU, RAM, Disk, and Connection metrics |
| `GET` | `/network_config` | `@login_required` | Returns network interface adapters and gateway info |
| `GET` | `/get_folder_list` | `@login_required` | Returns hierarchical directory tree for move modal |
| `GET, POST` | `/settings` | `@login_required` (Edit Mode) | Web configuration dashboard |

---

## 🚀 Installation & Quick Start

### Option 1: Windows Portable `.exe` (No Python Required)
1. Copy `FileStation.exe` into your desired folder.
2. *(Optional)* Place an `image.jpg` in the same directory for a custom background wallpaper.
3. Double-click `FileStation.exe`.
4. The server will run in the background, display a **System Tray Icon** in the taskbar, and serve the dashboard at `http://localhost:5000`.

---

### Option 2: Running from Python Source

#### Windows
1. Ensure **Python 3.9+** is installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   python app.py
   ```

#### Linux / macOS
1. Install Python virtual environment and dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Run the application:
   ```bash
   python3 app.py
   ```

---

### Option 3: Linux (Ubuntu Server) Systemd Service
Automate the deployment as a 24/7 background service on Ubuntu Server:

1. Grant execute permissions:
   ```bash
   chmod +x deploy.sh
   ```
2. Run the deployment script:
   ```bash
   sudo ./deploy.sh
   ```
3. The script configures Python, virtual environments, UFW firewall, and enables the `file-station.service` systemd daemon automatically. Access the dashboard via `http://<SERVER-IP>:5000`.

---

### Option 4: Compiling Standalone `.exe` via PyInstaller
To package `app.py` into a single, self-contained Windows executable:

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name "FileStation" --icon=icon.ico --collect-all psutil --collect-all waitress --hidden-import=psutil --hidden-import=waitress --hidden-import=pystray --hidden-import=PIL --hidden-import=win32api --hidden-import=flask --hidden-import=sqlite3 --clean --noconfirm app.py
```
The compiled executable will be generated at `dist/FileStation.exe`.

---

## 🔑 Initial Setup Guide

When running File Station for the first time, use the default credentials:

| Credential | Default Value | Description |
|---|:---:|---|
| **Login PIN** | `4321` | Enter at `/login` to view and browse files |
| **Upload PIN** | `1234` | Required when uploading files directly |
| **Edit PIN** | `5678` | Required to activate **Edit Mode** and modify files |

> ⚠️ **Security Recommendation**: After logging in, immediately activate **Edit Mode** (pencil icon), navigate to **Settings (⚙️)**, and change all default PINs to strong, unique numbers.

---

## 🗺️ Future Roadmap

- [ ] **Download Rate Limiting**: Bandwidth throttling for public share links.
- [ ] **Audit Trail & Activity Log**: Dedicated web UI log tracking administrative actions (Upload, Delete, Rename, Login) with IP logs.
- [ ] **Global Drag & Drop Upload**: Fullscreen dropzone for uploading files effortlessly.
- [ ] **macOS-Style Context Menu**: Custom right-click floating menu for file actions.
- [ ] **Global Recursive Search**: Instant search across all nested subdirectories.
- [ ] **Recycle Bin (Soft Delete)**: Trash retention system to restore accidentally deleted files.
- [ ] **Full Directory Uploading**: Upload complete folder trees preserving nested subfolder structures.

---

## 📜 License & Credits

Distributed under the MIT License. See `LICENSE` for more information.

### Acknowledgments & Libraries
- **[Flask](https://flask.palletsprojects.com/)** — Web Framework
- **[Waitress](https://docs.pylonsproject.org/projects/waitress/)** — Production WSGI Server
- **[Monaco Editor](https://microsoft.github.io/monaco-editor/)** — Microsoft Code Editor
- **[Tailwind CSS](https://tailwindcss.com/)** — Utility-first CSS Framework
- **[Chart.js](https://www.chartjs.org/)** — Data Visualization
- **[Bootstrap Icons](https://icons.getbootstrap.com/)** — UI Icon Library
- **[psutil](https://github.com/giampaolo/psutil)** — Cross-Platform System Process Utilities
