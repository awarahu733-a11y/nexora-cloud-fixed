# ============================================================
#  NEXORA CLOUD — User Settings Routes
#  Handles: change password, update profile/display name
#  Note: Firebase Auth manages passwords, so password change
#        goes through Firebase REST API (Email/Password sign-in)
# ============================================================

import json
import urllib.request
import urllib.error
from datetime import datetime
from flask import Blueprint, request, jsonify

user_settings_bp = Blueprint("user_settings", __name__)

_db_get = None
_db_update = None
_get_session = None
FIREBASE_WEB_API_KEY = None


def init_user_settings(db_get, db_update, get_session, firebase_web_api_key):
    global _db_get, _db_update, _get_session, FIREBASE_WEB_API_KEY
    _db_get = db_get
    _db_update = db_update
    _get_session = get_session
    FIREBASE_WEB_API_KEY = firebase_web_api_key


def _firebase_sign_in(email, password):
    """Re-authenticate user with Firebase to verify current password."""
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
    payload = json.dumps({
        "email": email,
        "password": password,
        "returnSecureToken": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        err_body = json.loads(e.read())
        err_msg = err_body.get("error", {}).get("message", "AUTH_ERROR")
        if "INVALID_PASSWORD" in err_msg or "INVALID_LOGIN_CREDENTIALS" in err_msg:
            return None, "Current password is incorrect"
        if "TOO_MANY_ATTEMPTS" in err_msg:
            return None, "Too many attempts. Try again later."
        return None, f"Auth error: {err_msg}"
    except Exception as e:
        return None, f"Network error: {str(e)}"


def _firebase_change_password(id_token, new_password):
    """Change user's Firebase Auth password using their ID token."""
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={FIREBASE_WEB_API_KEY}"
    payload = json.dumps({
        "idToken": id_token,
        "password": new_password,
        "returnSecureToken": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        err_body = json.loads(e.read())
        err_msg = err_body.get("error", {}).get("message", "UPDATE_ERROR")
        return None, f"Password update failed: {err_msg}"
    except Exception as e:
        return None, f"Network error: {str(e)}"


# ── POST /api/auth/change-password ───────────────────────
@user_settings_bp.route("/api/auth/change-password", methods=["POST"])
def change_password():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    data = request.get_json(silent=True) or {}
    current_password = data.get("current", "").strip()
    new_password = data.get("password", "").strip()

    if not current_password or not new_password:
        return jsonify({"success": False, "error": "Current and new password required"})

    if len(new_password) < 6:
        return jsonify({"success": False, "error": "New password must be at least 6 characters"})

    if current_password == new_password:
        return jsonify({"success": False, "error": "New password must be different from current"})

    # Get user's email from DB to re-authenticate
    user = _db_get(f"/users/{uid}") or {}
    email = user.get("email", "")
    if not email:
        return jsonify({"success": False, "error": "Could not retrieve account email"})

    # Step 1: Verify current password by signing in
    auth_result, auth_err = _firebase_sign_in(email, current_password)
    if auth_err:
        return jsonify({"success": False, "error": auth_err})

    # Step 2: Change password using the fresh ID token
    fresh_id_token = auth_result.get("idToken")
    _, change_err = _firebase_change_password(fresh_id_token, new_password)
    if change_err:
        return jsonify({"success": False, "error": change_err})

    return jsonify({"success": True, "message": "Password updated successfully"})


# ── POST /api/auth/update-profile ────────────────────────
@user_settings_bp.route("/api/auth/update-profile", methods=["POST"])
def update_profile():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()

    if not name:
        return jsonify({"success": False, "error": "Display name required"})

    if len(name) > 50:
        return jsonify({"success": False, "error": "Name must be 50 characters or less"})

    _db_update(f"/users/{uid}", {
        "name": name,
        "updated_at": datetime.utcnow().isoformat(),
    })

    return jsonify({"success": True, "message": "Profile updated", "name": name})
