# ============================================================
#  NEXORA CLOUD — Backend v2 (FULL FEATURES)
#  ✅ Start/Stop/Delete files
#  ✅ File rename + edit (text files)
#  ✅ Logs per file
#  ✅ Live pip install
#  ✅ Ban / Unban users (admin)
#  ✅ Code editor (read/write file content)
#  ✅ File upload from web
#  Auth: Firebase Auth | DB: Firebase RTDB | Bot: Telegram
# ============================================================

import os, json, uuid, math, secrets, threading, time, base64
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, Response
from werkzeug.utils import secure_filename as _secure_filename

# ─── FIREBASE CONFIG ───────────────────────────────────────
FIREBASE_DB_URL      = "https://hosting-website-bot-v9-v1-default-rtdb.asia-southeast1.firebasedatabase.app"
FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_WEB_API_KEY", "")  # Set in Vercel env vars

# ── Firebase Service Account: load from env var (NEVER hardcode keys!) ──────
# Set FIREBASE_SA_JSON in Vercel → Settings → Environment Variables
# Paste the entire JSON content of your service account key file as the value
_sa_json = os.environ.get("FIREBASE_SA_JSON", "")
if not _sa_json:
    raise RuntimeError(
        "❌ FIREBASE_SA_JSON environment variable not set!\n"
        "   Go to Vercel → Settings → Environment Variables\n"
        "   Add FIREBASE_SA_JSON = (paste your service account JSON here)"
    )
try:
    FIREBASE_SA = json.loads(_sa_json)
except json.JSONDecodeError as e:
    raise RuntimeError(f"❌ FIREBASE_SA_JSON is not valid JSON: {e}")

# ─── TELEGRAM REMOVED (v8) ──────────────────────────────────
# No Telegram needed — bot works via Firebase UID

# ─── SET YOUR UID AFTER FIRST LOGIN ─────────────────────────
OWNER_UID = "YOgpfHRjEWRzYEtiFwXPzonKAUT2"   # paste your Firebase UID here

# ─── BOT CONFIG (Telegram removed in v8, kept for tg_link compat) ───────────
BOT_NAME  = "NexOraCloudbot"   # optional: your Telegram bot username (without @), or leave empty

# ─── FILE LIMITS ────────────────────────────────────────────
FILE_LIMITS = {
    "free":       {"count": 10,   "size_mb": 20},
    "subscribed": {"count": 50,   "size_mb": 50},
    "pro":        {"count": 100,  "size_mb": 100},
    "enterprise": {"count": 9999, "size_mb": 200},
    "admin":      {"count": 9999, "size_mb": 200},
    "owner":      {"count": 9999, "size_mb": 9999},
}

BLOCKED_EXTENSIONS = {
    ".exe",".bat",".apk",".jar",".bin",".iso",".img",
    ".ps1",".vbs",".com",".scr",".cmd",".dll",".pif",
    ".msi",".msp",".hta",".wsf",".wsh",".reg",".cpl",
    ".sys",".drv",".ocx",".elf",".so",".dex",".class",
}
# Binary executable magic bytes
MALWARE_SIGS = [
    b"MZ",           # Windows PE (EXE/DLL)
    b"\x7fELF",      # Linux ELF binary
    b"\xfe\xed\xfa", # Mach-O binary (macOS)
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"PK\x03\x04",  # ZIP/JAR/APK
    b"#!",           # block shebangs in dangerous contexts
    b"\xca\xfe\xba\xbe", # Java class file
    b"dex\n",       # Android DEX
]
# Dangerous keywords in file content
SUSPICIOUS_KW = [
    b"ransomware", b"trojan", b"virus", b"malware",
    b"backdoor", b"exploit", b"keylogger", b"rootkit",
    b"reverse_shell", b"bind_shell", b"shellcode",
    b"meterpreter", b"mimikatz", b"metasploit",
    b"cryptominer", b"coinminer", b"xmrig",
]
# Dangerous code patterns (Python/JS specific)
DANGEROUS_PATTERNS = [
    b"subprocess.Popen([\x22']rm ",
    b"os.system([\x22']rm -rf",
    b"shutil.rmtree([\x22']/",
    b"eval(base64",
    b"exec(base64",
    b"__import__([\x22']os[\x22']).system",
]


# ─── FILENAME SANITIZER ──────────────────────────────────────
def sanitize_filename(raw: str) -> str:
    """Sanitize uploaded filename: keep extension, prevent path traversal."""
    import re
    if not raw:
        return "untitled.txt"
    # Strip directory separators (path traversal prevention)
    raw = raw.replace("\\", "/").split("/")[-1]
    # Split name and extension
    if "." in raw:
        name, ext = raw.rsplit(".", 1)
        ext = ext.lower()
    else:
        name, ext = raw, "txt"
    # Remove dangerous chars from name: keep alphanumeric, dash, underscore, dot, space
    name = re.sub(r"[^\w\s\-.]", "", name).strip()
    if not name:
        name = "file"
    # Limit length
    name = name[:80]
    return f"{name}.{ext}"

TEXT_EDITABLE_EXT  = {".py",".js",".txt",".json",".yaml",".yml",".sh",
                      ".md",".env",".cfg",".ini",".toml",".ts",".html",".css"}

# ─── FLASK ──────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, 
            template_folder=os.path.join(ROOT_DIR, "templates"),
            static_folder=os.path.join(ROOT_DIR, "static"),
            static_url_path="/static")

app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
# ─── SIMPLE RATE LIMITER (no extra deps) ────────────────────
_rate_store   = defaultdict(list)   # ip -> [timestamps]
_rate_store_lock = threading.Lock()

def _check_rate_limit(key: str, max_calls: int, window_sec: int) -> bool:
    """Returns True if allowed, False if rate limit exceeded."""
    now = time.time()
    with _rate_store_lock:
        calls = _rate_store[key]
        # Remove expired timestamps
        calls = [t for t in calls if now - t < window_sec]
        _rate_store[key] = calls
        if len(calls) >= max_calls:
            return False
        calls.append(now)
        return True

def get_client_ip():
    return (request.headers.get("X-Forwarded-For","").split(",")[0].strip()
            or request.headers.get("X-Real-IP","")
            or request.remote_addr or "unknown")




# ─── SECURITY HEADERS ────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    # Only allow scripts/styles from same origin
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://www.gstatic.com https://apis.google.com https://fonts.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://*.googleapis.com https://*.firebaseio.com https://identitytoolkit.googleapis.com https://securetoken.googleapis.com; "
        "frame-src https://hosting-website-bot-v9-v1.firebaseapp.com https://accounts.google.com https://*.firebaseapp.com;"
    )
    return response

# ─── JSON ERROR HANDLERS (prevent HTML error pages from breaking frontend) ───
@app.errorhandler(400)
def handle_400(e):
    from flask import request as req, render_template as rt
    if req.path.startswith("/api/"): return jsonify({"success": False, "error": "Bad request"}), 400
    return rt("500.html"), 400

@app.errorhandler(403)
def handle_403(e):
    from flask import request as req, render_template as rt
    if req.path.startswith("/api/"): return jsonify({"success": False, "error": "Forbidden"}), 403
    return rt("403.html"), 403

@app.errorhandler(404)
def handle_404(e):
    from flask import request as req, render_template as rt
    if req.path.startswith("/api/"): return jsonify({"success": False, "error": "Not found"}), 404
    return rt("404.html"), 404

@app.errorhandler(500)
def handle_500(e):
    from flask import request as req, render_template as rt
    if req.path.startswith("/api/"): return jsonify({"success": False, "error": "Internal server error"}), 500
    return rt("500.html"), 500

@app.errorhandler(502)
def handle_502(e):
    from flask import request as req, render_template as rt
    if req.path.startswith("/api/"): return jsonify({"success": False, "error": "Bad gateway"}), 502
    return rt("502.html"), 502

@app.errorhandler(503)
def handle_503(e):
    from flask import request as req, render_template as rt
    if req.path.startswith("/api/"): return jsonify({"success": False, "error": "Service unavailable"}), 503
    return rt("maintenance.html"), 503

@app.errorhandler(Exception)
def handle_exception(e):
    from flask import request as req, render_template as rt
    import traceback
    print(f"[UNHANDLED] {traceback.format_exc()}")
    if req.path.startswith("/api/"): return jsonify({"success": False, "error": str(e) or "Server error"}), 500
    return rt("500.html"), 500

# ─── EXTRA PAGES ────────────────────────────────────────────────────────────
@app.route("/maintenance")
def serve_maintenance():
    from flask import render_template
    return render_template("maintenance.html")

# ─── FIREBASE INIT ──────────────────────────────────────────
_fb_db = None

def _init_firebase():
    global _fb_db
    try:
        import firebase_admin
        from firebase_admin import credentials, db as rtdb
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_SA)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
        _fb_db = rtdb
        print("✅ Firebase RTDB connected")
    except Exception as e:
        print(f"❌ Firebase init failed: {e}")

_init_firebase()

# ─── DB HELPERS ─────────────────────────────────────────────
def db_get(path):
    try: return _fb_db.reference(path).get()
    except Exception as e: print(f"[DB-GET] {path}: {e}"); return None

def db_set(path, value):
    try: _fb_db.reference(path).set(value); return True
    except Exception as e: print(f"[DB-SET] {path}: {e}"); return False

def db_push(path, value):
    try: ref = _fb_db.reference(path).push(value); return ref.key
    except Exception as e: print(f"[DB-PUSH] {path}: {e}"); return None

def db_update(path, updates):
    try: _fb_db.reference(path).update(updates); return True
    except Exception as e: print(f"[DB-UPDATE] {path}: {e}"); return False

def db_delete(path):
    try: _fb_db.reference(path).delete(); return True
    except Exception as e: print(f"[DB-DELETE] {path}: {e}"); return False

def db_list(path):
    try:
        r = _fb_db.reference(path).get()
        return r if isinstance(r, dict) else {}
    except Exception as e: print(f"[DB-LIST] {path}: {e}"); return {}

# ─── AUTH VERIFY ────────────────────────────────────────────
def verify_firebase_token(id_token):
    try:
        from firebase_admin import auth as fb_auth
        d = fb_auth.verify_id_token(id_token)
        return d["uid"], d.get("email",""), d.get("name","") or d.get("email","").split("@")[0]
    except Exception:
        return None, None, None

# ─── SESSION ────────────────────────────────────────────────
def _make_session(uid):
    user = db_get(f"/users/{uid}") or {}
    role = user.get("role", "free")
    if uid == OWNER_UID and OWNER_UID:
        role = "owner"
    return {
        "uid":        uid,
        "role":       role,
        "name":       user.get("name",""),
        "email":      user.get("email",""),
        "tg_chat_id": user.get("tg_chat_id"),
        "banned":     user.get("banned", False),
        "ban_reason": user.get("ban_reason", ""),
    }

def create_session(uid, user_data=None):
    sid = secrets.token_hex(24)
    sess_data = {
        "uid":        uid,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
    }
    # Cache key user fields in session to avoid extra DB call on each request
    if user_data:
        sess_data["role"]  = user_data.get("role", "free")
        sess_data["name"]  = user_data.get("name", "")
        sess_data["email"] = user_data.get("email", "")
        sess_data["banned"] = user_data.get("banned", False)
        sess_data["ban_reason"] = user_data.get("ban_reason", "")
    db_set(f"/sessions/{sid}", sess_data)
    return sid

def get_session(sid):
    if not sid: return None
    data = db_get(f"/sessions/{sid}")
    if not data: return None
    try:
        if datetime.utcnow() > datetime.fromisoformat(data["expires_at"]):
            db_delete(f"/sessions/{sid}")
            return None
    except Exception:
        pass
    # Use cached data from session if available (saves 1 Firebase call)
    if data.get("role"):
        sess = {
            "uid":         data["uid"],
            "role":        data["role"],
            "name":        data.get("name", ""),
            "email":       data.get("email", ""),
            "tg_chat_id":  data.get("tg_chat_id"),
            "banned":      data.get("banned", False),
            "ban_reason":  data.get("ban_reason", ""),
            "suspended":   False,
            "suspend_reason": "",
            "suspend_until": None,
        }
        # Override role if owner
        if data["uid"] == OWNER_UID and OWNER_UID:
            sess["role"] = "owner"
    else:
        sess = _make_session(data["uid"])
    if sess.get("banned"):
        return None   # banned user → treated as unauthorized
    if sess.get("suspended"):
        return None   # suspended user → treated as unauthorized
    return sess

def get_session_allow_banned(sid):
    """Like get_session but returns session even if user is banned (for appeal/auth/me)."""
    if not sid: return None
    data = db_get(f"/sessions/{sid}")
    if not data: return None
    try:
        if datetime.utcnow() > datetime.fromisoformat(data["expires_at"]):
            db_delete(f"/sessions/{sid}")
            return None
    except Exception:
        pass
    return _make_session(data["uid"])

# ─── AUTH DECORATORS ────────────────────────────────────────
def require_auth(f):
    @wraps(f)
    def _wrap(*a, **kw):
        sid  = request.headers.get("X-Session-Id") or request.cookies.get("nsid","")
        sess = get_session(sid)
        if not sess:
            return jsonify({"success":False,"error":"Unauthorized"}), 401
        request.sess = sess
        return f(*a, **kw)
    return _wrap

