import os
import sys
import csv
import io
import json as _json
import urllib.request as _ur
import uuid
from functools import wraps
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, Response,
)
from werkzeug.utils import secure_filename
from database.db import Database

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024  # 3 MB max upload

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}

db = Database()

PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "admin1234")

VALID_ROLES = {"admin", "moderator", "dev", "team", "viewer"}
INTERNAL_ROLES = {"admin", "moderator", "dev", "team"}
# Team and above can manage members; moderator/dev cannot
MEMBER_MANAGER_ROLES = {"admin", "team"}
# Roles that can contact owner support
CONTACT_SUPPORT_ROLES = {"team", "dev"}

# ── Template context ──────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    unread = 0
    if session.get("logged_in") and session.get("role") == "admin":
        unread = db.get_unread_support_count()
    return {"unread_support_count": unread}


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def _session_project_ids():
    return session.get("project_ids", [])


def _can_internal_notes():
    return session.get("role") in INTERNAL_ROLES


def _can_manage_tickets():
    """Team, Dev, Mod and Admin can fully manage tickets."""
    return session.get("role") in {"admin", "team", "dev", "moderator"}


def _can_manage_members(project_id):
    if session.get("role") == "admin":
        return True
    if session.get("role") == "team":
        return project_id in _session_project_ids()
    return False


def _can_contact_support():
    return session.get("role") in CONTACT_SUPPORT_ROLES


