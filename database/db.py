import os
import asyncpg
import asyncio
import random
import string
from datetime import datetime

def generate_ticket_id():
    suffix = ''.join(random.choices(string.digits, k=4))
    return f"TKT-{suffix}"

def run_sync(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

async def get_connection():
    return await asyncpg.connect(os.environ["DATABASE_URL"])

class Database:
    def __init__(self):
        self.init_tables_sync()

    def init_tables_sync(self):
        run_sync(self.init_tables())

    async def init_tables(self):
        conn = await get_connection()
        await conn.execute("""
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
        await conn.close()

    def _run(self, coro):
        return run_sync(coro)

    # ── PROJECTS ──────────────────────────────────────────────────────────

    def get_all_projects(self):
        return self._run(self._get_all_projects())

    async def _get_all_projects(self):
        conn = await get_connection()
        rows = await conn.fetch("SELECT * FROM projects ORDER BY name")
        await conn.close()
        return [dict(r) for r in rows]

    def get_project(self, project_id):
        return self._run(self._get_project(project_id))

    async def _get_project(self, project_id):
        conn = await get_connection()
        row = await conn.fetchrow("SELECT * FROM projects WHERE id = $1", project_id)
        await conn.close()
        return dict(row) if row else None

    def get_project_by_group_id(self, group_chat_id):
        return self._run(self._get_project_by_group_id(group_chat_id))

    async def _get_project_by_group_id(self, group_chat_id):
        conn = await get_connection()
        row = await conn.fetchrow("SELECT * FROM projects WHERE group_chat_id = $1", group_chat_id)
        await conn.close()
        return dict(row) if row else None

    def create_project(self, name, group_chat_id=None, staff_chat_id=None):
        return self._run(self._create_project(name, group_chat_id, staff_chat_id))

    async def _create_project(self, name, group_chat_id, staff_chat_id):
        conn = await get_connection()
        row = await conn.fetchrow(
            "INSERT INTO projects (name, group_chat_id, staff_chat_id) VALUES ($1, $2, $3) RETURNING *",
            name, group_chat_id, staff_chat_id
        )
        await conn.close()
        return dict(row)

    def update_project(self, project_id, name=None, group_chat_id=None, staff_chat_id=None):
        return self._run(self._update_project(project_id, name, group_chat_id, staff_chat_id))

    async def _update_project(self, project_id, name, group_chat_id, staff_chat_id):
        conn = await get_connection()
        if name:
            await conn.execute("UPDATE projects SET name = $1 WHERE id = $2", name, project_id)
        if group_chat_id:
            await conn.execute("UPDATE projects SET group_chat_id = $1 WHERE id = $2", group_chat_id, project_id)
        if staff_chat_id:
            await conn.execute("UPDATE projects SET staff_chat_id = $1 WHERE id = $2", staff_chat_id, project_id)
        await conn.close()

    def delete_project(self, project_id):
        return self._run(self._delete_project(project_id))

    async def _delete_project(self, project_id):
        conn = await get_connection()
        await conn.execute("DELETE FROM projects WHERE id = $1", project_id)
        await conn.close()

    # ── TICKETS ───────────────────────────────────────────────────────────

    def create_ticket(self, project_id, user_telegram_id, username, category, description, severity):
        return self._run(self._create_ticket(project_id, user_telegram_id, username, category, description, severity))

    async def _create_ticket(self, project_id, user_telegram_id, username, category, description, severity):
        conn = await get_connection()
        ticket_id = generate_ticket_id()
        while True:
            row = await conn.fetchrow("SELECT id FROM tickets WHERE ticket_id = $1", ticket_id)
            if not row:
                break
            ticket_id = generate_ticket_id()
        row = await conn.fetchrow(
            """INSERT INTO tickets (ticket_id, project_id, user_telegram_id, username, category, description, severity)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
            ticket_id, project_id, user_telegram_id, username, category, description, severity
        )
        await conn.close()
        ticket = dict(row)
        project = await self._get_project(project_id)
        ticket["project_name"] = project["name"] if project else "Desconocido"
        return ticket

    def get_ticket_by_db_id(self, db_id):
        return self._run(self._get_ticket_by_db_id(db_id))

    async def _get_ticket_by_db_id(self, db_id):
        conn = await get_connection()
        row = await conn.fetchrow("""
            SELECT t.*, p.name as project_name, p.staff_chat_id
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.id = $1
        """, db_id)
        await conn.close()
        return dict(row) if row else None

    def get_ticket_by_ticket_id(self, ticket_id):
        return self._run(self._get_ticket_by_ticket_id(ticket_id))

    async def _get_ticket_by_ticket_id(self, ticket_id):
        conn = await get_connection()
        row = await conn.fetchrow("""
            SELECT t.*, p.name as project_name, p.staff_chat_id
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.ticket_id = $1
        """, ticket_id)
        await conn.close()
        return dict(row) if row else None

    def get_tickets_by_user(self, user_telegram_id):
        return self._run(self._get_tickets_by_user(user_telegram_id))

    async def _get_tickets_by_user(self, user_telegram_id):
        conn = await get_connection()
        rows = await conn.fetch("""
            SELECT t.*, p.name as project_name
            FROM tickets t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.user_telegram_id = $1 AND t.status NOT IN ('closed')
            ORDER BY t.created_at DESC LIMIT 10
        """, user_telegram_id)
        await conn.