def require_admin(f):
    @wraps(f)
    def _wrap(*a, **kw):
        sid  = request.headers.get("X-Session-Id") or request.cookies.get("nsid","")
        sess = get_session(sid)
        if not sess:
            return jsonify({"success":False,"error":"Admin only"}), 403
        # ── Always verify role fresh from Firebase (session cache can be stale) ──
        fresh_user = db_get(f"/users/{sess['uid']}") or {}
        fresh_role = fresh_user.get("role", "free")
        if sess["uid"] == OWNER_UID and OWNER_UID:
            fresh_role = "owner"
        if fresh_role not in ("admin", "owner"):
            return jsonify({"success":False,"error":"Admin only"}), 403
        sess["role"] = fresh_role  # update to fresh role
        request.sess = sess
        return f(*a, **kw)
    return _wrap

def require_owner(f):
    @wraps(f)
    def _wrap(*a, **kw):
        sid  = request.headers.get("X-Session-Id") or request.cookies.get("nsid","")
        sess = get_session(sid)
        if not sess or sess["role"] != "owner":
            return jsonify({"success":False,"error":"Owner only"}), 403
        request.sess = sess
        return f(*a, **kw)
    return _wrap

# ─── NOTIFICATION HELPERS (Firebase-only, no Telegram) ──────
def tg_send(chat_id, text):
    """No-op stub — Telegram removed in v8. Notifications go via Firebase."""
    pass

def notify_owner(text):
    """No-op stub — Telegram removed in v8."""
    pass

def notify_user(uid, text):
    """Send notification to user via Firebase — website reads and shows toast."""
    try:
        db_push(f"/notifications/{uid}", {
            "text": text, "type": "info",
            "time": datetime.utcnow().isoformat(), "read": False,
        })
    except Exception:
        pass

def log_activity(uid, action, msg):
    icons = {"login":"👤","upload":"📤","delete":"🗑️","start":"▶️",
             "stop":"⏹️","subscribe":"💳","broadcast":"📢","malware":"☣️",
             "ban":"🚫","unban":"✅","pip":"📦","edit":"✏️","rename":"📝",
             "logs":"📋"}
    db_push("/activity", {
        "uid":    uid,
        "action": action,
        "icon":   icons.get(action,"ℹ️"),
        "msg":    msg,
        "time":   datetime.utcnow().isoformat(),
    })
    try:
        acts = db_list("/activity")
        if len(acts) > 200:
            oldest = sorted(acts.keys())[:-200]
            for k in oldest:
                db_delete(f"/activity/{k}")
    except Exception:
        pass

# ─── MALWARE SCAN ───────────────────────────────────────────
import re as _scan_re

def scan_file(data: bytes, fname: str):
    """
    Multi-layer file scanner. Returns (safe: bool, reason: str).
    Layers:
      1. Extension block
      2. Magic byte detection (binary executables)
      3. Suspicious keyword scan (first 8KB)
      4. Dangerous code pattern scan (text files only)
      5. Encoded payload detection (base64 blobs)
    """
    ext = os.path.splitext(fname)[1].lower()

    # Layer 1: Block by extension
    if ext in BLOCKED_EXTENSIONS:
        return False, f"Blocked file type: {ext}"

    # Layer 2: Magic bytes — detect binary executables
    # Allow ZIP only if it's not an APK/JAR by extension (already caught above)
    for sig in MALWARE_SIGS:
        if sig == b"#!":
            continue  # shebang OK in .sh files
        if data.startswith(sig):
            return False, "Binary executable detected"

    # Layer 3: Suspicious keyword scan (first 8KB)
    sample = data[:8192].lower()
    for kw in SUSPICIOUS_KW:
        if kw in sample:
            return False, f"Suspicious content detected: {kw.decode()}"

    # Layer 4: Dangerous code patterns in text files
    if ext in {".py", ".js", ".ts", ".sh", ".bash"}:
        text_sample = data[:16384]
        for pat in DANGEROUS_PATTERNS:
            if pat in text_sample:
                return False, "Dangerous code pattern detected"

        # Layer 5: Detect suspiciously large base64 blobs (encoded payloads)
        try:
            txt = text_sample.decode("utf-8", errors="ignore")
            b64_matches = _scan_re.findall(r"[A-Za-z0-9+/]{200,}={0,2}", txt)
            if b64_matches:
                return False, "Suspicious encoded payload detected"
        except Exception:
            pass

    return True, "OK"

# ─── BOT COMMAND QUEUE ──────────────────────────────────────
def queue_bot_command(uid, command, payload):
    cmd_id = str(uuid.uuid4())
    db_set(f"/bot_commands/{uid}/{cmd_id}", {
        "id":         cmd_id,
        "command":    command,
        "payload":    payload,
        "status":     "pending",
        "created_at": datetime.utcnow().isoformat(),
    })
    return cmd_id

