import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
    ContextTypes
)
from database.db import Database
from datetime import datetime

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

CATEGORY, DESCRIPTION, SEVERITY = range(3)
db = Database()

# ─── HELPERS ────────────────────────────────────────────────────────────────

def get_severity_emoji(s):
    return {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(s, "⚪")

def get_status_emoji(s):
    return {"open": "📬", "in_progress": "⚙️", "resolved": "✅", "closed": "🔒", "unresolved": "❌"}.get(s, "❓")

def format_ticket_card(ticket):
    mod = ticket.get("assigned_mod_username") or "Sin asignar"
    created = ticket["created_at"].strftime("%d/%m/%Y %H:%M") if ticket.get("created_at") else "N/A"
    return (
        f"🎫 *Ticket #{ticket['ticket_id']}*\n"
        f"📁 Proyecto: `{ticket.get('project_name', 'N/A')}`\n"
        f"👤 Usuario: @{ticket.get('username', ticket['user_telegram_id'])}\n"
        f"🏷️ Categoría: {ticket['category']}\n"
        f"{get_severity_emoji(ticket['severity'])} Severidad: *{ticket['severity'].upper()}*\n"
        f"{get_status_emoji(ticket['status'])} Estado: *{ticket['status'].upper()}*\n"
        f"🛡️ Mod: {mod}\n"
        f"📝 Descripción:\n_{ticket['description']}_\n"
        f"🕐 Creado: {created}"
    )

async def notify_staff_channel(bot, ticket, project):
    staff_chat_id = project.get("staff_chat_id")
    if not staff_chat_id:
        return
    text = f"🚨 *NUEVO TICKET*\n\n{format_ticket_card(ticket)}"
    keyboard = [[
        InlineKeyboardButton("✋ Tomar", callback_data=f"take_{ticket['id']}"),
        InlineKeyboardButton("🔴 Severidad", callback_data=f"severity_{ticket['id']}"),
    ], [
        InlineKeyboardButton("✅ Resolver", callback_data=f"resolve_{ticket['id']}"),
        InlineKeyboardButton("❌ Sin solución", callback_data=f"unresolved_{ticket['id']}"),
    ]]
    try:
        await bot.send_message(
            chat_id=int(staff_chat_id),
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error notifying staff channel: {e}")

# ─── USER FLOW ───────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    # Handle deep link: /start ticket_<project_id>
    if "ticket_" in text:
        try:
            project_id = int(text.split("ticket_")[1])
            project = db.get_project(project_id)
            if project:
                context.user_data["project_id"] = project_id
                context.user_data["project_name"] = project["name"]
                return await show_category_menu(update, context, project["name"])
        except Exception:
            pass

    await update.message.reply_text(
        "👋 *Sistema de Tickets Oficial*\n\n"
        "Comandos:\n"
        "• /ticket — Abrir nuevo ticket\n"
        "• /miticket — Ver tus tickets\n"
        "• /cancelar — Cancelar operación\n\n"
        "_Soporte oficial del proyecto_",
        parse_mode="Markdown"
    )

async def show_category_menu(update, context, project_name):
    keyboard = [
        [InlineKeyboardButton("🐛 Bug / Error", callback_data="cat_Bug/Error")],
        [InlineKeyboardButton("❓ Consulta general", callback_data="cat_Consulta general")],
        [InlineKeyboardButton("🚨 Reporte de usuario", callback_data="cat_Reporte de usuario")],
        [InlineKeyboardButton("💡 Sugerencia", callback_data="cat_Sugerencia")],
        [InlineKeyboardButton("💰 Problema con transacción", callback_data="cat_Problema con transaccion")],
        [InlineKeyboardButton("🔧 Soporte técnico", callback_data="cat_Soporte tecnico")],
    ]
    msg = f"🏷️ *Proyecto: {project_name}*\n\n¿Cuál es la categoría del problema?"
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return DESCRIPTION

async def open_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        project = db.get_project_by_group_id(str(chat.id))
        if project:
            context.user_data["project_id"] = project["id"]
            context.user_data["project_name"] = project["name"]
            bot_info = await context.bot.get_me()
            keyboard = [[InlineKeyboardButton(
                "📬 Abrir ticket",
                url=f"https://t.me/{bot_info.username}?start=ticket_{project['id']}"
            )]]
            await update.message.reply_text(
                f"Para abrir un ticket de *{project['name']}* hacelo en privado 👇",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return ConversationHandler.END

    projects = db.get_all_projects()
    if not projects:
        await update.message.reply_text("⚠️ No hay proyectos configurados.")
        return ConversationHandler.END

    if len(projects) == 1:
        context.user_data["project_id"] = projects[0]["id"]
        context.user_data["project_name"] = projects[0]["name"]
        return await show_category_menu(update, context, projects[0]["name"])

    keyboard = [[InlineKeyboardButton(p["name"], callback_data=f"proj_{p['id']}")] for p in projects]
    await update.message.reply_text(
        "📋 *¿Para qué proyecto es el ticket?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CATEGORY

async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split("_")[1])
    project = db.get_project(project_id)
    context.user_data["project_id"] = project_id
    context.user_data["project_name"] = project["name"]
    return await show_category_menu(update, context, project["name"])

async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["category"] = query.data.replace("cat_", "")
    await query.edit_message_text(
        f"✅ Categoría: *{context.user_data['category']}*\n\n"
        "📝 Escribí una descripción detallada del problema:",
        parse_mode="Markdown"
    )
    return SEVERITY

async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = update.message.text
    keyboard = [
        [InlineKeyboardButton("🟢 Baja — No urgente", callback_data="sev_low")],
        [InlineKeyboardButton("🟡 Media — Molesto pero funcional", callback_data="sev_medium")],
        [InlineKeyboardButton("🟠 Alta — Afecta el uso normal", callback_data="sev_high")],
        [InlineKeyboardButton("🔴 Crítica — Sistema caído / fondos", callback_data="sev_critical")],
    ]
    await update.message.reply_text(
        "⚠️ *¿Cuál es la severidad?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def severity_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    severity = query.data.replace("sev_", "")
    user = query.from_user

    ticket = db.create_ticket(
        project_id=context.user_data["project_id"],
        user_telegram_id=str(user.id),
        username=user.username or user.first_name,
        category=context.user_data["category"],
        description=context.user_data["description"],
        severity=severity
    )
    project = db.get_project(context.user_data["project_id"])

    await query.edit_message_text(
        f"✅ *¡Ticket creado!*\n\n"
        f"🎫 ID: `#{ticket['ticket_id']}`\n"
        f"📁 Proyecto: {project['name']}\n"
        f"{get_severity_emoji(severity)} Severidad: {severity.upper()}\n\n"
        f"Un moderador te contactará pronto.\n"
        f"Seguí el estado con /miticket",
        parse_mode="Markdown"
    )
    await notify_staff_channel(context.bot, ticket, project)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END

async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tickets = db.get_tickets_by_user(str(user.id))
    if not tickets:
        await update.message.reply_text("📭 No tenés tickets activos.")
        return
    text = f"📋 *Tus tickets ({len(tickets)}):*\n\n"
    for t in tickets[:10]:
        text += f"{get_status_emoji(t['status'])} `#{t['ticket_id']}` — {t['category']} {get_severity_emoji(t['severity'])}\n"
        text += f"   _{t['description'][:60]}{'...' if len(t['description'])>60 else ''}_\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── MOD ACTIONS ─────────────────────────────────────────────────────────────

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
        updated = db.get_ticket_by_db_id(ticket_db_id)
        await query.edit_message_text(
            f"⚙️ *Ticket tomado por @{mod.username or mod.first_name}*\n\n"
            f"{format_ticket_card(updated)}\n\n"
            f"_Para responder: `/responder {ticket['ticket_id']} <mensaje>`_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Resolver", callback_data=f"resolve_{ticket_db_id}"),
                InlineKeyboardButton("❌ Sin solución", callback_data=f"unresolved_{ticket_db_id}"),
            ]])
        )
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"⚙️ *Tu ticket #{ticket['ticket_id']} fue tomado por un moderador.*\nPronto recibirás respuesta.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "resolve":
        db.update_ticket_status(ticket_db_id, "resolved")
        await query.edit_message_text(
            f"✅ *Ticket #{ticket['ticket_id']} RESUELTO por @{mod.username or mod.first_name}*",
            parse_mode="Markdown"
        )
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"✅ *Tu ticket #{ticket['ticket_id']} fue resuelto.* Si el problema persiste, abrí uno nuevo con /ticket.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "unresolved":
        db.update_ticket_status(ticket_db_id, "unresolved")
        await query.edit_message_text(
            f"❌ *Ticket #{ticket['ticket_id']} SIN SOLUCIÓN — @{mod.username or mod.first_name}*",
            parse_mode="Markdown"
        )
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"❌ *Tu ticket #{ticket['ticket_id']} fue cerrado sin solución.* El equipo trabajará en ello.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "severity":
        keyboard = [
            [InlineKeyboardButton("🟢 Baja", callback_data=f"setsev_low_{ticket_db_id}"),
             InlineKeyboardButton("🟡 Media", callback_data=f"setsev_medium_{ticket_db_id}")],
            [InlineKeyboardButton("🟠 Alta", callback_data=f"setsev_high_{ticket_db_id}"),
             InlineKeyboardButton("🔴 Crítica", callback_data=f"setsev_critical_{ticket_db_id}")],
        ]
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))

