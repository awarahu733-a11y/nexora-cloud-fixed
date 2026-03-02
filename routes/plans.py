# ============================================================
#  NEXORA CLOUD — Plan Upgrade Routes
#  Handles: submit upgrade request, admin approve/reject
#  Storage: Firebase RTDB (/upgrade_requests/)
# ============================================================

import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify

plans_bp = Blueprint("plans", __name__)

_db_get = None
_db_set = None
_db_list = None
_db_update = None
_get_session = None
_notify_owner = None
_notify_user = None


def init_plans(db_get, db_set, db_list, db_update, get_session,
               notify_owner, notify_user):
    global _db_get, _db_set, _db_list, _db_update
    global _get_session, _notify_owner, _notify_user
    _db_get = db_get
    _db_set = db_set
    _db_list = db_list
    _db_update = db_update
    _get_session = get_session
    _notify_owner = notify_owner
    _notify_user = notify_user


VALID_PLANS = {"free", "subscribed", "pro", "enterprise"}


# ── POST /api/plans/upgrade-request ──────────────────────
@plans_bp.route("/api/plans/upgrade-request", methods=["POST"])
def submit_upgrade_request():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    data = request.get_json(silent=True) or {}
    requested_plan = data.get("requested_plan", "").strip().lower()
    note = data.get("note", "").strip()[:500]

    if requested_plan not in VALID_PLANS:
        return jsonify({"success": False, "error": f"Invalid plan. Choose from: {', '.join(VALID_PLANS)}"})

    current_role = sess.get("role", "free")
    if current_role == requested_plan:
        return jsonify({"success": False, "error": "You already have this plan"})

    # Check for existing pending request
    existing = _db_list(f"/upgrade_requests") or {}
    for req_data in existing.values():
        if req_data.get("user_id") == uid and req_data.get("status") == "pending":
            return jsonify({
                "success": False,
                "error": "You already have a pending upgrade request",
                "pending": True,
            })

    request_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    upgrade_req = {
        "id": request_id,
        "user_id": uid,
        "user_name": sess.get("name", ""),
        "user_email": sess.get("email", ""),
        "current_plan": current_role,
        "requested_plan": requested_plan,
        "note": note,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    _db_set(f"/upgrade_requests/{request_id}", upgrade_req)

    _notify_owner(
        f"⬆️ <b>Plan Upgrade Request</b>\n"
        f"User: {sess.get('name', uid)}\n"
        f"Email: {sess.get('email', '')}\n"
        f"From: {current_role} → {requested_plan}\n"
        f"Note: {note or 'None'}"
    )

    return jsonify({"success": True, "message": "Upgrade request submitted. Admin will review shortly.",
                    "request_id": request_id})


# ── GET /api/plans/my-request ────────────────────────────
@plans_bp.route("/api/plans/my-request", methods=["GET"])
def my_upgrade_request():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    all_reqs = _db_list("/upgrade_requests") or {}
    user_reqs = [r for r in all_reqs.values() if r.get("user_id") == uid]
    user_reqs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    pending = next((r for r in user_reqs if r.get("status") == "pending"), None)

    return jsonify({"success": True, "pending_request": pending, "all_requests": user_reqs[:10]})


# ══════════════════════════════════════════════════════════
#  ADMIN UPGRADE ROUTES
# ══════════════════════════════════════════════════════════

# ── GET /api/admin/upgrade-requests ──────────────────────
@plans_bp.route("/api/admin/upgrade-requests", methods=["GET"])
def admin_list_requests():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess or sess.get("role") not in ("admin", "owner"):
        return jsonify({"success": False, "error": "Admin required"}), 403

    all_reqs = _db_list("/upgrade_requests") or {}
    status_filter = request.args.get("status")
    reqs = list(all_reqs.values())
    if status_filter:
        reqs = [r for r in reqs if r.get("status") == status_filter]
    reqs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "requests": reqs})


# ── POST /api/admin/upgrade-requests/<id>/approve ────────
@plans_bp.route("/api/admin/upgrade-requests/<req_id>/approve", methods=["POST"])
def admin_approve_request(req_id):
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess or sess.get("role") not in ("admin", "owner"):
        return jsonify({"success": False, "error": "Admin required"}), 403

    upgrade_req = _db_get(f"/upgrade_requests/{req_id}")
    if not upgrade_req:
        return jsonify({"success": False, "error": "Request not found"}), 404

    if upgrade_req.get("status") != "pending":
        return jsonify({"success": False, "error": "Request is not pending"})

    target_uid = upgrade_req["user_id"]
    new_plan = upgrade_req["requested_plan"]
    now = datetime.utcnow().isoformat()

    # Update user role
    _db_update(f"/users/{target_uid}", {"role": new_plan, "upgraded_at": now})
    # Update request status
    _db_update(f"/upgrade_requests/{req_id}", {
        "status": "approved",
        "reviewed_by": sess["uid"],
        "reviewed_at": now,
        "updated_at": now,
    })

    _notify_user(target_uid,
        f"✅ <b>Plan Upgrade Approved!</b>\n"
        f"Your plan has been upgraded to: <b>{new_plan}</b>"
    )

    return jsonify({"success": True, "message": f"Upgrade approved — user set to {new_plan}"})


# ── POST /api/admin/upgrade-requests/<id>/reject ─────────
@plans_bp.route("/api/admin/upgrade-requests/<req_id>/reject", methods=["POST"])
def admin_reject_request(req_id):
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    sess = _get_session(sid)
    if not sess or sess.get("role") not in ("admin", "owner"):
        return jsonify({"success": False, "error": "Admin required"}), 403

    upgrade_req = _db_get(f"/upgrade_requests/{req_id}")
    if not upgrade_req:
        return jsonify({"success": False, "error": "Request not found"}), 404

    if upgrade_req.get("status") != "pending":
        return jsonify({"success": False, "error": "Request is not pending"})

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "Request declined by admin").strip()
    now = datetime.utcnow().isoformat()
    target_uid = upgrade_req["user_id"]

    _db_update(f"/upgrade_requests/{req_id}", {
        "status": "rejected",
        "reject_reason": reason,
        "reviewed_by": sess["uid"],
        "reviewed_at": now,
        "updated_at": now,
    })

    _notify_user(target_uid,
        f"❌ <b>Plan Upgrade Declined</b>\n"
        f"Reason: {reason}"
    )

    return jsonify({"success": True, "message": "Upgrade request rejected"})