def wait_for_result(uid, cmd_id, timeout=8):
    """Wait for hosting_bot.py to process a command.
    On Vercel serverless, this will often timeout — that's OK.
    The command stays queued and hosting_bot picks it up async.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = db_get(f"/bot_commands/{uid}/{cmd_id}")
        if r and r.get("status") in ("done","error"):
            db_delete(f"/bot_commands/{uid}/{cmd_id}")
            return r
        time.sleep(0.6)
    # Don't delete the command — let hosting_bot pick it up later
    return {"status":"timeout","error":"Bot is not responding. Make sure it is running and try again."}

# ─── FILE HELPERS ───────────────────────────────────────────
def append_file_log(uid, file_id, line):
    """Append a log line to /file_logs/{uid}/{file_id}"""
    entry = {
        "ts":  datetime.utcnow().isoformat(),
        "msg": str(line)[:500],
    }
    db_push(f"/file_logs/{uid}/{file_id}", entry)
    # Keep max 200 lines per file
    try:
        logs = db_list(f"/file_logs/{uid}/{file_id}")
        if len(logs) > 200:
            oldest = sorted(logs.keys())[:-200]
            for k in oldest:
                db_delete(f"/file_logs/{uid}/{file_id}/{k}")
    except Exception:
        pass

# ─── STATIC SERVING ─────────────────────────────────────────
def _read_html(name):
    # Try multiple locations — works both locally and on Vercel
    base = os.path.dirname(os.path.abspath(__file__))
    dirs = [
        base,                                    # same folder as app.py
        os.path.dirname(base),                   # parent folder (when called from api/)
        "/var/task",                             # Vercel root
        os.path.join("/var/task"),
        os.getcwd(),
    ]
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return f"<h1>404 — {name} not found</h1>"

@app.route("/")
def serve_index():
    from flask import render_template
    return render_template("index.html")

@app.route("/dashboard")
def serve_dashboard():
    from flask import render_template
    # Auth handled client-side (session stored in localStorage, not cookies)
    return render_template("dashboard.html")

@app.route("/admin")
def serve_admin():
    from flask import render_template
    # Auth handled client-side (session stored in localStorage, not cookies)
    return render_template("admin_panel.html")

# ═══════════════════════════════════════════════════════════
#  AUTH ROUTES
# ═══════════════════════════════════════════════════════════

# ─── LOG SANITIZER ───────────────────────────────────────────
import re as _logre
def sanitize_log_lines(lines):
    """Strip internal server paths from log output."""
    result = []
    for line in (lines or []):
        s = str(line)
        # File "/home/.../UUID/bot.py", line N -> File "bot.py", line N
        s = _logre.sub(r'File "/home/[^"]*/([^/"]+)"', r'File "\1"', s)
        s = _logre.sub(r'/home/[^/]+/[^/]+/[0-9a-zA-Z_\-]{12,}/', '', s)
        s = _logre.sub(r'/home/container/', '', s)
        result.append(s)
    return result

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    # ── Rate limit: 10 login attempts per IP per minute ──────
    ip = get_client_ip()
    if not _check_rate_limit(f"login:{ip}", max_calls=10, window_sec=60):
        return jsonify({"success":False,"error":"Too many login attempts. Please wait a minute."}), 429

    body     = request.get_json() or {}
    id_token = body.get("id_token","")
    if not id_token:
        return jsonify({"success":False,"error":"id_token required"}), 400

    uid, email, name = verify_firebase_token(id_token)
    if not uid:
        return jsonify({"success":False,"error":"Invalid token"}), 401

    user = db_get(f"/users/{uid}") or {}

    # Check if banned
    if user.get("banned"):
        return jsonify({"success":False,"error":"🚫 Your account has been banned. Contact support."}), 403

    # Check if suspended AND show_on_login is enabled
    if user.get("suspended") and user.get("suspend_show_on_login"):
        return jsonify({
            "success": False,
            "suspended": True,
            "suspend_reason": user.get("suspend_reason",""),
            "suspend_until": user.get("suspend_until"),
            "error": "🔒 Your account is suspended."
        }), 403

    role = user.get("role", "free")
    if uid == OWNER_UID and OWNER_UID:
        role = "owner"

    # Preserve custom display name if user already has one set
    existing_name = user.get("name", "")
    user.update({
        "uid":        uid,
        "email":      email,
        "name":       existing_name or name or email.split("@")[0],
        "role":       role,
        "last_login": datetime.utcnow().isoformat(),
    })
    if "created_at" not in user:
        user["created_at"] = datetime.utcnow().isoformat()

    db_set(f"/users/{uid}", user)
    sid = create_session(uid, user)

    log_activity(uid, "login", f"{user['name']} logged in")

    return jsonify({
        "success": True,
        "session_id": sid,
        "user": {
            "uid":        uid,
            "email":      email,
            "name":       user["name"],
            "role":       role,
            "tg_chat_id": user.get("tg_chat_id"),
            "tg_link":    (f"https://t.me/{BOT_NAME}?start={uid}" if BOT_NAME else None),
        }
    })


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    sid  = request.headers.get("X-Session-Id","") or request.cookies.get("nsid","")
    sess = get_session_allow_banned(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    uid  = sess["uid"]
    # Use cached session data — avoids a redundant Firebase read on every page load
    return jsonify({
        "success": True,
        "user": {
            "uid":            uid,
            "email":          sess.get("email",""),
            "name":           sess.get("name",""),
            "role":           sess["role"],
            "tg_chat_id":     sess.get("tg_chat_id"),
            "tg_link":        (f"https://t.me/{BOT_NAME}?start={uid}" if BOT_NAME else None),
            "banned":         sess.get("banned", False),
            "ban_reason":     sess.get("ban_reason", ""),
            "suspended":      sess.get("suspended", False),
            "suspend_reason": sess.get("suspend_reason", ""),
            "suspend_until":  sess.get("suspend_until"),
            "plan":           sess.get("plan"),
        }
    })


@app.route("/api/auth/logout", methods=["POST"])
@require_auth
def api_logout():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid","")
    db_delete(f"/sessions/{sid}")
    return jsonify({"success": True})


@app.route("/api/bots/init", methods=["GET"])
def api_bots_init():
    """Bootstrap endpoint for Bot Manager page.
    Returns auth + files + uptimes in a single request.
    Always returns JSON — never crashes the frontend with non-JSON errors.
    """
    try:
        sid  = request.headers.get("X-Session-Id","").strip() or request.cookies.get("nsid","").strip()

        # ── Auth check ─────────────────────────────────────────
        if not sid:
            return jsonify({
                "success": False,
                "error":   "Unauthorized",
                "code":    "NO_SESSION"
            }), 401

        sess = get_session(sid)
        if not sess:
            return jsonify({
                "success": False,
                "error":   "Session expired or invalid. Please log in again.",
                "code":    "SESSION_EXPIRED"
            }), 401

        uid  = sess["uid"]
        role = sess["role"]

        # ── Data fetch (graceful fallback on Firebase errors) ──
        try:
            all_f = db_list(f"/files/{uid}") or {}
        except Exception:
            all_f = {}

        files = sorted(all_f.values(), key=lambda x: x.get("upload_time",""), reverse=True)
        lim   = FILE_LIMITS.get(role, FILE_LIMITS["free"])

        try:
            folders = db_list(f"/folders/{uid}") or {}
        except Exception:
            folders = {}

        try:
            uptime_data = db_list(f"/bot_uptimes/{uid}") or {}
        except Exception:
            uptime_data = {}

        now     = datetime.utcnow()
        uptimes = {}
        for fid, entry in uptime_data.items():
            if isinstance(entry, dict):
                enriched = dict(entry)
                if entry.get("running") and entry.get("start_time"):
                    try:
                        st = datetime.fromisoformat(entry["start_time"])
                        enriched["uptime_sec"] = int((now - st).total_seconds())
                    except Exception:
                        enriched["uptime_sec"] = 0
                uptimes[fid] = enriched

        return jsonify({
            "success": True,
            "user": {
                "uid":   uid,
                "name":  sess.get("name",""),
                "email": sess.get("email",""),
                "role":  role,
            },
            "files":         files,
            "folders":       list(folders.values()),
            "count":         len(files),
            "limit":         lim["count"],
            "size_limit_mb": lim["size_mb"],
            "uptimes":       uptimes,
        })

    except Exception as e:
        # Last-resort catch — always return JSON so frontend never sees an HTML error page
        print(f"[api_bots_init] Unhandled error: {e}")
        return jsonify({
            "success": False,
            "error":   "Internal server error. Please try again.",
            "code":    "SERVER_ERROR"
        }), 500


# ═══════════════════════════════════════════════════════════
#  FILE ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/files/list", methods=["GET"])
@require_auth
def api_files_list():
    uid   = request.sess["uid"]
    role  = request.sess["role"]
    all_f = db_list(f"/files/{uid}") or {}
    files = sorted(all_f.values(), key=lambda x: x.get("upload_time",""), reverse=True)
    lim   = FILE_LIMITS.get(role, FILE_LIMITS["free"])
    folders = db_list(f"/folders/{uid}") or {}
    return jsonify({
        "success": True,
        "files":   files,
        "folders": list(folders.values()),
        "count":   len(files),
        "limit":   lim["count"],
        "size_limit_mb": lim["size_mb"],
    })


@app.route("/api/files/upload", methods=["POST"])
@require_auth
def api_files_upload():
    sess = request.sess
    uid  = sess["uid"]
    role = sess["role"]

    # ── Rate limit: 20 uploads per user per minute ────────────
    if not _check_rate_limit(f"upload:{uid}", max_calls=20, window_sec=60):
        return jsonify({"success":False,"error":"Too many uploads. Please wait a moment."}), 429

    f = request.files.get("file")
    if not f:
        return jsonify({"success":False,"error":"No file"}), 400

    lim     = FILE_LIMITS.get(role, FILE_LIMITS["free"])
    cur_cnt = len(db_list(f"/files/{uid}") or {})
    if cur_cnt >= lim["count"]:
        return jsonify({"success":False,"error":f"File limit reached ({cur_cnt}/{lim['count']})"}), 403

    file_bytes = f.read()
    size_mb    = len(file_bytes) / 1024 / 1024
    if size_mb > lim["size_mb"]:
        return jsonify({"success":False,"error":f"File too large ({size_mb:.1f}MB > {lim['size_mb']}MB limit)"}), 413

    # ── Sanitize filename BEFORE doing anything with it ──────
    safe_name = sanitize_filename(f.filename)

    safe, reason = scan_file(file_bytes, safe_name)
    if not safe:
        log_activity(uid, "malware", f"Blocked: {safe_name} — {reason}")
        notify_owner(f"☣️ <b>Malware blocked</b>\nUser: {sess['name']}\nFile: {safe_name}\nReason: {reason}")
        return jsonify({"success":False,"error":f"Security check failed: {reason}"}), 400

    file_id  = str(uuid.uuid4())
    size_str = f"{size_mb:.2f} MB" if size_mb >= 0.01 else f"{len(file_bytes)} B"
    ext      = os.path.splitext(safe_name)[1].lower()
    editable = ext in TEXT_EDITABLE_EXT

    # Store file content for editable text files
    file_content_b64 = base64.b64encode(file_bytes).decode()

    # Read folder_id from form (sent by frontend when user is inside a folder)
    folder_id = request.form.get("folder_id") or None
    # Validate folder_id belongs to user
    if folder_id:
        fld = db_get(f"/folders/{uid}/{folder_id}")
        if not fld:
            folder_id = None  # invalid/unknown folder, fall back to root

    file_data = {
        "id":          file_id,
        "uid":         uid,
        "name":        safe_name,
        "size":        size_str,
        "size_bytes":  len(file_bytes),
        "type":        ext.lstrip("."),
        "status":      "stopped",
        "editable":    editable,
        "upload_time": datetime.utcnow().isoformat(),
        "folder_id":   folder_id,
    }
    db_set(f"/files/{uid}/{file_id}", file_data)

    # Store content for editing AND recovery (text/runnable files, max 500KB)
    is_runnable = ext.lstrip(".") in ("py", "js")
    if (editable or is_runnable) and len(file_bytes) < 512000:
        db_set(f"/file_content/{uid}/{file_id}", {
            "content_b64": file_content_b64,
            "updated_at":  datetime.utcnow().isoformat(),
        })

    # Queue upload command to hosting_bot.py — auto_start=False (user must manually start)
    cmd_id = queue_bot_command(uid, "upload", {
        "file_name":        safe_name,
        "file_content_b64": file_content_b64,
        "file_id":          file_id,
        "auto_start":       False,
    })
    bot_msg = "✅ File uploaded! Bot is processing..."
    time.sleep(2)
    r = db_get(f"/bot_commands/{uid}/{cmd_id}")
    if r and r.get("status") == "done":
        # File saved successfully — do NOT auto-start, keep status as "stopped"
        # User must manually press Start button
        db_delete(f"/bot_commands/{uid}/{cmd_id}")
        bot_msg = "✅ File uploaded successfully! Press Start to run it."
    elif r and r.get("status") == "error":
        bot_msg = "✅ File saved. Note: " + r.get("error","")
        db_delete(f"/bot_commands/{uid}/{cmd_id}")

    log_activity(uid, "upload", f"{sess['name']} uploaded {safe_name} ({size_str})")

    return jsonify({"success":True,"message":bot_msg,"file":file_data})



@app.route("/api/files/create", methods=["POST"])
@require_auth
def api_files_create():
    """Create a new file directly from the editor with optional starter content."""
    sess = request.sess
    uid  = sess["uid"]
    role = sess["role"]
    body = request.get_json() or {}

    name      = (body.get("name", "")).strip()
    content   = body.get("content", "")
    folder_id = body.get("folder_id") or None
    # Validate folder_id
    if folder_id:
        fld = db_get(f"/folders/{uid}/{folder_id}")
        if not fld:
            folder_id = None

    if not name:
        return jsonify({"success": False, "error": "File name required"}), 400

    # Sanitize name
    name = os.path.basename(name)
    if not name or ".." in name:
        return jsonify({"success": False, "error": "Invalid file name"}), 400

    # Scan file content for malware/dangerous code
    content_bytes = content.encode("utf-8", errors="ignore") if isinstance(content, str) else b""
    safe, reason = scan_file(content_bytes, name)
    if not safe:
        log_activity(uid, "malware", f"Blocked create: {name} — {reason}")
        return jsonify({"success": False, "error": f"Security check failed: {reason}"}), 400

    lim     = FILE_LIMITS.get(role, FILE_LIMITS["free"])
    cur_cnt = len(db_list(f"/files/{uid}") or {})
    if cur_cnt >= lim["count"]:
        return jsonify({"success": False, "error": f"File limit reached ({cur_cnt}/{lim['count']})"}), 403

    ext      = os.path.splitext(name)[1].lower()
    editable = ext in TEXT_EDITABLE_EXT

    file_bytes  = content.encode("utf-8")
    size_mb     = len(file_bytes) / 1024 / 1024
    size_str    = f"{size_mb:.2f} MB" if size_mb >= 0.01 else f"{len(file_bytes)} B"
    content_b64 = base64.b64encode(file_bytes).decode()
    file_id     = str(uuid.uuid4())

    file_data = {
        "id":          file_id,
        "uid":         uid,
        "name":        name,
        "size":        size_str,
        "size_bytes":  len(file_bytes),
        "type":        ext.lstrip("."),
        "status":      "stopped",
        "editable":    editable,
        "upload_time": datetime.utcnow().isoformat(),
        "folder_id":   folder_id,
    }
    db_set(f"/files/{uid}/{file_id}", file_data)

    if editable:
        db_set(f"/file_content/{uid}/{file_id}", {
            "content_b64": content_b64,
            "updated_at":  datetime.utcnow().isoformat(),
        })

    # Notify hosting_bot — auto_start=False, user must manually press Start
    try:
        queue_bot_command(uid, "upload", {
            "file_name":        name,
            "file_content_b64": content_b64,
            "file_id":          file_id,
            "auto_start":       False,
        })
    except Exception:
        pass

    log_activity(uid, "upload", f"{sess['name']} created {name} via editor")
    return jsonify({"success": True, "file": file_data, "message": f"File '{name}' created!"})

@app.route("/api/files/delete/<file_id>", methods=["DELETE"])
@require_auth
def api_files_delete(file_id):
    uid = request.sess["uid"]

    # Validate file_id format (security: prevent path traversal)
    if not file_id or "/" in file_id or "\\" in file_id or ".." in file_id:
        return jsonify({"success": False, "error": "Invalid file ID"}), 400

    # Verify the file belongs to this user
    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found or access denied"}), 404

    file_name = f.get("name", "unknown")
    errors = []

    # Delete from Firebase DB (all related records)
    if not db_delete(f"/files/{uid}/{file_id}"):
        errors.append("metadata")
    db_delete(f"/file_content/{uid}/{file_id}")  # non-critical
    db_delete(f"/file_logs/{uid}/{file_id}")      # non-critical

    if errors:
        return jsonify({"success": False, "error": f"Failed to delete file record. Please try again."}), 500

    # Notify hosting_bot to clean up process (no Telegram needed)
    try:
        queue_bot_command(uid, "delete", {
            "file_name": file_name,
            "file_id":   file_id,
        })
    except Exception as e:
        print(f"[DELETE] Bot notify failed: {e}")

    log_activity(uid, "delete", f"{request.sess['name']} deleted {file_name}")
    return jsonify({"success": True, "message": f"File '{file_name}' deleted successfully"})


@app.route("/api/files/action/<file_id>", methods=["POST"])
@require_auth
def api_files_action(file_id):
    sess   = request.sess
    uid    = sess["uid"]
    action = (request.get_json() or {}).get("action","")
    if action not in ("start","stop"):
        return jsonify({"success":False,"error":"Invalid action"}), 400

    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success":False,"error":"File not found"}), 404

    # Only .py and .js files can be run as bots
    if action == "start":
        ftype = (f.get("type") or "").lower()
        if ftype not in ("py", "js"):
            return jsonify({"success":False,"error":f"Only .py and .js files can be run as bots. ({ftype} files are not supported)"}), 400

    new_status = "active" if action == "start" else "stopped"
    # Queue command to hosting_bot.py
    cmd_id = queue_bot_command(uid, action, {
        "file_name": f["name"],
        "file_id":   file_id,
    })
    r = wait_for_result(uid, cmd_id, timeout=8)

    # If bot returned an explicit error, report it
    if r.get("status") == "error":
        return jsonify({"success":False,"error":r.get("error","Bot error")}), 500

    # Optimistic update: update DB status even if bot timed out (queued)
    # The bot will process the command when it next polls Firebase
    db_update(f"/files/{uid}/{file_id}", {"status": new_status})
    log_activity(uid, action, f"{sess['name']} {action}ed {f['name']}")

    if r.get("status") == "timeout":
        msg = f"⚡ {'Starting' if action=='start' else 'Stopping'}… Please wait a moment and refresh."
    else:
        msg = f"✅ {'Started' if action=='start' else 'Stopped'} successfully!"

    return jsonify({"success":True,"status":new_status,"message":msg})


@app.route("/api/files/rename/<file_id>", methods=["POST"])
@require_auth
def api_files_rename(file_id):
    uid  = request.sess["uid"]
    body = request.get_json() or {}
    new_name = (body.get("name","")).strip()
    if not new_name:
        return jsonify({"success":False,"error":"New name required"}), 400

    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success":False,"error":"File not found"}), 404

    old_name = f["name"]
    db_update(f"/files/{uid}/{file_id}", {"name": new_name})

    # Tell hosting_bot to rename (no Telegram needed)
    cmd_id = queue_bot_command(uid, "rename", {
        "old_name": old_name, "new_name": new_name, "file_id": file_id,
    })
    wait_for_result(uid, cmd_id, timeout=8)

    log_activity(uid, "rename", f"{request.sess['name']} renamed {old_name} → {new_name}")
    return jsonify({"success":True,"message":f"✅ Renamed to {new_name}"})


@app.route("/api/files/content/<file_id>", methods=["GET"])
@require_auth
def api_files_get_content(file_id):
    uid = request.sess["uid"]
    f   = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success":False,"error":"File not found"}), 404
    if not f.get("editable"):
        return jsonify({"success":False,"error":"File is not text-editable"}), 400

    stored = db_get(f"/file_content/{uid}/{file_id}")
    if not stored or not stored.get("content_b64"):
        # New file or content not yet saved — return empty editor
        return jsonify({"success":True,"content":"","name":f["name"]})

    try:
        content = base64.b64decode(stored["content_b64"]).decode("utf-8", errors="replace")
    except Exception:
        return jsonify({"success":False,"error":"Could not decode content"}), 500

    return jsonify({"success":True,"content":content,"name":f["name"]})


@app.route("/api/files/content/<file_id>", methods=["POST"])
@require_auth
def api_files_save_content(file_id):
    uid  = request.sess["uid"]
    body = request.get_json() or {}
    content = body.get("content","")

    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success":False,"error":"File not found"}), 404
    if not f.get("editable"):
        return jsonify({"success":False,"error":"File is not text-editable"}), 400

    file_bytes = content.encode("utf-8")
    size_mb    = len(file_bytes) / 1024 / 1024
    content_b64 = base64.b64encode(file_bytes).decode()

    db_set(f"/file_content/{uid}/{file_id}", {
        "content_b64": content_b64,
        "updated_at":  datetime.utcnow().isoformat(),
    })
    size_str = f"{size_mb:.2f} MB" if size_mb >= 0.01 else f"{len(file_bytes)} B"
    db_update(f"/files/{uid}/{file_id}", {"size": size_str, "size_bytes": len(file_bytes)})

    # Push new content to hosting_bot (update only — no auto-run)
    cmd_id = queue_bot_command(uid, "update_file", {
        "file_name": f["name"], "file_content_b64": content_b64, "file_id": file_id,
    })
    wait_for_result(uid, cmd_id, timeout=10)

    log_activity(uid, "edit", f"{request.sess['name']} edited {f['name']}")
    return jsonify({"success":True,"message":"✅ File saved!"})


@app.route("/api/files/logs/<file_id>", methods=["GET"])
@require_auth
def api_files_logs(file_id):
    uid = request.sess["uid"]
    f   = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success":False,"error":"File not found"}), 404

    # Ask hosting_bot for live logs (short timeout — fall back to Firebase logs quickly)
    cmd_id = queue_bot_command(uid, "get_logs", {
        "file_name": f["name"], "file_id": file_id,
    })
    r = wait_for_result(uid, cmd_id, timeout=5)
    if r.get("status") == "done" and r.get("logs"):
        return jsonify({"success":True,"logs":sanitize_log_lines(r["logs"]),"source":"bot"})

    # Fall back to stored Firebase logs
    stored = db_list(f"/file_logs/{uid}/{file_id}") or {}
    logs   = sorted(stored.values(), key=lambda x: x.get("ts",""))[-100:]
    log_lines = [f"[{l.get('ts','')}] {l.get('msg','')}" for l in logs]

    return jsonify({
        "success": True,
        "logs":    sanitize_log_lines(log_lines) if log_lines else ["No logs available yet."],
        "source":  "firebase"
    })


@app.route("/api/files/stats/<file_id>", methods=["GET"])
@require_auth
def api_files_stats(file_id):
    uid = request.sess["uid"]
    f   = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success":False,"error":"File not found"}), 404
    cmd_id = queue_bot_command(uid, "get_stats", {
        "file_name": f["name"], "file_id": file_id,
    })
    r = wait_for_result(uid, cmd_id, timeout=8)
    if r.get("status") == "done":
        return jsonify({"success":True,"stats":r.get("stats",{})})
    return jsonify({"success":True,"stats":{"cpu":0,"memory_mb":0,"uptime_sec":0,"running":False}})


@app.route("/api/files/pip/<file_id>", methods=["POST"])
@require_auth
def api_files_pip(file_id):
    uid  = request.sess["uid"]
    # ── Rate limit pip: 10 installs per user per 5 minutes ───
    if not _check_rate_limit(f"pip:{uid}", max_calls=10, window_sec=300):
        return jsonify({"success":False,"error":"Too many install requests. Please wait 5 minutes."}), 429
    body = request.get_json() or {}
    package = (body.get("package","")).strip()
    if not package:
        return jsonify({"success":False,"error":"Package name required"}), 400

    # Basic safety check
    forbidden = [";","&&","||","$(","`",">","<","|","rm","wget","curl","os.","sys."]
    for kw in forbidden:
        if kw in package:
            return jsonify({"success":False,"error":"Invalid package name"}), 400

    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success":False,"error":"File not found"}), 404

    # Queue pip install to hosting_bot (no Telegram needed)
    cmd_id = queue_bot_command(uid, "pip_install", {
        "package": package, "file_id": file_id,
    })
    r = wait_for_result(uid, cmd_id, timeout=25)

    if r.get("status") == "done":
        output = r.get("output","")
        log_activity(uid, "pip", f"{request.sess['name']} installed {package} for {f['name']}")
        append_file_log(uid, file_id, f"pip install {package}: SUCCESS\n{output[:200]}")
        return jsonify({"success":True,"message":f"✅ {package} installed!","output":output})
    else:
        err = r.get("error","")
        # Check if it's a timeout (bot not running) vs an actual package error
        if not err or "timeout" in err.lower() or "not running" in err.lower():
            return jsonify({"success":False,"error":"Bot must be running to install packages. Start it first, then retry."}), 400
        return jsonify({"success":False,"error":f"Install failed: {err}"}), 500


@app.route("/api/files/sync", methods=["POST"])
@require_auth
def api_files_sync():
    uid   = request.sess["uid"]
    cmd_id = queue_bot_command(uid, "sync_files", {})
    r      = wait_for_result(uid, cmd_id, timeout=12)

    if r.get("status") != "done":
        return jsonify({"success":False,"error":"Bot did not respond. Is the bot running?"})

    bot_files = r.get("files", [])
    all_db    = db_list(f"/files/{uid}") or {}

    for bf in bot_files:
        match = next((k for k,v in all_db.items() if v.get("name")==bf["name"]), None)
        if match:
            db_update(f"/files/{uid}/{match}", {"status": bf["status"]})

    return jsonify({"success":True,"synced":len(bot_files)})


# ═══════════════════════════════════════════════════════════
#  ACTIVITY
# ═══════════════════════════════════════════════════════════
@app.route("/api/activity", methods=["GET"])
@require_auth
def api_activity():
    uid  = request.sess["uid"]
    role = request.sess["role"]

    acts  = db_list("/activity") or {}
    items = sorted(acts.values(), key=lambda x: x.get("time",""), reverse=True)

    if role not in ("admin","owner"):
        items = [a for a in items if a.get("uid") == uid]

    return jsonify({"success":True,"activity":items[:60]})


# ═══════════════════════════════════════════════════════════
#  TELEGRAM DEEP-LINK
# ═══════════════════════════════════════════════════════════
@app.route("/api/telegram/deeplink", methods=["GET"])
@require_auth
def api_tg_deeplink():
    uid = request.sess["uid"]
    return jsonify({
        "success":  True,
        "deeplink": f"https://t.me/{BOT_NAME}?start={uid}",
        "bot_name": BOT_NAME,
    })


# ═══════════════════════════════════════════════════════════
#  ADMIN ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/admin/users", methods=["GET"])
@require_admin
def api_admin_users():
    users = db_list("/users") or {}
    return jsonify({"success":True,"users":list(users.values())})


@app.route("/api/admin/user/role", methods=["POST"])
@require_admin
def api_admin_set_role():
    sess    = request.sess
    body    = request.get_json() or {}
    tgt_uid = body.get("uid","")
    role    = body.get("role","")
    if role not in ("free","subscribed","admin"):
        return jsonify({"success":False,"error":"Invalid role"}), 400
    if not tgt_uid:
        return jsonify({"success":False,"error":"uid required"}), 400
    db_update(f"/users/{tgt_uid}", {"role": role})
    notify_user(tgt_uid, f"ℹ️ Your Nexora Cloud role has been updated to: <b>{role}</b>")
    log_activity(sess["uid"], "subscribe", f"{sess['name']} set {tgt_uid} role to {role}")
    return jsonify({"success":True,"message":f"✅ Role set to {role}"})


@app.route("/api/admin/user/ban", methods=["POST"])
@require_admin
def api_admin_ban_user():
    sess    = request.sess
    body    = request.get_json() or {}
    tgt_uid = body.get("uid","")
    ban     = bool(body.get("ban", True))

    if not tgt_uid:
        return jsonify({"success":False,"error":"uid required"}), 400

    # Can't ban owner
    tgt_user = db_get(f"/users/{tgt_uid}") or {}
    if tgt_user.get("role") == "owner":
        return jsonify({"success":False,"error":"Cannot ban owner"}), 403

    reason = body.get("reason", "")
    update_data = {"banned": ban}
    if ban and reason:
        update_data["ban_reason"] = reason
    elif not ban:
        update_data["ban_reason"] = None
    db_update(f"/users/{tgt_uid}", update_data)

    if ban:
        # Invalidate all their sessions
        sessions = db_list("/sessions") or {}
        for sid_k, sd in sessions.items():
            if sd.get("uid") == tgt_uid:
                db_delete(f"/sessions/{sid_k}")
        reason_text = f"\nReason: {reason}" if reason else ""
        notify_user(tgt_uid, f"🚫 Your Nexora Cloud account has been <b>banned</b>. Contact support.{reason_text}")
        log_activity(sess["uid"], "ban", f"{sess['name']} banned {tgt_user.get('name',tgt_uid)}: {reason}")
        action_word = "banned"
    else:
        notify_user(tgt_uid, "✅ Your Nexora Cloud account has been <b>unbanned</b>. Welcome back!")
        log_activity(sess["uid"], "unban", f"{sess['name']} unbanned {tgt_user.get('name',tgt_uid)}")
        action_word = "unbanned"

    return jsonify({"success":True,"message":f"✅ User {action_word}!"})


@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def api_admin_stats():
    users = db_list("/users") or {}
    files = {}
    for uid in users:
        uf = db_list(f"/files/{uid}") or {}
        files.update(uf)
    acts = db_list("/activity") or {}
    return jsonify({
        "success":        True,
        "total_users":    len(users),
        "total_files":    len(files),
        "active_files":   sum(1 for f in files.values() if f.get("status")=="active"),
        "total_activity": len(acts),
        "banned_users":   sum(1 for u in users.values() if u.get("banned")),
        "roles": {
            "free":       sum(1 for u in users.values() if u.get("role","free")=="free"),
            "subscribed": sum(1 for u in users.values() if u.get("role")=="subscribed"),
            "admin":      sum(1 for u in users.values() if u.get("role")=="admin"),
            "owner":      sum(1 for u in users.values() if u.get("role")=="owner"),
        }
    })


@app.route("/api/admin/broadcast", methods=["POST"])
@require_admin
def api_admin_broadcast():
    sess = request.sess
    body = request.get_json() or {}
    msg  = body.get("message","").strip()
    if not msg:
        return jsonify({"success":False,"error":"Message required"}), 400

    users = db_list("/users") or {}
    sent  = 0
    for uid, u in users.items():
        if u.get("banned"):
            continue
        tg = u.get("tg_chat_id")
        if tg:
            tg_send(int(tg), f"📢 <b>Nexora Cloud Broadcast</b>\n\n{msg}")
            sent += 1

    log_activity(sess["uid"], "broadcast", f"{sess['name']} broadcast to {sent} users")
    return jsonify({"success":True,"sent":sent})


# ── Notifications API ──
@app.route("/api/admin/notification", methods=["POST"])
@require_admin
def api_admin_send_notification():
    """Send a site notification to all users."""
    sess = request.sess
    body = request.get_json() or {}
    title = body.get("title", "").strip()
    message = body.get("message", "").strip()
    ntype = body.get("type", "info")
    if not title or not message:
        return jsonify({"success": False, "error": "title and message required"}), 400
    import time
    notif = {"title": title, "message": message, "type": ntype, "created_at": int(time.time()*1000)}
    # Store in /notifications as a broadcast list
    existing = db_list("/site_notifications") or {}
    db_set(f"/site_notifications/{len(existing)}", notif)
    return jsonify({"success": True, "message": "Notification sent"})

@app.route("/api/notifications", methods=["GET"])
def api_get_notifications():
    """Get notifications for the current user — personal + broadcast."""
    sid  = request.headers.get("X-Session-Id","") or request.cookies.get("nsid","")
    sess = get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    notif_list = []
    # Personal notifications (from notify_user)
    personal = db_list(f"/notifications/{sess['uid']}") or {}
    for key, n in personal.items():
        notif_list.append({**n, "id": f"p_{key}", "read": n.get("read", False)})
    # Global broadcast notifications
    site_notifs = db_list("/site_notifications") or {}
    for key, n in site_notifs.items():
        read = db_get(f"/user_notif_read/{sess['uid']}/{key}") is not None
        notif_list.append({**n, "id": key, "read": read})
    # Sort newest first
    notif_list.sort(key=lambda x: x.get("time", x.get("created_at", 0)), reverse=True)
    return jsonify({"success": True, "notifications": notif_list[:30]})

@app.route("/api/notifications/read/<notif_id>", methods=["POST"])
def api_notif_read_one(notif_id):
    sid  = request.headers.get("X-Session-Id","") or request.cookies.get("nsid","")
    sess = get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    if notif_id.startswith("p_"):
        real_id = notif_id[2:]
        db_update(f"/notifications/{sess['uid']}/{real_id}", {"read": True})
    else:
        db_set(f"/user_notif_read/{sess['uid']}/{notif_id}", True)
    return jsonify({"success": True})

@app.route("/api/notifications/read-all", methods=["POST"])
def api_notifs_read_all():
    sid  = request.headers.get("X-Session-Id","") or request.cookies.get("nsid","")
    sess = get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    # Mark personal
    personal = db_list(f"/notifications/{sess['uid']}") or {}
    for key in personal:
        db_update(f"/notifications/{sess['uid']}/{key}", {"read": True})
    # Mark broadcast
    site_notifs = db_list("/site_notifications") or {}
    for key in site_notifs:
        db_set(f"/user_notif_read/{sess['uid']}/{key}", True)
    return jsonify({"success": True})


# ── Unban Appeal API ──
@app.route("/api/unban-appeal", methods=["POST"])
def api_submit_unban_appeal():
    """User submits an unban appeal."""
    sid  = request.headers.get("X-Session-Id","") or request.cookies.get("nsid","")
    sess = get_session_allow_banned(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    body = request.get_json() or {}
    appeal_text = body.get("appeal", "").strip()
    if not appeal_text:
        return jsonify({"success": False, "error": "Appeal message required"}), 400
    import time
    appeal = {
        "uid": sess["uid"],
        "email": sess.get("email", ""),
        "name": sess.get("name", ""),
        "appeal": appeal_text,
        "created_at": int(time.time() * 1000),
        "status": "pending"
    }
    db_set(f"/unban_appeals/{sess['uid']}", appeal)
    notify_owner(f"⚖️ <b>Unban Appeal</b>\nUser: {sess.get('name','')}\nEmail: {sess.get('email','')}\nAppeal: {appeal_text[:200]}")
    return jsonify({"success": True})

@app.route("/api/admin/unban-appeals", methods=["GET"])
@require_admin
def api_admin_get_appeals():
    appeals = db_list("/unban_appeals") or {}
    appeal_list = [v for v in appeals.values() if isinstance(v, dict) and v.get("status") == "pending"]
    appeal_list.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return jsonify({"success": True, "appeals": appeal_list})

@app.route("/api/admin/unban-appeal/review", methods=["POST"])
@require_admin
def api_admin_review_appeal():
    sess = request.sess
    body = request.get_json() or {}
    uid = body.get("uid", "")
    decision = body.get("decision", "")
    if not uid or decision not in ("approve", "reject"):
        return jsonify({"success": False, "error": "uid and decision required"}), 400
    if decision == "approve":
        db_update(f"/users/{uid}", {"banned": False, "ban_reason": None})
        notify_user(uid, "✅ Your unban appeal has been <b>approved</b>. Welcome back to Nexora Cloud!")
    else:
        notify_user(uid, "❌ Your unban appeal has been <b>rejected</b>. Contact support for more info.")
    db_update(f"/unban_appeals/{uid}", {"status": decision})
    log_activity(sess["uid"], "ban", f"Appeal {decision} for user {uid}")
    return jsonify({"success": True})


@app.route("/api/admin/support_toggle", methods=["POST"])
@require_admin
def api_admin_support_toggle():
    sess   = request.sess
    body   = request.get_json() or {}
    enabled = bool(body.get("enabled", True))
    db_set("/settings/support_enabled", enabled)
    log_activity(sess["uid"], "edit", f"{sess['name']} {'enabled' if enabled else 'disabled'} support")
    return jsonify({"success": True, "support_enabled": enabled})


@app.route("/api/admin/support_status", methods=["GET"])
@require_admin
def api_admin_support_status():
    enabled = db_get("/settings/support_enabled")
    if enabled is None:
        enabled = True
    return jsonify({"success": True, "support_enabled": bool(enabled)})


@app.route("/api/public/support_status", methods=["GET"])
def api_public_support_status():
    """Public endpoint — tells frontend if support is on/off."""
    enabled = db_get("/settings/support_enabled")
    if enabled is None:
        enabled = True
    return jsonify({"support_enabled": bool(enabled)})


@app.route("/api/admin/bot_lock", methods=["POST"])
@require_admin
def api_admin_bot_lock():
    sess   = request.sess
    body   = request.get_json() or {}
    locked = bool(body.get("locked", False))
    db_set("/settings/bot_locked", locked)
    log_activity(sess["uid"], "malware" if locked else "login",
                 f"{sess['name']} {'locked' if locked else 'unlocked'} the bot")
    return jsonify({"success":True,"locked":locked})


@app.route("/api/admin/bot_status", methods=["GET"])
@require_admin
def api_admin_bot_status():
    locked = db_get("/settings/bot_locked") or False
    return jsonify({"success":True,"locked":locked})


@app.route("/api/admin/activity", methods=["GET"])
@require_admin
def api_admin_all_activity():
    acts  = db_list("/activity") or {}
    items = sorted(acts.values(), key=lambda x: x.get("time",""), reverse=True)
    return jsonify({"success":True,"activity":items[:100]})


# ═══════════════════════════════════════════════════════════
#  SUB-USER MANAGEMENT
# ═══════════════════════════════════════════════════════════

def find_uid_by_email(email):
    """Find a user's UID by email address."""
    try:
        users = db_list("/users") or {}
        for uid, udata in users.items():
            if udata.get("email", "").lower() == email.lower():
                return uid, udata
    except Exception:
        pass
    return None, None


