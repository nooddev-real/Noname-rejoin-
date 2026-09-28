import os
import time
import json
import subprocess
import sys
import threading
import requests
import sqlite3
import shutil
import psutil
import random
import re
import platform
import socket
import hashlib
import shlex
import secrets
from threading import Event, Lock
from collections import deque
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256, md5
import asyncio
from aiohttp import web

START_TIME = time.time()

# ĐÃ TỐI ƯU: HTTP Session toàn cục — reuse TCP connections thay vì tạo mới mỗi lần
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
})

# ĐÃ TỐI ƯU: Hàng đợi log luồng để hiển thị dưới Dashboard chống đè chữ
_CONSOLE_LOGS = deque(maxlen=6)
_CONSOLE_LOGS_LOCK = Lock()
_DASHBOARD_ACTIVE = False
_DASHBOARD_REFRESH_EVENT = Event()

def log_console(msg: str):
    # Lọc bỏ mã màu ANSI khi ghi log file nếu cần, ở đây giữ nguyên để hiển thị màu dưới dashboard
    clean_msg = msg.strip()
    if not clean_msg:
        return
    if _DASHBOARD_ACTIVE:
        with _CONSOLE_LOGS_LOCK:
            _CONSOLE_LOGS.append(clean_msg)
        _DASHBOARD_REFRESH_EVENT.set()
    else:
        print(clean_msg, flush=True)

def log_server(msg: str, is_warn=False, is_err=False):
    if globals().get("SHOW_LOCAL_SERVER_LOGS", False):
        if is_err:
            log_console(f"{C_ERR}{msg}{C_RESET}")
        elif is_warn:
            log_console(f"{C_WARN}{msg}{C_RESET}")
        else:
            log_console(f"{C_MAIN}{msg}{C_RESET}")


# ==========================================
#        MÀU SẮC GIAO DIỆN
# ==========================================
C_MAIN  = "\033[38;2;142;224;136m"
C_SUB   = "\033[38;2;197;228;194m"
C_RESET = "\033[0m"
C_CYAN  = "\033[1;36m"
C_ERR   = "\033[38;2;255;85;85m"
C_WARN  = "\033[38;2;255;170;0m"
LOGO_TEMPLATE = 1
TERMUX_BOOT_ENABLED = False

# ==========================================
#        CẤU HÌNH HỆ THỐNG
# ==========================================
CONFIG_DIR             = "NONAME"
SELECTED_PACKAGES_FILE = os.path.join(CONFIG_DIR, "selected_packages.json")
CONFIG_FILE            = os.path.join(CONFIG_DIR, "config.json")
SAVED_KEY_FILE         = os.path.join(CONFIG_DIR, "key.txt")
LUA_SOURCE_FILE        = os.path.join(CONFIG_DIR, "checkui.lua")
COOKIE_TXT             = "/sdcard/Download/cookie.txt"
AUTOEXEC_HUB_DIR       = "/sdcard/Download/Autoexe"
TMP_DIR                = "/data/local/tmp"
URL_FORMAT             = "roblox://experiences/start?placeId={}"

HOSTS_FILE         = "/etc/hosts"
FREEZE_CHECK_DELAY = 15          # giây giữa mỗi lần check freeze
MAX_FREEZE_STRIKES = 6           # cần 6 lần CPU delta thấp (~60s) mới trigger
MIN_CPU_DELTA      = 3           # delta <= này mới tính là freeze (tránh idle ngắn)
REJOIN_COOLDOWN    = 45          # tăng từ 30→45s, giảm rejoin vòng vòng
CAPTCHA_TIMEOUT    = 180
LOCAL_MONITOR_PORT = 5000
SHOW_LOCAL_SERVER_LOGS = False
LOGIN_PAYLOAD_CONTENT = r"""{
    "AppConfiguration": "{\"GUAC:10562178957:app-patch-in-experience\":\"{\\\"SchemaVersion\\\":\\\"1\\\",\\\"CanaryUserIds\\\":[],\\\"CanaryPercentage\\\":0,\\\"Stable\\\":{\\\"AppStorageResetId\\\":\\\"0\\\",\\\"AssetId\\\":\\\"80471914653504\\\",\\\"AssetVersion\\\":\\\"3113\\\",\\\"MaxAppVersion\\\":\\\"684\\\",\\\"IsForcedUpdate\\\":false}}\"}",
    "RobloxLocaleId": "en_us",
    "AppInstallationId": "374492643241884787",
    "Username": "noname_tool",
    "UserId": "10562178957",
    "Membership": "0",
    "DisplayName": "NoNameTool",
    "IsUnder13": "false",
    "AccountBlob": ""
}"""


SERVER_STATUS_URL = "https://gist.githubusercontent.com/nooddev-real/e01da2369603da66476f03981b38ed2d/raw/status.json"

VALID_KEYS_URL    = "https://gist.githubusercontent.com/nooddev-real/9c69c3729e032103a80b62374ba98dfd/raw/key.json"

GET_KEY_URL       = "https://gist.githubusercontent.com/nooddev-real/29938dc8bd15bed641df345175372075/raw/getkey.json"

# HTTP headers cho request server status (FIX: tránh NameError khi obf)
HDR = {
    "User-Agent":    "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
    "Accept":        "application/json",
    "Cache-Control": "no-cache",
}
# ==========================================================



try:
    IS_ROOT = os.geteuid() == 0
except:
    IS_ROOT = False

if not IS_ROOT:
    su_path = None
    for path in ["/system/bin/su", "/system/xbin/su", "/system/sbin/su"]:
        if os.path.exists(path):
            su_path = path
            break
    if not su_path:
        su_path = shutil.which("su") or "su"
    try:
        test_out = subprocess.check_output([su_path, "-c", "id"], stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=2.0).decode()
        if "uid=0" in test_out:
            current_file = os.path.abspath(sys.argv[0])
            current_dir = os.path.dirname(current_file)
            python_bin = sys.executable
            cmd_args = [python_bin, current_file] + sys.argv[1:]
            cmd_str = f"cd {shlex.quote(current_dir)} && export PATH=$PATH:/data/data/com.termux/files/usr/bin && " + " ".join(shlex.quote(x) for x in cmd_args)
            print(f"\n\033[38;2;255;170;0m⚠️ Cảnh báo: Tool chưa chạy dưới quyền ROOT (đang chạy với User thường).\033[0m")
            print(f"\033[38;2;142;224;136m🚀 Đang tự động nâng quyền hệ thống lên ROOT để tối ưu hóa CPU & tránh nghẽn su...\033[0m")
            time.sleep(1.5)
            os.execv(su_path, [su_path, "-c", cmd_str])
    except:
        pass

# BIẾN TOÀN CỤC
WEBHOOK_URL          = ""
SERVER_HOST_URL      = ""
DEVICE_NAME          = "Android Farm"
SERVER_TELEMETRY_ENABLED = False

WEBHOOK_INTERVAL     = 5
MONITOR_ENABLED      = True
FPS_LIMIT            = 20
CLEAN_CACHE_ENABLED  = True
LAUNCH_DELAY         = 10.0
WAKEUP_DELAY         = 2.0
BATCH_LAUNCH_ENABLED = False
EXTREME_MODE         = False
INJECT_SCRIPT_ENABLED = True
HEARTBEAT_TIMEOUT    = 220
LOAD_TIMEOUT         = 220
API_CHECK_INTERVAL   = 180
FIX_LAG_ENABLED = False
AUTO_BLOCK_ENABLED = False
SCREEN_WIDTH  = 540
SCREEN_HEIGHT = 960
SAVED_HWID = ""
SORT_TAB_ENABLED = False
DELTA_AUTO_KEY_ENABLED = False
_DELTA_BYPASSING = False
AUTO_CYCLE_MINUTES = 0
CYCLE_FARM_RANDOM_MINS = 0
CYCLE_REST_MINUTES = 0
CYCLE_REST_RANDOM_MINS = 0
TAB_CYCLE_MINUTES = {}

def get_tab_cycle_config(pkg: str):
    """
    Trả về (farm_mins, farm_random, rest_mins, rest_random) cho tab pkg.
    Ưu tiên cấu hình riêng trong TAB_CYCLE_MINUTES, nếu không có thì lấy cấu hình chung.
    """
    custom = TAB_CYCLE_MINUTES.get(pkg)
    if custom is not None:
        if isinstance(custom, dict):
            farm = custom.get("farm")
            if farm is None: farm = AUTO_CYCLE_MINUTES
            farm_rnd = custom.get("farm_random")
            if farm_rnd is None: farm_rnd = CYCLE_FARM_RANDOM_MINS
            rest = custom.get("rest")
            if rest is None: rest = CYCLE_REST_MINUTES
            rest_rnd = custom.get("rest_random")
            if rest_rnd is None: rest_rnd = CYCLE_REST_RANDOM_MINS
            return int(farm), int(farm_rnd), int(rest), int(rest_rnd)
        elif isinstance(custom, (int, float)):
            return int(custom), CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS
    return AUTO_CYCLE_MINUTES, CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS


def format_cycle_desc(farm, farm_rnd, rest, rest_rnd):
    if not farm or farm <= 0:
        return "Disabled (Tắt)"
    farm_s = f"Treo {farm}m" + (f" (±{farm_rnd}m)" if farm_rnd > 0 else "")
    if rest > 0:
        rest_s = f" -> Nghỉ {rest}m" + (f" (±{rest_rnd}m)" if rest_rnd > 0 else "")
    else:
        rest_s = " (Rejoin ngay)"
    return farm_s + rest_s

cookie_content = ""
download_path = "/storage/emulated/0/Download/cookie.txt"

os.makedirs(CONFIG_DIR, exist_ok=True)

stop_webhook_thread = False
GLOBAL_STATUS = {}
STATUS_LOCK   = Lock()
CAPTCHA_LOCK  = Lock()
INPUT_LOCK    = Lock()
SOLVING_LIST  = {}
REJOIN_LOCK   = Lock()
LAUNCH_MUTEX = Lock()

workspace_paths   = []
valid_exec_folders = []
user_data          = {}

# ==========================================
#    COOKIE DATABASE PERSISTENCE
# ==========================================
class CookieDB:
    DB_FILE = os.path.join(CONFIG_DIR, "cookie.db")

    @classmethod
    def init_db(cls):
        try:
            conn = sqlite3.connect(cls.DB_FILE)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_cookies (
                    package TEXT PRIMARY KEY,
                    userid TEXT,
                    username TEXT,
                    cookie TEXT,
                    updated_at REAL
                )
            """)
            conn.commit()
            conn.close()
        except:
            pass

    @classmethod
    def save_cookie(cls, pkg: str, userid: str, username: str, cookie: str):
        cls.init_db()
        try:
            conn = sqlite3.connect(cls.DB_FILE)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO account_cookies (package, userid, username, cookie, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(package) DO UPDATE SET
                    userid=excluded.userid,
                    username=excluded.username,
                    cookie=excluded.cookie,
                    updated_at=excluded.updated_at
            """, (pkg, str(userid) if userid else "", username or "", cookie or "", time.time()))
            conn.commit()
            conn.close()
        except:
            pass

    @classmethod
    def load_all_cookies(cls) -> dict:
        cls.init_db()
        res = {}
        try:
            conn = sqlite3.connect(cls.DB_FILE)
            cur = conn.cursor()
            cur.execute("SELECT package, userid, username, cookie FROM account_cookies")
            rows = cur.fetchall()
            for r in rows:
                res[r[0]] = {
                    "id": r[1] if r[1] else None,
                    "username": r[2] if r[2] else "Unknown",
                    "cookie": r[3] if r[3] else None
                }
            conn.close()
        except:
            pass
        return res
last_api_check     = {}
last_rejoin_time                = {}

# ── ACTIVE REJOIN THREADS (tránh zombie) ────────────────────────────────
_active_rejoin_threads: dict = {}   # pkg → Thread

# ── PID CACHE ────────────────────────────────────────────────────────────
_pid_cache:    dict = {}
_RUNNING_PIDS_MAP: dict = {}
PID_CACHE_TTL = 90.0

def map_to_three_states(raw_status: str) -> str:
    """Ánh xạ tất cả trạng thái phức tạp về 3 trạng thái duy nhất: Running, Joining, Pending."""
    s = raw_status.lower()
    if "running" in s or s.startswith("run") or "lag" in s:
        return "Running"
    if "joining" in s or "loading" in s or "starting" in s or "waking" in s or "waithb" in s or "rej:" in s:
        return "Joining"
    return "Pending"


# ── ANTI-FALSE-REJOIN COUNTERS ────────────────────────────────────────────
_pid_miss_count:   dict = {}   # pkg → int  (cần miss 3 lần liên tiếp mới crash)
_hbtimeout_grace:  dict = {}   # pkg → float (wall_time lần đầu phát hiện timeout)
_api_bad_count:    dict = {}   # pkg → int  (cần 2 lần bad mới rejoin)
HBTIMEOUT_GRACE_SEC = 35        # grace 35s trước khi rejoin do HBTimeout

# ── HEARTBEAT SYSTEM ─────────────────────────────────────────────────────
_hb_rate_window: dict = {}   # pkg → deque([wall_time, ...])
_hb_lock              = Lock()


# ==========================================
#    c_input  (fix liệt phím)
# ==========================================
def c_input(prompt_text: str) -> str:
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    try:
        return input()
    except EOFError:
        return ""


# ==========================================
#    SHELL UTILS
# ==========================================
def run_cmd(cmd_str: str, timeout_sec: float = 10):
    try:
        if IS_ROOT:
            subprocess.run(cmd_str, shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=timeout_sec)
        else:
            subprocess.run(["su", "-c", cmd_str],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=timeout_sec)
    except:
        pass


def run_batch_cmd(cmds: list, timeout_sec: float = 15):
    """Gộp nhiều lệnh shell thành 1 subprocess duy nhất — giảm overhead tạo process."""
    if not cmds:
        return
    joined = " ; ".join(cmds)
    run_cmd(joined, timeout_sec=timeout_sec)


def get_cmd_output(cmd_str: str, timeout_sec: float = 8) -> str:
    try:
        if IS_ROOT:
            return subprocess.check_output(
                cmd_str, shell=True, timeout=timeout_sec,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
            ).decode("utf-8", errors="ignore")
        else:
            return subprocess.check_output(
                ["su", "-c", cmd_str], timeout=timeout_sec,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
            ).decode("utf-8", errors="ignore")
    except:
        return ""


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as err:
        import errno
        if err.errno == errno.ESRCH:
            return False
        return True
    return True




def update_running_pids_cache():
    """Quét thư mục /proc một lần duy nhất để tạo cache PID cho tất cả package hoạt động."""
    global _RUNNING_PIDS_MAP
    new_map = {}
    try:
        for name in os.listdir('/proc'):
            if name.isdigit():
                try:
                    with open(os.path.join('/proc', name, 'cmdline'), 'r') as f:
                        cmd = f.read()
                    if cmd:
                        pkg = cmd.split('\x00')[0].strip()
                        if pkg:
                            new_map[pkg] = int(name)
                except:
                    pass
    except:
        pass
    _RUNNING_PIDS_MAP = new_map


def get_pkg_pid(pkg: str) -> int | None:
    now = time.time()
    pid, ts = _pid_cache.get(pkg, (None, 0))
    if pid is None and (now - ts) < 15.0:
        return None
    if pid and process_exists(pid):
        return pid

    # 1. Tra cứu trực tiếp từ cache map quét chung của loop
    pid = _RUNNING_PIDS_MAP.get(pkg)
    
    # 2. Nếu không tìm thấy (ví dụ mới bật app), quét thủ công nhanh cho package này
    if pid is None:
        try:
            for name in os.listdir('/proc'):
                if name.isdigit():
                    try:
                        with open(os.path.join('/proc', name, 'cmdline'), 'r') as f:
                            cmd = f.read()
                        if pkg in cmd:
                            pid = int(name)
                            break
                    except:
                        pass
        except:
            pass

    # 3. Fallback cuối cùng bằng pidof
    if pid is None:
        try:
            out = subprocess.check_output(
                ["pidof", pkg] if IS_ROOT else ["su", "-c", f"pidof {pkg}"],
                timeout=2, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
            ).decode("utf-8", errors="ignore").strip().split()
            pid = int(out[0]) if out and out[0].isdigit() else None
        except:
            pid = None
        
    _pid_cache[pkg] = (pid, now)
    return pid


def optimize_roblox_process(pid: int):
    """Gán PID vào cpuset/cpuctl foreground để tránh bị Android bóp chạy ngầm (giữ mức ưu tiên mặc định, tuyệt đối không giới hạn RAM)."""
    if not IS_ROOT or pid <= 0:
        return
    try:
        # Không can thiệp priority (nice value) và không giới hạn RAM để tránh xung đột với Android System
        cgroup_paths = [
            "/dev/cpuset/foreground/tasks",
            "/dev/cpuset/top-app/tasks",
            "/dev/cpuctl/foreground/tasks",
            "/dev/cpuctl/top-app/tasks",
            "/sys/fs/cgroup/cpuset/foreground/tasks",
            "/sys/fs/cgroup/cpuset/top-app/tasks"
        ]
        for path in cgroup_paths:
            if os.path.exists(path):
                try:
                    with open(path, "a") as f:
                        f.write(f"{pid}\n")
                except:
                    pass
    except Exception:
        pass


def invalidate_pid_cache(pkg: str):
    _pid_cache.pop(pkg, None)


def kill_and_force_stop(pkg: str):
    """Tắt ứng dụng triệt để bằng kill -9 và am force-stop."""
    pid = get_pkg_pid(pkg)
    if pid:
        run_cmd(f"kill -9 {pid}", timeout_sec=3)
    else:
        run_cmd(f"pkill -9 -f {pkg}", timeout_sec=3)
    invalidate_pid_cache(pkg)
    time.sleep(1.0)
    run_cmd(f"am force-stop {pkg}", timeout_sec=5)
    invalidate_pid_cache(pkg)




def get_smart_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LOCAL_IP = get_smart_local_ip() 

_SMART_HB_CACHE = {}
_SMART_HB_LOCK = threading.Lock()

class APIHeartbeatManager:
    @classmethod
    def fetch_data(cls):
        with _SMART_HB_LOCK:
            return _SMART_HB_CACHE.copy()
            
    @classmethod
    def update_from_lua(cls, pkg, hb_data):
        if not isinstance(hb_data, dict): return
        hb_data["last_seen"] = time.time()
        with _SMART_HB_LOCK:
            _SMART_HB_CACHE[pkg] = hb_data
            
    @classmethod
    def invalidate(cls, pkg):
        with _SMART_HB_LOCK:
            _SMART_HB_CACHE.pop(pkg, None)
            u = user_data.get(pkg, {})
            u_id = str(u.get("id", "")).strip()
            u_name = str(u.get("username", "")).strip()
            if u_id: _SMART_HB_CACHE.pop(u_id, None)
            if u_name:
                _SMART_HB_CACHE.pop(u_name, None)
                _SMART_HB_CACHE.pop(u_name.lower(), None)

    @classmethod
    def get_user_status(cls, identifier):
        if not identifier: return {}
        with _SMART_HB_LOCK:
            identifier = str(identifier)
            if identifier in _SMART_HB_CACHE:
                res = _SMART_HB_CACHE[identifier]
                return res if isinstance(res, dict) else {}
            
            for data in _SMART_HB_CACHE.values():
                if not isinstance(data, dict): continue
                if str(data.get("userid")) == identifier or str(data.get("username")) == identifier:
                    return data
            return {} 

def _apply_hb_match(p: str, userid: str, username: str, content: dict, now_ts: float):
    APIHeartbeatManager.update_from_lua(userid, content)
    APIHeartbeatManager.update_from_lua(username, content)
    if username != 'Unknown':
        APIHeartbeatManager.update_from_lua(username.lower(), content)
    APIHeartbeatManager.update_from_lua(p, content)

    _push_hb_api(p)
    with STATUS_LOCK:
        if p in GLOBAL_STATUS:
            GLOBAL_STATUS[p]["last_heartbeat"] = now_ts
            GLOBAL_STATUS[p]["first_hb"] = True
            curr_st = str(GLOBAL_STATUS[p].get("status", ""))
            if "Rejoin" not in curr_st and "Rej" not in curr_st and "Kick" not in curr_st:
                GLOBAL_STATUS[p]["status"] = "Running"
    log_server(f"⚡ [Heartbeat] Tab {username} ({userid}) -> {p} | HB Count: {content.get('hb', 'N/A')}")

