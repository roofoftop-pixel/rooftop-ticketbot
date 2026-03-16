import sqlite3
import os
import random
import string
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "tickets.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def generate_ticket_id():
    suffix = ''.join(random.choices(string.digits, k=4))
    return f"TKT-{suffix}"

class Database:
    def __init__(self):
        self.init_tables()

    def init_tables(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                group_chat_id TEXT,
                staff_chat_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT UNIQUE NOT NULL,
                project_id INTEGER REFERENCES projects(id),
                user_telegram_id TEXT NOT NULL,
                username TEXT,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                severity TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'open',
                assigned_mod_id TEXT,
                assigned_mod_username TEXT,
                mod_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER REFERENCES tickets(id),
                sender_type TEXT NOT NULL,
                sender_id TEXT,
                sender_username TEXT,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        conn.close()

    def get_all_projects(self):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_project(self, project_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_project_by_group_id(self, group_chat_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM projects WHERE group_chat_id = ?", (group_chat_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def create_project(self, name, group_chat_id=None, staff_chat_id=None):
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO projects (name, group_chat_id, staff_chat_id) VALUES (?, ?, ?)",
            (name, group_chat_id, staff_chat_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.close()
        return dict(row)

    def update_project(self, project_id, name=None, group_chat_id=None, staff_chat_id=None):
        conn = get_connection()
        if name:
            conn.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))
        if group_chat_id is not None:
            conn.execute("UPDATE projects SET group_chat_id = ? WHERE id = ?", (group_chat_id, project_id))
        if staff_chat_id is not None:
            conn.execute("UPDATE projects SET staff_chat_id = ? WHERE id = ?", (staff_chat_id, project_id))
        conn.commit()
        conn.close()

    def delete_project(self, project_id):
        conn = get_connection()
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        conn.close()

    def create_ticket(self, project_id, user_telegram_id, username, category, description, severity):
        conn = get_connection()
        ticket_id = generate_ticket_id()
        while conn.execute("SELECT id FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone():
            ticket_id = generate_ticket_id()
        cur = conn.execute(
            """INSERT INTO tickets (ticket_id, project_id, user_telegram_id, username, category, description, severity)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticket_id, project_id, user_telegram_id, username, category, description, severity)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.close()
        ticket = dict(row)
        project = self.get_project(project_id)
        ticket["project_name"] = project["name"] if project else "Desconocido"
        return ticket

    def get_ticket_by_db_id(self, db_id):
        conn = get_connection()
        row = conn.execute("""
            SELECT t.*, p.name as project_name, p.staff_chat_id
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.id = ?
        """, (db_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_ticket_by_ticket_id(self, ticket_id):
        conn = get_connection()
        row = conn.execute("""
            SELECT t.*, p.name as project_name, p.staff_chat_id
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.ticket_id = ?
        """, (ticket_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_tickets_by_user(self, user_telegram_id):
        conn = get_connection()
        rows = conn.execute("""
            SELECT t.*, p.name as project_name
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.user_telegram_id = ? AND t.status NOT IN ('closed')
            ORDER BY t.created_at DESC LIMIT 10
        """, (user_telegram_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_open_tickets(self, project_id=None):
        conn = get_connection()
        if project_id:
            rows = conn.execute("""
                SELECT t.*, p.name as project_name
                FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
                WHERE t.status IN ('open', 'in_progress') AND t.project_id = ?
                ORDER BY t.created_at ASC
            """, (project_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT t.*, p.name as project_name
                FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
                WHERE t.status IN ('open', 'in_progress')
                ORDER BY t.created_at ASC
            """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_tickets_by_mod(self, mod_telegram_id):
        conn = get_connection()
        rows = conn.execute("""
            SELECT t.*, p.name as project_name
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.assigned_mod_id = ? AND t.status = 'in_progress'
            ORDER BY t.created_at ASC
        """, (mod_telegram_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def assign_ticket(self, ticket_id, mod_id, mod_username):
        conn = get_connection()
        conn.execute(
            "UPDATE tickets SET assigned_mod_id=?, assigned_mod_username=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (mod_id, mod_username, ticket_id)
        )
        conn.commit()
        conn.close()

    def update_ticket_status(self, ticket_id, status):
        conn = get_connection()
        conn.execute(
            "UPDATE tickets SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, ticket_id)
        )
        conn.commit()
        conn.close()

    def update_ticket_severity(self, ticket_id, severity):
        conn = get_connection()
        conn.execute(
            "UPDATE tickets SET severity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (severity, ticket_id)
        )
        conn.commit()
        conn.close()

    def add_mod_response(self, ticket_id, mod_id, mod_username, message):
        conn = get_connection()
        conn.execute(
            "UPDATE tickets SET mod_response=?, assigned_mod_id=?, assigned_mod_username=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (message, mod_id, mod_username, ticket_id)
        )
        conn.execute(
            "INSERT INTO ticket_messages (ticket_id, sender_type, sender_id, sender_username, message) VALUES (?, 'mod', ?, ?, ?)",
            (ticket_id, mod_id, mod_username, message)
        )
        conn.commit()
        conn.close()

    def get_stats(self, project_id=None):
        conn = get_connection()
        if project_id:
            row = conn.execute("""
                SELECT
                    SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                    SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as in_progress_count,
                    SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) as resolved_count,
                    SUM(CASE WHEN status='unresolved' THEN 1 ELSE 0 END) as unresolved_count,
                    COUNT(*) as total
                FROM tickets WHERE project_id = ?
            """, (project_id,)).fetchone()
        else:
            row = conn.execute("""
                SELECT
                    SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                    SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as in_progress_count,
                    SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) as resolved_count,
                    SUM(CASE WHEN status='unresolved' THEN 1 ELSE 0 END) as unresolved_count,
                    COUNT(*) as total
                FROM tickets
            """).fetchone()
        conn.close()
        return dict(row) if row else {}

    def get_all_tickets_paginated(self, page=1, per_page=20, project_id=None, status=None):
        conn = get_connection()
        conditions = []
        params = []
        if project_id:
            conditions.append("t.project_id = ?")
            params.append(project_id)
        if status:
            conditions.append("t.status = ?")
            params.append(status)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        offset = (page - 1) * per_page
        rows = conn.execute(f"""
            SELECT t.*, p.name as project_name
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            {where}
            ORDER BY t.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset]).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM tickets t {where}", params).fetchone()[0]
        conn.close()
        return [dict(r) for r in rows], total