@app.route("/api/subusers/invite", methods=["POST"])
@require_auth
def api_subusers_invite():
    """Send an invite to a user by email."""
    owner_uid  = request.sess["uid"]
    owner_data = db_get(f"/users/{owner_uid}") or {}
    owner_email = owner_data.get("email", "")
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    permissions = data.get("permissions", [])

    if not email:
        return jsonify({"success": False, "error": "Email is required"})

    if email == owner_email.lower():
        return jsonify({"success": False, "error": "You cannot invite yourself"})

    # Find invitee
    invitee_uid, invitee_data = find_uid_by_email(email)
    if not invitee_uid:
        return jsonify({"success": False, "error": "No account found with that email address"})

    # Check if already a sub-user
    existing = db_get(f"/subusers/{owner_uid}/active/{invitee_uid}")
    if existing:
        return jsonify({"success": False, "error": "This user is already a sub-user"})

    # Save pending invite — stored on owner node AND invitee node
    invite_data = {
        "owner_uid":   owner_uid,
        "owner_email": owner_email,
        "invitee_uid": invitee_uid,
        "invitee_email": email,
        "permissions": permissions,
        "status":      "pending",
        "created_at":  datetime.utcnow().isoformat(),
    }
    db_set(f"/subusers/{owner_uid}/pending/{invitee_uid}", invite_data)
    db_set(f"/user_invites/{invitee_uid}/{owner_uid}", invite_data)

    return jsonify({"success": True, "message": f"Invite sent to {email}"})


