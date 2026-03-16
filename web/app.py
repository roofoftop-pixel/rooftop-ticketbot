import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from database.db import Database
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
db = Database()

PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "admin1234")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ─── AUTH ────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == PANEL_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Contraseña incorrecta"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── PAGES ───────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    stats = db.get_stats()
    projects = db.get_all_projects()
    recent_tickets, _ = db.get_all_tickets_paginated(page=1, per_page=10)
    return render_template("index.html", stats=stats, projects=projects, recent_tickets=recent_tickets)

@app.route("/tickets")
@login_required
def tickets():
    page = int(request.args.get("page", 1))
    project_id = request.args.get("project_id", type=int)
    status = request.args.get("status")
    per_page = 20
    tickets_list, total = db.get_all_tickets_paginated(page=page, per_page=per_page, project_id=project_id, status=status)
    projects = db.get_all_projects()
    total_pages = (total + per_page - 1) // per_page
    return render_template("tickets.html",
        tickets=tickets_list, projects=projects,
        page=page, total_pages=total_pages, total=total,
        project_id=project_id, status=status
    )

@app.route("/ticket/<int:ticket_db_id>")
@login_required
def ticket_detail(ticket_db_id):
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    if not ticket:
        return redirect(url_for("tickets"))
    return render_template("ticket_detail.html", ticket=ticket)

@app.route("/projects")
@login_required
def projects():
    projects_list = db.get_all_projects()
    return render_template("projects.html", projects=projects_list)

# ─── API ENDPOINTS ────────────────────────────────────────────────────────────

@app.route("/api/ticket/<int:ticket_db_id>/status", methods=["POST"])
@login_required
def update_status(ticket_db_id):
    status = request.json.get("status")
    valid = ["open", "in_progress", "resolved", "closed", "unresolved"]
    if status not in valid:
        return jsonify({"error": "Invalid status"}), 400
    db.update_ticket_status(ticket_db_id, status)
    return jsonify({"ok": True})

@app.route("/api/ticket/<int:ticket_db_id>/severity", methods=["POST"])
@login_required
def update_severity(ticket_db_id):
    severity = request.json.get("severity")
    valid = ["low", "medium", "high", "critical"]
    if severity not in valid:
        return jsonify({"error": "Invalid severity"}), 400
    db.update_ticket_severity(ticket_db_id, severity)
    return jsonify({"ok": True})

@app.route("/api/projects", methods=["GET"])
@login_required
def api_projects():
    return jsonify(db.get_all_projects())

@app.route("/api/projects", methods=["POST"])
@login_required
def api_create_project():
    data = request.json
    project = db.create_project(
        name=data.get("name"),
        group_chat_id=data.get("group_chat_id"),
        staff_chat_id=data.get("staff_chat_id")
    )
    return jsonify(project)

@app.route("/api/projects/<int:project_id>", methods=["PUT"])
@login_required
def api_update_project(project_id):
    data = request.json
    db.update_project(
        project_id=project_id,
        name=data.get("name"),
        group_chat_id=data.get("group_chat_id"),
        staff_chat_id=data.get("staff_chat_id")
    )
    return jsonify({"ok": True})

@app.route("/api/projects/<int:project_id>", methods=["DELETE"])
@login_required
def api_delete_project(project_id):
    db.delete_project(project_id)
    return jsonify({"ok": True})

@app.route("/api/stats")
@login_required
def api_stats():
    project_id = request.args.get("project_id", type=int)
    return jsonify(db.get_stats(project_id))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