def _get_project_theme():
    pids = _session_project_ids()
    if pids and session.get("role") != "admin":
        project = db.get_project(pids[0])
        if project:
            return {
                "primary_color": project.get("primary_color") or "#c9a84c",
                "site_name": project.get("site_name") or project["name"],
                "logo_url": project.get("logo_url") or "",
            }
    return {"primary_color": "#c9a84c", "site_name": "Roof of Top", "logo_url": ""}


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg_send(chat_id, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return
    payload = _json.dumps({
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    req = _ur.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        _ur.urlopen(req, timeout=10)
    except Exception as e:
        app.logger.warning(f"Telegram sendMessage error: {e}")


_STATUS_USER_MSG = {
    "open":        "📬 Ticket *#{tid}* has been reopened. The {project} team is on it.",
    "in_progress": "⚙️ Ticket *#{tid}* is now being reviewed by the {project} team. We'll update you shortly.",
    "resolved":    "✅ Ticket *#{tid}* has been resolved. If the issue persists, open a new ticket and reference this one.",
    "unresolved":  "❌ Ticket *#{tid}* was closed without resolution. The {project} team has noted the issue.",
    "closed":      "🔒 Ticket *#{tid}* has been closed. Thanks for reaching out to {project} support.",
}


def _notify_status_change(ticket, new_status):
    tid = ticket["ticket_id"]
    project = ticket.get("project_name") or "Support"
    user_msg = _STATUS_USER_MSG.get(new_status, "")
    if user_msg:
        _tg_send(
            ticket["user_telegram_id"],
            user_msg.replace("{tid}", tid).replace("{project}", project),
        )


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if (not username or username.lower() == "admin") and password == PANEL_PASSWORD:
            session["logged_in"] = True
            session["role"] = "admin"
            session["username"] = "admin"
            session["user_id"] = 0
            session["project_ids"] = []
            return redirect(url_for("index"))

        if username:
            user = db.get_web_user(username)
            from database.db import hash_password
            if user and user["password_hash"] == hash_password(password):
                legacy = [int(x) for x in user["project_ids"].split(",") if x.strip()]
                from_members = db.get_user_projects_from_members(user["id"])
                all_pids = list(set(legacy + from_members))
                session["logged_in"] = True
                session["role"] = user["role"]
                session["username"] = user["username"]
                session["user_id"] = user["id"]
                session["project_ids"] = all_pids
                return redirect(url_for("index"))

        error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    is_admin = session.get("role") == "admin"
    pids = _session_project_ids()
    theme = _get_project_theme()

    if is_admin:
        projects = db.get_all_projects()
        unread_count = db.get_unread_support_count()
        recent_support = db.get_support_messages()[:5]
        return render_template(
            "index.html",
            projects=projects,
            unread_count=unread_count,
            recent_support=recent_support,
            theme=theme,
            is_admin=True,
        )
    else:
        if pids:
            stats = db.get_stats(pids[0] if len(pids) == 1 else None)
            recent_tickets, _ = db.get_all_tickets_paginated(
                page=1, per_page=10, allowed_project_ids=pids
            )
        else:
            stats = db.get_stats()
            recent_tickets, _ = db.get_all_tickets_paginated(page=1, per_page=10)
        return render_template(
            "index.html",
            stats=stats,
            recent_tickets=recent_tickets,
            theme=theme,
            is_admin=False,
        )


@app.route("/tickets")
@login_required
def tickets():
    if session.get("role") == "admin":
        return redirect(url_for("index"))
    page = int(request.args.get("page", 1))
    project_id = request.args.get("project_id", type=int)
    status = request.args.get("status")
    per_page = 20
    pids = _session_project_ids()

    allowed = pids if pids else None
    if allowed and project_id and project_id not in allowed:
        project_id = None

    tickets_list, total = db.get_all_tickets_paginated(
        page=page, per_page=per_page,
        project_id=project_id, status=status,
        allowed_project_ids=allowed,
    )
    projects = [p for p in db.get_all_projects() if p["id"] in pids] if pids else db.get_all_projects()
    total_pages = (total + per_page - 1) // per_page
    theme = _get_project_theme()
    return render_template(
        "tickets.html",
        tickets=tickets_list, projects=projects,
        page=page, total_pages=total_pages, total=total,
        project_id=project_id, status=status, theme=theme,
    )


@app.route("/tickets/export")
@login_required
def export_tickets():
    pids = _session_project_ids()
    project_id = request.args.get("project_id", type=int)
    status = request.args.get("status")
    allowed = pids if pids else None
    tickets_list, _ = db.get_all_tickets_paginated(
        page=1, per_page=10000,
        project_id=project_id, status=status,
        allowed_project_ids=allowed,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Ticket ID", "Project", "User", "Description",
        "Wallet", "Blockchain", "TX Hash", "Screenshot",
        "Severity", "Status", "Assigned Mod", "Created", "Updated",
    ])
    for t in tickets_list:
        writer.writerow([
            t.get("ticket_id", ""), t.get("project_name", ""),
            t.get("username") or t.get("user_telegram_id", ""),
            t.get("description", ""), t.get("wallet_address", ""),
            t.get("blockchain", ""), t.get("tx_hash", ""),
            "Yes" if t.get("has_screenshot") else "No",
            t.get("severity", ""), t.get("status", ""),
            t.get("assigned_mod_username", ""),
            (t.get("created_at") or "")[:16],
            (t.get("updated_at") or "")[:16],
        ])

    filename = f"tickets_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/ticket/<int:ticket_db_id>")
@login_required
def ticket_detail(ticket_db_id):
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    if not ticket:
        return redirect(url_for("tickets"))
    pids = _session_project_ids()
    if pids and ticket.get("project_id") not in pids:
        return redirect(url_for("tickets"))
    theme = _get_project_theme()
    can_internal = _can_internal_notes()
    can_manage = _can_manage_tickets()
    return render_template(
        "ticket_detail.html", ticket=ticket, theme=theme,
        can_internal=can_internal, can_manage=can_manage,
    )


@app.route("/projects")
@admin_required
def projects():
    theme = _get_project_theme()
    return render_template("projects.html", projects=db.get_all_projects(), theme=theme)


@app.route("/project/<int:project_id>")
@login_required
def project_detail(project_id):
    is_admin = session.get("role") == "admin"
    pids = _session_project_ids()
    if not is_admin and project_id not in pids:
        return redirect(url_for("index"))
    project = db.get_project(project_id)
    if not project:
        return redirect(url_for("projects") if is_admin else url_for("index"))
    stats = db.get_stats(project_id)
    recent_tickets, _ = db.get_all_tickets_paginated(page=1, per_page=10, project_id=project_id)
    members = db.get_project_members(project_id)
    can_manage = _can_manage_members(project_id)
    theme = _get_project_theme()
    return render_template(
        "project_detail.html",
        project=project, stats=stats,
        recent_tickets=recent_tickets, members=members,
        can_manage=can_manage, theme=theme, is_admin=is_admin,
    )


@app.route("/members")
@login_required
def members_page():
    pids = _session_project_ids()
    if not pids:
        return redirect(url_for("index"))
    project_id = pids[0]
    project = db.get_project(project_id)
    members = db.get_project_members(project_id)
    can_manage = _can_manage_members(project_id)
    theme = _get_project_theme()
    return render_template(
        "members.html",
        project=project, members=members,
        can_manage=can_manage, theme=theme,
    )


# ── Support Messages ──────────────────────────────────────────────────────────

@app.route("/support")
@admin_required
def support():
    threads = db.get_support_messages()
    theme = _get_project_theme()
    return render_template("support.html", threads=threads, theme=theme)


@app.route("/contact-support")
@login_required
def contact_support():
    if not _can_contact_support():
        return redirect(url_for("index"))
    pids = _session_project_ids()
    project = db.get_project(pids[0]) if pids else None
    user_id = session.get("user_id", 0)
    my_threads = db.get_user_support_threads(user_id)
    theme = _get_project_theme()
    return render_template(
        "contact_support.html",
        project=project, my_threads=my_threads, theme=theme,
    )


@app.route("/users")
@admin_required
def users():
    theme = _get_project_theme()
    return render_template(
        "users.html",
        web_users=db.get_all_web_users(),
        projects=db.get_all_projects(),
        theme=theme,
    )


# ── Screenshot proxy ──────────────────────────────────────────────────────────

@app.route("/ticket/<int:ticket_db_id>/screenshot")
@login_required
def ticket_screenshot(ticket_db_id):
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    if not ticket or not ticket.get("screenshot_file_id"):
        return "No screenshot", 404
    pids = _session_project_ids()
    if pids and ticket.get("project_id") not in pids:
        return "Forbidden", 403
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    try:
        res = _ur.urlopen(
            f"https://api.telegram.org/bot{token}/getFile"
            f"?file_id={ticket['screenshot_file_id']}",
            timeout=10,
        )
        data = _json.loads(res.read())
        file_path = data["result"]["file_path"]
        img = _ur.urlopen(
            f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=15
        )
        content_type = img.headers.get("Content-Type", "image/jpeg")
        return Response(img.read(), mimetype=content_type)
    except Exception as e:
        return f"Error: {e}", 500


# ── Ticket API ────────────────────────────────────────────────────────────────

@app.route("/api/ticket/<int:ticket_db_id>/status", methods=["POST"])
@login_required
def update_status(ticket_db_id):
    status = (request.json or {}).get("status")
    if status not in ["open", "in_progress", "resolved", "closed", "unresolved"]:
        return jsonify({"error": "Invalid status"}), 400
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    if not ticket:
        return jsonify({"error": "Not found"}), 404
    db.update_ticket_status(ticket_db_id, status)
    ticket["status"] = status
    _notify_status_change(ticket, status)
    return jsonify({"ok": True})


@app.route("/api/ticket/<int:ticket_db_id>/severity", methods=["POST"])
@login_required
def update_severity(ticket_db_id):
    severity = (request.json or {}).get("severity")
    if severity not in ["low", "medium", "high", "critical"]:
        return jsonify({"error": "Invalid severity"}), 400
    db.update_ticket_severity(ticket_db_id, severity)
    return jsonify({"ok": True})


@app.route("/api/ticket/<int:ticket_db_id>/assign", methods=["POST"])
@login_required
def assign_ticket(ticket_db_id):
    """Take or unassign this ticket."""
    if not _can_manage_tickets():
        return jsonify({"error": "Not authorized"}), 403
    data = request.json or {}
    if data.get("unassign"):
        db.assign_ticket(ticket_db_id, None, None)
        return jsonify({"ok": True, "assigned_to": None})
    username = session.get("username", "Unknown")
    user_id = str(session.get("user_id", "web"))
    db.assign_ticket(ticket_db_id, user_id, username)
    db.update_ticket_status(ticket_db_id, "in_progress")
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    project = ticket.get("project_name") or "Support"
    _tg_send(
        ticket["user_telegram_id"],
        f"⚙️ Ticket *#{ticket['ticket_id']}* is now being reviewed by the "
        f"*{project} team*. We'll get back to you shortly.",
    )
    return jsonify({"ok": True, "assigned_to": username})


@app.route("/api/ticket/<int:ticket_db_id>/message", methods=["POST"])
@login_required
def send_message(ticket_db_id):
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    if not ticket:
        return jsonify({"error": "Not found"}), 404
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    sender_name = session.get("username", "Team")
    db.add_mod_response(ticket_db_id, "web_admin", sender_name, message)
    project_name = ticket.get("project_name") or "Support"
    _tg_send(
        ticket["user_telegram_id"],
        f"💬 *{project_name} Support — #{ticket['ticket_id']}*\n\n{message}",
    )
    return jsonify({"ok": True})


@app.route("/api/ticket/<int:ticket_db_id>/messages", methods=["GET"])
@login_required
def get_messages(ticket_db_id):
    return jsonify(db.get_ticket_messages(ticket_db_id))


@app.route("/api/ticket/<int:ticket_db_id>/internal", methods=["POST"])
@login_required
def send_internal_note(ticket_db_id):
    if not _can_internal_notes():
        return jsonify({"error": "Not authorized"}), 403
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    if not ticket:
        return jsonify({"error": "Not found"}), 404
    data = request.json or {}
    message = data.get("message", "").strip()
    photo_data = data.get("photo_data")
    if not message and not photo_data:
        return jsonify({"error": "Empty message"}), 400
    sender_name = session.get("username", "Team")
    role = session.get("role", "")
    db.add_message(
        ticket_db_id, role, "web", sender_name,
        message or "[photo]", is_internal=True, photo_data=photo_data,
    )
    return jsonify({"ok": True})


@app.route("/api/ticket/<int:ticket_db_id>/internal", methods=["GET"])
@login_required
def get_internal_notes(ticket_db_id):
    if not _can_internal_notes():
        return jsonify({"error": "Not authorized"}), 403
    return jsonify(db.get_internal_notes(ticket_db_id))


# ── Logo Upload ───────────────────────────────────────────────────────────────

@app.route("/api/upload-logo", methods=["POST"])
@admin_required
def upload_logo():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Invalid file type"}), 400
    filename = f"{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(UPLOAD_FOLDER, filename))
    return jsonify({"ok": True, "url": f"/static/uploads/{filename}"})


# ── Projects API ──────────────────────────────────────────────────────────────

@app.route("/api/projects", methods=["GET"])
@admin_required
def api_projects():
    return jsonify(db.get_all_projects())


@app.route("/api/projects", methods=["POST"])
@admin_required
def api_create_project():
    data = request.json or {}
    project = db.create_project(
        name=data.get("name"),
        group_chat_id=data.get("group_chat_id") or None,
        site_name=data.get("site_name") or None,
        logo_url=data.get("logo_url") or None,
        primary_color=data.get("primary_color") or None,
    )
    return jsonify(project)


@app.route("/api/projects/<int:project_id>", methods=["PUT"])
@admin_required
def api_update_project(project_id):
    data = request.json or {}
    db.update_project(
        project_id=project_id,
        name=data.get("name"),
        group_chat_id=data.get("group_chat_id"),
        site_name=data.get("site_name"),
        logo_url=data.get("logo_url"),
        primary_color=data.get("primary_color"),
    )
    return jsonify({"ok": True})


@app.route("/api/projects/<int:project_id>", methods=["DELETE"])
@admin_required
def api_delete_project(project_id):
    db.delete_project(project_id)
    return jsonify({"ok": True})


@app.route("/api/stats")
@login_required
def api_stats():
    project_id = request.args.get("project_id", type=int)
    return jsonify(db.get_stats(project_id))


# ── Project Members API ───────────────────────────────────────────────────────

@app.route("/api/project/<int:project_id>/members", methods=["GET"])
@login_required
def api_get_members(project_id):
    pids = _session_project_ids()
    if session.get("role") != "admin" and project_id not in pids:
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(db.get_project_members(project_id))


@app.route("/api/project/<int:project_id>/members", methods=["POST"])
@login_required
def api_add_member(project_id):
    if not _can_manage_members(project_id):
        return jsonify({"error": "Not authorized"}), 403
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "moderator")
    is_admin_user = session.get("role") == "admin"
    allowed = VALID_ROLES if is_admin_user else {"moderator", "dev", "viewer", "team"}
    if role not in allowed:
        return jsonify({"error": f"Role '{role}' not allowed"}), 400
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    existing = db.get_web_user(username)
    if existing:
        web_user_id = existing["id"]
    else:
        db.create_web_user(username, password, role, str(project_id))
        user = db.get_web_user(username)
        web_user_id = user["id"]
    db.add_project_member(project_id, web_user_id, role, added_by=session.get("username", ""))
    return jsonify({"ok": True})