@app.route("/api/subusers/list", methods=["GET"])
@require_auth
def api_subusers_list():
    """List active sub-users and pending invites for the current user."""
    uid = request.sess["uid"]

    # Active sub-users
    active_raw = db_list(f"/subusers/{uid}/active") or {}
    users_out = []
    for sub_uid, sub_data in active_raw.items():
        user_node = db_get(f"/users/{sub_uid}") or {}
        users_out.append({
            "uid":         sub_uid,
            "email":       sub_data.get("invitee_email") or user_node.get("email", ""),
            "name":        user_node.get("name", ""),
            "permissions": sub_data.get("permissions", []),
            "added_at":    sub_data.get("accepted_at") or sub_data.get("created_at", ""),
        })

    # Pending invites
    pending_raw = db_list(f"/subusers/{uid}/pending") or {}
    pending_out = [
        {
            "uid":        inv_uid,
            "email":      inv_data.get("invitee_email", ""),
            "created_at": inv_data.get("created_at", ""),
        }
        for inv_uid, inv_data in pending_raw.items()
    ]

    return jsonify({"success": True, "users": users_out, "pending": pending_out})


@app.route("/api/subusers/update", methods=["POST"])
@require_auth
def api_subusers_update():
    """Update permissions for an active sub-user."""
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    permissions = data.get("permissions", [])

    invitee_uid, _ = find_uid_by_email(email)
    if not invitee_uid:
        return jsonify({"success": False, "error": "User not found"})

    node = db_get(f"/subusers/{uid}/active/{invitee_uid}")
    if not node:
        return jsonify({"success": False, "error": "Sub-user not found"})

    node["permissions"] = permissions
    db_set(f"/subusers/{uid}/active/{invitee_uid}", node)
    # Also update in user_invites so they see the new permissions
    db_set(f"/user_invites/{invitee_uid}/{uid}/permissions", permissions)

    return jsonify({"success": True})


@app.route("/api/subusers/remove", methods=["POST"])
@require_auth
def api_subusers_remove():
    """Remove a sub-user."""
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    invitee_uid, _ = find_uid_by_email(email)
    if not invitee_uid:
        return jsonify({"success": False, "error": "User not found"})

    db_delete(f"/subusers/{uid}/active/{invitee_uid}")
    db_delete(f"/user_invites/{invitee_uid}/{uid}")

    return jsonify({"success": True})


@app.route("/api/subusers/cancel_invite", methods=["POST"])
@require_auth
def api_subusers_cancel_invite():
    """Cancel a pending invite."""
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    invitee_uid, _ = find_uid_by_email(email)
    if not invitee_uid:
        return jsonify({"success": False, "error": "User not found"})

    db_delete(f"/subusers/{uid}/pending/{invitee_uid}")
    db_delete(f"/user_invites/{invitee_uid}/{uid}")

    return jsonify({"success": True})


@app.route("/api/subusers/my_invite", methods=["GET"])
@require_auth
def api_subusers_my_invite():
    """Check if current user has any pending invites."""
    uid = request.sess["uid"]
    invites_raw = db_list(f"/user_invites/{uid}") or {}

    for owner_uid, inv_data in invites_raw.items():
        if inv_data.get("status") == "pending":
            return jsonify({"success": True, "invite": inv_data})

    return jsonify({"success": True, "invite": None})


@app.route("/api/subusers/respond", methods=["POST"])
@require_auth
def api_subusers_respond():
    """Accept or decline a pending invite."""
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    action = data.get("action")  # "accept" or "decline"

    if action not in ("accept", "decline"):
        return jsonify({"success": False, "error": "Invalid action"})

    # Find the first pending invite for this user
    invites_raw = db_list(f"/user_invites/{uid}") or {}
    for owner_uid, inv_data in invites_raw.items():
        if inv_data.get("status") == "pending":
            if action == "accept":
                inv_data["status"] = "accepted"
                inv_data["accepted_at"] = datetime.utcnow().isoformat()
                # Move from pending to active on owner node
                db_delete(f"/subusers/{owner_uid}/pending/{uid}")
                db_set(f"/subusers/{owner_uid}/active/{uid}", inv_data)
                db_set(f"/user_invites/{uid}/{owner_uid}", inv_data)
            else:
                db_delete(f"/subusers/{owner_uid}/pending/{uid}")
                db_delete(f"/user_invites/{uid}/{owner_uid}")
            return jsonify({"success": True, "action": action})

    return jsonify({"success": False, "error": "No pending invite found"})


@app.route("/api/subusers/workspace", methods=["GET"])
@require_auth
def api_subusers_workspace():
    """Get the workspace data the current user has access to as a sub-user.
    Returns owner's files and bots based on permissions granted."""
    uid = request.sess["uid"]

    # Find all accepted invites where this user is a sub-user
    invites_raw = db_list(f"/user_invites/{uid}") or {}
    workspaces = []

    for owner_uid, inv_data in invites_raw.items():
        if inv_data.get("status") != "accepted":
            continue
        permissions = inv_data.get("permissions", [])
        owner_info = db_get(f"/users/{owner_uid}") or {}

        workspace = {
            "owner_uid": owner_uid,
            "owner_name": owner_info.get("name", "Unknown"),
            "owner_email": owner_info.get("email", ""),
            "permissions": permissions,
            "files": [],
            "bots": [],
        }

        # Load files - show to anyone with file or bot permissions
        file_perms = {"files.read","files.edit","files.delete","files.upload","bots.view","bots.start","bots.create","editor.access","all"}
        if any(p in permissions for p in file_perms):
            all_files = db_list(f"/files/{owner_uid}") or {}
            files = sorted(all_files.values(), key=lambda x: x.get("upload_time",""), reverse=True)
            workspace["files"] = files

        # Load bots list
        if "bots.view" in permissions or "bots.start" in permissions or "all" in permissions:
            all_files = db_list(f"/files/{owner_uid}") or {}
            bots = [f for f in all_files.values() if f.get("type") in ("py", "js", "ts", "sh")]
            workspace["bots"] = bots

        workspaces.append(workspace)

    return jsonify({"success": True, "workspaces": workspaces})


@app.route("/api/subusers/workspace/file/action", methods=["POST"])
@require_auth
def api_subuser_file_action():
    """Sub-user controls a file in an owner's workspace (start/stop)."""
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    owner_uid = data.get("owner_uid", "")
    file_id   = data.get("file_id", "")
    action    = data.get("action", "")

    if not owner_uid or not file_id or action not in ("start", "stop"):
        return jsonify({"success": False, "error": "owner_uid, file_id, and action required"}), 400

    # Verify this user is an accepted sub-user of the owner with bots.start permission
    inv_data = db_get(f"/user_invites/{uid}/{owner_uid}")
    if not inv_data or inv_data.get("status") != "accepted":
        return jsonify({"success": False, "error": "Access denied"}), 403

    permissions = inv_data.get("permissions", [])
    if "bots.start" not in permissions and "all" not in permissions:
        return jsonify({"success": False, "error": "You don't have permission to control bots"}), 403

    f = db_get(f"/files/{owner_uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found"}), 404

    new_status = "active" if action == "start" else "stopped"
    # Queue command to hosting_bot (no Telegram needed)
    cmd_id = queue_bot_command(owner_uid, action, {
        "file_name": f["name"], "file_id": file_id,
    })
    r = wait_for_result(owner_uid, cmd_id, timeout=12)
    if r.get("status") == "error":
        return jsonify({"success": False, "error": r.get("error", "Bot error")}), 500

    db_update(f"/files/{owner_uid}/{file_id}", {"status": new_status})
    return jsonify({"success": True, "status": new_status})


@app.route("/api/subusers/workspace/file/content/<owner_uid>/<file_id>", methods=["GET"])
@require_auth
def api_subuser_file_content(owner_uid, file_id):
    """Sub-user reads file content from owner's workspace."""
    uid = request.sess["uid"]

    inv_data = db_get(f"/user_invites/{uid}/{owner_uid}")
    if not inv_data or inv_data.get("status") != "accepted":
        return jsonify({"success": False, "error": "Access denied"}), 403

    permissions = inv_data.get("permissions", [])
    if "files.read" not in permissions and "editor.access" not in permissions and "files.edit" not in permissions and "all" not in permissions:
        return jsonify({"success": False, "error": "Read permission required"}), 403

    f = db_get(f"/files/{owner_uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found"}), 404

    stored = db_get(f"/file_content/{owner_uid}/{file_id}")
    if not stored or not stored.get("content_b64"):
        return jsonify({"success": False, "error": "Content not available"}), 404

    try:
        import base64
        content = base64.b64decode(stored["content_b64"]).decode("utf-8", errors="replace")
    except Exception:
        return jsonify({"success": False, "error": "Could not decode content"}), 500

    return jsonify({"success": True, "content": content, "name": f["name"]})





@app.route("/api/subusers/workspace/file/save", methods=["POST"])
@require_auth
def api_subuser_file_save():
    """Sub-user saves/edits file content in owner's workspace (requires files.edit)."""
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    owner_uid = data.get("owner_uid", "")
    file_id   = data.get("file_id", "")
    new_content = data.get("content", "")

    if not owner_uid or not file_id:
        return jsonify({"success": False, "error": "owner_uid and file_id required"}), 400

    inv_data = db_get(f"/user_invites/{uid}/{owner_uid}")
    if not inv_data or inv_data.get("status") != "accepted":
        return jsonify({"success": False, "error": "Access denied"}), 403

    permissions = inv_data.get("permissions", [])
    if "files.edit" not in permissions and "all" not in permissions:
        return jsonify({"success": False, "error": "Edit permission required"}), 403

    f = db_get(f"/files/{owner_uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found"}), 404

    import base64
    encoded = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
    db_update(f"/file_content/{owner_uid}/{file_id}", {
        "content_b64": encoded,
        "updated_at": datetime.utcnow().isoformat(),
    })
    db_update(f"/files/{owner_uid}/{file_id}", {"updated_at": datetime.utcnow().isoformat()})
    log_activity(uid, "edit", f"Sub-user edited {f.get('name','file')} in {owner_uid}'s workspace")
    return jsonify({"success": True})


