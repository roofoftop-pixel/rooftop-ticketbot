import os
import psycopg2
import psycopg2.extras
from datetime import datetime
import random
import string

def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)

def generate_ticket_id():
    suffix = ''.join(random.choices(string.digits, k=4))
    return f"TKT-{suffix}"

class Database:
    def __init__(self):
        self.init_tables()

    def init_tables(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                group_chat_id TEXT,
                staff_chat_id TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id SERIAL PRIMARY KEY,
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
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id SERIAL PRIMARY KEY,
                ticket_id INTEGER REFERENCES tickets(id),
                sender_type TEXT NOT NULL,
                sender_id TEXT,
                sender_username TEXT,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        conn.commit()
        cur.close()
        conn.close()

    # ── PROJECTS ──────────────────────────────────────────────────────────

    def get_all_projects(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM projects ORDER BY name")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]

    def get_project(self, project_id: int):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None

    def get_project_by_group_id(self, group_chat_id: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM projects WHERE group_chat_id = %s", (group_chat_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None

    def create_project(self, name: str, group_chat_id: str = None, staff_chat_id: str = None):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO projects (name, group_chat_id, staff_chat_id) VALUES (%s, %s, %s) RETURNING *",
            (name, group_chat_id, staff_chat_id)
        )
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        return dict(row)

    def update_project(self, project_id: int, name: str = None, group_chat_id: str = None, staff_chat_id: str = None):
        conn = get_connection()
        cur = conn.cursor()
        if name:
            cur.execute("UPDATE projects SET name = %s WHERE id = %s", (name, project_id))
        if group_chat_id:
            cur.execute("UPDATE projects SET group_chat_id = %s WHERE id = %s", (group_chat_id, project_id))
        if staff_chat_id:
            cur.execute("UPDATE projects SET staff_chat_id = %s WHERE id = %s", (staff_chat_id, project_id))
        conn.commit(); cur.close(); conn.close()

    def delete_project(self, project_id: int):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        conn.commit(); cur.close(); conn.close()

    # ── TICKETS ───────────────────────────────────────────────────────────

    def create_ticket(self, project_id, user_telegram_id, username, category, description, severity):
        conn = get_connection()
        cur = conn.cursor()
        ticket_id = generate_ticket_id()
        # Ensure uniqueness
        while True:
            cur.execute("SELECT id FROM tickets WHERE ticket_id = %s", (ticket_id,))
            if not cur.fetchone():
                break
            ticket_id = generate_ticket_id()

        cur.execute(
            """INSERT INTO tickets
               (ticket_id, project_id, user_telegram_id, username, category, description, severity)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (ticket_id, project_id, user_telegram_id, username, category, description, severity)
        )
        ticket = dict(cur.fetchone())
        conn.commit(); cur.close(); conn.close()
        # Attach project name
        project = self.get_project(project_id)
        ticket["project_name"] = project["name"] if project else "Desconocido"
        return ticket

    def get_ticket_by_db_id(self, db_id: int):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.*, p.name as project_name, p.staff_chat_id
            FROM tickets t
            LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.id = %s
        """, (db_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None

    def get_ticket_by_ticket_id(self, ticket_id: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.*, p.name as project_name, p.staff_chat_id
            FROM tickets t
            LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.ticket_id = %s
        """, (ticket_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None

    def get_tickets_by_user(self, user_telegram_id: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.*, p.name as project_name
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.user_telegram_id = %s
            AND t.status NOT IN ('closed')
            ORDER BY t.created_at DESC LIMIT 10
        """, (user_telegram_id,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]

    def get_open_tickets(self, project_id: int = None):
        conn = get_connection()
        cur = conn.cursor()
        if project_id:
            cur.execute("""
                SELECT t.*, p.name as project_name
                FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
                WHERE t.status IN ('open', 'in_progress') AND t.project_id = %s
                ORDER BY t.severity DESC, t.created_at ASC
            """, (project_id,))
        else:
            cur.execute("""
                SELECT t.*, p.name as project_name
                FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
                WHERE t.status IN ('open', 'in_progress')
                ORDER BY t.severity DESC, t.created_at ASC
            """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]

    def get_tickets_by_mod(self, mod_telegram_id: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.*, p.name as project_name
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.assigned_mod_id = %s AND t.status = 'in_progress'
            ORDER BY t.created_at ASC
        """, (mod_telegram_id,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]

    def assign_ticket(self, ticket_id: int, mod_id: str, mod_username: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE tickets SET assigned_mod_id=%s, assigned_mod_username=%s, updated_at=NOW() WHERE id=%s",
            (mod_id, mod_username, ticket_id)
        )
        conn.commit(); cur.close(); conn.close()

    def update_ticket_status(self, ticket_id: int, status: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE tickets SET status=%s, updated_at=NOW() WHERE id=%s",
            (status, ticket_id)
        )
        conn.commit(); cur.close(); conn.close()

    def update_ticket_severity(self, ticket_id: int, severity: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE tickets SET severity=%s, updated_at=NOW() WHERE id=%s",
            (severity, ticket_id)
        )
        conn.commit(); cur.close(); conn.close()

    def add_mod_response(self, ticket_id: int, mod_id: str, mod_username: str, message: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE tickets SET mod_response=%s, assigned_mod_id=%s, assigned_mod_username=%s, updated_at=NOW() WHERE id=%s",
            (message, mod_id, mod_username, ticket_id)
        )
        cur.execute(
            "INSERT INTO ticket_messages (ticket_id, sender_type, sender_id, sender_username, message) VALUES (%s, 'mod', %s, %s, %s)",
            (ticket_id, mod_id, mod_username, message)
        )
        conn.commit(); cur.close(); conn.close()

    # ── STATS ─────────────────────────────────────────────────────────────

    def get_stats(self, project_id: int = None):
        conn = get_connection()
        cur = conn.cursor()
        base = "WHERE t.project_id = %s" if project_id else ""
        params = (project_id,) if project_id else ()
        cur.execute(f"""
            SELECT
                COUNT(*) FILTER (WHERE status = 'open') as open_count,
                COUNT(*) FILTER (WHERE status = 'in_progress') as in_progress_count,
                COUNT(*) FILTER (WHERE status = 'resolved') as resolved_count,
                COUNT(*) FILTER (WHERE status = 'unresolved') as unresolved_count,
                COUNT(*) as total
            FROM tickets t {base}
        """, params)
        stats = dict(cur.fetchone())
        cur.close(); conn.close()
        return stats

    def get_all_tickets_paginated(self, page: int = 1, per_page: int = 20, project_id: int = None, status: str = None):
        conn = get_connection()
        cur = conn.cursor()
        conditions = []
        params = []
        if project_id:
            conditions.append("t.project_id = %s")
            params.append(project_id)
        if status:
            conditions.append("t.status = %s")
            params.append(status)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        offset = (page - 1) * per_page
        params.extend([per_page, offset])
        cur.execute(f"""
            SELECT t.*, p.name as project_name
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            {where}
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
        """, params)
        rows = cur.fetchall()
        # Total count
        params2 = params[:-2]
        cur.execute(f"SELECT COUNT(*) FROM tickets t {where}", params2)
        total = cur.fetchone()["count"]
        cur.close(); conn.close()
        return [dict(r) for r in rows], total
