import configparser
import sqlite3
import os
import sys
import threading
import logging
import time
import shutil
import platform
import psutil
import re
from flask import Flask, send_from_directory, render_template_string, request, redirect, url_for, abort, session, flash, jsonify, Response
from werkzeug.utils import secure_filename
from waitress import serve
from PIL import Image
from functools import wraps
import zipfile
import stat
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
import html
import socket 
import struct
import ipaddress 
import subprocess 
from datetime import datetime, timedelta
if platform.system() == "Windows":
    import winreg 

# Setup logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

MAX_FILE_SIZE_MB = 10000
ENABLE_PUBLIC_SHARE = True 

def get_app_dir():
    """Mengembalikan path direktori asli tempat file .exe atau script .py berada."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
DB_FILE = os.path.join(APP_DIR, "settings.db")

def get_resource_path(relative_path):
    """Mencari file resource: prioritas di APP_DIR, fallback ke bundle PyInstaller (_MEIPASS)."""
    local_path = os.path.join(APP_DIR, relative_path)
    if os.path.exists(local_path):
        return local_path
    if hasattr(sys, '_MEIPASS'):
        bundle_path = os.path.join(sys._MEIPASS, relative_path)
        if os.path.exists(bundle_path):
            return bundle_path
    return local_path

def is_safe_path(base_dir, path, follow_symlinks=True):
    """Memeriksa apakah path berada di dalam base_dir untuk mencegah Directory Traversal."""
    try:
        if follow_symlinks:
            matchpath = os.path.realpath(path)
            base = os.path.realpath(base_dir)
        else:
            matchpath = os.path.abspath(path)
            base = os.path.abspath(base_dir)
        return base == matchpath or matchpath.startswith(base + os.sep)
    except Exception:
        return False
def get_secure_filename(filename):
    """Sanitasi nama file dari Path Traversal (CWE-22) dengan fallback karakter UTF-8."""
    if not filename:
        return ""
    clean = filename.replace("\x00", "").replace("\\", "/")
    base = os.path.basename(clean).strip()
    sec = secure_filename(base)
    _, orig_ext = os.path.splitext(base)
    if sec and (not orig_ext or sec.endswith(orig_ext.lower())):
        return sec
    safe_chars = re.sub(r'[\/\\:\*\?\"<>\|\x00-\x1f]', '_', base)
    while '..' in safe_chars:
        safe_chars = safe_chars.replace('..', '')
    return safe_chars.strip(' ._')

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_and_migrate():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    
    cursor = conn.execute("SELECT COUNT(*) FROM settings")
    count = cursor.fetchone()[0]
    
    if count == 0 and os.path.exists("config.ini"):
        logging.info("Migrating from config.ini to SQLite...")
        config = configparser.ConfigParser()
        config.read("config.ini")
        
        default_settings = {
            "host": config.get("server", "host", fallback="0.0.0.0"),
            "port": str(config.getint("server", "port", fallback=5000)),
            "directory": config.get("server", "directory", fallback="shared_files"),
            "upload_pin": config.get("server", "upload_pin", fallback="1234"),
            "login_pin": config.get("server", "login_pin", fallback="4321"),
            "edit_pin": config.get("server", "edit_pin", fallback="5678"),
            "allowed_extensions": config.get("server", "allowed_extensions", fallback=".txt,.jpg,.png"),
            "tg_bot_token": config.get("telegram", "bot_token", fallback="").strip().strip('"').strip("'"),
            "tg_chat_id": config.get("telegram", "chat_id", fallback="").strip().strip('"').strip("'"),
            "tg_enabled": str(config.getboolean("telegram", "enable_notification", fallback=False))
        }
        for k, v in default_settings.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()
        try:
            os.rename("config.ini", "config.ini.bak")
        except Exception as e:
            logging.error(f"Failed to rename config.ini: {e}")
            
    elif count == 0:
        logging.info("Initializing fresh SQLite DB...")
        default_settings = {
            "host": "0.0.0.0",
            "port": "5000",
            "directory": "shared_files",
            "upload_pin": "1234",
            "login_pin": "4321",
            "edit_pin": "5678",
            "allowed_extensions": ".txt,.jpg,.png,.pdf,.exe,.dll,.mp4,.7z,.zip,.rar,.xls,.xlsx,.docx",
            "tg_bot_token": "",
            "tg_chat_id": "",
            "tg_enabled": "False"
        }
        for k, v in default_settings.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()
    conn.close()

def get_setting(key, fallback=""):
    try:
        conn = get_db_connection()
        cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row['value']
    except Exception as e:
        logging.error(f"DB Error get_setting: {e}")
    return fallback

def set_setting(key, value):
    try:
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"DB Error set_setting: {e}")

init_db_and_migrate()

host = get_setting("host", "0.0.0.0")
port = int(get_setting("port", "5000"))
raw_dir = get_setting("directory", "shared_files").strip()
directory = raw_dir if raw_dir else "shared_files"
upload_pin = get_setting("upload_pin", "1234")
login_pin = get_setting("login_pin", "4321")
edit_pin = get_setting("edit_pin", "5678")
raw_extensions = get_setting("allowed_extensions", ".txt,.jpg,.png")
ALLOWED_EXTENSIONS = set(ext.strip().lower() for ext in raw_extensions.split(",") if ext.strip().startswith("."))

tg_bot_token = get_setting("tg_bot_token", "")
tg_chat_id = get_setting("tg_chat_id", "")
tg_enabled = get_setting("tg_enabled", "False").lower() in ["true", "1", "yes"]

directory = os.path.abspath(directory)
os.makedirs(directory, exist_ok=True)

def reload_global_settings():
    global host, port, directory, upload_pin, login_pin, edit_pin, raw_extensions, ALLOWED_EXTENSIONS
    global tg_bot_token, tg_chat_id, tg_enabled
    
    host = get_setting("host", "0.0.0.0")
    port = int(get_setting("port", "5000"))
    raw_dir = get_setting("directory", "shared_files").strip()
    directory = raw_dir if raw_dir else "shared_files"
    upload_pin = get_setting("upload_pin", "1234")
    login_pin = get_setting("login_pin", "4321")
    edit_pin = get_setting("edit_pin", "5678")
    raw_extensions = get_setting("allowed_extensions", ".txt,.jpg,.png")
    ALLOWED_EXTENSIONS = set(ext.strip().lower() for ext in raw_extensions.split(",") if ext.strip().startswith("."))
    
    tg_bot_token = get_setting("tg_bot_token", "")
    tg_chat_id = get_setting("tg_chat_id", "")
    tg_enabled = get_setting("tg_enabled", "False").lower() in ["true", "1", "yes"]
    
    directory = os.path.abspath(directory)
    os.makedirs(directory, exist_ok=True)

def is_allowed_extension(ext, allow_zip=False):
    ext = ext.lower()
    if allow_zip and ext == '.zip':
        return True
    if not ALLOWED_EXTENSIONS:
        return True
    return ext in ALLOWED_EXTENSIONS

def cleanup_temp_uploads(max_age_hours=12):
    """Cleans up orphaned chunked upload folders older than max_age_hours."""
    temp_dir = os.path.join(directory, ".upload_temp")
    if not os.path.exists(temp_dir):
        return

    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    cleaned_count = 0

    try:
        for item in os.listdir(temp_dir):
            item_path = os.path.join(temp_dir, item)
            if os.path.isdir(item_path):
                mtime = os.path.getmtime(item_path)
                if mtime < cutoff:
                    shutil.rmtree(item_path, ignore_errors=True)
                    cleaned_count += 1
        if cleaned_count > 0:
            logging.info(f"Cleaned up {cleaned_count} orphaned temporary upload folder(s).")
    except Exception as e:
        logging.error(f"Error during temporary upload cleanup: {e}")

def start_temp_cleanup_scheduler(interval_seconds=3600, max_age_hours=12):
    def _loop():
        while True:
            cleanup_temp_uploads(max_age_hours=max_age_hours)
            time.sleep(interval_seconds)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

start_temp_cleanup_scheduler()

app = Flask(__name__)

def get_or_create_secret_key():
    key = get_setting("flask_secret_key", "")
    if not key:
        key = secrets.token_hex(32)
        set_setting("flask_secret_key", key)
    return key

app.secret_key = get_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

def get_real_client_ip():
    """Mencoba mendapatkan IP asli user di balik Proxy/Cloudflare"""
    try:
        if request.headers.get('CF-Connecting-IP'):
            return request.headers.get('CF-Connecting-IP')
        if request.headers.get('X-Real-IP'):
            return request.headers.get('X-Real-IP')
        if request.headers.get('X-Forwarded-For'):
            return request.headers.get('X-Forwarded-For').split(',')[0].strip()
        return request.remote_addr
    except Exception:
        return request.remote_addr

def send_telegram_notification(filename, remote_ip):
    if not tg_enabled or not tg_bot_token or not tg_chat_id:
        return        

    def _send():
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Sanitasi input
            safe_filename = html.escape(filename)
            safe_ip = html.escape(remote_ip)

            message = (
                f"🚨 <b>File Download Alert!</b>\n\n"
                f"📂 <b>File:</b> {safe_filename}\n"
                f"🌍 <b>IP:</b> {safe_ip}\n"
                f"⏰ <b>Time:</b> {timestamp}\n"
                f"⚠️ <i>Activity monitored.</i>"
            )
            
            url = f"https://api.telegram.org/bot{tg_bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": tg_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }).encode("utf-8")
            
            req = urllib.request.Request(url, data=data) 
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.getcode() == 200:
                    logging.info(f"Telegram notification sent for {filename}")
        
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8')
            logging.error(f"Telegram API Failed (HTTP {e.code}): {err_msg}")
        except Exception as e:
            logging.error(f"Failed to send Telegram notification: {e}")

    threading.Thread(target=_send, daemon=True).start()


def send_login_notification(status, remote_ip, browser_info="N/A"):
    """Sends a Telegram notification regarding login access details"""
    if not tg_enabled or not tg_bot_token or not tg_chat_id:
        return

    def _send():
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            safe_ip = html.escape(remote_ip)
            safe_browser = html.escape(browser_info)
            icon = "✅" if status == "SUCCESS✅" else "⚠️"
            
            # Focused on browser access location/identity
            message = (
                f"{icon} <b>Login Activity Alert!</b>\n\n"
                f"👤 <b>Status:</b> {status}\n"
                f"🌍 <b>Access IP:</b> {safe_ip}\n"
                f"📱 <b>Browser/OS:</b> {safe_browser}\n"
                f"⏰ <b>Access Time:</b> {timestamp}\n"
                f"⚠️ <i>Security Monitoring Active</i>"
            )
            
            url = f"https://api.telegram.org/bot{tg_bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": tg_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }).encode("utf-8")
            
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.getcode() == 200:
                    logging.info(f"Login notification ({status}) sent.")
        except Exception as e:
            logging.error(f"Failed to send Login notification: {e}")

    threading.Thread(target=_send, daemon=True).start()

def send_telegram_otp(otp, remote_ip, browser_info="N/A"):
    """Sends an OTP code via Telegram bot"""
    if not tg_enabled or not tg_bot_token or not tg_chat_id:
        return False

    def _send():
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            safe_ip = html.escape(remote_ip)
            
            message = (
                f"🔑 <b>Login OTP Request</b>\n\n"
                f"Your security code is: <code>{otp}</code>\n"
                f"Valid for 5 minutes.\n\n"
                f"🌍 <b>IP:</b> {safe_ip}\n"
                f"⏰ <b>Time:</b> {timestamp}\n"
                f"<i>If you did not request this, please secure your account.</i>"
            )
            
            url = f"https://api.telegram.org/bot{tg_bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": tg_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }).encode("utf-8")
            
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.getcode() == 200:
                    logging.info(f"OTP sent to Telegram.")
        except Exception as e:
            logging.error(f"Failed to send OTP: {e}")

    threading.Thread(target=_send, daemon=True).start()
    return True

@app.route("/request_telegram_otp", methods=["POST"])
def request_telegram_otp():
    if not tg_enabled or not tg_bot_token or not tg_chat_id:
        return jsonify({"success": False, "message": "Telegram login is not configured on the server."})
    
    # Generate 6-digit OTP
    otp = "".join([str(secrets.randbelow(10)) for _ in range(6)])
    expiry = datetime.now() + timedelta(minutes=5)
    
    session["login_otp"] = otp
    session["login_otp_expiry"] = expiry.strftime("%Y-%m-%d %H:%M:%S")
    
    client_ip = get_real_client_ip()
    user_agent = request.headers.get('User-Agent', 'Unknown Browser')
    
    if send_telegram_otp(otp, client_ip, user_agent):
        return jsonify({"success": True, "message": "OTP has been sent to your Telegram bot."})
    else:
        return jsonify({"success": False, "message": "Failed to send OTP. Please contact admin."})
    

def zip_folder_or_file(source_path, ziph, base_dir_for_zip):
    source_path = os.path.abspath(source_path)
    if os.path.isfile(source_path):
        ziph.write(source_path, os.path.basename(source_path))
    elif os.path.isdir(source_path):
        for root, dirs, files in os.walk(source_path):
            base_dir_to_use = os.path.dirname(source_path) 
            for file in files:
                file_path = os.path.join(root, file)
                rel_path_in_zip = os.path.relpath(file_path, base_dir_to_use)
                ziph.write(file_path, rel_path_in_zip)
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                rel_path_in_zip = os.path.relpath(dir_path, base_dir_to_use)
                if not os.listdir(dir_path):
                    zipInfo = zipfile.ZipInfo(rel_path_in_zip.replace('\\', '/') + '/')
                    ziph.writestr(zipInfo, '')

def get_file_info(file_path):
    size = os.path.getsize(file_path) / (1024 * 1024)
    version = "-"
    created_time = os.path.getctime(file_path)
    created_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_time))
    if platform.system() == "Windows" and file_path.lower().endswith((".exe", ".dll")):
        try:
            import win32api
            info = win32api.GetFileVersionInfo(file_path, "\\")
            ms, ls = info['FileVersionMS'], info['FileVersionLS']
            version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except KeyError:
            version = "Not available"
        except Exception as e:
            logging.warning(f"Error reading file version {file_path}: {e}")
    return f"{size:.2f} MB", version, created_date

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated_function

# Shared links
SHARED_LINKS_FILE = os.path.join(APP_DIR, "shared_links.json")

def load_shared_links():
    if os.path.exists(SHARED_LINKS_FILE):
        try:
            with open(SHARED_LINKS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading {SHARED_LINKS_FILE}: {e}")
            return {}
    return {}

def save_shared_links(links):
    try:
        with open(SHARED_LINKS_FILE, "w") as f:
            json.dump(links, f)
    except Exception as e:
        logging.error(f"Error saving {SHARED_LINKS_FILE}: {e}")

# Shared upload links
SHARED_UPLOAD_LINKS_FILE = os.path.join(APP_DIR, "shared_upload_links.json")

def load_shared_upload_links():
    if os.path.exists(SHARED_UPLOAD_LINKS_FILE):
        try:
            with open(SHARED_UPLOAD_LINKS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading {SHARED_UPLOAD_LINKS_FILE}: {e}")
            return {}
    return {}

def save_shared_upload_links(links):
    try:
        with open(SHARED_UPLOAD_LINKS_FILE, "w") as f:
            json.dump(links, f)
    except Exception as e:
        logging.error(f"Error saving {SHARED_UPLOAD_LINKS_FILE}: {e}")

UPLOAD_REQUEST_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Upload Request - File Station</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23818cf8'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {
            background: #0d1117 url('/image.jpg') center/cover no-repeat fixed;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background: rgba(13, 17, 23, 0.6);
            pointer-events: none;
            z-index: 0;
        }
        .glass-card {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            box-shadow: 0 16px 40px rgba(0,0,0,0.4);
        }
        .dropzone {
            border: 2px dashed rgba(255, 255, 255, 0.2);
            background: rgba(255, 255, 255, 0.03);
            transition: all 0.2s ease;
        }
        .dropzone.dragover {
            border-color: #3b82f6;
            background: rgba(59, 130, 246, 0.08);
        }
        .btn-gradient {
            background: #2563eb;
            color: #ffffff;
            transition: all 0.2s ease;
        }
        .btn-gradient:hover {
            background: #1d4ed8;
            transform: translateY(-1px);
        }
        .btn-gradient:active {
            transform: translateY(1px);
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-in {
            animation: fadeIn 0.3s ease forwards;
        }
    </style>
</head>
<body class="p-4 antialiased font-sans relative">
    <div class="w-full max-w-[500px] relative z-10 animate-fade-in">
        <div class="glass-card rounded-2xl overflow-hidden p-8 sm:p-10">
            <!-- Header Section -->
            <div class="text-center mb-8">
                <div class="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-blue-600 text-white mb-4">
                    <i class="bi bi-cloud-arrow-up text-2xl"></i>
                </div>
                <h1 class="text-2xl font-bold text-white tracking-tight">Upload Request</h1>
                <p class="text-slate-400 text-sm mt-2">You have been requested to upload a file to:</p>
                <div class="inline-flex items-center gap-2 mt-3 px-3 py-1.5 rounded-xl bg-slate-950/40 border border-white/5 text-blue-400 text-xs font-semibold">
                    <i class="bi bi-folder-fill"></i>
                    <span>/{{ folder_name }}</span>
                </div>
                {% if expiry_date %}
                <div class="block mt-3 text-amber-400 text-xs font-medium">
                    <i class="bi bi-calendar-event me-1"></i>
                    <span>Valid until: <span class="font-bold">{{ expiry_date }}</span></span>
                </div>
                {% endif %}
            </div>

            <!-- Main Upload Section -->
            <div id="uploadSection" class="space-y-6">
                <!-- Dropzone Area -->
                <div id="dropzone" class="dropzone rounded-xl p-8 text-center cursor-pointer flex flex-col items-center justify-center">
                    <input type="file" id="fileInput" class="hidden">
                    <i class="bi bi-cloud-upload text-4xl text-slate-400 mb-3" id="uploadIcon"></i>
                    <p class="text-sm font-semibold text-white mb-1">Drag & drop a file here</p>
                    <p class="text-xs text-slate-400">or click to browse from device</p>
                </div>

                <!-- Selected File List -->
                <div id="filesListContainer" class="hidden space-y-3">
                    <p class="text-xs font-bold text-slate-400 uppercase tracking-wider ml-1">Selected File</p>
                    <div id="filesList" class="space-y-2"></div>
                </div>

                <!-- Progress Section -->
                <div id="progressContainer" class="hidden space-y-2 bg-slate-950/40 p-4 rounded-xl border border-white/5">
                    <div class="flex justify-between text-xs font-bold text-slate-300">
                        <span id="currentFileName" class="truncate max-w-[70%]">Uploading...</span>
                        <span id="progressPercent">0%</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-2.5 overflow-hidden">
                        <div id="progressBar" class="bg-blue-600 h-2.5 rounded-full transition-all duration-150" style="width: 0%"></div>
                    </div>
                    <p id="progressDetail" class="text-[10px] text-slate-400 text-right mt-1">0 / 0 MB</p>
                </div>

                <!-- Action Button -->
                <button id="btnStartUpload" disabled
                        class="w-full bg-slate-800 text-slate-500 cursor-not-allowed font-bold py-3.5 px-5 rounded-xl flex items-center justify-center gap-2 transition-all duration-200">
                    <i class="bi bi-upload text-sm"></i>
                    <span>Start Upload</span>
                </button>
            </div>

            <!-- Success Section -->
            <div id="successCard" class="hidden text-center space-y-6 py-4 animate-fade-in">
                <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/25 text-emerald-400">
                    <i class="bi bi-check2-all text-3xl"></i>
                </div>
                <div>
                    <h2 class="text-xl font-bold text-white mb-2">Upload Successful!</h2>
                    <p class="text-slate-400 text-sm leading-relaxed">Your file has been uploaded and stored securely.</p>
                </div>
                <div class="p-4 bg-slate-950/40 border border-white/5 rounded-2xl text-left">
                    <p class="text-[10px] text-slate-500 uppercase tracking-widest font-bold mb-1">Uploaded File</p>
                    <p id="uploadedFileName" class="text-sm font-semibold text-white truncate">filename.ext</p>
                </div>
                <div class="flex items-start gap-2.5 p-4 bg-amber-500/5 border border-amber-500/10 rounded-2xl text-left">
                    <i class="bi bi-info-circle text-amber-400 mt-0.5 text-base"></i>
                    <p class="text-xs text-slate-400 leading-relaxed font-medium">This upload link has now been invalidated. You cannot upload any more files using this link.</p>
                </div>
            </div>

            <!-- Footer -->
            <div class="mt-8 pt-6 border-t border-white/5 flex justify-between items-center text-[9px] text-slate-500 font-bold uppercase tracking-wider">
                <span>Secure Channel</span>
                <span>File Station</span>
            </div>
        </div>
        <div class="mt-8 flex justify-center">
            <p class="text-slate-650 text-[10px] font-bold uppercase tracking-[0.2em]">&copy; File Station Security</p>
        </div>
    </div>

    <script>
        const dropzone = document.getElementById('dropzone');
        const fileInput = document.getElementById('fileInput');
        const filesListContainer = document.getElementById('filesListContainer');
        const filesList = document.getElementById('filesList');
        const btnStartUpload = document.getElementById('btnStartUpload');
        
        const progressContainer = document.getElementById('progressContainer');
        const currentFileName = document.getElementById('currentFileName');
        const progressPercent = document.getElementById('progressPercent');
        const progressBar = document.getElementById('progressBar');
        const progressDetail = document.getElementById('progressDetail');

        let selectedFile = null;
        const CHUNK_SIZE = 10 * 1024 * 1024;

        // Trigger file browse on click
        dropzone.addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', handleFilesSelect);

        // Drag and Drop Events
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });

        dropzone.addEventListener('dragleave', () => {
            dropzone.classList.remove('dragover');
        });

        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                fileInput.files = e.dataTransfer.files;
                handleFilesSelect();
            }
        });

        function handleFilesSelect() {
            if (fileInput.files.length > 0) {
                selectedFile = fileInput.files[0];
                renderFilesList();
                btnStartUpload.disabled = false;
                btnStartUpload.className = "btn-gradient w-full text-white font-bold py-3.5 px-5 rounded-xl flex items-center justify-center gap-2";
            } else {
                selectedFile = null;
                renderFilesList();
                btnStartUpload.disabled = true;
                btnStartUpload.className = "w-full bg-slate-800 text-slate-500 cursor-not-allowed font-bold py-3.5 px-5 rounded-xl flex items-center justify-center gap-2";
            }
        }

        function renderFilesList() {
            filesList.innerHTML = '';
            if (!selectedFile) {
                filesListContainer.classList.add('hidden');
                return;
            }
            filesListContainer.classList.remove('hidden');
            const sizeMB = (selectedFile.size / (1024 * 1024)).toFixed(2);
            const fileRow = document.createElement('div');
            fileRow.className = "flex justify-between items-center bg-white/[0.02] border border-white/5 p-3 rounded-xl text-xs";
            fileRow.innerHTML = `
                <div class="flex items-center gap-2 truncate max-w-[80%]">
                    <i class="bi bi-file-earmark-arrow-up text-blue-400"></i>
                    <span class="text-slate-200 truncate font-medium">${selectedFile.name}</span>
                </div>
                <span class="text-slate-500 font-semibold">${sizeMB} MB</span>
            `;
            filesList.appendChild(fileRow);
        }

        btnStartUpload.addEventListener('click', async () => {
            if (!selectedFile) return;

            btnStartUpload.disabled = true;
            btnStartUpload.classList.add('opacity-50', 'cursor-not-allowed');
            progressContainer.classList.remove('hidden');

            try {
                await uploadFileInChunks(selectedFile);
                document.getElementById('uploadSection').classList.add('hidden');
                document.getElementById('successCard').classList.remove('hidden');
                document.getElementById('uploadedFileName').innerText = selectedFile.name;
            } catch(err) {
                alert("Upload error: " + err);
                btnStartUpload.disabled = false;
                btnStartUpload.classList.remove('opacity-50', 'cursor-not-allowed');
                progressContainer.classList.add('hidden');
            }
        });

        function uploadFileInChunks(file) {
            return new Promise((resolve, reject) => {
                const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
                const uploadId = Date.now().toString() + '_' + Math.random().toString(36).substr(2, 9);
                let currentChunk = 0;

                currentFileName.innerText = file.name;
                updateProgress(0, file.size);

                function sendNextChunk() {
                    const start = currentChunk * CHUNK_SIZE;
                    const end = Math.min(start + CHUNK_SIZE, file.size);
                    const chunk = file.slice(start, end);

                    const formData = new FormData();
                    formData.append('file', chunk, file.name);
                    formData.append('filename', file.name);
                    formData.append('chunk_index', currentChunk);
                    formData.append('total_chunks', totalChunks);
                    formData.append('upload_id', uploadId);

                    const xhr = new XMLHttpRequest();
                    xhr.open('POST', `/upload_request/{{ token }}/chunk`, true);

                    xhr.onload = function() {
                        if (xhr.status === 200) {
                            currentChunk++;
                            const uploadedSize = Math.min(currentChunk * CHUNK_SIZE, file.size);
                            updateProgress(uploadedSize, file.size);

                            if (currentChunk < totalChunks) {
                                sendNextChunk();
                            } else {
                                resolve();
                            }
                        } else {
                            try {
                                const response = JSON.parse(xhr.responseText);
                                reject(response.message || 'Upload failed');
                            } catch(e) {
                                reject('Upload failed with status ' + xhr.status);
                            }
                        }
                    };

                    xhr.onerror = function() {
                        reject('Connection error');
                    };

                    xhr.send(formData);
                }

                sendNextChunk();
            });
        }

        function updateProgress(uploaded, total) {
            const percent = Math.round((uploaded / total) * 100);
            progressBar.style.width = percent + '%';
            progressPercent.innerText = percent + '%';
            
            const uploadedMB = (uploaded / (1024 * 1024)).toFixed(2);
            const totalMB = (total / (1024 * 1024)).toFixed(2);
            progressDetail.innerText = `${uploadedMB} / ${totalMB} MB`;
        }
    </script>
</body>
</html>
"""

@app.route("/generate_upload_link", methods=["POST"])
@login_required
def generate_upload_link():
    data = request.get_json(silent=True) or {}
    if data.get("folder_path") is None:
        return jsonify({"success": False, "message": "Folder path not provided."}), 400
    rel_path = data.get("folder_path", "")
    expiry_date = data.get("expiry_date")
    
    rel_path = rel_path.replace("\\", "/")
    if rel_path.endswith("/"):
        rel_path = rel_path[:-1]
    
    target_abs_path = os.path.abspath(os.path.join(directory, rel_path))
    if not is_safe_path(directory, target_abs_path) or not os.path.isdir(target_abs_path):
        return jsonify({"success": False, "message": "Invalid or unauthorized folder path."}), 400
    
    links = load_shared_upload_links()
    
    for token, info in links.items():
        if info.get("rel_path") == rel_path and info.get("expiry") == expiry_date:
            return jsonify({"success": True, "token": token})
    
    new_token = secrets.token_hex(8)
    links[new_token] = {
        "rel_path": rel_path,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "expiry": expiry_date
    }
    save_shared_upload_links(links)
    return jsonify({"success": True, "token": new_token})