@app.route("/api/subusers/workspace/file/delete", methods=["POST"])
@require_auth
def api_subuser_file_delete():
    """Sub-user deletes a file from owner's workspace (requires files.delete)."""
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    owner_uid = data.get("owner_uid", "")
    file_id   = data.get("file_id", "")

    if not owner_uid or not file_id:
        return jsonify({"success": False, "error": "owner_uid and file_id required"}), 400

    inv_data = db_get(f"/user_invites/{uid}/{owner_uid}")
    if not inv_data or inv_data.get("status") != "accepted":
        return jsonify({"success": False, "error": "Access denied"}), 403

    permissions = inv_data.get("permissions", [])
    if "files.delete" not in permissions and "all" not in permissions:
        return jsonify({"success": False, "error": "Delete permission required"}), 403

    f = db_get(f"/files/{owner_uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found"}), 404

    db_delete(f"/files/{owner_uid}/{file_id}")
    db_delete(f"/file_content/{owner_uid}/{file_id}")
    log_activity(uid, "delete", f"Sub-user deleted {f.get('name','file')} from {owner_uid}'s workspace")
    return jsonify({"success": True})


@app.route("/api/subusers/workspace/file/upload", methods=["POST"])
@require_auth
def api_subuser_file_upload():
    """Sub-user uploads a file to owner's workspace (requires files.upload)."""
    import base64, uuid
    uid  = request.sess["uid"]
    data = request.get_json(silent=True) or {}
    owner_uid = data.get("owner_uid", "")
    filename  = data.get("name", "").strip()
    content   = data.get("content", "")

    if not owner_uid or not filename:
        return jsonify({"success": False, "error": "owner_uid and name required"}), 400

    inv_data = db_get(f"/user_invites/{uid}/{owner_uid}")
    if not inv_data or inv_data.get("status") != "accepted":
        return jsonify({"success": False, "error": "Access denied"}), 403

    permissions = inv_data.get("permissions", [])
    if "files.upload" not in permissions and "all" not in permissions:
        return jsonify({"success": False, "error": "Upload permission required"}), 403

    # Check owner's file limit
    owner_data = db_get(f"/users/{owner_uid}") or {}
    existing = db_list(f"/files/{owner_uid}") or {}
    plan = owner_data.get("role", "free")
    plan_limits = FILE_LIMITS.get(plan, FILE_LIMITS["free"])
    limit = plan_limits["count"]
    if len(existing) >= limit:
        return jsonify({"success": False, "error": "Owner's file limit reached"}), 400

    file_id = str(uuid.uuid4()).replace("-", "")[:16]
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    db_set(f"/files/{owner_uid}/{file_id}", {
        "id": file_id, "name": filename, "type": ext,
        "status": "stopped", "upload_time": datetime.utcnow().isoformat(),
        "uploaded_by_subuser": uid,
    })
    db_set(f"/file_content/{owner_uid}/{file_id}", {
        "content_b64": encoded, "updated_at": datetime.utcnow().isoformat(),
    })
    log_activity(uid, "upload", f"Sub-user uploaded {filename} to {owner_uid}'s workspace")
    return jsonify({"success": True, "file_id": file_id})

# ═══════════════════════════════════════════════════════════
#  FOLDER / DIRECTORY ROUTES
# ═══════════════════════════════════════════════════════════

@app.route("/api/folders/list", methods=["GET"])
@require_auth
def api_folders_list():
    uid = request.sess["uid"]
    folders = db_list(f"/folders/{uid}") or {}
    return jsonify({"success": True, "folders": list(folders.values())})


@app.route("/api/folders/create", methods=["POST"])
@require_auth
def api_folders_create():
    uid  = request.sess["uid"]
    body = request.get_json() or {}
    name = (body.get("name", "")).strip()
    if not name:
        return jsonify({"success": False, "error": "Folder name required"}), 400
    name = os.path.basename(name)
    if not name or ".." in name:
        return jsonify({"success": False, "error": "Invalid folder name"}), 400
    # Check duplicate
    existing = db_list(f"/folders/{uid}") or {}
    for fld in existing.values():
        if fld.get("name", "").lower() == name.lower():
            return jsonify({"success": False, "error": "Folder already exists"}), 400
    folder_id = str(uuid.uuid4())
    folder_data = {
        "id":         folder_id,
        "uid":        uid,
        "name":       name,
        "created_at": datetime.utcnow().isoformat(),
    }
    db_set(f"/folders/{uid}/{folder_id}", folder_data)
    return jsonify({"success": True, "folder": folder_data})


@app.route("/api/folders/delete/<folder_id>", methods=["DELETE"])
@require_auth
def api_folders_delete(folder_id):
    uid = request.sess["uid"]
    if not folder_id or "/" in folder_id or ".." in folder_id:
        return jsonify({"success": False, "error": "Invalid folder ID"}), 400
    fld = db_get(f"/folders/{uid}/{folder_id}")
    if not fld:
        return jsonify({"success": False, "error": "Folder not found"}), 404
    # Move files out of folder before deleting (unassign folder_id)
    all_files = db_list(f"/files/{uid}") or {}
    for fid, fd in all_files.items():
        if fd.get("folder_id") == folder_id:
            db_update(f"/files/{uid}/{fid}", {"folder_id": None})
    db_delete(f"/folders/{uid}/{folder_id}")
    return jsonify({"success": True, "message": f"Folder '{fld['name']}' deleted"})


@app.route("/api/folders/rename/<folder_id>", methods=["POST"])
@require_auth
def api_folders_rename(folder_id):
    uid  = request.sess["uid"]
    body = request.get_json() or {}
    new_name = (body.get("name", "")).strip()
    if not new_name:
        return jsonify({"success": False, "error": "Name required"}), 400
    fld = db_get(f"/folders/{uid}/{folder_id}")
    if not fld:
        return jsonify({"success": False, "error": "Folder not found"}), 404
    db_update(f"/folders/{uid}/{folder_id}", {"name": new_name})
    return jsonify({"success": True})


@app.route("/api/files/move/<file_id>", methods=["POST"])
@require_auth
def api_files_move(file_id):
    """Move a file to a folder (or root if folder_id is null)."""
    uid  = request.sess["uid"]
    body = request.get_json() or {}
    folder_id = body.get("folder_id")  # None = root
    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found"}), 404
    if folder_id:
        fld = db_get(f"/folders/{uid}/{folder_id}")
        if not fld:
            return jsonify({"success": False, "error": "Folder not found"}), 404
    db_update(f"/files/{uid}/{file_id}", {"folder_id": folder_id})
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════
#  BOT UPTIME PERSISTENCE — save/restore uptime across refreshes
# ═══════════════════════════════════════════════════════════

@app.route("/api/bots/uptime", methods=["GET"])
@require_auth
def api_bots_uptime_get():
    uid = request.sess["uid"]
    data = db_list(f"/bot_uptimes/{uid}") or {}
    # Compute live uptime_sec for running bots
    now = datetime.utcnow()
    result = {}
    for fid, entry in data.items():
        if isinstance(entry, dict):
            enriched = dict(entry)
            if entry.get("running") and entry.get("start_time"):
                try:
                    st = datetime.fromisoformat(entry["start_time"])
                    enriched["uptime_sec"] = int((now - st).total_seconds())
                except Exception:
                    enriched["uptime_sec"] = 0
            result[fid] = enriched
    return jsonify({"success": True, "uptimes": result})

@app.route("/api/bots/uptime/<file_id>", methods=["POST"])
@require_auth
def api_bots_uptime_set(file_id):
    uid = request.sess["uid"]
    body = request.get_json() or {}
    action = body.get("action")  # "start" or "stop"
    if action == "start":
        start_iso = body.get("start_time", datetime.utcnow().isoformat())
        db_set(f"/bot_uptimes/{uid}/{file_id}", {
            "start_time": start_iso,
            "running": True,
        })
        # Record uptime history entry
        db_push(f"/bot_uptime_history/{uid}/{file_id}", {
            "started_at": start_iso,
            "ts": datetime.utcnow().isoformat(),
        })
    elif action == "stop":
        existing = db_get(f"/bot_uptimes/{uid}/{file_id}") or {}
        if existing.get("start_time"):
            start_ts = datetime.fromisoformat(existing["start_time"])
            uptime_sec = int((datetime.utcnow() - start_ts).total_seconds())
            # Update last history entry with stop time
            hist = db_list(f"/bot_uptime_history/{uid}/{file_id}") or {}
            if hist:
                last_key = sorted(hist.keys())[-1]
                db_update(f"/bot_uptime_history/{uid}/{file_id}/{last_key}", {
                    "stopped_at": datetime.utcnow().isoformat(),
                    "uptime_sec": uptime_sec,
                })
        db_set(f"/bot_uptimes/{uid}/{file_id}", {"running": False, "start_time": None})
    return jsonify({"success": True})

@app.route("/api/bots/uptime-history/<file_id>", methods=["GET"])
@require_auth
def api_bots_uptime_history(file_id):
    uid = request.sess["uid"]
    hist = db_list(f"/bot_uptime_history/{uid}/{file_id}") or {}
    items = sorted(hist.values(), key=lambda x: x.get("ts", ""), reverse=True)[:30]
    # Calculate uptime percentage for last 7 days
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    total_sec = 7 * 24 * 3600
    running_sec = 0
    for item in hist.values():
        try:
            start = datetime.fromisoformat(item["started_at"])
            stop_str = item.get("stopped_at")
            stop = datetime.fromisoformat(stop_str) if stop_str else now
            # Clamp to last 7 days
            s = max(start, week_ago)
            e = min(stop, now)
            if e > s:
                running_sec += (e - s).total_seconds()
        except Exception:
            pass
    pct = round(min(100, (running_sec / total_sec) * 100), 1)
    return jsonify({"success": True, "history": items, "uptime_pct_7d": pct})


# ═══════════════════════════════════════════════════════════
#  BOT STATS HISTORY — save CPU/memory snapshots
# ═══════════════════════════════════════════════════════════

@app.route("/api/bots/stats-history/<file_id>", methods=["POST"])
@require_auth
def api_bots_save_stats(file_id):
    uid = request.sess["uid"]
    body = request.get_json() or {}
    cpu = body.get("cpu", 0)
    mem = body.get("memory_mb", 0)
    db_push(f"/bot_stats_history/{uid}/{file_id}", {
        "ts": datetime.utcnow().isoformat(),
        "cpu": cpu,
        "memory_mb": mem,
    })
    # Keep max 100 entries
    try:
        hist = db_list(f"/bot_stats_history/{uid}/{file_id}") or {}
        if len(hist) > 100:
            oldest = sorted(hist.keys())[:-100]
            for k in oldest:
                db_delete(f"/bot_stats_history/{uid}/{file_id}/{k}")
    except Exception:
        pass
    return jsonify({"success": True})

@app.route("/api/bots/stats-history/<file_id>", methods=["GET"])
@require_auth
def api_bots_get_stats(file_id):
    uid = request.sess["uid"]
    hist = db_list(f"/bot_stats_history/{uid}/{file_id}") or {}
    items = sorted(hist.values(), key=lambda x: x.get("ts", ""))[-50:]
    return jsonify({"success": True, "history": items})


# ═══════════════════════════════════════════════════════════
#  BOT SCHEDULING — cron-style schedules
# ═══════════════════════════════════════════════════════════

@app.route("/api/bots/schedule/<file_id>", methods=["GET"])
@require_auth
def api_bots_schedule_get(file_id):
    uid = request.sess["uid"]
    sched = db_get(f"/bot_schedules/{uid}/{file_id}") or {}
    return jsonify({"success": True, "schedule": sched})

@app.route("/api/bots/schedule/<file_id>", methods=["POST"])
@require_auth
def api_bots_schedule_set(file_id):
    uid = request.sess["uid"]
    body = request.get_json() or {}
    label = body.get("label", "")
    cron = body.get("cron", "")  # e.g. "0 9 * * *"
    action = body.get("action", "restart")  # start/stop/restart
    enabled = bool(body.get("enabled", True))
    db_set(f"/bot_schedules/{uid}/{file_id}", {
        "label": label,
        "cron": cron,
        "action": action,
        "enabled": enabled,
        "updated_at": datetime.utcnow().isoformat(),
    })
    return jsonify({"success": True, "message": "Schedule saved"})

@app.route("/api/bots/schedule/<file_id>", methods=["DELETE"])
@require_auth
def api_bots_schedule_delete(file_id):
    uid = request.sess["uid"]
    db_delete(f"/bot_schedules/{uid}/{file_id}")
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════
#  BOT GROUPS — manage groups
# ═══════════════════════════════════════════════════════════

@app.route("/api/bots/groups", methods=["GET"])
@require_auth
def api_bots_groups_list():
    uid = request.sess["uid"]
    groups = db_list(f"/bot_groups/{uid}") or {}
    return jsonify({"success": True, "groups": list(groups.values())})

@app.route("/api/bots/groups", methods=["POST"])
@require_auth
def api_bots_groups_create():
    uid = request.sess["uid"]
    body = request.get_json() or {}
    name = (body.get("name", "")).strip()
    file_ids = body.get("file_ids", [])
    if not name:
        return jsonify({"success": False, "error": "Group name required"}), 400
    group_id = str(uuid.uuid4())
    db_set(f"/bot_groups/{uid}/{group_id}", {
        "id": group_id,
        "name": name,
        "file_ids": file_ids,
        "created_at": datetime.utcnow().isoformat(),
    })
    return jsonify({"success": True, "group_id": group_id})