def _bind_pkg_to_user(userid: str, username: str, content: dict, now_ts: float) -> str | None:
    # 1. Thử khớp chính xác trước (Chỉ khớp với package đang thực sự hoạt động trong GLOBAL_STATUS)
    for p, u in list(user_data.items()):
        if p in GLOBAL_STATUS:
            u_id = str(u.get("id", "")).strip()
            u_name = str(u.get("username", "")).strip()
            if (u_id and u_id == userid) or (u_name and u_name.lower() == username.lower()) or (p == userid) or (p == username):
                _apply_hb_match(p, userid, username, content, now_ts)
                return p

    # 2. Nếu không khớp, thử auto-bind sang package đang Joining
    target_pkg = None
    with STATUS_LOCK:
        for p, v in GLOBAL_STATUS.items():
            st = str(v.get("status", ""))
            if "Join" in st:
                target_pkg = p
                break

    if target_pkg:
        p = target_pkg
        if p not in user_data:
            user_data[p] = {}
        user_data[p]["id"] = int(userid) if userid.isdigit() else userid
        user_data[p]["username"] = username
        
        # Cập nhật SQLite
        try:
            conn = sqlite3.connect(CookieDB.DB_FILE)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO account_cookies (package, userid, username, cookie, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(package) DO UPDATE SET
                    userid=excluded.userid,
                    username=excluded.username,
                    updated_at=excluded.updated_at
            """, (p, userid, username, "", time.time()))
            conn.commit()
            conn.close()
        except Exception as e:
            log_server(f"❌ [Local Server] Lỗi lưu liên kết vào DB: {e}", is_err=True)
            
        log_server(f"⚡ [Local Server] Tự động liên kết Tab {username} ({userid}) -> {p}")
        _apply_hb_match(p, userid, username, content, now_ts)
        return p

    log_server(f"⚠️ [Local Server] Không tìm thấy Tab nào ở trạng thái Joining để liên kết cho {username} ({userid})!", is_warn=True)
    return None

async def monitor_update(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    try:
        content = None
        try:
            content = await request.json(content_type=None)
        except Exception:
            try:
                raw_text = await request.text()
                content = json.loads(raw_text)
            except Exception:
                return web.json_response({"error": "Bad JSON"}, status=400, headers=headers)

        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                pass

        if not isinstance(content, dict):
            return web.json_response({"error": "Invalid Format"}, status=400, headers=headers)

        userid = str(content.get('userid', 'Unknown')).strip()
        username = str(content.get('username', 'Unknown')).strip()
        is_kicked = content.get('is_kicked', False)

        # Bỏ qua gói tin rác nếu userid/username chưa load xong
        if userid in ("Unknown", "0", "") and username in ("Unknown", "Loading...", ""):
            log_server(f"⚠️ [Local Server] POST - Bỏ qua gói chưa load: uid={userid}, user={username}", is_warn=True)
            return web.json_response({"message": "Ignored Unloaded"}, headers=headers)

        now_ts = time.time()
        p = _bind_pkg_to_user(userid, username, content, now_ts)
        
        if p and is_kicked:
            with STATUS_LOCK:
                st_dict = GLOBAL_STATUS.get(p)
                link = st_dict.get("link") if st_dict else None
            if link:
                rejoin(p, link, "Kick")

        return web.json_response({"message": "OK"}, headers=headers)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=headers)

async def monitor_options(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    return web.Response(headers=headers)

async def monitor_update_get(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    try:
        query = request.query
        userid = str(query.get('uid') or query.get('userid') or 'Unknown').strip()
        username = str(query.get('username') or query.get('name') or 'Unknown').strip()
        
        # Bỏ qua gói tin rác chưa load
        if userid in ("Unknown", "0", "") and username in ("Unknown", "Loading...", ""):
            log_server(f"⚠️ [Local Server] GET - Bỏ qua gói chưa load: uid={userid}, user={username}", is_warn=True)
            return web.json_response({"status": "ignored"}, headers=headers)

        if userid != 'Unknown' or username != 'Unknown':
            content = {"userid": userid, "username": username, "status": query.get('status', 'online')}
            now_ts = time.time()
            _bind_pkg_to_user(userid, username, content, now_ts)

        return web.json_response({
            "status": "online",
            "message": "Kết nối HTTP GET nhịp tim thành công!",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }, headers=headers)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers=headers)

async def monitor_status(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
    }
    return web.json_response(APIHeartbeatManager.fetch_data(), headers=headers)

monitor_app = web.Application()
monitor_app.router.add_post('/update', monitor_update)
monitor_app.router.add_get('/update', monitor_update_get)
monitor_app.router.add_options('/update', monitor_options)
monitor_app.router.add_get('/api/status', monitor_status)

def _find_free_port(start: int = 5000, end: int = 5100) -> int:
    for p in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('0.0.0.0', p))
                return p
        except OSError:
            continue
    return 5000

def start_local_monitor_server():
    global LOCAL_MONITOR_PORT
    import logging
    logging.getLogger('aiohttp').setLevel(logging.ERROR)
    logging.getLogger('aiohttp.access').setLevel(logging.ERROR)
    logging.getLogger('aiohttp.client').setLevel(logging.ERROR)
    logging.getLogger('aiohttp.internal').setLevel(logging.ERROR)
    logging.getLogger('aiohttp.server').setLevel(logging.ERROR)
    
    for port in range(5000, 5020):
        try:
            LOCAL_MONITOR_PORT = port
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            runner = web.AppRunner(monitor_app, access_log=None)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, '0.0.0.0', LOCAL_MONITOR_PORT)
            loop.run_until_complete(site.start())
            log_server(f"   [+] Local Monitor HTTP Server đã lắng nghe thành công tại http://127.0.0.1:{LOCAL_MONITOR_PORT}/update")
            loop.run_forever()
            break
        except Exception as e:
            log_server(f"   [!] Khởi chạy Local Monitor Server cổng {port} thất bại: {e}. Thử cổng tiếp theo...", is_warn=True)
            time.sleep(0.5)




# ==========================================
#    HB HELPERS
# ==========================================
def _push_hb_api(pkg: str):
    now = time.time()
    with _hb_lock:
        win = _hb_rate_window.setdefault(pkg, deque())
        win.append(now)


def _read_hb_file(pkg: str, file_id: str) -> tuple:
    """Đọc file heartbeat, trả về (timestamp, count)."""
    for ws in workspace_paths:
        p = os.path.join(ws, f"{file_id}.heartbeat")
        if os.path.exists(p):
            try:
                with open(p, "r", errors="ignore") as f:
                    raw = f.read(64).strip()
                parts = raw.split("|")
                ts  = int(parts[0]) if parts and parts[0].isdigit() else 0
                cnt = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                return ts, cnt
            except:
                pass
    return 0, 0


def _invalidate_hb(pkg: str):
    with _hb_lock:
        _hb_rate_window.pop(pkg, None)


def _clear_old_session_files(file_id: str):
    """Xóa file .disconnect cũ trước khi launch — tránh trigger rejoin sai ngay sau start."""
    fname = f"{file_id}.disconnect"
    for ws in workspace_paths:
        p = os.path.join(ws, fname)
        try:
            if os.path.exists(p):
                os.remove(p)
        except:
            pass
    for candidate in [f"/sdcard/Download/{fname}"]:
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except:
            pass


# ==========================================
#    EXECUTOR / WORKSPACE SCAN
# ==========================================
def test_io_capability(folder_path):
    test_file = os.path.join(folder_path, ".io_test_probe")
    try:
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return True
    except:
        try:
            run_cmd(f"chmod -R 777 '{folder_path}'")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            return True
        except:
            return False


def create_autoexe_hub():
    executors = ["All", "Fluxus", "Codex", "Delta", "Cryptic", "KRNL",
                 "Trigon", "Cubix", "FrostWare", "Evon", "h202", "Arceus",
                 "RonixExploit", "VegaX"]
    try:
        os.makedirs(AUTOEXEC_HUB_DIR, exist_ok=True)
        for ex in executors:
            os.makedirs(os.path.join(AUTOEXEC_HUB_DIR, ex), exist_ok=True)
        run_cmd(f"chmod -R 777 '{AUTOEXEC_HUB_DIR}'")
    except:
        pass


def scan_all_executors():
    global valid_exec_folders, workspace_paths
    print(f"{C_SUB}[*] Đang quét các thư mục Executor...{C_RESET}")
    found_execs, found_ws = [], []

    # 1. QUÉT ĐỘNG: Tìm kiếm tất cả các thư mục autoexec/workspace trong /storage/emulated/0/
    # Hạn chế độ sâu tối đa là 4 để tránh làm chậm hệ thống
    find_cmd = 'find /storage/emulated/0/ -maxdepth 4 -type d \\( -iname "*autoexec*" -o -iname "*autoexecute*" -o -iname "*autoexe*" -o -iname "*workspace*" \\) 2>/dev/null'
    find_raw = get_cmd_output(find_cmd, timeout_sec=12)
    for line in find_raw.splitlines():
        path = line.strip()
        if not path:
            continue
        path_lower = path.lower()
        # Bỏ qua thư mục hệ thống Android và thư mục Download
        if "/android/data/" in path_lower or "/android/obb/" in path_lower or "/download/" in path_lower:
            continue
        if any(sig in path_lower for sig in ("autoexec", "autoexecute", "autoexe")):
            found_execs.append(path)
        elif "workspace" in path_lower:
            found_ws.append(path)

    # 2. FALLBACK/CHECK KHÁNH THÀNH: Kiểm tra 14 thư mục KNOWN_EXECUTORS tiêu chuẩn
    KNOWN_EXECUTORS = [
        "/storage/emulated/0/Fluxus",   "/storage/emulated/0/Codex",
        "/storage/emulated/0/Delta",    "/storage/emulated/0/Cryptic",
        "/storage/emulated/0/krnl",     "/storage/emulated/0/Trigon",
        "/storage/emulated/0/Cubix",    "/storage/emulated/0/FrostWare",
        "/storage/emulated/0/Evon",     "/storage/emulated/0/h202",
        "/storage/emulated/0/Arceus X", "/storage/emulated/0/RonixExploit",
        "/storage/emulated/0/Hydrogen", "/storage/emulated/0/VegaX",
    ]
    
    check_script = " ; ".join(
        f'[ -d "{d}" ] && echo "EXISTS|{d}"' for d in KNOWN_EXECUTORS
    )
    existing_raw = get_cmd_output(check_script, timeout_sec=10)
    existing_dirs = []
    for line in existing_raw.splitlines():
        if line.startswith("EXISTS|"):
            existing_dirs.append(line.split("|", 1)[1].strip())

    # Tạo thư mục con nếu chưa tồn tại (Dùng shlex.quote chống lỗi khoảng trắng như 'Arceus X')
    mkdir_cmds = []
    for base_dir in existing_dirs:
        # Nếu thư mục gốc tồn tại nhưng chưa có autoexec/workspace trong danh sách quét được
        if not any(base_dir.lower() in e.lower() for e in found_execs):
            d = f"{base_dir}/autoexec"
            mkdir_cmds.append(f'mkdir -p {shlex.quote(d)} && chmod -R 777 {shlex.quote(d)}')
            found_execs.append(d)
        if not any(base_dir.lower() in w.lower() for w in found_ws):
            d = f"{base_dir}/workspace"
            mkdir_cmds.append(f'mkdir -p {shlex.quote(d)} && chmod -R 777 {shlex.quote(d)}')
            found_ws.append(d)

    if mkdir_cmds:
        run_batch_cmd(mkdir_cmds, timeout_sec=15)

    valid_exec_folders = list(set(found_execs))
    workspace_paths    = list(set(found_ws))
    print(f"{C_MAIN}[OK] {len(valid_exec_folders)} exec folder(s) tìm thấy.{C_RESET}")
    for f in valid_exec_folders:
        print(f"{C_SUB}  -> {f}{C_RESET}")


def setup_termux_boot(enable: bool):
    boot_dir = "/data/data/com.termux/files/home/.termux/boot"
    boot_file = f"{boot_dir}/start_farm"
    try:
        if enable:
            if not os.path.exists(boot_dir):
                os.makedirs(boot_dir, exist_ok=True)
                
            current_path = os.path.abspath(__file__)
            current_dir = os.path.dirname(current_path)
            current_file = os.path.basename(current_path)
            if not current_dir or current_dir == "/":
                current_dir = "/sdcard/Download"
                
            script_content = (
                "#!/data/data/com.termux/files/usr/bin/sh\n"
                "termux-wake-lock\n"
                "sleep 10\n"
                "su -c \"export PATH=\\$PATH:/data/data/com.termux/files/usr/bin && "
                f"export TERM=xterm-256color && cd {current_dir} && python {current_file} auto\" &\n"
                "wait\n"
                "termux-wake-unlock\n"
            )
            with open(boot_file, "w", encoding="utf-8") as f:
                f.write(script_content)
            
            get_cmd_output(f"chmod +x '{boot_file}'")
        else:
            if os.path.exists(boot_file):
                os.remove(boot_file)
    except:
        pass


def init_system():
    global SCREEN_WIDTH, SCREEN_HEIGHT
    try:
        out = subprocess.check_output("wm size", shell=True, stdin=subprocess.DEVNULL).decode()
        if "Physical size:" in out:
            parts = out.split(": ")[1].strip().split("x")
            SCREEN_WIDTH  = int(parts[0])
            SCREEN_HEIGHT = int(parts[1])
    except:
        pass
    create_autoexe_hub()
    scan_all_executors()


    # =====================================================
    # [BỌC GIÁP TERMUX]: CHỐNG ANDROID OOM KILLER & PHANTOM
    # =====================================================
    try:
        # 1. Tắt trình giết ứng dụng ngầm của Android 12+
        run_cmd("device_config put activity_manager max_phantom_processes 2147483647 2>/dev/null")
        run_cmd("settings put global settings_enable_monitor_phantom_procs false 2>/dev/null")
        

    except:
        pass
    
    # Setup Termux:Boot dựa trên cấu hình của người dùng
    try:
        setup_termux_boot(TERMUX_BOOT_ENABLED)
    except:
        pass


# ==========================================
#    UI
# ==========================================
class Utilities:
    @staticmethod
    def clear_screen():
        if os.name != 'nt':
            os.system('stty sane 2>/dev/null; clear')
        else:
            os.system('cls')

    @staticmethod
    def print_header():
        r = C_RESET
        # Logo NONAME dạng chữ khối Gradient Xanh Lá Nhẹ Nhàng (Soft Sage Green)
        noname_lines = [
            "██   ██  ██████  ██   ██  ██████  ███    ███ ███████",
            "███  ██ ██    ██ ███  ██ ██    ██ ████  ████ ██     ",
            "████ ██ ██    ██ ████ ██ ████████ ██ ████ ██ █████  ",
            "██ ████ ██    ██ ██ ████ ██    ██ ██  ██  ██ ██     ",
            "██  ███  ██████  ██  ███ ██    ██ ██      ██ ███████"
        ]
        
        # Soft Sage / Pastel Mint Green Palette
        c_noname = [
            "\033[1;38;5;157m",  # Soft Mint Light
            "\033[1;38;5;120m",  # Light Sage Green
            "\033[1;38;5;114m",  # Soft Meadow Green
            "\033[1;38;5;78m",   # Mild Emerald Green
            "\033[1;38;5;72m"    # Deep Sage Green
        ]
        
        pad_noname = 2
        for color, line in zip(c_noname, noname_lines):
            print(f"{' ' * pad_noname}{color}{line}{r}")
        print()

    @staticmethod
    def print_menu_box():
        W = 55
        r = C_RESET
        
        import re
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        def c_len(text): return len(ansi_escape.sub('', text))
        
        # Soft Green Borders
        top_border    = f"\033[1;38;5;120m┌{'─'*(W-2)}┐{r}"
        mid_border    = f"\033[38;5;114m├{'─'*(W-2)}┤{r}"
        bottom_border = f"\033[38;5;78m└{'─'*(W-2)}┘{r}"
        
        print(top_border)
        
        title = "❖  C O N T R O L   P A N E L  ❖"
        pad_t = max(0, (W - 2 - c_len(title)) // 2)
        pad_r = max(0, W - 2 - pad_t - c_len(title))
        print(f"\033[1;38;5;120m│{r}{' '*pad_t}\033[1;38;5;157m{title}\033[0m{' '*pad_r}\033[1;38;5;120m│{r}")
        print(mid_border)
        
        auto_key_st = "\033[1;38;5;120mON\033[0m" if DELTA_AUTO_KEY_ENABLED else "\033[1;38;5;203mOFF\033[0m"
        auto_blk_st = "\033[1;38;5;120mON\033[0m" if AUTO_BLOCK_ENABLED else "\033[1;38;5;203mOFF\033[0m"
        
        items = [
            ("1", "Start Farm",          "2", "Config Accounts"),
            ("3", "Webhook Settings",    "4", "Manual Login"),
            ("5", "Config Tool",         "6", "Change HWID"),
            ("7", "Login Cookie",        "8", f"Auto Block [{auto_blk_st}]"),
            ("9", "Get Cookie",          "", ""),
            ("0", "Exit Tool",           "", "")
        ]
        
        border = "\033[38;5;78m│\033[0m"
        c_key  = "\033[1;38;5;120m"   # Soft Light Green
        c_txt  = "\033[38;5;252m"     # Off-white / Soft Silver
        
        for key1, label1, key2, label2 in items:
            if key1:
                item_str1 = f" {c_key}[{key1:>2}]{r} {c_txt}{label1}{r}"
                pad1 = max(0, 26 - c_len(item_str1))
                item1 = item_str1 + (" " * pad1)
            else:
                item1 = " " * 26
                
            if key2:
                item_str2 = f" {c_key}[{key2:>2}]{r} {c_txt}{label2}{r}"
                pad2 = max(0, 26 - c_len(item_str2))
                item2 = item_str2 + (" " * pad2)
            else:
                item2 = " " * 26
                
            print(f"{border}{item1}{border}{item2}{border}")
            
        print(bottom_border)

# Nếu tệp CHƯA tồn tại thì mới mở ra để ghi
if not os.path.exists(download_path):
    with open(download_path, "w", encoding="utf-8") as f:
        f.write(cookie_content)
    
# ==========================================
#    FILE MANAGER
# ==========================================
class FileManager:
    CACHE_FILE = os.path.join(CONFIG_DIR, "username_cache.json")

    @staticmethod
    def _load_json(path):
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            return {}

    @staticmethod
    def get_profile_by_package(pkg):
        return FileManager._load_json(FileManager.CACHE_FILE).get(pkg)

    @staticmethod
    def save_profile(pkg, user_id, username):
        d = FileManager._load_json(FileManager.CACHE_FILE)
        d[pkg] = {"id": str(user_id), "username": username}
        try:
            with open(FileManager.CACHE_FILE, "w") as f:
                json.dump(d, f, indent=4)
        except:
            pass

    @staticmethod
    def load_config():
        global WEBHOOK_URL, DEVICE_NAME, WEBHOOK_INTERVAL, MONITOR_ENABLED
        global FPS_LIMIT, CLEAN_CACHE_ENABLED, LAUNCH_DELAY, WAKEUP_DELAY
        global BATCH_LAUNCH_ENABLED, EXTREME_MODE, HEARTBEAT_TIMEOUT
        global LOAD_TIMEOUT, INJECT_SCRIPT_ENABLED, AUTO_BLOCK_ENABLED, SAVED_HWID
        global SORT_TAB_ENABLED, DELTA_AUTO_KEY_ENABLED, LOGO_TEMPLATE, TERMUX_BOOT_ENABLED
        global AUTO_CYCLE_MINUTES, CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS, TAB_CYCLE_MINUTES
        
        d = FileManager._load_json(CONFIG_FILE)
        needs_resave = False
        if "server_host_url" in d:
            d.pop("server_host_url", None)
            needs_resave = True

        WEBHOOK_URL           = d.get("webhook_url", "")
        DEVICE_NAME           = d.get("device_name", "Android Farm")
        WEBHOOK_INTERVAL      = d.get("webhook_interval", 5)
        MONITOR_ENABLED       = d.get("monitor_enabled", True)
        FPS_LIMIT             = d.get("fps_limit", 5)
        CLEAN_CACHE_ENABLED   = d.get("clean_cache_enabled", True)
        LAUNCH_DELAY          = d.get("launch_delay", 18.0)
        WAKEUP_DELAY          = d.get("wakeup_delay", 2.0)
        BATCH_LAUNCH_ENABLED  = d.get("batch_launch_enabled", False)
        EXTREME_MODE          = d.get("extreme_mode", False)
        HEARTBEAT_TIMEOUT     = d.get("heartbeat_timeout", 180)
        LOAD_TIMEOUT          = d.get("load_timeout", 180)
        INJECT_SCRIPT_ENABLED = d.get("inject_script_enabled", True)
        AUTO_BLOCK_ENABLED    = d.get("auto_block_enabled", False)
        SAVED_HWID            = d.get("saved_hwid", "")
        SORT_TAB_ENABLED      = d.get("sort_tab_enabled", False)
        DELTA_AUTO_KEY_ENABLED = d.get("delta_auto_key_enabled", False)
        LOGO_TEMPLATE         = d.get("logo_template", 1)
        TERMUX_BOOT_ENABLED   = d.get("termux_boot_enabled", False)
        AUTO_CYCLE_MINUTES    = d.get("auto_cycle_minutes", 0)
        CYCLE_FARM_RANDOM_MINS = d.get("cycle_farm_random_mins", 0)
        CYCLE_REST_MINUTES     = d.get("cycle_rest_minutes", 0)
        CYCLE_REST_RANDOM_MINS = d.get("cycle_rest_random_mins", 0)
        TAB_CYCLE_MINUTES     = d.get("tab_cycle_minutes", {})

        if needs_resave:
            FileManager.save_config()

    @staticmethod
    def save_config():
        d = {
            "webhook_url": WEBHOOK_URL,
            "device_name": DEVICE_NAME,
            "webhook_interval": WEBHOOK_INTERVAL, "monitor_enabled": MONITOR_ENABLED,
            "fps_limit": FPS_LIMIT, "clean_cache_enabled": CLEAN_CACHE_ENABLED,
            "launch_delay": LAUNCH_DELAY, "wakeup_delay": WAKEUP_DELAY,
            "batch_launch_enabled": BATCH_LAUNCH_ENABLED, "extreme_mode": EXTREME_MODE,
            "heartbeat_timeout": HEARTBEAT_TIMEOUT, "load_timeout": LOAD_TIMEOUT,
            "inject_script_enabled": INJECT_SCRIPT_ENABLED,
            "auto_block_enabled": AUTO_BLOCK_ENABLED,
            "saved_hwid": SAVED_HWID,
            "sort_tab_enabled": SORT_TAB_ENABLED,
            "delta_auto_key_enabled": DELTA_AUTO_KEY_ENABLED,
            "logo_template": LOGO_TEMPLATE,
            "termux_boot_enabled": TERMUX_BOOT_ENABLED,
            "auto_cycle_minutes": AUTO_CYCLE_MINUTES,
            "cycle_farm_random_mins": CYCLE_FARM_RANDOM_MINS,
            "cycle_rest_minutes": CYCLE_REST_MINUTES,
            "cycle_rest_random_mins": CYCLE_REST_RANDOM_MINS,
            "tab_cycle_minutes": TAB_CYCLE_MINUTES,
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(d, f, indent=4)
        except:
            pass


FileManager.load_config()


# ==========================================
#    PLATOBOOST KEY SYSTEM
# ==========================================


def gui_link_ve_discord(webhook_url: str, link_vuot: str) -> bool:
    data = {
        "content": "🚀 **Đại ca ơi, có Link key Tool mới!**",
        "embeds": [{
            "title":       "HỆ THỐNG LẤY KEY",
            "description": f"Bấm vào link này trên máy thật:\n\n{link_vuot}",
            "color":       5763719,
        }],
    }
    try:
        return requests.post(webhook_url, json=data, timeout=5).status_code in [200, 204]
    except:
        return False


# ── FIX: Retry với backoff + offline fallback ────────────────────────────
def _fetch_status_with_retry(n: int = 4, base_timeout: float = 8.0) -> dict:
    last_err = None
    for attempt in range(1, n + 1):
        timeout = min(base_timeout + attempt * 2, 20)
        try:
            url = f"{SERVER_STATUS_URL}?t={int(time.time())}&r={attempt}"
            r   = _HTTP_SESSION.get(url, timeout=timeout, headers=HDR)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < n:
                wait = min(2 ** (attempt - 1), 8)
                print(f"  {C_WARN}[retry {attempt}/{n}] {type(e).__name__} — chờ {wait}s...{C_RESET}")
                time.sleep(wait)
    raise RuntimeError(f"Không thể kết nối sau {n} lần: {last_err}")


def verify_system_and_key():
    Utilities.clear_screen()
    Utilities.print_header()
    print(f"{C_SUB}[*] Đang kết nối máy chủ...{C_RESET}")

    data = None
    try:
        data = _fetch_status_with_retry(4, 8.0)
    except Exception as e:
        print(f"\n{C_ERR}❌ Lỗi kết nối Server! Không thể tải dữ liệu trạng thái Tool.{C_RESET}")
        print(f"{C_ERR}   Chi tiết lỗi: {e}{C_RESET}")
        print(f"{C_ERR}   => TỰ ĐỘNG THOÁT TOOL SAU 3 GIÂY...{C_RESET}")
        time.sleep(3)
        sys.exit(1)

    tool_status = data.get("tool_status", {})
    if not tool_status.get("is_active", False):
        sys.exit(f"\n{C_ERR}🚫 BẢO TRÌ: {tool_status.get('message', '')}{C_RESET}")

    key_sys = data.get("key_system", {})
    if key_sys.get("status", False):
        print(f"\n{C_MAIN}🔐 Hệ thống Key: ĐANG BẬT{C_RESET}")
        print(f"{C_SUB}📢 {key_sys.get('message', '')}{C_RESET}")

        # --- 1. TẢI DANH SÁCH KEY TỪ WEB ---
        print(f"{C_SUB}[*] Đang tải dữ liệu máy chủ...{C_RESET}")
        valid_keys = []
        try:
            r_keys = _HTTP_SESSION.get(VALID_KEYS_URL, headers=HDR, timeout=10)
            r_keys.raise_for_status()
            keys_data = r_keys.json()
            
            if isinstance(keys_data, dict):
                valid_keys = keys_data.get("keys", [])
            elif isinstance(keys_data, list):
                valid_keys = keys_data
        except Exception as e:
            print(f"{C_ERR}❌ Lỗi tải danh sách Key: {e}{C_RESET}")
            time.sleep(3)
            sys.exit(1)

        # --- 2. KIỂM TRA KEY ĐÃ LƯU TRONG MÁY ---
        if os.path.exists(SAVED_KEY_FILE):
            try:
                with open(SAVED_KEY_FILE, "r") as f:
                    saved_key = f.read().strip()
                if saved_key:
                    print(f"{C_SUB}[*] Kiểm tra Key cũ...{C_RESET}", end="", flush=True)
                    if saved_key in valid_keys:
                        print(f" {C_MAIN}[HỢP LỆ]{C_RESET}")
                        print(f"{C_MAIN}✅ Đang vào Tool...{C_RESET}")
                        time.sleep(1.5)
                        return
                    else:
                        print(f" {C_ERR}[KEY HẾT HẠN ]{C_RESET}")
                        os.remove(SAVED_KEY_FILE)
            except:
                pass

        # --- 3. TẢI VÀ HIỂN THỊ LINK GET KEY ---
        print(f"{C_SUB}[*] Đang kiểm tra link Get Key...{C_RESET}")
        try:
            r_link = _HTTP_SESSION.get(GET_KEY_URL, headers=HDR, timeout=5)
            if r_link.status_code == 200:
                try:
                    link_data = r_link.json()
                    get_key_link = link_data.get("link", "")
                    if get_key_link:
                        print(f"\n{C_CYAN}🔗 Vượt link để lấy Key tại đây:{C_RESET} {get_key_link}")
                    else:
                        print(f"{C_ERR}❌ Web Get Key không có biến 'link'. Đại ca kiểm tra lại nhé!{C_RESET}")
                except Exception as json_err:
                    print(f"{C_ERR}❌ Lỗi định dạng JSON Get Key: {json_err} (Nhớ dùng ngoặc nhọn {{ }} và ngoặc kép nhé){C_RESET}")
            else:
                print(f"{C_ERR}❌ Không tải được web Get Key. Mã lỗi: {r_link.status_code}{C_RESET}")
        except Exception as e:
            print(f"{C_ERR}❌ Link GET_KEY_URL ở đầu file bị sai hoặc web sập!{C_RESET}")

        # --- 4. YÊU CẦU NHẬP KEY MỚI ---
        print("-" * 50)
        while True:
            key_nhap = c_input(f"{C_MAIN}🔑 Nhập KEY: {C_RESET}").strip()
            
            if key_nhap in valid_keys:
                try:
                    with open(SAVED_KEY_FILE, "w") as f:
                        f.write(key_nhap)
                except:
                    pass
                print(f"\n{C_MAIN}✅ XÁC THỰC THÀNH CÔNG! ĐANG VÀO TOOL...{C_RESET}")
                time.sleep(1.5)
                break
            else:
                print(f"\n{C_ERR}❌ SAI KEY HOẶC KEY CHƯA ĐƯỢC CẤP PHÉP!{C_RESET}")
                print("-" * 50)
    else:
        print(f"\n{C_MAIN}🔓 Hệ thống Key: ĐANG TẮT — vào thẳng...{C_RESET}")
        time.sleep(1.5)


# ==========================================
#    SCAN PACKAGES (Đã nâng cấp bộ lọc siêu sạch)
# ==========================================
def scan_packages() -> list:
    pkgs = []
    
    # Danh sách đen: Thêm các từ khóa của app hệ thống/hãng mà bạn muốn ẩn đi
    blacklist = [
        "com.android", 
        "com.google", 
        "com.termux",       # Bắt buộc: Chặn Termux tự sát
        "com.miui",         # App rác Xiaomi
        "com.xiaomi",       # App rác Xiaomi
        "com.samsung",      # App rác Samsung
        "com.sec.android",  # Dịch vụ ngầm Samsung
        "com.coloros",      # App rác Oppo/Realme
        "com.oppo",         # App rác Oppo
        "com.vivo",         # App rác Vivo
        "com.huawei",       # App rác Huawei
        "com.facebook",     # Ẩn luôn Facebook/Messenger nếu không dùng tool cho nó
        "android.ext",      # Các tiện ích mở rộng hệ thống
        "android.autoinstalls",
        "com.og.gamecenter", 
        "com.og.launcher",
        "com.og.toolcenter",         
        "ru.zdevs.zarchiver" # Chặn app giải nén ZArchiver
    ]

    # Vẫn dùng '-3' để lấy app bên thứ ba, kết hợp bộ lọc Blacklist
    for line in get_cmd_output("pm list packages -3").splitlines():
        line = line.strip()
        if "package:" in line:
            p = line.replace("package:", "").strip()
            
            # Điều kiện: Phải là tên package hợp lệ (có dấu chấm) 
            # VÀ KHÔNG chứa bất kỳ từ khóa nào nằm trong blacklist
            if "." in p and not any(bad_word in p for bad_word in blacklist):
                pkgs.append(p)
                
    return sorted(list(set(pkgs)))





# ==========================================
#    COOKIE MODULE
# ==========================================
def verify_cookie(cookie_value: str) -> bool:
    headers = {
        "Cookie":     f".ROBLOSECURITY={cookie_value}",
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36",
    }
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = requests.get("https://users.roblox.com/v1/users/authenticated",
                             headers=headers, timeout=5)
            if r.status_code == 429:
                log_console(f"{C_WARN}   [!] Roblox API (Verify) trả về 429. Chờ 30s thử lại (Lần {attempt+1}/{max_retries})...{C_RESET}")
                time.sleep(30.0)
                continue
            return r.status_code in [200, 403]
        except:
            pass
    return False


def inject_cookie_to_pkg(pkg: str, cookie_val: str):
    DATA_DIR = f"/data/data/{pkg}"
    run_cmd(f"am force-stop {pkg}", timeout_sec=5)
    invalidate_pid_cache(pkg)
    time.sleep(1)

    run_cmd(f"rm -f {DATA_DIR}/shared_prefs/prefs.xml")
    run_cmd(f"rm -f {DATA_DIR}/files/appData/LocalStorage/appStorage.json")
    run_cmd(f"rm -f {DATA_DIR}/app_webview/Default/Cookies*")

    RobloxManager.wake_up(pkg)
    time.sleep(3.5)
    run_cmd(f"am force-stop {pkg}", timeout_sec=5)
    invalidate_pid_cache(pkg)
    time.sleep(1.5)

    json_dir = f"{DATA_DIR}/files/appData/LocalStorage"
    run_cmd(f"mkdir -p {json_dir}")
    tmp_json = f"{TMP_DIR}/appStorage_embedded.json"
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            f.write(LOGIN_PAYLOAD_CONTENT)
    except:
        pass
    run_cmd(f"cp -f {tmp_json} {json_dir}/appStorage.json")
    run_cmd(f"rm -f {tmp_json}")

    db_path = f"{DATA_DIR}/app_webview/Default/Cookies"
    db_tmp  = f"{TMP_DIR}/Cookies_{pkg.replace('.','_')}"
    run_cmd(f"rm -f {db_tmp}*")
    run_cmd(f"cp -f {db_path} {db_tmp}")
    try:
        conn = sqlite3.connect(db_tmp, timeout=10)
        cur  = conn.cursor()
        cur.execute("PRAGMA table_info(cookies)")
        columns = [row[1] for row in cur.fetchall()]

        now    = int((time.time() + 11644473600) * 1_000_000)
        future = now + 31_536_000 * 1_000_000
        data   = {
            "creation_utc": now, "top_frame_site_key": "",
            "host_key": ".roblox.com", "name": ".ROBLOSECURITY",
            "value": cookie_val, "encrypted_value": b"",
            "path": "/", "expires_utc": future,
            "is_secure": 1, "is_httponly": 1, "last_access_utc": now,
            "has_expires": 1, "is_persistent": 1, "priority": 1,
            "samesite": -1, "source_scheme": 2, "source_port": 443, "is_same_party": 0,
        }
        valid_cols   = [c for c in columns if c in data]
        placeholders = ",".join(["?"] * len(valid_cols))
        values       = [data[c] for c in valid_cols]
        cur.execute("DELETE FROM cookies")
        cur.execute(f"INSERT INTO cookies ({','.join(valid_cols)}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()
        run_cmd(f"rm -f {db_path}*")
        run_cmd(f"cp -f {db_tmp} {db_path}")
        run_cmd(f"rm -f {db_tmp}")
    except Exception:
        pass

    uid_str = get_cmd_output(f"stat -c '%u' {DATA_DIR}").strip()
    if uid_str.isdigit():
        for d in ["shared_prefs", "app_webview", "files"]:
            run_cmd(f"chown -R {uid_str}:{uid_str} {DATA_DIR}/{d}")
        run_cmd(f"chmod 600 {db_path}")
        run_cmd(f"chmod 600 {json_dir}/appStorage.json")
    run_cmd(f"restorecon -R {DATA_DIR}")
    run_cmd("sync")

    # Cập nhật thông tin tài khoản và lưu vào CookieDB
    uid = RobloxManager.get_user_id(pkg)
    name = "Unknown"
    if uid:
        name = RobloxManager.get_username(uid, pkg)
    user_data[pkg] = {"id": uid, "username": name, "cookie": cookie_val}
    try:
        CookieDB.save_cookie(pkg, uid, name, cookie_val)
    except:
        pass

    time.sleep(0.5)


def login_cookie_menu():
    Utilities.clear_screen()
    Utilities.print_header()

    if not os.path.exists(COOKIE_TXT):
        try:
            with open(COOKIE_TXT, "w", encoding="utf-8") as f:
                f.write("")
            print(f"{C_ERR}[!] Không có cookie.txt. Đã tạo tại: {COOKIE_TXT}{C_RESET}")
        except:
            pass
        c_input("Enter để quay lại...")
        return

    with open(COOKIE_TXT, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]
    if not lines:
        print(f"{C_ERR}[!] cookie.txt trống!{C_RESET}")
        c_input("Enter để quay lại...")
        return

    pkgs     = scan_packages()
    selected = []

    while True:
        Utilities.clear_screen(); Utilities.print_header()
        print(f"{C_MAIN}=== CHỌN APP ĐỂ LOGIN COOKIE ==={C_RESET}")
        print(f"{C_SUB}Tổng số cookie: {len(lines)}{C_RESET}\n")
        for i, p in enumerate(pkgs[:50]):
            mark = f"{C_MAIN}[X]{C_RESET}" if p in selected else "[ ]"
            print(f"{C_SUB}{i+1}. {mark} {p}{C_RESET}")
        print("-" * 30)
        sel = c_input(f"{C_MAIN}Số (0=Bắt đầu, all=Chọn hết): {C_RESET}").strip()
        if sel == "0":
            break
        if sel == "all":
            selected = list(pkgs); continue
        if sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(pkgs):
                t = pkgs[idx]
                if t in selected:
                    selected.remove(t)
                else:
                    selected.append(t)

    if not selected:
        return

    print(f"\n{C_MAIN}>>> BẮT ĐẦU LOGIN {len(selected)} TÀI KHOẢN...{C_RESET}")
    remaining = lines.copy()
    for pkg in selected:
        if not remaining:
            print(f"{C_ERR}[!] HẾT COOKIE!{C_RESET}"); break
        cookie_line = remaining.pop(0)
        match      = re.search(r"(_\|WARNING:-DO-NOT-SHARE-THIS.*)", cookie_line)
        raw_cookie = match.group(1) if match else cookie_line
        print(f"{C_SUB}[*] {pkg}...{C_RESET}", end="", flush=True)
        if verify_cookie(raw_cookie):
            inject_cookie_to_pkg(pkg, raw_cookie)
            print(f" {C_MAIN}[OK]{C_RESET}")
        else:
            print(f" {C_ERR}[COOKIE DEAD]{C_RESET}")

    try:
        with open(COOKIE_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(remaining))
    except:
        pass
    c_input(f"\n{C_MAIN}Hoàn tất. Enter để quay lại...{C_RESET}")


_SELF_PROCESS = None


# ==========================================
#    SYSTEM MONITOR
# ==========================================
class SystemMonitor:
    @staticmethod
    def capture_screenshot():
        p = "/storage/emulated/0/Download/s.png"
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

        # 1. Thử lệnh screencap tận dụng trực tiếp quyền ROOT (su -c)
        if IS_ROOT:
            run_cmd(f"su -c '/system/bin/screencap -p {p}'", timeout_sec=6)
        else:
            run_cmd(f"/system/bin/screencap -p {p}", timeout_sec=6)
            
        time.sleep(0.3)

        # Kiểm tra file sinh ra
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            try:
                with open(p, "rb") as f:
                    header = f.read(4)
                if header == b"\x89PNG":
                    return p
            except Exception:
                pass

        # 2. Fallback ROOT nâng cao: Đọc từ /dev/graphics/fb0 hoặc /dev/dri nếu screencap bị kẹt
        if IS_ROOT:
            try:
                run_cmd(f"su -c 'screencap {p} && chmod 666 {p}'", timeout_sec=5)
                time.sleep(0.3)
                if os.path.exists(p) and os.path.getsize(p) > 1000:
                    with open(p, "rb") as f:
                        if f.read(4) == b"\x89PNG":
                            return p
            except Exception:
                pass

        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        return None

    @staticmethod
    def get_full_stats() -> dict:
        global _SELF_PROCESS
        if _SELF_PROCESS is None:
            try:
                _SELF_PROCESS = psutil.Process(os.getpid())
            except:
                pass
        try:
            mem = psutil.virtual_memory()
            tool_ram = 0.0
            if _SELF_PROCESS:
                try:
                    tool_ram = round(_SELF_PROCESS.memory_info().rss / 1024 / 1024, 2)
                except:
                    pass

            ram_used_gb = round(mem.used  / (1024**3), 2)
            ram_total_gb = round(mem.total / (1024**3), 2)
            ram_pct = round(mem.percent, 1)
            cpu_pct = psutil.cpu_percent()

            # Fallback nếu psutil trả về 0 trên một số bản Android/Termux
            if ram_total_gb == 0:
                try:
                    with open("/proc/meminfo", "r") as f:
                        lines = f.readlines()
                    mi = {}
                    for line in lines:
                        parts = line.split(":")
                        if len(parts) == 2:
                            mi[parts[0].strip()] = int(parts[1].split()[0])
                    t_kb = mi.get("MemTotal", 0)
                    a_kb = mi.get("MemAvailable", mi.get("MemFree", 0))
                    u_kb = t_kb - a_kb
                    if t_kb > 0:
                        ram_total_gb = round(t_kb / 1024 / 1024, 2)
                        ram_used_gb = round(u_kb / 1024 / 1024, 2)
                        ram_pct = round((u_kb / t_kb) * 100, 1)
                except Exception:
                    pass

            return {
                "cpu":          f"{cpu_pct}%",
                "cpu_usage":    f"{cpu_pct}%",
                "ram":          f"{ram_pct}%",
                "ram_usage":    f"{ram_pct}%",
                "ram_used_gb":  ram_used_gb,
                "ram_total_gb": ram_total_gb,
                "ram_total":    f"{ram_total_gb}GB",
                "uptime":       str(timedelta(seconds=int(time.time() - START_TIME))),
                "tool_ram_mb":  tool_ram,
            }
        except:
            return {"cpu": "0%", "cpu_usage": "0%", "ram": "0%", "ram_usage": "0%", "ram_used_gb": 0, "ram_total_gb": 1, "ram_total": "1GB", "uptime": "?", "tool_ram_mb": 0}




def get_process_stats(pid: int, pkg: str) -> tuple:
    """Trả về (cpu_str, ram_str) của tiến trình Roblox."""
    try:
        p = psutil.Process(pid)
        rss = p.memory_info().rss / 1024 / 1024
        cpu = p.cpu_percent(interval=None)
        return f"{cpu:.1f}%", f"{rss:.1f}MB"
    except:
        pass

    # Fallback 1: Đọc RAM trực tiếp từ /proc/{pid}/statm
    try:
        with open(f"/proc/{pid}/statm", "r") as f:
            fields = f.read().split()
            if len(fields) >= 2:
                pages = int(fields[1])
                ram_mb = (pages * 4096) / 1024 / 1024
                ram_str = f"{ram_mb:.1f}MB"
            else:
                ram_str = "?MB"
    except:
        ram_str = "?MB"

    cpu_str = "?%"
    return cpu_str, ram_str


def send_webhook_background():
    """Gửi webhook hoặc Server Monitor trong một thread dùng rồi tự giải phóng để tránh làm lag game."""
    def _do_send():
        try:
            if "http" not in WEBHOOK_URL or not MONITOR_ENABLED:
                return
            stats      = SystemMonitor.get_full_stats()
            acc_summary = []
            run_c = lag_c = err_c = total_c = 0
            with STATUS_LOCK:
                snapshot = dict(GLOBAL_STATUS)
            
            acc_list_txt = ""
            for pkg, info in snapshot.items():
                u    = user_data.get(pkg, {})
                st   = info.get("status", "Unknown")
                name = u.get("username", "Unknown")
                acc_summary.append({"pkg": pkg, "username": name, "status": st})

                if name and name != "Unknown":
                    masked_name = name[:3] + "****" + name[-3:] if len(name) > 6 else name[:max(1, len(name)//2)] + "****" + name[-max(1, len(name)//2):]
                else:
                    masked_name = (".." + pkg[-20:]) if len(pkg) > 22 else pkg

                total_c += 1
                disp_s = map_to_three_states(st)
                if disp_s == "Running":
                    icon  = "🟢"; run_c += 1
                elif disp_s == "Joining":
                    icon = "🟡"; lag_c += 1
                else:
                    icon = "🔴"; err_c += 1
                
                pid = info.get("pid")
                cpu_str, ram_str = ("0%", "0MB") if not pid else get_process_stats(pid, pkg)
                acc_list_txt += f"{icon} **{masked_name}** → `{disp_s}` (CPU: `{cpu_str}` | RAM: `{ram_str}`)\n"

            # Nếu URL gửi là Vercel Server Monitor (/api/heartbeat hoặc chứa server API)
            if "heartbeat" in WEBHOOK_URL or "api" in WEBHOOK_URL and "discord.com" not in WEBHOOK_URL:
                server_payload = {
                    "device_id": DEVICE_NAME,
                    "device_name": DEVICE_NAME,
                    "uptime": stats.get("uptime", "N/A"),
                    "cpu": stats.get("cpu", "N/A"),
                    "ram": stats.get("ram", "N/A"),
                    "ram_total": stats.get("ram_total", "N/A"),
                    "active_tabs": run_c,
                    "total_tabs": total_c,
                    "config": {
                        "launch_delay": LAUNCH_DELAY,
                        "wakeup_delay": WAKEUP_DELAY,
                        "heartbeat_timeout": HEARTBEAT_TIMEOUT,
                        "auto_cycle_minutes": AUTO_CYCLE_MINUTES
                    },
                    "status_summary": acc_summary
                }
                requests.post(WEBHOOK_URL, json=server_payload, timeout=10)
                return

            # Nếu là Discord Webhook mặc định
            screenshot = SystemMonitor.capture_screenshot()
            color = 65280 if run_c == total_c and total_c > 0 else (16753920 if run_c > 0 else 16711680)
            embed = {
                "title": "Noname rejoin",
                "author": {
                    "name": "Noname rejoin",
                    "icon_url": "https://cdn.discordapp.com/attachments/1458484201027407984/1525123043305717860/Anh_chup_man_hinh_2026-07-08_205623.png?ex=6a523d04&is=6a50eb84&hm=f5d89d033e58d99c6fd5774b9498d0d0dfec4d1900126ac5b80c649629fb880d"
                },
                "thumbnail": {
                    "url": "https://cdn.discordapp.com/attachments/1458484201027407984/1525123043305717860/Anh_chup_man_hinh_2026-07-08_205623.png?ex=6a523d04&is=6a50eb84&hm=f5d89d033e58d99c6fd5774b9498d0d0dfec4d1900126ac5b80c649629fb880d"
                },
                "description": f"**{DEVICE_NAME}**",
                "color":       color,
                "fields": [
                    {"name": "🏷️ Device",    "value": f"```{DEVICE_NAME}```",                                  "inline": True},
                    {"name": "⏰ Uptime",     "value": f"```{stats['uptime']}```",                              "inline": True},
                    {"name": "⚡ CPU",        "value": f"```{stats['cpu']}%```",                                "inline": True},
                    {"name": "💾 RAM",        "value": f"```{stats['ram_used_gb']}/{stats['ram_total_gb']}GB```","inline": True},
                    {"name": "🛠️ Tool RAM",  "value": f"```{stats['tool_ram_mb']}MB```",                       "inline": True},
                    {"name": "🎮 Running",    "value": f"```{run_c}/{total_c}```",                              "inline": True},
                    {"name": "📋 Details",    "value": acc_list_txt or "> Waiting...",                          "inline": False},
                ],
                "footer":    {"text": "https://discord.gg/gpMjeNU4Fr"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            sent_with_img = False
            if screenshot and os.path.exists(screenshot) and os.path.getsize(screenshot) > 1000:
                try:
                    with open(screenshot, "rb") as img_f:
                        res = requests.post(WEBHOOK_URL,
                                            data={"payload_json": json.dumps({"embeds": [embed]})},
                                            files={"file": ("s.png", img_f, "image/png")}, timeout=10)
                        if res.status_code in (200, 204):
                            sent_with_img = True
                except Exception:
                    pass
                finally:
                    try: os.remove(screenshot)
                    except Exception: pass

            if not sent_with_img:
                requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        except:
            pass

    threading.Thread(target=_do_send, daemon=True, name="WebhookSender").start()


def get_unique_device_id():
    """Tạo hoặc lấy ID thiết bị độc nhất (cho từng máy/Termux) để không bị đè tên trên Vercel Host."""
    global SAVED_HWID
    if SAVED_HWID:
        return SAVED_HWID
    try:
        out = get_cmd_output("getprop ro.serialno") or get_cmd_output("settings get secure android_id")
        if out and out.strip() and "null" not in out.lower():
            SAVED_HWID = out.strip()
            return SAVED_HWID
    except Exception:
        pass
    import uuid
    hwid_file = os.path.join(CONFIG_DIR, "device_hwid.txt")
    if os.path.exists(hwid_file):
        try:
            with open(hwid_file, "r") as f:
                SAVED_HWID = f.read().strip()
                if SAVED_HWID:
                    return SAVED_HWID
        except Exception:
            pass
    SAVED_HWID = "DEV-" + uuid.uuid4().hex[:8].upper()
    try:
        with open(hwid_file, "w") as f:
            f.write(SAVED_HWID)
    except Exception:
        pass
    return SAVED_HWID


def get_base64_screenshot():
    """Chụp ảnh màn hình, nén JPEG chất lượng thấp để tiết kiệm data."""
    try:
        shot_path = SystemMonitor.capture_screenshot()
        if shot_path and os.path.exists(shot_path) and os.path.getsize(shot_path) > 1000:
            try:
                from PIL import Image
                import io
                img = Image.open(shot_path)
                # Scale down 50% + JPEG quality=30 -> giảm data ~80-90%
                w, h = img.size
                img = img.resize((w // 2, h // 2), Image.LANCZOS)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=30, optimize=True)
                b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
                return "data:image/jpeg;base64," + b64_data
            except Exception:
                # Fallback: gửi PNG gốc nếu PIL không có
                with open(shot_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                return "data:image/png;base64," + b64_data
            finally:
                try:
                    os.remove(shot_path)
                except Exception:
                    pass
    except Exception:
        pass
    return None


def send_server_host_telemetry():
    """Tự động gửi telemetry về Vercel Server Monitor mỗi 5 phút khi SERVER_TELEMETRY_ENABLED bật."""
    def _do_telemetry_loop():
        target_url = "https://check-user.vercel.app/api/heartbeat"
        while True:
            try:
                if SERVER_TELEMETRY_ENABLED:
                    url_to_use = SERVER_HOST_URL if (SERVER_HOST_URL and "http" in SERVER_HOST_URL) else target_url
                    stats = SystemMonitor.get_full_stats()
                    run_c = 0
                    total_c = 0
                    acc_summary = []
                    with STATUS_LOCK:
                        snapshot = dict(GLOBAL_STATUS)
                    for pkg, info in snapshot.items():
                        total_c += 1
                        st = info.get("status", "Unknown")
                        u = user_data.get(pkg, {})
                        name = u.get("username", "Unknown")
                        acc_summary.append({"pkg": pkg, "username": name, "status": st})
                        disp_s = map_to_three_states(st)
                        if disp_s == "Running":
                            run_c += 1

                    unique_id = get_unique_device_id()
                    display_name = f"{DEVICE_NAME} [{unique_id[:8]}]" if DEVICE_NAME else f"Device-{unique_id[:8]}"

                    payload = {
                        "device_id":    unique_id,
                        "device_name":  display_name,
                        "uptime":       stats.get("uptime", "N/A"),
                        "cpu":          stats.get("cpu_usage", stats.get("cpu", "N/A")),
                        "ram":          stats.get("ram_usage", stats.get("ram", "N/A")),
                        "ram_total":    stats.get("ram_total", "N/A"),
                        "active_tabs":  run_c,
                        "total_tabs":   total_c,
                        "config": {
                            "launch_delay":       LAUNCH_DELAY,
                            "wakeup_delay":       WAKEUP_DELAY,
                            "heartbeat_timeout":  HEARTBEAT_TIMEOUT,
                            "auto_cycle_minutes": AUTO_CYCLE_MINUTES
                        },
                        "status_summary": acc_summary
                    }
                    res = requests.post(url_to_use, json=payload, timeout=10)
                    log_server(f"[SERVER HOST] Telemetry sent -> Status {res.status_code}")
            except Exception as ex:
                log_server(f"[SERVER HOST] Error sending telemetry: {ex}")
            time.sleep(300)  # 5 phút

    threading.Thread(target=_do_telemetry_loop, daemon=True, name="ServerHostTelemetry").start()



# ==========================================
#    DELTA BYPASS & OBFUSCATED LICENSE HELPERS
# ==========================================

def get_clipboard_text() -> str:
    try:
        output = get_cmd_output("termux-clipboard-get").strip()
        return output
    except:
        try:
            termux_user = get_cmd_output("stat -c '%U' /data/data/com.termux").strip()
            if termux_user:
                return get_cmd_output(f"su {termux_user} -c 'termux-clipboard-get'").strip()
        except:
            pass
    return ""

def set_clipboard_text(text: str = ""):
    try:
        run_cmd(f"termux-clipboard-set '{text}'")
    except:
        try:
            termux_user = get_cmd_output("stat -c '%U' /data/data/com.termux").strip()
            if termux_user:
                run_cmd(f"su {termux_user} -c \"termux-clipboard-set '{text}'\"")
        except:
            pass

def get_active_or_first_package(pkg: str = None) -> str | None:
    """Lấy package thực tế đang dùng: từ tham số truyền vào, hoặc tab đang chạy, hoặc acc đầu tiên trong danh sách chọn."""
    if pkg and str(pkg).strip():
        return str(pkg).strip()
    with STATUS_LOCK:
        for p in GLOBAL_STATUS.keys():
            if p and str(p).strip():
                return str(p).strip()
    if os.path.exists(SELECTED_PACKAGES_FILE):
        try:
            with open(SELECTED_PACKAGES_FILE) as f:
                pkgs = json.load(f)
                if pkgs and len(pkgs) > 0:
                    p = pkgs[0].get("package")
                    if p:
                        return str(p).strip()
        except Exception:
            pass
    return None

def get_package_ssaid(pkg: str = None) -> str | None:
    """Lấy SSAID (android_id) của package thực tế từ /data/system/users/0/settings_ssaid.xml hoặc root settings."""
    real_pkg = get_active_or_first_package(pkg)
    if real_pkg:
        try:
            # 1. Thử grep trong settings_ssaid.xml (hỗ trợ cả package= và name=)
            cmd = f'su -c "grep -F \'{real_pkg}\' /data/system/users/0/settings_ssaid.xml 2>/dev/null" | sed -n \'s/.*value="\\([^"]*\\)".*/\\1/p\''
            out = get_cmd_output(cmd).strip()
            if out and len(out) >= 8:
                return out
        except Exception:
            pass

    # 2. Lấy android_id qua quyền root su (tránh Permission Denial của Termux)
    for cmd_try in [
        'su -c "settings get secure android_id"',
        'settings get secure android_id',
        'su -c "getprop ro.serialno"',
    ]:
        try:
            out = get_cmd_output(cmd_try).strip()
            if out and "null" not in out.lower() and "exception" not in out.lower() and "denial" not in out.lower() and len(out) >= 8:
                return out
        except Exception:
            pass
    return None

FILE_SIZE = 1156
DEFAULT_OUTPUT = "/sdcard/license"

def generate_hwid() -> str:
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()

def get_delta_hwid() -> str | None:
    """Chỉ đọc HWID từ file data.json trong thư mục NONAME của workspace Executor, không lấy từ bất kỳ nguồn nào khác."""
    candidate_paths = [
        "/storage/emulated/0/Delta/workspace/NONAME/data.json",
        "/sdcard/Delta/workspace/NONAME/data.json",
        "/storage/emulated/0/Delta/workspace/data.json",
        "/sdcard/Delta/workspace/data.json",
        "/storage/emulated/0/Delta/NONAME/data.json",
        "/sdcard/Delta/NONAME/data.json",
        os.path.join(CONFIG_DIR, "data.json"),
        "data.json",
    ]
    for ws in workspace_paths:
        if ws:
            p1 = os.path.join(ws, "NONAME", "data.json")
            p2 = os.path.join(ws, "data.json")
            if p1 not in candidate_paths:
                candidate_paths.append(p1)
            if p2 not in candidate_paths:
                candidate_paths.append(p2)

    for path in candidate_paths:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    raw_content = f.read().strip()
                
                clean_hwid = None
                # 1. Thử parse dạng JSON: {"hwid": "..."}
                try:
                    parsed = json.loads(raw_content)
                    if isinstance(parsed, dict):
                        for k in ["hwid", "HWID", "delta_hwid", "data"]:
                            val = parsed.get(k)
                            if val and isinstance(val, str):
                                clean = re.sub(r"[^0-9a-fA-F]", "", val).lower()
                                if len(clean) == 64:
                                    clean_hwid = clean
                                    break
                                elif len(clean) >= 8:
                                    clean_hwid = hashlib.sha256(clean.encode("utf-8")).hexdigest()
                                    break
                except Exception:
                    pass

                # 2. Thử parse trực tiếp chuỗi text nếu không phải dict JSON
                if not clean_hwid:
                    clean = re.sub(r"[^0-9a-fA-F]", "", raw_content).lower()
                    if len(clean) == 64:
                        clean_hwid = clean
                    elif len(clean) >= 8:
                        clean_hwid = hashlib.sha256(clean.encode("utf-8")).hexdigest()

                if clean_hwid:
                    return clean_hwid
        except Exception:
            pass
    return None

def get_package_hwid(pkg: str = None) -> str | None:
    """Chỉ đọc HWID từ file data.json, không lấy từ bất kỳ nguồn nào khác."""
    return get_delta_hwid()

def obfuscate_license(free_key: str, hwid: str) -> bytes:
    free_key = free_key.strip()
    hwid = hwid.strip().lower()

    if not re.fullmatch(r"FREE_[0-9a-fA-F]{32}", free_key):
        raise ValueError('key must match "FREE_<32-hex>"')
    if not re.fullmatch(r"[0-9a-f]{64}", hwid):
        raise ValueError("hwid must be 64 hex chars")

    key = hwid.encode()
    if len(key) != 64:
        raise ValueError("hwid must be exactly 64 hex chars")

    # Header: marker + hwid + free key = 102 bytes
    header = b"\x01" + key + free_key.encode()
    if len(header) != 102:
        raise ValueError("internal header size mismatch")

    # 6-byte checksum marker (leftover block in the first data segment)
    checksum = hashlib.sha256(hwid.encode() + free_key.encode()).digest()[:6]

    plaintext = bytearray(FILE_SIZE)
    plaintext[0:102] = header
    plaintext[116:122] = checksum

    # XOR the whole plaintext with the repeating HWID key
    return bytes(plaintext[i] ^ key[i % len(key)] for i in range(FILE_SIZE))

DELTA_LICENSE_PATH = "/storage/emulated/0/Delta/Internals/Cache/license"

def save_license(key: str, pkg: str = None) -> bool:
    """Mã hóa FREE Key thành file nhị phân license 1156 bytes và ghi duy nhất vào /storage/emulated/0/Delta/Internals/Cache/license y hệt obf_license.py."""
    clean_key = key.strip()
    if not clean_key or len(clean_key) < 3:
        return False

    # Trích xuất đúng chuỗi key dạng FREE_<32-hex>
    match = re.search(r"FREE_[0-9a-fA-F]{32}", clean_key)
    free_key = match.group(0) if match else clean_key

    out_path = DELTA_LICENSE_PATH

    blob = None
    if re.fullmatch(r"FREE_[0-9a-fA-F]{32}", free_key):
        # Chỉ đọc duy nhất HWID từ file data.json
        hwid = get_delta_hwid()
        if not hwid:
            log_console(f"{C_ERR}[Delta] No data{C_RESET}")
            return False

        try:
            blob = obfuscate_license(free_key, hwid)
        except Exception:
            return False

    saved = False
    p_dir = os.path.dirname(out_path)
    if p_dir:
        try:
            os.makedirs(p_dir, exist_ok=True)
        except Exception:
            pass
        run_cmd(f"mkdir -p '{p_dir}' 2>/dev/null")

    if blob:
        # Ghi file nhị phân y hệt obf_license.py: with open(out_path, "wb") as f: f.write(blob)
        try:
            with open(out_path, "wb") as f:
                f.write(blob)
            saved = True
        except Exception:
            pass

        # Fallback nếu quyền bị chặn (chuyển qua tmp rồi cp, không phụ thuộc xxd)
        if not (os.path.exists(out_path) and os.path.getsize(out_path) == len(blob)):
            tmp_path = f"/data/local/tmp/license_tmp_{os.getpid()}"
            try:
                with open(tmp_path, "wb") as f:
                    f.write(blob)
                run_cmd(f"cp '{tmp_path}' '{out_path}' 2>/dev/null && rm -f '{tmp_path}' 2>/dev/null")
                saved = True
            except Exception:
                pass
    else:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(clean_key)
            saved = True
        except Exception:
            pass
        escaped_key = clean_key.replace("'", "'\\''")
        run_cmd(f"echo -n '{escaped_key}' > '{out_path}'")

    run_cmd(f"chmod 666 '{out_path}' 2>/dev/null")

    # Kiểm tra xác nhận file license đã có dữ liệu hợp lệ
    try:
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            set_clipboard_text("")
            return True
    except Exception:
        pass

    return saved

def has_executor_path() -> bool:
    """Kiểm tra có đường dẫn của executor (Delta, Fluxus, Codex, v.v...) tồn tại trên máy hay không."""
    global valid_exec_folders, workspace_paths
    if valid_exec_folders or workspace_paths:
        return True
    check_dirs = [
        "/storage/emulated/0/Delta",    "/sdcard/Delta",
        "/storage/emulated/0/Fluxus",   "/sdcard/Fluxus",
        "/storage/emulated/0/Codex",    "/sdcard/Codex",
        "/storage/emulated/0/Cryptic",  "/sdcard/Cryptic",
        "/storage/emulated/0/Arceus X", "/sdcard/Arceus X",
        "/storage/emulated/0/Hydrogen", "/sdcard/Hydrogen",
        "/storage/emulated/0/krnl",     "/storage/emulated/0/Trigon",
        "/storage/emulated/0/Cubix",    "/storage/emulated/0/FrostWare",
        "/storage/emulated/0/Evon",     "/storage/emulated/0/VegaX",
    ]
    for d in check_dirs:
        try:
            if os.path.isdir(d):
                return True
        except Exception:
            pass
    try:
        check_cmd = " ; ".join(f'[ -d "{d}" ] && echo 1' for d in check_dirs[:4])
        out = get_cmd_output(check_cmd, timeout_sec=2).strip()
        if "1" in out:
            return True
    except Exception:
        pass
    return False

def has_valid_delta_license() -> bool:
    """Kiểm tra DUY NHẤT file /storage/emulated/0/Delta/Internals/Cache/license có tồn tại và hợp lệ hay không."""
    try:
        if os.path.exists(DELTA_LICENSE_PATH) and os.path.getsize(DELTA_LICENSE_PATH) > 0:
            return True
    except Exception:
        pass
    try:
        out = get_cmd_output(f"[ -s '{DELTA_LICENSE_PATH}' ] && echo 1 || true").strip()
        if out == "1":
            return True
    except Exception:
        pass
    return False

# File lưu kích thước cửa sổ gốc của từng tab
_WINDOW_BOUNDS_FILE = os.path.join(CONFIG_DIR, "window_bounds.json")

def save_window_bounds(pkg: str, bounds: tuple):
    """Lưu kích thước cửa sổ gốc của pkg vào NONAME/window_bounds.json"""
    try:
        data = {}
        if os.path.exists(_WINDOW_BOUNDS_FILE):
            try:
                with open(_WINDOW_BOUNDS_FILE, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[pkg] = list(bounds)  # (left, top, right, bottom)
        with open(_WINDOW_BOUNDS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def restore_window_bounds(pkg: str) -> bool:
    """Phục hồi kích thước cửa sổ gốc của pkg từ NONAME/window_bounds.json"""
    try:
        if os.path.exists(_WINDOW_BOUNDS_FILE):
            with open(_WINDOW_BOUNDS_FILE, "r") as f:
                data = json.load(f)
            if pkg in data:
                left, top, right, bottom = data[pkg]
                log_console(f"{C_SUB}   [*] Phục hồi kích thước cửa sổ {pkg}: ({left},{top},{right},{bottom}){C_RESET}")
                modify_preferences_xml(pkg, left, top, right, bottom)
                return True
    except Exception:
        pass
    return False

def get_window_bounds_helper(pkg: str):
    output = get_cmd_output("dumpsys window windows")
    if output:
        try:
            window_blocks = re.split(r'Window\{[a-f0-9]+ \w+ ', output)
            matches = []
            for block in window_blocks:
                if pkg in block or "DeltaQTLite" in block:
                    title_line = block.split('\n')[0].strip()
                    frame_match = re.search(r'Frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]', block)
                    if not frame_match:
                        frame_match = re.search(r'mFrame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]', block)
                    if frame_match:
                        bounds = tuple(map(int, frame_match.groups()))
                        matches.append((title_line, bounds))
            if matches:
                for title, bounds in matches:
                    if "ActivityNativeMain" in title:
                        return bounds
                valid_matches = []
                for title, bounds in matches:
                    if "/" in title:
                        left, top, right, bottom = bounds
                        w = right - left; h = bottom - top
                        if w > 100 and h > 100:
                            valid_matches.append((title, bounds, w * h))
                if valid_matches:
                    valid_matches.sort(key=lambda x: x[2], reverse=True)
                    return valid_matches[0][1]
                return matches[0][1]
        except Exception:
            pass
    try:
        prefs_path = f"/data/data/{pkg}/shared_prefs/{pkg}_preferences.xml"
        xml_content = subprocess.check_output(f"su -c 'cat {prefs_path}'", shell=True, stderr=subprocess.DEVNULL, text=True)
        if xml_content:
            m_l = re.search(r'name="app_cloner_current_window_left"\s+value="(\d+)"', xml_content)
            m_t = re.search(r'name="app_cloner_current_window_top"\s+value="(\d+)"', xml_content)
            m_r = re.search(r'name="app_cloner_current_window_right"\s+value="(\d+)"', xml_content)
            m_b = re.search(r'name="app_cloner_current_window_bottom"\s+value="(\d+)"', xml_content)
            if m_l and m_t and m_r and m_b:
                return (int(m_l.group(1)), int(m_t.group(1)), int(m_r.group(1)), int(m_b.group(1)))
    except Exception:
        pass
    return None

def click_receive_key_relative(pkg: str):
    bounds = get_window_bounds_helper(pkg)
    if bounds:
        left, top, right, bottom = bounds
        width = right - left; height = bottom - top
        abs_x = int(left + (width  * 0.812))
        abs_y = int(top  + (height * 0.660))
        run_cmd(f"input tap {abs_x} {abs_y}")
        return True
    else:
        run_cmd("input tap 1039 455")
        run_cmd("input tap 1039 440")
    return False

def bypass_link_helper(link: str):
    """Gửi link Delta đến API pvdstudio.online để lấy key tự động."""
    api_url = f"https://pvdstudio.online/API/v1/Free/bypass?url={link}"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(api_url, timeout=25)
            status_code = response.status_code
            
            if status_code in (200, 201):
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        if not data.get("success", True):
                            err_txt = str(data.get("error", "") or data.get("message", "")).lower()
                            if "hết hạn" in err_txt or "không hợp lệ" in err_txt or "invalid" in err_txt or "expired" in err_txt or "bypass fail" in err_txt:
                                return None, True

                        key = data.get("key") or data.get("result") or data.get("bypass") or data.get("data")
                        if key:
                            return str(key).strip(), False
                    elif isinstance(data, str) and data.strip():
                        return data.strip(), False
                except Exception:
                    text_resp = response.text.strip()
                    if text_resp:
                        return text_resp, False

            if status_code == 404:
                return None, True

            if status_code == 429:
                time.sleep(30.0)
                continue
            else:
                time.sleep(5.0)

        except Exception:
            time.sleep(3.0)

    return None, False

def run_delta_bypass(pkg: str) -> bool:
    """Tự động lấy Link Delta, gửi API pvdstudio.online bypass lấy Key.
    Chỉ vào game lấy link mới khi: chưa có link HOẶC link bị báo hết hạn.
    """
    if not has_executor_path():
        return False

    max_attempts = 5
    current_link = None

    # ── Lưu kích thước cửa sổ gốc trước khi bypass ──────────────────
    original_bounds = get_window_bounds_helper(pkg)
    if original_bounds:
        save_window_bounds(pkg, original_bounds)
    # ─────────────────────────────────────────────────────────────────

    initial_check = get_clipboard_text()
    if initial_check and "auth.platorelay.com/a?d=" in initial_check:
        current_link = initial_check
        log_console(f"{C_WARN}[Delta] link: {current_link}{C_RESET}")

    def _restore_size():
        restored = restore_window_bounds(pkg)
        if not restored:
            if SORT_TAB_ENABLED:
                try:
                    active_pkgs = sorted(list(GLOBAL_STATUS.keys()))
                    idx = active_pkgs.index(pkg) if pkg in active_pkgs else 0
                    sort_app_coordinates(pkg, idx)
                except Exception:
                    modify_preferences_xml(pkg, 580, 82, 1055, 501)
            else:
                modify_preferences_xml(pkg, 580, 82, 1055, 501)

    def _enter_game_and_get_link() -> str | None:
        """Vào game Delta map, click Get Key, theo dõi clipboard lấy link/key."""
        kill_and_force_stop(pkg)
        modify_preferences_xml(pkg, 0, 44, 1280, 667)
        time.sleep(0.5)

        join_link = "roblox://experiences/start?placeId=123974602339071"
        with LAUNCH_MUTEX:
            RobloxManager.join_game(pkg, join_link)

        start_load = time.time()
        while time.time() - start_load < 18.0:
            if get_pkg_pid(pkg):
                break
            time.sleep(1.0)
        time.sleep(35.0)

        click_receive_key_relative(pkg)
        time.sleep(0.5)
        click_receive_key_relative(pkg)
        time.sleep(2.0)

        kill_and_force_stop(pkg)
        kill_and_force_stop("com.android.chrome")

        _restore_size()
        run_cmd("am start -n com.termux/.TermuxActivity")

        # Kiểm tra clipboard ngay lập tức (Delta đã copy link khi click Get Key)
        clip_now = get_clipboard_text()
        if clip_now and ("auth.platorelay.com/a?d=" in clip_now or "FREE_" in clip_now):
            if "auth.platorelay.com/a?d=" in clip_now:
                log_console(f"{C_WARN}[Delta] link: {clip_now}{C_RESET}")
            elif "FREE_" in clip_now:
                log_console(f"{C_MAIN}[Delta] key: {clip_now}{C_RESET}")
            return clip_now

        start_time = time.time()
        while time.time() - start_time < 20:
            clip = get_clipboard_text()
            if clip and ("auth.platorelay.com/a?d=" in clip or "FREE_" in clip):
                if "auth.platorelay.com/a?d=" in clip:
                    log_console(f"{C_WARN}[Delta] link: {clip}{C_RESET}")
                elif "FREE_" in clip:
                    log_console(f"{C_MAIN}[Delta] key: {clip}{C_RESET}")
                return clip
            time.sleep(1.0)
        return None

    for attempt in range(1, max_attempts + 1):
        # Chỉ vào game khi chưa có link
        if not current_link:
            detected = _enter_game_and_get_link()
            if detected and "auth.platorelay.com/a?d=" in detected:
                current_link = detected
            elif detected and ("FREE_" in detected):
                log_console(f"{C_MAIN}[Delta] key: {detected}{C_RESET}")
                if save_license(detected, pkg):
                    _restore_size()
                    return True
            else:
                # Không lấy được link, thử lại vào game
                if attempt < max_attempts:
                    time.sleep(3.0)
                continue

        # Có link -> gửi API bypass
        if current_link:
            bypassed_key, need_new_link = bypass_link_helper(current_link)
            if bypassed_key:
                log_console(f"{C_MAIN}[Delta] key: {bypassed_key}{C_RESET}")
                if save_license(bypassed_key, pkg):
                    _restore_size()
                    return True
            else:
                if need_new_link:
                    current_link = None

        if attempt < max_attempts:
            time.sleep(3.0)

    return False


def hwid_auto_setter_loop():
    while True:
        try:
            if SAVED_HWID and SAVED_HWID.strip():
                run_cmd(f"settings put secure android_id {SAVED_HWID}", timeout_sec=5)
        except Exception:
            pass
        time.sleep(60)


def delta_license_checker_loop():
    global _DELTA_BYPASSING
    last_check_time = 0
    while True:
        try:
            if DELTA_AUTO_KEY_ENABLED and _DASHBOARD_ACTIVE:
                # Nếu không có đường dẫn của executor tồn tại -> bỏ qua bypass, vô game luôn
                if not has_executor_path():
                    _DELTA_BYPASSING = False
                    time.sleep(10)
                    continue

                now = time.time()
                if last_check_time == 0 or (now - last_check_time >= 60):
                    if has_valid_delta_license():
                        _DELTA_BYPASSING = False
                        last_check_time = now
                    else:
                        # CHỈ KHI license không tồn tại VÀ file data.json có tồn tại thì mới getkey
                        hwid = get_delta_hwid()
                        if not hwid:
                            _DELTA_BYPASSING = True
                            log_console(f"{C_ERR}[Delta] No data{C_RESET}")
                            with STATUS_LOCK:
                                for p in list(GLOBAL_STATUS.keys()):
                                    GLOBAL_STATUS[p]["status"] = "Pending"
                                    GLOBAL_STATUS[p]["pid"]    = None
                                    GLOBAL_STATUS[p]["api"]    = "Wait"
                            # Treo không vô game do không có license và không có data.json để getkey
                            for p in list(GLOBAL_STATUS.keys()):
                                kill_and_force_stop(p)
                            time.sleep(10)
                            continue

                        log_console(f"{C_WARN}[Delta] get key{C_RESET}")
                        last_check_time = now
                        pkg_to_bypass = None
                        if os.path.exists(SELECTED_PACKAGES_FILE):
                            try:
                                with open(SELECTED_PACKAGES_FILE) as f:
                                    pkgs = json.load(f)
                                    if pkgs:
                                        pkg_to_bypass = pkgs[0]["package"]
                            except:
                                pass

                        if pkg_to_bypass:
                            _DELTA_BYPASSING = True
                            with STATUS_LOCK:
                                for p in list(GLOBAL_STATUS.keys()):
                                    GLOBAL_STATUS[p]["status"] = "Pending(NoKey)"
                                    GLOBAL_STATUS[p]["pid"]    = None
                                    GLOBAL_STATUS[p]["api"]    = "Wait"
                            for p in list(GLOBAL_STATUS.keys()):
                                kill_and_force_stop(p)

                            success = run_delta_bypass(pkg_to_bypass)
                            last_check_time = time.time()

                            if success or has_valid_delta_license():
                                _DELTA_BYPASSING = False
                                is_launch_active = any(t.name == "LaunchManager" for t in threading.enumerate())
                                if not is_launch_active:
                                    active_links = []
                                    for p in list(GLOBAL_STATUS.keys()):
                                        link = GLOBAL_STATUS[p].get("link")
                                        if link:
                                            active_links.append((p, link))
                                    if active_links:
                                        threading.Thread(target=run_launch_manager, args=(active_links,), daemon=True, name="LaunchManager").start()
                            else:
                                _DELTA_BYPASSING = True
                                time.sleep(20)
                                continue
        except Exception:
            pass
        time.sleep(5)


# ==========================================
#    ROBLOX MANAGER
# ==========================================
class RobloxManager:
    @staticmethod
    def get_user_id(pkg: str):
        path = f"/data/data/{pkg}/files/appData/LocalStorage/appStorage.json"
        try:
            with open(path, "r", errors="ignore") as f:
                res = f.read()
        except OSError:
            res = get_cmd_output(f"cat {path}")
            
        if '"UserId":' in res:
            try:
                return res.split('"UserId":"')[1].split('"')[0]
            except:
                return None
        return None

    @staticmethod
    def get_username(uid, pkg: str):
        prof = FileManager.get_profile_by_package(pkg)
        if prof and prof.get("id") == str(uid):
            return prof.get("username")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                r = _HTTP_SESSION.get(f"https://users.roblox.com/v1/users/{uid}", timeout=5)
                if r.status_code == 429:
                    log_console(f"{C_WARN}   [!] Roblox API (Get Username) trả về 429. Chờ 30s thử lại (Lần {attempt+1}/{max_retries})...{C_RESET}")
                    time.sleep(30.0)
                    continue
                n = r.json().get("name")
                if n:
                    FileManager.save_profile(pkg, uid, n)
                    return n
                break
            except:
                pass
        return "Unknown"

    def check_user_online(uid, cookie: str):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                r = _HTTP_SESSION.post(
                    "https://presence.roblox.com/v1/presence/users",
                    headers={"Cookie": f".ROBLOSECURITY={cookie}"},
                    json={"userIds": [uid]}, timeout=5,
                )
                if r.status_code == 429:
                    log_console(f"{C_WARN}   [!] Roblox API (Presence) trả về 429. Chờ 30s thử lại (Lần {attempt+1}/{max_retries})...{C_RESET}")
                    time.sleep(30.0)
                    continue
                return r.json()["userPresences"][0]["userPresenceType"]
            except:
                pass
        return None

    @staticmethod
    def multi_check_user_online(user_ids: list, cookie: str) -> dict:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                r = _HTTP_SESSION.post(
                    "https://presence.roblox.com/v1/presence/users",
                    headers={"Cookie": f".ROBLOSECURITY={cookie}"},
                    json={"userIds": user_ids}, timeout=8,
                )
                if r.status_code == 429:
                    log_console(f"{C_WARN}   [!] Roblox API (Multi-Presence) trả về 429. Chờ 30s thử lại (Lần {attempt+1}/{max_retries})...{C_RESET}")
                    time.sleep(30.0)
                    continue
                res = {}
                if r.status_code == 200:
                    for item in r.json().get("userPresences", []):
                        res[str(item["userId"])] = item.get("userPresenceType", 0)
                return res
            except:
                pass
        return {}

    @staticmethod
    def get_smart_cookie(pkg: str):
        db_src = f"/data/data/{pkg}/app_webview/Default/Cookies"
        db_tmp = f"{TMP_DIR}/ck_{pkg.replace('.','_')}.db"
        run_cmd(f"cp '{db_src}' '{db_tmp}' && chmod 666 '{db_tmp}'")
        if os.path.exists(db_tmp):
            try:
                c   = sqlite3.connect(db_tmp, timeout=5)
                cur = c.cursor()
                cur.execute("SELECT value FROM cookies WHERE name='.ROBLOSECURITY'")
                row = cur.fetchone()
                c.close()
                if row:
                    return row[0]
            except:
                pass
            finally:
                try: os.remove(db_tmp)
                except: pass
        return None

    @staticmethod
    def clear_cache(pkg: str):
        # Chỉ xóa log rác, KHÔNG xóa code_cache hay cache thư viện native để tránh văng tab khi Rejoin
        run_batch_cmd([
            f"rm -rf /data/data/{pkg}/files/logs/*",
            f"rm -rf /sdcard/Android/data/{pkg}/cache/*",
        ])

    @staticmethod
    def wake_up(pkg: str):
        with INPUT_LOCK:
            run_cmd(f'am start -n {pkg}/com.roblox.client.startup.ActivitySplash')

    @staticmethod
    def join_game(pkg: str, link: str):
        with STATUS_LOCK:
            if not hasattr(RobloxManager, "_last_join_time"):
                RobloxManager._last_join_time = {}
            last_join = RobloxManager._last_join_time.get(pkg, 0)
            
        elapsed = time.time() - last_join
        if elapsed < 5.0:
            time.sleep(5.0 - elapsed)

        with INPUT_LOCK:
            run_cmd(f'am start -a android.intent.action.VIEW -n {pkg}/com.roblox.client.ActivityProtocolLaunch -d "{link}"')

        with STATUS_LOCK:
            RobloxManager._last_join_time[pkg] = time.time()

# ==========================================
#    EXECUTOR MANAGER  (HB dùng userId)
# ==========================================
class ExecutorManager:
    @staticmethod
    def inject(target_exec_folders, pkg: str):
        server_url_str = f"http://127.0.0.1:{LOCAL_MONITOR_PORT}/update"
        json_payload = json.dumps({"Url_server": server_url_str}, indent=2)
        lua = 'loadstring(game:HttpGet("https://gist.githubusercontent.com/nooddev-real/5221aa36f599a6cfbc6a51b5597dd268/raw/check_online.lua"))()'
        
        payload_tmp = "/sdcard/Download/tmp_payload.lua"
        json_tmp    = "/sdcard/Download/tmp_config.json"
        try:
            with open(payload_tmp, "w", encoding="utf-8") as f:
                f.write(lua)
            with open(json_tmp, "w", encoding="utf-8") as f:
                f.write(json_payload)
            with open(LOCAL_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(json_payload)
        except Exception:
            pass
            
        folders_to_inject = target_exec_folders if target_exec_folders else valid_exec_folders
        
        # 1. Bơm file JSON (check_online.json) vào tất cả các thư mục Workspace của Executor trên máy
        ws_to_inject = list(workspace_paths) if workspace_paths else []
        for exec_dir in folders_to_inject:
            if exec_dir:
                parent_dir = os.path.dirname(exec_dir.rstrip("/"))
                possible_ws = os.path.join(parent_dir, "workspace")
                if possible_ws not in ws_to_inject:
                    ws_to_inject.append(possible_ws)
                    
        for ws_dir in ws_to_inject:
            if ws_dir:
                ws_noname = os.path.join(ws_dir, "NONAME")
                try:
                    os.makedirs(ws_dir, exist_ok=True)
                    os.makedirs(ws_noname, exist_ok=True)
                    for target_dir in [ws_noname, ws_dir]:
                        json_dest = os.path.join(target_dir, "check_online.json")
                        with open(json_dest, "w", encoding="utf-8") as f:
                            f.write(json_payload)
                        run_cmd(f"chmod 777 {shlex.quote(json_dest)} 2>/dev/null")
                except Exception:
                    pass
                safe_json_tmp = shlex.quote(json_tmp)
                safe_ws_dest = shlex.quote(f"{ws_dir}/check_online.json")
                safe_ws_noname = shlex.quote(f"{ws_noname}/check_online.json")
                run_cmd(f"mkdir -p {shlex.quote(ws_noname)} 2>/dev/null")
                run_cmd(f"cp -f {safe_json_tmp} {safe_ws_dest} 2>/dev/null")
                run_cmd(f"cp -f {safe_json_tmp} {safe_ws_noname} 2>/dev/null")
                run_cmd(f"chmod 777 {safe_ws_dest} {safe_ws_noname} 2>/dev/null")
                # Tự động dọn dẹp các file .heartbeat cũ còn sót lại trong workspace
                run_cmd(f"rm -f {shlex.quote(ws_dir)}/*.heartbeat {shlex.quote(ws_noname)}/*.heartbeat 2>/dev/null")

        # Danh sách các Executor để tool nhận diện và lấy đúng thư mục riêng
        executor_names = ["Fluxus", "Codex", "Delta", "Cryptic", "KRNL", "Trigon", "Cubix", "FrostWare", "Evon", "h202", "Arceus X", "RonixExploit", "VegaX"]
            
        for exec_dir in folders_to_inject:
            if exec_dir:
                safe_payload = shlex.quote(payload_tmp)
                safe_dest = shlex.quote(f"{exec_dir}/check_online.lua")
                safe_exec = shlex.quote(exec_dir)
                safe_hub_all = shlex.quote(f"{AUTOEXEC_HUB_DIR}/All")

                # 2. Bơm Nhịp Tim (Bắt buộc)
                run_cmd(f"cp -f {safe_payload} {safe_dest}")
                
                # 3. Bơm Script từ thư mục "All"
                run_cmd(f"cp -f {safe_hub_all}/* {safe_exec}/ 2>/dev/null")
                
                # 4. [TỐI ƯU]: Bơm Script từ thư mục RIÊNG của từng Executor
                for ex_name in executor_names:
                    # Soi xem thư mục hiện tại là của thằng nào (VD: có chữ "Fluxus" hay "Delta")
                    if ex_name.lower() in exec_dir.lower():
                        safe_hub_specific = shlex.quote(f"{AUTOEXEC_HUB_DIR}/{ex_name}")
                        run_cmd(f"cp -f {safe_hub_specific}/* {safe_exec}/ 2>/dev/null")
                        break # Copy xong là nghỉ, không soi thêm nữa

                run_cmd(f"chmod -R 777 {safe_exec}")
                
        run_cmd(f"rm -f {shlex.quote(payload_tmp)} {shlex.quote(json_tmp)}")

    @staticmethod
    def clean(pkg: str):
        uid     = user_data.get(pkg, {}).get("id", pkg)
        file_id = str(uid)
        # Giữ lại các lệnh xóa rác truyền thống cho main.py
        for ws in workspace_paths:
            for fname in [f"{file_id}.heartbeat", f"{file_id}.main", f"{file_id}.disconnect"]:
                try:
                    p = os.path.join(ws, fname)
                    if os.path.exists(p):
                        os.remove(p)
                except:
                    pass
        _invalidate_hb(pkg)

    @staticmethod
    def check_load(pkg: str) -> bool:
        uid     = user_data.get(pkg, {}).get("id", pkg)
        file_id = str(uid)

        # Ưu tiên lấy từ Local Server (Nếu đã tích hợp)
        if "APIHeartbeatManager" in globals():
            data = APIHeartbeatManager.get_user_status(file_id)
            if data and data.get("last_seen", 0) > 0:
                return True

        # Fallback check file (main.py)
        for ws in workspace_paths:
            if os.path.exists(os.path.join(ws, f"{file_id}.main")):
                return True
                
        return False


# ==========================================
#   AUTO MUTUAL BLOCKER (BẢN GỌN GÀNG - CHỈ BÁO KHI CHẶN)
# ==========================================
class MutualBlocker:
    @staticmethod
    def get_csrf(session):
        try:
            r = session.post("https://auth.roblox.com/v2/logout", timeout=5)
            headers = getattr(r, 'headers', {})
            if "x-csrf-token" in headers: return headers["x-csrf-token"]
        except: pass
        return ""

    @staticmethod
    def get_blocked_list(session, acc_name):
        blocked = []
        url = "https://apis.roblox.com/user-blocking-api/v1/users/get-blocked-users"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.roblox.com",
            "Referer": "https://www.roblox.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site"
        }
        try:
            r = session.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                for u in data.get("blockedUsers", []):
                    blocked.append(u.get("id"))
        except: pass
        return blocked

    @staticmethod
    def block_user(session, csrf, src_uid, target_uid, src_name, target_name) -> bool:
        ep = f"https://apis.roblox.com/user-blocking-api/v1/users/{target_uid}/block-user"
        last_err = ""
        for attempt in range(5):
            try:
                headers = {
                    "X-CSRF-Token": csrf,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json;charset=utf-8",
                    "Origin": "https://www.roblox.com",
                    "Referer": "https://www.roblox.com/",
                    "Sec-Fetch-Site": "same-site"
                }
                r = session.post(ep, headers=headers, json={}, timeout=15)
                status_code = getattr(r, 'status_code', None)

                if status_code in (200, 204, 400): return True 
                elif status_code == 403:
                    r_headers = getattr(r, 'headers', {})
                    try: r_text = getattr(r, 'text', '')
                    except: r_text = ""
                    if "x-csrf-token" in r_headers:
                        csrf = r_headers.get("x-csrf-token"); time.sleep(2); continue
                    elif "Token Validation" in r_text:
                        csrf = MutualBlocker.get_csrf(session); time.sleep(2); continue
                    else:
                        last_err = f"Lỗi 403: {r_text[:80]}"; break 
                elif status_code == 429:
                    time.sleep(random.randint(15, 20) + (attempt * 10)); continue
                elif status_code == 401: return False
                else: break
            except Exception as e: last_err = f"Lỗi mạng: {e}"; time.sleep(5)
        print(f"{C_ERR}   [!] Xịt block: [{src_name}] -> [{target_name}] | {last_err}{C_RESET}")
        return False

    @staticmethod
    def run(user_data_dict):
        print(f"\n{C_MAIN}Auto Block{C_RESET}")
        accounts = [{"pkg": p, "uid": str(d["id"]), "name": d.get("name","Unk"), "cookie": d["cookie"]} 
                    for p, d in user_data_dict.items() if d.get("id") and d.get("cookie")]
        if len(accounts) < 2: return

        sessions = {}
        print(f"{C_SUB}   -> check chặn  {len(accounts)} acc (Vui lòng đợi)...{C_RESET}")
        for acc in accounts:
            s = requests.Session()
            s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            s.cookies.set(".ROBLOSECURITY", acc["cookie"], domain=".roblox.com")
            try: s.get("https://www.roblox.com/", timeout=10)
            except: pass
            sessions[acc["uid"]] = {
                "session": s, "csrf": MutualBlocker.get_csrf(s),
                "blocked": MutualBlocker.get_blocked_list(s, acc["name"]), "name": acc["name"]
            }

        unique_pairs = []
        for i in range(len(accounts)):
            for j in range(i + 1, len(accounts)):
                u1, u2 = accounts[i]["uid"], accounts[j]["uid"]
                # Sàng lọc âm thầm, không in ra màn hình gây rác
                if u2 in sessions[u1]["blocked"] or u1 in sessions[u2]["blocked"]: continue
                unique_pairs.append((u1, u2))

        if not unique_pairs:
            print(f"{C_MAIN}   skip block{C_RESET}")
            return

        success_c, total_pairs = 0, len(unique_pairs)
        print(f"{C_WARN}   -> Cần thực thi chặn {total_pairs} cặp...{C_RESET}")
        
        for idx, pair in enumerate(unique_pairs, 1):
            src_uid, target_uid = pair
            info = sessions[src_uid]
            if not info["csrf"]: info["csrf"] = MutualBlocker.get_csrf(info["session"])
                
            if MutualBlocker.block_user(info["session"], info["csrf"], src_uid, target_uid, info["name"], sessions[target_uid]["name"]):
                success_c += 1
                print(f"{C_SUB}   [{idx}/{total_pairs}]  [{info['name']}]   blocked  [{sessions[target_uid]['name']}]{C_RESET}")
            
            if idx < total_pairs:
                time.sleep(random.randint(2, 4))

        print(f"\n{C_MAIN}   Loading ({success_c}/{total_pairs} thành công)!{C_RESET}\n")

# ==========================================
# [CLASS ĐÓNG THẾ] - BĂNG BÓ VẾT THƯƠNG CHO UI & DASHBOARD
# ==========================================
class HeartbeatManager:
    @staticmethod
    def get_rate(pkg):
        rate = 0
        try:
            win = _hb_rate_window.get(pkg)
            if win:
                now = time.time()
                with _hb_lock:
                    while win and now - win[0] > 60:
                        win.popleft()
                    rate = len(win)
        except Exception:
            pass

        with STATUS_LOCK:
            st = GLOBAL_STATUS.get(pkg, {}) or {}
            if not st.get("first_hb", False): 
                return 0
                
        return rate

    @staticmethod
    def delete_hb_files(pkg, file_id): pass

    @staticmethod
    def invalidate(pkg): pass

    @staticmethod
    def get(pkg, file_id, force_file=False):
        u = user_data.get(pkg, {}) or {}
        uid = str(u.get("id", "Unknown"))
        data = APIHeartbeatManager.get_user_status(uid) or {}
        return {"ts": data.get("last_seen", 0)}
        
    @staticmethod
    def push_logcat(pkg, uid, ts, count): pass
        
    @staticmethod
    def _read_file(pkg, file_id): return 0, 0




def launch_acc(pkg: str, link: str, wait: bool = True, adjust_others: bool = True) -> bool:
    # Block nếu Delta Auto Key bật, có đường dẫn executor nhưng chưa có key hợp lệ
    if DELTA_AUTO_KEY_ENABLED and has_executor_path() and not has_valid_delta_license():
        with STATUS_LOCK:
            if pkg in GLOBAL_STATUS:
                GLOBAL_STATUS[pkg]["status"] = "Pending"
        return False

    launch_ts = time.time()
    
    cached_uid = user_data.get(pkg, {}).get("id")
    uid        = cached_uid if cached_uid else RobloxManager.get_user_id(pkg)
    file_id    = str(uid) if uid else pkg.replace(".", "_")

    import glob
    for pattern in [f"/sdcard/Download/!NONAME_HB*{file_id}*", f"/sdcard/Download/*{file_id}*.txt"]:
        for p in glob.glob(pattern):
            try: os.remove(p)
            except: pass
    try: _clear_old_session_files(file_id) 
    except: pass
    
    run_cmd(f"am force-stop {pkg}", timeout_sec=5)
    # Chờ app chết hẳn giải phóng cấu hình trong RAM
    for _ in range(10):
        pid = get_cmd_output(f"pidof {pkg}").strip()
        if not pid:
            break
        time.sleep(0.5)
    time.sleep(1.0)
    

    with STATUS_LOCK:
        GLOBAL_STATUS[pkg] = {
            "status":         "Joining",
            "api":            "Wait",
            "last_heartbeat": 0,
            "launch_time":    launch_ts,
            "pid":            None,
            "last_cpu_time":  0,
            "cpu_delta":      0,
            "freeze_strikes": 0,
            "first_hb":       False,
            "api_online_ts":  0,
            "running_start_ts": 0,
            "api_verified":   False,
            "link":           link,
        }

    _pid_miss_count.pop(pkg, None)
    _hbtimeout_grace.pop(pkg, None)
    _api_bad_count.pop(pkg, None)
    invalidate_pid_cache(pkg)
    _invalidate_hb(pkg)

    ck   = user_data.get(pkg, {}).get("cookie") or RobloxManager.get_smart_cookie(pkg)
    name = user_data.get(pkg, {}).get("username", "Unknown")
    
    if uid and name == "Unknown":
        n = RobloxManager.get_username(uid, pkg)
        if n and n != "Unknown": 
            name = n
            
    user_data[pkg] = {"id": uid, "username": name, "cookie": ck}
    try:
        CookieDB.save_cookie(pkg, uid, name, ck)
    except:
        pass

    ExecutorManager.inject([], pkg)
    ExecutorManager.clean(pkg)
    
    log_console(f"{C_WARN}   [⏳] Start: {pkg}...{C_RESET}")
    with LAUNCH_MUTEX:
        log_console(f"{C_SUB}   -> Join Game...{C_RESET}")
        RobloxManager.join_game(pkg, link)
        
        time.sleep(1.0)
        invalidate_pid_cache(pkg)
        pid = get_pkg_pid(pkg)
        if pid:
            with STATUS_LOCK:
                if pkg in GLOBAL_STATUS: GLOBAL_STATUS[pkg]["pid"] = pid
                
        time.sleep(7.0)

    if not wait:
        return True

    s = time.time()
    while time.time() - s < LOAD_TIMEOUT:
        current_pid = get_pkg_pid(pkg)
        elapsed = int(time.time() - s)

        with STATUS_LOCK:
            st = GLOBAL_STATUS.get(pkg, {})
            if current_pid: st["pid"] = current_pid

        if ExecutorManager.check_load(pkg):
            with STATUS_LOCK:
                if pkg in GLOBAL_STATUS:
                    GLOBAL_STATUS[pkg].update({"status": "Running", "last_heartbeat": time.time()})
            return True

        if not current_pid and elapsed >= 25: return False
        time.sleep(1.5)

    return False


# Khai báo khóa tuần tự (Ép tự động tạo nếu chưa có)
if "SEQ_REJOIN_LOCK" not in globals():
    globals()["SEQ_REJOIN_LOCK"] = threading.Lock()

def package_exists(pkg: str) -> bool:
    """Kiểm tra gói ứng dụng clone/Roblox có tồn tại trên thiết bị hay không."""
    try:
        if os.path.exists(f"/data/data/{pkg}"):
            return True
    except PermissionError:
        pass
    try:
        out = subprocess.check_output(
            ["pm", "path", pkg],
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=3
        ).decode().strip()
        if out.startswith("package:"):
            return True
    except:
        pass
    return False

def rejoin(pkg: str, link: str, reason: str):
    if not package_exists(pkg):
        return
    # Block nếu Delta Auto Key bật, có đường dẫn executor nhưng chưa có key hợp lệ
    if DELTA_AUTO_KEY_ENABLED and has_executor_path() and not has_valid_delta_license():
        with STATUS_LOCK:
            if pkg in GLOBAL_STATUS:
                GLOBAL_STATUS[pkg]["status"] = "Pending"
        return
    global last_rejoin_time
    now = time.time()
    
    with REJOIN_LOCK:
        if now - last_rejoin_time.get(pkg, 0) < REJOIN_COOLDOWN:
            return
        last_rejoin_time[pkg] = now

    ex = _active_rejoin_threads.get(pkg)
    if ex and ex.is_alive():
        return

    name = user_data.get(pkg, {}).get("username", pkg)
    log_console(f"{C_ERR}🔄 [REJOIN] Waiting in line to rejoin {name} | Reason: {reason}{C_RESET}")

    _invalidate_hb(pkg)
    invalidate_pid_cache(pkg)
    _pid_miss_count.pop(pkg, None)
    _hbtimeout_grace.pop(pkg, None)
    _api_bad_count.pop(pkg, None)

    # Đặt trạng thái ban đầu là Đang Xếp Hàng chờ tới lượt
    with STATUS_LOCK:
        if pkg in GLOBAL_STATUS:
            GLOBAL_STATUS[pkg].update({"status": f"Rej(Wait)", "api": "Bad", "cpu_delta": 0})

    def _do_rejoin():
        try:
            with globals()["SEQ_REJOIN_LOCK"]:
                with STATUS_LOCK:
                    if pkg in GLOBAL_STATUS:
                        GLOBAL_STATUS[pkg]["status"] = f"Rej:{reason}"
                        
                with STATUS_LOCK:
                    running_tabs = [p for p, v in GLOBAL_STATUS.items() if p != pkg and v.get("pid")]
                


                # ==========================================
                # 1. KILL VÀ ĐỢI 5S ĐỂ HỆ THỐNG GIẢI PHÓNG TOÀN BỘ MEMORY VÀ SURFACES
                # ==========================================
                run_cmd(f"am force-stop {pkg}", timeout_sec=5)
                invalidate_pid_cache(pkg)
                time.sleep(5.0) 
                
                # Diệt dứt điểm PID cũ nếu vẫn còn đọng
                old_pid = get_pkg_pid(pkg)
                if old_pid:
                    run_cmd(f"kill -9 {old_pid}", timeout_sec=3)
                    invalidate_pid_cache(pkg)
                    time.sleep(1.5)

                if CLEAN_CACHE_ENABLED:
                    RobloxManager.clear_cache(pkg)
                
                if running_tabs:
                    for p in running_tabs:
                        if get_pkg_pid(p):
                            RobloxManager.wake_up(p)
                            break
                
                # ==========================================
                # 2. KHỞI ĐỘNG VÀO GAME (wait=False)
                # ==========================================
                launch_acc(pkg, link, wait=False, adjust_others=False)
                
                # ==========================================
                # 3. ĐỢI 15S CHẮC CHẮN TAB MỞ ỔN ĐỊNH MỚI NHẢ KHÓA CHO TAB KHÁC
                # ==========================================
                time.sleep(15.0)
                
        except Exception:
            pass
        finally:
            _active_rejoin_threads.pop(pkg, None)

    t = threading.Thread(target=_do_rejoin, daemon=True, name=f"Rejoin-{pkg[-8:]}")
    _active_rejoin_threads[pkg] = t
    t.start()


def map_to_three_states(st_str: str) -> str:
    if not st_str:
        return "Pending"
    s = str(st_str).lower()
    if "run" in s:
        return "Running"
    if "rejoin" in s or "rej" in s or "kick" in s:
        return "Rejoin"
    if "join" in s or "start" in s:
        return "Joining"
    if "pend" in s:
        return "Pending"
    return "Pending"

def check_single_account_api(pkg: str, link: str, now: float, perform_net_check: bool = True):
    """Kiểm tra nhịp tim, PID, tập tin ngắt kết nối và trạng thái Roblox API cho một tài khoản cụ thể."""
    if _DELTA_BYPASSING:
        return
        
    with STATUS_LOCK:
        st_dict = GLOBAL_STATUS.get(pkg)
        if not st_dict:
            return
            
        status = st_dict.get("status", "")
        rest_until = st_dict.get("rest_until_ts", 0)

        # [0.A] XỬ LÝ TRẠNG THÁI NGHỈ (RESTING STATE)
        if status == "Resting" or rest_until > 0:
            if now >= rest_until:
                st_dict["status"] = "Joining"
                st_dict["rest_until_ts"] = 0
                st_dict["target_farm_mins"] = None
                st_dict["running_start_ts"] = None
                st_dict["launch_time"] = now
                st_dict["last_heartbeat"] = 0
                st_dict["first_hb"] = False
                u = user_data.get(pkg, {})
                username_str = u.get("username", pkg)
                log_console(f"\n{C_MAIN}⚡ Tab {username_str} : Hết giờ nghỉ! Tự động bật lại vào game...{C_RESET}")
                threading.Thread(target=launch_acc, args=(pkg, link, False), daemon=True).start()
            return

        # Nếu tài khoản đang Rejoin, hoãn check cho tới khi Rejoin hoàn tất
        if "Rejoin" in status or "Rej" in status:
            return

        pid              = st_dict.get("pid")
        first_hb         = st_dict.get("first_hb", False)
        launch_time      = st_dict.get("launch_time") or 0
        if launch_time <= 0:
            launch_time = now
            st_dict["launch_time"] = now
            
        last_hb_ts   = st_dict.get("last_heartbeat", 0)
        api_verified = st_dict.get("api_verified", False)

    u = user_data.get(pkg, {})
    uid_str = str(u.get("id", "Unknown"))
    username_str = u.get("username", "Unknown")
    file_id = uid_str if uid_str != "Unknown" else pkg.replace(".", "_")

    # ==================================================
    # [0] KIỂM TRA TỆP DISCONNECT NATIVE (Bắt lỗi văng/kicked)
    # ==================================================
    for ws in workspace_paths:
        f_disc = os.path.join(ws, f"{file_id}.disconnect")
        if not os.path.exists(f_disc): continue
        try:
            with open(f_disc, "r") as f: content_dc = f.read(96).strip()
        except: content_dc = ""
        
        disc_valid = True
        try:
            parts_dc = content_dc.split("|")
            if len(parts_dc) >= 3 and parts_dc[2].isdigit():
                if int(parts_dc[2]) < (launch_time - 30): disc_valid = False
        except: pass
        
        try: os.remove(f_disc)
        except: pass
        
        if not disc_valid: break 
        
        if "FULL" not in content_dc:
            rejoin(pkg, link, "Kick"); return

    # ==================================================
    # [1] QUẢN LÝ PID VÀ TỐI ƯU HÓA TIẾN TRÌNH
    # ==================================================
    current_pid = get_pkg_pid(pkg)
    if current_pid:
        if pid != current_pid:
            with STATUS_LOCK:
                if pkg in GLOBAL_STATUS: GLOBAL_STATUS[pkg]["pid"] = current_pid
            optimize_roblox_process(current_pid)

    # ==================================================
    # [2] THU THẬP NHỊP TIM (LOCAL SERVER / FILE LOG)
    # ==================================================
    hb_server_ts = 0
    if "APIHeartbeatManager" in globals():
        cloud_data = APIHeartbeatManager.get_user_status(uid_str) or \
                     APIHeartbeatManager.get_user_status(username_str) or \
                     APIHeartbeatManager.get_user_status(pkg)
        if cloud_data:
            hb_server_ts = cloud_data.get("last_seen", 0)

    # Nhịp tim THỰC TẾ nhận được từ Local Server hoặc File trên đĩa (không tính timestamp khởi động mặc định)
    file_ts, _ = _read_hb_file(pkg, file_id)
    actual_hb_recv = max(hb_server_ts, file_ts)

    # CHỈ CÔNG NHẬN LÀ RUNNING KHI CÓ NHỊP TIM THỰC TẾ GỬI VỀ SAU THỜI ĐIỂM GỬI LỆNH LAUNCH (+1.5s margin)
    if actual_hb_recv > (launch_time + 1.5):
        _hbtimeout_grace.pop(pkg, None)
        _api_bad_count.pop(pkg, None)
        with STATUS_LOCK:
            if pkg in GLOBAL_STATUS:
                st2 = GLOBAL_STATUS[pkg]
                st2["first_hb"] = True
                if not st2.get("running_start_ts"):
                    st2["running_start_ts"] = now
                st2["last_heartbeat"] = max(st2.get("last_heartbeat", 0), actual_hb_recv)
                
                curr_st = str(st2.get("status", ""))
                if "Rejoin" not in curr_st and "Rej" not in curr_st and "Kick" not in curr_st:
                    st2["status"] = "Running"
                
        last_hb_ts = actual_hb_recv
        first_hb   = True

    # ==================================================
    # [2.5] CHU KỲ TREO & NGHỈ (WORK & REST CYCLE)
    # ==================================================
    if first_hb:
        farm_mins, farm_rnd, rest_mins, rest_rnd = get_tab_cycle_config(pkg)
        if farm_mins > 0:
            with STATUS_LOCK:
                r_start_ts = st_dict.get("running_start_ts") or launch_time
                target_farm = st_dict.get("target_farm_mins")
                if not target_farm:
                    if farm_rnd > 0:
                        jitter = random.uniform(-float(farm_rnd), float(farm_rnd))
                        target_farm = max(1.0, round(farm_mins + jitter, 1))
                    else:
                        target_farm = float(farm_mins)
                    st_dict["target_farm_mins"] = target_farm

            time_in_game_mins = (now - r_start_ts) / 60.0
            if time_in_game_mins >= target_farm:
                if rest_mins > 0:
                    if rest_rnd > 0:
                        jitter_rest = random.uniform(-float(rest_rnd), float(rest_rnd))
                        target_rest = max(1.0, round(rest_mins + jitter_rest, 1))
                    else:
                        target_rest = float(rest_mins)
                    
                    target_rest_until = now + target_rest * 60.0
                    with STATUS_LOCK:
                        st_dict["status"] = "Resting"
                        st_dict["rest_until_ts"] = target_rest_until
                        st_dict["target_farm_mins"] = None
                        st_dict["running_start_ts"] = None
                        st_dict["first_hb"] = False
                        st_dict["pid"] = None
                    
                    kill_and_force_stop(pkg)
                    time_rest_end = time.strftime('%H:%M', time.localtime(target_rest_until))
                    log_console(f"\n{C_WARN}⏰ Tab {username_str or pkg} : Treo xong {time_in_game_mins:.1f}m. Nghỉ {target_rest:.1f}m (đến {time_rest_end})!{C_RESET}")
                    return
                else:
                    with STATUS_LOCK:
                        st_dict["target_farm_mins"] = None
                    log_console(f"\n{C_WARN}⏰ Tab {username_str or pkg} : {target_farm:.1f}m ({time_in_game_mins:.1f}m). Kill & Rejoin!{C_RESET}")
                    rejoin(pkg, link, f"Cycle({int(target_farm)}m)")
                    return

    # ==================================================
    # [3] ROBLOX API CHECK (BẤT ĐỒNG BỘ)
    # ==================================================
    if perform_net_check and first_hb and not _DELTA_BYPASSING:
        last_chk = last_api_check.get(pkg, 0)
        if now - last_chk > API_CHECK_INTERVAL:
            last_api_check[pkg] = now
            uid_api, ck = u.get("id"), u.get("cookie")
            if uid_api and ck:
                def _do_async_api_check(p_pkg, p_link, p_uid, p_ck):
                    if _DELTA_BYPASSING:
                        last_api_check[p_pkg] = 0
                        return
                    try:
                        presence = RobloxManager.check_user_online(p_uid, p_ck)
                        if presence is None:
                            last_api_check[p_pkg] = 0
                            return
                        is_ingame = (presence == 2)
                        with STATUS_LOCK:
                            if p_pkg in GLOBAL_STATUS:
                                if is_ingame:
                                    GLOBAL_STATUS[p_pkg]["api_verified"] = True
                                    GLOBAL_STATUS[p_pkg]["api"] = "OK"
                                else:
                                    GLOBAL_STATUS[p_pkg]["api"] = "BAD"

                        with STATUS_LOCK:
                            st_d = GLOBAL_STATUS.get(p_pkg, {})
                            curr_verified = st_d.get("api_verified", False)
                            curr_hb = st_d.get("first_hb", False)
                            r_start_ts = st_d.get("running_start_ts") or time.time()
                        
                        if not curr_verified and curr_hb and not _DELTA_BYPASSING:
                            elapsed_running = time.time() - r_start_ts
                            if elapsed_running > 320:
                                log_console(f"\n{C_ERR}🚨 [Roblox API] Tài khoản {p_pkg} không xác nhận In-Game sau 320s Running. Rejoin!{C_RESET}")
                                rejoin(p_pkg, p_link, "API: bad")
                    except Exception:
                        last_api_check[p_pkg] = 0

                threading.Thread(
                    target=_do_async_api_check,
                    args=(pkg, link, uid_api, ck),
                    daemon=True,
                    name=f"ApiCheck-{pkg[-8:]}"
                ).start()

    # ==================================================
    # [4] XỬ LÝ 2 TRƯỜNG HỢP TIMEOUT (LOAD_TIMEOUT & HEARTBEAT_TIMEOUT)
    # ==================================================
    if _DELTA_BYPASSING:
        return

    elapsed_launch = now - launch_time

    # TRƯỜNG HỢP A: Chưa từng nhận Heartbeat (Đang ở Pending hoặc Joining)
    if not first_hb:
        with STATUS_LOCK:
            curr_st = str(GLOBAL_STATUS.get(pkg, {}).get("status", ""))
        # Nếu tài khoản vẫn chưa tới lượt gọi lệnh launch (đang ở Pending), giữ nguyên Pending!
        if "Pending" in curr_st:
            return

        # Nếu đã được gọi lệnh launch vào game (Joining) mà quá LOAD_TIMEOUT -> Rejoin!
        if elapsed_launch > LOAD_TIMEOUT:
            rejoin(pkg, link, f"Ket_Join(>{int(LOAD_TIMEOUT)}s)")
            return
        return

    # TRƯỜNG HỢP B: Đang ở Running (Đã từng nhận nhịp tim đầu tiên)
    last_hb_real = st_dict.get("last_heartbeat", 0)
    lag_sec = int(now - last_hb_real) if last_hb_real > 0 else int(now - launch_time)

    if lag_sec > HEARTBEAT_TIMEOUT:
        _hbtimeout_grace.pop(pkg, None)
        rejoin(pkg, link, f"HBTimeout({lag_sec}s)")
        return


def setup_all_tab_coordinates(pkgs_or_links):
    """Bắt đầu set kích thước và sắp xếp toàn bộ file XML cửa sổ trước khi cho bất kỳ tab nào vào game."""
    if not SORT_TAB_ENABLED:
        return
    log_console(f"{C_WARN}   [⏳] Sort tab...{C_RESET}")
    pkgs = []
    for item in pkgs_or_links:
        if isinstance(item, (tuple, list)):
            pkgs.append(item[0])
        elif isinstance(item, dict):
            pkgs.append(item.get("package"))
        elif isinstance(item, str):
            pkgs.append(item)
            
    pkgs = sorted(list(set([p for p in pkgs if p])))
    for idx, pkg in enumerate(pkgs):
        try:
            sort_app_coordinates(pkg, idx)
            time.sleep(0.3)
        except Exception:
            pass


def run_launch_manager(links_to_start: list):
    global _DELTA_BYPASSING
    
    # NẾU BẬT SORT TAB: Xử lý hết toàn bộ file XML kích thước cửa sổ trước khi cho bất kỳ tab nào vào game
    if SORT_TAB_ENABLED:
        setup_all_tab_coordinates(links_to_start)

    for p, l in links_to_start:
        if _DELTA_BYPASSING:
            while _DELTA_BYPASSING:
                time.sleep(2.0)
        
        with STATUS_LOCK:
            st_dict = GLOBAL_STATUS.get(p, {})
            st = st_dict.get("status", "")
            first_hb = st_dict.get("first_hb", False)
            rest_until = st_dict.get("rest_until_ts", 0)

        # Bỏ qua nếu tab đang trong thời gian nghỉ
        if st == "Resting" or rest_until > time.time():
            continue

        # Bỏ qua nếu tab đã nhận được nhịp tim / đang ở trạng thái Running
        if first_hb or "Running" in st:
            continue

        now_ts = time.time()
        with STATUS_LOCK:
            if p in GLOBAL_STATUS:
                GLOBAL_STATUS[p]["status"] = "Joining"
                GLOBAL_STATUS[p]["launch_time"] = now_ts
                GLOBAL_STATUS[p]["last_heartbeat"] = 0

        # Gọi lệnh vào game tuần tự từng tab một
        success = launch_acc(p, l, wait=False)
        if not success:
            with STATUS_LOCK:
                if p in GLOBAL_STATUS:
                    GLOBAL_STATUS[p]["status"] = "Rejoin"
                    GLOBAL_STATUS[p]["launch_time"] = time.time()

        time.sleep(max(1.0, float(LAUNCH_DELAY)))



def run_unified_farm_loop(links: list):
    global _DASHBOARD_ACTIVE, _DELTA_BYPASSING
    _DASHBOARD_ACTIVE = True
    
    if DELTA_AUTO_KEY_ENABLED and has_executor_path():
        if not has_valid_delta_license():
            _DELTA_BYPASSING = True
        else:
            _DELTA_BYPASSING = False
    else:
        _DELTA_BYPASSING = False
            
    now_init = time.time()
    for p, l in links:
        with STATUS_LOCK:
            if p not in GLOBAL_STATUS:
                GLOBAL_STATUS[p] = {"status": "Pending", "api": "Wait", "link": l, "launch_time": now_init, "last_heartbeat": now_init}
            else:
                GLOBAL_STATUS[p]["link"] = l
    try:
        # Chạy Manager khởi động ngầm
        threading.Thread(target=run_launch_manager, args=(links,), daemon=True, name="LaunchManager").start()

        # Vòng lặp hợp nhất tuần tự (Radar + Dashboard + Webhook)
        last_webhook_time = 0
        os.system('cls' if os.name == 'nt' else 'clear')
        last_term_size = None

        while True:
            now = time.time()
            update_running_pids_cache()
            
            # 1. Quét trạng thái tuần tự
            for pkg, link in links:
                try:
                    check_single_account_api(pkg, link, now, True)
                except Exception:
                    pass

            # 2. Vẽ giao diện Dashboard mới (Bảng 5 cột độc đáo, tông xanh lá nhẹ nhàng)
            try:
                sys_info = SystemMonitor.get_full_stats()

                # Palette màu Xanh Lá Nhẹ Nhàng (Soft Sage / Mint Green)
                C_BORDER  = "\033[1;38;5;120m" # Mint Light Green
                C_BORDER2 = "\033[38;5;114m"   # Sage Green
                C_BORDER3 = "\033[38;5;78m"    # Meadow Green
                C_TITLE   = "\033[1;38;5;157m" # Light Pastel Mint
                C_INFO    = "\033[38;5;120m"   # Sage Info
                C_TXT     = "\033[38;5;252m"   # Soft Off-white Text
                C_GREEN   = "\033[1;38;5;120m" # Soft Green
                C_YELLOW  = "\033[1;38;5;220m" # Soft Amber
                C_RED     = "\033[1;38;5;203m" # Soft Red
                C_BLUE    = "\033[1;38;5;75m"  # Soft Blue
                C_GRAY    = "\033[38;5;245m"   # Soft Gray
                C_LOG     = "\033[38;5;246m"   # Log Gray
                C_RST     = "\033[0m"

                b_pipe = f"{C_BORDER2}│{C_RST}"
                top_b  = f"{C_BORDER}┌" + "─" * 58 + f"┐{C_RST}"
                mid_b  = f"{C_BORDER2}├" + "─" * 58 + f"┤{C_RST}"

                sep_hdr = f"{C_BORDER2}├" + "─"*4 + "┬" + "─"*18 + "┬" + "─"*6 + "┬" + "─"*27 + f"┤{C_RST}"
                sep_row = f"{C_BORDER2}├" + "─"*4 + "┼" + "─"*18 + "┼" + "─"*6 + "┼" + "─"*27 + f"┤{C_RST}"
                bot_b   = f"{C_BORDER3}└" + "─"*4 + "┴" + "─"*18 + "┴" + "─"*6 + "┴" + "─"*27 + f"┘{C_RST}"

                buf = []
                # Logo NONAME dạng chữ khối ở đầu Dashboard
                noname_lines = [
                    "██   ██  ██████  ██   ██  ██████  ███    ███ ███████",
                    "███  ██ ██    ██ ███  ██ ██    ██ ████  ████ ██     ",
                    "████ ██ ██    ██ ████ ██ ████████ ██ ████ ██ █████  ",
                    "██ ████ ██    ██ ██ ████ ██    ██ ██  ██  ██ ██     ",
                    "██  ███  ██████  ██  ███ ██    ██ ██      ██ ███████"
                ]
                c_noname = [
                    "\033[1;38;5;157m",
                    "\033[1;38;5;120m",
                    "\033[1;38;5;114m",
                    "\033[1;38;5;78m",
                    "\033[1;38;5;72m"
                ]
                for color, line in zip(c_noname, noname_lines):
                    buf.append(f"  {color}{line}{C_RST}")
                buf.append("")

                buf.append(top_b)
                # Dòng 1: Thông số hệ thống
                sys_str = f"CPU: {sys_info['cpu']}%  │  RAM: {sys_info['ram_used_gb']} GB  │  Uptime: {sys_info['uptime']}"
                buf.append(f"{b_pipe}{C_INFO}" + sys_str.center(58) + f"{C_RST}{b_pipe}")
                buf.append(sep_hdr)

                # Header Bảng 4 Cột
                hdr_line = (
                    f"{b_pipe}{C_TITLE}" + "#".center(4) + f"{C_RST}"
                    f"{b_pipe}{C_TITLE}" + " Account Name".ljust(18) + f"{C_RST}"
                    f"{b_pipe}{C_TITLE}" + " Ping".center(6) + f"{C_RST}"
                    f"{b_pipe}{C_TITLE}" + " Status".ljust(27) + f"{C_RST}"
                    f"{b_pipe}"
                )
                buf.append(hdr_line)
                buf.append(sep_row)

                with STATUS_LOCK:
                    snapshot = list(GLOBAL_STATUS.items())

                def _st_pri(item):
                    st_val = item[1].get("status", "")
                    rest_u = item[1].get("rest_until_ts", 0)
                    if st_val == "Resting" or rest_u > time.time():
                        return 4
                    s = map_to_three_states(st_val)
                    return 0 if s == "Running" else (1 if s == "Joining" else (2 if s == "Pending" else 3))
                snapshot.sort(key=_st_pri)

                for idx, (pkg, info_d) in enumerate(snapshot, 1):
                    u = user_data.get(pkg, {})
                    name = u.get("username", "Unknown")
                    if name and name != "Unknown":
                        if len(name) <= 1:
                            disp_n = name + "****"
                        elif len(name) == 2:
                            disp_n = name[0] + "****" + name[1]
                        else:
                            disp_n = name[:2] + "****" + name[-1]
                    else:
                        disp_n = (".." + pkg[-14:]) if len(pkg) > 16 else pkg
                    
                    disp_n = disp_n[:16]

                    st = info_d.get("status", "")
                    rest_u = info_d.get("rest_until_ts", 0)
                    is_resting = (st == "Resting") or (rest_u > time.time())

                    disp_s = map_to_three_states(st)

                    last_hb = info_d.get("last_heartbeat", 0)
                    launch_t = info_d.get("launch_time", time.time())
                    first_hb = info_d.get("first_hb", False)
                    if first_hb and last_hb > 0:
                        ping = int(time.time() - last_hb)
                    else:
                        ping = int(time.time() - launch_t)
                    p_str = f"{ping}s" if ping < 999 else "999+"
                    c_p = C_GREEN if ping < 60 else C_RED

                    if is_resting:
                        rem_m = max(1, int((rest_u - time.time()) / 60.0))
                        sym, c_s = "💤", C_CYAN
                        disp_s = f"Bé ơi ngủ đi dêm đã khuya rồi ({rem_m}m)"
                        p_str = "--"
                        c_p = C_GRAY
                    elif disp_s == "Running":
                        sym, c_s = "●", C_GREEN
                    elif disp_s == "Joining":
                        sym, c_s = "●", C_YELLOW
                    elif disp_s == "Rejoin":
                        sym, c_s = "●", C_RED
                    else:
                        sym, c_s = "○", C_BLUE

                    c1_str = str(idx).center(4)
                    c2_str = f" {disp_n}".ljust(18)
                    c3_str = p_str.center(6)
                    c4_str = f" {sym} {disp_s}".ljust(27)

                    row_str = (
                        f"{b_pipe}{C_GRAY}{c1_str}{C_RST}"
                        f"{b_pipe}{C_TXT}{c2_str}{C_RST}"
                        f"{b_pipe}{c_p}{c3_str}{C_RST}"
                        f"{b_pipe}{c_s}{c4_str}{C_RST}"
                        f"{b_pipe}"
                    )
                    buf.append(row_str)

                buf.append(bot_b)

                # Dòng Trạng thái tính năng (Nằm bên dưới Bảng và trên Log)
                wh_st = f"{C_GREEN}ON{C_RST}" if ("http" in WEBHOOK_URL and MONITOR_ENABLED) else f"{C_RED}OFF{C_RST}"
                st_st = f"{C_GREEN}ON{C_RST}" if SORT_TAB_ENABLED else f"{C_RED}OFF{C_RST}"
                ab_st = f"{C_GREEN}ON{C_RST}" if AUTO_BLOCK_ENABLED else f"{C_RED}OFF{C_RST}"
                ak_st = f"{C_GREEN}ON{C_RST}" if DELTA_AUTO_KEY_ENABLED else f"{C_RED}OFF{C_RST}"

                buf.append(f"  {C_TXT}HB Timeout: {C_GREEN}{HEARTBEAT_TIMEOUT}s{C_RST}  │  {C_TXT}Join Timeout: {C_GREEN}{LOAD_TIMEOUT}s{C_RST}")
                buf.append(f"  {C_TXT}Webhook: {wh_st}  │  {C_TXT}Sort Tab: {st_st}  │  {C_TXT}Auto Block: {ab_st}  │  {C_TXT}Auto Bypass : {ak_st}")

                # Hiển thị các log gần nhất trực tiếp bên dưới bảng
                with _CONSOLE_LOGS_LOCK:
                    logs_snap = list(_CONSOLE_LOGS)[-4:]
                if logs_snap:
                    buf.append("")
                    for lg in logs_snap:
                        clean_lg = lg.replace('\n', ' ')
                        buf.append(f"  {C_LOG}> {clean_lg}{C_RST}")

                # Atomic write: clear rồi dump 1 lần
                if os.name != 'nt':
                    os.system('stty sane 2>/dev/null; clear')
                else:
                    os.system('cls')
                sys.stdout.write("\r\n".join(buf) + "\r\n")
                sys.stdout.flush()
            except Exception:
                pass

            # 3. Gửi Webhook Discord (nếu đến hạn)
            if "http" in WEBHOOK_URL and MONITOR_ENABLED:
                if now - last_webhook_time >= WEBHOOK_INTERVAL * 60:
                    last_webhook_time = now
                    send_webhook_background()

            _DASHBOARD_REFRESH_EVENT.wait(5.0)
            _DASHBOARD_REFRESH_EVENT.clear()
    finally:
        _DASHBOARD_ACTIVE = False



# Tải danh sách hàm Menu và các chức năng phụ trợ


# ==========================================
#    MENU FUNCTIONS
# ==========================================
def get_cookie_menu():
    global WEBHOOK_URL  # Gọi biến Webhook dùng chung
    OUT_FILE = "/sdcard/Download/extracted_cookies.txt"

    pkgs = scan_packages()
    selected = []
    
    # Kế thừa: Tự động tick các app đã chọn từ Lựa chọn 2 nếu có
    if os.path.exists(SELECTED_PACKAGES_FILE):
        try:
            with open(SELECTED_PACKAGES_FILE) as f:
                for x in json.load(f):
                    selected.append(x["package"])
        except: pass

    # Giao diện chọn list app bằng phím
    while True:
        Utilities.clear_screen(); Utilities.print_header()
        print(f"{C_MAIN}=== CHỌN APP ĐỂ GET COOKIE ==={C_RESET}")
        for i, p in enumerate(pkgs[:50]):
            mark = f"{C_MAIN}[X]{C_RESET}" if p in selected else "[ ]"
            print(f"{C_SUB}{i+1}. {mark} {p}{C_RESET}")
        print("-" * 30)
        sel = c_input(f"{C_MAIN}Số (0=Bắt đầu Get, all=Chọn hết): {C_RESET}").strip()
        if sel == "0": break
        if sel == "all": selected = list(pkgs); continue
        if sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(pkgs):
                t = pkgs[idx]
                if t in selected: selected.remove(t)
                else: selected.append(t)

    if not selected:
        return

    print(f"\n{C_WARN}>> Đã chốt {len(selected)} App. get cookie ............ {C_RESET}")
    time.sleep(1)

    results = []
    for pkg in selected:
        print(f"[*] Quét {pkg:<25} ... ", end="")
        sys.stdout.flush()

        # Logic trích xuất Cookie siêu tốc
        db_src = f"/data/data/{pkg}/app_webview/Default/Cookies"
        db_tmp = f"/sdcard/Download/tmp_ck_{pkg.replace('.','_')}.db"

        run_cmd(f"cp '{db_src}' '{db_tmp}' 2>/dev/null")
        run_cmd(f"cp '{db_src}-wal' '{db_tmp}-wal' 2>/dev/null")
        run_cmd(f"cp '{db_src}-shm' '{db_tmp}-shm' 2>/dev/null")
        run_cmd(f"chmod 777 '{db_tmp}'* 2>/dev/null")

        cookie_val = None
        if os.path.exists(db_tmp):
            try:
                conn = sqlite3.connect(db_tmp)
                cur = conn.cursor()
                cur.execute("SELECT value FROM cookies WHERE name='.ROBLOSECURITY'")
                row = cur.fetchone()
                if row:
                    cookie_val = row[0]
                conn.close()
            except:
                pass
            finally:
                run_cmd(f"rm -f '{db_tmp}'* 2>/dev/null")

        if cookie_val:
            # Check Username API Roblox
            uname = None
            try:
                sess = requests.Session()
                sess.cookies.set(".ROBLOSECURITY", cookie_val, domain=".roblox.com")
                sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

                r_csrf = sess.post("https://auth.roblox.com/v2/logout", timeout=5)
                if r_csrf.headers.get("x-csrf-token"):
                    sess.headers.update({"x-csrf-token": r_csrf.headers.get("x-csrf-token")})

                r = sess.get("https://users.roblox.com/v1/users/authenticated", timeout=5)
                if r.status_code == 200:
                    uname = r.json().get("name")
            except:
                pass

            if uname:
                print(f"{C_MAIN}THÀNH CÔNG ({uname}){C_RESET}")
                results.append(f"{uname}:{cookie_val}")
            else:
                print(f"{C_ERR}COOKIE DEAD / MẠNG LAG{C_RESET}")
        else:
            print(f"{C_WARN}KHÔNG CÓ COOKIE{C_RESET}")

    print(f"\n{C_MAIN}{'='*45}{C_RESET}")

    # Ghi file và đẩy Webhook
    if results:
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(results))
        print(f"{C_MAIN}✅ Đã thu hoạch {len(results)} Cookie!{C_RESET}")
        print(f"{C_MAIN}✅ Đã lưu vào: {OUT_FILE}{C_RESET}\n")

        # Kiểm tra nếu dùng Webhook chung thì mới gửi
        if WEBHOOK_URL and "http" in WEBHOOK_URL:
            print(f"{C_WARN}[*] Đang đóng gói gửi file về Discord...{C_RESET}")
            try:
                with open(OUT_FILE, "rb") as f:
                    payload = {"content": f"🎯 **BÁO CÁO THU HOẠCH!** Đã hút được **{len(results)}** Cookies thành công!"}
                    requests.post(WEBHOOK_URL, data=payload, files={"file": ("Extracted_Cookies.txt", f, "text/plain")})
                print(f"{C_MAIN}🚀 Đã gửi lên Discord thành công!{C_RESET}")
            except Exception as e:
                print(f"{C_ERR}[!] Gửi Webhook thất bại: {e}{C_RESET}")
        else:
            print(f"{C_WARN}⚠️ Không tìm thấy Link Webhook (Lựa chọn 4). Đã bỏ qua bước gửi lên Discord.{C_RESET}")
    else:
        print(f"{C_ERR}❌ không lấy được Cookie nào!{C_RESET}")

    c_input(f"\n{C_MAIN}Hoàn tất. Enter để quay lại...{C_RESET}")


def tab_cycle_settings_menu():
    global TAB_CYCLE_MINUTES, AUTO_CYCLE_MINUTES, CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS
    while True:
        Utilities.clear_screen(); Utilities.print_header()
        print(f"{C_MAIN}=== CÀI ĐẶT CHU KỲ TREO & NGHỈ RIÊNG TỪNG ACCOUNT ==={C_RESET}")
        gen_desc = format_cycle_desc(AUTO_CYCLE_MINUTES, CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS)
        print(f"{C_SUB}Cấu hình chung mặc định: {C_CYAN}{gen_desc}{C_RESET}\n")

        pkgs = []
        if os.path.exists(SELECTED_PACKAGES_FILE):
            try:
                with open(SELECTED_PACKAGES_FILE) as f:
                    for x in json.load(f):
                        pkgs.append(x["package"])
            except:
                pass

        if not pkgs:
            try:
                pkgs = scan_packages()
            except:
                pass

        if not pkgs:
            print(f"{C_WARN}Chưa tìm thấy tài khoản hoặc gói ứng dụng nào!{C_RESET}")
            c_input("Ấn Enter để quay lại...")
            break

        for idx, pkg in enumerate(pkgs, 1):
            u = user_data.get(pkg, {})
            name = u.get("username", pkg)
            custom = TAB_CYCLE_MINUTES.get(pkg)
            if custom is not None:
                if isinstance(custom, dict):
                    f = custom.get("farm", AUTO_CYCLE_MINUTES)
                    fr = custom.get("farm_random", CYCLE_FARM_RANDOM_MINS)
                    r = custom.get("rest", CYCLE_REST_MINUTES)
                    rr = custom.get("rest_random", CYCLE_REST_RANDOM_MINS)
                    st_txt = f"{C_CYAN}{format_cycle_desc(f, fr, r, rr)} (Riêng){C_RESET}"
                elif isinstance(custom, (int, float)):
                    if custom > 0:
                        st_txt = f"{C_CYAN}Treo {custom}m (Riêng){C_RESET}"
                    else:
                        st_txt = f"{C_ERR}Disabled (Tắt riêng tab này){C_RESET}"
                else:
                    st_txt = f"{C_SUB}{gen_desc} (Dùng chung){C_RESET}"
            else:
                st_txt = f"{C_SUB}{gen_desc} (Dùng chung){C_RESET}"
            print(f"{C_MAIN}{idx}. {name} ({pkg[-12:]}) -> {st_txt}")

        print(f"\n{C_SUB}all. Cài đặt nhanh cho TẤT CẢ các tab{C_RESET}")
        print(f"{C_SUB}d-all. Xóa cấu hình riêng TẤT CẢ tab (về mặc định chung){C_RESET}")
        print(f"{C_SUB}0. Lưu & Quay lại{C_RESET}\n" + "-"*30)
        c = c_input(f"{C_MAIN}Chọn Tab (1-{len(pkgs)} / all / d-all / 0): {C_RESET}").strip().lower()
        if c in ("0", "b", "back", "exit", "q"):
            FileManager.save_config()
            break

        if c == "d-all":
            TAB_CYCLE_MINUTES.clear()
            FileManager.save_config()
            print(f"{C_MAIN}[OK] Đã xóa toàn bộ cấu hình riêng, tất cả tab dùng chung mặc định!{C_RESET}")
            time.sleep(1.2)
            continue

        if c == "all":
            print(f"\n{C_MAIN}=== CÀI ĐẶT CHU KỲ CHO TẤT CẢ {len(pkgs)} TAB ==={C_RESET}")
            f_in = c_input(f"{C_MAIN}1. Số phút Treo (0 để Tắt chu kỳ, Enter={AUTO_CYCLE_MINUTES}): {C_RESET}").strip()
            if f_in == "":
                f_val = AUTO_CYCLE_MINUTES
            elif f_in.isdigit():
                f_val = int(f_in)
            else:
                continue

            if f_val == 0:
                for p in pkgs:
                    TAB_CYCLE_MINUTES[p] = {"farm": 0, "farm_random": 0, "rest": 0, "rest_random": 0}
                print(f"{C_MAIN}[OK] Đã TẮT chu kỳ cho tất cả {len(pkgs)} tab!{C_RESET}")
                FileManager.save_config()
                time.sleep(1.2)
                continue

            fr_in = c_input(f"{C_MAIN}2. Random sai số Treo (± phút, Enter={CYCLE_FARM_RANDOM_MINS}): {C_RESET}").strip()
            fr_val = int(fr_in) if fr_in.isdigit() else CYCLE_FARM_RANDOM_MINS

            r_in = c_input(f"{C_MAIN}3. Số phút Nghỉ sau khi Treo (0 = không nghỉ, Enter={CYCLE_REST_MINUTES}): {C_RESET}").strip()
            r_val = int(r_in) if r_in.isdigit() else CYCLE_REST_MINUTES

            rr_val = 0
            if r_val > 0:
                rr_in = c_input(f"{C_MAIN}4. Random sai số Nghỉ (± phút, Enter={CYCLE_REST_RANDOM_MINS}): {C_RESET}").strip()
                rr_val = int(rr_in) if rr_in.isdigit() else CYCLE_REST_RANDOM_MINS

            cfg_dict = {"farm": f_val, "farm_random": fr_val, "rest": r_val, "rest_random": rr_val}
            for p in pkgs:
                TAB_CYCLE_MINUTES[p] = dict(cfg_dict)
            desc = format_cycle_desc(f_val, fr_val, r_val, rr_val)
            print(f"{C_MAIN}[OK] Đã áp dụng cho toàn bộ {len(pkgs)} tab: {desc}!{C_RESET}")
            FileManager.save_config()
            time.sleep(1.5)
            continue

        if c.isdigit():
            choice_idx = int(c) - 1
            if 0 <= choice_idx < len(pkgs):
                target_pkg = pkgs[choice_idx]
                name = user_data.get(target_pkg, {}).get("username", target_pkg)
                cur_f, cur_fr, cur_r, cur_rr = get_tab_cycle_config(target_pkg)
                has_custom = target_pkg in TAB_CYCLE_MINUTES

                print(f"\n{C_MAIN}Cài đặt Chu kỳ Treo & Nghỉ cho Tab: {C_GREEN}{name}{C_RESET}")
                if has_custom:
                    print(f"{C_SUB}- Nhập 'd' để XÓA cấu hình riêng (quay về dùng chung mặc định).{C_RESET}")
                print(f"{C_SUB}- Nhập 0 để TẮT tự động kill/nghỉ cho Tab này.{C_RESET}\n")

                f_in = c_input(f"{C_MAIN}1. Số phút Treo (hiện tại: {cur_f}m, Enter={cur_f}): {C_RESET}").strip().lower()
                if f_in == "d":
                    TAB_CYCLE_MINUTES.pop(target_pkg, None)
                    print(f"{C_MAIN}[OK] Đã xóa cấu hình riêng cho {name}, quay lại dùng chung.{C_RESET}")
                    FileManager.save_config()
                    time.sleep(1.2)
                    continue

                if f_in == "":
                    f_val = cur_f
                elif f_in.isdigit():
                    f_val = int(f_in)
                else:
                    continue

                if f_val == 0:
                    TAB_CYCLE_MINUTES[target_pkg] = {"farm": 0, "farm_random": 0, "rest": 0, "rest_random": 0}
                    print(f"{C_MAIN}[OK] Đã TẮT chu kỳ cho {name}!{C_RESET}")
                    FileManager.save_config()
                    time.sleep(1.2)
                    continue

                fr_in = c_input(f"{C_MAIN}2. Random sai số Treo (± phút, hiện tại: {cur_fr}m, Enter={cur_fr}): {C_RESET}").strip()
                fr_val = int(fr_in) if fr_in.isdigit() else cur_fr

                r_in = c_input(f"{C_MAIN}3. Số phút Nghỉ sau khi Treo (0 = không nghỉ, hiện tại: {cur_r}m, Enter={cur_r}): {C_RESET}").strip()
                r_val = int(r_in) if r_in.isdigit() else cur_r

                rr_val = 0
                if r_val > 0:
                    rr_in = c_input(f"{C_MAIN}4. Random sai số Nghỉ (± phút, hiện tại: {cur_rr}m, Enter={cur_rr}): {C_RESET}").strip()
                    rr_val = int(rr_in) if rr_in.isdigit() else cur_rr

                TAB_CYCLE_MINUTES[target_pkg] = {
                    "farm": f_val,
                    "farm_random": fr_val,
                    "rest": r_val,
                    "rest_random": rr_val
                }
                desc = format_cycle_desc(f_val, fr_val, r_val, rr_val)
                print(f"{C_MAIN}[OK] Đã set riêng cho {name}: {desc}!{C_RESET}")
                FileManager.save_config()
                time.sleep(1.5)



def config_tool_menu():
    global LAUNCH_DELAY, WAKEUP_DELAY, HEARTBEAT_TIMEOUT, LOAD_TIMEOUT
    global SORT_TAB_ENABLED, TERMUX_BOOT_ENABLED
    global AUTO_CYCLE_MINUTES, CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS, TAB_CYCLE_MINUTES
    while True:
        Utilities.clear_screen(); Utilities.print_header()
        st_on = "\033[1;38;5;120mON\033[0m"
        st_off = "\033[1;38;5;203mOFF\033[0m"
        sort_st = st_on if SORT_TAB_ENABLED else st_off
        boot_st = st_on if TERMUX_BOOT_ENABLED else st_off
        cycle_st = format_cycle_desc(AUTO_CYCLE_MINUTES, CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS)

        print(f"{C_MAIN}=== CONFIG TOOL ==={C_RESET}")
        print(f"{C_SUB}1. Launch Delay            : {C_CYAN}{LAUNCH_DELAY}s{C_RESET}")
        print(f"{C_SUB}2. Wake-up Delay           : {C_CYAN}{WAKEUP_DELAY}s{C_RESET}")
        print(f"{C_SUB}3. Heartbeat Timeout       : {C_CYAN}{HEARTBEAT_TIMEOUT}s{C_RESET}")
        print(f"{C_SUB}4. Load Timeout            : {C_CYAN}{LOAD_TIMEOUT}s{C_RESET}")
        print(f"{C_SUB}5. Chu kỳ Treo & Nghỉ (Chung) : {C_CYAN}{cycle_st}{C_RESET}")
        print(f"{C_SUB}6. Set Chu kỳ từng Tab     : {C_CYAN}Cài đặt riêng từng Tab{C_RESET}")
        print(f"{C_SUB}7. Sort Tab (Sắp xếp)      : {sort_st}")
        print(f"{C_SUB}8. Termux:Boot (Tự chạy)   : {boot_st}")
        print(f"{C_SUB}0. Save & Back{C_RESET}\n" + "-"*30)
        c = c_input(f"{C_MAIN}Select: {C_RESET}").strip()
        if c == "1":
            try:
                val = float(c_input(f"{C_MAIN}Seconds (current {LAUNCH_DELAY}s): {C_RESET}"))
                if 0 <= val <= 60:
                    LAUNCH_DELAY = val
            except: pass
        elif c == "2":
            try:
                val = float(c_input(f"{C_MAIN}Seconds (current {WAKEUP_DELAY}s): {C_RESET}"))
                if 0 <= val <= 10:
                    WAKEUP_DELAY = val
            except: pass
        elif c == "3":
            try:
                val = int(c_input(f"{C_MAIN}Seconds (current {HEARTBEAT_TIMEOUT}s): {C_RESET}"))
                if val >= 0:
                    HEARTBEAT_TIMEOUT = val
            except: pass
        elif c == "4":
            try:
                val = int(c_input(f"{C_MAIN}Seconds (current {LOAD_TIMEOUT}s): {C_RESET}"))
                if val >= 0:
                    LOAD_TIMEOUT = val
            except: pass
        elif c == "5":
            try:
                print(f"\n{C_MAIN}=== CÀI ĐẶT CHU KỲ TREO & NGHỈ (CHUNG) ==={C_RESET}")
                print(f"{C_SUB}Ví dụ: Treo 90p (±10p), Nghỉ 120p (±10p){C_RESET}\n")
                f_in = c_input(f"{C_MAIN}1. Số phút Treo chung (0 để Tắt, hiện tại {AUTO_CYCLE_MINUTES}m): {C_RESET}").strip()
                if f_in.isdigit():
                    f_val = int(f_in)
                    if f_val == 0:
                        AUTO_CYCLE_MINUTES = 0
                        print(f"{C_MAIN}[OK] Đã TẮT chu kỳ Treo & Nghỉ chung!{C_RESET}")
                    else:
                        AUTO_CYCLE_MINUTES = f_val
                        fr_in = c_input(f"{C_MAIN}2. Random sai số Treo (± phút, hiện tại {CYCLE_FARM_RANDOM_MINS}m, Enter={CYCLE_FARM_RANDOM_MINS}): {C_RESET}").strip()
                        CYCLE_FARM_RANDOM_MINS = int(fr_in) if fr_in.isdigit() else CYCLE_FARM_RANDOM_MINS

                        r_in = c_input(f"{C_MAIN}3. Số phút Nghỉ sau khi Treo (0 = không nghỉ, hiện tại {CYCLE_REST_MINUTES}m, Enter={CYCLE_REST_MINUTES}): {C_RESET}").strip()
                        CYCLE_REST_MINUTES = int(r_in) if r_in.isdigit() else CYCLE_REST_MINUTES

                        if CYCLE_REST_MINUTES > 0:
                            rr_in = c_input(f"{C_MAIN}4. Random sai số Nghỉ (± phút, hiện tại {CYCLE_REST_RANDOM_MINS}m, Enter={CYCLE_REST_RANDOM_MINS}): {C_RESET}").strip()
                            CYCLE_REST_RANDOM_MINS = int(rr_in) if rr_in.isdigit() else CYCLE_REST_RANDOM_MINS
                        else:
                            CYCLE_REST_RANDOM_MINS = 0

                        desc = format_cycle_desc(AUTO_CYCLE_MINUTES, CYCLE_FARM_RANDOM_MINS, CYCLE_REST_MINUTES, CYCLE_REST_RANDOM_MINS)
                        print(f"{C_MAIN}[OK] Đã lưu cấu hình chung: {desc}!{C_RESET}")
                    FileManager.save_config()
                    time.sleep(1.5)
            except: pass
        elif c == "6":
            tab_cycle_settings_menu()
        elif c == "7":
            SORT_TAB_ENABLED = not SORT_TAB_ENABLED
        elif c == "8":
            TERMUX_BOOT_ENABLED = not TERMUX_BOOT_ENABLED
            setup_termux_boot(TERMUX_BOOT_ENABLED)
        elif c == "0":
            FileManager.save_config()
            print(f"{C_MAIN}[OK] Saved Config!{C_RESET}")
            time.sleep(1)
            break



def webhook_settings_menu():
    global WEBHOOK_URL, DEVICE_NAME, WEBHOOK_INTERVAL, MONITOR_ENABLED
    while True:
        Utilities.clear_screen(); Utilities.print_header()
        st_on = "\033[1;38;5;120mON\033[0m"
        st_off = "\033[1;38;5;203mOFF\033[0m"
        mon_st = st_on if MONITOR_ENABLED else st_off

        print(f"{C_MAIN}=== WEBHOOK SETTINGS ==={C_RESET}")
        url_p = (WEBHOOK_URL[:20] + "...") if len(WEBHOOK_URL) > 20 else WEBHOOK_URL
        print(f"{C_SUB}1. URL: {C_CYAN}{url_p}{C_RESET}\n"
              f"{C_SUB}2. Name: {C_CYAN}{DEVICE_NAME}{C_RESET}\n"
              f"{C_SUB}3. Interval: {C_CYAN}{WEBHOOK_INTERVAL} min{C_RESET}\n"
              f"{C_SUB}4. Status: {mon_st}\n"
              f"{C_SUB}0. Save & Back{C_RESET}\n" + "-"*30)
        c = c_input(f"{C_MAIN}Select: {C_RESET}").strip()
        if   c == "1": WEBHOOK_URL   = c_input(f"{C_MAIN}URL: {C_RESET}").strip()
        elif c == "2": DEVICE_NAME   = c_input(f"{C_MAIN}Name: {C_RESET}").strip()
        elif c == "3":
            try: WEBHOOK_INTERVAL = int(c_input(f"{C_MAIN}Interval: {C_RESET}").strip())
            except: pass
        elif c == "4": MONITOR_ENABLED = not MONITOR_ENABLED
        elif c == "0": FileManager.save_config(); break



def auto_fix_network_on_start():
    run_cmd("svc power stayon true")
    run_cmd("settings put system screen_off_timeout 2147483647")
    if os.path.exists(HOSTS_FILE):
        try:
            with open(HOSTS_FILE, "r") as f:
                content = f.read()
            if "rbxcdn.com" in content:
                if os.path.exists(f"{HOSTS_FILE}.bak"):
                    run_cmd(f"cp {HOSTS_FILE}.bak {HOSTS_FILE}")
                else:
                    run_cmd(f"echo '127.0.0.1 localhost' > {HOSTS_FILE}")
                    run_cmd(f"echo '::1 localhost' >> {HOSTS_FILE}")
        except: pass


# ==========================================
#    TÍNH NĂNG MỚI: DEEP SCAN & CONFIG
# ==========================================
def deep_scan_roblox() -> list:
    print(f"{C_SUB}[*] Đang lấy danh sách ứng dụng từ hệ thống...{C_RESET}")
    cmd_out = get_cmd_output("pm list packages -f")
    
    all_apps = []
    for line in cmd_out.splitlines():
        if line.startswith("package:"):
            parts = line.replace("package:", "").split("=")
            if len(parts) >= 2:
                package_name = parts[-1]
                apk_path = "=".join(parts[:-1])
                app_dir = os.path.dirname(apk_path)
                all_apps.append({
                    "package_name": package_name,
                    "app_dir": app_dir
                })
    
    print(f"{C_SUB}[*] Found a total of {len(all_apps)} application packages....{C_RESET}")
    roblox_clones = []
    for app in all_apps:
        p_name = app["package_name"]
        p_dir = app["app_dir"]
        
        # Quét chuyên sâu tìm file .so đặc trưng của Roblox
        check_cmd = f"find {p_dir} -type f -name '*Roblox*.so' -o -name '*roblox*.so' 2>/dev/null"
        found = get_cmd_output(check_cmd)
        
        if found.strip():
            print(f"{C_MAIN}[+] Detect : {p_name} {C_RESET}")
            roblox_clones.append(p_name)
            
    return roblox_clones

def config_accounts_flow(pkgs: list):
    """Hàm xử lý Chọn App và Cài đặt Game"""
    selected = []
    
    if os.path.exists(SELECTED_PACKAGES_FILE):
        try:
            with open(SELECTED_PACKAGES_FILE) as f:
                for x in json.load(f):
                    selected.append(x["package"])
        except: pass

    while True:
        Utilities.clear_screen(); Utilities.print_header()
        print(f"{C_MAIN}=== SELECT PACKAGES ==={C_RESET}")
        for i, p in enumerate(pkgs[:50]):
            mark = f"{C_MAIN}[X]{C_RESET}" if p in selected else "[ ]"
            print(f"{C_SUB}{i+1}. {mark} {p}{C_RESET}")
        print("-" * 30)
        sel = c_input(f"{C_MAIN}Số (0=Lưu, all=Chọn hết, A=Tự động chọn & lưu): {C_RESET}").strip()
        if sel == "0": 
            break
        if sel.lower() in ("a", "auto"):
            print(f"\n{C_MAIN}[*] Scan all files on the device {C_RESET}")
            roblox_pkgs = deep_scan_roblox()
            if roblox_pkgs:
                selected = roblox_pkgs
                print(f"{C_MAIN}[+] detect {len(selected)}  Roblox !{C_RESET}")
                time.sleep(1.5)
                break
            else:
                print(f"{C_ERR}[!] No Roblox pack was found through Deep Scan. Please select manually.{C_RESET}")
                time.sleep(2.0)
                continue
        if sel == "all": 
            selected = list(pkgs); continue
        if sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(pkgs):
                t = pkgs[idx]
                if t in selected: selected.remove(t)
                else: selected.append(t)

    # NẾU CÓ APP ĐƯỢC CHỌN THÌ TIẾP TỤC CHỌN GAME
    if selected:
        games_list = [
            ("Blox Fruit",                   "2753915549"),
            ("Escape Tsunami For Brainrots", "131623223084840"),
            ("Survive LAVA for Brainrots",   "119987266683883"),
            ("99 Nights in the Forest",      "79546208627805"),
            ("Fish It",                      "121864768012064"),
            ("Bee Swarm Simulator",          "1537690962"),
            ("Adopt Me",                     "920587237"),
            ("Steal a Brainrot",             "109983668079237"),
            ("Abyss",                        "127794225497302"),
            ("Grow A Garden 2",              "97598239454123"), 
        ]

        while True:
            Utilities.clear_screen(); Utilities.print_header()
            print(f"{C_MAIN}=== CHẾ ĐỘ CÀI ĐẶT GAME ==={C_RESET}")
            print(f"{C_SUB}1. Cài Đặt CHUNG (Tất cả acc vào chung 1 game/link){C_RESET}")
            print(f"{C_SUB}2. Cài Đặt RIÊNG (Chọn game/link cho TỪNG acc){C_RESET}")
            print("-" * 30)
            mode_sel = c_input(f"{C_MAIN}Chọn (1 hoặc 2): {C_RESET}").strip()
            if mode_sel in ["1", "2"]:
                break

        final = []

        def get_game_choice(pkg_name="TẤT CẢ ACC"):
            place_id  = "2753915549" 
            link_code = ""
            raw_url   = "" 
            
            while True:
                Utilities.clear_screen(); Utilities.print_header()
                print(f"{C_MAIN}=== CHỌN GAME CHO [{pkg_name}] ==={C_RESET}")
                for idx, (g_name, _) in enumerate(games_list):
                    print(f"{C_SUB}{idx+1}. {g_name}{C_RESET}")
                print(f"{C_SUB}{len(games_list)+1}. Link Server VIP ")
                print(f"{C_SUB}{len(games_list)+2}. PlaceID thủ công{C_RESET}\n" + "-"*30)
                g_sel = c_input(f"{C_MAIN}Chọn: {C_RESET}").strip()
                
                if g_sel.isdigit():
                    g_idx = int(g_sel)
                    if 1 <= g_idx <= len(games_list):
                        place_id = games_list[g_idx-1][1]; break
                    elif g_idx == len(games_list) + 1:
                        ui = c_input(f"{C_MAIN}Dán Link (Web/Share/VIP): {C_RESET}").strip()
                        if ui.startswith("http"): raw_url = ui
                        else: link_code = ui
                        break
                    elif g_idx == len(games_list) + 2:
                        ui = c_input(f"{C_MAIN}PlaceID: {C_RESET}").strip()
                        if ui.isdigit(): place_id = ui
                        break
            return raw_url if raw_url else (place_id, link_code)

        if mode_sel == "1":
            res = get_game_choice("TẤT CẢ ACC")
            for p in selected:
                if isinstance(res, str): 
                    final.append({"package": p, "raw_link": res})
                else:
                    final.append({"package": p, "placeid": res[0], "linkcode": res[1]})
        else:
            for p in selected:
                res = get_game_choice(p)
                if isinstance(res, str):
                    final.append({"package": p, "raw_link": res})
                else:
                    final.append({"package": p, "placeid": res[0], "linkcode": res[1]})

        with open(SELECTED_PACKAGES_FILE, "w") as f:
            json.dump(final, f, indent=4)
        print(f"{C_MAIN}Saved!{C_RESET}"); time.sleep(1)


# ==========================================
#    RAM CLEANER (Chạy định kỳ 15 phút để chống lag lâu dài)
# ==========================================
def ram_cleaner_thread():
    import gc
    while True:
        try:
            # 1. Thu gom rác bộ nhớ Python ngầm
            gc.collect()

            # 2. Xóa drop_caches hệ thống (tuyệt đối không bóp hay giới hạn RAM của ứng dụng Roblox)
            if IS_ROOT:
                run_cmd("sync; echo 3 > /proc/sys/vm/drop_caches")
                active_pkgs = []
                active_pids = []
                with STATUS_LOCK:
                    for p, v in GLOBAL_STATUS.items():
                        if "Run" in v.get("status", ""):
                            active_pkgs.append(p)
                            pid_val = v.get("pid")
                            if pid_val:
                                active_pids.append(pid_val)
                if active_pkgs:
                    cmds = "; ".join([f"am send-trim-memory {pkg} RUNNING_LOW"
                                      for pkg in active_pkgs])
                    run_cmd(cmds)
                # Giữ ứng dụng Roblox ở cpuset foreground mà KHÔNG can thiệp Nice/Priority
                for pid in active_pids:
                    optimize_roblox_process(pid)

            # 3. Dọn dẹp cache PID cũ hết hạn để tiết kiệm tài nguyên
            now = time.time()
            with STATUS_LOCK:
                running_pids = {v.get("pid") for v in GLOBAL_STATUS.values() if v.get("pid")}
            expired_keys = [k for k, (pid, ts) in list(_pid_cache.items()) if pid not in running_pids and now - ts > 60]
            for k in expired_keys:
                _pid_cache.pop(k, None)
        except Exception:
            pass
        time.sleep(300)


def get_file_owner_group_helper(path):
    try:
        cmd = f"su -c \"stat -c '%U:%G' {path}\""
        result = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True).strip()
        if ":" in result:
            return result
    except:
        pass
    return None

def modify_preferences_xml(pkg: str, left: int = 0, top: int = 44, right: int = 1280, bottom: int = 667) -> bool:
    """
    Sửa đổi cấu hình XML của App Cloner để đặt cửa sổ ở chế độ Full Screen.
    (Bê nguyên từ delta_auto_key.py)
    """
    prefs_path = f"/data/data/{pkg}/shared_prefs/{pkg}_preferences.xml"
    xml_content = get_cmd_output(f"cat {prefs_path}")
    if not xml_content or not xml_content.strip():
        return False

    coord_mappings = {
        "app_cloner_current_window_left": left,
        "app_cloner_current_window_top": top,
        "app_cloner_current_window_right": right,
        "app_cloner_current_window_bottom": bottom
    }

    new_xml_lines = []
    updated_keys = set()
    
    for line in xml_content.split('\n'):
        line_updated = False
        for key, value in coord_mappings.items():
            if f'name="{key}"' in line:
                new_line = re.sub(r'value="[^"]*"', f'value="{value}"', line)
                new_xml_lines.append(new_line)
                updated_keys.add(key)
                line_updated = True
                break
        if not line_updated:
            new_xml_lines.append(line)

    for key, value in coord_mappings.items():
        if key not in updated_keys:
            for idx, line in enumerate(new_xml_lines):
                if "</map>" in line:
                    new_xml_lines.insert(idx, f'    <int name="{key}" value="{value}" />')
                    break

    new_xml_content = '\n'.join(new_xml_lines)

    temp_path = f"/sdcard/temp_preferences_{pkg}.xml"
    temp_local = f"temp_preferences_{pkg}.xml"
    try:
        with open(temp_local, "w", encoding="utf-8") as f:
            f.write(new_xml_content)
        run_cmd(f"cp {temp_local} {temp_path}")
        run_cmd(f"cp {temp_path} {prefs_path}")
        run_cmd(f"rm -f {temp_path}")
        if os.path.exists(temp_local):
            os.remove(temp_local)
    except Exception as e:
        return False

    owner_group = get_cmd_output(f"stat -c '%U:%G' /data/data/{pkg}/shared_prefs").strip()
    if not owner_group:
        owner_group = get_cmd_output(f"stat -c '%U:%G' {prefs_path}").strip()
    if not owner_group:
        owner_group = "u0_a123:u0_a123"

    run_cmd(f"chown {owner_group} {prefs_path}")
    run_cmd(f"chmod 660 {prefs_path}")
    return True

def sort_app_coordinates(pkg: str, index: int):
    """Tự động tính toán toạ độ và cập nhật vào XML cấu hình dựa trên số lượng tab đang chạy."""
    try:
        import math
        # 1. Lấy danh sách các tài khoản đang chạy thực tế trong farm của main.py
        active_pkgs = sorted(list(GLOBAL_STATUS.keys()))
        n = len(active_pkgs)
        if n == 0:
            if os.path.exists(SELECTED_PACKAGES_FILE):
                try:
                    with open(SELECTED_PACKAGES_FILE) as f:
                        d = json.load(f)
                        active_pkgs = sorted([i["package"] for i in d])
                        n = len(active_pkgs)
                except:
                    pass
        if n == 0:
            n = 1
            
        # Lấy chỉ mục chính xác của pkg trong danh sách đang chạy
        if pkg in active_pkgs:
            real_index = active_pkgs.index(pkg)
        else:
            real_index = index
            
        # 2. Lấy độ phân giải màn hình ngang
        width, height = 1920, 1080
        try:
            out = get_cmd_output("wm size")
            if "Physical size:" in out:
                parts = out.split(": ")[1].strip().split("x")
                width = int(parts[0])
                height = int(parts[1])
        except Exception:
            pass
        w_width = max(width, height)
        w_height = min(width, height)
        
        # 3. Tính toán kích thước lưới — chừa vùng an toàn cho status bar (trên) và Termux (dưới)
        safe_top = 82           # status bar
        margin_bottom = 120     # chừa cho Termux phía dưới
        gap = 10                # khoảng cách giữa các hàng
        
        if n <= 3:
            cols = n
            rows = 1
        else:
            cols = math.ceil(n / 2.0)
            rows = 2
        
        # Vùng khả dụng thực tế SAU KHI trừ status bar + margin Termux
        usable_h = w_height - safe_top - margin_bottom - (gap * (rows - 1))
        usable_w = w_width
        
        cell_w = usable_w / cols
        cell_h = usable_h / rows
        
        # Ép tỉ lệ landscape tối thiểu 1.2 để Roblox không bị bóp dọc
        target_ratio = 1.2
        if cell_w / cell_h < target_ratio:
            cell_h = cell_w / target_ratio
            
        r = real_index // cols
        c = real_index % cols
        
        left = int(c * cell_w)
        top = int(safe_top + r * (cell_h + gap))
        right = int(left + cell_w)
        bottom = int(top + cell_h)
        
        # Clamp: không cho tràn phải
        if right > w_width:
            right = w_width
            left = max(0, right - int(cell_w))
            
        # Clamp: không cho tràn xuống vùng Termux
        max_bottom = w_height - margin_bottom
        if bottom > max_bottom:
            bottom = max_bottom
            top = max(safe_top, bottom - int(cell_h))
            
        modify_preferences_xml(pkg, left, top, right, bottom)
    except Exception as e:
        pass


# ==========================================
#    MAIN
# ==========================================
def main():
    init_system()
    try:
        CookieDB.init_db()
        user_data.update(CookieDB.load_all_cookies())
    except:
        pass
    auto_fix_network_on_start()
    verify_system_and_key()

    for target, daemon_flag, t_name in [
        (start_local_monitor_server, True, "LocalMonitor"),
        (ram_cleaner_thread,         True, "RamCleaner"),
        (hwid_auto_setter_loop,      True, "HwidAutoSetter"),
        (delta_license_checker_loop, True, "DeltaLicenseChecker"),
        (send_server_host_telemetry, True, "ServerHostTelemetry")
    ]:
        t = threading.Thread(target=target, daemon=daemon_flag, name=t_name)
        t.start()
        
    global stop_webhook_thread, AUTO_BLOCK_ENABLED, DELTA_AUTO_KEY_ENABLED, SAVED_HWID

    # =========================================================
    # [TÍNH NĂNG MỚI]: BẮT LỆNH AUTO TỪ TERMINAL/CMD
    # =========================================================
    is_auto_mode = len(sys.argv) > 1 and sys.argv[1].lower() in ['auto', '--auto', '-a']

    while True:
        # NẾU CÓ CỜ AUTO -> TỰ ĐỘNG GÕ PHÍM 1 THAY CHO NGƯỜI DÙNG
        if is_auto_mode:
            print(f"\n{C_MAIN}🚀 AUTO MODE KÍCH HOẠT: Tự động tải cấu hình và vào Farm...{C_RESET}")
            time.sleep(1.5)
            c = "1"
            is_auto_mode = False # Tắt cờ để lỡ ngắt Farm (Ctrl+C) nó sẽ hiện Menu chứ không kẹt auto
        else:
            Utilities.clear_screen(); Utilities.print_header(); Utilities.print_menu_box()
            c = c_input(f"\n{C_MAIN}>> {C_RESET}").strip()

        if c == "1":
            global SERVER_TELEMETRY_ENABLED
            SERVER_TELEMETRY_ENABLED = True
            if not os.path.exists(SELECTED_PACKAGES_FILE):
                print("Chưa có config! Hãy chọn số 2 trước."); time.sleep(1); SERVER_TELEMETRY_ENABLED = False; continue
            try:
                with open(SELECTED_PACKAGES_FILE) as f:
                    d = json.load(f)
            except:
                SERVER_TELEMETRY_ENABLED = False
                continue



            # Helper vẽ màn hình chuẩn bị gọn gàng (Phong cách Xanh Lá Nhẹ Nhàng)
            def _prep_screen(step_name, progress, detail=""):
                Wp = 45
                c_border = "\033[1;38;5;120m"
                c_title  = "\033[1;38;5;157m"
                c_sub    = "\033[38;5;114m"
                c_detail = "\033[38;5;252m"
                r = C_RESET

                top_b = f"{c_border}┌{'─'*(Wp-2)}┐{r}"
                bot_b = f"{c_border}└{'─'*(Wp-2)}┘{r}"
                
                title_p = "❖  N O N A M E   R E J O I N  ❖"
                pad_t = max(0, (Wp - 2 - len(title_p)) // 2)
                pad_r = max(0, Wp - 2 - pad_t - len(title_p))

                lines_p = [
                    top_b,
                    f"{c_border}│{r}{' '*pad_t}{c_title}{title_p}{r}{' '*pad_r}{c_border}│{r}",
                    bot_b,
                    "",
                    f"  {c_sub}▸ {step_name} {C_SUB}({progress}){r}",
                ]
                if detail:
                    lines_p.append(f"  {c_detail}  > {detail}{r}")
                if os.name != 'nt':
                    os.system('stty sane 2>/dev/null; clear')
                else:
                    os.system('cls')
                sys.stdout.write("\r\n".join(lines_p) + "\r\n")
                sys.stdout.flush()

            # Khởi chạy thử và buộc dừng tất cả để chuẩn bị cookie sạch
            for idx, i in enumerate(d, 1):
                pkg = i["package"]
                _prep_screen("WAKE UP", f"{idx}/{len(d)}", pkg)
                RobloxManager.wake_up(pkg)
                time.sleep(8.0)

            for idx, i in enumerate(d, 1):
                pkg = i["package"]
                _prep_screen("KILL & CLEAR", f"{idx}/{len(d)}", pkg)
                run_cmd(f"am force-stop {pkg}", timeout_sec=5)
                RobloxManager.clear_cache(pkg)
                time.sleep(4.0)

            _prep_screen("WAIT", "...")
            time.sleep(2.0)

            for idx, i in enumerate(d, 1):
                pkg = i["package"]
                _prep_screen("GET USER", f"{idx}/{len(d)}", pkg)
                
                cached = user_data.get(pkg) or {}
                ck = cached.get("cookie") or RobloxManager.get_smart_cookie(pkg)
                uid = cached.get("id") or RobloxManager.get_user_id(pkg)
                name = cached.get("username") or "Unknown"
                if uid and ck and name == "Unknown":
                    name = RobloxManager.get_username(uid, pkg)
                
                user_data[pkg] = {"id": uid, "username": name, "cookie": ck}
                try:
                    CookieDB.save_cookie(pkg, uid, name, ck)
                except:
                    pass
                masked_name = name[:3] + "****" + name[-3:] if len(name) > 6 else name
                _prep_screen("GET USER", f"{idx}/{len(d)}", masked_name)
                time.sleep(0.5)

            with STATUS_LOCK:
                GLOBAL_STATUS.clear()
                for i in d:
                    GLOBAL_STATUS[i["package"]] = {
                        "status":        "Pending",
                        "api":           "Wait",
                        "last_heartbeat": time.time(),
                        "launch_time":   time.time(),
                        "pid":           None,
                        "last_cpu_time": 0,
                        "cpu_delta":     0,
                        "freeze_strikes": 0,
                        "first_hb":      False,
                    }

            links = []
            for i in d:
                if "raw_link" in i:
                    links.append((i["package"], i["raw_link"]))
                else:
                    pid   = i.get("placeid", "2753915549")
                    lcode = i.get("linkcode", "")
                    lnk   = (f"roblox://experiences/start?placeId={pid}&linkCode={lcode}"
                             if lcode else f"roblox://experiences/start?placeId={pid}")
                    links.append((i["package"], lnk))

            # ==================================================
            # [CHÈN VÀO ĐÂY]: AUTO BLOCK XỬ LÝ TRƯỚC KHI VÀO GAME
            # ==================================================
            if AUTO_BLOCK_ENABLED:
                print(f"{C_MAIN}>>>  AUTO BLOCK...{C_RESET}")
                block_data = {}
                for pkg, _ in links:
                    u = user_data.get(pkg, {})
                    ck = u.get("cookie") or RobloxManager.get_smart_cookie(pkg)
                    uid = u.get("id") or RobloxManager.get_user_id(pkg)
                    if uid and ck:
                        name = u.get("username", "Unknown")
                        if name == "Unknown":
                            name = RobloxManager.get_username(uid, pkg)
                        block_data[pkg] = {"id": uid, "name": name, "cookie": ck}
                        
                if block_data:
                    MutualBlocker.run(block_data)
                else:
                    print(f"{C_ERR}[!] Không lấy được Cookie từ App. Đang bỏ qua Auto Block.{C_RESET}")
                time.sleep(2)
            # ==================================================

            # Xóa sạch màn hình trước khi chuyển sang Dashboard
            os.system('cls' if os.name == 'nt' else 'clear')
            # Chạy trực tiếp vòng lặp hợp nhất trong luồng chính
            try:
                run_unified_farm_loop(links)
            except KeyboardInterrupt:
                print(f"\n{C_WARN}⚠️ Đang dừng Farm và quay lại Menu...{C_RESET}")
                time.sleep(1)
            finally:
                SERVER_TELEMETRY_ENABLED = False

        elif c == "2":
            # Cấu hình Accounts: Quét nhanh packages và mở giao diện chọn
            pkgs = scan_packages()
            config_accounts_flow(pkgs)

        elif c == "3":  
            webhook_settings_menu()

        elif c == "4":  
            if os.path.exists(SELECTED_PACKAGES_FILE):
                try:
                    with open(SELECTED_PACKAGES_FILE) as f:
                        for i in json.load(f):
                            RobloxManager.wake_up(i['package'])
                            time.sleep(5.0)
                except: pass
            c_input("Done. Press Enter...")

        elif c == "5":  
            config_tool_menu()

        elif c == "6":
            global SAVED_HWID
            while True:
                Utilities.clear_screen(); Utilities.print_header()
                print(f"{C_MAIN}=== CHANGE HWID ==={C_RESET}")
                print(f"{C_SUB}Auto-HWID hiện tại: {repr(SAVED_HWID) if SAVED_HWID else 'Chưa cài đặt (Disabled)'}{C_RESET}")
                print(f"{C_SUB}1. Arceus (no key) (Set: k3m9n2p7r4v6w8x1)\n2. CRYPTIC\n3. Tùy chọn\n0. Back{C_RESET}\n" + "-"*30)
                choice = c_input(f"{C_MAIN}Select: {C_RESET}").strip()
                if choice == "1":
                    new_id = "k3m9n2p7r4v6w8x1"
                    SAVED_HWID = new_id
                    FileManager.save_config()
                    get_cmd_output(f"settings put secure android_id {new_id}")
                    print(f"{C_MAIN}[OK] HWID Arceus: {new_id} (Đã lưu & tự động áp dụng){C_RESET}")
                    c_input("Enter...")
                elif choice == "2":
                    new_id = c_input(f"{C_MAIN}Nhập HWID CRYPTIC mới (Để trống để Tắt): {C_RESET}").strip()
                    SAVED_HWID = new_id
                    FileManager.save_config()
                    if new_id != "":
                        get_cmd_output(f"settings put secure android_id {new_id}")
                        print(f"{C_MAIN}[OK] HWID CRYPTIC: {new_id} (Đã lưu & tự động áp dụng){C_RESET}")
                    else:
                        print(f"{C_MAIN}[OK] Đã tắt tự động set HWID!{C_RESET}")
                    c_input("Enter...")
                elif choice == "3":
                    new_id = c_input(f"{C_MAIN}Nhập HWID Tùy chọn mới (Để trống để Tắt): {C_RESET}").strip()
                    SAVED_HWID = new_id
                    FileManager.save_config()
                    if new_id != "":
                        get_cmd_output(f"settings put secure android_id {new_id}")
                        print(f"{C_MAIN}[OK] HWID: {new_id} (Đã lưu & tự động áp dụng){C_RESET}")
                    else:
                        print(f"{C_MAIN}[OK] Đã tắt tự động set HWID!{C_RESET}")
                    c_input("Enter...")
                elif choice == "0":
                    break

        elif c == "7":  
            login_cookie_menu()

        elif c == "8":
            AUTO_BLOCK_ENABLED = not AUTO_BLOCK_ENABLED
            FileManager.save_config()
            print(f"{C_MAIN}Auto Mutual Block: {'ON' if AUTO_BLOCK_ENABLED else 'OFF'}{C_RESET}")
            time.sleep(1)

        elif c == "9": 
            get_cookie_menu()

        elif c == "36":
            global DELTA_AUTO_KEY_ENABLED
            DELTA_AUTO_KEY_ENABLED = not DELTA_AUTO_KEY_ENABLED
            FileManager.save_config()
            print(f"{C_MAIN}Delta Auto Key (Bypass): {'ON' if DELTA_AUTO_KEY_ENABLED else 'OFF'}{C_RESET}")
            time.sleep(1)


        elif c == "0":  
            sys.exit(0)

if __name__ == "__main__":
    main()
