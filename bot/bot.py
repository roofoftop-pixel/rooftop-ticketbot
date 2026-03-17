import asyncio
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database.db import Database

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database()

# ── Conversation states ────────────────────────────────────────────────────────
DESCRIPTION, WALLET, BLOCKCHAIN, TX_HASH, SCREENSHOT = range(5)
ADD_NAME, ADD_GROUP, ADD_STAFF = range(10, 13)

# ── Helpers ────────────────────────────────────────────────────────────────────

def sev_emoji(s):
    return {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(s or "", "⚪")


def status_emoji(s):
    return {
        "open": "📬", "in_progress": "⚙️", "resolved": "✅",
        "closed": "🔒", "unresolved": "❌",
    }.get(s or "", "❓")


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def staff_notification_text(ticket):
    """Texto compacto para el grupo staff — solo lo esencial."""
    created = (ticket.get("created_at") or "")[:16]
    desc = ticket["description"][:200]
    wallet = ticket.get("wallet_address") or "—"
    chain = ticket.get("blockchain") or "—"
    txhash = ticket.get("tx_hash") or "—"
    screenshot = "✅" if ticket.get("has_screenshot") else "❌"
    return (
        f"🎫 *Nuevo ticket #{ticket['ticket_id']}*\n"
        f"📁 {ticket.get('project_name','?')} · "
        f"👤 @{ticket.get('username') or ticket['user_telegram_id']}\n\n"
        f"_{desc}_\n\n"
        f"👛 `{wallet}` · ⛓️ {chain}\n"
        f"🔗 `{txhash}` · 🖼️ {screenshot}\n"
        f"🕐 {created}"
    )


def staff_keyboard_simple(ticket_db_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✋ Take", callback_data=f"take_{ticket_db_id}"),
        InlineKeyboardButton("🔄 Reassign", callback_data=f"reassign_{ticket_db_id}"),
    ]])


async def notify_staff(bot, ticket, project):
    staff_chat_id = project.get("staff_chat_id")
    if not staff_chat_id:
        return
    try:
        msg = await bot.send_message(
            chat_id=int(staff_chat_id),
            text=staff_notification_text(ticket),
            parse_mode="Markdown",
            reply_markup=staff_keyboard_simple(ticket["id"]),
        )
        db.save_staff_message_id(ticket["id"], msg.message_id)
    except Exception as e:
        logger.error(f"Error notifying staff: {e}")


async def edit_staff_message(bot, ticket, status_label):
    """Edita el mensaje del staff group al cerrar/resolver un ticket."""
    msg_id = ticket.get("staff_message_id")
    chat_id = ticket.get("staff_chat_id")
    if not msg_id or not chat_id:
        return
    text = (
        f"🎫 *Ticket #{ticket['ticket_id']}* — {status_label}\n"
        f"📁 {ticket.get('project_name','?')} · "
        f"👤 @{ticket.get('username') or ticket['user_telegram_id']}\n\n"
        f"_{ticket['description'][:150]}_\n\n"
        f"🕐 Actualizado: {_now_str()}"
    )
    try:
        await bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(msg_id),
            text=text,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Error editing staff message: {e}")


