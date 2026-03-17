import sqlite3
import os
import random
import string
import hashlib

DB_PATH = os.environ.get("DB_PATH", "tickets.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def generate_ticket_id():
    suffix = "".join(random.choices(string.digits, k=4))
    return f"TKT-{suffix}"


def hash_password(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


class Database:
    def __init__(self):
        self.init_tables()

    def init_tables(self):
        conn = get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                group_chat_id TEXT,
                site_name TEXT,
                logo_url TEXT,
                primary_color TEXT DEFAULT '#c9a84c',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT UNIQUE NOT NULL,
                project_id INTEGER REFERENCES projects(id),
                user_telegram_id TEXT NOT NULL,
                username TEXT,
                description TEXT NOT NULL,
                wallet_address TEXT,
                blockchain TEXT,
                tx_hash TEXT,
                has_screenshot INTEGER DEFAULT 0,
                screenshot_file_id TEXT,
                staff_message_id INTEGER,
                severity TEXT,
                status TEXT DEFAULT 'open',
                assigned_mod_id TEXT,
                assigned_mod_username TEXT,
                mod_response TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER REFERENCES tickets(id),
                sender_type TEXT NOT NULL,
                sender_id TEXT,
                sender_username TEXT,
                message TEXT NOT NULL,
                is_internal INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS web_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'viewer',
                project_ids TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS project_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                web_user_id INTEGER NOT NULL REFERENCES web_users(id),
                role TEXT NOT NULL DEFAULT 'moderator',
                added_by TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, web_user_id)
            );
        """)
        conn.commit()
        # Migrations for columns added after initial deploy
        migrations = [
            "ALTER TABLE tickets ADD COLUMN screenshot_file_id TEXT",
            "ALTER TABLE tickets ADD COLUMN staff_message_id INTEGER",
            "ALTER TABLE ticket_messages ADD COLUMN is_internal INTEGER DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN site_name TEXT",
            "ALTER TABLE projects ADD COLUMN logo_url TEXT",
            "ALTER TABLE projects ADD COLUMN primary_color TEXT DEFAULT '#c9a84c'",
            # Legacy: keep staff_chat_id column for existing installs
            "ALTER TABLE projects ADD COLUMN staff_chat_id TEXT",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass
        conn.close()

    # ── PROJECTS ──────────────────────────────────────────────────────────────

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
        row = conn.execute(
            "SELECT * FROM projects WHERE group_chat_id = ?", (group_chat_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def create_project(self, name, group_chat_id=None, site_name=None,
                       logo_url=None, primary_color=None):
        conn = get_connection()
        cur = conn.execute(
            """INSERT INTO projects (name, group_chat_id, site_name, logo_url, primary_color)
               VALUES (?, ?, ?, ?, ?)""",
            (name, group_chat_id, site_name, logo_url, primary_color or "#c9a84c"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.close()
        return dict(row)

    def update_project(self, project_id, name=None, group_chat_id=None,
                       site_name=None, logo_url=None, primary_color=None):
        conn = get_connection()
        fields = []
        params = []
        if name is not None:
            fields.append("name = ?"); params.append(name)
        if group_chat_id is not None:
            fields.append("group_chat_id = ?"); params.append(group_chat_id)
        if site_name is not None:
            fields.append("site_name = ?"); params.append(site_name)
        if logo_url is not None:
            fields.append("logo_url = ?"); params.append(logo_url)
        if primary_color is not None:
            fields.append("primary_color = ?"); params.append(primary_color)
        if fields:
            params.append(project_id)
            conn.execute(
                f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", params
            )
            conn.commit()
        conn.close()

    def delete_project(self, project_id):
        conn = get_connection()
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        conn.close()

    # ── TICKETS ───────────────────────────────────────────────────────────────

    def create_ticket(
        self,
        project_id,
        user_telegram_id,
        username,
        description,
        wallet_address=None,
        blockchain=None,
        tx_hash=None,
        has_screenshot=False,
        screenshot_file_id=None,
    ):
        conn = get_connection()
        ticket_id = generate_ticket_id()
        while conn.execute(
            "SELECT id FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone():
            ticket_id = generate_ticket_id()
        cur = conn.execute(
            """INSERT INTO tickets
               (ticket_id, project_id, user_telegram_id, username,
                description, wallet_address, blockchain, tx_hash,
                has_screenshot, screenshot_file_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticket_id, project_id, user_telegram_id, username,
                description, wallet_address, blockchain, tx_hash,
                1 if has_screenshot else 0, screenshot_file_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.close()
        ticket = dict(row)
        project = self.get_project(project_id)
        ticket["project_name"] = project["name"] if project else "Unknown"
        return ticket

    def save_staff_message_id(self, ticket_db_id, message_id):
        conn = get_connection()
        conn.execute(
            "UPDATE tickets SET staff_message_id = ? WHERE id = ?",
            (message_id, ticket_db_id),
        )
        conn.commit()
        conn.close()

    def get_ticket_by_db_id(self, db_id):
        conn = get_connection()
        row = conn.execute(
            """SELECT t.*, p.name as project_name, p.primary_color as project_color,
                      p.site_name as project_site_name, p.logo_url as project_logo_url
               FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.id = ?""",
            (db_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_ticket_by_ticket_id(self, ticket_id):
        conn = get_connection()
        row = conn.execute(
            """SELECT t.*, p.name as project_name, p.primary_color as project_color
               FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.ticket_id = ?""",
            (ticket_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_tickets_by_user(self, user_telegram_id):
        conn = get_connection()
        rows = conn.execute(
            """SELECT t.*, p.name as project_name
               FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.user_telegram_id = ? AND t.status NOT IN ('closed')
               ORDER BY t.created_at DESC LIMIT 10""",
            (user_telegram_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_active_ticket_for_user(self, user_telegram_id):
        conn = get_connection()
        row = conn.execute(
            """SELECT t.*, p.name as project_name
               FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.user_telegram_id = ? AND t.status IN ('open','in_progress')
               ORDER BY t.created_at DESC LIMIT 1""",
            (user_telegram_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_open_tickets(self, project_id=None):
        conn = get_connection()
        if project_id:
            rows = conn.execute(
                """SELECT t.*, p.name as project_name
                   FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
                   WHERE t.status IN ('open','in_progress') AND t.project_id = ?
                   ORDER BY t.created_at ASC""",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.*, p.name as project_name
                   FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
                   WHERE t.status IN ('open','in_progress')
                   ORDER BY t.created_at ASC"""
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_tickets_by_mod(self, mod_telegram_id):
        conn = get_connection()
        rows = conn.execute(
            """SELECT t.*, p.name as project_name
               FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.assigned_mod_id = ? AND t.status = 'in_progress'
               ORDER BY t.created_at ASC""",
            (mod_telegram_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def assign_ticket(self, ticket_db_id, mod_id, mod_username):
        conn = get_connection()
        conn.execute(
            """UPDATE tickets
               SET assigned_mod_id=?, assigned_mod_username=?, updated_at=datetime('now')
               WHERE id=?""",
            (mod_id, mod_username, ticket_db_id),
        )
        conn.commit()
        conn.close()

    def unassign_ticket(self, ticket_db_id):
        conn = get_connection()
        conn.execute(
            """UPDATE tickets
               SET assigned_mod_id=NULL, assigned_mod_username=NULL,
                   status='open', updated_at=datetime('now')
               WHERE id=?""",
            (ticket_db_id,),
        )
        conn.commit()
        conn.close()

    def update_ticket_status(self, ticket_db_id, status):
        conn = get_connection()
        conn.execute(
            "UPDATE tickets SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, ticket_db_id),
        )
        conn.commit()
        conn.close()

    def update_ticket_severity(self, ticket_db_id, severity):
        conn = get_connection()
        conn.execute(
            "UPDATE tickets SET severity=?, updated_at=datetime('now') WHERE id=?",
            (severity, ticket_db_id),
        )
        conn.commit()
        conn.close()

    def add_mod_response(self, ticket_db_id, mod_id, mod_username, message):
        conn = get_connection()
        conn.execute(
            """UPDATE tickets
               SET mod_response=?, assigned_mod_id=?, assigned_mod_username=?,
                   updated_at=datetime('now')
               WHERE id=?""",
            (message, mod_id, mod_username, ticket_db_id),
        )
        conn.execute(
            """INSERT INTO ticket_messages
               (ticket_id, sender_type, sender_id, sender_username, message, is_internal)
               VALUES (?, 'mod', ?, ?, ?, 0)""",
            (ticket_db_id, mod_id, mod_username, message),
        )
        conn.commit()
        conn.close()

    def add_message(self, ticket_db_id, sender_type, sender_id, sender_username,
                    message, is_internal=False):
        conn = get_connection()
        conn.execute(
            """INSERT INTO ticket_messages
               (ticket_id, sender_type, sender_id, sender_username, message, is_internal)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticket_db_id, sender_type, sender_id, sender_username, message,
             1 if is_internal else 0),
        )
        conn.execute(
            "UPDATE tickets SET updated_at=datetime('now') WHERE id=?",
            (ticket_db_id,),
        )
        conn.commit()
        conn.close()

    def get_ticket_messages(self, ticket_db_id, include_internal=False):
        conn = get_connection()
        if include_internal:
            rows = conn.execute(
                "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY created_at ASC",
                (ticket_db_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM ticket_messages
                   WHERE ticket_id = ? AND is_internal = 0
                   ORDER BY created_at ASC""",
                (ticket_db_id,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_internal_notes(self, ticket_db_id):
        conn = get_connection()
        rows = conn.execute(
            """SELECT * FROM ticket_messages
               WHERE ticket_id = ? AND is_internal = 1
               ORDER BY created_at ASC""",
            (ticket_db_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self, project_id=None):
        conn = get_connection()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        row = conn.execute(
            f"""SELECT
                SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as in_progress_count,
                SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) as resolved_count,
                SUM(CASE WHEN status='unresolved' THEN 1 ELSE 0 END) as unresolved_count,
                COUNT(*) as total
            FROM tickets {where}""",
            params,
        ).fetchone()
        conn.close()
        return dict(row) if row else {}

    def get_all_tickets_paginated(self, page=1, per_page=20, project_id=None,
                                  status=None, allowed_project_ids=None):
        conn = get_connection()
        conditions, params = [], []
        if project_id:
            conditions.append("t.project_id = ?")
            params.append(project_id)
        if status:
            conditions.append("t.status = ?")
            params.append(status)
        if allowed_project_ids:
            placeholders = ",".join("?" * len(allowed_project_ids))
            conditions.append(f"t.project_id IN ({placeholders})")
            params.extend(allowed_project_ids)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""SELECT t.*, p.name as project_name
                FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
                {where}
                ORDER BY t.created_at DESC
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM tickets t {where}", params
        ).fetchone()[0]
        conn.close()
        return [dict(r) for r in rows], total

    # ── WEB USERS ─────────────────────────────────────────────────────────────

    def get_all_web_users(self):
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, username, role, project_ids, created_at FROM web_users ORDER BY username"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_web_user(self, username):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM web_users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def create_web_user(self, username, password, role="viewer", project_ids=""):
        conn = get_connection()
        conn.execute(
            "INSERT INTO web_users (username, password_hash, role, project_ids) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), role, project_ids),
        )
        conn.commit()
        conn.close()

    def update_web_user_password(self, user_id, new_password):
        conn = get_connection()
        conn.execute(
            "UPDATE web_users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )
        conn.commit()
        conn.close()

    def delete_web_user(self, user_id):
        conn = get_connection()
        conn.execute("DELETE FROM web_users WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM project_members WHERE web_user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    # ── PROJECT MEMBERS ───────────────────────────────────────────────────────

    def get_project_members(self, project_id):
        conn = get_connection()
        rows = conn.execute(
            """SELECT pm.*, wu.username
               FROM project_members pm
               JOIN web_users wu ON pm.web_user_id = wu.id
               WHERE pm.project_id = ?
               ORDER BY pm.created_at ASC""",
            (project_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_project_member(self, project_id, web_user_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM project_members WHERE project_id = ? AND web_user_id = ?",
            (project_id, web_user_id),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def add_project_member(self, project_id, web_user_id, role, added_by=""):
        conn = get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO project_members
               (project_id, web_user_id, role, added_by)
               VALUES (?, ?, ?, ?)""",
            (project_id, web_user_id, role, added_by),
        )
        conn.commit()
        conn.close()

    def update_project_member_role(self, member_id, role):
        conn = get_connection()
        conn.execute(
            "UPDATE project_members SET role = ? WHERE id = ?",
            (role, member_id),
        )
        conn.commit()
        conn.close()

    def remove_project_member(self, member_id):
        conn = get_connection()
        conn.execute("DELETE FROM project_members WHERE id = ?", (member_id,))
        conn.commit()
        conn.close()

    def get_user_projects_from_members(self, web_user_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT project_id FROM project_members WHERE web_user_id = ?",
            (web_user_id,),
        ).fetchall()
        conn.close()
        return [r["project_id"] for r in rows]