@app.route("/api/project/<int:project_id>/members/<int:member_id>", methods=["PUT"])
@login_required
def api_update_member(project_id, member_id):
    if not _can_manage_members(project_id):
        return jsonify({"error": "Not authorized"}), 403
    role = (request.json or {}).get("role")
    is_admin_user = session.get("role") == "admin"
    allowed = VALID_ROLES if is_admin_user else {"moderator", "dev", "viewer", "team"}
    if role not in allowed:
        return jsonify({"error": f"Role '{role}' not allowed"}), 400
    db.update_project_member_role(member_id, role)
    return jsonify({"ok": True})


@app.route("/api/project/<int:project_id>/members/<int:member_id>", methods=["DELETE"])
@login_required
def api_remove_member(project_id, member_id):
    if not _can_manage_members(project_id):
        return jsonify({"error": "Not authorized"}), 403
    db.remove_project_member(member_id)
    return jsonify({"ok": True})


# ── Support Messages API ──────────────────────────────────────────────────────

@app.route("/api/support-messages", methods=["POST"])
@login_required
def api_send_support_message():
    if not _can_contact_support():
        return jsonify({"error": "Not authorized"}), 403
    data = request.json or {}
    message = data.get("message", "").strip()
    photo_data = data.get("photo_data")
    thread_id = data.get("thread_id")
    if not message and not photo_data:
        return jsonify({"error": "Message or photo required"}), 400
    pids = _session_project_ids()
    project = db.get_project(pids[0]) if pids else None
    msg_id = db.create_support_message(
        from_user_id=session.get("user_id", 0),
        from_username=session.get("username", "unknown"),
        from_role=session.get("role", "team"),
        project_id=project["id"] if project else None,
        project_name=project.get("site_name") or project["name"] if project else "Unknown",
        message=message or "[photo]",
        photo_data=photo_data,
        thread_id=int(thread_id) if thread_id else None,
    )
    return jsonify({"ok": True, "id": msg_id})