# ── /start — deep link ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if "ticket_" in text:
        try:
            project_id = int(text.split("ticket_")[1])
            project = db.get_project(project_id)
            if project:
                context.user_data["project_id"] = project_id
                context.user_data["project_name"] = project["name"]
                await update.message.reply_text(
                    f"🎫 *Nuevo ticket — {project['name']}*\n\n"
                    "📝 Describí tu problema en detalle:",
                    parse_mode="Markdown",
                )
                return DESCRIPTION
        except Exception:
            pass
    await update.message.reply_text(
        "👋 *Ticket Support Bot*\n\n"
        "Para abrir un ticket, usá /ticket en el grupo del proyecto.\n\n"
        "Comandos mod:\n"
        "• /tickets — ver tickets abiertos\n"
        "• /assigned — mis tickets asignados\n"
        "• /reply TKT-XXXX mensaje — responder al usuario",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /ticket ────────────────────────────────────────────────────────────────────

async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        project = db.get_project_by_group_id(str(chat.id))
        if project:
            bot_info = await context.bot.get_me()
            keyboard = [[InlineKeyboardButton(
                "📬 Abrir ticket",
                url=f"https://t.me/{bot_info.username}?start=ticket_{project['id']}",
            )]]
            await update.message.reply_text(
                f"Para abrir un ticket de *{project['name']}*, hacelo en privado 👇",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("⚠️ Este grupo no tiene proyecto configurado.")
        return ConversationHandler.END

    projects = db.get_all_projects()
    if not projects:
        await update.message.reply_text("⚠️ No hay proyectos configurados.")
        return ConversationHandler.END
    if len(projects) == 1:
        context.user_data["project_id"] = projects[0]["id"]
        context.user_data["project_name"] = projects[0]["name"]
        await update.message.reply_text(
            f"🎫 *Nuevo ticket — {projects[0]['name']}*\n\n"
            "📝 Describí tu problema en detalle:",
            parse_mode="Markdown",
        )
        return DESCRIPTION
    keyboard = [[InlineKeyboardButton(p["name"], callback_data=f"proj_{p['id']}")] for p in projects]
    await update.message.reply_text(
        "📋 *¿Para qué proyecto es el ticket?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return DESCRIPTION


async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split("_")[1])
    project = db.get_project(project_id)
    context.user_data["project_id"] = project_id
    context.user_data["project_name"] = project["name"]
    await query.edit_message_text(
        f"🎫 *Nuevo ticket — {project['name']}*\n\n"
        "📝 Describí tu problema en detalle:",
        parse_mode="Markdown",
    )
    return DESCRIPTION


# ── Pasos del formulario ────────────────────────────────────────────────────────

async def got_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = update.message.text
    keyboard = [[InlineKeyboardButton("⏭️ Saltar", callback_data="skip_wallet")]]
    await update.message.reply_text(
        "👛 *Dirección de wallet?*\n_(o saltá si no aplica)_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WALLET


async def got_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wallet_address"] = update.message.text
    return await _ask_blockchain_msg(update)


async def skip_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["wallet_address"] = None
    return await _ask_blockchain_query(query)


def _blockchain_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ETH", callback_data="chain_ETH"),
            InlineKeyboardButton("BSC", callback_data="chain_BSC"),
            InlineKeyboardButton("SOL", callback_data="chain_SOL"),
        ],
        [
            InlineKeyboardButton("MATIC", callback_data="chain_MATIC"),
            InlineKeyboardButton("ARB", callback_data="chain_ARB"),
            InlineKeyboardButton("OTHER", callback_data="chain_OTHER"),
        ],
        [InlineKeyboardButton("⏭️ Saltar", callback_data="chain_skip")],
    ])


async def _ask_blockchain_msg(update):
    await update.message.reply_text(
        "⛓️ *Blockchain / red?*",
        reply_markup=_blockchain_keyboard(),
        parse_mode="Markdown",
    )
    return BLOCKCHAIN


async def _ask_blockchain_query(query):
    await query.edit_message_text(
        "⛓️ *Blockchain / red?*",
        reply_markup=_blockchain_keyboard(),
        parse_mode="Markdown",
    )
    return BLOCKCHAIN


