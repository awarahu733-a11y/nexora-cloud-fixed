# ============================================================
#  NEXORA CLOUD — Support Ticket Routes
#  Handles: create ticket, list tickets, get ticket, reply
#  Storage: Firebase RTDB (/support_tickets/)
# ============================================================

import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify

support_bp = Blueprint("support", __name__)

# These will be injected from app.py via init_support()
_db_get = None
_db_set = None
_db_push = None
_db_update = None
_db_list = None
_db_delete = None
_require_auth = None
_require_admin = None
_notify_owner = None


def init_support(db_get, db_set, db_push, db_update, db_list, db_delete,
                 require_auth, require_admin, notify_owner):
    global _db_get, _db_set, _db_push, _db_update, _db_list, _db_delete
    global _require_auth, _require_admin, _notify_owner
    _db_get = db_get
    _db_set = db_set
    _db_push = db_push
    _db_update = db_update
    _db_list = db_list
    _db_delete = db_delete
    _require_auth = require_auth
    _require_admin = require_admin
    _notify_owner = notify_owner


# ── POST /api/support/ticket ─────────────────────────────
@support_bp.route("/api/support/ticket", methods=["POST"])
def create_ticket():
    # Manual auth check (blueprint can't use decorator from app easily)
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    # We rely on the auth check injected via require_auth pattern
    # Instead we use the session helper directly
    from flask import current_app
    sess = current_app.get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    data = request.get_json(silent=True) or {}
    category = data.get("category", "general").strip()
    subject = data.get("subject", "").strip()
    message = data.get("message", "").strip()

    if not subject or not message:
        return jsonify({"success": False, "error": "Subject and message are required"})

    ticket_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    ticket = {
        "id": ticket_id,
        "user_id": uid,
        "category": category,
        "subject": subject,
        "status": "open",
        "created_at": now,
        "updated_at": now,
    }
    # Store ticket metadata
    _db_set(f"/support_tickets/{ticket_id}", ticket)

    # Store first message separately
    _db_push(f"/support_messages/{ticket_id}", {
        "role": "user",
        "uid": uid,
        "message": message,
        "created_at": now,
    })

    _notify_owner(
        f"🎫 <b>New Support Ticket</b>\n"
        f"User: {sess.get('name', uid)}\n"
        f"Subject: {subject}\n"
        f"Category: {category}"
    )

    return jsonify({"success": True, "message": "Ticket created", "ticket_id": ticket_id})


# ── GET /api/support/tickets ──────────────────────────────
@support_bp.route("/api/support/tickets", methods=["GET"])
def list_tickets():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    from flask import current_app
    sess = current_app.get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    role = sess.get("role", "free")

    all_tickets = _db_list("/support_tickets") or {}

    # Admins/owners see all tickets; regular users see only their own
    if role in ("admin", "owner"):
        category_filter = request.args.get("category")
        tickets = list(all_tickets.values())
        if category_filter:
            tickets = [t for t in tickets if t.get("category") == category_filter]
    else:
        tickets = [t for t in all_tickets.values() if t.get("user_id") == uid]

    tickets.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "tickets": tickets[:100]})


# ── GET /api/support/ticket/<id> ──────────────────────────
@support_bp.route("/api/support/ticket/<ticket_id>", methods=["GET"])
def get_ticket(ticket_id):
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    from flask import current_app
    sess = current_app.get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    role = sess.get("role", "free")

    ticket = _db_get(f"/support_tickets/{ticket_id}")
    if not ticket:
        return jsonify({"success": False, "error": "Ticket not found"}), 404

    # Access control
    if ticket.get("user_id") != uid and role not in ("admin", "owner"):
        return jsonify({"success": False, "error": "Access denied"}), 403

    # Load messages
    msgs_raw = _db_list(f"/support_messages/{ticket_id}") or {}
    messages = sorted(msgs_raw.values(), key=lambda x: x.get("created_at", ""))

    # Clear unread flag when user (non-admin) views the ticket
    is_admin_view = role in ("admin", "owner")
    if not is_admin_view and ticket.get("has_unread_reply"):
        _db_update(f"/support_tickets/{ticket_id}", {"has_unread_reply": False})
        ticket["has_unread_reply"] = False

    return jsonify({"success": True, "ticket": ticket, "messages": messages})


# ── POST /api/support/reply ───────────────────────────────
@support_bp.route("/api/support/reply", methods=["POST"])
def reply_ticket():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    from flask import current_app
    sess = current_app.get_session(sid)
    if not sess:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    uid = sess["uid"]
    role = sess.get("role", "free")
    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id", "").strip()
    message = data.get("message", "").strip()

    if not ticket_id or not message:
        return jsonify({"success": False, "error": "ticket_id and message required"})

    ticket = _db_get(f"/support_tickets/{ticket_id}")
    if not ticket:
        return jsonify({"success": False, "error": "Ticket not found"}), 404

    is_admin = role in ("admin", "owner")
    if ticket.get("user_id") != uid and not is_admin:
        return jsonify({"success": False, "error": "Access denied"}), 403

    if ticket.get("status") == "closed" and not is_admin:
        return jsonify({"success": False, "error": "Ticket is closed"})

    now = datetime.utcnow().isoformat()
    _db_push(f"/support_messages/{ticket_id}", {
        "role": "admin" if is_admin else "user",
        "uid": uid,
        "message": message,
        "created_at": now,
    })
    update_data = {"updated_at": now}
    # If admin replies → mark ticket as having unread reply for the user
    if is_admin:
        update_data["has_unread_reply"] = True
        update_data["last_admin_reply_at"] = now
    else:
        # User replied → clear unread flag
        update_data["has_unread_reply"] = False
    _db_update(f"/support_tickets/{ticket_id}", update_data)

    return jsonify({"success": True, "message": "Reply sent"})


# ══════════════════════════════════════════════════════════
#  ADMIN SUPPORT ROUTES
# ══════════════════════════════════════════════════════════

# ── GET /api/admin/support/tickets ────────────────────────
@support_bp.route("/api/admin/support/tickets", methods=["GET"])
def admin_list_tickets():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    from flask import current_app
    sess = current_app.get_session(sid)
    if not sess or sess.get("role") not in ("admin", "owner"):
        return jsonify({"success": False, "error": "Admin required"}), 403

    all_tickets = _db_list("/support_tickets") or {}
    category_filter = request.args.get("category")
    tickets = list(all_tickets.values())
    if category_filter:
        tickets = [t for t in tickets if t.get("category") == category_filter]
    tickets.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "tickets": tickets[:200]})


# ── POST /api/admin/support/ticket/close ──────────────────
@support_bp.route("/api/admin/support/ticket/close", methods=["POST"])
def admin_close_ticket():
    sid = request.headers.get("X-Session-Id") or request.cookies.get("nsid", "")
    from flask import current_app
    sess = current_app.get_session(sid)
    if not sess or sess.get("role") not in ("admin", "owner"):
        return jsonify({"success": False, "error": "Admin required"}), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id", "").strip()
    if not ticket_id:
        return jsonify({"success": False, "error": "ticket_id required"})

    ticket = _db_get(f"/support_tickets/{ticket_id}")
    if not ticket:
        return jsonify({"success": False, "error": "Ticket not found"}), 404

    _db_update(f"/support_tickets/{ticket_id}", {
        "status": "closed",
        "closed_at": datetime.utcnow().isoformat(),
    })
    return jsonify({"success": True, "message": "Ticket closed"})
