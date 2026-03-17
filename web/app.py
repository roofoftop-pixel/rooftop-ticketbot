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
    """Lista de project_ids permitidos para el usuario actual. Vacío = todos (admin)."""
    return session.get("project_ids", [])


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


def _tg_edit(chat_id, message_id, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id or not message_id:
        return
    payload = _json.dumps({
        "chat_id": str(chat_id),
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    req = _ur.Request(
        f"https://api.telegram.org/bot{token}/editMessageText",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        _ur.urlopen(req, timeout=10)
    except Exception as e:
        app.logger.warning(f"Telegram editMessage error: {e}")


_STATUS_USER_MSG = {
    "open":        "📬 Tu ticket *#{tid}* fue reabierto.",
    "in_progress": "⚙️ Tu ticket *#{tid}* está siendo atendido por el equipo.",
    "resolved":    "✅ Tu ticket *#{tid}* fue marcado como *resuelto*. Si el problema persiste, abrí un nuevo ticket.",
    "unresolved":  "❌ Tu ticket *#{tid}* fue cerrado *sin solución*. El equipo está al tanto.",
    "closed":      "🔒 Tu ticket *#{tid}* fue cerrado.",
}

_STATUS_STAFF_LABEL = {
    "open":        "📬 REABIERTO",
    "in_progress": "⚙️ EN PROGRESO",
    "resolved":    "✅ RESUELTO",
    "unresolved":  "❌ SIN SOLUCIÓN",
    "closed":      "🔒 CERRADO",
}


def _notify_status_change(ticket, new_status):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tid = ticket["ticket_id"]

    # Notificar al usuario por Telegram
    user_msg = _STATUS_USER_MSG.get(new_status, "")
    if user_msg:
        _tg_send(ticket["user_telegram_id"], user_msg.replace("{tid}", tid))

    # Editar el mensaje del grupo staff
    label = _STATUS_STAFF_LABEL.get(new_status, new_status.upper())
    staff_text = (
        f"🎫 *Ticket #{tid}* — {label}\n"
        f"📁 {ticket.get('project_name','?')} · "
        f"👤 @{ticket.get('username') or ticket['user_telegram_id']}\n\n"
        f"_{ticket['description'][:150]}_\n\n"
        f"🕐 {now}"
    )
    _tg_edit(ticket.get("staff_chat_id"), ticket.get("staff_message_id"), staff_text)


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Admin login (username vacío o "admin" → PANEL_PASSWORD)
        if (not username or username.lower() == "admin") and password == PANEL_PASSWORD:
            session["logged_in"] = True
            session["role"] = "admin"
            session["username"] = "admin"
            session["project_ids"] = []
            return redirect(url_for("index"))

        # Viewer login (web_users table)
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

        error = "Credenciales incorrectas"
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
        # Viewer: stats solo de sus proyectos (primer proyecto para simplificar)
        stats = db.get_stats(pids[0] if len(pids) == 1 else None)
        _, _ = None, None
        recent_tickets, _ = db.get_all_tickets_paginated(
            page=1, per_page=10, allowed_project_ids=pids
        )
        projects = [p for p in db.get_all_projects() if p["id"] in pids]
    else:
        stats = db.get_stats()
        recent_tickets, _ = db.get_all_tickets_paginated(page=1, per_page=10)
        projects = db.get_all_projects()
    return render_template(
        "index.html", stats=stats, projects=projects, recent_tickets=recent_tickets
    )


@app.route("/tickets")
@login_required
def tickets():
    page = int(request.args.get("page", 1))
    project_id = request.args.get("project_id", type=int)
    status = request.args.get("status")
    per_page = 20
    pids = _session_project_ids()

    # Viewers solo ven sus proyectos
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
    return render_template(
        "tickets.html",
        tickets=tickets_list, projects=projects,
        page=page, total_pages=total_pages, total=total,
        project_id=project_id, status=status,
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
    return render_template("ticket_detail.html", ticket=ticket)


@app.route("/projects")
@admin_required
def projects():
    return render_template("projects.html", projects=db.get_all_projects())


@app.route("/users")
@admin_required
def users():
    return render_template("users.html",
                           web_users=db.get_all_web_users(),
                           projects=db.get_all_projects())


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
    # Recargar con status actualizado para las notificaciones
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
    sender_name = f"Admin (web) — {session.get('username','?')}"
    db.add_mod_response(ticket_db_id, "web_admin", sender_name, message)
    _tg_send(
        ticket["user_telegram_id"],
        f"💬 *Respuesta del moderador — Ticket #{ticket['ticket_id']}:*\n\n{message}",
    )
    return jsonify({"ok": True})


@app.route("/api/ticket/<int:ticket_db_id>/messages", methods=["GET"])
@login_required
def get_messages(ticket_db_id):
    return jsonify(db.get_ticket_messages(ticket_db_id))


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
        group_chat_id=data.get("group_chat_id"),
        staff_chat_id=data.get("staff_chat_id"),
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
        staff_chat_id=data.get("staff_chat_id"),
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