async def got_blockchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chain = query.data.replace("chain_", "")
    context.user_data["blockchain"] = None if chain == "skip" else chain
    keyboard = [[InlineKeyboardButton("⏭️ Saltar", callback_data="skip_txhash")]]
    await query.edit_message_text(
        "🔗 *Hash de transacción?*\n_(o saltá)_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return TX_HASH


async def got_tx_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tx_hash"] = update.message.text
    return await _ask_screenshot_msg(update)


async def skip_tx_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["tx_hash"] = None
    keyboard = [[InlineKeyboardButton("⏭️ Saltar", callback_data="skip_screenshot")]]
    await query.edit_message_text(
        "🖼️ *Enviá un screenshot* o saltá:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SCREENSHOT


async def _ask_screenshot_msg(update):
    keyboard = [[InlineKeyboardButton("⏭️ Saltar", callback_data="skip_screenshot")]]
    await update.message.reply_text(
        "🖼️ *Enviá un screenshot* o saltá:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SCREENSHOT


async def got_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # mejor calidad
    context.user_data["has_screenshot"] = True
    context.user_data["screenshot_file_id"] = photo.file_id
    return await _do_create_ticket(update=update, context=context)


async def skip_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["has_screenshot"] = False
    context.user_data["screenshot_file_id"] = None
    return await _do_create_ticket(query=query, context=context)


async def _do_create_ticket(context, update=None, query=None):
    user = (update or query).from_user
    ud = context.user_data
    ticket = db.create_ticket(
        project_id=ud["project_id"],
        user_telegram_id=str(user.id),
        username=user.username or user.first_name,
        description=ud["description"],
        wallet_address=ud.get("wallet_address"),
        blockchain=ud.get("blockchain"),
        tx_hash=ud.get("tx_hash"),
        has_screenshot=ud.get("has_screenshot", False),
        screenshot_file_id=ud.get("screenshot_file_id"),
    )
    project = db.get_project(ud["project_id"])
    confirmation = (
        f"✅ *Ticket #{ticket['ticket_id']} creado*\n\n"
        f"📁 Proyecto: {project['name']}\n"
        "Un moderador te contactará pronto.\n\n"
        "_Podés seguir escribiendo acá para agregar más información._"
    )
    if update:
        await update.message.reply_text(confirmation, parse_mode="Markdown")
    else:
        await query.edit_message_text(confirmation, parse_mode="Markdown")
    await notify_staff(context.bot, ticket, project)
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelado.")
    return ConversationHandler.END


# ── Mensajes libres del usuario en DM ─────────────────────────────────────────

async def handle_user_free_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda el mensaje en la BD — llega solo a la web, no al grupo staff."""
    if update.effective_chat.type != "private":
        return
    user = update.effective_user
    ticket = db.get_active_ticket_for_user(str(user.id))
    if not ticket:
        await update.message.reply_text(
            "No tenés un ticket activo. Usá /ticket para abrir uno."
        )
        return
    text = update.message.text or "[media]"
    db.add_message(ticket["id"], "user", str(user.id), user.username or user.first_name, text)
    await update.message.reply_text(
        f"✅ Mensaje guardado en tu ticket `#{ticket['ticket_id']}`.\n"
        "_El moderador lo verá en el panel._",
        parse_mode="Markdown",
    )


# ── /mytickets ─────────────────────────────────────────────────────────────────

async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tickets = db.get_tickets_by_user(str(user.id))
    if not tickets:
        await update.message.reply_text("📭 No tenés tickets activos.")
        return
    lines = [f"📋 *Tus tickets ({len(tickets)}):*\n"]
    for t in tickets[:10]:
        lines.append(
            f"{status_emoji(t['status'])} `#{t['ticket_id']}` {sev_emoji(t.get('severity'))}\n"
            f"   _{t['description'][:60]}{'...' if len(t['description'])>60 else ''}_\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Mod commands ───────────────────────────────────────────────────────────────

async def list_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    is_staff = chat.type == "private"
    if not is_staff and chat.type in ("group", "supergroup"):
        for p in db.get_all_projects():
            if p.get("staff_chat_id") and str(chat.id) == str(p["staff_chat_id"]):
                is_staff = True
                break
    if is_staff:
        tickets = db.get_open_tickets()
        if not tickets:
            await update.message.reply_text("📭 No hay tickets abiertos.")
            return
        lines = [f"📋 *Tickets abiertos ({len(tickets)}):*\n"]
        for t in tickets[:15]:
            lines.append(
                f"• `#{t['ticket_id']}` {sev_emoji(t.get('severity'))} "
                f"[{t.get('project_name','?')}] — {status_emoji(t['status'])}\n"
            )
    else:
        tickets = db.get_tickets_by_user(str(user.id))
        if not tickets:
            await update.message.reply_text("📭 No tenés tickets activos. Usá /ticket para abrir uno.")
            return
        lines = [f"📋 *Tus tickets ({len(tickets)}):*\n"]
        for t in tickets[:10]:
            lines.append(
                f"{status_emoji(t['status'])} `#{t['ticket_id']}` {sev_emoji(t.get('severity'))}\n"
                f"   _{t['description'][:60]}{'...' if len(t['description'])>60 else ''}_\n"
            )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def my_assigned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mod = update.effective_user
    tickets = db.get_tickets_by_mod(str(mod.id))
    if not tickets:
        await update.message.reply_text("📭 No tenés tickets asignados.")
        return
    lines = [f"🛡️ *Tus asignados ({len(tickets)}):*\n"]
    for t in tickets:
        lines.append(
            f"{status_emoji(t['status'])} `#{t['ticket_id']}` "
            f"{sev_emoji(t.get('severity'))} [{t.get('project_name','?')}]\n"
            f"   _{t['description'][:60]}_\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Uso: `/reply TKT-XXXX mensaje aquí`", parse_mode="Markdown"
        )
        return
    ticket_id = context.args[0].upper().replace("#", "")
    message = " ".join(context.args[1:])
    ticket = db.get_ticket_by_ticket_id(ticket_id)
    if not ticket:
        await update.message.reply_text(f"⚠️ Ticket {ticket_id} no encontrado.")
        return
    mod = update.effective_user
    db.add_mod_response(ticket["id"], str(mod.id), mod.username or mod.first_name, message)
    try:
        await context.bot.send_message(
            chat_id=int(ticket["user_telegram_id"]),
            text=f"💬 *Respuesta del moderador — Ticket #{ticket_id}:*\n\n{message}",
            parse_mode="Markdown",
        )
        await update.message.reply_text(f"✅ Respuesta enviada al ticket #{ticket_id}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ No se pudo enviar al usuario: {e}")


# ── Mod callback actions ───────────────────────────────────────────────────────

async def handle_mod_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.rsplit("_", 1)
    action, ticket_db_id = parts[0], int(parts[1])
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    mod = query.from_user

    if not ticket:
        await query.answer("⚠️ Ticket no encontrado.", show_alert=True)
        return

    if action == "take":
        db.assign_ticket(ticket_db_id, str(mod.id), mod.username or mod.first_name)
        db.update_ticket_status(ticket_db_id, "in_progress")
        await query.edit_message_text(
            f"🎫 *Ticket #{ticket['ticket_id']}* — ⚙️ EN PROGRESO\n"
            f"📁 {ticket.get('project_name','?')} · "
            f"👤 @{ticket.get('username') or ticket['user_telegram_id']}\n\n"
            f"_{ticket['description'][:150]}_\n\n"
            f"✋ Tomado por @{mod.username or mod.first_name} a las {_now_str()}\n"
            f"_Respondé desde el panel web o con /reply {ticket['ticket_id']} mensaje_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Reassign", callback_data=f"reassign_{ticket_db_id}"),
            ]]),
        )
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"⚙️ *Tu ticket #{ticket['ticket_id']} fue tomado por un moderador.*\nPronto recibirás respuesta.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "reassign":
        db.unassign_ticket(ticket_db_id)
        updated = db.get_ticket_by_db_id(ticket_db_id)
        await query.edit_message_text(
            staff_notification_text(updated),
            parse_mode="Markdown",
            reply_markup=staff_keyboard_simple(ticket_db_id),
        )

    elif action == "reply":
        await query.answer(
            f"Respondé con: /reply {ticket['ticket_id']} tu mensaje",
            show_alert=True,
        )

    elif action == "resolve":
        db.update_ticket_status(ticket_db_id, "resolved")
        updated = db.get_ticket_by_db_id(ticket_db_id)
        await edit_staff_message(context.bot, updated, "✅ RESUELTO")
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"✅ *Tu ticket #{ticket['ticket_id']} fue resuelto.* Si el problema persiste, abrí uno nuevo con /ticket.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "unresolved":
        db.update_ticket_status(ticket_db_id, "unresolved")
        updated = db.get_ticket_by_db_id(ticket_db_id)
        await edit_staff_message(context.bot, updated, "❌ SIN SOLUCIÓN")
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"❌ *Tu ticket #{ticket['ticket_id']} fue cerrado sin solución.* El equipo está trabajando en ello.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "severity":
        keyboard = [
            [
                InlineKeyboardButton("🟢 Baja", callback_data=f"setsev_low_{ticket_db_id}"),
                InlineKeyboardButton("🟡 Media", callback_data=f"setsev_medium_{ticket_db_id}"),
            ],
            [
                InlineKeyboardButton("🟠 Alta", callback_data=f"setsev_high_{ticket_db_id}"),
                InlineKeyboardButton("🔴 Crítica", callback_data=f"setsev_critical_{ticket_db_id}"),
            ],
        ]
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))


async def handle_set_severity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, severity, ticket_db_id_str = query.data.split("_", 2)
    ticket_db_id = int(ticket_db_id_str)
    db.update_ticket_severity(ticket_db_id, severity)
    updated = db.get_ticket_by_db_id(ticket_db_id)
    await query.edit_message_text(
        staff_notification_text(updated),
        parse_mode="Markdown",
        reply_markup=staff_keyboard_simple(ticket_db_id),
    )


# ── /addproject ────────────────────────────────────────────────────────────────

async def addproject_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *Nuevo proyecto*\n\nPaso 1: ¿Cuál es el *nombre del proyecto*?",
        parse_mode="Markdown",
    )
    return ADD_NAME


async def addproject_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_proj_name"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Nombre: *{context.user_data['new_proj_name']}*\n\n"
        "Paso 2: Agregame al *grupo público* y enviá cualquier mensaje ahí.\n"
        "Voy a detectar el Group Chat ID automáticamente.\n\n"
        "_O enviame el ID directamente (ej: -1001234567890)_",
        parse_mode="Markdown",
    )
    return ADD_GROUP


async def addproject_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        context.user_data["new_proj_group_id"] = str(chat.id)
        await update.message.reply_text(
            f"✅ Grupo público detectado: `{chat.id}` ({chat.title})\n\n"
            "Paso 3: Ahora agregame al *grupo de staff* y enviá cualquier mensaje ahí.\n\n"
            "_O enviame el ID directamente_",
            parse_mode="Markdown",
        )
        return ADD_STAFF
    text = update.message.text.strip()
    context.user_data["new_proj_group_id"] = text
    await update.message.reply_text(
        f"✅ Group ID: `{text}`\n\n"
        "Paso 3: Enviame el *Staff Chat ID*\n_(o agregame al grupo staff y enviá un mensaje)_",
        parse_mode="Markdown",
    )
    return ADD_STAFF


async def addproject_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    staff_id = str(chat.id) if chat.type in ("group", "supergroup") else update.message.text.strip()
    project = db.create_project(
        name=context.user_data["new_proj_name"],
        group_chat_id=context.user_data.get("new_proj_group_id"),
        staff_chat_id=staff_id,
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=(
            f"✅ *Proyecto creado*\n\n"
            f"Nombre: *{project['name']}*\n"
            f"Grupo: `{project.get('group_chat_id','—')}`\n"
            f"Staff: `{project.get('staff_chat_id','—')}`\n\n"
            "Usá /ticket en el grupo público para probar."
        ),
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = db.get_all_projects()
    if not projects:
        await update.message.reply_text("📭 No hay proyectos. Usá /addproject.")
        return
    lines = [f"📋 *Proyectos ({len(projects)}):*\n"]
    for p in projects:
        lines.append(
            f"• *{p['name']}* (#{p['id']})\n"
            f"  Grupo: `{p.get('group_chat_id','—')}`\n"
            f"  Staff: `{p.get('staff_chat_id','—')}`\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── main ───────────────────────────────────────────────────────────────────────

async def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    ticket_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("ticket", ticket_command),
        ],
        states={
            DESCRIPTION: [
                CallbackQueryHandler(project_selected, pattern=r"^proj_\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_description),
            ],
            WALLET: [
                CallbackQueryHandler(skip_wallet, pattern=r"^skip_wallet$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_wallet),
            ],
            BLOCKCHAIN: [
                CallbackQueryHandler(got_blockchain, pattern=r"^chain_"),
            ],
            TX_HASH: [
                CallbackQueryHandler(skip_tx_hash, pattern=r"^skip_txhash$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_tx_hash),
            ],
            SCREENSHOT: [
                CallbackQueryHandler(skip_screenshot, pattern=r"^skip_screenshot$"),
                MessageHandler(filters.PHOTO, got_screenshot),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    addproject_conv = ConversationHandler(
        entry_points=[CommandHandler("addproject", addproject_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addproject_name)],
            ADD_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, addproject_group)],
            ADD_STAFF: [MessageHandler(filters.TEXT & ~filters.COMMAND, addproject_staff)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
    )

    app.add_handler(ticket_conv)
    app.add_handler(addproject_conv)
    app.add_handler(CommandHandler("mytickets", my_tickets))
    app.add_handler(CommandHandler("tickets", list_tickets))
    app.add_handler(CommandHandler("assigned", my_assigned))
    app.add_handler(CommandHandler("reply", reply_to_user))
    app.add_handler(CommandHandler("projects", list_projects))
    app.add_handler(CallbackQueryHandler(handle_set_severity, pattern=r"^setsev_"))
    app.add_handler(
        CallbackQueryHandler(
            handle_mod_action,
            pattern=r"^(take|reply|reassign|resolve|unresolved|severity)_",
        )
    )
    # Mensajes libres en DM (fuera de ConversationHandler)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_user_free_message,
        ),
        group=1,
    )

    logger.info("Bot iniciando...")
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await asyncio.Event().wait()
