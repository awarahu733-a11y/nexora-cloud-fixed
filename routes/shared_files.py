# ============================================================
#  NEXORA CLOUD — Sub-User Shared Files Routes
#  Handles: shared-with-me, permissions management
#  Storage: Firebase RTDB (/shared_files/)
# ============================================================

from datetime import datetime
from flask import Blueprint, request, jsonify

shared_files_bp = Blueprint("shared_files", __name__)

_db_get = None
_db_set = None
_db_list = None
_db_update = None
_db_delete = None
_get_session = None


def init_shared_files(db_get, db_set, db_list, db_update, db_delete, get_session):
    global _db_get, _db_set, _db_list, _db_update, _db_delete, _get_session
    _db_get = db_get
    _db_set = db_set
    _db_list = db_list
    _db_update = db_update
    _db_delete = db_delete
    _get_session = get_session


VALID_PERMISSIONS = {"read", "run", "edit"}


# ── GET /api/subusers/shared-with-me ─────────────────────
@shared_files_bp.route("/api/subusers/shared-with-me", methods=["GET"])
def shared_with_me():
    """Returns files shared with the current user by their owners."""
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]

    # Find all owners who have this user as an active sub-user
    all_owners_raw = _db_list("/subusers") or {}
    shared_files = []

    for owner_uid, owner_data in all_owners_raw.items():
        active = owner_data.get("active", {}) if isinstance(owner_data, dict) else {}
        if uid not in active:
            continue

        sub_entry = active[uid]
        permissions = sub_entry.get("permissions", [])
        if not permissions:
            continue

        # Get owner's files
        owner_files = _db_list(f"/files/{owner_uid}") or {}
        owner_info = _db_get(f"/users/{owner_uid}") or {}

        for file_id, file_data in owner_files.items():
            # Check if there's a file-level override in shared_files
            file_perms_override = _db_get(f"/shared_files/{owner_uid}/{uid}/{file_id}")
            effective_perms = file_perms_override.get("permissions", permissions) if file_perms_override else permissions

            if effective_perms:
                shared_files.append({
                    "file_id": file_id,
                    "owner_uid": owner_uid,
                    "owner_name": owner_info.get("name", "Unknown"),
                    "owner_email": owner_info.get("email", ""),
                    "name": file_data.get("name", ""),
                    "type": file_data.get("type", ""),
                    "size": file_data.get("size", ""),
                    "status": file_data.get("status", "stopped"),
                    "upload_time": file_data.get("upload_time", ""),
                    "permissions": effective_perms,
                    "can_read": "read" in effective_perms,
                    "can_run": "run" in effective_perms,
                    "can_edit": "edit" in effective_perms,
                })

    return jsonify({"success": True, "files": shared_files, "count": len(shared_files)})


# ── GET /api/subusers/permissions ────────────────────────
@shared_files_bp.route("/api/subusers/permissions", methods=["GET"])
def get_permissions():
    """Owner gets all sub-user permissions for their files."""
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]

    # Active sub-users
    active_raw = _db_list(f"/subusers/{uid}/active") or {}
    result = []
    for sub_uid, sub_data in active_raw.items():
        sub_user_info = _db_get(f"/users/{sub_uid}") or {}
        # File-level overrides
        file_perms = _db_list(f"/shared_files/{uid}/{sub_uid}") or {}
        result.append({
            "sub_uid": sub_uid,
            "email": sub_data.get("invitee_email") or sub_user_info.get("email", ""),
            "name": sub_user_info.get("name", ""),
            "default_permissions": sub_data.get("permissions", []),
            "file_overrides": file_perms,
        })

    return jsonify({"success": True, "permissions": result})


# ── POST /api/subusers/update-permissions ────────────────
@shared_files_bp.route("/api/subusers/update-permissions", methods=["POST"])
def update_permissions():
    """Owner updates permissions for a sub-user (global or per-file)."""
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    data = request.get_json(silent=True) or {}
    sub_uid = data.get("sub_uid", "").strip()
    permissions = data.get("permissions", [])
    file_id = data.get("file_id")  # Optional — if set, updates per-file perms

    if not sub_uid:
        return jsonify({"success": False, "error": "sub_uid required"})

    # Validate permissions
    invalid = set(permissions) - VALID_PERMISSIONS
    if invalid:
        return jsonify({"success": False, "error": f"Invalid permissions: {invalid}. Use: read, run, edit"})

    # Verify sub_uid is actually a sub-user of this owner
    active_entry = _db_get(f"/subusers/{uid}/active/{sub_uid}")
    if not active_entry:
        return jsonify({"success": False, "error": "Sub-user not found"}), 404

    if file_id:
        # Per-file permission override
        file_data = _db_get(f"/files/{uid}/{file_id}")
        if not file_data:
            return jsonify({"success": False, "error": "File not found"}), 404

        if permissions:
            _db_set(f"/shared_files/{uid}/{sub_uid}/{file_id}", {
                "file_id": file_id,
                "file_name": file_data.get("name", ""),
                "permissions": permissions,
                "updated_at": datetime.utcnow().isoformat(),
            })
        else:
            # Empty permissions = remove override (falls back to default)
            _db_delete(f"/shared_files/{uid}/{sub_uid}/{file_id}")

        return jsonify({"success": True, "message": "File permissions updated"})
    else:
        # Update global/default permissions for this sub-user
        active_entry["permissions"] = permissions
        active_entry["updated_at"] = datetime.utcnow().isoformat()
        _db_set(f"/subusers/{uid}/active/{sub_uid}", active_entry)
        # Also sync to user_invites so sub-user's view reflects this
        _db_update(f"/user_invites/{sub_uid}/{uid}", {"permissions": permissions})
        return jsonify({"success": True, "message": "Permissions updated"})
