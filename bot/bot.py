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


# ── Helpers ────────────────────────────────────────────────────────────────────

def sev_emoji(s):
    return {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(s or "", "⚪")


def status_emoji(s):
    return {
        "open": "📬", "in_progress": "⚙️", "resolved": "✅",
        "closed": "🔒", "unresolved": "❌",
    }.get(s or "", "❓")


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
                context.user_data["in_flow"] = True
                await update.message.reply_text(
                    f"🎫 *Roof of Top Support — {project['name']}*\n\n"
                    "Describe your issue clearly. The more detail you provide, "
                    "the faster our team can help.",
                    parse_mode="Markdown",
                )
                return DESCRIPTION
        except Exception:
            pass
    await update.message.reply_text(
        "👋 *Roof of Top Support*\n\n"
        "To submit a ticket, use `/ticket` in your project's group "
        "or tap the button shared by the team.\n\n"
        "• /ticket — open a new ticket\n"
        "• /mytickets — view your open tickets",
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
                "📬 Open Ticket",
                url=f"https://t.me/{bot_info.username}?start=ticket_{project['id']}",
            )]]
            await update.message.reply_text(
                f"📬 Open a support ticket for *{project['name']}* in private chat:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "⚠️ This group does not have a project configured."
            )
        return ConversationHandler.END

    projects = db.get_all_projects()
    if not projects:
        await update.message.reply_text("⚠️ No projects are currently configured.")
        return ConversationHandler.END
    if len(projects) == 1:
        context.user_data["project_id"] = projects[0]["id"]
        context.user_data["project_name"] = projects[0]["name"]
        context.user_data["in_flow"] = True
        await update.message.reply_text(
            f"🎫 *Roof of Top Support — {projects[0]['name']}*\n\n"
            "Describe your issue clearly. Include wallet, tx hash, or any "
            "relevant context to speed up resolution.",
            parse_mode="Markdown",
        )
        return DESCRIPTION
    keyboard = [[InlineKeyboardButton(p["name"], callback_data=f"proj_{p['id']}")] for p in projects]
    await update.message.reply_text(
        "📋 *Select the project for your ticket:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    context.user_data["in_flow"] = True
    return DESCRIPTION


async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split("_")[1])
    project = db.get_project(project_id)
    context.user_data["project_id"] = project_id
    context.user_data["project_name"] = project["name"]
    context.user_data["in_flow"] = True
    await query.edit_message_text(
        f"🎫 *Roof of Top Support — {project['name']}*\n\n"
        "Describe your issue clearly. Include wallet, tx hash, or any "
        "relevant context to speed up resolution.",
        parse_mode="Markdown",
    )
    return DESCRIPTION


# ── Form steps ────────────────────────────────────────────────────────────────

async def got_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = update.message.text
    context.user_data["in_flow"] = True
    keyboard = [[InlineKeyboardButton("Skip ⏭", callback_data="skip_wallet")]]
    await update.message.reply_text(
        "👛 *Wallet address?*\n_Paste the address related to this issue, or skip._",
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
        [InlineKeyboardButton("Skip ⏭", callback_data="chain_skip")],
    ])


async def _ask_blockchain_msg(update):
    await update.message.reply_text(
        "⛓️ *Which network?*",
        reply_markup=_blockchain_keyboard(),
        parse_mode="Markdown",
    )
    return BLOCKCHAIN


async def _ask_blockchain_query(query):
    await query.edit_message_text(
        "⛓️ *Which network?*",
        reply_markup=_blockchain_keyboard(),
        parse_mode="Markdown",
    )
    return BLOCKCHAIN


async def got_blockchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chain = query.data.replace("chain_", "")
    context.user_data["blockchain"] = None if chain == "skip" else chain
    keyboard = [[InlineKeyboardButton("Skip ⏭", callback_data="skip_txhash")]]
    await query.edit_message_text(
        "🔗 *Transaction hash?*\n_Paste the tx hash, or skip if not applicable._",
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
    keyboard = [[InlineKeyboardButton("Skip ⏭", callback_data="skip_screenshot")]]
    await query.edit_message_text(
        "📸 *Got a screenshot?*\n_Send the image now, or skip._",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SCREENSHOT


async def _ask_screenshot_msg(update):
    keyboard = [[InlineKeyboardButton("Skip ⏭", callback_data="skip_screenshot")]]
    await update.message.reply_text(
        "📸 *Got a screenshot?*\n_Send the image now, or skip._",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SCREENSHOT


async def got_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
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
    # Fix: Update objects don't have .from_user — use effective_user
    if update is not None:
        user = update.effective_user
    else:
        user = query.from_user

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
        f"✅ *Ticket #{ticket['ticket_id']} submitted*\n\n"
        f"*Project:* {project['name']}\n\n"
        "The Roof of Top support team will review your case and get back to you shortly. "
        "Feel free to reply here with any additional info."
    )
    if update:
        await update.message.reply_text(confirmation, parse_mode="Markdown")
    else:
        await query.edit_message_text(confirmation, parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Ticket cancelled. Use /ticket whenever you're ready to submit one."
    )
    return ConversationHandler.END


# ── Free messages from user in DM ─────────────────────────────────────────────

async def handle_user_free_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves message to DB — visible on web panel, not forwarded anywhere."""
    if update.effective_chat.type != "private":
        return
    # Ignore if user is currently filling out the ticket form
    if context.user_data.get("in_flow"):
        return
    user = update.effective_user
    ticket = db.get_active_ticket_for_user(str(user.id))
    if not ticket:
        await update.message.reply_text(
            "No active ticket found. Use /ticket to open one."
        )
        return
    text = update.message.text or "[media]"
    db.add_message(ticket["id"], "user", str(user.id), user.username or user.first_name, text)
    await update.message.reply_text(
        f"📨 Message added to ticket `#{ticket['ticket_id']}`.\n"
        "_Our team will see it in the panel._",
        parse_mode="Markdown",
    )


# ── /mytickets ─────────────────────────────────────────────────────────────────

async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tickets = db.get_tickets_by_user(str(user.id))
    if not tickets:
        await update.message.reply_text(
            "No open tickets found. Use /ticket to submit one."
        )
        return
    lines = [f"📋 *Your open tickets ({len(tickets)}):*\n"]
    for t in tickets[:10]:
        lines.append(
            f"{status_emoji(t['status'])} `#{t['ticket_id']}` {sev_emoji(t.get('severity'))}\n"
            f"   _{t['description'][:60]}{'...' if len(t['description'])>60 else ''}_\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Notification helper (called from web) ─────────────────────────────────────

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

    app.add_handler(ticket_conv)
    app.add_handler(CommandHandler("mytickets", my_tickets))
    # Free-text messages in DM (outside ConversationHandler)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_user_free_message,
        ),
        group=1,
    )

    logger.info("Bot starting...")
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await asyncio.Event().wait()