@app.route("/api/bots/groups/<group_id>", methods=["DELETE"])
@require_auth
def api_bots_groups_delete(group_id):
    uid = request.sess["uid"]
    db_delete(f"/bot_groups/{uid}/{group_id}")
    return jsonify({"success": True})

@app.route("/api/bots/groups/<group_id>/action", methods=["POST"])
@require_auth
def api_bots_groups_action(group_id):
    sess = request.sess
    uid = sess["uid"]
    body = request.get_json() or {}
    action = body.get("action", "start")
    group = db_get(f"/bot_groups/{uid}/{group_id}")
    if not group:
        return jsonify({"success": False, "error": "Group not found"}), 404
    results = []
    for file_id in (group.get("file_ids") or []):
        f = db_get(f"/files/{uid}/{file_id}")
        if not f:
            continue
        new_status = "active" if action == "start" else "stopped"
        cmd_id = queue_bot_command(uid, action, {"file_name": f["name"], "file_id": file_id})
        r = wait_for_result(uid, cmd_id, timeout=10)
        if r.get("status") != "error":
            db_update(f"/files/{uid}/{file_id}", {"status": new_status})
            results.append({"file_id": file_id, "status": new_status})
    return jsonify({"success": True, "results": results})


# ═══════════════════════════════════════════════════════════
#  FILE TAGS — save tags to Firebase
# ═══════════════════════════════════════════════════════════

@app.route("/api/files/tags", methods=["GET"])
@require_auth
def api_files_tags_get():
    uid = request.sess["uid"]
    raw = db_list(f"/file_tags/{uid}") or {}
    # Normalize: legacy single strings → arrays
    tags = {}
    for fid, val in raw.items():
        if isinstance(val, list):
            tags[fid] = val
        elif isinstance(val, str) and val:
            tags[fid] = [val]
        else:
            tags[fid] = []
    return jsonify({"success": True, "tags": tags})

@app.route("/api/files/tags/<file_id>", methods=["POST"])
@require_auth
def api_files_tags_set(file_id):
    uid = request.sess["uid"]
    body = request.get_json() or {}
    # Accept both "tags" (array) and legacy "tag" (string)
    if "tags" in body:
        tags = body.get("tags", [])
        if not isinstance(tags, list):
            tags = [tags] if tags else []
    else:
        t = body.get("tag", "")
        tags = [t] if t else []
    valid = {"production", "testing", "stable", "dev", "paused"}
    tags = [t for t in tags if t in valid]
    if tags:
        db_set(f"/file_tags/{uid}/{file_id}", tags)
        db_update(f"/files/{uid}/{file_id}", {"tags": tags})
    else:
        db_delete(f"/file_tags/{uid}/{file_id}")
        db_update(f"/files/{uid}/{file_id}", {"tags": []})
    return jsonify({"success": True, "tags": tags})


# ═══════════════════════════════════════════════════════════
#  AUTO-RESTART TOGGLE — saved to Firebase
# ═══════════════════════════════════════════════════════════

@app.route("/api/files/auto_restart/<file_id>", methods=["GET"])
@require_auth
def api_auto_restart_get(file_id):
    uid = request.sess["uid"]
    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found"}), 404
    return jsonify({"success": True, "auto_restart": bool(f.get("auto_restart", False))})

@app.route("/api/files/auto_restart/<file_id>", methods=["POST"])
@require_auth
def api_auto_restart_set(file_id):
    uid = request.sess["uid"]
    f = db_get(f"/files/{uid}/{file_id}")
    if not f:
        return jsonify({"success": False, "error": "File not found"}), 404
    body = request.get_json() or {}
    enabled = bool(body.get("enabled", False))
    db_update(f"/files/{uid}/{file_id}", {"auto_restart": enabled})
    return jsonify({"success": True, "auto_restart": enabled})


# ═══════════════════════════════════════════════════════════
#  GLOBAL SEARCH
# ═══════════════════════════════════════════════════════════

@app.route("/api/search", methods=["GET"])
@require_auth
def api_global_search():
    uid = request.sess["uid"]
    q = (request.args.get("q", "")).lower().strip()
    if not q or len(q) < 2:
        return jsonify({"success": True, "results": []})
    results = []
    # Search files
    files = db_list(f"/files/{uid}") or {}
    for fid, f in files.items():
        if q in (f.get("name", "") or "").lower():
            results.append({
                "type": "file",
                "id": fid,
                "name": f.get("name", ""),
                "status": f.get("status", ""),
                "url": f"/editor?id={fid}&file={f.get('name','')}",
            })
    # Search activity
    acts = db_list("/activity") or {}
    for k, a in acts.items():
        if a.get("uid") == uid and q in (a.get("msg", "") or "").lower():
            results.append({
                "type": "activity",
                "id": k,
                "name": a.get("msg", ""),
                "url": "/activity",
            })
    return jsonify({"success": True, "results": results[:20]})


# ═══════════════════════════════════════════════════════════
#  MULTI-FILE ACTIONS
# ═══════════════════════════════════════════════════════════

@app.route("/api/files/multi-action", methods=["POST"])
@require_auth
def api_files_multi_action():
    sess = request.sess
    uid = sess["uid"]
    body = request.get_json() or {}
    file_ids = body.get("file_ids", [])
    action = body.get("action", "")
    if not file_ids or action not in ("delete", "start", "stop", "tag"):
        return jsonify({"success": False, "error": "file_ids and action required"}), 400
    results = []
    for file_id in file_ids[:50]:
        f = db_get(f"/files/{uid}/{file_id}")
        if not f:
            continue
        try:
            if action == "delete":
                db_delete(f"/files/{uid}/{file_id}")
                db_delete(f"/file_content/{uid}/{file_id}")
                queue_bot_command(uid, "delete", {"file_name": f["name"], "file_id": file_id})
                results.append({"file_id": file_id, "done": True})
            elif action in ("start", "stop"):
                new_status = "active" if action == "start" else "stopped"
                cmd_id = queue_bot_command(uid, action, {"file_name": f["name"], "file_id": file_id})
                r = wait_for_result(uid, cmd_id, timeout=10)
                if r.get("status") != "error":
                    db_update(f"/files/{uid}/{file_id}", {"status": new_status})
                    results.append({"file_id": file_id, "status": new_status})
            elif action == "tag":
                tag = body.get("tag", "")
                if tag:
                    db_set(f"/file_tags/{uid}/{file_id}", tag)
                results.append({"file_id": file_id, "tagged": tag})
        except Exception as e:
            results.append({"file_id": file_id, "error": str(e)})
    log_activity(uid, "edit", f"{sess['name']} bulk {action} on {len(file_ids)} files")
    return jsonify({"success": True, "results": results})


# ═══════════════════════════════════════════════════════════
#  HEALTH
# ═══════════════════════════════════════════════════════════

@app.route("/api/bots/heartbeat", methods=["POST"])
def api_bot_heartbeat():
    """Called by hosting_bot.py to report it's alive."""
    import time
    body = request.get_json() or {}
    uid  = body.get("uid", "")
    if uid:
        db_set(f"/bot_heartbeat/{uid}", {
            "ts":      datetime.utcnow().isoformat(),
            "epoch":   int(time.time()),
            "version": body.get("version", "unknown"),
        })
    return jsonify({"success": True})


@app.route("/api/bots/online-status", methods=["GET"])
@require_auth
def api_bots_online_status():
    """Check if the user's hosting_bot is online (heartbeat in last 60s)."""
    import time
    uid  = request.sess["uid"]
    hb   = db_get(f"/bot_heartbeat/{uid}")
    if not hb:
        return jsonify({"success": True, "online": False, "last_seen": None})
    epoch = hb.get("epoch", 0)
    online = (time.time() - epoch) < 60
    return jsonify({
        "success":   True,
        "online":    online,
        "last_seen": hb.get("ts"),
        "version":   hb.get("version"),
    })

@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"success":True,"status":"ok","time":datetime.utcnow().isoformat()})


# ─── PWA MANIFEST ───────────────────────────────────────────
@app.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": "NexoraCloud",
        "short_name": "Nexora",
        "description": "Host and manage your bots on NexoraCloud",
        "start_url": "/dashboard",
        "display": "standalone",
        "background_color": "#0A0B14",
        "theme_color": "#5B6EF7",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "screenshots": [],
        "categories": ["productivity", "utilities"],
    }
    from flask import make_response
    resp = make_response(jsonify(manifest))
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp

@app.route("/sw.js")
def pwa_service_worker():
    sw_code = """
const CACHE = 'nexora-v1';
const OFFLINE_URLS = ['/dashboard', '/bots', '/files', '/editor'];
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(['/']))
    .then(() => self.skipWaiting())
  );
});
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/')) return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
"""
    from flask import make_response
    resp = make_response(sw_code)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


# ─── ALL PAGE ROUTES ───────────────────────────────────────
@app.route("/terms")
def serve_terms():
    from flask import render_template
    return render_template("terms.html")

@app.route("/privacy")
def serve_privacy():
    from flask import render_template
    return render_template("privacy.html")

@app.route("/api/admin/legal/get", methods=["GET"])
@require_admin
def api_admin_legal_get():
    """Get current terms and privacy policy content."""
    terms   = db_get("/settings/legal/terms")   or ""
    privacy = db_get("/settings/legal/privacy") or ""
    return jsonify({"success": True, "terms": terms, "privacy": privacy})

@app.route("/api/admin/legal/update", methods=["POST"])
@require_admin
def api_admin_legal_update():
    """Update terms or privacy policy content (stored in Firebase)."""
    sess = request.sess
    body = request.get_json() or {}
    doc_type = body.get("type", "")  # "terms" or "privacy"
    content_val = body.get("content", "").strip()
    if doc_type not in ("terms", "privacy"):
        return jsonify({"success": False, "error": "type must be terms or privacy"}), 400
    if not content_val:
        return jsonify({"success": False, "error": "Content cannot be empty"}), 400
    db_set(f"/settings/legal/{doc_type}", content_val)
    log_activity(sess["uid"], "edit", f"{sess['name']} updated {doc_type}")
    return jsonify({"success": True, "message": f"{doc_type.capitalize()} updated"})

@app.route("/api/public/legal/<doc_type>", methods=["GET"])
def api_public_legal(doc_type):
    """Public: get terms or privacy content."""
    if doc_type not in ("terms", "privacy"):
        return jsonify({"error": "not found"}), 404
    content_val = db_get(f"/settings/legal/{doc_type}") or ""
    return jsonify({"content": content_val})

@app.route("/files")
def serve_files():
    from flask import render_template
    return render_template("file_manager.html")

@app.route("/editor")
def serve_editor():
    from flask import render_template
    return render_template("editor.html")

@app.route("/subusers")
def serve_subusers():
    from flask import render_template
    return render_template("subusers.html")

@app.route("/bots")
def serve_bots():
    from flask import render_template
    return render_template("bots.html")

@app.route("/settings")
def serve_settings():
    from flask import render_template
    return render_template("settings.html")

@app.route("/activity")
def serve_activity():
    from flask import render_template
    return render_template("activity.html")

@app.route("/support")
def serve_support():
    from flask import render_template
    # Auth check is handled client-side in support.html (session stored in localStorage)
    return render_template("support.html")


# ═══════════════════════════════════════════════════════════
#  ADMIN — SYSTEM ACTIVITY & SUPPORT TICKETS (proper impl)
# ═══════════════════════════════════════════════════════════

@app.route("/api/admin/support-tickets", methods=["GET"])
@app.route("/api/admin/support/tickets", methods=["GET"])
@require_admin
def api_admin_support_tickets():
    """Admin: list all support tickets."""
    all_tickets = db_list("/support_tickets") or {}
    tickets = sorted(all_tickets.values(), key=lambda x: x.get("created_at",""), reverse=True)
    return jsonify({"success": True, "tickets": tickets[:50]})

@app.route("/api/admin/support/ticket/close", methods=["POST"])
@require_admin
def api_admin_close_ticket():
    """Admin: close a support ticket."""
    sess = request.sess
    body = request.get_json() or {}
    ticket_id = body.get("ticket_id", "")
    if not ticket_id:
        return jsonify({"success": False, "error": "ticket_id required"}), 400
    ticket = db_get(f"/support_tickets/{ticket_id}")
    if not ticket:
        return jsonify({"success": False, "error": "Ticket not found"}), 404
    db_update(f"/support_tickets/{ticket_id}", {"status": "closed"})
    # Notify the ticket owner
    owner_uid = ticket.get("uid", "")
    if owner_uid:
        notify_user(owner_uid, f"🎫 Your support ticket <b>{ticket.get('subject','')}</b> has been closed by admin.")
    log_activity(sess["uid"], "edit", f"Admin closed ticket {ticket_id}")
    return jsonify({"success": True})


@app.route("/api/admin/system-activity", methods=["GET"])
@require_admin
def api_admin_system_activity():
    acts = db_list("/activity") or {}
    items = sorted(acts.values(), key=lambda x: x.get("time",""), reverse=True)
    return jsonify({"success": True, "activity": items[:100]})


# ═══════════════════════════════════════════════════════════
#  EXPOSE get_session ON APP OBJECT (used by blueprints)
# ═══════════════════════════════════════════════════════════

app.get_session = get_session


# ═══════════════════════════════════════════════════════════
#  REGISTER BLUEPRINTS
# ═══════════════════════════════════════════════════════════