async def handle_set_severity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, severity, ticket_db_id = query.data.split("_", 2)
    ticket_db_id = int(ticket_db_id)
    db.update_ticket_severity(ticket_db_id, severity)
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    await query.edit_message_text(
        format_ticket_card(ticket),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✋ Tomar", callback_data=f"take_{ticket_db_id}"),
            InlineKeyboardButton("✅ Resolver", callback_data=f"resolve_{ticket_db_id}"),
        ]])
    )

async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Uso: `/responder TKT-0000 mensaje aquí`", parse_mode="Markdown")
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
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ Respuesta enviada al ticket #{ticket_id}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ No se pudo enviar al usuario: {e}")

async def list_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickets = db.get_open_tickets()
    if not tickets:
        await update.message.reply_text("📭 No hay tickets abiertos.")
        return
    text = f"📋 *Tickets abiertos ({len(tickets)}):*\n\n"
    for t in tickets[:15]:
        text += f"• `#{t['ticket_id']}` {get_severity_emoji(t['severity'])} [{t['project_name']}] — {t['category']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def my_assigned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mod = update.effective_user
    tickets = db.get_tickets_by_mod(str(mod.id))
    if not tickets:
        await update.message.reply_text("📭 No tenés tickets asignados.")
        return
    text = f"🛡️ *Tus asignados ({len(tickets)}):*\n\n"
    for t in tickets:
        text += f"{get_status_emoji(t['status'])} `#{t['ticket_id']}` {get_severity_emoji(t['severity'])} [{t['project_name']}]\n"
        text += f"   _{t['description'][:60]}_\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("ticket", open_ticket_start)],
        states={
            CATEGORY: [CallbackQueryHandler(project_selected, pattern=r"^proj_\d+$")],
            DESCRIPTION: [CallbackQueryHandler(category_selected, pattern=r"^cat_")],
            SEVERITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, description_received),
                CallbackQueryHandler(severity_selected, pattern=r"^sev_"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("miticket", my_tickets))
    app.add_handler(CommandHandler("tickets", list_tickets))
    app.add_handler(CommandHandler("asignados", my_assigned))
    app.add_handler(CommandHandler("responder", reply_to_user))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_set_severity, pattern=r"^setsev_"))
    app.add_handler(CallbackQueryHandler(handle_mod_action, pattern=r"^(take|resolve|unresolved|severity)_"))

    logger.info("🤖 Bot de tickets iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
