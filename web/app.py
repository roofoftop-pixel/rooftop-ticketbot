import os
import sys
import json as _json
import urllib.request as _ur
import urllib.parse as _up
from functools import wraps
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, Response,
)
from database.db import Database

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
db = Database()

PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "admin1234")

# Valid roles
VALID_ROLES = {"admin", "moderator", "dev", "team", "viewer"}

# Roles that can post internal notes
INTERNAL_ROLES = {"admin", "moderator", "dev", "team"}

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


def _get_project_theme():
    """Return theme dict for the current user's project (viewers only)."""
    pids = _session_project_ids()
    if pids and session.get("role") != "admin":
        project = db.get_project(pids[0])
        if project:
            return {
                "primary_color": project.get("primary_color") or "#c9a84c",
                "site_name": project.get("site_name") or project["name"],
                "logo_url": project.get("logo_url") or "",
            }
    return {"primary_color": "#c9a84c", "site_name": "Support Panel", "logo_url": ""}


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
    "open":        "📬 Ticket *#{tid}* has been reopened. The Roof of Top team is on it.",
    "in_progress": "⚙️ Ticket *#{tid}* is now being reviewed by our support team. We'll update you shortly.",
    "resolved":    "✅ Ticket *#{tid}* has been marked as resolved. If the issue persists, open a new ticket and reference this one.",
    "unresolved":  "❌ Ticket *#{tid}* was closed without resolution. Our team has noted the issue and will work on a fix.",
    "closed":      "🔒 Ticket *#{tid}* has been closed. Thanks for reaching out to Roof of Top support.",
}


def _notify_status_change(ticket, new_status):
    tid = ticket["ticket_id"]
    user_msg = _STATUS_USER_MSG.get(new_status, "")
    if user_msg:
        _tg_send(ticket["user_telegram_id"], user_msg.replace("{tid}", tid))


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Admin login
        if (not username or username.lower() == "admin") and password == PANEL_PASSWORD:
            session["logged_in"] = True
            session["role"] = "admin"
            session["username"] = "admin"
            session["project_ids"] = []
            return redirect(url_for("index"))

        # Web user login
        if username:
            user = db.get_web_user(username)
            from database.db import hash_password
            if user and user["password_hash"] == hash_password(password):
                pids = [int(x) for x in user["project_ids"].split(",") if x.strip()]
                session["logged_in"] = True
                session["role"] = user["role"]
                session["username"] = user["username"]
                session["project_ids"] = pids
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
    pids = _session_project_ids()
    if pids:
        stats = db.get_stats(pids[0] if len(pids) == 1 else None)
        recent_tickets, _ = db.get_all_tickets_paginated(
            page=1, per_page=10, allowed_project_ids=pids
        )
        projects = [p for p in db.get_all_projects() if p["id"] in pids]
    else:
        stats = db.get_stats()
        recent_tickets, _ = db.get_all_tickets_paginated(page=1, per_page=10)
        projects = db.get_all_projects()
    theme = _get_project_theme()
    return render_template(
        "index.html", stats=stats, projects=projects,
        recent_tickets=recent_tickets, theme=theme,
    )


@app.route("/tickets")
@login_required
def tickets():
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
    if pids:
        projects = [p for p in db.get_all_projects() if p["id"] in pids]
    else:
        projects = db.get_all_projects()
    total_pages = (total + per_page - 1) // per_page
    theme = _get_project_theme()
    return render_template(
        "tickets.html",
        tickets=tickets_list, projects=projects,
        page=page, total_pages=total_pages, total=total,
        project_id=project_id, status=status, theme=theme,
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
    return render_template(
        "ticket_detail.html", ticket=ticket, theme=theme,
        can_internal=can_internal,
    )


@app.route("/projects")
@admin_required
def projects():
    theme = _get_project_theme()
    return render_template("projects.html", projects=db.get_all_projects(), theme=theme)


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


# ── API ───────────────────────────────────────────────────────────────────────

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


@app.route("/api/ticket/<int:ticket_db_id>/message", methods=["POST"])
@login_required
def send_message(ticket_db_id):
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    if not ticket:
        return jsonify({"error": "Not found"}), 404
    message = (request.json or {}).get("message", "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    sender_name = session.get("username", "Team")
    db.add_mod_response(ticket_db_id, "web_admin", sender_name, message)
    _tg_send(
        ticket["user_telegram_id"],
        f"💬 *Roof of Top Support — #{ticket['ticket_id']}*\n\n{message}",
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
    message = (request.json or {}).get("message", "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    sender_name = session.get("username", "Team")
    role = session.get("role", "")
    db.add_message(
        ticket_db_id, role, "web", sender_name, message, is_internal=True
    )
    return jsonify({"ok": True})


@app.route("/api/ticket/<int:ticket_db_id>/internal", methods=["GET"])
@login_required
def get_internal_notes(ticket_db_id):
    if not _can_internal_notes():
        return jsonify({"error": "Not authorized"}), 403
    return jsonify(db.get_internal_notes(ticket_db_id))


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


# ── Users API (admin only) ────────────────────────────────────────────────────

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