@app.route("/api/support-messages", methods=["GET"])
@admin_required
def api_get_support_messages():
    return jsonify(db.get_support_messages())


@app.route("/api/support-messages/<int:msg_id>/thread", methods=["GET"])
@login_required
def api_get_thread(msg_id):
    # Admin sees all; team sees only their own threads
    msg = db.get_support_message(msg_id)
    if not msg:
        return jsonify({"ok": True, "messages": []})
    if session.get("role") != "admin" and msg.get("from_user_id") != session.get("user_id"):
        return jsonify({"error": "Forbidden"}), 403
    replies = db.get_support_messages(thread_id=msg_id)
    return jsonify({"ok": True, "messages": [msg] + replies})


@app.route("/api/support-messages/<int:msg_id>/reply", methods=["POST"])
@login_required
def api_reply_support_message(msg_id):
    is_admin = session.get("role") == "admin"
    can_contact = _can_contact_support()
    if not is_admin and not can_contact:
        return jsonify({"error": "Not authorized"}), 403
    data = request.json or {}
    message = data.get("message", "").strip()
    photo_data = data.get("photo_data")
    if not message and not photo_data:
        return jsonify({"error": "Empty reply"}), 400
    root = db.get_support_message(msg_id)
    if not root:
        return jsonify({"error": "Not found"}), 404
    # Non-admin can only reply to their own threads
    if not is_admin and root.get("from_user_id") != session.get("user_id"):
        return jsonify({"error": "Forbidden"}), 403
    from_username = "Owner" if is_admin else session.get("username", "team")
    from_role = session.get("role", "team")
    db.create_support_message(
        from_user_id=session.get("user_id", 0),
        from_username=from_username,
        from_role=from_role,
        project_id=root.get("project_id"),
        project_name=root.get("project_name"),
        message=message or "[photo]",
        photo_data=photo_data,
        thread_id=msg_id,
    )
    if is_admin:
        db.mark_support_read(msg_id)
    return jsonify({"ok": True})


@app.route("/api/support-messages/<int:msg_id>/read", methods=["PUT"])
@admin_required
def api_mark_support_read(msg_id):
    db.mark_support_read(msg_id)
    return jsonify({"ok": True})


# ── Global Users API (admin only) ────────────────────────────────────────────

@app.route("/api/users", methods=["POST"])
@admin_required
def api_create_user():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "viewer")
    project_ids = data.get("project_ids", "")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    if role not in VALID_ROLES:
        return jsonify({"error": "Invalid role"}), 400
    if db.get_web_user(username):
        return jsonify({"error": "Username already exists"}), 409
    db.create_web_user(username, password, role, project_ids)
    return jsonify({"ok": True})


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@admin_required
def api_delete_user(user_id):
    db.delete_web_user(user_id)
    return jsonify({"ok": True})


@app.route("/api/users/<int:user_id>/password", methods=["PUT"])
@admin_required
def api_update_user_password(user_id):
    data = request.json or {}
    new_password = data.get("password", "").strip()
    if not new_password:
        return jsonify({"error": "Password required"}), 400
    db.update_web_user_password(user_id, new_password)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