from routes.support import support_bp, init_support
from routes.user_settings import user_settings_bp, init_user_settings
from routes.plans import plans_bp, init_plans
from routes.shared_files import shared_files_bp, init_shared_files

# Wire up DB helpers and session into each blueprint
init_support(
    db_get=db_get, db_set=db_set, db_push=db_push,
    db_update=db_update, db_list=db_list, db_delete=db_delete,
    require_auth=require_auth, require_admin=require_admin,
    notify_owner=notify_owner,
)

init_user_settings(
    db_get=db_get, db_update=db_update,
    get_session=get_session,
    firebase_web_api_key=FIREBASE_WEB_API_KEY,
)

init_plans(
    db_get=db_get, db_set=db_set, db_list=db_list, db_update=db_update,
    get_session=get_session,
    notify_owner=notify_owner, notify_user=notify_user,
)

init_shared_files(
    db_get=db_get, db_set=db_set, db_list=db_list,
    db_update=db_update, db_delete=db_delete,
    get_session=get_session,
)

app.register_blueprint(support_bp)
app.register_blueprint(user_settings_bp)
app.register_blueprint(plans_bp)
app.register_blueprint(shared_files_bp)



# ═══════════════════════════════════════════════════════════
#  ADMIN — DELETE ACCOUNT, SUSPEND, DELETION COOLDOWN
# ═══════════════════════════════════════════════════════════

@app.route("/api/admin/user/delete", methods=["POST"])
@require_admin
def api_admin_delete_user():
    sess    = request.sess
    body    = request.get_json() or {}
    tgt_uid = body.get("uid", "")
    reason  = body.get("reason", "").strip()
    cooldown_hours = int(body.get("cooldown_hours", 0))  # 0 = no cooldown
    keep_data = bool(body.get("keep_data", True))  # default: keep backup

    if not tgt_uid:
        return jsonify({"success": False, "error": "uid required"}), 400
    tgt_user = db_get(f"/users/{tgt_uid}") or {}
    if tgt_user.get("role") == "owner":
        return jsonify({"success": False, "error": "Cannot delete owner"}), 403

    email = tgt_user.get("email", "")
    email_key = email.replace(".", "_").replace("@", "__") if email else tgt_uid
    now_iso = datetime.utcnow().isoformat()

    # Always backup user data to /deleted_account_data (for recovery)
    if keep_data:
        backup = {
            "uid": tgt_uid,
            "email": email,
            "name": tgt_user.get("name", ""),
            "role": tgt_user.get("role", "free"),
            "reason": reason,
            "deleted_at": now_iso,
            "deleted_by": sess["uid"],
            "user_data": tgt_user,
        }
        # Backup files metadata
        files_backup = db_list(f"/files/{tgt_uid}") or {}
        backup["files_metadata"] = files_backup
        # Backup file content (for restoring editable files)
        files_content_backup = db_list(f"/file_content/{tgt_uid}") or {}
        backup["files_content"] = files_content_backup
        db_set(f"/deleted_account_data/{tgt_uid}", backup)

    # Save deletion cooldown record
    unlock_at = None
    if cooldown_hours > 0:
        unlock_at = (datetime.utcnow() + timedelta(hours=cooldown_hours)).isoformat()
    db_set(f"/deleted_accounts/{email_key}", {
        "uid": tgt_uid, "email": email, "reason": reason,
        "deleted_at": now_iso,
        "unlock_at": unlock_at,
        "cooldown_hours": cooldown_hours,
        "data_backed_up": keep_data,
    })

    # Invalidate all sessions
    sessions = db_list("/sessions") or {}
    for sid_k, sd in sessions.items():
        if sd.get("uid") == tgt_uid:
            db_delete(f"/sessions/{sid_k}")

    # Delete user data
    db_delete(f"/users/{tgt_uid}")
    db_delete(f"/files/{tgt_uid}")
    db_delete(f"/file_content/{tgt_uid}")
    db_delete(f"/file_logs/{tgt_uid}")
    db_delete(f"/notifications/{tgt_uid}")
    db_delete(f"/subusers/{tgt_uid}")

    log_activity(sess["uid"], "delete", f"{sess['name']} deleted account {email or tgt_uid}: {reason}")
    return jsonify({"success": True, "message": "Account deleted", "data_backed_up": keep_data})


@app.route("/api/admin/deleted-account/restore", methods=["POST"])
@require_admin
def api_admin_restore_account():
    """Restore a deleted account from backup."""
    sess    = request.sess
    body    = request.get_json() or {}
    tgt_uid = body.get("uid", "")
    if not tgt_uid:
        return jsonify({"success": False, "error": "uid required"}), 400

    backup = db_get(f"/deleted_account_data/{tgt_uid}")
    if not backup:
        return jsonify({"success": False, "error": "No backup data found for this account"}), 404

    # Restore user record
    user_data = backup.get("user_data", {})
    if not user_data:
        return jsonify({"success": False, "error": "Backup data is empty"}), 400

    user_data["suspended"] = False
    user_data["banned"] = False
    user_data["restored_at"] = datetime.utcnow().isoformat()
    user_data["restored_by"] = sess["uid"]
    db_set(f"/users/{tgt_uid}", user_data)

    # Restore files metadata
    files_meta = backup.get("files_metadata", {})
    if files_meta:
        db_set(f"/files/{tgt_uid}", files_meta)

    # Restore file content (so code editor works after restore)
    files_content = backup.get("files_content", {})
    if files_content:
        db_set(f"/file_content/{tgt_uid}", files_content)

    # Remove cooldown so they can log in
    email = backup.get("email", "")
    if email:
        email_key = email.replace(".", "_").replace("@", "__")
        db_delete(f"/deleted_accounts/{email_key}")

    log_activity(sess["uid"], "unban", f"{sess['name']} restored deleted account {email or tgt_uid}")
    return jsonify({"success": True, "message": f"Account {email or tgt_uid} restored successfully"})


@app.route("/api/admin/deleted-account/remove-cooldown", methods=["POST"])
@require_admin
def api_admin_remove_cooldown():
    """Remove re-registration cooldown from a deleted account."""
    sess = request.sess
    body = request.get_json() or {}
    uid_or_email = body.get("uid", "") or body.get("email", "")
    if not uid_or_email:
        return jsonify({"success": False, "error": "uid or email required"}), 400

    # Try by UID first
    backup = db_get(f"/deleted_account_data/{uid_or_email}")
    if backup:
        email = backup.get("email", "")
        if email:
            email_key = email.replace(".", "_").replace("@", "__")
            db_update(f"/deleted_accounts/{email_key}", {"unlock_at": None, "cooldown_hours": 0})
    else:
        # Try treating it as email_key directly
        db_update(f"/deleted_accounts/{uid_or_email}", {"unlock_at": None, "cooldown_hours": 0})

    log_activity(sess["uid"], "unban", f"{sess['name']} removed cooldown for {uid_or_email}")
    return jsonify({"success": True, "message": "Cooldown removed"})


@app.route("/api/admin/deleted-account/edit-registration", methods=["POST"])
@require_admin
def api_admin_edit_deleted_registration():
    """Edit registration block (reason, cooldown) for a deleted account."""
    sess = request.sess
    body = request.get_json() or {}
    uid  = body.get("uid", "")
    new_reason = body.get("reason", "").strip()
    new_cooldown_hours = body.get("cooldown_hours")

    backup = db_get(f"/deleted_account_data/{uid}")
    if not backup:
        return jsonify({"success": False, "error": "Account not found in backups"}), 404

    email = backup.get("email", "")
    email_key = email.replace(".", "_").replace("@", "__") if email else uid

    update_data = {}
    if new_reason is not None:
        update_data["reason"] = new_reason
    if new_cooldown_hours is not None:
        if int(new_cooldown_hours) > 0:
            unlock_at = (datetime.utcnow() + timedelta(hours=int(new_cooldown_hours))).isoformat()
            update_data["unlock_at"] = unlock_at
            update_data["cooldown_hours"] = int(new_cooldown_hours)
        else:
            update_data["unlock_at"] = None
            update_data["cooldown_hours"] = 0

    if update_data:
        db_update(f"/deleted_accounts/{email_key}", update_data)

    log_activity(sess["uid"], "edit", f"{sess['name']} edited deletion record for {uid}")
    return jsonify({"success": True, "message": "Updated"})


@app.route("/api/admin/deleted-account-backups", methods=["GET"])
@require_admin
def api_admin_deleted_backups():
    """List all backed-up deleted accounts."""
    data = db_list("/deleted_account_data") or {}
    items = sorted(data.values(), key=lambda x: x.get("deleted_at", ""), reverse=True)
    # Don't send full user_data — just metadata
    result = []
    for item in items:
        result.append({
            "uid": item.get("uid"),
            "email": item.get("email"),
            "name": item.get("name"),
            "role": item.get("role"),
            "reason": item.get("reason"),
            "deleted_at": item.get("deleted_at"),
            "deleted_by": item.get("deleted_by"),
            "files_count": len(item.get("files_metadata", {})),
        })
    return jsonify({"success": True, "backups": result})


@app.route("/api/admin/user/suspend", methods=["POST"])
@require_admin
def api_admin_suspend_user():
    sess    = request.sess
    body    = request.get_json() or {}
    tgt_uid = body.get("uid", "")
    reason  = body.get("reason", "").strip()
    suspend = bool(body.get("suspend", True))
    duration_hours = body.get("duration_hours")  # None = permanent

    if not tgt_uid:
        return jsonify({"success": False, "error": "uid required"}), 400
    tgt_user = db_get(f"/users/{tgt_uid}") or {}
    if tgt_user.get("role") == "owner":
        return jsonify({"success": False, "error": "Cannot suspend owner"}), 403
    if not reason and suspend:
        return jsonify({"success": False, "error": "Reason required"}), 400

    if suspend:
        show_on_login = bool(body.get("show_on_login", False))
        update_data = {
            "suspended": True,
            "suspend_reason": reason,
            "suspended_at": datetime.utcnow().isoformat(),
            "suspend_until": None,
            "suspend_show_on_login": show_on_login,
        }
        if duration_hours:
            until = (datetime.utcnow() + timedelta(hours=float(duration_hours))).isoformat()
            update_data["suspend_until"] = until
        db_update(f"/users/{tgt_uid}", update_data)
        # Invalidate sessions
        sessions = db_list("/sessions") or {}
        for sid_k, sd in sessions.items():
            if sd.get("uid") == tgt_uid:
                db_delete(f"/sessions/{sid_k}")
        notify_user(tgt_uid, f"🔒 Your account has been <b>suspended</b>.<br>Reason: {reason}")
        log_activity(sess["uid"], "ban", f"{sess['name']} suspended {tgt_user.get('name',tgt_uid)}: {reason}")
    else:
        db_update(f"/users/{tgt_uid}", {"suspended": False, "suspend_reason": None, "suspend_until": None})
        notify_user(tgt_uid, "✅ Your account suspension has been lifted. Welcome back!")
        log_activity(sess["uid"], "unban", f"{sess['name']} unsuspended {tgt_user.get('name',tgt_uid)}")

    return jsonify({"success": True})


@app.route("/api/admin/deleted-accounts", methods=["GET"])
@require_admin
def api_admin_deleted_accounts():
    data = db_list("/deleted_accounts") or {}
    items = sorted(data.values(), key=lambda x: x.get("deleted_at",""), reverse=True)
    return jsonify({"success": True, "accounts": items})


@app.route("/api/auth/check-cooldown", methods=["POST"])
def api_check_cooldown():
    """Called at login to check if email is under deletion cooldown."""
    body  = request.get_json() or {}
    email = body.get("email", "").strip().lower()
    if not email:
        return jsonify({"success": True, "blocked": False})
    key = email.replace('.','_').replace('@','__')
    rec = db_get(f"/deleted_accounts/{key}")
    if not rec:
        return jsonify({"success": True, "blocked": False})
    unlock_at_str = rec.get("unlock_at")
    if not unlock_at_str:
        return jsonify({"success": True, "blocked": False})
    try:
        unlock_at = datetime.fromisoformat(unlock_at_str)
        if datetime.utcnow() < unlock_at:
            remaining = unlock_at - datetime.utcnow()
            hours_left = int(remaining.total_seconds() / 3600) + 1
            return jsonify({
                "success": True,
                "blocked": True,
                "reason": rec.get("reason",""),
                "unlock_at": unlock_at_str,
                "hours_left": hours_left,
            })
    except Exception:
        pass
    # Cooldown expired — remove record
    db_delete(f"/deleted_accounts/{key}")
    return jsonify({"success": True, "blocked": False})


# Patch get_session to handle suspensions
_orig_make_session = _make_session

def _make_session(uid):
    user = db_get(f"/users/{uid}") or {}
    # Auto-lift expired suspension
    if user.get("suspended") and user.get("suspend_until"):
        try:
            if datetime.utcnow() >= datetime.fromisoformat(user["suspend_until"]):
                db_update(f"/users/{uid}", {"suspended": False, "suspend_reason": None, "suspend_until": None})
                user["suspended"] = False
        except Exception:
            pass
    role = user.get("role", "free")
    if uid == OWNER_UID and OWNER_UID:
        role = "owner"
    return {
        "uid":             uid,
        "role":            role,
        "name":            user.get("name",""),
        "email":           user.get("email",""),
        "tg_chat_id":      user.get("tg_chat_id"),
        "banned":          user.get("banned", False),
        "ban_reason":      user.get("ban_reason",""),
        "suspended":       user.get("suspended", False),
        "suspend_reason":  user.get("suspend_reason",""),
        "suspend_until":   user.get("suspend_until"),
    }