@app.route("/upload_request/<token>/chunk", methods=["POST"])
def upload_request_chunk(token):
    links = load_shared_upload_links()
    if token not in links:
        return jsonify({"success": False, "message": "Invalid or non-existent upload link."}), 404
    
    link_info = links[token]
    if link_info.get("used"):
        return jsonify({"success": False, "message": "This upload link has already been used."}), 410
    if is_link_expired(link_info):
        return jsonify({"success": False, "message": "This upload link has expired."}), 410
    
    target_rel_path = link_info.get("rel_path")
    
    file_chunk = request.files.get("file")
    raw_filename = request.form.get("filename")
    chunk_index = int(request.form.get("chunk_index", 0))
    total_chunks = int(request.form.get("total_chunks", 1))
    raw_upload_id = request.form.get("upload_id")

    if not file_chunk or not raw_filename or not raw_upload_id:
        return jsonify({"success": False, "message": "Missing required upload parameters."}), 400

    filename = get_secure_filename(raw_filename)
    if not filename:
        return jsonify({"success": False, "message": "Invalid filename."}), 400

    upload_id = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_upload_id)
    if not upload_id:
        return jsonify({"success": False, "message": "Invalid upload ID."}), 400

    ext = os.path.splitext(filename)[1].lower()
    if not is_allowed_extension(ext):
        return jsonify({"success": False, "message": "File type is not allowed."}), 400

    target_abs_path = os.path.abspath(os.path.join(directory, target_rel_path))
    if not is_safe_path(directory, target_abs_path):
        return jsonify({"success": False, "message": "Unauthorized upload path."}), 403

    file_path = os.path.abspath(os.path.join(target_abs_path, filename))
    if not is_safe_path(target_abs_path, file_path) or not is_safe_path(directory, file_path):
        return jsonify({"success": False, "message": "Unauthorized file path."}), 403

    os.makedirs(target_abs_path, exist_ok=True)
    
    temp_dir = os.path.join(directory, ".upload_temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    upload_temp_dir = os.path.abspath(os.path.join(temp_dir, upload_id))
    if not is_safe_path(temp_dir, upload_temp_dir):
        return jsonify({"success": False, "message": "Unauthorized temporary path."}), 403
    os.makedirs(upload_temp_dir, exist_ok=True)
    
    chunk_filename = f"{chunk_index}.part"
    chunk_path = os.path.abspath(os.path.join(upload_temp_dir, chunk_filename))
    if not is_safe_path(upload_temp_dir, chunk_path):
        return jsonify({"success": False, "message": "Unauthorized chunk path."}), 403
    file_chunk.save(chunk_path)
    
    if chunk_index == total_chunks - 1:
        if os.path.exists(file_path):
            try:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                base, ext = os.path.splitext(filename)
                backup_filename = f"{base}.BAK_{timestamp}{ext}"
                backup_path = os.path.abspath(os.path.join(target_abs_path, backup_filename))
                if is_safe_path(target_abs_path, backup_path):
                    shutil.move(file_path, backup_path)
                    logging.info(f"Existing file backed up to: {backup_filename}")
            except Exception as e:
                logging.error(f"Failed to backup existing file {filename}: {e}")
                shutil.rmtree(upload_temp_dir, ignore_errors=True)
                return jsonify({"success": False, "message": "Failed to backup existing file."}), 500
        
        try:
            with open(file_path, "wb") as final_file:
                for i in range(total_chunks):
                    part_path = os.path.join(upload_temp_dir, f"{i}.part")
                    with open(part_path, "rb") as part_file:
                        final_file.write(part_file.read())
            
            shutil.rmtree(upload_temp_dir, ignore_errors=True)
            link_info["used"] = True
            links[token] = link_info
            save_shared_upload_links(links)
            logging.info(f"Chunked file uploaded via link: {filename} to {target_abs_path}")
            return jsonify({"success": True, "message": f"File '{filename}' uploaded successfully."})
        except Exception as e:
            logging.error(f"Failed to assemble chunked file {filename}: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            shutil.rmtree(upload_temp_dir, ignore_errors=True)
            return jsonify({"success": False, "message": f"Assembly failed: {e}"}), 500

    return jsonify({"success": True, "message": f"Chunk {chunk_index + 1}/{total_chunks} received."})

@app.route("/upload_chunk", methods=["POST"])
@login_required
def upload_chunk():
    pin = request.form.get("pin", "")
    if not upload_pin or not secrets.compare_digest(str(pin), str(upload_pin)):
        logging.warning(f"Incorrect PIN for chunked upload: {pin}")
        return jsonify({"success": False, "message": "Incorrect PIN! Access denied."}), 403

    file_chunk = request.files.get("file")
    raw_filename = request.form.get("filename")
    chunk_index = int(request.form.get("chunk_index", 0))
    total_chunks = int(request.form.get("total_chunks", 1))
    target_path = request.form.get("target_path", "").strip().replace("/", os.sep)
    raw_upload_id = request.form.get("upload_id")

    if not file_chunk or not raw_filename or not raw_upload_id:
        return jsonify({"success": False, "message": "Missing required upload parameters."}), 400

    filename = get_secure_filename(raw_filename)
    if not filename:
        return jsonify({"success": False, "message": "Invalid filename."}), 400

    upload_id = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_upload_id)
    if not upload_id:
        return jsonify({"success": False, "message": "Invalid upload ID."}), 400

    ext = os.path.splitext(filename)[1].lower()
    if not is_allowed_extension(ext):
        return jsonify({"success": False, "message": "File type is not allowed."}), 400

    target_abs_path = os.path.abspath(os.path.join(directory, target_path))
    if not is_safe_path(directory, target_abs_path):
        return jsonify({"success": False, "message": "Unauthorized upload path."}), 403

    file_path = os.path.abspath(os.path.join(target_abs_path, filename))
    if not is_safe_path(target_abs_path, file_path) or not is_safe_path(directory, file_path):
        return jsonify({"success": False, "message": "Unauthorized file path."}), 403

    os.makedirs(target_abs_path, exist_ok=True)
    
    temp_dir = os.path.join(directory, ".upload_temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    upload_temp_dir = os.path.abspath(os.path.join(temp_dir, upload_id))
    if not is_safe_path(temp_dir, upload_temp_dir):
        return jsonify({"success": False, "message": "Unauthorized temporary path."}), 403
    os.makedirs(upload_temp_dir, exist_ok=True)
    
    chunk_filename = f"{chunk_index}.part"
    chunk_path = os.path.abspath(os.path.join(upload_temp_dir, chunk_filename))
    if not is_safe_path(upload_temp_dir, chunk_path):
        return jsonify({"success": False, "message": "Unauthorized chunk path."}), 403
    file_chunk.save(chunk_path)
    
    if chunk_index == total_chunks - 1:
        if os.path.exists(file_path):
            try:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                base, ext = os.path.splitext(filename)
                backup_filename = f"{base}.BAK_{timestamp}{ext}"
                backup_path = os.path.abspath(os.path.join(target_abs_path, backup_filename))
                if is_safe_path(target_abs_path, backup_path):
                    shutil.move(file_path, backup_path)
                    logging.info(f"Existing file backed up to: {backup_filename}")
            except Exception as e:
                logging.error(f"Failed to backup existing file {filename}: {e}")
                shutil.rmtree(upload_temp_dir, ignore_errors=True)
                return jsonify({"success": False, "message": "Failed to backup existing file."}), 500
        
        try:
            with open(file_path, "wb") as final_file:
                for i in range(total_chunks):
                    part_path = os.path.join(upload_temp_dir, f"{i}.part")
                    with open(part_path, "rb") as part_file:
                        final_file.write(part_file.read())
            
            shutil.rmtree(upload_temp_dir, ignore_errors=True)
            logging.info(f"Chunked file uploaded: {filename} to {target_abs_path}")
            return jsonify({"success": True, "message": f"File '{filename}' uploaded successfully."})
        except Exception as e:
            logging.error(f"Failed to assemble chunked file {filename}: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            shutil.rmtree(upload_temp_dir, ignore_errors=True)
            return jsonify({"success": False, "message": f"Assembly failed: {e}"}), 500

    return jsonify({"success": True, "message": f"Chunk {chunk_index + 1}/{total_chunks} received."})

UPLOAD_LINK_EXPIRED_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Link Invalid - File Station</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23818cf8'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {
            background-color: #0b0f19;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow-x: hidden;
        }
        .glass-card {
            background: rgba(17, 24, 39, 0.7);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
        }
    </style>
</head>
<body class="p-4 antialiased font-sans relative">
    <div class="absolute top-[-10%] left-[-10%] w-[60vw] h-[60vw] rounded-full bg-blue-500/5 blur-[120px] pointer-events-none"></div>
    <div class="absolute bottom-[-10%] right-[-10%] w-[60vw] h-[60vw] rounded-full bg-indigo-600/5 blur-[120px] pointer-events-none"></div>

    <div class="w-full max-w-[450px] relative z-10 text-center">
        <div class="glass-card rounded-3xl p-8 sm:p-10 animate-fade-in">
            <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-amber-500/10 border border-amber-500/25 text-amber-500 mb-6">
                <i class="bi bi-exclamation-triangle text-3xl"></i>
            </div>
            <h1 class="text-xl font-bold text-white mb-3">Upload Link Invalid</h1>
            <p class="text-slate-400 text-sm leading-relaxed mb-4">
                {{ error_message }}
            </p>
            <p class="text-xs text-slate-500 leading-relaxed border-t border-white/5 pt-4 font-medium">
                For security reasons, each request link allows only a single upload transaction. If you need to upload again, please request a new link from the owner.
            </p>
        </div>
    </div>
</body>
</html>
"""

@app.route("/upload_request/<token>")
def public_upload_request(token):
    links = load_shared_upload_links()
    if token not in links:
        abort(404)
    
    link_info = links[token]
    if link_info.get("used"):
        return render_template_string(UPLOAD_LINK_EXPIRED_TEMPLATE, error_message="This upload link has already been used."), 410
        
    if is_link_expired(link_info):
        return render_template_string(UPLOAD_LINK_EXPIRED_TEMPLATE, error_message="This upload link has expired."), 410
    
    rel_path = link_info.get("rel_path")
    folder_name = os.path.basename(rel_path) if rel_path else "Root"
    expiry_date = link_info.get("expiry")
    
    return render_template_string(
        UPLOAD_REQUEST_TEMPLATE,
        token=token,
        folder_name=folder_name,
        expiry_date=expiry_date,
        current_year=datetime.now().year
    )

@app.route("/generate_share_link", methods=["POST"])
@login_required
def generate_share_link():
    data = request.get_json(silent=True) or {}
    rel_path = data.get("file_path")
    expiry_date = data.get("expiry_date") # Format: YYYY-MM-DD or None
    if not rel_path:
        return jsonify({"success": False, "message": "File path not provided."}), 400
    
    # Clean rel_path
    rel_path = rel_path.replace("\\", "/").lstrip("/")
    target_abs_path = os.path.abspath(os.path.join(directory, rel_path))
    if not is_safe_path(directory, target_abs_path) or not os.path.exists(target_abs_path):
        return jsonify({"success": False, "message": "Invalid or unauthorized file path."}), 400
    
    links = load_shared_links()
    
    # Check if path already has a token with the SAME expiry
    for token, info in links.items():
        if info.get("rel_path") == rel_path and info.get("expiry") == expiry_date:
            return jsonify({"success": True, "token": token})
    
    # Generate new token
    new_token = secrets.token_hex(8)
    links[new_token] = {
        "rel_path": rel_path,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "expiry": expiry_date
    }
    save_shared_links(links)
    return jsonify({"success": True, "token": new_token})

def is_link_expired(token_info):
    expiry = token_info.get("expiry")
    if not expiry:
        return False
    try:
        expiry_dt = datetime.strptime(expiry, "%Y-%m-%d")
        # Expiry is end of day
        expiry_dt = expiry_dt.replace(hour=23, minute=59, second=59)
        return datetime.now() > expiry_dt
    except Exception:
        return False

# Public share route
@app.route("/share/<token>", defaults={"subpath": ""})
@app.route("/share/<token>/<path:subpath>")
def public_share(token, subpath=""):
    if not ENABLE_PUBLIC_SHARE:
        abort(404)
    
    links = load_shared_links()
    if token not in links:
        abort(404)
    
    if is_link_expired(links[token]):
        return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Link Expired</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23818cf8'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
</head>
<body class="bg-slate-50 min-h-screen flex items-center justify-center p-6">
    <div class="max-w-md w-full bg-white rounded-3xl shadow-2xl p-8 text-center border border-slate-100">
        <div class="w-20 h-20 bg-red-50 text-red-500 rounded-full flex items-center justify-center mx-auto mb-6">
            <i class="bi bi-clock-history text-4xl"></i>
        </div>
        <h1 class="text-2xl font-bold text-slate-800 mb-2">Link Expired</h1>
        <p class="text-slate-500 mb-8">This shared link has reached its expiration date and is no longer accessible.</p>
        <div class="text-[10px] text-slate-400 uppercase tracking-[0.2em] font-medium border-t pt-8">
            Powered by File Station
        </div>
    </div>
</body>
</html>
""")

    base_rel_path = links[token].get("rel_path")
    base_abs_path = os.path.abspath(os.path.join(directory, base_rel_path))
    
    if not is_safe_path(directory, base_abs_path) or not os.path.exists(base_abs_path):
        abort(404)
    
    # Target path inside the shared folder
    clean_subpath = subpath.strip("/").replace("\\", "/")
    if clean_subpath:
        target_abs_path = os.path.abspath(os.path.join(base_abs_path, clean_subpath))
    else:
        target_abs_path = base_abs_path

    if not is_safe_path(base_abs_path, target_abs_path) or not os.path.exists(target_abs_path):
        abort(403)
        
    expiry_date = links[token].get("expiry", "Unlimited") or "Unlimited"
    
    # Directory sharing
    if os.path.isdir(target_abs_path):
        items = []
        for item in os.listdir(target_abs_path):
            item_path = os.path.join(target_abs_path, item)
            is_dir = os.path.isdir(item_path)
            size = "-"
            if not is_dir:
                ext = os.path.splitext(item)[1].lower()
                if not is_allowed_extension(ext, allow_zip=True):
                    logging.warning(f"Public share: Hidden disallowed file: {item}")
                    continue
                size_bytes = os.path.getsize(item_path)
                if size_bytes < 1024: size = f"{size_bytes} B"
                elif size_bytes < 1024*1024: size = f"{size_bytes/1024:.2f} KB"
                else: size = f"{size_bytes/(1024*1024):.2f} MB"
            
            item_rel = clean_subpath + "/" + item if clean_subpath else item
            items.append({
                "name": item,
                "is_dir": is_dir,
                "size": size,
                "rel_path": item_rel
            })
        
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        
        parent_subpath = ""
        if clean_subpath:
            parts = clean_subpath.split("/")
            if len(parts) > 1:
                parent_subpath = "/".join(parts[:-1])
        
        is_root = (clean_subpath == "")
        folder_display_name = os.path.basename(target_abs_path) if not is_root else os.path.basename(base_abs_path)
        if not folder_display_name:
            folder_display_name = "Shared Folder"
            
        return render_template_string(
            SHARED_FOLDER_TEMPLATE, 
            folder_name=folder_display_name, 
            items=items, 
            token=token, 
            expiry_date=expiry_date,
            is_root=is_root,
            parent_subpath=parent_subpath,
            current_subpath=clean_subpath
        )

    # Single file sharing
    ext = os.path.splitext(target_abs_path)[1].lower()
    if not is_allowed_extension(ext):
        logging.warning(f"Public share: Blocked disallowed file: {clean_subpath}")
        abort(403)
    
    filename = os.path.basename(target_abs_path)
    file_size_bytes = os.path.getsize(target_abs_path)
    if file_size_bytes < 1024:
        file_size = f"{file_size_bytes} B"
    elif file_size_bytes < 1024 * 1024:
        file_size = f"{file_size_bytes / 1024:.2f} KB"
    else:
        file_size = f"{file_size_bytes / (1024 * 1024):.2f} MB"
    
    file_type = ext.upper().replace(".", "")
    download_url = url_for('direct_download', token=token)
    
    return render_template_string(DOWNLOAD_TEMPLATE, filename=filename, file_size=file_size, file_type=file_type, download_url=download_url, expiry_date=expiry_date)

@app.route("/download/<token>")
def direct_download(token):
    if not ENABLE_PUBLIC_SHARE:
        abort(404)
    
    links = load_shared_links()
    if token not in links:
        abort(404)
    
    if is_link_expired(links[token]):
        abort(403)
    
    rel_path = links[token].get("rel_path")
    abs_path = os.path.abspath(os.path.join(directory, rel_path))
    
    if not is_safe_path(directory, abs_path) or not os.path.isfile(abs_path):
        abort(404)
    
    ext = os.path.splitext(abs_path)[1].lower()
    if not is_allowed_extension(ext):
        abort(403)
        
    client_ip = get_real_client_ip()
    send_telegram_notification(os.path.basename(abs_path), client_ip)

    return send_from_directory(os.path.dirname(abs_path), os.path.basename(abs_path), as_attachment=True)

@app.route("/download/<token>/<path:filename>")
def download_from_folder(token, filename):
    if not ENABLE_PUBLIC_SHARE:
        abort(404)
    
    links = load_shared_links()
    if token not in links:
        abort(404)
    
    if is_link_expired(links[token]):
        abort(403)
    
    # Jalur folder utama yang di-share
    base_rel_path = links[token].get("rel_path")
    base_abs_path = os.path.abspath(os.path.join(directory, base_rel_path))
    
    if not is_safe_path(directory, base_abs_path) or not os.path.isdir(base_abs_path):
        abort(404)
    
    # Jalur file spesifik di dalam folder tersebut
    file_abs_path = os.path.abspath(os.path.join(base_abs_path, filename))
    
    # Keamanan: Pastikan file masih berada di dalam folder yang di-share
    if not is_safe_path(base_abs_path, file_abs_path) or not os.path.isfile(file_abs_path):
        abort(403)
        
    ext = os.path.splitext(file_abs_path)[1].lower()
    if not is_allowed_extension(ext, allow_zip=True):
        logging.warning(f"Public download: Blocked disallowed file extension ({ext}): {filename}")
        abort(403)

    client_ip = get_real_client_ip()
    send_telegram_notification(os.path.basename(file_abs_path), client_ip)

    return send_from_directory(os.path.dirname(file_abs_path), os.path.basename(file_abs_path), as_attachment=True)

@app.route("/zip_folder/<token>", defaults={"subpath": ""})
@app.route("/zip_folder/<token>/<path:subpath>")
def zip_folder(token, subpath=""):
    if not ENABLE_PUBLIC_SHARE:
        abort(404)
        
    links = load_shared_links()
    if token not in links:
        abort(404)
        
    if is_link_expired(links[token]):
        abort(403)
        
    base_rel_path = links[token].get("rel_path")
    base_abs_path = os.path.abspath(os.path.join(directory, base_rel_path))
    
    if not is_safe_path(directory, base_abs_path) or not os.path.isdir(base_abs_path):
        abort(404)
        
    clean_subpath = subpath.strip("/").replace("\\", "/")
    if clean_subpath:
        target_abs_path = os.path.abspath(os.path.join(base_abs_path, clean_subpath))
    else:
        target_abs_path = base_abs_path
        
    if not is_safe_path(base_abs_path, target_abs_path) or not os.path.isdir(target_abs_path):
        abort(403)
        
    folder_name = os.path.basename(target_abs_path)
    if not folder_name:
        folder_name = "shared_folder"
        
    zip_filename = f"{folder_name}.zip"
    
    import tempfile

    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_zip_path = temp_zip.name
    temp_zip.close()

    try:
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, dirs, files_in_dir in os.walk(target_abs_path):
                for file in files_in_dir:
                    ext = os.path.splitext(file)[1].lower()
                    if not is_allowed_extension(ext, allow_zip=True):
                        continue
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, target_abs_path)
                    zip_file.write(file_path, arcname)

        client_ip = get_real_client_ip()
        send_telegram_notification(f"{zip_filename} (ZIP)", client_ip)

        def stream_and_cleanup():
            try:
                with open(temp_zip_path, 'rb') as f_zip:
                    while True:
                        chunk = f_zip.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                if os.path.exists(temp_zip_path):
                    try:
                        os.remove(temp_zip_path)
                    except Exception as ex:
                        logging.warning(f"Failed to delete temp zip {temp_zip_path}: {ex}")

        file_size = os.path.getsize(temp_zip_path)
        resp = Response(stream_and_cleanup(), mimetype='application/zip')
        resp.headers['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
        resp.headers['Content-Length'] = str(file_size)
        return resp
    except Exception as e:
        if os.path.exists(temp_zip_path):
            try:
                os.remove(temp_zip_path)
            except Exception:
                pass
        logging.error(f"Failed to generate zip for shared folder: {e}")
        abort(500)


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>File Station - Secure File Server</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23818cf8'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: { sans: ['Inter', 'sans-serif'] },
                    colors: {
                        primary: '#5865f2',
                        'primary-light': '#7c85f5',
                        secondary: '#64748b',
                        'bg-desktop': '#0d1117',
                        'window-bg': 'rgba(22,27,34,0.85)',
                        'border-color': 'rgba(255,255,255,0.08)',
                    }
                }
            }
        }
    </script>
    <script>
        // Always dark theme for macOS glass look
        document.documentElement.classList.add('dark');
    </script>
    <style>
        :root {
            --glass-bg: rgba(22, 27, 34, 0.75);
            --glass-border: rgba(255, 255, 255, 0.08);
            --glass-shadow: 0 8px 32px rgba(0,0,0,0.5);
            --accent: #5865f2;
            --accent-glow: rgba(88,101,242,0.3);
        }

        * { box-sizing: border-box; }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0d1117 url('/image.jpg') center/cover no-repeat fixed;
            color: #e6edf3;
            overflow: hidden;
        }

        /* Overlay for contrast */
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background: rgba(13, 17, 23, 0.45); /* Semi-transparent dark overlay */
            pointer-events: none;
            z-index: 0;
        }

        /* Glass panels */
        .glass {
            background: var(--glass-bg);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            border: 1px solid var(--glass-border);
        }

        .glass-header {
            background: rgba(30, 36, 48, 0.5);
            border-bottom: 1px solid rgba(255,255,255,0.06);
        }

        .glass-sidebar {
            background: rgba(16, 20, 28, 0.75);
            border-right: 1px solid rgba(255,255,255,0.08);
        }

        .glass-toolbar {
            background: rgba(20, 24, 34, 0.35);
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }

        .glass-row:hover {
            background: rgba(88,101,242,0.1);
        }

        /* macOS-style title bar */
        .macos-titlebar {
            background: rgba(32, 38, 52, 0.4);
            border-bottom: 1px solid rgba(255,255,255,0.07);
        }

        /* macOS Custom Scrollbar */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.15);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.3);
        }

        /* Traffic lights */
        .traffic-light {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 8px;
            transition: all 0.15s;
        }
        .traffic-light:hover { filter: brightness(1.2); }

        /* Buttons */
        .btn-glass {
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            color: #e2e8f0;
            transition: all 0.15s ease;
        }
        .btn-glass:hover {
            background: rgba(255,255,255,0.12);
            border-color: rgba(255,255,255,0.22);
            transform: translateY(-1px);
        }
        .btn-glass:active { transform: translateY(0); }
        .btn-glass:focus-visible {
            outline: 2px solid #5865f2;
            outline-offset: 2px;
        }

        .btn-accent {
            background: #5865f2;
            border: 1px solid rgba(255,255,255,0.15);
            color: white;
            transition: all 0.15s ease;
            box-shadow: 0 2px 6px rgba(0,0,0,0.25);
        }
        .btn-accent:hover {
            background: #4752c4;
            box-shadow: 0 4px 10px rgba(0,0,0,0.3);
            transform: translateY(-1px);
        }
        .btn-accent:active { transform: translateY(0); }
        .btn-accent:focus-visible {
            outline: 2px solid #ffffff;
            outline-offset: 2px;
        }

        /* File item */
        .file-item {
            border-bottom: 1px solid rgba(255,255,255,0.03);
            transition: background 0.15s;
        }
        .file-item:hover { background: rgba(255,255,255,0.04); }

        /* Sidebar items */
        .sidebar-item {
            border-radius: 8px;
            margin: 2px 8px;
            padding: 8px 12px;
            transition: all 0.15s;
            cursor: pointer;
            font-size: 13px;
        }
        .sidebar-item:hover {
            background: rgba(255,255,255,0.06);
        }
        .sidebar-item.active {
            background: rgba(88,101,242,0.2);
            border-left: 3px solid #5865f2;
        }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }

        /* Column header */
        .col-header {
            background: rgba(16,20,28,0.95);
            border-bottom: 1px solid rgba(255,255,255,0.08);
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: #94a3b8;
        }

        /* Path bar */
        .path-bar {
            background: rgba(13,17,23,0.85);
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }

        /* Status bar */
        .status-bar {
            background: rgba(10,13,18,0.95);
            backdrop-filter: blur(12px);
            border-top: 1px solid rgba(255,255,255,0.05);
        }

        /* Input glass */
        .input-glass {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.12);
            color: #e2e8f0;
            transition: all 0.2s;
        }
        .input-glass:focus {
            border-color: #5865f2;
            box-shadow: 0 0 0 2px rgba(88,101,242,0.25);
            background: rgba(255,255,255,0.08);
        }
        .input-glass:focus-visible {
            outline: 2px solid #5865f2;
            outline-offset: 1px;
        }
        .input-glass::placeholder { color: #8b949e; }

        button:focus-visible, a:focus-visible {
            outline: 2px solid #5865f2;
            outline-offset: 2px;
        }

        /* Flash alerts */
        .flash-success { background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.25); color: #6ee7b7; }
        .flash-error   { background: rgba(239,68,68,0.1);  border: 1px solid rgba(239,68,68,0.25);  color: #fca5a5; }

        /* Separator */
        .separator { width: 1px; background: rgba(255,255,255,0.08); height: 20px; }

        /* Action buttons in file rows */
        .row-action {
            padding: 5px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            border: 1px solid transparent;
            transition: all 0.15s;
            color: white;
            min-width: 30px;
            text-align: center;
        }

        /* ===== RESPONSIVE ===== */

        /* Mobile Radial Menu */
        @media (max-width: 767px) {
            .mobile-action-toggle {
                display: inline-flex !important;
                align-items: center;
                justify-content: center;
                width: 32px;
                height: 32px;
                border-radius: 50%;
                background: rgba(255,255,255,0.08);
                color: #e2e8f0;
                z-index: 20;
                border: 1px solid rgba(255,255,255,0.1);
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            }
            .mobile-action-toggle.active {
                background: rgba(88,101,242,0.4);
                border-color: rgba(88,101,242,0.6);
                transform: rotate(90deg);
            }
            
            .file-actions-wrap { 
                position: relative; 
                justify-content: flex-end;
                padding-right: 4px;
            }
            
            .action-buttons-container {
                position: fixed;
                width: 0;
                height: 0;
                pointer-events: none;
                z-index: 9999;
            }
            .action-buttons-container.active {
                pointer-events: auto;
            }
            
            .action-buttons-container > * {
                position: absolute;
                top: -18px;
                left: -18px;
                width: 36px !important;
                height: 36px !important;
                border-radius: 50% !important;
                opacity: 0;
                transform: scale(0.3);
                transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 0 !important;
                min-width: 0 !important;
                box-shadow: 0 4px 12px rgba(0,0,0,0.5);
            }
            
            .action-buttons-container form .row-action {
                width: 100% !important;
                height: 100% !important;
                border-radius: 50% !important;
                margin: 0;
                padding: 0 !important;
                display: flex;
                align-items: center;
                justify-content: center;
                min-width: 0 !important;
            }
            
            /* Default Arc (Centered Left) */
            .action-buttons-container.active:not(.arc-up):not(.arc-down) > :nth-child(1) { transform: rotate(70deg) translateX(-80px) rotate(-70deg) scale(1); opacity: 1; transition-delay: 0.05s; }
            .action-buttons-container.active:not(.arc-up):not(.arc-down) > :nth-child(2) { transform: rotate(35deg) translateX(-80px) rotate(-35deg) scale(1); opacity: 1; transition-delay: 0.10s; }
            .action-buttons-container.active:not(.arc-up):not(.arc-down) > :nth-child(3) { transform: rotate(0deg) translateX(-80px) rotate(0deg) scale(1); opacity: 1; transition-delay: 0.15s; }
            .action-buttons-container.active:not(.arc-up):not(.arc-down) > :nth-child(4) { transform: rotate(-35deg) translateX(-80px) rotate(35deg) scale(1); opacity: 1; transition-delay: 0.20s; }
            .action-buttons-container.active:not(.arc-up):not(.arc-down) > :nth-child(5) { transform: rotate(-70deg) translateX(-80px) rotate(70deg) scale(1); opacity: 1; transition-delay: 0.25s; }

            /* Arc Up (Quarter circle Left to Up: 0deg to 90deg) */
            .action-buttons-container.arc-up.active > :nth-child(1) { transform: rotate(90deg) translateX(-80px) rotate(-90deg) scale(1); opacity: 1; transition-delay: 0.05s; }
            .action-buttons-container.arc-up.active > :nth-child(2) { transform: rotate(67.5deg) translateX(-80px) rotate(-67.5deg) scale(1); opacity: 1; transition-delay: 0.10s; }
            .action-buttons-container.arc-up.active > :nth-child(3) { transform: rotate(45deg) translateX(-80px) rotate(-45deg) scale(1); opacity: 1; transition-delay: 0.15s; }
            .action-buttons-container.arc-up.active > :nth-child(4) { transform: rotate(22.5deg) translateX(-80px) rotate(-22.5deg) scale(1); opacity: 1; transition-delay: 0.20s; }
            .action-buttons-container.arc-up.active > :nth-child(5) { transform: rotate(0deg) translateX(-80px) rotate(0deg) scale(1); opacity: 1; transition-delay: 0.25s; }

            /* Arc Down (Quarter circle Left to Down: 0deg to -90deg) */
            .action-buttons-container.arc-down.active > :nth-child(1) { transform: rotate(0deg) translateX(-80px) rotate(0deg) scale(1); opacity: 1; transition-delay: 0.05s; }
            .action-buttons-container.arc-down.active > :nth-child(2) { transform: rotate(-22.5deg) translateX(-80px) rotate(22.5deg) scale(1); opacity: 1; transition-delay: 0.10s; }
            .action-buttons-container.arc-down.active > :nth-child(3) { transform: rotate(-45deg) translateX(-80px) rotate(45deg) scale(1); opacity: 1; transition-delay: 0.15s; }
            .action-buttons-container.arc-down.active > :nth-child(4) { transform: rotate(-67.5deg) translateX(-80px) rotate(67.5deg) scale(1); opacity: 1; transition-delay: 0.20s; }
            .action-buttons-container.arc-down.active > :nth-child(5) { transform: rotate(-90deg) translateX(-80px) rotate(90deg) scale(1); opacity: 1; transition-delay: 0.25s; }
        }

        @media (min-width: 768px) {
            .mobile-action-toggle { display: none !important; }
        }

        /* Mobile toolbar: compact icon-only buttons */
        @media (max-width: 640px) {
            .window-chrome {
                border-radius: 10px;
            }
            .toolbar-label { display: none; }
            .glass-toolbar { padding: 8px 10px; gap: 6px; }
            .toolbar-btn { padding: 8px 10px !important; }
            .separator { display: none; }
            #fileFilter { width: 100% !important; margin-left: 0 !important; }
            #pathInput { width: 80px !important; }
            /* File rows: give more space to name on mobile */
            .file-col-name  { width: 70% !important; }
            .file-col-actions { width: 30% !important; }
            /* Action buttons always visible on mobile (no hover) */
            .file-actions-wrap { opacity: 1 !important; }
            /* Smaller row padding on mobile */
            .file-item { padding-top: 10px !important; padding-bottom: 10px !important; }
        }

        @media (min-width: 641px) and (max-width: 767px) {
            .toolbar-label { display: none; }
            .glass-toolbar { padding: 8px 12px; gap: 6px; }
            .toolbar-btn { padding: 8px 12px !important; }
            #fileFilter { width: 140px !important; }
            .file-col-name  { width: 60% !important; }
            .file-col-actions { width: 40% !important; }
            .file-actions-wrap { opacity: 1 !important; }
        }

        /* Desktop: restore wider name column */
        @media (min-width: 768px) {
            .file-col-name    { width: 42% !important; }
            .file-col-actions { width: 20% !important; }
        }

        /* Edit Mode */
        body:not(.edit-mode-active) .edit-mode-section { display: none !important; }

        /* Animate spin */
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .fa-spin, .animate-spin { animation: spin 1.5s linear infinite; }

        /* macOS window chrome */
        .window-chrome {
            background: rgba(22, 27, 34, 0.45);
            backdrop-filter: blur(35px) saturate(200%);
            -webkit-backdrop-filter: blur(35px) saturate(200%);
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.1);
            box-shadow: 0 32px 80px rgba(0,0,0,0.6), 0 0 0 0.5px rgba(255,255,255,0.05);
            overflow: hidden;
        }

        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(8px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-up { animation: fadeUp 0.35s ease forwards; }
    </style>
</head>
<body class="{{ 'edit-mode-active' if session.get('edit_mode_active') else '' }} h-screen overflow-hidden">
<div class="flex flex-col h-screen" style="position:relative;z-index:1;padding-bottom:calc(2.5rem + env(safe-area-inset-bottom));">
    <div class="flex-1 mx-2 mt-2 mb-2 sm:mx-4 sm:mt-3 sm:mb-3 md:mx-5 md:mt-4 md:mb-4 window-chrome flex flex-col animate-fade-up" style="min-height:0;">
        <!-- macOS Title Bar -->
        <div class="macos-titlebar px-4 py-3 flex items-center justify-between shrink-0 select-none">
            <div class="flex items-center gap-2">
                <i class="bi bi-box-seam-fill text-base" style="color:#5865f2;"></i>
                <span class="font-semibold text-sm text-slate-200 tracking-wide">File Station</span>
            </div>
            <div class="flex items-center gap-2">
                {% if session.get('edit_mode_active') %}
                <a href="/settings" class="btn-glass px-2.5 py-1.5 rounded-lg text-xs flex items-center gap-1.5 text-slate-300 hover:text-white border border-white/5" title="Settings">
                    <i class="bi bi-gear-fill"></i>
                </a>
                {% endif %}
                <button id="themeToggle" class="btn-glass px-2.5 py-1.5 rounded-lg text-xs flex items-center gap-1.5" title="Toggle Theme" style="display:none;">
                    <i class="bi bi-sun-fill"></i>
                </button>
            </div>
        </div>
        <div class="flex flex-1 overflow-hidden" style="min-height:0;">
            <!-- Glass Sidebar -->
            <div class="w-56 shrink-0 glass-sidebar py-3 overflow-y-auto hidden md:flex md:flex-col">
                <div class="px-4 pb-2 pt-1">
                    <p class="text-xs font-bold uppercase tracking-widest" style="color:#8b949e;letter-spacing:0.1em;">Navigation</p>
                </div>
                <div class="sidebar-item active flex items-center gap-3 text-slate-200">
                    <i class="bi bi-folder-fill text-base" style="color:#f59e0b;"></i>
                    <span>Current Directory</span>
                </div>
                <div class="sidebar-item flex items-center gap-3 text-slate-400" onclick="openModal('systemStatsModal')">
                    <i class="bi bi-hdd-rack-fill text-base" style="color:#10b981;"></i>
                    <span>System Resources</span>
                </div>
                <div class="sidebar-item flex items-center gap-3 text-slate-400" onclick="openModal('networkConfigModal')">
                    <i class="bi bi-ethernet text-base" style="color:#8b5cf6;"></i>
                    <span>Network</span>
                </div>
                <a href="{{ url_for('list_files', req_path='') }}" class="no-underline">
                    <div class="sidebar-item flex items-center gap-3 text-slate-400">
                        <i class="bi bi-house-door-fill text-base" style="color:#64748b;"></i>
                        <span class="truncate">Shared Root ({{ os.path.basename(base_directory) }})</span>
                    </div>
                </a>
            </div>
            <!-- Main Content Area -->
            <div class="flex-1 flex flex-col overflow-hidden" style="min-height:0;background:rgba(13,17,23,0.1);">
                <!-- Glass Toolbar -->
                <div class="glass-toolbar px-3 sm:px-4 py-2.5 flex items-center gap-1.5 sm:gap-2 flex-wrap shrink-0 z-10">
                    <!-- Row 1: Primary actions -->
                    <div class="flex items-center gap-1.5 sm:gap-2 flex-wrap w-full sm:w-auto">
                        <button id="uploadBtn" class="toolbar-btn btn-accent px-3 sm:px-4 py-2 rounded-lg text-xs sm:text-sm font-medium flex items-center gap-1.5" onclick="openModal('uploadModal')">
                            <i class="bi bi-cloud-arrow-up-fill"></i>
                            <span class="toolbar-label">Upload</span>
                        </button>
                        <button id="shareUploadLinkBtn" class="toolbar-btn btn-accent px-3 sm:px-4 py-2 rounded-lg text-xs sm:text-sm font-medium flex items-center gap-1.5 edit-mode-section" onclick="openShareUploadModal()">
                            <i class="bi bi-share-fill"></i>
                            <span class="toolbar-label">Share Upload</span>
                        </button>
                        <button id="createFolderBtn" class="toolbar-btn btn-glass px-3 sm:px-4 py-2 rounded-lg text-xs sm:text-sm font-medium flex items-center gap-1.5 edit-mode-section" onclick="openModal('createFolderModal')">
                            <i class="bi bi-folder-plus" style="color:#f59e0b;"></i>
                            <span class="toolbar-label">New Folder</span>
                        </button>
                        <button id="folderStatsBtn" class="toolbar-btn btn-glass px-3 sm:px-4 py-2 rounded-lg text-xs sm:text-sm font-medium flex items-center gap-1.5" onclick="openFolderStatsModal()">
                            <i class="bi bi-pie-chart-fill" style="color:#8b5cf6;"></i>
                            <span class="toolbar-label">Stats</span>
                        </button>
                        <div class="separator hidden sm:block"></div>
                        <button id="editModeBtn"
                            class="toolbar-btn px-3 sm:px-4 py-2 rounded-lg text-xs sm:text-sm font-medium flex items-center gap-1.5 transition-all duration-200 {{ 'btn-accent' if session.get('edit_mode_active') else 'btn-glass' }}">
                            {% if session.get('edit_mode_active') %}
                                <i class="bi bi-unlock-fill"></i>
                                <span class="toolbar-label">Deactivate Edit</span>
                            {% else %}
                                <i class="bi bi-lock-fill" style="color:#5865f2;"></i>
                                <span class="toolbar-label">Edit Mode</span>
                            {% endif %}
                        </button>
                    </div>
                    <!-- Row 2: Path + Filter (always full width on mobile) -->
                    <div class="flex items-center gap-1.5 w-full sm:w-auto sm:ml-auto mt-1.5 sm:mt-0">
                        <div class="flex items-center rounded-lg overflow-hidden flex-1 sm:flex-none" style="border:1px solid rgba(255,255,255,0.08);">
                            <input type="text" id="pathInput" class="input-glass px-2.5 py-1.5 text-xs border-0 rounded-none min-w-0 flex-1 sm:w-28" style="border:none;" placeholder="Go to path..." onkeydown="if(event.key === 'Enter') navigateToPath()">
                            <button onclick="navigateToPath()" class="px-2.5 py-1.5 text-slate-300 hover:text-white transition-colors shrink-0" style="background:rgba(255,255,255,0.05);border-left:1px solid rgba(255,255,255,0.08);" title="Go"><i class="bi bi-arrow-right text-xs"></i></button>
                        </div>
                        <input type="text" id="fileFilter" class="input-glass px-3 py-1.5 rounded-lg text-xs flex-1 sm:w-44 min-w-0" placeholder="Filter files...">
                    </div>
                </div>
                <!-- Path Bar -->
                <div class="path-bar px-4 py-2">
                    <div class="text-xs font-medium truncate flex items-center gap-1.5" style="color:#c9d1d9;">
                        <i class="bi bi-geo-alt-fill" style="color:#5865f2;"></i>
                        {% set path_parts = req_path.split('/') if req_path else [] %}
                        <a href="{{ url_for('list_files', req_path='') }}" class="hover:text-white transition-colors" style="color:#94a3b8;">Shared Root</a>
                        {% set current_build = [] %}
                        {% for part in path_parts %}
                            {% if part %}
                                {% set _ = current_build.append(part) %}
                                <i class="bi bi-chevron-right" style="color:#6e7681; font-size:10px;"></i>
                                <a href="{{ url_for('list_files', req_path='/'.join(current_build)) }}" class="hover:text-white transition-colors {% if loop.last %}text-slate-200 font-semibold{% else %}text-slate-400{% endif %}">
                                    {{ part }}
                                </a>
                            {% endif %}
                        {% endfor %}
                    </div>
                </div>
                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% if messages %}
                        <!-- Glassmorphism Toast Container -->
                        <div class="fixed top-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-3 pointer-events-none w-[90%] sm:w-auto min-w-[320px] max-w-lg" id="toastContainer">
                        {% for category, message in messages %}
                            <div class="toast-message pointer-events-auto p-3.5 sm:p-4 rounded-2xl text-sm flex items-center justify-between bg-slate-800/85 backdrop-blur-xl shadow-2xl border transition-all duration-500
                                {{ 'border-emerald-500/30' if category == 'success' else 'border-red-500/30' }}" role="alert"
                                style="box-shadow: 0 10px 40px -10px rgba(0,0,0,0.5); animation: toastSlideDown 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;">
                                <div class="flex items-center gap-3">
                                    <div class="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center 
                                        {{ 'bg-emerald-500/20 text-emerald-400' if category == 'success' else 'bg-red-500/20 text-red-400' }}">
                                        <i class="bi {{ 'bi-check-lg' if category == 'success' else 'bi-exclamation-triangle-fill' }} text-lg"></i>
                                    </div>
                                    <span class="text-slate-200 font-medium">{{ message }}</span>
                                </div>
                                <button type="button" class="ml-4 opacity-60 hover:opacity-100 transition-opacity bg-white/5 hover:bg-white/10 rounded-full w-7 h-7 flex items-center justify-center shrink-0" onclick="closeToast(this.closest('.toast-message'))">
                                    <i class="bi bi-x-lg text-xs text-white"></i>
                                </button>
                            </div>
                        {% endfor %}
                        </div>
                        <style>
                            @keyframes toastSlideDown {
                                0% { opacity: 0; transform: translateY(-20px) scale(0.95); }
                                100% { opacity: 1; transform: translateY(0) scale(1); }
                            }
                            .toast-hide {
                                opacity: 0 !important;
                                transform: translateY(-20px) scale(0.95) !important;
                                margin-top: -80px !important; /* collapse height */
                            }
                        </style>
                        <script>
                            function closeToast(el) {
                                el.classList.add('toast-hide');
                                setTimeout(() => el.remove(), 500);
                            }
                            // Auto dismiss after 4 seconds
                            setTimeout(() => {
                                document.querySelectorAll('.toast-message').forEach(toast => closeToast(toast));
                            }, 4000);
                        </script>
                    {% endif %}
                {% endwith %}
                <!-- Column Headers -->
                <div class="col-header px-3 sm:px-4 py-2 flex shrink-0 select-none">
                    <div class="flex items-center edit-mode-section" style="width:40px;">
                        <input type="checkbox" id="selectAllCheckbox" class="rounded border-white/20 bg-black/20 text-indigo-500 focus:ring-indigo-500 cursor-pointer w-3.5 h-3.5" onclick="toggleAllFiles(this)">
                    </div>
                    <div class="file-col-name cursor-pointer hover:text-white transition-colors flex items-center" style="width:calc(70% - 40px);" onclick="sortFiles('name')">Name <i class="bi bi-arrow-down-up ml-1.5 text-[9px] opacity-40"></i></div>
                    <div class="text-center hidden md:flex items-center justify-center cursor-pointer hover:text-white transition-colors" style="width:10%;" onclick="sortFiles('size')">Size <i class="bi bi-arrow-down-up ml-1.5 text-[9px] opacity-40"></i></div>
                    <div class="text-center hidden md:block" style="width:10%;">Version</div>
                    <div class="text-center hidden md:flex items-center justify-center cursor-pointer hover:text-white transition-colors" style="width:8%;" onclick="sortFiles('date')">Date <i class="bi bi-arrow-down-up ml-1.5 text-[9px] opacity-40"></i></div>
                    <div class="text-right file-col-actions" style="width:30%;">Actions</div>
                </div>
                <!-- File List -->
                <div class="overflow-y-auto flex-1">
                    {% if directory != base_directory %}
                        <div class="file-item flex items-center px-3 sm:px-4 py-2.5 cursor-pointer text-sm">
                            <a href="{{ url_for('list_files', req_path=parent_path) }}" class="flex items-center w-full no-underline" style="color:#7d8590;">
                                <div class="w-7 text-center text-base mr-2.5 shrink-0" style="color:#5865f2;">
                                    <i class="bi bi-arrow-up-circle-fill"></i>
                                </div>
                                <div class="font-medium text-xs">.. Back</div>
                            </a>
                        </div>
                    {% endif %}
                    {% for file, size, version, created in files %}
                        {% set is_dir = file.endswith('/') %}
                        {% set filename_only = os.path.basename(file.rstrip('/')) %}
                        <div class="file-item flex items-center px-3 sm:px-4 py-2.5 text-sm group" data-filename="{{ filename_only }}" data-isdir="{{ '1' if is_dir else '0' }}" data-size="{{ size }}" data-date="{{ created }}">
                            <div class="flex items-center edit-mode-section" style="width:40px;">
                                <input type="checkbox" class="file-checkbox rounded border-white/20 bg-black/20 text-indigo-500 focus:ring-indigo-500 cursor-pointer w-3.5 h-3.5" value="{{ file }}" onclick="handleCheckboxClick(event, this)">
                            </div>
                            <!-- Name column: 70% on mobile, 42% on desktop -->
                            <div class="file-col-name flex items-center overflow-hidden" style="width:calc(70% - 40px);">
                                {% if not is_dir and file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.mp4', '.mov', '.avi', '.mkv', '.webm', '.mp3', '.wav', '.ogg', '.flac', '.txt', '.py', '.js', '.html', '.css', '.json', '.md', '.ini', '.yml', '.sh', '.conf', '.sql', '.sp', '.trigger')) %}
                                <a href="{{ url_for('list_files', req_path=file) }}" onclick="openLightbox('{{ url_for('list_files', req_path=file) }}', '{{ filename_only }}', event)" class="flex items-center w-full no-underline truncate" style="color:#c9d1d9;">
                                {% else %}
                                <a href="{{ url_for('list_files', req_path=file) }}" class="flex items-center w-full no-underline truncate" style="color:#c9d1d9;">
                                {% endif %}
                                    <div class="w-6 sm:w-7 text-center text-sm sm:text-base mr-2 sm:mr-3 shrink-0
                                        {% if is_dir %} text-yellow-400
                                        {% elif file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')) %} text-cyan-400
                                        {% elif file.lower().endswith('.pdf') %} text-red-400
                                        {% elif file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')) %} text-green-400
                                        {% elif file.lower().endswith(('.zip', '.rar', '.7z')) %} text-orange-400
                                        {% elif file.lower().endswith(('.exe', '.dll')) %} text-purple-400
                                        {% else %} text-blue-400
                                        {% endif %}">
                                        {% if is_dir %}<i class="bi bi-folder-fill"></i>
                                        {% elif file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')) %}<i class="bi bi-file-earmark-image-fill"></i>
                                        {% elif file.lower().endswith(('.zip', '.rar', '.7z')) %}<i class="bi bi-file-earmark-zip-fill"></i>
                                        {% elif file.lower().endswith('.pdf') %}<i class="bi bi-file-earmark-pdf-fill"></i>
                                        {% elif file.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')) %}<i class="bi bi-file-earmark-play-fill"></i>
                                        {% elif file.lower().endswith(('.exe', '.dll')) %}<i class="bi bi-filetype-exe"></i>
                                        {% else %}<i class="bi bi-file-earmark-text-fill"></i>
                                        {% endif %}
                                    </div>
                                    <span class="truncate text-xs sm:text-sm editable-name">{{ filename_only }}</span>
                                </a>
                            </div>
                            <!-- Desktop-only metadata columns -->
                            <div class="text-center hidden md:block text-xs" style="width:10%; color:#7d8590;">{{ size }}</div>
                            <div class="text-center hidden md:block text-xs" style="width:10%; color:#7d8590;">{{ version }}</div>
                            <div class="text-center hidden md:block text-xs" style="width:8%; color:#7d8590;">{{ created.split(' ')[0] }}</div>
                            <!-- Actions column: always visible, 30% on mobile / 20% on desktop -->
                            <div class="file-actions-wrap file-col-actions flex justify-end items-center md:opacity-0 md:group-hover:opacity-100 transition-all duration-150" style="width:30%;">
                                <!-- Mobile toggle -->
                                <button type="button" class="mobile-action-toggle hidden" onclick="toggleRadialMenu(event, this)">
                                    <i class="bi bi-three-dots-vertical"></i>
                                </button>
                                
                                <div class="action-buttons-container flex justify-end items-center gap-1">
                                    {% if file.endswith(('.zip')) %}
                                    <button type="button" class="row-action edit-mode-section" title="Extract"
                                            style="background:rgba(88,101,242,0.25);border-color:rgba(88,101,242,0.4);"
                                            onclick="openModal('extractModal'); setModalData('extractModal', {targetPath: '{{ file }}', fileName: '{{ filename_only }}'})">
                                        <i class="bi bi-box-seam"></i>
                                    </button>
                                {% endif %}
                                {% if not file.endswith(('.zip')) %}
                                    <button type="button" class="row-action edit-mode-section" title="Compress"
                                            style="background:rgba(88,101,242,0.25);border-color:rgba(88,101,242,0.4);"
                                            onclick="openModal('compressModal'); setModalData('compressModal', {targetPath: '{{ file }}', fileName: '{{ filename_only }}'})">
                                        <i class="bi bi-file-zip-fill"></i>
                                    </button>
                                {% endif %}
                                <button type="button" class="row-action edit-mode-section" title="Rename"
                                        style="background:rgba(234,179,8,0.2);border-color:rgba(234,179,8,0.35);"
                                        onclick="triggerRename('{{ filename_only }}')">
                                    <i class="bi bi-pencil-square"></i>
                                </button>
                                <button type="button" class="row-action edit-mode-section" title="Move"
                                        style="background:rgba(20,184,166,0.2);border-color:rgba(20,184,166,0.35);"
                                        onclick="openModal('moveModal'); setModalData('moveModal', {sourcePath: '{{ file }}', fileName: '{{ filename_only }}'})">
                                    <i class="bi bi-arrows-move"></i>
                                </button>
                                <button type="button" class="row-action" title="Share Link"
                                        style="background:rgba(20,184,166,0.2);border-color:rgba(20,184,166,0.35);"
                                        onclick="event.preventDefault(); event.stopPropagation(); openModal('shareLinkModal'); setModalData('shareLinkModal', {filePath: '{{ file }}'})">
                                    <i class="bi bi-share-fill"></i>
                                </button>
                                    <form action="{{ url_for('delete_file') }}" method="post" class="inline-flex items-center edit-mode-section">
                                        <input type="hidden" name="file_path" value="{{ file }}">
                                        <button type="submit" class="row-action" title="Delete"
                                                style="background:rgba(239,68,68,0.2);border-color:rgba(239,68,68,0.35);"
                                                onclick="event.preventDefault(); showCustomConfirm('Are you sure you want to delete {{ filename_only }}?', this.closest('form'));"><i class="bi bi-trash-fill"></i></button>
                                    </form>
                                </div>
                            </div>
                        </div>
                    {% else %}
                        <div class="flex flex-col items-center justify-center py-20 text-slate-500">
                            <i class="bi bi-box2 text-6xl mb-4" style="color:rgba(255,255,255,0.15); drop-shadow: 0 4px 6px rgba(0,0,0,0.3);"></i>
                            <p class="text-base font-semibold tracking-wide text-slate-300">This folder is empty</p>
                            <p class="text-xs mt-1 text-slate-500">No files found in this directory.</p>
                        </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>
</div>
<!-- macOS-style Status Bar -->
<div class="status-bar fixed bottom-0 left-0 right-0 flex items-center justify-between px-3 sm:px-4 z-50 select-none" style="padding-top: 0.375rem; padding-bottom: calc(0.375rem + env(safe-area-inset-bottom));">
    <div class="flex items-center gap-2 sm:gap-3 overflow-hidden">
        <span class="text-[11px] sm:text-xs font-medium whitespace-nowrap truncate" style="color:#7d8590;">WAN File Station</span>
        <button id="checkSystemStatsBtnMobile" class="btn-glass px-2 sm:px-2.5 py-1 rounded-md text-xs flex items-center gap-1.5 flex-shrink-0" onclick="openModal('systemStatsModal')">
            <i class="bi bi-hdd-rack-fill text-xs" style="color:#10b981;"></i>
            <span class="hidden sm:inline" style="color:#c9d1d9;">Resources</span>
        </button>
    </div>
    <a href="{{ url_for('logout') }}" class="btn-glass px-2 sm:px-3 py-1 rounded-md text-[11px] sm:text-xs flex items-center gap-1 flex-shrink-0 no-underline" style="color:#ef4444; margin-left: 0.5rem;">
        <i class="bi bi-box-arrow-right"></i>
        <span>Logout</span>
    </a>
</div>
<!-- Lightbox Modal -->
<div id="lightboxOverlay" class="fixed inset-0 z-[100] hidden flex-col items-center justify-center bg-black/80 backdrop-blur-md opacity-0 transition-opacity duration-300">
    <div class="absolute top-4 right-4 flex gap-3 z-10">
        <button onclick="closeLightbox()" class="w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors border border-white/10" aria-label="Close Preview" title="Close (Esc)">
            <i class="bi bi-x-lg text-lg"></i>
        </button>
    </div>
    <div class="absolute top-4 left-4 z-10 max-w-[calc(100vw-5rem)] sm:max-w-[75vw]">
        <p id="lightboxTitle" class="text-white/90 font-medium text-xs sm:text-sm px-3 sm:px-4 py-2 bg-black/50 rounded-lg border border-white/10 backdrop-blur-md truncate" title=""></p>
    </div>
    <img id="lightboxImg" class="hidden max-w-[90%] max-h-[85vh] object-contain rounded-lg shadow-2xl transition-transform duration-300 scale-95 relative z-0" src="" alt="Preview">
    
    <!-- PDF Viewer Container -->
    <div id="lightboxPdfContainer" class="hidden w-[94%] max-w-5xl flex flex-col bg-[#161b22] border border-white/10 rounded-xl overflow-hidden shadow-2xl transition-transform duration-300 scale-95 relative z-0">
        <div class="flex items-center justify-between px-3 sm:px-4 py-2.5 bg-[#0d1117] border-b border-white/10 gap-2 flex-wrap sm:flex-nowrap">
            <div class="flex items-center gap-2 truncate pr-1 min-w-0">
                <i class="bi bi-file-earmark-pdf-fill text-red-400 shrink-0"></i>
                <span id="lightboxPdfTitle" class="text-xs sm:text-sm font-semibold text-slate-200 truncate"></span>
            </div>
            <!-- Toolbar for page navigation and zoom -->
            <!-- Toolbar for page navigation and zoom (R-03: Accessible Tap Targets) -->
            <div id="pdfToolbar" class="flex items-center gap-1.5 shrink-0 bg-white/5 px-2.5 py-1 rounded-lg border border-white/10 text-xs mx-auto sm:mx-0">
                <button type="button" id="pdfPrevBtn" onclick="scrollPdfToPage(pdfCurrentPageNum - 1)" class="w-7 h-7 sm:w-8 sm:h-8 rounded-md flex items-center justify-center text-slate-300 hover:text-white hover:bg-white/10 transition-colors disabled:opacity-30 disabled:pointer-events-none focus:outline-none focus:ring-1 focus:ring-indigo-500" title="Previous Page (ArrowUp / ArrowLeft)">
                    <i class="bi bi-chevron-up text-xs"></i>
                </button>
                <div class="flex items-center gap-1 px-1.5 text-slate-200 text-xs font-mono select-none">
                    <input type="number" id="pdfPageInput" min="1" value="1" onchange="jumpPdfToPage(this.value)" class="w-9 sm:w-11 text-center bg-black/40 border border-white/10 rounded px-1 py-0.5 text-xs text-white focus:outline-none focus:border-indigo-500 font-mono" title="Current sheet number">
                    <span class="text-slate-400">/</span>
                    <span id="pdfTotalPages" class="text-slate-300">1</span>
                </div>
                <button type="button" id="pdfNextBtn" onclick="scrollPdfToPage(pdfCurrentPageNum + 1)" class="w-7 h-7 sm:w-8 sm:h-8 rounded-md flex items-center justify-center text-slate-300 hover:text-white hover:bg-white/10 transition-colors disabled:opacity-30 disabled:pointer-events-none focus:outline-none focus:ring-1 focus:ring-indigo-500" title="Next Page (ArrowDown / ArrowRight)">
                    <i class="bi bi-chevron-down text-xs"></i>
                </button>
                <div class="w-px h-4 bg-white/10 mx-1"></div>
                <button type="button" onclick="zoomPdf(-0.2)" class="w-7 h-7 sm:w-8 sm:h-8 rounded-md flex items-center justify-center text-slate-300 hover:text-white hover:bg-white/10 transition-colors focus:outline-none focus:ring-1 focus:ring-indigo-500" title="Zoom Out">
                    <i class="bi bi-dash text-sm"></i>
                </button>
                <span id="pdfZoomLabel" class="text-[11px] font-mono font-medium text-slate-300 px-1 select-none min-w-[38px] text-center">100%</span>
                <button type="button" onclick="zoomPdf(0.2)" class="w-7 h-7 sm:w-8 sm:h-8 rounded-md flex items-center justify-center text-slate-300 hover:text-white hover:bg-white/10 transition-colors focus:outline-none focus:ring-1 focus:ring-indigo-500" title="Zoom In">
                    <i class="bi bi-plus text-sm"></i>
                </button>
                <button type="button" onclick="resetPdfZoom()" class="px-2 py-1 rounded text-[11px] font-mono font-medium text-slate-300 hover:text-white hover:bg-white/10 border border-white/5 transition-colors focus:outline-none focus:ring-1 focus:ring-indigo-500" title="Fit to Width">
                    Fit
                </button>
            </div>
            <div class="flex items-center gap-2 shrink-0">
                <span id="lightboxPdfFormat" class="px-2 py-0.5 text-[10px] font-mono font-medium rounded bg-red-500/20 text-red-300 border border-red-500/30">PDF</span>
                <a id="lightboxPdfNewTab" href="#" target="_blank" rel="noopener noreferrer" class="px-2.5 py-1 text-xs font-medium rounded-lg bg-white/10 hover:bg-white/20 text-slate-200 hover:text-white transition-colors flex items-center gap-1.5 border border-white/10" title="Open PDF in new tab">
                    <i class="bi bi-box-arrow-up-right text-[11px]"></i>
                    <span class="hidden sm:inline">Open in Tab</span>
                </a>
            </div>
        </div>
        <div id="pdfViewerArea" class="relative bg-[#0b0f17] flex-1 w-full h-[70vh] sm:h-[78vh] overflow-y-auto overflow-x-auto flex flex-col items-center p-3 sm:p-6 select-none scroll-smooth">
            <div id="pdfLoadingIndicator" class="flex flex-col items-center justify-center m-auto text-slate-300 py-16">
                <i class="bi bi-arrow-repeat animate-spin text-3xl text-indigo-400 mb-2"></i>
                <span class="text-xs font-medium text-slate-300">Rendering PDF document...</span>
            </div>
            <!-- Multi-Sheet Container: Real Paper Sheet Aesthetics -->
            <div id="pdfPagesContainer" class="flex flex-col items-center w-full max-w-full space-y-6"></div>
            <div id="pdfFallbackNotice" class="hidden flex flex-col items-center justify-center m-auto text-center p-6 max-w-md">
                <i class="bi bi-file-earmark-pdf text-red-400 text-4xl mb-3"></i>
                <p class="text-sm font-semibold text-slate-100 mb-1">Cannot render PDF preview</p>
                <p class="text-xs text-slate-400 mb-4">Your browser was unable to render this document. You can open it in a new tab or download the file.</p>
                <div class="flex items-center gap-3">
                    <a id="pdfFallbackNewTabBtn" href="#" target="_blank" rel="noopener noreferrer" class="px-4 py-2 text-xs font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors flex items-center gap-2"><i class="bi bi-box-arrow-up-right"></i> Open in New Tab</a>
                    <a id="pdfFallbackDownloadBtn" href="#" class="px-4 py-2 text-xs font-medium rounded-lg bg-white/10 hover:bg-white/20 text-slate-200 border border-white/10 transition-colors flex items-center gap-2"><i class="bi bi-download"></i> Download</a>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Video Player Container (Anti-Slop Craftsmanship) -->
    <div id="lightboxVideoContainer" class="hidden w-[94%] max-w-4xl flex flex-col bg-[#161b22] border border-white/10 rounded-xl overflow-hidden shadow-2xl transition-transform duration-300 scale-95 relative z-0">
        <div class="flex items-center justify-between px-4 py-2.5 bg-[#0d1117] border-b border-white/10">
            <div class="flex items-center gap-2 truncate pr-2">
                <i class="bi bi-play-circle-fill text-blue-400"></i>
                <span id="lightboxVideoTitle" class="text-xs sm:text-sm font-semibold text-slate-200 truncate"></span>
            </div>
            <span id="lightboxVideoFormat" class="px-2 py-0.5 text-[10px] font-mono font-medium rounded bg-white/10 text-slate-300 shrink-0">MP4</span>
        </div>
        <div class="relative bg-black flex items-center justify-center min-h-[200px] max-h-[75vh]">
            <video id="lightboxVideo" class="w-full max-h-[75vh] object-contain" controls preload="metadata" playsinline></video>
            <div id="videoErrorMessage" class="hidden p-6 text-center text-slate-300 text-xs sm:text-sm">
                <i class="bi bi-exclamation-triangle text-amber-400 text-2xl mb-2 block"></i>
                Browser cannot play this video codec directly. You can download the file to play it locally.
            </div>
        </div>
    </div>

    <!-- Audio Player Container (Anti-Slop Craftsmanship) -->
    <div id="lightboxAudioContainer" class="hidden w-[92%] max-w-md bg-[#161b22] border border-white/10 rounded-xl p-5 sm:p-6 shadow-2xl transition-transform duration-300 scale-95 relative z-0">
        <div class="flex items-center gap-3.5 mb-4">
            <div class="w-12 h-12 rounded-lg bg-blue-600/20 border border-blue-500/30 text-blue-400 flex items-center justify-center shrink-0">
                <i class="bi bi-music-note-beamed text-2xl"></i>
            </div>
            <div class="overflow-hidden min-w-0 flex-1">
                <div class="flex items-center gap-2 mb-0.5">
                    <span id="lightboxAudioFormat" class="px-1.5 py-0.5 text-[9px] font-mono font-semibold uppercase rounded bg-white/10 text-slate-300">AUDIO</span>
                </div>
                <h3 id="lightboxAudioTitle" class="text-sm font-semibold text-slate-100 truncate"></h3>
                <p class="text-xs text-slate-400 mt-0.5">Audio Player</p>
            </div>
        </div>
        <div class="bg-[#0d1117] rounded-lg p-2 border border-white/5">
            <audio id="lightboxAudio" class="w-full" controls preload="metadata"></audio>
        </div>
        <div id="audioErrorMessage" class="hidden text-center text-slate-300 text-xs mt-3">
            <i class="bi bi-exclamation-circle text-amber-400 mr-1"></i>
            Audio stream error or format not supported by browser.
        </div>
    </div>

    <div id="lightboxEditor" class="hidden w-[90%] max-w-5xl h-[85vh] rounded-lg shadow-2xl relative z-0 border border-white/10 overflow-hidden bg-[#1e1e1e] text-left"></div>
    <div class="mt-5 z-10 flex gap-3">
        <button id="lightboxSaveBtn" class="hidden px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs sm:text-sm font-medium transition-colors flex items-center gap-2" onclick="saveEditorContent()">
            <i class="bi bi-save2"></i> <span class="hidden sm:inline">Save Changes</span><span class="sm:hidden">Save</span>
        </button>
        <a id="lightboxDownloadBtn" href="#" class="px-5 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white text-xs sm:text-sm font-medium transition-colors border border-white/10 backdrop-blur-md flex items-center gap-2" title="Download file">
            <i class="bi bi-download"></i> <span id="lightboxDownloadText">Download</span>
        </a>
    </div>
</div>

<!-- Floating Bulk Action Bar -->
<div id="bulkActionBar" class="fixed bottom-4 sm:bottom-12 left-1/2 -translate-x-1/2 z-[90] hidden flex-col sm:flex-row items-center gap-3 sm:gap-3 px-4 sm:px-5 py-3 rounded-2xl sm:rounded-full bg-slate-800/95 backdrop-blur-md border border-white/10 shadow-2xl transition-all duration-300 translate-y-10 opacity-0 text-xs sm:text-sm w-[92%] sm:w-auto max-w-max">
    <!-- Top row (Mobile only: selection count + close) -->
    <div class="flex items-center justify-between w-full sm:w-auto shrink-0">
        <span class="text-slate-200 font-semibold whitespace-nowrap ml-1"><span id="bulkSelectedCount">0</span> selected</span>
        <button onclick="clearBulkSelection()" class="w-7 h-7 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center transition-colors shrink-0 sm:hidden">
            <i class="bi bi-x-lg text-xs text-slate-300"></i>
        </button>
    </div>
    
    <!-- Divider (Mobile only) -->
    <div class="w-full h-px bg-white/10 sm:hidden"></div>
    
    <!-- Actions row -->
    <div class="flex flex-row items-center gap-2 sm:gap-3 w-full sm:w-auto justify-between sm:justify-start">
        <form id="bulkDeleteForm" action="{{ url_for('bulk_delete') }}" method="POST" class="m-0 shrink-0">
            <input type="hidden" name="files" id="bulkDeleteFiles">
            <input type="hidden" name="current_path" value="{{ req_path }}">
            <button type="submit" class="px-3 py-1.5 rounded-full bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/30 transition-colors flex items-center gap-1.5 whitespace-nowrap" onclick="event.preventDefault(); showCustomConfirm('Are you sure you want to delete the selected items?', this.closest('form'));">
                <i class="bi bi-trash-fill"></i> <span class="hidden min-[380px]:inline">Delete</span>
            </button>
        </form>
        
        <div class="w-px h-5 bg-white/10 shrink-0 hidden sm:block"></div>
        
        <form id="bulkCompressForm" action="{{ url_for('bulk_compress') }}" method="POST" class="m-0 flex items-center gap-2 shrink-0 flex-1 sm:flex-initial justify-end">
            <input type="hidden" name="files" id="bulkCompressFiles">
            <input type="hidden" name="current_path" value="{{ req_path }}">
            <input type="text" name="output_name" placeholder="archive.zip" class="bg-black/20 border border-white/10 rounded-full px-3 py-1.5 text-xs text-white placeholder-slate-400 focus:outline-none focus:border-indigo-500 w-24 sm:w-32 min-w-0" required>
            <button type="submit" class="px-3 py-1.5 rounded-full bg-indigo-500/20 text-indigo-400 hover:bg-indigo-500/30 border border-indigo-500/30 transition-colors flex items-center gap-1.5 whitespace-nowrap shrink-0">
                <i class="bi bi-file-earmark-zip-fill"></i> <span class="hidden min-[380px]:inline">Compress</span>
            </button>
        </form>
        
        <!-- Close button (Desktop only) -->
        <button onclick="clearBulkSelection()" class="hidden sm:flex w-7 h-7 rounded-full bg-white/10 hover:bg-white/20 items-center justify-center transition-colors shrink-0">
            <i class="bi bi-x-lg text-xs text-slate-300"></i>
        </button>
    </div>
</div>

{% include 'modals.html' %}
<script>
    const editPin = "{{ edit_pin }}";

    // --- Theme Toggle Logic ---
    const themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            if (document.documentElement.classList.contains('dark')) {
                document.documentElement.classList.remove('dark');
                localStorage.setItem('theme', 'light');
            } else {
                document.documentElement.classList.add('dark');
                localStorage.setItem('theme', 'dark');
            }
        });
    }

    function navigateToPath() {
        const path = document.getElementById('pathInput').value;
        if (path) {
            window.location.href = "{{ url_for('list_files') }}" + (path.startsWith('/') ? path.substring(1) : path);
        }
    }

    // Global state for current editing file
    let currentEditingFileUrl = "";
    let currentEditingFilePath = "";

    // PDF.js State & Multi-Sheet Helpers (Anti-Slop Craftsmanship)
    if (window.pdfjsLib) {
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    }
    let pdfDocInstance = null;
    let pdfCurrentPageNum = 1;
    let pdfCurrentZoom = 1.0;
    let pdfObserver = null;
    let pdfRenderGeneration = 0;

    async function renderPdfDocument() {
        if (!pdfDocInstance) return;
        const currentGen = ++pdfRenderGeneration;
        const container = document.getElementById('pdfPagesContainer');
        const loading = document.getElementById('pdfLoadingIndicator');
        const fallback = document.getElementById('pdfFallbackNotice');
        
        if (!container) return;
        container.innerHTML = '';
        if (loading) loading.classList.remove('hidden');
        if (fallback) fallback.classList.add('hidden');
        
        const totalPages = pdfDocInstance.numPages;
        const totalEl = document.getElementById('pdfTotalPages');
        if (totalEl) totalEl.textContent = totalPages;
        
        const pageInput = document.getElementById('pdfPageInput');
        if (pageInput) {
            pageInput.max = totalPages;
            pageInput.value = pdfCurrentPageNum;
        }

        const zoomLabel = document.getElementById('pdfZoomLabel');
        if (zoomLabel) {
            zoomLabel.textContent = Math.round(pdfCurrentZoom * 100) + '%';
        }

        const viewerArea = document.getElementById('pdfViewerArea');
        const availableWidth = viewerArea ? Math.max(300, viewerArea.clientWidth - 56) : 800;
        
        if (pdfObserver) {
            pdfObserver.disconnect();
            pdfObserver = null;
        }

        try {
            // Pre-create sheet shells for natural continuous scroll height
            for (let num = 1; num <= totalPages; num++) {
                const sheetWrapper = document.createElement('div');
                sheetWrapper.id = `pdfSheet_${num}`;
                sheetWrapper.dataset.pageNum = num;
                sheetWrapper.className = 'pdf-sheet-wrapper relative flex flex-col items-center transition-all duration-150';
                
                // Floating Sheet Badge
                const pill = document.createElement('div');
                pill.className = 'pdf-sheet-pill mb-2 self-start px-2.5 py-0.5 rounded text-[11px] font-mono font-medium bg-[#161b22]/90 text-slate-300 border border-white/10 shadow-sm flex items-center gap-1.5 select-none';
                pill.innerHTML = `<i class="bi bi-file-earmark-text text-red-400"></i> Sheet ${num} of ${totalPages}`;
                
                // Real Paper Card Appearance
                const card = document.createElement('div');
                card.className = 'pdf-sheet-card bg-white rounded-sm shadow-2xl overflow-hidden border border-black/15';
                card.style.boxShadow = '0 8px 30px rgba(0,0,0,0.5), 0 1px 3px rgba(0,0,0,0.3)';
                
                const canvas = document.createElement('canvas');
                canvas.className = 'pdf-sheet-canvas block';
                canvas.id = `pdfCanvas_${num}`;
                
                card.appendChild(canvas);
                sheetWrapper.appendChild(pill);
                sheetWrapper.appendChild(card);
                container.appendChild(sheetWrapper);
            }

            // Sequentially render canvases
            for (let num = 1; num <= totalPages; num++) {
                if (currentGen !== pdfRenderGeneration) return;
                
                const page = await pdfDocInstance.getPage(num);
                const unscaled = page.getViewport({ scale: 1.0 });
                
                let baseScale = availableWidth / unscaled.width;
                if (baseScale > 1.35) baseScale = 1.35;
                if (baseScale < 0.35) baseScale = 0.35;
                let scale = baseScale * pdfCurrentZoom;
                
                const dpr = window.devicePixelRatio || 1;
                const viewport = page.getViewport({ scale: scale });
                
                const canvas = document.getElementById(`pdfCanvas_${num}`);
                if (!canvas) continue;
                
                canvas.width = Math.floor(viewport.width * dpr);
                canvas.height = Math.floor(viewport.height * dpr);
                canvas.style.width = Math.floor(viewport.width) + 'px';
                canvas.style.height = Math.floor(viewport.height) + 'px';
                
                const ctx = canvas.getContext('2d');
                ctx.scale(dpr, dpr);
                
                await page.render({
                    canvasContext: ctx,
                    viewport: viewport
                }).promise;
            }

            if (loading) loading.classList.add('hidden');
            setupPdfScrollSpy();
            updatePdfNavButtons();

        } catch (err) {
            console.error('PDF multi-sheet render error', err);
            if (loading) loading.classList.add('hidden');
            if (fallback) fallback.classList.remove('hidden');
        }
    }

    function setupPdfScrollSpy() {
        const viewerArea = document.getElementById('pdfViewerArea');
        if (!viewerArea) return;
        
        pdfObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting && entry.intersectionRatio >= 0.35) {
                    const num = parseInt(entry.target.dataset.pageNum, 10);
                    if (num && num !== pdfCurrentPageNum) {
                        pdfCurrentPageNum = num;
                        const pageInput = document.getElementById('pdfPageInput');
                        if (pageInput) pageInput.value = num;
                        updatePdfNavButtons();
                    }
                }
            });
        }, {
            root: viewerArea,
            threshold: [0.35, 0.7]
        });

        document.querySelectorAll('.pdf-sheet-wrapper').forEach(sheet => {
            pdfObserver.observe(sheet);
        });
    }

    function scrollPdfToPage(num) {
        if (!pdfDocInstance) return;
        if (num < 1) num = 1;
        if (num > pdfDocInstance.numPages) num = pdfDocInstance.numPages;
        pdfCurrentPageNum = num;
        
        const targetSheet = document.getElementById(`pdfSheet_${num}`);
        if (targetSheet) {
            targetSheet.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        const pageInput = document.getElementById('pdfPageInput');
        if (pageInput) pageInput.value = num;
        updatePdfNavButtons();
    }

    function jumpPdfToPage(val) {
        let num = parseInt(val, 10);
        if (isNaN(num)) num = 1;
        scrollPdfToPage(num);
    }

    function updatePdfNavButtons() {
        if (!pdfDocInstance) return;
        const prev = document.getElementById('pdfPrevBtn');
        const next = document.getElementById('pdfNextBtn');
        if (prev) prev.disabled = (pdfCurrentPageNum <= 1);
        if (next) next.disabled = (pdfCurrentPageNum >= pdfDocInstance.numPages);
    }

    function zoomPdf(delta) {
        pdfCurrentZoom += delta;
        if (pdfCurrentZoom < 0.4) pdfCurrentZoom = 0.4;
        if (pdfCurrentZoom > 2.5) pdfCurrentZoom = 2.5;
        renderPdfDocument();
    }

    function resetPdfZoom() {
        pdfCurrentZoom = 1.0;
        renderPdfDocument();
    }

    // Lightbox modal logic
    function openLightbox(url, filename, event) {
        if(event) { event.preventDefault(); event.stopPropagation(); }
        const overlay = document.getElementById('lightboxOverlay');
        const img = document.getElementById('lightboxImg');
        const pdfContainer = document.getElementById('lightboxPdfContainer');
        const pdfTitle = document.getElementById('lightboxPdfTitle');
        const pdfNewTab = document.getElementById('lightboxPdfNewTab');
        const pdfFallbackNotice = document.getElementById('pdfFallbackNotice');
        const pdfFallbackNewTabBtn = document.getElementById('pdfFallbackNewTabBtn');
        const pdfFallbackDownloadBtn = document.getElementById('pdfFallbackDownloadBtn');
        const video = document.getElementById('lightboxVideo');
        const videoContainer = document.getElementById('lightboxVideoContainer');
        const videoTitle = document.getElementById('lightboxVideoTitle');
        const videoFormat = document.getElementById('lightboxVideoFormat');
        const videoErr = document.getElementById('videoErrorMessage');
        const audio = document.getElementById('lightboxAudio');
        const audioContainer = document.getElementById('lightboxAudioContainer');
        const audioTitle = document.getElementById('lightboxAudioTitle');
        const audioFormat = document.getElementById('lightboxAudioFormat');
        const audioErr = document.getElementById('audioErrorMessage');
        const editor = document.getElementById('lightboxEditor');
        const title = document.getElementById('lightboxTitle');
        const downloadBtn = document.getElementById('lightboxDownloadBtn');
        const downloadText = document.getElementById('lightboxDownloadText');
        const saveBtn = document.getElementById('lightboxSaveBtn');
        
        title.textContent = filename;
        title.title = filename;
        downloadBtn.href = url; // Always direct download
        
        const previewUrl = url + (url.includes('?') ? '&' : '?') + 'preview=1';
        
        // Hide all first
        img.classList.add('hidden'); img.src = '';
        if(pdfContainer) pdfContainer.classList.add('hidden');
        if(pdfFallbackNotice) pdfFallbackNotice.classList.add('hidden');
        if(videoContainer) videoContainer.classList.add('hidden');
        if(audioContainer) audioContainer.classList.add('hidden');
        if(videoErr) videoErr.classList.add('hidden');
        if(audioErr) audioErr.classList.add('hidden');
        if(video) { video.pause(); video.src = ''; }
        if(audio) { audio.pause(); audio.src = ''; }
        if(editor) editor.classList.add('hidden');
        if(saveBtn) saveBtn.classList.add('hidden');
        
        const ext = filename.toLowerCase();
        let targetEl = null;
        const textExts = ['.txt', '.py', '.js', '.html', '.css', '.json', '.md', '.ini', '.yml', '.sh', '.conf', '.sql', '.sp', '.trigger'];
        const isText = textExts.some(e => ext.endsWith(e));
        
        if (isText && editor) {
            targetEl = editor;
            if(downloadText) downloadText.textContent = "Download Text";
            // Check if edit mode is active, if so show save button
            if (document.body.classList.contains('edit-mode-active') && saveBtn) {
                saveBtn.classList.remove('hidden');
            }
            
            // Extract the relative path from the url parameter req_path
            const urlObj = new URL(url, window.location.origin);
            currentEditingFilePath = urlObj.searchParams.get('req_path') || filename;
            
            // Set Monaco language based on extension
            let lang = "plaintext";
            if(ext.endsWith('.py')) lang = "python";
            else if(ext.endsWith('.js')) lang = "javascript";
            else if(ext.endsWith('.html')) lang = "html";
            else if(ext.endsWith('.css')) lang = "css";
            else if(ext.endsWith('.json')) lang = "json";
            else if(ext.endsWith('.md')) lang = "markdown";
            else if(ext.endsWith('.sql') || ext.endsWith('.sp') || ext.endsWith('.trigger')) lang = "sql";
            else if(ext.endsWith('.sh')) lang = "shell";
            else if(ext.endsWith('.ini') || ext.endsWith('.conf')) lang = "ini";
            else if(ext.endsWith('.yml')) lang = "yaml";
            
            // Fetch content
            if (window.monacoEditorInstance) {
                window.monacoEditorInstance.setValue("Loading...");
                monaco.editor.setModelLanguage(window.monacoEditorInstance.getModel(), lang);
                
                fetch(previewUrl)
                    .then(r => r.text())
                    .then(text => {
                        window.monacoEditorInstance.setValue(text);
                    }).catch(e => {
                        window.monacoEditorInstance.setValue("Error loading file: " + e);
                    });
            }
        } else if (ext.endsWith('.pdf')) {
            targetEl = pdfContainer;
            if(pdfTitle) pdfTitle.textContent = filename;
            if(pdfNewTab) pdfNewTab.href = previewUrl;
            if(pdfFallbackNewTabBtn) pdfFallbackNewTabBtn.href = previewUrl;
            if(pdfFallbackDownloadBtn) pdfFallbackDownloadBtn.href = url;
            
            const loading = document.getElementById('pdfLoadingIndicator');
            const fallback = document.getElementById('pdfFallbackNotice');
            const container = document.getElementById('pdfPagesContainer');
            if(container) container.innerHTML = '';
            if(loading) loading.classList.remove('hidden');
            if(fallback) fallback.classList.add('hidden');
            
            pdfDocInstance = null;
            pdfCurrentPageNum = 1;
            pdfCurrentZoom = 1.0;
            
            if (window.pdfjsLib) {
                pdfjsLib.getDocument(previewUrl).promise.then(function(doc) {
                    pdfDocInstance = doc;
                    renderPdfDocument();
                }).catch(function(err) {
                    console.error('Failed to load PDF via PDF.js', err);
                    if(loading) loading.classList.add('hidden');
                    if(fallback) fallback.classList.remove('hidden');
                });
            } else {
                if(loading) loading.classList.add('hidden');
                if(fallback) fallback.classList.remove('hidden');
            }
            if(downloadText) downloadText.textContent = "Download PDF";
        } else if (ext.endsWith('.mp4') || ext.endsWith('.mov') || ext.endsWith('.avi') || ext.endsWith('.mkv') || ext.endsWith('.webm')) {
            targetEl = videoContainer;
            if(videoTitle) videoTitle.textContent = filename;
            if(videoFormat) videoFormat.textContent = ext.split('.').pop().toUpperCase();
            if(video) {
                video.onerror = () => { if(videoErr) videoErr.classList.remove('hidden'); };
                video.src = previewUrl;
                video.play().catch(e => console.log('Auto-play prevented', e));
            }
            if(downloadText) downloadText.textContent = "Download Video";
        } else if (ext.endsWith('.mp3') || ext.endsWith('.wav') || ext.endsWith('.ogg') || ext.endsWith('.flac') || ext.endsWith('.m4a') || ext.endsWith('.aac')) {
            targetEl = audioContainer;
            if(audioTitle) audioTitle.textContent = filename;
            if(audioFormat) audioFormat.textContent = ext.split('.').pop().toUpperCase();
            if(audio) {
                audio.onerror = () => { if(audioErr) audioErr.classList.remove('hidden'); };
                audio.src = previewUrl;
                audio.play().catch(e => console.log('Auto-play prevented', e));
            }
            if(downloadText) downloadText.textContent = "Download Audio";
        } else {
            targetEl = img;
            targetEl.src = previewUrl;
            if(downloadText) downloadText.textContent = "Download Image";
        }
        
        if(targetEl) targetEl.classList.remove('hidden');
        
        overlay.classList.remove('hidden');
        overlay.classList.add('flex');
        
        setTimeout(() => {
            overlay.classList.remove('opacity-0');
            if(targetEl) {
                targetEl.classList.remove('scale-95');
                targetEl.classList.add('scale-100');
            }
        }, 10);
    }

    function saveEditorContent() {
        if(!window.monacoEditorInstance) return;
        const btn = document.getElementById('lightboxSaveBtn');
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-arrow-repeat animate-spin"></i> <span class="hidden sm:inline">Saving...</span><span class="sm:hidden">Saving</span>';
        btn.disabled = true;
        
        const content = window.monacoEditorInstance.getValue();
        fetch('/api/save_text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path: currentEditingFilePath, content: content })
        })
        .then(r => r.json())
        .then(data => {
            if(data.success) {
                const toastContainer = document.getElementById('toastContainer');
                if (toastContainer) {
                    const toast = document.createElement('div');
                    toast.className = `toast-message pointer-events-auto p-3.5 sm:p-4 rounded-xl text-sm flex items-center justify-between bg-slate-800/90 shadow-2xl border transition-all duration-300 border-emerald-500/30`;
                    toast.innerHTML = `
                        <div class="flex items-center gap-3">
                            <div class="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-emerald-500/20 text-emerald-400">
                                <i class="bi bi-check-lg text-lg"></i>
                            </div>
                            <span class="text-slate-200 font-medium">File saved successfully!</span>
                        </div>
                        <button type="button" class="ml-4 opacity-60 hover:opacity-100 transition-opacity bg-white/5 hover:bg-white/10 rounded-full w-7 h-7 flex items-center justify-center shrink-0" onclick="closeToast(this.closest('.toast-message'))">
                            <i class="bi bi-x-lg text-xs text-white"></i>
                        </button>
                    `;
                    toastContainer.appendChild(toast);
                    setTimeout(() => closeToast(toast), 4000);
                } else {
                    alert("File saved successfully!");
                }
            } else {
                alert("Failed to save: " + (data.message || data.error || "Unknown error"));
            }
        })
        .catch(err => {
            alert("Error saving file: " + err);
        })
        .finally(() => {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        });
    }

    function closeLightbox() {
        const overlay = document.getElementById('lightboxOverlay');
        const img = document.getElementById('lightboxImg');
        const pdfContainer = document.getElementById('lightboxPdfContainer');
        const video = document.getElementById('lightboxVideo');
        const videoContainer = document.getElementById('lightboxVideoContainer');
        const audio = document.getElementById('lightboxAudio');
        const audioContainer = document.getElementById('lightboxAudioContainer');
        const editor = document.getElementById('lightboxEditor');
        
        pdfDocInstance = null;
        pdfRenderGeneration++;
        if (pdfObserver) {
            pdfObserver.disconnect();
            pdfObserver = null;
        }
        const pagesContainer = document.getElementById('pdfPagesContainer');
        if(pagesContainer) pagesContainer.innerHTML = '';
        
        overlay.classList.add('opacity-0');
        if(img) { img.classList.remove('scale-100'); img.classList.add('scale-95'); }
        if(pdfContainer) { pdfContainer.classList.remove('scale-100'); pdfContainer.classList.add('scale-95'); }
        if(videoContainer) { videoContainer.classList.remove('scale-100'); videoContainer.classList.add('scale-95'); }
        if(audioContainer) { audioContainer.classList.remove('scale-100'); audioContainer.classList.add('scale-95'); }
        
        if(video) { video.pause(); }
        if(audio) { audio.pause(); }
        
        setTimeout(() => {
            overlay.classList.add('hidden');
            overlay.classList.remove('flex');
            if(window.monacoEditorInstance) window.monacoEditorInstance.setValue("");
            if(img) img.src = '';
            if(video) { video.src = ''; video.load(); }
            if(audio) { audio.src = ''; audio.load(); }
        }, 300);
    }

    // Keyboard Accessibility (R-32): Close lightbox with Escape, PDF page navigation with arrows
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const overlay = document.getElementById('lightboxOverlay');
            if (overlay && !overlay.classList.contains('hidden')) {
                closeLightbox();
            }
        } else if ((e.key === 'ArrowUp' || e.key === 'ArrowLeft') && pdfDocInstance) {
            scrollPdfToPage(pdfCurrentPageNum - 1);
        } else if ((e.key === 'ArrowDown' || e.key === 'ArrowRight') && pdfDocInstance) {
            scrollPdfToPage(pdfCurrentPageNum + 1);
        }
    });

    // --- SORTABLE COLUMNS ---
    let sortOrders = { name: 1, size: 1, date: 1 };
    
    function sortFiles(type) {
        const container = document.querySelector('.overflow-y-auto.flex-1');
        const items = Array.from(container.querySelectorAll('.file-item[data-filename]'));
        const backBtn = container.querySelector('.file-item:not([data-filename])'); 
        const emptyState = container.querySelector('.bi-box2'); 
        
        if (emptyState) return;
        
        sortOrders[type] *= -1; 
        
        items.sort((a, b) => {
            const isDirA = a.dataset.isdir === '1';
            const isDirB = b.dataset.isdir === '1';
            
            if (isDirA !== isDirB) return isDirA ? -1 : 1;
            
            let valA, valB;
            if (type === 'name') {
                valA = a.dataset.filename.toLowerCase();
                valB = b.dataset.filename.toLowerCase();
            } else if (type === 'size') {
                const parseSize = (str) => {
                    if (str === '-') return 0;
                    const val = parseFloat(str) || 0;
                    if (str.includes('KB')) return val * 1024;
                    if (str.includes('MB')) return val * 1024 * 1024;
                    if (str.includes('GB')) return val * 1024 * 1024 * 1024;
                    return val;
                };
                valA = parseSize(a.dataset.size || '0');
                valB = parseSize(b.dataset.size || '0');
            } else if (type === 'date') {
                valA = a.dataset.date || '';
                valB = b.dataset.date || '';
            }
            
            if (valA < valB) return -1 * sortOrders[type];
            if (valA > valB) return 1 * sortOrders[type];
            return 0;
        });
        
        container.innerHTML = '';
        if (backBtn) container.appendChild(backBtn);
        items.forEach(item => container.appendChild(item));
    }

    // --- BULK ACTIONS ---
    let lastCheckedCheckbox = null;

    function handleCheckboxClick(event, checkbox) {
        if (!lastCheckedCheckbox) {
            lastCheckedCheckbox = checkbox;
            updateBulkActionBar();
            return;
        }

        if (event.shiftKey) {
            const checkboxes = Array.from(document.querySelectorAll('.file-checkbox'));
            const start = checkboxes.indexOf(checkbox);
            const end = checkboxes.indexOf(lastCheckedCheckbox);
            const min = Math.min(start, end);
            const max = Math.max(start, end);

            for (let i = min; i <= max; i++) {
                checkboxes[i].checked = checkbox.checked;
            }
        }

        lastCheckedCheckbox = checkbox;
        updateBulkActionBar();
    }

    function toggleAllFiles(source) {
        const checkboxes = document.querySelectorAll('.file-checkbox');
        checkboxes.forEach(cb => cb.checked = source.checked);
        updateBulkActionBar();
    }
    
    function updateBulkActionBar() {
        const checkboxes = document.querySelectorAll('.file-checkbox:checked');
        const count = checkboxes.length;
        const bar = document.getElementById('bulkActionBar');
        
        if (count > 0) {
            document.getElementById('bulkSelectedCount').textContent = count;
            const selectedFiles = Array.from(checkboxes).map(cb => cb.value);
            document.getElementById('bulkDeleteFiles').value = JSON.stringify(selectedFiles);
            document.getElementById('bulkCompressFiles').value = JSON.stringify(selectedFiles);
            
            bar.classList.remove('hidden');
            bar.classList.add('flex');
            setTimeout(() => {
                bar.classList.remove('translate-y-10', 'opacity-0');
                bar.classList.add('translate-y-0', 'opacity-100');
            }, 10);
        } else {
            bar.classList.remove('translate-y-0', 'opacity-100');
            bar.classList.add('translate-y-10', 'opacity-0');
            setTimeout(() => {
                bar.classList.add('hidden');
                bar.classList.remove('flex');
            }, 300);
        }
    }
    
    function clearBulkSelection() {
        document.getElementById('selectAllCheckbox').checked = false;
        const checkboxes = document.querySelectorAll('.file-checkbox');
        checkboxes.forEach(cb => cb.checked = false);
        updateBulkActionBar();
    }

    // --- Modal Functions (Vanilla JS) ---
    function openModal(modalId) {
        const modal = document.getElementById(modalId);
        if (!modal) return;
        
        // Populate data if specific modals are opened
        if (modalId === 'systemStatsModal') {
            switchTab('overview'); // Reset to overview tab
            fetchSystemStats();
        }
        if (modalId === 'networkConfigModal') {
            fetchNetworkConfig();
        }

        modal.classList.remove('hidden');
        modal.classList.add('flex');
        // Small delay to allow display:flex to apply before changing opacity for transition
        setTimeout(() => {
            modal.classList.remove('opacity-0');
            modal.querySelector('div[class*="transform"]').classList.remove('scale-95');
            modal.querySelector('div[class*="transform"]').classList.add('scale-100');
        }, 10);
    }

    function closeModal(modalId) {
        const modal = document.getElementById(modalId);
        if (!modal) return;

        modal.classList.add('opacity-0');
        modal.querySelector('div[class*="transform"]').classList.remove('scale-100');
        modal.querySelector('div[class*="transform"]').classList.add('scale-95');
        
        setTimeout(() => {
            modal.classList.remove('flex');
            modal.classList.add('hidden');
        }, 300); // Match transition duration
    }

    function setModalData(modalId, data) {
        const modal = document.getElementById(modalId);
        if (!modal) return;

        if (modalId === 'moveModal') {
            modal.querySelector('#moveTargetName').textContent = data.fileName;
            modal.querySelector('#moveSourcePath').value = data.sourcePath;
            // Fetch folder list for the destination input value
            const destInput = modal.querySelector('#moveDestinationInput');
            fetchFolderList(destInput.value);
        } else if (modalId === 'compressModal') {
            modal.querySelector('#compressTargetPath').value = data.targetPath;
            modal.querySelector('#compressOutputName').value = data.fileName.replace(/\\.[^/.]+$/, "") + '.zip';
        } else if (modalId === 'extractModal') {
            modal.querySelector('#extractTargetPath').value = data.targetPath;
            const destInput = modal.querySelector('#extractDestinationInput');
            // Construct default destination: current path + filename without extension
            const currentPath = "{{ req_path }}";
            const newFolder = data.fileName.replace(/\\.[^/.]+$/, "");
            destInput.value = currentPath ? (currentPath + '/' + newFolder) : newFolder;
        } else if (modalId === 'shareLinkModal') {
            const expiryInput = modal.querySelector('#linkExpiryDate');
            if (expiryInput) expiryInput.value = ''; // Reset to unlimited by default
            
            // Store the filePath for regenerate button if needed
            modal.dataset.filePath = data.filePath;
            
            generateSharedLinkFromModal();

            // Add listener to regenerate link when date changes
            if (expiryInput && !expiryInput.dataset.hasListener) {
                expiryInput.addEventListener('change', () => generateSharedLinkFromModal());
                expiryInput.dataset.hasListener = 'true';
            }
        }
    }

    function generateSharedLinkFromModal() {
        const modal = document.getElementById('shareLinkModal');
        if (!modal) return;

        const filePath = modal.dataset.filePath;
        const expiryDate = modal.querySelector('#linkExpiryDate').value;
        const shareLinkInput = modal.querySelector('#shareLinkInput');
        const qrImg = modal.querySelector('#shareQRCode');

        shareLinkInput.value = "Generating link...";
        if (qrImg) qrImg.style.display = 'none';

        fetch('/generate_share_link', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                file_path: filePath,
                expiry_date: expiryDate || null
            })
        })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                const fullUrl = `${window.location.origin}/share/${res.token}`;
                shareLinkInput.value = fullUrl;
                
                if (qrImg) {
                    qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=${encodeURIComponent(fullUrl)}`;
                    qrImg.style.display = 'block';
                }
            } else {
                shareLinkInput.value = "Error generating link.";
            }
        })
        .catch(e => {
            console.error(e);
            shareLinkInput.value = "Failed to connect to server.";
        });
    }

    function switchTab(tabId) {
        // Tabs buttons
        ['overview', 'connections', 'eventlog'].forEach(id => {
            const btn = document.getElementById('tab-' + id);
            const content = document.getElementById('tab-content-' + id);
            
            if (id === tabId) {
                btn.classList.remove('text-gray-600', 'dark:text-slate-400', 'hover:bg-gray-200', 'dark:hover:bg-slate-800');
                btn.classList.add('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-400', 'shadow-sm', 'border', 'border-cyan-100', 'dark:border-cyan-800', 'ring-1', 'ring-cyan-200', 'dark:ring-cyan-900');
                content.classList.remove('hidden');
            } else {
                btn.classList.add('text-gray-600', 'dark:text-slate-400', 'hover:bg-gray-200', 'dark:hover:bg-slate-800');
                btn.classList.remove('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-400', 'shadow-sm', 'border', 'border-cyan-100', 'dark:border-cyan-800', 'ring-1', 'ring-cyan-200', 'dark:ring-cyan-900');
                content.classList.add('hidden');
            }
        });

        // Trigger fetches if needed
         if (tabId === 'connections') {
            // Check if processed connections are already there, or re-fetch?
            // Re-fetching logic handled by connectionFilter logic mostly, but let's refresh stats if needed.
            // System Stats fetch gets all data including connections.
         } else if (tabId === 'eventlog') {
             fetchEventLog();
         }
    }

    function handleRename(event, filePath, fileName) {
        if (!document.body.classList.contains('edit-mode-active')) return;
        
        event.preventDefault();
        event.stopPropagation();
        
        if (filePath && typeof filePath === 'string') {
            filePath = filePath.replace(/^\\/+/, '');
        }

        const span = event.target;
        // Check if already editing
        if (span.querySelector('input')) return;

        // Split extension
        const lastDotIndex = fileName.lastIndexOf('.');
        let baseName = fileName;
        let ext = "";
        if (lastDotIndex > 0) {
            baseName = fileName.substring(0, lastDotIndex);
            ext = fileName.substring(lastDotIndex);
        }

        const originalContent = span.innerHTML;
        
        // Render Input only (no buttons)
        span.innerHTML = `
            <div class="inline-flex items-center gap-1" onclick="event.preventDefault(); event.stopPropagation();">
                <input type="text" class="px-1 py-0.5 border rounded text-xs text-black font-normal" value="${baseName}" style="width: 150px;" placeholder="Filename">
                <span class="text-gray-500 text-xs">${ext}</span>
            </div>
        `;
        
        const input = span.querySelector('input');
        input.focus();
        
        // Prevent link click when clicking input
        input.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
        });

        let isCanceling = false;

        const submitChange = () => {
            if (isCanceling) return;
            const newBaseName = input.value.trim();
            if (newBaseName && newBaseName !== baseName) {
                const finalName = newBaseName + ext;
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/rename';
                
                const inputPath = document.createElement('input');
                inputPath.type = 'hidden';
                inputPath.name = 'file_path';
                inputPath.value = filePath;
                
                const inputName = document.createElement('input');
                inputName.type = 'hidden';
                inputName.name = 'new_name';
                inputName.value = finalName;
                
                form.appendChild(inputPath);
                form.appendChild(inputName);
                document.body.appendChild(form);
                form.submit();
            } else {
                span.innerHTML = originalContent;
            }
        };

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                e.stopPropagation();
                input.blur(); // Trigger blur to save
            } else if (e.key === 'Escape') {
                 e.preventDefault();
                 e.stopPropagation();
                 isCanceling = true;
                 span.innerHTML = originalContent;
            }
        });
        
        // Auto-save on blur (click elsewhere)
        input.addEventListener('blur', () => {
            // Small delay to check if it was cancelled
            setTimeout(() => {
                if (!isCanceling) submitChange();
            }, 100);
        });
    }


    function triggerRename(fileName) {
        // Find the file item row
        const rows = document.querySelectorAll('.file-item');
        for (let row of rows) {
            if (row.getAttribute('data-filename') === fileName) {
                const span = row.querySelector('.truncate.editable-name') || row.querySelector('span[onclick^="handleRename"]');
                if (span) {
                    // Create a mock event to pass to handleRename
                    const mockEvent = {
                        target: span,
                        preventDefault: () => {},
                        stopPropagation: () => {}
                    };
                    const linkElem = row.querySelector('a');
                    const rawHref = linkElem ? linkElem.getAttribute('href') : '';
                    const filePath = rawHref.replace(/^\\/+/, '');
                    handleRename(mockEvent, filePath, fileName);
                }
                break;
            }
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const editModeBtn = document.getElementById('editModeBtn');
        const createFolderBtn = document.getElementById('createFolderBtn'); // Although now handled by onclick
        const fileFilterInput = document.getElementById('fileFilter');
        const body = document.body;
        const modalPinFields = document.querySelectorAll('.edit-pin-wrapper'); // Updated class selector in new HTML
        
        let globalActiveConnections = [];
        const activeConnectionsContent = document.getElementById('active-connections-content');
        const systemStatsContent = document.getElementById('system-stats-content');
        const eventLogContent = document.getElementById('event-log-content');
        const networkConfigContent = document.getElementById('network-config-content');

        // --- LIVE SMART SEARCH (FILE FILTER) ---
        if (fileFilterInput) {
            fileFilterInput.addEventListener('input', function(e) {
                const term = e.target.value.toLowerCase();
                const items = document.querySelectorAll('.file-item');
                items.forEach(item => {
                    const filename = item.getAttribute('data-filename');
                    if (!filename) return; // Skip "Back" button
                    
                    if (filename.toLowerCase().includes(term)) {
                        item.style.display = '';
                    } else {
                        item.style.display = 'none';
                    }
                });
            });
        }

        // Chunked upload progress handler
        const uploadForm = document.getElementById('uploadForm');
        const fileInput = document.getElementById('fileInput');
        const dropZone = document.getElementById('dropZone');
        const fileSelectedName = document.getElementById('fileSelectedName');

        if (fileInput && dropZone && fileSelectedName) {
            fileInput.addEventListener('change', () => {
                if (fileInput.files.length > 0) {
                    fileSelectedName.textContent = fileInput.files[0].name;
                    fileSelectedName.classList.add('text-indigo-600', 'dark:text-indigo-400', 'font-bold');
                } else {
                    fileSelectedName.textContent = 'Drag and drop file here, or click to browse';
                    fileSelectedName.classList.remove('text-indigo-600', 'dark:text-indigo-400', 'font-bold');
                }
            });

            ['dragenter', 'dragover'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => {
                    dropZone.classList.add('border-indigo-500', 'bg-indigo-50/50', 'dark:bg-indigo-950/20');
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => {
                    dropZone.classList.remove('border-indigo-500', 'bg-indigo-50/50', 'dark:bg-indigo-950/20');
                }, false);
            });
        }

        if (uploadForm) {
            uploadForm.addEventListener('submit', function(e) {
                e.preventDefault(); 
                const pinInput = document.getElementById('pinInput');
                const progressContainer = document.getElementById('progressContainer');
                const progressBar = document.getElementById('progressBar');
                const progressText = document.getElementById('progressText');
                const btnStart = document.getElementById('btnStartUpload');
                const btnCancel = document.getElementById('btnCancelUpload');

                if (!fileInput.files.length || !pinInput.value) {
                    alert("Please select a file and enter the PIN.");
                    return;
                }

                progressContainer.classList.remove('hidden');
                progressBar.style.width = '0%';
                progressText.innerText = 'Preparing chunked upload...';
                btnStart.disabled = true;
                btnCancel.disabled = true;
                btnStart.classList.add('opacity-50', 'cursor-not-allowed');

                const file = fileInput.files[0];
                const pin = pinInput.value;
                const targetPath = uploadForm.querySelector('[name="target_path"]').value;

                const CHUNK_SIZE = 10 * 1024 * 1024;
                const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
                const uploadId = Date.now().toString() + '_' + Math.random().toString(36).substr(2, 9);
                let currentChunk = 0;

                function sendNextChunk() {
                    const start = currentChunk * CHUNK_SIZE;
                    const end = Math.min(start + CHUNK_SIZE, file.size);
                    const chunk = file.slice(start, end);

                    const formData = new FormData();
                    formData.append('file', chunk, file.name);
                    formData.append('filename', file.name);
                    formData.append('chunk_index', currentChunk);
                    formData.append('total_chunks', totalChunks);
                    formData.append('upload_id', uploadId);
                    formData.append('pin', pin);
                    formData.append('target_path', targetPath);

                    const xhr = new XMLHttpRequest();
                    xhr.open('POST', '/upload_chunk', true);

                    xhr.upload.onprogress = function(e) {
                        if (e.lengthComputable) {
                            const chunkPercent = e.loaded / e.total;
                            const overallPercent = Math.round(((currentChunk + chunkPercent) / totalChunks) * 100);
                            progressBar.style.width = overallPercent + '%';
                            progressText.innerText = `Uploading... ${overallPercent}%`;
                        }
                    };

                    xhr.onload = function() {
                        if (xhr.status === 200) {
                            currentChunk++;
                            if (currentChunk < totalChunks) {
                                sendNextChunk();
                            } else {
                                progressText.innerText = 'Processing file (saving)...';
                                progressBar.classList.add('animate-pulse');
                                setTimeout(() => { window.location.reload(); }, 500);
                            }
                        } else {
                            try {
                                const response = JSON.parse(xhr.responseText);
                                alert(response.message || 'Upload failed');
                            } catch(e) {
                                alert('Upload failed with status ' + xhr.status);
                            }
                            resetUploadUI();
                        }
                    };

                    xhr.onerror = function() {
                        alert('Connection error occurred.');
                        resetUploadUI();
                    };

                    xhr.send(formData);
                }

                sendNextChunk();

                function resetUploadUI() {
                    progressContainer.classList.add('hidden');
                    progressBar.style.width = '0%';
                    btnStart.disabled = false;
                    btnCancel.disabled = false;
                    btnStart.classList.remove('opacity-50', 'cursor-not-allowed');
                    progressBar.classList.remove('animate-pulse');
                    if (fileSelectedName) {
                        fileSelectedName.textContent = 'Drag and drop file here, or click to browse';
                        fileSelectedName.classList.remove('text-indigo-600', 'dark:text-indigo-400', 'font-bold');
                    }
                    if (fileInput) {
                        fileInput.value = '';
                    }
                }
            });
        }

        // --- EDIT MODE LOGIC ---
        function updateEditModeUI(isActive) {
            if (isActive) {
                body.classList.add('edit-mode-active');
                editModeBtn.innerHTML = '<i class="bi bi-unlock-fill"></i> Deactivate Edit';
                editModeBtn.className = 'px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-all duration-200 btn-accent';
                modalPinFields.forEach(div => div.style.display = 'none');
            } else {
                body.classList.remove('edit-mode-active');
                editModeBtn.innerHTML = '<i class="bi bi-lock-fill" style="color:#5865f2;"></i> Edit Mode';
                editModeBtn.className = 'px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-all duration-200 btn-glass';
                modalPinFields.forEach(div => {
                    div.style.display = 'block';
                    const pinInput = div.querySelector('.edit-pin-field');
                    if (pinInput) pinInput.value = '';
                });
            }
        }

        editModeBtn.addEventListener('click', () => {
            const isActive = body.classList.contains('edit-mode-active');
            if (isActive) {
                fetch('/toggle_edit_mode', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ action: 'deactivate' })
                })
                .then(r => r.json())
                .then(data => {
                    if(data.success) { updateEditModeUI(false); window.location.reload(); }
                    else { alert("Error deactivating Edit Mode."); }
                });
            } else {
                openEditModePinModal();
            }
        });

        window.openEditModePinModal = function() {
            const modal = document.getElementById('editModePinModal');
            const pinInput = document.getElementById('editModePinInput');
            if (!modal) return;
            pinInput.value = '';
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div[class*="transform"]').classList.remove('scale-95');
                modal.querySelector('div[class*="transform"]').classList.add('scale-100');
                pinInput.focus();
            }, 10);
        };

        window.closeEditModePinModal = function() {
            const modal = document.getElementById('editModePinModal');
            if (!modal) return;
            modal.classList.add('opacity-0');
            modal.querySelector('div[class*="transform"]').classList.remove('scale-100');
            modal.querySelector('div[class*="transform"]').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.remove('flex');
                modal.classList.add('hidden');
            }, 300);
        };

        const btnSubmitEditModePin = document.getElementById('btnSubmitEditModePin');
        const editModePinInput = document.getElementById('editModePinInput');
        
        function submitEditModePin() {
            const userPin = editModePinInput.value;
            if (userPin) {
                btnSubmitEditModePin.disabled = true;
                btnSubmitEditModePin.innerHTML = '<i class="bi bi-arrow-repeat animate-spin"></i> Checking...';
                
                fetch('/toggle_edit_mode', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ action: 'activate', pin: userPin })
                })
                .then(r => r.json())
                .then(data => {
                    if(data.success) {
                        window.location.reload();
                    } else {
                        alert("Incorrect PIN! " + (data.message || ""));
                        btnSubmitEditModePin.disabled = false;
                        btnSubmitEditModePin.innerHTML = 'Submit';
                        editModePinInput.value = '';
                        editModePinInput.focus();
                    }
                })
                .catch(e => {
                    console.error(e);
                    alert("Failed to connect.");
                    btnSubmitEditModePin.disabled = false;
                    btnSubmitEditModePin.innerHTML = 'Submit';
                });
            }
        }

        if (btnSubmitEditModePin) {
            btnSubmitEditModePin.addEventListener('click', submitEditModePin);
        }
        if (editModePinInput) {
            editModePinInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    submitEditModePin();
                }
            });
        }

        // --- CONNECTION FILTER ---
        const connectionFilter = document.getElementById('connectionFilter');
        if (connectionFilter) {
            connectionFilter.addEventListener('change', function() {
                if (globalActiveConnections.length > 0) {
                    renderConnectionsTable(globalActiveConnections, activeConnectionsContent, this.value);
                }
            });
        }

        window.fetchSystemStats = function() {
            systemStatsContent.innerHTML = '<div class="text-center text-gray-500 dark:text-slate-400 py-10"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Fetching system statistics...</div>';
            if (activeConnectionsContent) {
                 activeConnectionsContent.innerHTML = '<div class="text-center text-gray-500 dark:text-slate-400 py-10"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Fetching active connections...</div>';
            }
            
            fetch('/system_stats')
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                         const err = `<div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-3 rounded text-center"><i class="bi bi-exclamation-triangle me-2"></i>${data.error}</div>`;
                         systemStatsContent.innerHTML = err;
                         if(activeConnectionsContent) activeConnectionsContent.innerHTML = err;
                    } else {
                        let html = '';
                        html += `<h5 class="text-md font-bold mb-3 border-b dark:border-slate-700 pb-2 flex items-center text-gray-700 dark:text-slate-200"><i class="bi bi-info-circle me-2 text-cyan-600 dark:text-cyan-400"></i>System Information</h5>
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4 text-sm">
                                <div class="bg-gray-50 dark:bg-slate-800 p-3 rounded border dark:border-slate-700 text-slate-700 dark:text-slate-300"><strong>OS:</strong> ${data.os_version}</div>
                                <div class="bg-gray-50 dark:bg-slate-800 p-3 rounded border dark:border-slate-700 text-slate-700 dark:text-slate-300"><strong>Processor:</strong> ${data.processor_model}</div>
                            </div>`;
                        
                        html += `<h5 class="text-md font-bold mb-3 border-b dark:border-slate-700 pb-2 flex items-center text-gray-700 dark:text-slate-200"><i class="bi bi-cpu me-2 text-purple-600 dark:text-purple-400"></i>CPU (${data.cpu_cores} Cores)</h5>
                             <div class="mb-4">
                                <div class="flex justify-between text-sm mb-1 text-slate-600 dark:text-slate-400"><span>Usage</span><span class="font-bold text-slate-800 dark:text-slate-200">${data.cpu_percent}%</span></div>
                                <div class="w-full bg-gray-200 dark:bg-slate-700 rounded-full h-2.5"><div class="bg-purple-600 dark:bg-purple-500 h-2.5 rounded-full" style="width: ${data.cpu_percent}%"></div></div>
                             </div>`;

                        html += `<h5 class="text-md font-bold mb-3 border-b dark:border-slate-700 pb-2 flex items-center text-gray-700 dark:text-slate-200"><i class="bi bi-memory me-2 text-green-600 dark:text-green-400"></i>RAM Usage</h5>
                             <p class="text-xs text-gray-500 dark:text-slate-500 mb-2">Total: ${data.ram_total_gb} GB / Available: ${data.ram_available_gb} GB</p>
                             <div class="mb-4">
                                <div class="flex justify-between text-sm mb-1 text-slate-600 dark:text-slate-400"><span>Used: ${data.ram_used_gb} GB</span><span class="font-bold text-slate-800 dark:text-slate-200">${data.ram_percent}%</span></div>
                                <div class="w-full bg-gray-200 dark:bg-slate-700 rounded-full h-2.5"><div class="bg-green-600 dark:bg-green-500 h-2.5 rounded-full" style="width: ${data.ram_percent}%"></div></div>
                             </div>`;

                        html += `<h5 class="text-md font-bold mb-3 border-b dark:border-slate-700 pb-2 flex items-center text-gray-700 dark:text-slate-200"><i class="bi bi-hdd me-2 text-orange-600 dark:text-orange-400"></i>Disk Partitions</h5>
                                 <div class="space-y-3">`;
                        data.disk_partitions.forEach(disk => {
                             let color = 'bg-green-500';
                             if(disk.disk_used_percent > 80) color = 'bg-red-500';
                             else if(disk.disk_used_percent > 60) color = 'bg-yellow-500';
                             
                             html += `<div class="bg-gray-50 dark:bg-slate-800 p-3 rounded border dark:border-slate-700">
                                        <div class="flex justify-between items-center mb-1">
                                            <span class="font-bold text-sm text-gray-700 dark:text-slate-200">${disk.device}</span>
                                            <span class="text-xs text-gray-500 dark:text-slate-400">${disk.fstype}</span>
                                        </div>
                                        <div class="text-xs text-gray-600 dark:text-slate-400 mb-1">Free: ${disk.disk_free_gb} GB / Total: ${disk.disk_total_gb} GB</div>
                                        <div class="w-full bg-gray-200 dark:bg-slate-700 rounded-full h-2"><div class="${color} h-2 rounded-full" style="width: ${disk.disk_used_percent}%"></div></div>
                                      </div>`;
                        });
                        html += `</div>`;

                        systemStatsContent.innerHTML = html;

                        // Active Connections
                        if(activeConnectionsContent) {
                            globalActiveConnections = data.active_connections;
                            if (globalActiveConnections.length === 0) {
                                activeConnectionsContent.innerHTML = '<div class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400 px-4 py-3 rounded text-center"><i class="bi bi-info-circle me-2"></i>No active connections.</div>';
                            } else {
                                renderConnectionsTable(globalActiveConnections, activeConnectionsContent, connectionFilter ? connectionFilter.value : 'all');
                            }
                        }
                    }
                })
                .catch(e => {
                    console.error(e);
                    systemStatsContent.innerHTML = '<div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-3 rounded text-center">Failed to fetch system stats.</div>';
                });
        };

        function renderConnectionsTable(connections, container, filterValue) {
             const filtered = connections.filter(conn => {
                if (filterValue === 'all') return true;
                return conn.type === filterValue || conn.status === filterValue;
            });
            
            if (filtered.length === 0) {
                container.innerHTML = '<div class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400 px-4 py-3 rounded text-center text-sm">No connections match filter.</div>';
                return;
            }

            let html = `<div class="overflow-x-auto w-full max-w-full"><table class="w-full text-sm text-left text-gray-500 dark:text-slate-400" style="table-layout: fixed;">
                        <colgroup>
                            <col style="width: 25%;">
                            <col style="width: 15%;">
                            <col style="width: 30%;">
                            <col style="width: 30%;">
                        </colgroup>
                        <thead class="text-xs text-gray-700 dark:text-slate-300 uppercase bg-gray-50 dark:bg-slate-800 border-b dark:border-slate-700">
                            <tr>
                                <th class="px-3 py-2 truncate">PID/Process</th>
                                <th class="px-3 py-2 truncate">Types</th>
                                <th class="px-3 py-2 truncate">Local</th>
                                <th class="px-3 py-2 truncate">Remote</th>
                            </tr>
                        </thead><tbody>`;
            
            filtered.forEach(conn => {
                html += `<tr class="bg-white dark:bg-slate-900 border-b dark:border-slate-800 hover:bg-gray-50 dark:hover:bg-slate-800/50">
                            <td class="px-3 py-2 font-medium text-gray-900 dark:text-slate-200 truncate" title="${conn.pid} - ${conn.process || '-'}">
                                ${conn.pid}<br><span class="text-xs font-normal text-gray-500 dark:text-slate-400">${conn.process || '-'}</span>
                            </td>
                            <td class="px-3 py-2">
                                <span class="bg-gray-100 dark:bg-slate-800 text-gray-800 dark:text-slate-200 text-xs font-medium px-2 py-0.5 rounded border border-gray-400 dark:border-slate-600">${conn.type}</span>
                                <div class="mt-1"><span class="bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300 text-xs font-medium px-2 py-0.5 rounded border border-blue-400 dark:border-blue-800">${conn.status}</span></div>
                            </td>
                            <td class="px-3 py-2 font-mono text-xs text-slate-700 dark:text-slate-300 break-all">${conn.local_address}</td>
                            <td class="px-3 py-2 font-mono text-xs text-slate-700 dark:text-slate-300 break-all">${conn.remote_address}</td>
                        </tr>`;
            });
            html += '</tbody></table></div>';
            container.innerHTML = html;
        }

        window.fetchEventLog = function() {
            if(!eventLogContent) return;
            eventLogContent.innerHTML = '<div class="text-center text-gray-500 dark:text-slate-400 py-10"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Fetching System Shutdown logs...</div>';
            
            fetch('/event_log/1074')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        if (data.logs.length === 0) {
                            eventLogContent.innerHTML = `<div class="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400 px-4 py-3 rounded text-center">${data.message}</div>`;
                        } else {
                            let html = '<div class="space-y-3">';
                            data.logs.forEach(log => {
                                html += `
                                <div class="bg-gray-50 dark:bg-slate-800/50 border border-gray-200 dark:border-slate-700 rounded-lg p-3 hover:bg-white dark:hover:bg-slate-800 hover:shadow-sm transition-all text-sm">
                                    <div class="font-bold text-gray-800 dark:text-slate-200 mb-1 flex justify-between">
                                        <span>${log.TimeCreated}</span>
                                        <span class="text-red-500 dark:text-red-400 text-xs bg-red-50 dark:bg-red-900/20 px-2 py-0.5 rounded border border-red-100 dark:border-red-900/50">Event 1074</span>
                                    </div>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2 mt-2">
                                        <div><span class="font-semibold text-gray-600 dark:text-slate-400">Process:</span> <span class="break-all font-mono text-xs text-gray-700 dark:text-slate-300">${log.Process}</span></div>
                                        <div><span class="font-semibold text-gray-600 dark:text-slate-400">Reason:</span> <span class="text-gray-700 dark:text-slate-300">${log.Reason}</span></div>
                                    </div>
                                </div>
                                `;
                            });
                            html += '</div>';
                            eventLogContent.innerHTML = html;
                        }
                    } else {
                        eventLogContent.innerHTML = `<div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-3 rounded text-center">${data.error || "Error fetching logs"}</div>`;
                    }
                })
                .catch(e => {
                     eventLogContent.innerHTML = '<div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-3 rounded text-center">Failed to fetch logs.</div>';
                });
        };
        window.fetchNetworkConfig = function() {
            if(!networkConfigContent) return;
            networkConfigContent.innerHTML = '<div class="text-center text-gray-500 dark:text-slate-400 py-10"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Fetching network config...</div>';

            fetch('/network_config').then(r => r.json()).then(data => {
                if (data.error) {
                    networkConfigContent.innerHTML = `<div class="bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300 p-3 rounded">${data.error}</div>`;
                } else if (!data.network_configs || data.network_configs.length === 0) {
                     networkConfigContent.innerHTML = '<div class="bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300 p-3 rounded">No IPv4 config found.</div>';
                } else {
                    let html = '<div class="grid gap-4">';
                    data.network_configs.forEach(net => {
                        const isUp = net.status === "Up";
                        html += `<div class="bg-white dark:bg-slate-900 border ${isUp ? 'border-green-200 dark:border-green-800 ring-1 ring-green-100 dark:ring-green-900/20' : 'border-red-200 dark:border-red-800'} rounded-lg shadow-sm overflow-hidden">
                                    <div class="px-4 py-3 bg-gray-50 dark:bg-slate-800 border-b dark:border-slate-700 flex justify-between items-center">
                                        <h5 class="font-bold text-gray-700 dark:text-slate-200 flex items-center"><i class="bi bi-ethernet me-2"></i>${net.interface}</h5>
                                        <span class="px-2 py-1 text-xs font-bold rounded ${isUp ? 'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300' : 'bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300'}">${net.status}</span>
                                    </div>
                                    <div class="p-4 text-sm grid grid-cols-1 md:grid-cols-2 gap-y-2 gap-x-4">
                                        <div><span class="text-gray-500 dark:text-slate-400 block text-xs uppercase tracking-wide">IP Address</span><span class="font-mono font-medium text-slate-700 dark:text-slate-200">${net.ip_address}</span> <span class="text-xs text-gray-400">${net.cidr}</span></div>
                                        <div><span class="text-gray-500 dark:text-slate-400 block text-xs uppercase tracking-wide">MAC Address</span><span class="font-mono text-slate-700 dark:text-slate-200">${net.mac_address}</span></div>
                                        <div><span class="text-gray-500 dark:text-slate-400 block text-xs uppercase tracking-wide">Subnet</span><span class="font-mono text-slate-700 dark:text-slate-200">${net.netmask}</span></div>
                                        <div><span class="text-gray-500 dark:text-slate-400 block text-xs uppercase tracking-wide">Gateway</span><span class="font-mono text-slate-700 dark:text-slate-200">${net.gateway}</span></div>
                                        <div class="md:col-span-2 border-t dark:border-slate-800 pt-2 mt-1"><span class="text-gray-500 dark:text-slate-400 block text-xs uppercase tracking-wide">DNS</span><span class="font-mono text-slate-700 dark:text-slate-200">${net.dns}</span></div>
                                    </div>
                                 </div>`;
                    });
                    html += '</div>';
                    networkConfigContent.innerHTML = html;
                }
            }).catch(e => {
                networkConfigContent.innerHTML = '<div class="bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300 p-3 rounded">Failed to load config.</div>';
            });
        };

        window.fetchFolderList = function(targetPath = '') {
            const container = document.getElementById('folderTreeContainer');
            const pathInput = document.getElementById('moveDestinationInput');
            if(!container) return;
            container.innerHTML = '<div class="text-center p-4 text-gray-400"><i class="bi bi-arrow-repeat animate-spin me-2"></i>Loading...</div>';

            fetch('/get_folder_list?path=' + encodeURIComponent(targetPath))
                .then(r => r.json())
                .then(data => {
                     if (data.error) {
                         container.innerHTML = `<div class="text-red-500 dark:text-red-400 text-sm p-3 border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 rounded">${data.error}</div>`;
                     } else {
                         let html = `<ul class="space-y-1">`;
                         const isRoot = data.current_path === '';
                         html += `<li class="flex items-center p-2 rounded cursor-pointer transition-colors ${isRoot ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 font-bold' : 'hover:bg-gray-100 dark:hover:bg-slate-700 text-gray-700 dark:text-slate-200'}" onclick="selectFolder('', this)">
                                    <i class="bi bi-hdd-network me-2 ${isRoot ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400'}"></i>
                                    <span>Shared Root (/)</span>
                                  </li>`;
                         data.folders.forEach(f => {
                             const isSel = f.path === data.current_path;
                             html += `<li class="flex items-center p-2 rounded cursor-pointer transition-colors ml-4 ${isSel ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 font-bold' : 'hover:bg-gray-100 dark:hover:bg-slate-700 text-gray-700 dark:text-slate-200'}" onclick="selectFolder('${f.path}', this)">
                                        <i class="bi bi-folder-fill me-2 ${isSel ? 'text-blue-500 dark:text-blue-400' : 'text-yellow-500'}"></i>
                                        <span class="truncate">${f.name}</span>
                                      </li>`;
                         });
                         html += `</ul>`;
                         container.innerHTML = html;
                         pathInput.value = data.current_path;
                     }
                }).catch(e => {
                    container.innerHTML = '<div class="text-red-500 dark:text-red-400 text-sm">Failed.</div>';
                });
        };

        window.selectFolder = function(path, el) {
             document.getElementById('moveDestinationInput').value = path;
             // Visual feedback
             const allItems = document.querySelectorAll('#folderTreeContainer li');
             allItems.forEach(i => {
                 i.classList.remove('bg-blue-100', 'dark:bg-blue-900/30', 'text-blue-700', 'dark:text-blue-300', 'font-bold');
                 i.classList.add('hover:bg-gray-100', 'dark:hover:bg-slate-700', 'text-gray-700', 'dark:text-slate-200');
                 const icon = i.querySelector('i');
                 if(icon.classList.contains('bi-folder-fill')) icon.classList.replace('text-blue-500', 'text-yellow-500');
             });
             el.classList.remove('hover:bg-gray-100', 'dark:hover:bg-slate-700', 'text-gray-700', 'dark:text-slate-200');
             el.classList.add('bg-blue-100', 'dark:bg-blue-900/30', 'text-blue-700', 'dark:text-blue-300', 'font-bold');
             const icon = el.querySelector('i');
             if(icon.classList.contains('bi-folder-fill')) icon.classList.replace('text-yellow-500', 'text-blue-500');
        };

        // Copy Share Link
        const copyBtn = document.getElementById('copyShareLinkBtn');
        if(copyBtn) {
            copyBtn.addEventListener('click', function() {
                const input = document.getElementById('shareLinkInput');
                input.select();
                navigator.clipboard.writeText(input.value).then(() => {
                    const original = this.innerHTML;
                    this.innerHTML = '<i class="bi bi-check-lg text-green-600"></i>';
                    setTimeout(() => { this.innerHTML = original; }, 2000);
                });
            });
        }

        // Copy Share Upload Link
        const copyUploadBtn = document.getElementById('copyShareUploadLinkBtn');
        if(copyUploadBtn) {
            copyUploadBtn.addEventListener('click', function() {
                const input = document.getElementById('shareUploadLinkInput');
                input.select();
                navigator.clipboard.writeText(input.value).then(() => {
                    const original = this.innerHTML;
                    this.innerHTML = '<i class="bi bi-check-lg text-green-600"></i>';
                    setTimeout(() => { this.innerHTML = original; }, 2000);
                });
            });
        }

        function openShareUploadModal() {
            const currentFolderPath = "{{ req_path }}";
            const modal = document.getElementById('shareUploadLinkModal');
            if (modal) {
                modal.dataset.folderPath = currentFolderPath;
                const expiryInput = modal.querySelector('#uploadLinkExpiryDate');
                if (expiryInput) expiryInput.value = '';
                openModal('shareUploadLinkModal');
                generateSharedUploadLinkFromModal();
                
                if (expiryInput && !expiryInput.dataset.hasListener) {
                    expiryInput.addEventListener('change', () => generateSharedUploadLinkFromModal());
                    expiryInput.dataset.hasListener = 'true';
                }
            }
        }

        function generateSharedUploadLinkFromModal() {
            const modal = document.getElementById('shareUploadLinkModal');
            if (!modal) return;

            const folderPath = modal.dataset.folderPath || "";
            const expiryDate = modal.querySelector('#uploadLinkExpiryDate').value;
            const shareLinkInput = modal.querySelector('#shareUploadLinkInput');
            const qrImg = modal.querySelector('#shareUploadQRCode');

            shareLinkInput.value = "Generating link...";
            if (qrImg) qrImg.style.display = 'none';

            fetch('/generate_upload_link', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ 
                    folder_path: folderPath,
                    expiry_date: expiryDate || null
                })
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    const fullUrl = `${window.location.origin}/upload_request/${res.token}`;
                    shareLinkInput.value = fullUrl;
                    
                    if (qrImg) {
                        qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=${encodeURIComponent(fullUrl)}`;
                        qrImg.style.display = 'block';
                    }
                } else {
                    shareLinkInput.value = "Error generating link.";
                }
            })
            .catch(e => {
                console.error(e);
                shareLinkInput.value = "Failed to connect to server.";
            });
        }
        
        window.openShareUploadModal = openShareUploadModal;
        window.generateSharedUploadLinkFromModal = generateSharedUploadLinkFromModal;

        // File Filter
        if(fileFilterInput) {
            fileFilterInput.addEventListener('keyup', () => {
                const term = fileFilterInput.value.toLowerCase();
                document.querySelectorAll('.file-item').forEach(item => {
                    const name = item.getAttribute('data-filename').toLowerCase();
                    if(name.includes(term)) {
                        item.classList.remove('hidden');
                        item.classList.add('flex');
                    } else {
                        item.classList.add('hidden');
                        item.classList.remove('flex');
                    }
                });
            });
        }
        
        // Radial Menu Toggling
        window.toggleRadialMenu = function(event, btn) {
            event.preventDefault();
            event.stopPropagation();
            
            const isActive = btn.classList.contains('active');
            
            // Close all
            document.querySelectorAll('.mobile-action-toggle').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.action-buttons-container').forEach(c => c.classList.remove('active'));
            
            // Open this one if it wasn't already active
            if (!isActive) {
                btn.classList.add('active');
                const container = btn.nextElementSibling;
                if(container && container.classList.contains('action-buttons-container')) {
                    // Position fixed to avoid overflow clipping
                    const rect = btn.getBoundingClientRect();
                    const y = rect.top + rect.height / 2;
                    container.style.top = y + 'px';
                    container.style.left = (rect.left + rect.width / 2) + 'px';
                    
                    container.classList.remove('arc-up', 'arc-down');
                    if (y > window.innerHeight - 150) {
                        container.classList.add('arc-up');
                    } else if (y < 150) {
                        container.classList.add('arc-down');
                    }
                    
                    container.classList.add('active');
                }
            }
        };

        // Close radial menu when clicking anywhere else
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.file-actions-wrap')) {
                document.querySelectorAll('.mobile-action-toggle').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.action-buttons-container').forEach(c => c.classList.remove('active'));
            }
        });

        // Close radial menu on scroll
        const scrollContainer = document.querySelector('.overflow-y-auto');
        if (scrollContainer) {
            scrollContainer.addEventListener('scroll', () => {
                document.querySelectorAll('.mobile-action-toggle.active').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.action-buttons-container.active').forEach(c => c.classList.remove('active'));
            }, { passive: true });
        }
        
        window.openFolderStatsModal = function() {
            openModal('folderStatsModal');
            const items = document.querySelectorAll('.file-item');
            let extCounts = {};
            let extSizes = {};
            let totalFiles = 0;
            let totalFolders = 0;
            let totalSizeMB = 0;
            
            items.forEach(item => {
                const name = item.getAttribute('data-filename');
                if (!name) return; // Skip "Back" button row
                
                const lastDot = name.lastIndexOf('.');
                const isDir = item.querySelector('i').className.includes('bi-folder-fill') || item.querySelector('i').className.includes('folder');
                
                let ext = 'Folder';
                if (!isDir && lastDot > 0) {
                    ext = name.substring(lastDot).toUpperCase();
                    totalFiles++;
                } else if (!isDir) {
                    ext = 'Unknown';
                    totalFiles++;
                } else {
                    totalFolders++;
                }
                
                // Get size text (column index 1 in the flex layout)
                let sizeMB = 0;
                if (item.children && item.children.length > 1) {
                    const sizeText = item.children[1].textContent.trim();
                    if (sizeText !== '-' && sizeText !== '') {
                        const parts = sizeText.split(' ');
                        if (parts.length >= 2) {
                            let val = parseFloat(parts[0]);
                            if (!isNaN(val)) {
                                if (parts[1] === 'MB') sizeMB = val;
                                else if (parts[1] === 'KB') sizeMB = val / 1024;
                                else if (parts[1] === 'GB') sizeMB = val * 1024;
                                else if (parts[1] === 'B') sizeMB = val / (1024 * 1024);
                            }
                        }
                    }
                }
                totalSizeMB += sizeMB;
                extCounts[ext] = (extCounts[ext] || 0) + 1;
                extSizes[ext] = (extSizes[ext] || 0) + sizeMB;
            });
            
            // Update summary UI
            document.getElementById('statsTotalFiles').textContent = totalFiles;
            document.getElementById('statsTotalFolders').textContent = totalFolders;
            document.getElementById('statsTotalSize').textContent = totalSizeMB.toFixed(2);
            
            // Vibrant colors for dark mode glassmorphism
            const colors = ['#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#6366f1', '#06b6d4', '#14b8a6', '#f43f5e'];
            const labels = Object.keys(extCounts);
            
            if(window.fileCountChartInst) window.fileCountChartInst.destroy();
            if(window.fileSizeChartInst) window.fileSizeChartInst.destroy();
            
            const chartOptions = {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '75%',
                plugins: { 
                    legend: { position: 'right', labels: { color: '#cbd5e1', font: { size: 11 }, padding: 10 } }
                }
            };
            
            const ctxCount = document.getElementById('fileCountChart');
            if(ctxCount && labels.length > 0) {
                window.fileCountChartInst = new Chart(ctxCount, {
                    type: 'doughnut',
                    data: {
                        labels: labels,
                        datasets: [{ 
                            data: Object.values(extCounts), 
                            backgroundColor: colors.slice(0, labels.length),
                            borderWidth: 0,
                            hoverOffset: 4
                        }]
                    },
                    options: chartOptions
                });
            }
            
            const ctxSize = document.getElementById('fileSizeChart');
            if(ctxSize && labels.length > 0) {
                const sizeData = labels.map(l => parseFloat(extSizes[l].toFixed(2)));
                window.fileSizeChartInst = new Chart(ctxSize, {
                    type: 'doughnut',
                    data: {
                        labels: labels,
                        datasets: [{ 
                            data: sizeData, 
                            backgroundColor: colors.slice(0, labels.length),
                            borderWidth: 0,
                            hoverOffset: 4
                        }]
                    },
                    options: { 
                        ...chartOptions,
                        plugins: { 
                            ...chartOptions.plugins,
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        return (context.label ? context.label + ': ' : '') + context.parsed + ' MB';
                                    }
                                }
                            }
                        } 
                    }
                });
            }
        };
    });
</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs/loader.min.js"></script>
<script>
    require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs' }});
    window.monacoEditorInstance = null;
    require(['vs/editor/editor.main'], function() {
        window.monacoEditorInstance = monaco.editor.create(document.getElementById('lightboxEditor'), {
            value: "",
            language: "plaintext",
            theme: "vs-dark",
            automaticLayout: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 14
        });
    });
</script>
</body>
</html>
"""

MODALS_HTML = """
<style>
/* Glass Modals */
.modal-glass {
    background: rgba(15, 23, 42, 0.45);
    backdrop-filter: blur(25px) saturate(200%);
    -webkit-backdrop-filter: blur(25px) saturate(200%);
    border: 1px solid rgba(255,255,255,0.1);
    box-shadow: 0 40px 80px rgba(0,0,0,0.6), 0 0 0 0.5px rgba(255,255,255,0.05);
}
.modal-header {
    background: rgba(255,255,255,0.04);
    border-bottom: 1px solid rgba(255,255,255,0.07);
    padding: 14px 20px;
}
.modal-body { padding: 20px; }
.modal-footer {
    background: rgba(0,0,0,0.2);
    border-top: 1px solid rgba(255,255,255,0.06);
    padding: 14px 20px;
}
.modal-input {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.12);
    color: #e2e8f0;
    border-radius: 8px;
    padding: 10px 14px;
    width: 100%;
    font-size: 13px;
    transition: all 0.2s;
}
.modal-input:focus {
    border-color: #5865f2;
    box-shadow: 0 0 0 2px rgba(88,101,242,0.25);
    background: rgba(255,255,255,0.08);
}
.modal-input:focus-visible {
    outline: 2px solid #5865f2;
    outline-offset: 1px;
}
.modal-input::placeholder { color: #8b949e; }
.modal-label {
    display: block;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #94a3b8;
    margin-bottom: 6px;
}
.modal-input-group {
    display: flex;
    align-items: center;
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    overflow: hidden;
    transition: all 0.2s;
}
.modal-input-group:focus-within {
    border-color: #5865f2;
    box-shadow: 0 0 0 2px rgba(88,101,242,0.25);
}
.modal-input-group .group-icon {
    padding: 10px 12px;
    color: #5865f2;
    border-right: 1px solid rgba(255,255,255,0.08);
    flex-shrink: 0;
}
.modal-input-group input, .modal-input-group textarea {
    background: transparent;
    border: none;
    color: #e2e8f0;
    padding: 10px 14px;
    font-size: 13px;
    flex: 1;
}
.modal-input-group input:focus-visible, .modal-input-group textarea:focus-visible {
    outline: none;
}
.modal-input-group input::placeholder { color: #8b949e; }
.modal-btn-cancel {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.1);
    color: #c9d1d9;
    padding: 9px 18px;
    border-radius: 9px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
}
.modal-btn-cancel:hover { background: rgba(255,255,255,0.1); }
.modal-btn-primary {
    padding: 9px 18px;
    border-radius: 9px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    color: white;
    display: flex;
    align-items: center;
    gap: 6px;
}
.modal-info-box {
    background: rgba(88,101,242,0.08);
    border: 1px solid rgba(88,101,242,0.2);
    border-radius: 10px;
    padding: 12px 14px;
    font-size: 12px;
    color: #a5b4fc;
    display: flex;
    gap: 8px;
    align-items: flex-start;
}
.modal-info-box.warning {
    background: rgba(245,158,11,0.08);
    border-color: rgba(245,158,11,0.2);
    color: #fcd34d;
}
</style>

<!-- Folder Stats Modal -->
<div id="folderStatsModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('folderStatsModal')">
  <div class="modal-glass rounded-2xl w-full max-w-2xl flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-pie-chart-fill text-base" style="color:#ec4899;"></i>Folder Statistics</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('folderStatsModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto">
      <!-- Summary Cards -->
      <div class="grid grid-cols-3 gap-3 mb-6">
          <div class="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col items-center justify-center">
              <i class="bi bi-file-earmark-fill text-xl mb-1" style="color:#6366f1;"></i>
              <span class="text-xs text-slate-400 font-medium">Files</span>
              <span id="statsTotalFiles" class="text-lg font-bold text-white">0</span>
          </div>
          <div class="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col items-center justify-center">
              <i class="bi bi-folder-fill text-xl mb-1" style="color:#f59e0b;"></i>
              <span class="text-xs text-slate-400 font-medium">Folders</span>
              <span id="statsTotalFolders" class="text-lg font-bold text-white">0</span>
          </div>
          <div class="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col items-center justify-center">
              <i class="bi bi-hdd-fill text-xl mb-1" style="color:#10b981;"></i>
              <span class="text-xs text-slate-400 font-medium">Size (MB)</span>
              <span id="statsTotalSize" class="text-lg font-bold text-white">0</span>
          </div>
      </div>
      <!-- Charts -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div class="flex flex-col items-center bg-white/5 border border-white/10 rounded-xl p-4">
              <h6 class="text-xs font-bold uppercase tracking-widest mb-3" style="color:#a5b4fc;">File Types (Count)</h6>
              <div class="relative w-full flex justify-center" style="height:200px;">
                  <canvas id="fileCountChart"></canvas>
              </div>
          </div>
          <div class="flex flex-col items-center bg-white/5 border border-white/10 rounded-xl p-4">
              <h6 class="text-xs font-bold uppercase tracking-widest mb-3" style="color:#a5b4fc;">Storage Usage (MB)</h6>
              <div class="relative w-full flex justify-center" style="height:200px;">
                  <canvas id="fileSizeChart"></canvas>
              </div>
          </div>
      </div>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('folderStatsModal')">Close</button>
    </div>
  </div>
</div>

<!-- Upload Modal -->
<div id="uploadModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('uploadModal')">
  <div class="modal-glass rounded-2xl w-full max-w-lg flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-cloud-arrow-up-fill text-base" style="color:#5865f2;"></i>Upload File</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('uploadModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto space-y-4">
      <form action="/upload" method="post" enctype="multipart/form-data" id="uploadForm">
          <div class="mb-4">
              <label class="modal-label">Choose file or drag & drop:</label>
              <div id="dropZone" class="rounded-xl p-6 text-center cursor-pointer relative transition-all" style="border: 2px dashed rgba(255,255,255,0.1);background:rgba(255,255,255,0.02);">
                  <input type="file" name="file" id="fileInput" class="absolute inset-0 w-full h-full opacity-0 cursor-pointer" required>
                  <div class="flex flex-col items-center justify-center space-y-2 pointer-events-none">
                      <i class="bi bi-cloud-arrow-up text-4xl" style="color:#5865f2;"></i>
                      <p class="text-sm font-medium text-slate-300" id="fileSelectedName">Drag and drop file here, or click to browse</p>
                      <p class="text-xs" style="color:#8b949e;">Supports all standard files</p>
                  </div>
              </div>
          </div>
          <div class="mb-4">
              <label class="modal-label" for="pinInput">Upload PIN</label>
              <div class="modal-input-group">
                  <span class="group-icon"><i class="bi bi-key-fill text-sm"></i></span>
                  <input type="password" name="pin" id="pinInput" placeholder="Enter Upload PIN" required>
              </div>
          </div>
          <input type="hidden" name="target_path" value="{{ req_path }}">
          <div id="progressContainer" class="mt-2 hidden p-4 rounded-xl" style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);">
              <p class="mb-2 text-center text-xs font-medium" style="color:#7d8590;" id="progressText">Uploading... 0%</p>
              <div class="w-full rounded-full h-1.5 overflow-hidden" style="background:rgba(255,255,255,0.08);">
                  <div id="progressBar" class="h-1.5 rounded-full transition-all duration-300" style="width: 0%;background:linear-gradient(90deg,#5865f2,#818cf8);box-shadow:0 0 10px rgba(88,101,242,0.5);"></div>
              </div>
          </div>
      </form>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('uploadModal')" id="btnCancelUpload">Cancel</button>
      <button type="submit" form="uploadForm" class="modal-btn-primary" style="background:linear-gradient(135deg,#5865f2,#818cf8);box-shadow:0 4px 15px rgba(88,101,242,0.3);" id="btnStartUpload"><i class="bi bi-upload text-xs"></i> Start Upload</button>
    </div>
  </div>
</div>

<!-- Create Folder Modal -->
<div id="createFolderModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('createFolderModal')">
  <div class="modal-glass rounded-2xl w-full max-w-md flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-folder-plus text-base" style="color:#10b981;"></i>Create New Folder</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('createFolderModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto space-y-4">
      <form action="{{ url_for('new_folder') }}" method="post" id="createFolderForm">
          <div class="mb-4">
              <label class="modal-label" for="folderNameInput">Folder Name</label>
              <div class="modal-input-group">
                  <span class="group-icon" style="color:#f59e0b;"><i class="bi bi-folder text-sm"></i></span>
                  <input type="text" name="folder_name" id="folderNameInput" placeholder="Enter folder name" required>
              </div>
          </div>
          <div class="mb-4 edit-pin-wrapper" {% if session.get('edit_mode_active') %} style="display: none !important;" {% endif %}>
              <label class="modal-label" for="folderPinInput">Edit PIN</label>
              <div class="modal-input-group">
                  <span class="group-icon"><i class="bi bi-shield-lock text-sm"></i></span>
                  <input type="password" name="pin" class="edit-pin-field" id="folderPinInput" placeholder="Enter Edit PIN"
                          {% if not session.get('edit_mode_active') %} required
                          {% else %} value="{{ edit_pin }}"
                          {% endif %}>
              </div>
          </div>
          <input type="hidden" name="target_path" value="{{ req_path }}">
      </form>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('createFolderModal')">Cancel</button>
      <button type="submit" form="createFolderForm" class="modal-btn-primary" style="background:linear-gradient(135deg,#10b981,#34d399);box-shadow:0 4px 15px rgba(16,185,129,0.3);"><i class="bi bi-plus-lg text-xs"></i> Create</button>
    </div>
  </div>
</div>

<!-- Move Modal -->
<div id="moveModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('moveModal')">
  <div class="modal-glass rounded-2xl w-full max-w-4xl flex flex-col max-h-[85vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-arrows-move text-base" style="color:#14b8a6;"></i>Move File/Folder</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('moveModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto flex-1 flex flex-col">
      <form action="{{ url_for('move_file') }}" method="post" id="moveForm" class="h-full flex flex-col gap-4">
          <input type="hidden" name="source_path" id="moveSourcePath">
          <div>
              <h6 class="modal-label flex items-center gap-1.5 mb-2"><i class="bi bi-folder2-open" style="color:#14b8a6;"></i> Select Destination Folder:</h6>
              <div class="rounded-xl overflow-hidden overflow-y-auto" style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);min-height:200px;max-height:250px;" id="folderTreeContainer">
                  <div class="text-center p-4" style="color:#7d8590;"><i class="bi bi-arrow-repeat animate-spin me-2"></i>Loading folders...</div>
              </div>
          </div>
          <div>
              <label class="modal-label" for="moveDestinationInput">Destination Path</label>
              <div class="modal-input-group">
                  <span class="group-icon" style="color:#14b8a6;"><i class="bi bi-folder2-open text-sm"></i></span>
                  <input type="text" name="destination_folder" id="moveDestinationInput" placeholder="e.g. folderA/subfolderB" value="{{ req_path }}" required readonly>
              </div>
              <p class="text-xs mt-1.5" style="color:#7d8590;"><i class="bi bi-info-circle me-1"></i>Moving: <strong id="moveTargetName" style="color:#14b8a6;"></strong></p>
          </div>
          <div class="edit-pin-wrapper" {% if session.get('edit_mode_active') %} style="display: none !important;" {% endif %}>
              <label class="modal-label" for="movePinInput">Edit PIN</label>
              <div class="modal-input-group">
                  <span class="group-icon"><i class="bi bi-shield-lock text-sm"></i></span>
                  <input type="password" name="pin" class="edit-pin-field" id="movePinInput" placeholder="Enter Edit PIN"
                     {% if not session.get('edit_mode_active') %} required
                     {% else %} value="{{ edit_pin }}"
                     {% endif %}>
              </div>
          </div>
      </form>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('moveModal')">Cancel</button>
      <button type="submit" form="moveForm" class="modal-btn-primary" style="background:linear-gradient(135deg,#14b8a6,#2dd4bf);box-shadow:0 4px 15px rgba(20,184,166,0.3);"><i class="bi bi-arrow-right text-xs"></i> Move Item</button>
    </div>
  </div>
</div>

<!-- Compress Modal -->
<div id="compressModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('compressModal')">
  <div class="modal-glass rounded-2xl w-full max-w-md flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-file-zip-fill text-base" style="color:#f59e0b;"></i>Compress to ZIP</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('compressModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto space-y-4">
      <form action="{{ url_for('compress_file') }}" method="post" id="compressForm">
          <input type="hidden" name="target_path" id="compressTargetPath">
          <div class="modal-info-box mb-4">
              <i class="bi bi-info-circle flex-shrink-0 mt-0.5"></i>
              <span>Compressing file/folder into a ZIP archive in the current directory ({{ req_path if req_path else '/' }}).</span>
          </div>
          <div class="mb-4">
              <label class="modal-label" for="compressOutputName">Archive Filename (.zip)</label>
              <div class="modal-input-group">
                  <span class="group-icon" style="color:#f59e0b;"><i class="bi bi-file-earmark-zip text-sm"></i></span>
                  <input type="text" name="output_name" id="compressOutputName" required>
              </div>
          </div>
          <div class="edit-pin-wrapper" {% if session.get('edit_mode_active') %} style="display: none !important;" {% endif %}>
              <label class="modal-label" for="compressPinInput">Edit PIN</label>
              <div class="modal-input-group">
                  <span class="group-icon"><i class="bi bi-shield-lock text-sm"></i></span>
                  <input type="password" name="pin" class="edit-pin-field" id="compressPinInput" placeholder="Enter Edit PIN"
                          {% if not session.get('edit_mode_active') %} required
                          {% else %} value="{{ edit_pin }}"
                          {% endif %}>
              </div>
          </div>
      </form>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('compressModal')">Cancel</button>
      <button type="submit" form="compressForm" class="modal-btn-primary" style="background:linear-gradient(135deg,#f59e0b,#fbbf24);box-shadow:0 4px 15px rgba(245,158,11,0.3);color:#0d1117;"><i class="bi bi-file-zip-fill text-xs"></i> Compress</button>
    </div>
  </div>
</div>

<!-- Extract Modal -->
<div id="extractModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('extractModal')">
  <div class="modal-glass rounded-2xl w-full max-w-md flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-box-seam text-base" style="color:#a78bfa;"></i>Extract ZIP Archive</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('extractModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto space-y-4">
      <form action="{{ url_for('extract_file') }}" method="post" id="extractForm">
          <input type="hidden" name="archive_path" id="extractTargetPath">
          <div class="modal-info-box mb-4" style="background:rgba(167,139,250,0.08);border-color:rgba(167,139,250,0.2);color:#c4b5fd;">
              <i class="bi bi-info-circle flex-shrink-0 mt-0.5"></i>
              <span>Extracting archive to specified folder relative to shared root ({{ os.path.basename(base_directory) }}).</span>
          </div>
          <div class="mb-4">
              <label class="modal-label" for="extractDestinationInput">Destination Folder</label>
              <div class="modal-input-group">
                  <span class="group-icon" style="color:#a78bfa;"><i class="bi bi-folder2-open text-sm"></i></span>
                  <input type="text" name="destination_folder" id="extractDestinationInput" required>
              </div>
          </div>
          <div class="edit-pin-wrapper" {% if session.get('edit_mode_active') %} style="display: none !important;" {% endif %}>
              <label class="modal-label" for="extractPinInput">Edit PIN</label>
              <div class="modal-input-group">
                  <span class="group-icon"><i class="bi bi-shield-lock text-sm"></i></span>
                  <input type="password" name="pin" class="edit-pin-field" id="extractPinInput" placeholder="Enter Edit PIN"
                          {% if not session.get('edit_mode_active') %} required
                          {% else %} value="{{ edit_pin }}"
                          {% endif %}>
              </div>
          </div>
      </form>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('extractModal')">Cancel</button>
      <button type="submit" form="extractForm" class="modal-btn-primary" style="background:linear-gradient(135deg,#7c3aed,#a78bfa);box-shadow:0 4px 15px rgba(124,58,237,0.3);"><i class="bi bi-box-seam text-xs"></i> Extract</button>
    </div>
  </div>
</div>

<!-- System Stats Modal -->
<div id="systemStatsModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('systemStatsModal')">
    <div class="modal-glass rounded-2xl w-full max-w-2xl flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
        <div class="modal-header flex justify-between items-center rounded-t-2xl">
            <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-hdd-rack-fill text-base" style="color:#06b6d4;"></i>System Resources</h5>
            <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('systemStatsModal')"><i class="bi bi-x-lg text-sm"></i></button>
        </div>
        <div class="flex flex-col overflow-hidden flex-1">
            <div class="flex gap-1 p-2 shrink-0" style="background:rgba(0,0,0,0.2);border-bottom:1px solid rgba(255,255,255,0.06);">
                 <button class="px-4 py-1.5 rounded-lg text-xs font-semibold transition-colors" style="background:rgba(6,182,212,0.15);color:#67e8f9;border:1px solid rgba(6,182,212,0.25);" id="tab-overview" onclick="switchTab('overview')">Overview</button>
                 <button class="px-4 py-1.5 rounded-lg text-xs font-semibold transition-colors" style="color:#7d8590;" id="tab-connections" onclick="switchTab('connections')">Active Connections</button>
                 <button class="px-4 py-1.5 rounded-lg text-xs font-semibold transition-colors" style="color:#7d8590;" id="tab-eventlog" onclick="switchTab('eventlog')">System Log</button>
            </div>
            <div id="tab-content-overview" class="p-5 overflow-y-auto flex-1">
                 <div id="system-stats-content">
                    <div class="text-center py-10" style="color:#7d8590;"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Loading system statistics...</div>
                 </div>
            </div>
            <div id="tab-content-connections" class="p-5 overflow-y-auto flex-1 hidden">
                <div class="mb-3 flex items-center gap-2">
                    <label class="text-xs font-semibold" style="color:#7d8590;">Filter:</label>
                    <select id="connectionFilter" class="rounded-lg px-3 py-1.5 text-xs outline-none" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:#c9d1d9;">
                        <option value="all">All Connections</option>
                        <option value="ESTABLISHED">Established</option>
                        <option value="LISTEN">Listening</option>
                    </select>
                </div>
                <div id="active-connections-content">
                     <div class="text-center py-10" style="color:#7d8590;"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Loading active connections...</div>
                </div>
            </div>
            <div id="tab-content-eventlog" class="p-5 overflow-y-auto flex-1 hidden">
                 <div id="event-log-content">
                    <div class="text-center py-10" style="color:#7d8590;"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Loading System Shutdown logs (1074)...</div>
                </div>
            </div>
        </div>
        <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
            <button type="button" class="modal-btn-cancel" onclick="closeModal('systemStatsModal')">Close</button>
        </div>
    </div>
</div>

<!-- Network Config Modal -->
<div id="networkConfigModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('networkConfigModal')">
    <div class="modal-glass rounded-2xl w-full max-w-4xl flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
        <div class="modal-header flex justify-between items-center rounded-t-2xl">
             <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-ethernet text-base" style="color:#f59e0b;"></i>Network Configuration</h5>
             <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('networkConfigModal')"><i class="bi bi-x-lg text-sm"></i></button>
        </div>
        <div class="p-5 overflow-y-auto" id="network-config-content">
            <div class="text-center py-10" style="color:#7d8590;"><i class="bi bi-arrow-repeat animate-spin me-2 text-2xl"></i><br>Loading network configuration...</div>
        </div>
        <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
             <button type="button" class="modal-btn-cancel" onclick="closeModal('networkConfigModal')">Close</button>
        </div>
    </div>
</div>

<!-- Share Link Modal -->
<div id="shareLinkModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('shareLinkModal')">
  <div class="modal-glass rounded-2xl w-full max-w-md flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-share-fill text-base" style="color:#14b8a6;"></i>Share File Link</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('shareLinkModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto space-y-4">
      <p class="text-xs" style="color:#94a3b8;">Anyone with this link can view and download the file (no login required).</p>
      <div>
          <label class="modal-label" for="linkExpiryDate">Valid Until (Optional)</label>
          <div class="modal-input-group">
              <span class="group-icon" style="color:#14b8a6;"><i class="bi bi-calendar-event text-sm"></i></span>
              <input type="date" id="linkExpiryDate" style="color-scheme:dark;" title="Leave empty for unlimited access">
              <button onclick="document.getElementById('linkExpiryDate').value = ''; generateSharedLinkFromModal();" class="px-3 text-xs font-bold hover:opacity-80 transition-opacity border-l" style="color:#14b8a6;border-color:rgba(255,255,255,0.08);" title="Clear to make Unlimited">Unlimited</button>
          </div>
          <p class="text-xs mt-1 italic" style="color:#8b949e;">* Default is unlimited if not set.</p>
      </div>
      <div class="modal-input-group">
          <input type="text" id="shareLinkInput" class="font-mono text-xs" style="color:#c9d1d9;" readonly>
          <button class="px-4 border-l flex-shrink-0 hover:opacity-80 transition-opacity" style="color:#14b8a6;border-color:rgba(255,255,255,0.08);" type="button" id="copyShareLinkBtn" title="Copy to clipboard">
            <i class="bi bi-clipboard-fill text-sm"></i>
          </button>
      </div>
      <div class="flex flex-col items-center justify-center">
          <p class="text-xs font-semibold mb-2" style="color:#94a3b8;">Scan QR Code</p>
          <img id="shareQRCode" src="" class="rounded-xl p-2" style="width:150px;height:150px;display:none;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);" alt="QR Code">
      </div>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('shareLinkModal')">Close</button>
    </div>
  </div>
</div>

<!-- Share Upload Link Modal -->
<div id="shareUploadLinkModal" class="fixed inset-0 bg-black/50 z-[1060] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('shareUploadLinkModal')">
  <div class="modal-glass rounded-2xl w-full max-w-md flex flex-col max-h-[90vh] scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200"><i class="bi bi-share-fill text-base" style="color:#10b981;"></i>Share Upload Link</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('shareUploadLinkModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body overflow-y-auto space-y-4">
      <p class="text-xs" style="color:#94a3b8;">Anyone with this link can upload files directly to this folder (no login or PIN required).</p>
      <div>
          <label class="modal-label" for="uploadLinkExpiryDate">Valid Until (Optional)</label>
          <div class="modal-input-group">
              <span class="group-icon" style="color:#10b981;"><i class="bi bi-calendar-event text-sm"></i></span>
              <input type="date" id="uploadLinkExpiryDate" style="color-scheme:dark;" title="Leave empty for unlimited access">
              <button onclick="document.getElementById('uploadLinkExpiryDate').value = ''; generateSharedUploadLinkFromModal();" class="px-3 text-xs font-bold hover:opacity-80 transition-opacity border-l" style="color:#10b981;border-color:rgba(255,255,255,0.08);" title="Clear to make Unlimited">Unlimited</button>
          </div>
          <p class="text-xs mt-1 italic" style="color:#8b949e;">* Default is unlimited if not set.</p>
      </div>
      <div class="modal-input-group">
          <input type="text" id="shareUploadLinkInput" class="font-mono text-xs" style="color:#c9d1d9;" readonly>
          <button class="px-4 border-l flex-shrink-0 hover:opacity-80 transition-opacity" style="color:#10b981;border-color:rgba(255,255,255,0.08);" type="button" id="copyShareUploadLinkBtn" title="Copy to clipboard">
            <i class="bi bi-clipboard-fill text-sm"></i>
          </button>
      </div>
      <div class="flex flex-col items-center justify-center">
          <p class="text-xs font-semibold mb-2" style="color:#94a3b8;">Scan QR Code</p>
          <img id="shareUploadQRCode" src="" class="rounded-xl p-2" style="width:150px;height:150px;display:none;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);" alt="QR Code">
      </div>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('shareUploadLinkModal')">Close</button>
    </div>
  </div>
</div>

<!-- Edit Mode PIN Modal -->
<div id="editModePinModal" class="fixed inset-0 bg-black/50 z-[2000] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeEditModePinModal()">
  <div class="modal-glass rounded-2xl w-full max-w-sm flex flex-col scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200 text-sm"><i class="bi bi-shield-lock-fill" style="color:#5865f2;"></i>Enter PIN</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeEditModePinModal()"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body space-y-3">
      <p class="text-xs" style="color:#7d8590;">Please enter the Edit PIN to activate Edit Mode.</p>
      <div class="modal-input-group">
          <span class="group-icon"><i class="bi bi-key-fill text-sm"></i></span>
          <input type="password" id="editModePinInput" class="text-center font-bold tracking-[0.4em]" placeholder="••••" required>
      </div>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeEditModePinModal()">Cancel</button>
      <button type="button" id="btnSubmitEditModePin" class="modal-btn-primary" style="background:linear-gradient(135deg,#5865f2,#818cf8);box-shadow:0 4px 15px rgba(88,101,242,0.3);">Submit</button>
    </div>
  </div>
</div>

<!-- Custom Confirm Modal -->
<div id="customConfirmModal" class="fixed inset-0 bg-black/50 z-[2000] hidden justify-center items-center backdrop-blur-sm px-4 opacity-0 transition-opacity duration-300" onclick="if(event.target === this) closeModal('customConfirmModal')">
  <div class="modal-glass rounded-2xl w-full max-w-sm flex flex-col scale-95 transition-transform duration-300 transform">
    <div class="modal-header flex justify-between items-center rounded-t-2xl">
      <h5 class="font-semibold flex items-center gap-2 text-slate-200 text-sm"><i class="bi bi-question-circle-fill" style="color:#eab308;"></i>Confirmation</h5>
      <button type="button" class="text-slate-400 hover:text-white transition-colors" onclick="closeModal('customConfirmModal')"><i class="bi bi-x-lg text-sm"></i></button>
    </div>
    <div class="modal-body space-y-3">
      <p id="customConfirmMessage" class="text-sm font-medium" style="color:#c9d1d9;"></p>
    </div>
    <div class="modal-footer flex justify-end gap-3 rounded-b-2xl">
      <button type="button" class="modal-btn-cancel" onclick="closeModal('customConfirmModal')">Cancel</button>
      <button type="button" class="modal-btn-primary" style="background:linear-gradient(135deg,#ef4444,#f87171);box-shadow:0 4px 15px rgba(239,68,68,0.3);" onclick="if(customConfirmPendingForm) { customConfirmPendingForm.submit(); }">Yes, Delete</button>
    </div>
  </div>
</div>
<script>
    let customConfirmPendingForm = null;
    function showCustomConfirm(message, formEl) {
        customConfirmPendingForm = formEl;
        document.getElementById('customConfirmMessage').textContent = message;
        openModal('customConfirmModal');
    }
</script>
"""

DOWNLOAD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Download - {{ filename }}</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23818cf8'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0d1117 url('/image.jpg') center/cover no-repeat fixed;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            color: #e6edf3;
            position: relative;
        }
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background: rgba(13, 17, 23, 0.6);
            pointer-events: none;
            z-index: 0;
        }
        .card {
            background: rgba(15, 23, 42, 0.45);
            backdrop-filter: blur(25px) saturate(200%);
            -webkit-backdrop-filter: blur(25px) saturate(200%);
            border: 1px solid rgba(255,255,255,0.1);
            box-shadow: 0 40px 80px rgba(0,0,0,0.6), 0 0 0 0.5px rgba(255,255,255,0.05);
            border-radius: 24px;
            overflow: hidden;
            max-width: 400px;
            width: 100%;
            position: relative;
            z-index: 10;
        }
        .card-header {
            background: linear-gradient(135deg, rgba(88,101,242,0.3), rgba(124,133,245,0.2));
            border-bottom: 1px solid rgba(255,255,255,0.08);
            padding: 36px 32px;
            text-align: center;
        }
        .icon-wrap {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 72px;
            height: 72px;
            border-radius: 50%;
            background: rgba(88,101,242,0.2);
            border: 1px solid rgba(88,101,242,0.3);
            margin-bottom: 16px;
        }
        .card-header h1 { font-size: 18px; font-weight: 600; margin-bottom: 4px; color: #e2e8f0; }
        .card-header p { font-size: 12px; color: rgba(200,210,230,0.6); }
        .card-body { padding: 28px; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
        .stat-box {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 14px;
            text-align: center;
        }
        .stat-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #7d8590; margin-bottom: 4px; }
        .stat-value { font-size: 15px; font-weight: 600; color: #c9d1d9; }
        .expiry-box {
            background: rgba(245,158,11,0.08);
            border: 1px solid rgba(245,158,11,0.2);
            border-radius: 12px;
            padding: 14px;
            text-align: center;
            margin-bottom: 24px;
        }
        .expiry-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #f59e0b; margin-bottom: 4px; display: flex; align-items: center; justify-content: center; gap: 4px; }
        .expiry-value { font-size: 15px; font-weight: 600; color: #fcd34d; }
        .btn-download {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            width: 100%;
            padding: 14px 20px;
            background: #5865f2;
            color: white;
            font-weight: 600;
            font-size: 15px;
            border-radius: 10px;
            text-decoration: none;
            transition: all 0.15s ease;
            box-shadow: 0 2px 6px rgba(0,0,0,0.25);
        }
        .btn-download:hover {
            background: #4752c4;
            box-shadow: 0 4px 10px rgba(0,0,0,0.3);
            transform: translateY(-1px);
        }
        .btn-download:focus-visible {
            outline: 2px solid #ffffff;
            outline-offset: 2px;
        }
        .footer { text-align: center; font-size: 10px; color: #8b949e; margin-top: 20px; letter-spacing: 0.15em; text-transform: uppercase; font-weight: 600; }
    </style>
</head>
<body>
    <div class="card">
        <div class="card-header">
            <div class="icon-wrap">
                <i class="bi bi-file-earmark-arrow-down" style="font-size: 28px; color: #818cf8;"></i>
            </div>
            <h1>{{ filename }}</h1>
            <p>Secure Download Link</p>
        </div>
        <div class="card-body">
            <div class="stat-grid">
                <div class="stat-box">
                    <div class="stat-label">Size</div>
                    <div class="stat-value">{{ file_size }}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Type</div>
                    <div class="stat-value">{{ file_type }}</div>
                </div>
            </div>
            <div class="expiry-box">
                <div class="expiry-label"><i class="bi bi-clock-history"></i> Valid Until</div>
                <div class="expiry-value">{{ expiry_date }}</div>
            </div>
            <a href="{{ download_url }}" class="btn-download">
                <i class="bi bi-cloud-arrow-down-fill" style="font-size: 20px;"></i>
                Download File
            </a>
            <p class="footer">Powered by File Station</p>
        </div>
    </div>
</body>
</html>
"""


SHARED_FOLDER_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Shared Folder - {{ folder_name }}</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23818cf8'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { 
            font-family: 'Inter', sans-serif; 
            background: #0d1117 url('/image.jpg') center/cover no-repeat fixed;
            color: #e6edf3;
        }
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background: rgba(13, 17, 23, 0.6);
            pointer-events: none;
            z-index: -1;
        }
        .glass-panel {
            background: rgba(15, 23, 42, 0.45);
            backdrop-filter: blur(25px) saturate(200%);
            -webkit-backdrop-filter: blur(25px) saturate(200%);
            border: 1px solid rgba(255,255,255,0.1);
            box-shadow: 0 32px 80px rgba(0,0,0,0.6), 0 0 0 0.5px rgba(255,255,255,0.05);
        }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.15); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.3); }
    </style>
</head>
<body class="min-h-screen p-4 sm:p-8 relative">
    <div class="max-w-4xl mx-auto glass-panel rounded-3xl overflow-hidden relative z-10">
        <div class="bg-slate-900/40 border-b border-white/10 p-6 text-white flex items-center justify-between">
            <div class="flex items-center">
                <div class="w-12 h-12 bg-white/10 rounded-xl flex items-center justify-center me-4 border border-white/5">
                    <i class="bi bi-folder-fill text-2xl text-blue-400"></i>
                </div>
                <div>
                    <h1 class="text-xl font-bold truncate">{{ folder_name }}</h1>
                    <p class="text-slate-400 text-xs">Shared Folder</p>
                </div>
            </div>
            <div class="text-right flex gap-6 items-center">
                <a href="{{ url_for('zip_folder', token=token, subpath=current_subpath) }}" 
                   class="inline-flex items-center px-3 py-1.5 bg-white/10 hover:bg-blue-600 text-white rounded-xl text-xs font-bold transition-all border border-white/5"
                   title="Download entire folder as ZIP">
                    <i class="bi bi-file-zip-fill me-1.5"></i> Download ZIP
                </a>
                <div class="hidden sm:block">
                    <p class="text-xs text-slate-400 uppercase tracking-wider font-semibold">Valid Until</p>
                    <p class="text-white font-bold">{{ expiry_date }}</p>
                </div>
                <div class="hidden sm:block text-right">
                    <p class="text-xs text-slate-400 uppercase tracking-wider font-semibold">Total Items</p>
                    <p class="text-white font-bold text-lg leading-tight">{{ items|length }}</p>
                </div>
            </div>
        </div>
        
        <div class="p-0">
            <div class="overflow-x-auto">
                <table class="w-full text-left">
                    <thead class="bg-slate-900/30 border-b border-white/5">
                        <tr>
                            <th class="px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">Name</th>
                            <th class="px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider text-center">Size</th>
                            <th class="px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider text-right">Action</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-white/5">
                        {% if not is_root %}
                        <tr class="hover:bg-white/5 transition-colors">
                            <td class="px-4 py-3" colspan="3">
                                <a href="{{ url_for('public_share', token=token, subpath=parent_subpath) }}" class="flex items-center text-sm font-medium text-blue-400 hover:text-blue-300">
                                    <i class="bi bi-arrow-up-circle-fill text-lg me-3"></i>
                                    <span>.. Back</span>
                                </a>
                            </td>
                        </tr>
                        {% endif %}
                        {% for item in items %}
                        <tr class="hover:bg-white/5 transition-colors group">
                            <td class="px-4 py-3">
                                <div class="flex items-center">
                                    <div class="w-8 text-lg mr-3 
                                        {% if item.is_dir %} text-yellow-500
                                        {% elif item.name.endswith(('.png', '.jpg', '.jpeg', '.gif')) %} text-cyan-400
                                        {% elif item.name.endswith('.pdf') %} text-red-400
                                        {% elif item.name.endswith(('.mp4', '.mov', '.avi')) %} text-green-400
                                        {% elif item.name.endswith(('.zip', '.rar', '.7z')) %} text-orange-400
                                        {% else %} text-blue-400
                                        {% endif %}">
                                        {% if item.is_dir %}<i class="bi bi-folder-fill"></i>
                                        {% elif item.name.endswith(('.png', '.jpg', '.jpeg', '.gif')) %}<i class="bi bi-file-earmark-image-fill"></i>
                                        {% elif item.name.endswith('.pdf') %}<i class="bi bi-file-earmark-pdf-fill"></i>
                                        {% elif item.name.endswith(('.zip', '.rar', '.7z')) %}<i class="bi bi-file-earmark-zip-fill"></i>
                                        {% else %}<i class="bi bi-file-earmark-text-fill"></i>
                                        {% endif %}
                                    </div>
                                    {% if item.is_dir %}
                                    <a href="{{ url_for('public_share', token=token, subpath=item.rel_path) }}" class="text-sm font-medium text-slate-200 hover:text-white hover:underline truncate max-w-xs sm:max-w-md">
                                        {{ item.name }}
                                    </a>
                                    {% else %}
                                    <span class="text-sm font-medium text-slate-300 truncate max-w-xs sm:max-w-md">{{ item.name }}</span>
                                    {% endif %}
                                </div>
                            </td>
                            <td class="px-4 py-3 text-center text-sm text-slate-400 font-mono">
                                {{ item.size }}
                            </td>
                            <td class="px-4 py-3 text-right">
                                {% if not item.is_dir %}
                                <a href="{{ url_for('download_from_folder', token=token, filename=item.rel_path) }}" 
                                   class="inline-flex items-center px-3 py-1.5 bg-white/10 hover:bg-blue-600 hover:border-transparent text-slate-200 border border-white/5 rounded-lg text-xs font-bold transition-all duration-200">
                                    <i class="bi bi-cloud-arrow-down-fill me-1.5"></i> Download
                                </a>
                                {% else %}
                                <a href="{{ url_for('zip_folder', token=token, subpath=item.rel_path) }}" 
                                   class="inline-flex items-center px-3 py-1.5 bg-white/10 hover:bg-blue-600 hover:border-transparent text-slate-200 border border-white/5 rounded-lg text-xs font-bold transition-all duration-200">
                                    <i class="bi bi-file-zip-fill me-1.5"></i> Download ZIP
                                </a>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            
            {% if not items %}
            <div class="p-12 text-center">
                <i class="bi bi-folder2-open text-5xl text-white/20 mb-4 block"></i>
                <p class="text-slate-400 text-sm">This folder is empty</p>
            </div>
            {% endif %}
            <div class="p-6 bg-slate-900/20 border-t border-white/5 text-center">
                <p class="text-[10px] text-slate-500 font-bold uppercase tracking-widest">Powered by File Station</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Sign In - File Station</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23818cf8'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: { sans: ['Plus Jakarta Sans', 'sans-serif'] },
                    colors: {
                        synology: {
                            blue: '#0066cc',
                            hover: '#0059b3',
                            bg: '#1a1b1e',
                            card: '#2c2c2e',
                            input: '#3a3a3c',
                            text: '#f2f2f7',
                            muted: '#8e8e93',
                        }
                    }
                }
            }
        }
    </script>
    <style>
        body {
            background: #0d1117 url('/image.jpg') center/cover no-repeat fixed;
            min-height: 100vh;
            overflow: hidden;
        }
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background: rgba(13, 17, 23, 0.6);
            pointer-events: none;
            z-index: 0;
        }
        .bg-blur-overlay {
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
        }
        .glass-card {
            background: rgba(15, 23, 42, 0.45);
            backdrop-filter: blur(25px);
            -webkit-backdrop-filter: blur(25px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 30px 60px -15px rgba(0, 0, 0, 0.6);
        }
        .glow-input {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.1);
            color: #ffffff;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .glow-input:focus {
            border-color: #5865f2;
            box-shadow: 0 0 0 3px rgba(88,101,242,0.2);
            background: rgba(255,255,255,0.07);
            outline: none;
        }
        .btn-gradient {
            background: linear-gradient(135deg, #5865f2 0%, #7c85f5 100%);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 4px 15px rgba(88,101,242,0.3);
        }
        .btn-gradient:hover {
            background: linear-gradient(135deg, #6674ff 0%, #8b94f8 100%);
            box-shadow: 0 10px 25px -5px rgba(88,101,242,0.5);
            transform: translateY(-1px);
        }
        .btn-gradient:active {
            transform: translateY(1px) scale(0.98);
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-in {
            animation: fadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
        /* Custom scrollbar just in case */
        ::-webkit-scrollbar {
            width: 6px;
        }
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
        }
    </style>
</head>
<body class="antialiased font-sans relative flex items-center justify-center min-h-screen w-screen p-0 m-0">
    <!-- Clean dark background via CSS body class -->

    <!-- Responsive Layout Container -->
    <div class="w-full min-h-screen flex flex-col md:flex-row items-center justify-between p-6 md:p-16 lg:p-24 relative z-10 overflow-y-auto">
        
        <!-- Left Side: Custom NAS Clock & Status Info (Hidden or repositioned on mobile) -->
        <div class="flex flex-col text-white mb-8 md:mb-0 md:max-w-md w-full md:w-auto text-left select-none animate-fade-in mt-4 md:mt-0">
            <div class="mb-4">
                <div class="text-5xl md:text-7xl lg:text-8xl font-light tracking-tight font-sans drop-shadow-md" id="live-time">00:00</div>
                <div class="text-sm md:text-base lg:text-lg font-medium text-slate-200 tracking-wide mt-2 opacity-90 drop-shadow-sm" id="live-date">Monday, 1 January 2026</div>
            </div>
            <div class="mt-4 border-l-2 border-indigo-500 pl-4 py-1 hidden md:block">
                <h2 class="text-lg font-semibold tracking-wide text-white drop-shadow-md">DiskStation File Hub</h2>
                <p class="text-xs text-slate-300 mt-1 opacity-80">Connected to secure private cloud server. Ready to share and manage your assets.</p>
            </div>
        </div>

        <!-- Right Side: Login Glassmorphism Box -->
        <div class="w-full max-w-[420px] animate-fade-in">
            <div class="glass-card rounded-2xl overflow-hidden p-8 sm:p-10 border border-white/10">
                <!-- Header / Logo -->
                <div class="text-center mb-8">
                    <div class="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-slate-950/60 border border-white/10 shadow-inner mb-4 relative group">
                        <i class="bi bi-box-seam-fill text-2xl text-indigo-400 group-hover:scale-110 transition-transform duration-300"></i>
                    </div>
                    <h1 class="text-xl font-bold text-white tracking-tight">Sign In</h1>
                    <p class="text-xs text-slate-400 mt-1">Accessing NAS File Station</p>
                </div>

                <!-- Notification Messages -->
                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% if messages %}
                        {% for category, message in messages %}
                            <div class="mb-6 {{ 'bg-red-500/15 border-red-500/30 text-red-200' if category == 'danger' else 'bg-emerald-500/15 border-emerald-500/30 text-emerald-200' }} border rounded-xl p-3.5 flex items-start gap-3 animate-fade-in">
                                <i class="bi {{ 'bi-exclamation-triangle-fill text-red-400' if category == 'danger' else 'bi-check-circle-fill text-emerald-400' }} mt-0.5 text-base"></i>
                                <div class="flex-1">
                                    <p class="text-xs opacity-90 leading-relaxed font-medium">{{ message }}</p>
                                </div>
                            </div>
                        {% endfor %}
                    {% endif %}
                {% endwith %}

                <!-- Tab Headers -->
                <div class="flex bg-slate-950/50 p-1 rounded-xl border border-white/5 mb-6">
                    <button type="button" onclick="switchLoginMethod('pin')" id="tab-pin" class="flex-1 py-2 text-xs font-semibold rounded-lg transition-all border-b-0 border-transparent bg-transparent text-white focus:outline-none">PIN Access</button>
                    <button type="button" onclick="switchLoginMethod('telegram')" id="tab-telegram" class="flex-1 py-2 text-xs font-semibold rounded-lg transition-all border-b-0 border-transparent bg-transparent text-slate-400 hover:text-white focus:outline-none">Telegram OTP</button>
                </div>

                <!-- PIN Form -->
                <form id="form-pin" method="post" action="{{ url_for('login') }}" class="space-y-5">
                    <input type="hidden" name="login_method" value="pin">
                    <div>
                        <label class="block text-xs font-medium text-slate-300 mb-2 ml-1">Secure PIN</label>
                        <div class="relative group">
                            <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400 group-focus-within:text-blue-400 transition-colors">
                                <i class="bi bi-shield-lock text-sm"></i>
                            </div>
                            <input type="password" name="pin" id="pin-input"
                                   class="glow-input block w-full pl-10 pr-10 py-2.5 rounded-xl text-sm placeholder-slate-500" 
                                   placeholder="Enter Access PIN" required autofocus>
                            <button type="button" onclick="togglePasswordVisibility()" class="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-400 hover:text-slate-200 transition-colors focus:outline-none">
                                <i id="password-toggle-icon" class="bi bi-eye"></i>
                            </button>
                        </div>
                    </div>

                    <button type="submit" 
                            class="btn-gradient w-full text-white font-medium py-3 px-5 rounded-xl flex items-center justify-center gap-2">
                        <span>Sign In</span>
                        <i class="bi bi-chevron-right text-xs"></i>
                    </button>
                </form>

                <!-- Telegram Form -->
                <div id="form-telegram" class="hidden space-y-5">
                    <div id="otp-request-section">
                        <div class="flex items-start gap-3 p-3.5 bg-blue-500/10 border border-blue-500/20 rounded-xl mb-5">
                            <i class="bi bi-info-circle text-blue-400 mt-0.5 text-sm"></i>
                            <p class="text-xs text-slate-300 leading-relaxed">Request a security code sent to the administrator Telegram bot.</p>
                        </div>
                        <button type="button" onclick="requestOTP()" id="btn-request-otp"
                                class="w-full bg-slate-800/80 hover:bg-slate-700/80 text-white font-medium py-3 px-5 rounded-xl flex items-center justify-center gap-2 border border-white/5 hover:border-white/10 transition-all duration-200 active:scale-[0.98]">
                            <i class="bi bi-telegram text-sm"></i>
                            <span>Send Security Code</span>
                        </button>
                    </div>

                    <form method="post" action="{{ url_for('login') }}" id="otp-verify-section" class="hidden space-y-5">
                        <input type="hidden" name="login_method" value="telegram">
                        <div>
                            <label class="block text-xs font-medium text-slate-300 mb-2 ml-1">Telegram OTP</label>
                            <div class="relative group">
                                <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                                    <i class="bi bi-shield-check text-sm"></i>
                                </div>
                                <input type="text" name="otp" maxlength="6"
                                       class="glow-input block w-full pl-10 pr-4 py-2.5 rounded-xl text-sm tracking-[0.4em] text-center font-bold placeholder-slate-600 text-white" 
                                       placeholder="······" required>
                            </div>
                        </div>

                        <button type="submit" 
                                class="btn-gradient w-full text-white font-medium py-3 px-5 rounded-xl flex items-center justify-center gap-2">
                            <span>Verify & Sign In</span>
                            <i class="bi bi-shield-check-fill text-sm"></i>
                        </button>
                        
                        <button type="button" onclick="resetOTPRequest()" class="w-full text-center text-xs text-slate-400 hover:text-slate-200 transition-colors pt-1">
                            Resend Code
                        </button>
                    </form>
                </div>

                <!-- Footer details -->
                <div class="mt-6 pt-5 border-t border-white/5 flex justify-center items-center text-[10px] text-slate-500 font-semibold select-none">
                    <span class="flex items-center gap-1.5"><i class="bi bi-shield-lock-fill text-blue-500/70"></i> Secured Connection</span>
                </div>
            </div>
            
            <div class="mt-6 text-center select-none">
                <p class="text-slate-500 text-[11px]">
                    &copy; {{ current_year }} WAN Portal. All Rights Reserved.
                </p>
            </div>
        </div>
    </div>

    <script>
        // Update Live Clock & Date
        function updateClock() {
            const timeEl = document.getElementById('live-time');
            const dateEl = document.getElementById('live-date');
            if (!timeEl || !dateEl) return;

            const now = new Date();
            let hours = now.getHours().toString().padStart(2, '0');
            let minutes = now.getMinutes().toString().padStart(2, '0');
            timeEl.textContent = `${hours}:${minutes}`;

            const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
            dateEl.textContent = now.toLocaleDateString('en-US', options);
        }
        setInterval(updateClock, 1000);
        updateClock();

        function togglePasswordVisibility() {
            const pinInput = document.getElementById('pin-input');
            const icon = document.getElementById('password-toggle-icon');
            if (pinInput.type === 'password') {
                pinInput.type = 'text';
                icon.classList.remove('bi-eye');
                icon.classList.add('bi-eye-slash');
            } else {
                pinInput.type = 'password';
                icon.classList.add('bi-eye');
                icon.classList.remove('bi-eye-slash');
            }
        }

        function switchLoginMethod(method) {
            const formPin = document.getElementById('form-pin');
            const formTelegram = document.getElementById('form-telegram');
            const tabPin = document.getElementById('tab-pin');
            const tabTelegram = document.getElementById('tab-telegram');

            if (method === 'pin') {
                formPin.classList.remove('hidden');
                formTelegram.classList.add('hidden');
                
                tabPin.classList.add('bg-slate-800/80', 'text-white');
                tabPin.classList.remove('text-slate-400');
                
                tabTelegram.classList.remove('bg-slate-800/80', 'text-white');
                tabTelegram.classList.add('text-slate-400');
            } else {
                formPin.classList.add('hidden');
                formTelegram.classList.remove('hidden');
                
                tabTelegram.classList.add('bg-slate-800/80', 'text-white');
                tabTelegram.classList.remove('text-slate-400');
                
                tabPin.classList.remove('bg-slate-800/80', 'text-white');
                tabPin.classList.add('text-slate-400');
            }
        }
        
        // Initialize switch state
        switchLoginMethod('pin');

        function requestOTP() {
            const btn = document.getElementById('btn-request-otp');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="bi bi-arrow-repeat animate-spin"></i> Sending...';

            fetch('/request_telegram_otp', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('otp-request-section').classList.add('hidden');
                    document.getElementById('otp-verify-section').classList.remove('hidden');
                } else {
                    alert(data.message);
                    btn.disabled = false;
                    btn.innerHTML = originalText;
                }
            })
            .catch(e => {
                alert("Failed to connect to server.");
                btn.disabled = false;
                btn.innerHTML = originalText;
            });
        }

        function resetOTPRequest() {
            document.getElementById('otp-request-section').classList.remove('hidden');
            document.getElementById('otp-verify-section').classList.add('hidden');
            const btn = document.getElementById('btn-request-otp');
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-telegram text-sm"></i> <span>Send Security Code</span>';
        }
    </script>
</body>
</html>
"""

# Edit mode session management
@app.route("/toggle_edit_mode", methods=["POST"])
@login_required
def toggle_edit_mode():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    pin = data.get("pin", "")
    if action == "activate":
        if edit_pin and secrets.compare_digest(str(pin), str(edit_pin)):
            session["edit_mode_active"] = True
            logging.info("Edit mode activated via PIN.")
            return jsonify({"success": True})
        else:
            session["edit_mode_active"] = False
            logging.warning("Failed attempt to activate edit mode.")
            return jsonify({"success": False, "message": "Incorrect PIN."})
    elif action == "deactivate":
        session["edit_mode_active"] = False
        logging.info("Edit mode deactivated.")
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Invalid action."})

@app.route("/check_edit_mode_status", methods=["GET"])
@login_required
def check_edit_mode_status():
    return jsonify({"active": session.get("edit_mode_active", False)})

# Network configuration
@app.route("/network_config", methods=["GET"])
@login_required
def get_network_config():
    network_data = []
    try:
        interface_addrs = psutil.net_if_addrs()
        gateway_default = "N/A"
        try:
            if platform.system() == "Windows":
                flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                output = subprocess.check_output("ipconfig", text=True, creationflags=flags)
                for line in output.splitlines():
                    if "Default Gateway" in line or "Gerbang Default" in line:
                        parts = line.split(":")
                        if len(parts) > 1 and parts[1].strip():
                            gw = parts[1].strip()
                            if gw and not gw.startswith("::"):
                                gateway_default = gw
                                break
            else:
                if os.path.exists("/proc/net/route"):
                    with open("/proc/net/route", "r") as f:
                        for line in f.readlines()[1:]:
                            fields = line.strip().split()
                            if len(fields) >= 3 and fields[1] == '00000000':
                                gateway_default = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
                                break
        except Exception as e:
            logging.debug(f"Failed to resolve default gateway: {e}")
        interface_stats = psutil.net_if_stats()
        for name, addrs in interface_addrs.items():
            is_relevant = False
            ipv4_config = {}
            mac_address = "N/A"
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    if addr.address.startswith('127.'):
                         continue
                    ipv4_config = {
                        "ip_address": addr.address,
                        "netmask": addr.netmask,
                    }
                    is_relevant = True
                elif addr.family == psutil.AF_LINK:
                    mac_address = addr.address
            if is_relevant:
                cidr = ""
                try:
                    if ipv4_config.get("netmask") and ipv4_config.get("ip_address"):
                        network = ipaddress.ip_network(f'{ipv4_config["ip_address"]}/{ipv4_config["netmask"]}', strict=False)
                        cidr = f"/{network.prefixlen}"
                except Exception:
                    cidr = ""
                status = "Down"
                if interface_stats.get(name) and interface_stats[name].isup:
                    status = "Up"
                network_data.append({
                    "interface": name,
                    "mac_address": mac_address,
                    "ip_address": ipv4_config.get("ip_address", "N/A"),
                    "netmask": ipv4_config.get("netmask", "N/A"),
                    "cidr": cidr,
                    "gateway": gateway_default,
                    "dns": "N/A (OS-dependent, check OS settings)",
                    "status": status
                })
        return jsonify({"success": True, "network_configs": network_data})
    except Exception as e:
        logging.error(f"Failed to get network config: {e}")
        return jsonify({"success": False, "error": f"Failed to get network configuration: {e}"}), 500

# Windows Event Log 1074
if platform.system() == "Windows":
    def clean_time_string(time_raw):
        try:
            if 'T' in time_raw and ('Z' in time_raw or '.' in time_raw):
                time_raw = time_raw.split('.')[0] 
                time_obj = datetime.strptime(time_raw, '%Y-%m-%dT%H:%M:%S')
                return time_obj.strftime('%Y-%m-%d %H:%M:%S')
            try:
                time_obj = datetime.strptime(time_raw.split('.')[0], '%Y-%m-%d %H:%M:%S')
                return time_obj.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                time_obj = datetime.strptime(time_raw.split('.')[0], '%m/%d/%Y %I:%M:%S %p')
                return time_obj.strftime('%Y-%m-%d %H:%M:%S')
            except:
                return time_raw
        except Exception as e:
            logging.debug(f"Time parsing failed for '{time_raw}': {e}")
            return time_raw

    @app.route("/event_log/1074", methods=["GET"])
    @login_required
    def get_event_log_1074():
        start_time = datetime.now() - timedelta(days=30)
        start_time_str = start_time.strftime('%Y-%m-%dT%H:%M:%S')
        xpath_query = (
            f"*[System[("
            f"EventID=1074) and ("
            f"TimeCreated[@SystemTime>='{start_time_str}']"
            f")]]"
        )
        command = [
            'wevtutil', 'qe', 'System', f'/q:{xpath_query}',
            '/c:100', '/rd:true', '/f:text'
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            logs = []
            current_log = {}
            for line in result.stdout.splitlines():
                if line.strip().startswith(('Tanggal:', 'Date:', 'Log Name:')):
                    if current_log and (current_log.get("TimeCreated") != "N/A" or len(logs) > 0): 
                        logs.append(current_log)
                    current_log = {"TimeCreated": "N/A", "Process": "N/A", "Reason": "N/A"}
                    if line.strip().startswith(('Tanggal:', 'Date:')):
                         try:
                             time_raw = line.split(': ', 1)[-1].strip()
                             current_log["TimeCreated"] = clean_time_string(time_raw)
                         except:
                             pass
                elif 'Pengguna Yang Melakukan Log On:' in line or 'User:' in line:
                    pass
                elif 'Nama Proses:' in line or 'Process Name:' in line:
                    current_log['Process'] = line.split(': ', 1)[-1].strip()
                elif 'Alasan:' in line or 'Reason:' in line or 'Shutdown Type:' in line or 'Comment:' in line:
                    reason_detail = line.split(":", 1)[-1].strip()
                    if "telah memulai" in line or "has initiated" in line or "Reason:" in line:
                         current_log['Reason'] = reason_detail
                    else:
                         current_log['Reason'] = current_log.get('Reason', '').strip() + " | " + reason_detail
            if current_log and current_log not in logs:
                 logs.append(current_log)
            logs.reverse() 
            if not logs:
                 return jsonify({"success": True, "logs": [], "message": f"No Event ID 1074 logs found since {start_time_str.split('T')[0]}."})
            return jsonify({"success": True, "logs": logs, "message": "Logs retrieved successfully."})
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to run wevtutil: {e.stderr}")
            return jsonify({"success": False, "error": f"Failed to retrieve logs. Check server privileges (Error: {e.stderr.strip()})."}), 500
        except Exception as e:
            logging.error(f"Error accessing event log: {e}")
            return jsonify({"success": False, "error": f"Error accessing Event Log: {e}"}), 500
else:
    @app.route("/event_log/1074", methods=["GET"])
    @login_required
    def get_event_log_1074():
        return jsonify({"success": False, "error": "Event Log (ID 1074) feature is only available on Windows."})

@app.route("/get_folder_list", methods=["GET"])
@login_required
def get_folder_list():
    target_rel_path = request.args.get("path", "").strip().replace("\\", "/")
    if target_rel_path in ["", "/", "."]:
        current_path = ""
    else:
        current_path = target_rel_path.rstrip("/")
    folder_data = []
    try:
        for root, dirs, _ in os.walk(directory):
            # Exclude hidden directories like .upload_temp
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            dirs.sort(key=lambda x: x.lower())
            for d in list(dirs):
                full_path = os.path.join(root, d)
                rel_path = os.path.relpath(full_path, directory).replace("\\", "/")
                depth = rel_path.count("/")
                display_name = ("\u00A0\u00A0\u00A0\u00A0" * depth) + d if depth > 0 else d
                folder_data.append({
                    "name": display_name,
                    "path": rel_path,
                    "is_open": False,
                    "children": []
                })
        return jsonify({
            "folders": folder_data,
            "current_path": current_path 
        })
    except Exception as e:
        logging.error(f"Failed to load folder list: {e}")
        return jsonify({"error": "Failed to load folder list due to a server error."}), 500

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_method = request.form.get("login_method", "pin")
        client_ip = get_real_client_ip() 
        user_agent = request.headers.get('User-Agent', 'Unknown Browser')
        
        if login_method == "telegram":
            otp_input = request.form.get("otp", "").strip()
            stored_otp = session.get("login_otp")
            expiry_str = session.get("login_otp_expiry")
            
            if not stored_otp or not expiry_str:
                flash("OTP not requested or session expired.", "danger")
                return render_template_string(LOGIN_TEMPLATE, current_year=datetime.now().year)
            
            try:
                expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                if datetime.now() > expiry_dt:
                    flash("OTP has expired. Please request a new one.", "danger")
                    return render_template_string(LOGIN_TEMPLATE, current_year=datetime.now().year)
            except Exception:
                flash("Internal session error. Please try again.", "danger")
                return render_template_string(LOGIN_TEMPLATE, current_year=datetime.now().year)
                
            if otp_input and stored_otp and secrets.compare_digest(str(otp_input), str(stored_otp)):
                session.pop("login_otp", None)
                session.pop("login_otp_expiry", None)
                session["logged_in"] = True
                send_login_notification("SUCCESS✅ (Telegram OTP)", client_ip, user_agent)
                next_url = request.args.get("next")
                if not next_url or not next_url.startswith("/") or next_url.startswith("//") or "\\" in next_url:
                    next_url = url_for("list_files")
                return redirect(next_url)
            else:
                send_login_notification("❌FAILED (Wrong OTP)❌", client_ip, user_agent)
                flash("Invalid OTP code.", "danger")
                return render_template_string(LOGIN_TEMPLATE, current_year=datetime.now().year)

        else: # Standard PIN
            pin = request.form.get("pin", "")
            if login_pin and secrets.compare_digest(str(pin), str(login_pin)):
                session["logged_in"] = True
                send_login_notification("SUCCESS✅ (PIN)", client_ip, user_agent)
                next_url = request.args.get("next")
                if not next_url or not next_url.startswith("/") or next_url.startswith("//") or "\\" in next_url:
                    next_url = url_for("list_files")
                return redirect(next_url)
            else:
                send_login_notification("❌FAILED (Wrong PIN)❌", client_ip, user_agent)
                flash("Access denied: Incorrect PIN.", "danger")
                
    return render_template_string(LOGIN_TEMPLATE, current_year=datetime.now().year)

@app.route("/image.jpg")
def serve_wallpaper():
    """Route to serve the macOS wallpaper background image"""
    wallpaper_path = get_resource_path('image.jpg')
    if os.path.exists(wallpaper_path):
        return send_from_directory(os.path.dirname(wallpaper_path), os.path.basename(wallpaper_path))
    abort(404)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/system_stats", methods=["GET"])
@login_required
def get_system_stats():
    GB = 1024 ** 3
    try:
        processor_model = platform.processor()
        if platform.system() == "Windows":
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                     r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", 
                                     0, 
                                     winreg.KEY_READ)
                processor_model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                winreg.CloseKey(key)
            except Exception as e:
                logging.warning(f"Failed to read ProcessorNameString from Registry: {e}")
                processor_model = platform.processor()
        os_version = platform.platform()
        if platform.system() == "Windows":
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                     r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", 
                                     0, 
                                     winreg.KEY_READ)
                product_name, _ = winreg.QueryValueEx(key, "ProductName")
                current_build, _ = winreg.QueryValueEx(key, "CurrentBuild")
                version_info = ""
                try:
                    display_version, _ = winreg.QueryValueEx(key, "DisplayVersion")
                    version_info = f"Version {display_version}"
                except FileNotFoundError:
                    try:
                        release_id, _ = winreg.QueryValueEx(key, "ReleaseId")
                        version_info = f"Release {release_id}"
                    except FileNotFoundError:
                        pass
                if "windows 10" in product_name.lower() and int(current_build) >= 22000:
                    product_name = product_name.replace("10", "11")
                os_version = f"{product_name} {version_info} (Build {current_build})"
                winreg.CloseKey(key)
            except Exception as e:
                logging.warning(f"Failed to read Windows OS details from Registry: {e}")
                os_version = platform.platform()
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_cores = psutil.cpu_count(logical=True)
        ram_info = psutil.virtual_memory()
        ram_total_gb = round(ram_info.total / GB, 2)
        ram_available_gb = round(ram_info.available / GB, 2)
        ram_used_gb = round(ram_info.used / GB, 2)
        ram_percent = ram_info.percent
        disk_partitions_data = []
        partitions = psutil.disk_partitions(all=False)
        for p in partitions:
            if 'cdrom' in p.opts or p.fstype == '':
                continue
            try:
                usage = psutil.disk_usage(p.mountpoint)
                disk_total_gb = round(usage.total / GB, 2)
                disk_used_gb = round(usage.used / GB, 2)
                disk_free_gb = round(usage.free / GB, 2)
                disk_used_percent = usage.percent
                disk_partitions_data.append({
                    "device": p.device,
                    "mountpoint": p.mountpoint,
                    "fstype": p.fstype,
                    "disk_total_gb": disk_total_gb,
                    "disk_used_gb": disk_used_gb,
                    "disk_free_gb": disk_free_gb,
                    "disk_used_percent": round(disk_used_percent, 2)
                })
            except Exception as e:
                logging.warning(f"Could not get disk usage for {p.mountpoint}: {e}")
        active_connections_data = []
        try:
            for conn in psutil.net_connections(kind='inet'): 
                process_name = "N/A"
                if conn.pid:
                    try:
                        p = psutil.Process(conn.pid)
                        process_name = p.name()
                    except psutil.NoSuchProcess:
                        process_name = f"PID {conn.pid} (Dead)"
                    except Exception:
                        process_name = f"PID {conn.pid} (Error)"
                local_addr = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr and conn.laddr.ip else "N/A"
                remote_addr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr and conn.raddr.ip else "-"
                conn_type = "TCP" if conn.type == socket.SOCK_STREAM else "UDP"
                active_connections_data.append({
                    "pid": conn.pid if conn.pid else "-",
                    "process": process_name,
                    "type": conn_type,
                    "local_address": local_addr,
                    "remote_address": remote_addr,
                    "status": conn.status if conn.status else "N/A"
                })
        except Exception as e:
            logging.debug(f"Could not retrieve full active network connections: {e}")
        return jsonify({
            "processor_model": processor_model,
            "os_version": os_version,
            "cpu_percent": cpu_percent,
            "cpu_cores": cpu_cores,
            "ram_total_gb": ram_total_gb,
            "ram_available_gb": ram_available_gb,
            "ram_used_gb": ram_used_gb,
            "ram_percent": ram_percent,
            "disk_partitions": disk_partitions_data,
            "active_connections": active_connections_data 
        })
    except Exception as e:
        logging.error(f"Failed to get system stats (general error): {e}")
        return jsonify({"error": f"Failed to get system statistics: {e}"}), 500

@app.route("/", defaults={"req_path": ""})
@app.route("/<path:req_path>")
@login_required
def list_files(req_path):
    abs_path = os.path.abspath(os.path.join(directory, req_path))
    if not is_safe_path(directory, abs_path):
        logging.warning(f"Unauthorized access attempt: {abs_path}")
        abort(403)
    if os.path.isfile(abs_path):
        ext = os.path.splitext(abs_path)[1].lower()
        if not is_allowed_extension(ext, allow_zip=True):
            logging.warning(f"Direct access blocked for disallowed extension ({ext}): {abs_path}")
            abort(403)
        as_attachment = request.args.get('preview') != '1'
        return send_from_directory(os.path.dirname(abs_path), os.path.basename(abs_path), as_attachment=as_attachment)
    if not os.path.isdir(abs_path):
        logging.warning(f"Path not found: {abs_path}")
        abort(404)
    file_list = []
    for item in os.listdir(abs_path):
        item_path = os.path.join(abs_path, item)
        rel_path = os.path.relpath(item_path, directory).replace("\\", "/")
        if os.path.isdir(item_path):
            created_time = os.path.getctime(item_path)
            created_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_time))
            file_list.append((rel_path + "/", "-", "-", created_date))
        elif os.path.isfile(item_path):
            ext = os.path.splitext(item)[1].lower()
            if is_allowed_extension(ext, allow_zip=True):
                size, version, created_date = get_file_info(item_path)
                file_list.append((rel_path, size, version, created_date))
            else:
                logging.warning(f"File {item} is not allowed.")
    parent_path = os.path.relpath(os.path.dirname(abs_path), directory).replace("\\", "/")
    if parent_path == ".":
        parent_path = ""
    final_template = HTML_TEMPLATE.replace("{% include 'modals.html' %}", MODALS_HTML)
    return render_template_string(
        final_template,
        files=file_list,
        directory=abs_path,
        base_directory=directory,
        parent_path=parent_path,
        req_path=req_path,
        edit_pin=edit_pin,
        os=os,
        current_year=datetime.now().year 
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files or "pin" not in request.form or "target_path" not in request.form:
        flash("File, PIN, and target path must be included.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    pin = request.form.get("pin", "")
    if not upload_pin or not secrets.compare_digest(str(pin), str(upload_pin)):
        logging.warning(f"Incorrect PIN for upload: {pin}")
        flash("Incorrect PIN! Access denied.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    file = request.files["file"]
    if not file or file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    filename = get_secure_filename(file.filename)
    if not filename:
        flash("Invalid filename.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    ext = os.path.splitext(filename)[1].lower()
    if not is_allowed_extension(ext):
        flash("File type is not allowed.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    file.seek(0, os.SEEK_END)
    file_size_bytes = file.tell()
    file.seek(0)
    file_size_mb = file_size_bytes / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        flash(f"File is too large! Maximum {MAX_FILE_SIZE_MB} MB.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    target_rel_path = request.form["target_path"].strip().replace("/", os.sep)
    target_abs_path = os.path.abspath(os.path.join(directory, target_rel_path))
    if not is_safe_path(directory, target_abs_path):
        logging.warning(f"Unauthorized upload path: {target_abs_path}")
        flash("Unauthorized upload path.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    file_path = os.path.abspath(os.path.join(target_abs_path, filename))
    if not is_safe_path(target_abs_path, file_path) or not is_safe_path(directory, file_path):
        logging.warning(f"Unauthorized upload file path: {file_path}")
        flash("Unauthorized upload path.", "danger")
        return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    os.makedirs(target_abs_path, exist_ok=True)
    if os.path.exists(file_path):
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            base, ext = os.path.splitext(filename)
            backup_filename = f"{base}.BAK_{timestamp}{ext}"
            backup_path = os.path.abspath(os.path.join(target_abs_path, backup_filename))
            if is_safe_path(target_abs_path, backup_path):
                shutil.move(file_path, backup_path)
                logging.info(f"Existing file backed up to: {backup_filename}")
                flash(f"Existing file '{filename}' was backed up (versioned).", "warning")
        except Exception as e:
            logging.error(f"Failed to backup existing file {filename}: {e}")
            flash("Failed to create file backup. Upload aborted.", "danger")
            return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))
    file.save(file_path)
    logging.info(f"File uploaded to {target_abs_path}: {filename} ({file_size_mb:.2f} MB)")
    flash(f"File '{filename}' successfully uploaded.", "success")
    return redirect(url_for("list_files", req_path=request.form.get("target_path", "")))

@app.route("/delete", methods=["POST"])
@login_required
def delete_file():
    file_to_delete_rel = (request.form.get("file_path") or "").strip().lstrip("/\\").rstrip("/\\")
    pin = request.form.get("pin")
    if not file_to_delete_rel or file_to_delete_rel in [".", "/", "\\"]:
        flash("Cannot delete root directory.", "danger")
        return redirect(url_for("list_files"))
    
    if not session.get("edit_mode_active"):
        if not pin:
            flash("PIN is required.", "danger")
            return redirect(url_for("list_files"))
        if not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin)):
            logging.warning(f"Incorrect PIN for file deletion: {pin}")
            flash("Incorrect PIN! Access denied.", "danger")
            return redirect(url_for("list_files"))
    file_to_delete_abs = os.path.abspath(os.path.join(directory, file_to_delete_rel))
    current_path_rel = os.path.dirname(file_to_delete_rel).replace(os.sep, "/")
    if current_path_rel == ".":
        current_path_rel = ""

    if file_to_delete_abs == os.path.abspath(directory) or not is_safe_path(directory, file_to_delete_abs):
        logging.warning(f"Unauthorized deletion attempt outside the allowed directory: {file_to_delete_abs}")
        flash("Operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not os.path.exists(file_to_delete_abs):
        flash("File not found.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    try:
        if os.path.isfile(file_to_delete_abs):
            os.remove(file_to_delete_abs)
            logging.info(f"File successfully deleted: {file_to_delete_abs}")
            flash(f"File '{os.path.basename(file_to_delete_abs)}' successfully deleted.", "success")
        elif os.path.isdir(file_to_delete_abs):
            if not os.listdir(file_to_delete_abs):
                os.rmdir(file_to_delete_abs)
                logging.info(f"Directory successfully deleted: {file_to_delete_abs}")
                flash(f"Directory '{os.path.basename(file_to_delete_abs)}' successfully deleted.", "success")
            else:
                flash("Cannot delete non-empty folder. Please delete content first.", "danger")
                return redirect(url_for("list_files", req_path=current_path_rel))
        else:
            flash("Cannot delete, path is not a file or directory.", "danger")
    except OSError as e:
        logging.error(f"Error deleting {file_to_delete_abs}: {e}")
        flash(f"Failed to delete file/directory: {e}", "danger")
    return redirect(url_for("list_files", req_path=current_path_rel))

@app.route("/bulk_delete", methods=["POST"])
@login_required
def bulk_delete():
    files_json = request.form.get("files")
    pin = request.form.get("pin")
    current_path_rel = request.form.get("current_path", "")
    
    if not files_json:
        flash("No files provided for deletion.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    
    if not session.get("edit_mode_active"):
        if not pin:
            flash("PIN is required.", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
        if not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin)):
            logging.warning(f"Incorrect PIN for bulk deletion: {pin}")
            flash("Incorrect PIN! Access denied.", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
        
    try:
        files = json.loads(files_json)
    except json.JSONDecodeError:
        flash("Invalid file list format.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
        
    success_count = 0
    error_count = 0
    
    for file_rel in files:
        file_to_delete_rel = file_rel.lstrip("/\\").rstrip("/\\")
        file_to_delete_abs = os.path.abspath(os.path.join(directory, file_to_delete_rel))
        if not is_safe_path(directory, file_to_delete_abs) or not os.path.exists(file_to_delete_abs):
            error_count += 1
            continue
            
        try:
            if os.path.isfile(file_to_delete_abs):
                os.remove(file_to_delete_abs)
                success_count += 1
            elif os.path.isdir(file_to_delete_abs):
                if not os.listdir(file_to_delete_abs):
                    os.rmdir(file_to_delete_abs)
                    success_count += 1
                else:
                    error_count += 1
        except Exception as e:
            logging.error(f"Error bulk deleting {file_to_delete_abs}: {e}")
            error_count += 1
            
    if success_count > 0:
        flash(f"Successfully deleted {success_count} item(s).", "success")
    if error_count > 0:
        flash(f"Failed to delete {error_count} item(s) (ensure folders are empty).", "danger")
        
    return redirect(url_for("list_files", req_path=current_path_rel))

@app.route("/bulk_compress", methods=["POST"])
@login_required
def bulk_compress():
    files_json = request.form.get("files")
    output_name = request.form.get("output_name")
    pin = request.form.get("pin")
    current_path_rel = request.form.get("current_path", "")
    
    if not files_json or not output_name:
        flash("Files and output name must be provided.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
        
    if not session.get("edit_mode_active"):
        if not pin:
            flash("PIN is required.", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
        if not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin)):
            logging.warning(f"Incorrect PIN for bulk compression: {pin}")
            flash("Incorrect PIN! Access denied.", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
        
    sanitized_output = get_secure_filename(output_name)
    if not sanitized_output:
        flash("Invalid output file name.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not sanitized_output.lower().endswith(".zip"):
        sanitized_output += ".zip"
        
    try:
        files = json.loads(files_json)
    except json.JSONDecodeError:
        flash("Invalid file list format.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
        
    current_dir_abs = os.path.abspath(os.path.join(directory, current_path_rel))
    if not is_safe_path(directory, current_dir_abs):
        flash("Operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    output_abs = os.path.abspath(os.path.join(current_dir_abs, sanitized_output))
    if not is_safe_path(current_dir_abs, output_abs) or not is_safe_path(directory, output_abs):
        flash("Operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    
    if os.path.exists(output_abs):
        flash(f"File '{sanitized_output}' already exists. Delete the existing file first.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
        
    try:
        with zipfile.ZipFile(output_abs, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_rel in files:
                target_abs = os.path.abspath(os.path.join(directory, file_rel.lstrip("/\\").rstrip("/\\")))
                if is_safe_path(directory, target_abs) and os.path.exists(target_abs):
                    zip_folder_or_file(target_abs, zipf, os.path.dirname(target_abs))
                    
        flash(f"Successfully compressed {len(files)} item(s) to '{sanitized_output}'.", "success")
        logging.info(f"Bulk compression completed to {output_abs}")
    except Exception as e:
        logging.error(f"Bulk compression failed: {e}")
        flash(f"Bulk compression failed: {e}", "danger")
        
    return redirect(url_for("list_files", req_path=current_path_rel))

@app.route("/rename", methods=["POST"])
@login_required
def rename_file():
    old_file_rel = (request.form.get("file_path") or "").lstrip("/\\").rstrip("/\\")
    new_name_base = request.form.get("new_name")
    pin = request.form.get("pin")
    if not old_file_rel or not new_name_base:
        flash("File path or new name not provided.", "danger")
        return redirect(url_for("list_files"))
    if not session.get("edit_mode_active"):
        if not pin:
             flash("PIN is required.", "danger")
             return redirect(url_for("list_files"))
        if not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin)):
            logging.warning(f"Incorrect PIN for file renaming: {pin}")
            flash("Incorrect PIN! Access denied.", "danger")
            return redirect(url_for("list_files"))
    old_file_abs = os.path.abspath(os.path.join(directory, old_file_rel))
    current_path_rel = os.path.dirname(old_file_rel).replace(os.sep, "/")
    if current_path_rel == ".":
        current_path_rel = ""

    if not is_safe_path(directory, old_file_abs):
        logging.warning(f"Unauthorized renaming attempt outside the allowed directory: {old_file_abs}")
        flash("Operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not os.path.exists(old_file_abs):
        flash("File or directory not found.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    try:
        sanitized_name = get_secure_filename(new_name_base)
        if not sanitized_name:
            flash("Invalid new name.", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
        if os.path.isfile(old_file_abs):
            _, old_ext = os.path.splitext(os.path.basename(old_file_abs))
            new_name_root, _ = os.path.splitext(sanitized_name)
            final_new_name = new_name_root + old_ext
        elif os.path.isdir(old_file_abs):
            final_new_name = sanitized_name
        else:
            flash("Cannot rename, path is not a file or directory.", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
        parent_dir = os.path.dirname(old_file_abs)
        new_name_abs = os.path.abspath(os.path.join(parent_dir, final_new_name))
        if not is_safe_path(parent_dir, new_name_abs) or not is_safe_path(directory, new_name_abs):
            logging.warning(f"Unauthorized renaming destination: {new_name_abs}")
            flash("Operation not allowed.", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
        if os.path.exists(new_name_abs):
             flash(f"A file or directory named '{final_new_name}' already exists.", "danger")
             return redirect(url_for("list_files", req_path=current_path_rel))
        os.rename(old_file_abs, new_name_abs)
        logging.info(f"File/directory successfully renamed from '{old_file_rel}' to '{final_new_name}'")
        flash(f"'{os.path.basename(old_file_rel)}' was successfully renamed to '{final_new_name}'.", "success")
    except OSError as e:
        logging.error(f"Error renaming {old_file_abs}: {e}")
        flash(f"Failed to rename file: {e}", "danger")
    return redirect(url_for("list_files", req_path=current_path_rel))

@app.route("/new_folder", methods=["POST"])
@login_required
def new_folder():
    raw_folder_name = request.form.get("folder_name", "")
    pin = request.form.get("pin")
    target_rel_path = request.form.get("target_path", "").replace("/", os.sep)
    if not raw_folder_name or not pin:
        flash("Folder name and PIN must be included.", "danger")
        return redirect(url_for("list_files", req_path=target_rel_path))
    if not session.get("edit_mode_active") and (not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin))):
        logging.warning(f"Incorrect PIN for creating a folder: {pin}")
        flash("Incorrect PIN! Access denied.", "danger")
        return redirect(url_for("list_files", req_path=target_rel_path))
    folder_name = get_secure_filename(raw_folder_name)
    if not folder_name:
        flash("Invalid folder name.", "danger")
        return redirect(url_for("list_files", req_path=target_rel_path))
    parent_abs_path = os.path.abspath(os.path.join(directory, target_rel_path))
    if not is_safe_path(directory, parent_abs_path):
        logging.warning(f"Attempt to create folder outside the allowed directory: {parent_abs_path}")
        flash("Operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=target_rel_path))
    target_abs_path = os.path.abspath(os.path.join(parent_abs_path, folder_name))
    if not is_safe_path(parent_abs_path, target_abs_path) or not is_safe_path(directory, target_abs_path):
        logging.warning(f"Attempt to create folder outside the allowed directory: {target_abs_path}")
        flash("Operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=target_rel_path))
    if os.path.exists(target_abs_path):
        flash("A folder with that name already exists.", "danger")
        return redirect(url_for("list_files", req_path=target_rel_path))
    try:
        os.makedirs(target_abs_path)
        logging.info(f"New folder successfully created: {target_abs_path}")
        flash(f"Folder '{folder_name}' successfully created.", "success")
    except OSError as e:
        logging.error(f"Failed to create folder {target_abs_path}: {e}")
        flash(f"Failed to create folder: {e}", "danger")
    return redirect(url_for("list_files", req_path=target_rel_path))

@app.route("/move", methods=["POST"])
@login_required
def move_file():
    source_rel = request.form.get("source_path")
    destination_rel = request.form.get("destination_folder", "").strip().replace("\\", "/") 
    pin = request.form.get("pin")
    if not source_rel or not destination_rel or not pin:
        flash("Source path, destination folder, and PIN must be included.", "danger")
        return redirect(url_for("list_files"))
    if not session.get("edit_mode_active") and (not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin))):
        logging.warning(f"Incorrect PIN for file move: {pin}")
        flash("Incorrect PIN! Access denied.", "danger")
        current_path_rel = os.path.dirname(source_rel).replace(os.sep, "/")
        return redirect(url_for("list_files", req_path=current_path_rel))
    source_abs = os.path.abspath(os.path.join(directory, source_rel))
    current_path_rel = os.path.dirname(source_rel).replace(os.sep, "/")
    destination_rel_cleaned = destination_rel.strip().rstrip("/").rstrip("\\")
    if destination_rel_cleaned.startswith(os.path.basename(directory)):
        destination_rel_cleaned = destination_rel_cleaned[len(os.path.basename(directory)) + 1:].lstrip("/").lstrip("\\")
    destination_abs = os.path.abspath(os.path.join(directory, destination_rel_cleaned))
    if not is_safe_path(directory, source_abs) or not os.path.exists(source_abs):
        logging.warning(f"Unauthorized move source or source not found: {source_abs}")
        flash("Source file/folder not found or operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not is_safe_path(directory, destination_abs):
        logging.warning(f"Unauthorized move destination outside the allowed directory: {destination_abs}")
        flash("Destination folder not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not os.path.isdir(destination_abs):
        logging.warning(f"Move destination is not a directory or does not exist: {destination_abs}")
        flash(f"Destination folder '{destination_rel_cleaned if destination_rel_cleaned else '/'}' does not exist.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    target_name = os.path.basename(source_abs)
    target_abs = os.path.abspath(os.path.join(destination_abs, target_name))
    if not is_safe_path(destination_abs, target_abs) or not is_safe_path(directory, target_abs):
        flash("Destination folder not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if os.path.exists(target_abs):
        flash(f"Cannot move: A file/folder named '{target_name}' already exists in the destination.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if os.path.isdir(source_abs) and target_abs.startswith(source_abs + os.sep):
        flash("Cannot move a folder into its own sub-directory.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    try:
        shutil.move(source_abs, target_abs)
        destination_display = destination_rel_cleaned if destination_rel_cleaned else '/'
        logging.info(f"File/directory successfully moved from '{source_rel}' to '{destination_display}'")
        flash(f"'{target_name}' successfully moved to '{destination_display}'.", "success")
        return redirect(url_for("list_files", req_path=current_path_rel))
    except Exception as e:
        logging.error(f"Error moving {source_abs} to {destination_abs}: {e}")
        flash(f"Failed to move file/directory: {e}", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))

@app.route("/compress", methods=["POST"])
@login_required
def compress_file():
    target_rel = request.form.get("target_path")
    output_name = request.form.get("output_name")
    pin = request.form.get("pin")
    if not target_rel or not output_name or not pin:
        flash("Target path, output name, and PIN must be provided.", "danger")
        return redirect(url_for("list_files"))
    if not session.get("edit_mode_active") and (not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin))):
        logging.warning(f"Incorrect PIN for compression: {pin}")
        flash("Incorrect PIN! Access denied.", "danger")
        return redirect(url_for("list_files"))
    if not output_name.lower().endswith(".zip"):
        output_name += ".zip"
    target_abs = os.path.abspath(os.path.join(directory, target_rel))
    current_dir_abs = os.path.dirname(target_abs)
    current_path_rel = os.path.dirname(target_rel).replace(os.sep, "/")
    if not is_safe_path(directory, target_abs) or not os.path.exists(target_abs):
        flash("File or folder not found or operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    sanitized_output = get_secure_filename(output_name)
    if not sanitized_output:
        flash("Invalid output file name.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not sanitized_output.lower().endswith(".zip"):
        sanitized_output += ".zip"
    output_abs = os.path.abspath(os.path.join(current_dir_abs, sanitized_output))
    if not is_safe_path(current_dir_abs, output_abs) or not is_safe_path(directory, output_abs):
        flash("Operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if os.path.exists(output_abs):
        flash(f"File '{sanitized_output}' already exists. Delete the existing file first.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    try:
        with zipfile.ZipFile(output_abs, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zip_folder_or_file(target_abs, zipf, os.path.dirname(target_abs))
            flash(f"'{os.path.basename(target_abs)}' successfully compressed to '{sanitized_output}'.", "success")
            logging.info(f"Item compressed: {target_abs} to {output_abs}")
    except Exception as e:
        logging.error(f"Compression failed for {target_rel}: {e}")
        flash(f"Compression failed: {e}", "danger")
    return redirect(url_for("list_files", req_path=current_path_rel))

@app.route("/extract", methods=["POST"])
@login_required
def extract_file():
    archive_rel = request.form.get("archive_path")
    destination_rel = request.form.get("destination_folder")
    pin = request.form.get("pin")
    if not archive_rel or not destination_rel or not pin:
        flash("Archive path, destination folder, and PIN must be provided.", "danger")
        return redirect(url_for("list_files"))
    if not session.get("edit_mode_active") and (not edit_pin or not secrets.compare_digest(str(pin), str(edit_pin))):
        logging.warning(f"Incorrect PIN for extraction: {pin}")
        flash("Incorrect PIN! Access denied.", "danger")
        return redirect(url_for("list_files"))
    if not archive_rel.lower().endswith(".zip"):
        flash("File is not a supported ZIP archive.", "danger")
        return redirect(url_for("list_files"))
    archive_abs = os.path.abspath(os.path.join(directory, archive_rel))
    destination_abs = os.path.abspath(os.path.join(directory, destination_rel))
    current_path_rel = os.path.dirname(archive_rel).replace(os.sep, "/")
    if not is_safe_path(directory, archive_abs) or not os.path.exists(archive_abs) or not os.path.isfile(archive_abs):
        flash("Archive file not found or operation not allowed.", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not is_safe_path(directory, destination_abs):
        flash("Destination folder not allowed (must be inside shared directory).", "danger")
        return redirect(url_for("list_files", req_path=current_path_rel))
    if not os.path.exists(destination_abs):
        try:
            os.makedirs(destination_abs)
        except OSError as e:
            flash(f"Failed to create destination folder: {e}", "danger")
            return redirect(url_for("list_files", req_path=current_path_rel))
    try:
        with zipfile.ZipFile(archive_abs, 'r') as zipf:
            for member in zipf.namelist():
                sanitized_member = os.path.normpath(member)
                if sanitized_member.startswith('..'):
                    raise Exception("Path traversal detected.")
                member_path = os.path.abspath(os.path.join(destination_abs, sanitized_member))
                if not is_safe_path(destination_abs, member_path) or not is_safe_path(directory, member_path):
                    flash("Archive contains files that try to extract outside the destination. Extraction aborted.", "danger")
                    logging.error(f"Zip Slip attempt detected in archive: {archive_rel}")
                    return redirect(url_for("list_files", req_path=current_path_rel))
            zipf.extractall(destination_abs)
            flash(f"Archive '{os.path.basename(archive_abs)}' successfully extracted to '{destination_rel}'.", "success")
            logging.info(f"Archive extracted: {archive_abs} to {destination_abs}")
    except zipfile.BadZipFile:
        flash("Extraction failed: The file is not a valid ZIP archive.", "danger")
    except Exception as e:
        logging.error(f"Extraction failed for {archive_rel}: {e}")
        flash(f"Extraction failed: {e}", "danger")
    return redirect(url_for("list_files", req_path=current_path_rel))

def run_server():
    logging.info(f"Server is running at http://{host}:{port}")
    serve(app, host=host, port=port)

def create_tray_icon():
    from pystray import Icon, MenuItem, Menu
    image = None
    icon_path = get_resource_path("icon.ico")
    if os.path.exists(icon_path):
        try:
            image = Image.open(icon_path)
        except Exception as e:
            logging.error(f"Failed to load icon.ico: {e}")
            
    if image is None:
        try:
            # Create a professional, transparent fallback icon with a dark navy/black gradient
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            from PIL import ImageDraw
            
            # Draw gradient and elements on a temporary canvas
            temp_canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(temp_canvas)
            
            # Smooth dark-navy to black background gradient
            for y in range(4, 60):
                ratio = (y - 4) / 56
                r = int(15 * (1 - ratio) + 2 * ratio)
                g = int(23 * (1 - ratio) + 6 * ratio)
                b = int(42 * (1 - ratio) + 23 * ratio)
                draw.line([(4, y), (59, y)], fill=(r, g, b, 255))
            
            # Draw rounded border and server bays
            draw.rounded_rectangle([4, 4, 59, 59], radius=12, outline=(0, 102, 204, 220), width=2)
            draw.rounded_rectangle([18, 18, 45, 25], radius=2, fill=(240, 240, 240, 230))
            draw.rounded_rectangle([18, 28, 45, 35], radius=2, fill=(240, 240, 240, 230))
            draw.rounded_rectangle([18, 38, 45, 45], radius=2, fill=(240, 240, 240, 230))
            
            # Small green LEDs
            draw.ellipse([40, 20, 42, 22], fill=(34, 197, 94, 255))
            draw.ellipse([40, 30, 42, 32], fill=(34, 197, 94, 255))
            draw.ellipse([40, 40, 42, 42], fill=(34, 197, 94, 255))
            
            # Rounded corner mask
            mask = Image.new("L", (64, 64), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([4, 4, 59, 59], radius=12, fill=255)
            
            # Combine canvas with mask
            image.paste(temp_canvas, (0, 0), mask=mask)
        except Exception as e:
            logging.error(f"Failed to generate dynamic tray image: {e}")
            image = Image.new("RGB", (64, 64), (0, 102, 204))

    menu = Menu(MenuItem("Exit", exit_app))
    icon = Icon("FileServer", image, menu=menu)
    icon.run()

def exit_app(icon, item):
    logging.info("Shutting down application...")
    icon.stop()
    os._exit(0)

SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <title>Settings - File Station</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%235865f2'%3E%3Cpath fill-rule='evenodd' d='M15.528 2.973a.75.75 0 0 1 .472.696v8.662a.75.75 0 0 1-.472.696l-7.25 2.9a.75.75 0 0 1-.557 0l-7.25-2.9A.75.75 0 0 1 0 12.331V3.669a.75.75 0 0 1 .471-.696L7.443.184l.01-.003.268-.108a.75.75 0 0 1 .558 0l.269.108.01.003zM10.404 2 4.25 4.461 1.846 3.5 1 3.839v.4l6.5 2.6v7.922l.5.2.5-.2V6.84l6.5-2.6v-.4l-.846-.339L8 5.961 5.596 5l6.154-2.461z'/%3E%3C/svg%3E">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0d1117 url('/image.jpg') center/cover no-repeat fixed;
            color: #e6edf3;
        }
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background: rgba(13, 17, 23, 0.6);
            pointer-events: none;
            z-index: -1;
        }
        .glass-panel {
            background: rgba(22, 27, 34, 0.45);
            backdrop-filter: blur(35px) saturate(200%);
            -webkit-backdrop-filter: blur(35px) saturate(200%);
            border: 1px solid rgba(255,255,255,0.1);
            box-shadow: 0 32px 80px rgba(0,0,0,0.6), 0 0 0 0.5px rgba(255,255,255,0.05);
        }
        .input-glass {
            background: rgba(0,0,0,0.2);
            border: 1px solid rgba(255,255,255,0.1);
            color: white;
            transition: all 0.2s;
        }
        .input-glass:focus {
            outline: none;
            border-color: #6366f1;
            background: rgba(0,0,0,0.4);
            box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
        }
    </style>
</head>
<body class="min-h-screen p-4 md:p-8">
    <div class="max-w-3xl mx-auto">
        <div class="flex items-center justify-between mb-8">
            <h1 class="text-2xl font-bold text-white flex items-center gap-3">
                <i class="bi bi-gear-fill text-indigo-400"></i> Settings Dashboard
            </h1>
            <a href="/" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-sm rounded-lg transition-colors border border-white/10 flex items-center gap-2">
                <i class="bi bi-house-door-fill"></i> Back to Home
            </a>
        </div>
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                <!-- Glassmorphism Toast Container -->
                <div class="fixed top-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-3 pointer-events-none w-[90%] sm:w-auto min-w-[320px] max-w-lg" id="toastContainer">
                {% for category, message in messages %}
                    <div class="toast-message pointer-events-auto p-3.5 sm:p-4 rounded-2xl text-sm flex items-center justify-between bg-slate-800/85 backdrop-blur-xl shadow-2xl border transition-all duration-500
                        {{ 'border-emerald-500/30' if category == 'success' else 'border-red-500/30' }}" role="alert"
                        style="box-shadow: 0 10px 40px -10px rgba(0,0,0,0.5); animation: toastSlideDown 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;">
                        <div class="flex items-center gap-3">
                            <div class="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center 
                                {{ 'bg-emerald-500/20 text-emerald-400' if category == 'success' else 'bg-red-500/20 text-red-400' }}">
                                <i class="bi {{ 'bi-check-lg' if category == 'success' else 'bi-exclamation-triangle-fill' }} text-lg"></i>
                            </div>
                            <span class="text-slate-200 font-medium">{{ message }}</span>
                        </div>
                        <button type="button" class="ml-4 opacity-60 hover:opacity-100 transition-opacity bg-white/5 hover:bg-white/10 rounded-full w-7 h-7 flex items-center justify-center shrink-0" onclick="closeToast(this.closest('.toast-message'))">
                            <i class="bi bi-x-lg text-xs text-white"></i>
                        </button>
                    </div>
                {% endfor %}
                </div>
                <style>
                    @keyframes toastSlideDown {
                        0% { opacity: 0; transform: translateY(-20px) scale(0.95); }
                        100% { opacity: 1; transform: translateY(0) scale(1); }
                    }
                    .toast-hide {
                        opacity: 0 !important;
                        transform: translateY(-20px) scale(0.95) !important;
                        margin-top: -80px !important;
                    }
                </style>
                <script>
                    function closeToast(el) {
                        el.classList.add('toast-hide');
                        setTimeout(() => el.remove(), 500);
                    }
                    setTimeout(() => {
                        document.querySelectorAll('.toast-message').forEach(toast => closeToast(toast));
                    }, 4000);
                </script>
            {% endif %}
        {% endwith %}

        <form method="POST" action="/settings" class="space-y-6">
            <!-- Server Settings -->
            <div class="glass-panel p-6 rounded-2xl">
                <h2 class="text-lg font-semibold mb-4 text-indigo-300 border-b border-white/10 pb-2">Server Settings</h2>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm text-slate-400 mb-1">Host (IP Address)</label>
                        <input type="text" name="host" value="{{ config.host }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-1">Port <span class="text-xs text-yellow-500">(Requires Restart)</span></label>
                        <input type="number" name="port" value="{{ config.port }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-sm text-slate-400 mb-1">Shared Directory Path</label>
                        <input type="text" name="directory" value="{{ config.directory }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-sm text-slate-400 mb-1">Allowed Extensions (Comma separated)</label>
                        <input type="text" name="allowed_extensions" value="{{ config.raw_extensions }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                        <p class="text-xs text-slate-500 mt-1.5"><i class="bi bi-info-circle mr-1"></i> Leave blank to allow all file types.</p>
                    </div>
                </div>
            </div>

            <!-- Security PINs -->
            <div class="glass-panel p-6 rounded-2xl">
                <h2 class="text-lg font-semibold mb-4 text-indigo-300 border-b border-white/10 pb-2">Security PINs</h2>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label class="block text-sm text-slate-400 mb-1">Login PIN</label>
                        <input type="text" name="login_pin" value="{{ config.login_pin }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-1">Upload PIN</label>
                        <input type="text" name="upload_pin" value="{{ config.upload_pin }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-1">Edit PIN</label>
                        <input type="text" name="edit_pin" value="{{ config.edit_pin }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                </div>
            </div>

            <!-- Telegram Bot -->
            <div class="glass-panel p-6 rounded-2xl">
                <h2 class="text-lg font-semibold mb-4 text-indigo-300 border-b border-white/10 pb-2">Telegram Bot Notifications</h2>
                <div class="grid grid-cols-1 gap-4">
                    <div class="flex items-center gap-3 mb-2">
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" name="tg_enabled" value="true" class="sr-only peer" {% if config.tg_enabled %}checked{% endif %}>
                            <div class="w-11 h-6 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-500"></div>
                        </label>
                        <span class="text-sm font-medium text-slate-300">Enable Telegram Notifications</span>
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-1">Bot Token</label>
                        <input type="text" name="tg_bot_token" value="{{ config.tg_bot_token }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-1">Chat ID</label>
                        <input type="text" name="tg_chat_id" value="{{ config.tg_chat_id }}" class="w-full px-3 py-2 rounded-lg input-glass text-sm">
                    </div>
                </div>
            </div>

            <div class="flex justify-end pt-4">
                <button type="submit" class="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg transition-colors shadow-lg shadow-indigo-900/50 flex items-center gap-2">
                    <i class="bi bi-save2"></i> Save Settings
                </button>
            </div>
        </form>
    </div>
</body>
</html>
"""

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_dashboard():
    if not session.get('edit_mode_active'):
        flash("You must activate Edit Mode to access Settings.", "danger")
        return redirect(url_for('list_files'))
        
    if request.method == 'POST':
        set_setting('host', request.form.get('host', '0.0.0.0'))
        set_setting('port', request.form.get('port', '5000'))
        new_dir = request.form.get('directory', '').strip()
        if not new_dir:
            new_dir = 'shared_files'
        set_setting('directory', new_dir)
        set_setting('allowed_extensions', request.form.get('allowed_extensions', ''))
        
        new_login_pin = request.form.get('login_pin', '').strip()
        new_upload_pin = request.form.get('upload_pin', '').strip()
        new_edit_pin = request.form.get('edit_pin', '').strip()
        
        if new_login_pin:
            set_setting('login_pin', new_login_pin)
        else:
            flash("Login PIN cannot be empty; previous PIN retained.", "warning")
            
        if new_upload_pin:
            set_setting('upload_pin', new_upload_pin)
        else:
            flash("Upload PIN cannot be empty; previous PIN retained.", "warning")
            
        if new_edit_pin:
            set_setting('edit_pin', new_edit_pin)
        else:
            flash("Edit PIN cannot be empty; previous PIN retained.", "warning")
        
        set_setting('tg_bot_token', request.form.get('tg_bot_token', ''))
        set_setting('tg_chat_id', request.form.get('tg_chat_id', ''))
        set_setting('tg_enabled', 'true' if request.form.get('tg_enabled') else 'false')
        
        reload_global_settings()
        flash("Settings saved successfully! Directory and PIN changes are active.", "success")
        return redirect(url_for('settings_dashboard'))
        
    current_config = {
        'host': host,
        'port': port,
        'directory': directory,
        'raw_extensions': raw_extensions,
        'login_pin': login_pin,
        'upload_pin': upload_pin,
        'edit_pin': edit_pin,
        'tg_bot_token': tg_bot_token,
        'tg_chat_id': tg_chat_id,
        'tg_enabled': tg_enabled
    }
    
    return render_template_string(SETTINGS_HTML, config=current_config)

@app.route('/api/save_text', methods=['POST'])
@login_required
def save_text():
    if not session.get('edit_mode_active'):
        return jsonify({'success': False, 'message': 'Edit mode is not active'}), 403
    
    data = request.get_json(silent=True) or {}
    req_path = data.get('req_path') or data.get('file_path')
    content = data.get('content')
    
    if not req_path:
        return jsonify({'success': False, 'message': 'No path provided'}), 400
    if content is None:
        content = ""
        
    clean_req_path = req_path.lstrip("/\\")
    base_dir = get_setting('directory', 'shared_files')
    abs_path = os.path.abspath(os.path.join(base_dir, clean_req_path))
    if not is_safe_path(base_dir, abs_path):
        return jsonify({'success': False, 'message': 'Unauthorized path'}), 403
        
    _, ext = os.path.splitext(abs_path)
    if not is_allowed_extension(ext):
        return jsonify({'success': False, 'message': 'File type is not allowed'}), 403
        
    try:
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logging.info(f"File updated via web editor: {clean_req_path}")
        return jsonify({'success': True, 'message': 'File saved successfully'})
    except Exception as e:
        logging.error(f"Failed to save file {clean_req_path}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    if platform.system() == "Windows":
        create_tray_icon()
    else:
        server_thread.join()