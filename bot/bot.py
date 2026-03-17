import asyncio
import logging
import os
import sys

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
# User ticket flow
DESCRIPTION, WALLET, BLOCKCHAIN, TX_HASH, SCREENSHOT = range(5)
# Addproject flow
ADD_NAME, ADD_GROUP, ADD_STAFF = range(10, 13)

# ── Helpers ────────────────────────────────────────────────────────────────────

def sev_emoji(s):
    return {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(s or "", "⚪")


def status_emoji(s):
    return {
        "open": "📬",
        "in_progress": "⚙️",
        "resolved": "✅",
        "closed": "🔒",
        "unresolved": "❌",
    }.get(s or "", "❓")


def ticket_card(ticket):
    mod = ticket.get("assigned_mod_username") or "Unassigned"
    created = (ticket.get("created_at") or "")[:16]
    sev = ticket.get("severity")
    sev_line = f"{sev_emoji(sev)} Severity: *{sev.upper()}*\n" if sev else "⚪ Severity: *not set*\n"
    wallet = ticket.get("wallet_address") or "—"
    chain = ticket.get("blockchain") or "—"
    txhash = ticket.get("tx_hash") or "—"
    screenshot = "✅ Yes" if ticket.get("has_screenshot") else "❌ No"
    return (
        f"🎫 *Ticket #{ticket['ticket_id']}*\n"
        f"📁 Project: `{ticket.get('project_name', 'N/A')}`\n"
        f"👤 User: @{ticket.get('username') or ticket['user_telegram_id']}\n"
        f"{sev_line}"
        f"{status_emoji(ticket['status'])} Status: *{ticket['status'].upper()}*\n"
        f"🛡️ Mod: {mod}\n\n"
        f"📝 *Description:*\n_{ticket['description']}_\n\n"
        f"👛 Wallet: `{wallet}`\n"
        f"⛓️ Blockchain: {chain}\n"
        f"🔗 TX Hash: `{txhash}`\n"
        f"🖼️ Screenshot: {screenshot}\n"
        f"🕐 Created: {created}"
    )


def staff_keyboard(ticket_db_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✋ Take", callback_data=f"take_{ticket_db_id}"),
            InlineKeyboardButton("🔴 Set Severity", callback_data=f"severity_{ticket_db_id}"),
        ],
        [
            InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_db_id}"),
            InlineKeyboardButton("🔄 Reassign", callback_data=f"reassign_{ticket_db_id}"),
        ],
        [
            InlineKeyboardButton("✅ Resolve", callback_data=f"resolve_{ticket_db_id}"),
            InlineKeyboardButton("❌ No solution", callback_data=f"unresolved_{ticket_db_id}"),
        ],
    ])


async def notify_staff(bot, ticket, project):
    staff_chat_id = project.get("staff_chat_id")
    if not staff_chat_id:
        return
    text = f"🚨 *NEW TICKET*\n\n{ticket_card(ticket)}"
    try:
        await bot.send_message(
            chat_id=int(staff_chat_id),
            text=text,
            parse_mode="Markdown",
            reply_markup=staff_keyboard(ticket["id"]),
        )
    except Exception as e:
        logger.error(f"Error notifying staff: {e}")


# ── /start — deep link entry point ─────────────────────────────────────────────

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
                    f"🎫 *New ticket — {project['name']}*\n\n"
                    "📝 Describe your problem in detail:",
                    parse_mode="Markdown",
                )
                return DESCRIPTION
        except Exception:
            pass

    await update.message.reply_text(
        "👋 *Ticket Support Bot*\n\n"
        "To open a ticket, go to the project group and use /ticket.\n\n"
        "Mod commands:\n"
        "• /tickets — list open tickets\n"
        "• /assigned — your assigned tickets\n"
        "• /reply TKT-XXXX message — reply to user",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /ticket — in group sends deep link; in DM starts flow directly ─────────────

async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        project = db.get_project_by_group_id(str(chat.id))
        if project:
            bot_info = await context.bot.get_me()
            keyboard = [[
                InlineKeyboardButton(
                    "📬 Open ticket privately",
                    url=f"https://t.me/{bot_info.username}?start=ticket_{project['id']}",
                )
            ]]
            await update.message.reply_text(
                f"To open a ticket for *{project['name']}*, tap below 👇",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("⚠️ This group has no project configured.")
        return ConversationHandler.END

    # In DM: pick project if multiple
    projects = db.get_all_projects()
    if not projects:
        await update.message.reply_text("⚠️ No projects configured yet.")
        return ConversationHandler.END

    if len(projects) == 1:
        context.user_data["project_id"] = projects[0]["id"]
        context.user_data["project_name"] = projects[0]["name"]
        await update.message.reply_text(
            f"🎫 *New ticket — {projects[0]['name']}*\n\n"
            "📝 Describe your problem in detail:",
            parse_mode="Markdown",
        )
        return DESCRIPTION

    keyboard = [[InlineKeyboardButton(p["name"], callback_data=f"proj_{p['id']}")] for p in projects]
    await update.message.reply_text(
        "📋 *Which project is this ticket for?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    # Stay in DESCRIPTION; project_selected moves us there
    return DESCRIPTION


async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split("_")[1])
    project = db.get_project(project_id)
    context.user_data["project_id"] = project_id
    context.user_data["project_name"] = project["name"]
    await query.edit_message_text(
        f"🎫 *New ticket — {project['name']}*\n\n"
        "📝 Describe your problem in detail:",
        parse_mode="Markdown",
    )
    return DESCRIPTION


async def got_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["description"] = update.message.text
    keyboard = [[InlineKeyboardButton("⏭️ Skip", callback_data="skip_wallet")]]
    await update.message.reply_text(
        "👛 *Wallet address?*\n_(or skip if not applicable)_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WALLET


async def got_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wallet_address"] = update.message.text
    return await ask_blockchain(update, context)


async def skip_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["wallet_address"] = None
    return await ask_blockchain_query(query, context)


async def ask_blockchain(update, context):
    keyboard = [
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
        [InlineKeyboardButton("⏭️ Skip", callback_data="chain_skip")],
    ]
    await update.message.reply_text(
        "⛓️ *Blockchain / network?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return BLOCKCHAIN


async def ask_blockchain_query(query, context):
    keyboard = [
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
        [InlineKeyboardButton("⏭️ Skip", callback_data="chain_skip")],
    ]
    await query.edit_message_text(
        "⛓️ *Blockchain / network?*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return BLOCKCHAIN


async def got_blockchain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chain = query.data.replace("chain_", "")
    context.user_data["blockchain"] = None if chain == "skip" else chain
    keyboard = [[InlineKeyboardButton("⏭️ Skip", callback_data="skip_txhash")]]
    await query.edit_message_text(
        "🔗 *Transaction hash?*\n_(or skip)_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return TX_HASH


async def got_tx_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tx_hash"] = update.message.text
    return await ask_screenshot(update, context)


async def skip_tx_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["tx_hash"] = None
    keyboard = [[InlineKeyboardButton("⏭️ Skip", callback_data="skip_screenshot")]]
    await query.edit_message_text(
        "🖼️ *Send a screenshot* or skip:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SCREENSHOT


async def ask_screenshot(update, context):
    keyboard = [[InlineKeyboardButton("⏭️ Skip", callback_data="skip_screenshot")]]
    await update.message.reply_text(
        "🖼️ *Send a screenshot* or skip:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SCREENSHOT


async def got_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["has_screenshot"] = True
    return await create_ticket(update, context)


async def skip_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["has_screenshot"] = False
    return await create_ticket_from_query(query, context)


async def create_ticket(update, context):
    user = update.effective_user
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
    )
    project = db.get_project(ud["project_id"])
    await update.message.reply_text(
        f"✅ *Ticket #{ticket['ticket_id']} created!*\n\n"
        f"📁 Project: {project['name']}\n"
        "A moderator will contact you soon.",
        parse_mode="Markdown",
    )
    await notify_staff(context.bot, ticket, project)
    context.user_data.clear()
    return ConversationHandler.END


async def create_ticket_from_query(query, context):
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
    )
    project = db.get_project(ud["project_id"])
    await query.edit_message_text(
        f"✅ *Ticket #{ticket['ticket_id']} created!*\n\n"
        f"📁 Project: {project['name']}\n"
        "A moderator will contact you soon.",
        parse_mode="Markdown",
    )
    await notify_staff(context.bot, ticket, project)
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ── /mytickets ─────────────────────────────────────────────────────────────────

async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tickets = db.get_tickets_by_user(str(user.id))
    if not tickets:
        await update.message.reply_text("📭 No active tickets.")
        return
    lines = [f"📋 *Your tickets ({len(tickets)}):*\n"]
    for t in tickets[:10]:
        sev = sev_emoji(t.get("severity"))
        lines.append(
            f"{status_emoji(t['status'])} `#{t['ticket_id']}` {sev}\n"
            f"   _{t['description'][:60]}{'...' if len(t['description'])>60 else ''}_\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Mod commands ───────────────────────────────────────────────────────────────

async def list_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickets = db.get_open_tickets()
    if not tickets:
        await update.message.reply_text("📭 No open tickets.")
        return
    lines = [f"📋 *Open tickets ({len(tickets)}):*\n"]
    for t in tickets[:15]:
        lines.append(
            f"• `#{t['ticket_id']}` {sev_emoji(t.get('severity'))} "
            f"[{t.get('project_name','?')}] — {status_emoji(t['status'])}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def my_assigned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mod = update.effective_user
    tickets = db.get_tickets_by_mod(str(mod.id))
    if not tickets:
        await update.message.reply_text("📭 No assigned tickets.")
        return
    lines = [f"🛡️ *Your assigned ({len(tickets)}):*\n"]
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
            "Usage: `/reply TKT-XXXX your message here`", parse_mode="Markdown"
        )
        return
    ticket_id = context.args[0].upper().replace("#", "")
    message = " ".join(context.args[1:])
    ticket = db.get_ticket_by_ticket_id(ticket_id)
    if not ticket:
        await update.message.reply_text(f"⚠️ Ticket {ticket_id} not found.")
        return
    mod = update.effective_user
    db.add_mod_response(ticket["id"], str(mod.id), mod.username or mod.first_name, message)
    try:
        await context.bot.send_message(
            chat_id=int(ticket["user_telegram_id"]),
            text=f"💬 *Moderator reply — Ticket #{ticket_id}:*\n\n{message}",
            parse_mode="Markdown",
        )
        await update.message.reply_text(f"✅ Reply sent to ticket #{ticket_id}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not send to user: {e}")


# ── Mod callback actions ───────────────────────────────────────────────────────

async def handle_mod_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.rsplit("_", 1)
    action, ticket_db_id = parts[0], int(parts[1])
    ticket = db.get_ticket_by_db_id(ticket_db_id)
    mod = query.from_user

    if not ticket:
        await query.answer("⚠️ Ticket not found.", show_alert=True)
        return

    if action == "take":
        db.assign_ticket(ticket_db_id, str(mod.id), mod.username or mod.first_name)
        db.update_ticket_status(ticket_db_id, "in_progress")
        updated = db.get_ticket_by_db_id(ticket_db_id)
        await query.edit_message_text(
            f"⚙️ *Taken by @{mod.username or mod.first_name}*\n\n{ticket_card(updated)}\n\n"
            f"_Reply with: `/reply {ticket['ticket_id']} your message`_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_db_id}"),
                    InlineKeyboardButton("🔄 Reassign", callback_data=f"reassign_{ticket_db_id}"),
                ],
                [
                    InlineKeyboardButton("✅ Resolve", callback_data=f"resolve_{ticket_db_id}"),
                    InlineKeyboardButton("❌ No solution", callback_data=f"unresolved_{ticket_db_id}"),
                ],
            ]),
        )
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"⚙️ *Your ticket #{ticket['ticket_id']} has been picked up by a moderator.*\nYou'll receive a reply soon.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "reply":
        await query.answer(
            f"Reply with: /reply {ticket['ticket_id']} your message",
            show_alert=True,
        )

    elif action == "reassign":
        db.unassign_ticket(ticket_db_id)
        updated = db.get_ticket_by_db_id(ticket_db_id)
        await query.edit_message_text(
            f"🔄 *Ticket #{ticket['ticket_id']} reassigned — now open*\n\n{ticket_card(updated)}",
            parse_mode="Markdown",
            reply_markup=staff_keyboard(ticket_db_id),
        )

    elif action == "resolve":
        db.update_ticket_status(ticket_db_id, "resolved")
        await query.edit_message_text(
            f"✅ *Ticket #{ticket['ticket_id']} RESOLVED by @{mod.username or mod.first_name}*",
            parse_mode="Markdown",
        )
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"✅ *Your ticket #{ticket['ticket_id']} has been resolved.*\nIf the issue persists, open a new ticket with /ticket.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "unresolved":
        db.update_ticket_status(ticket_db_id, "unresolved")
        await query.edit_message_text(
            f"❌ *Ticket #{ticket['ticket_id']} CLOSED — no solution — @{mod.username or mod.first_name}*",
            parse_mode="Markdown",
        )
        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_telegram_id"]),
                text=f"❌ *Your ticket #{ticket['ticket_id']} was closed without a solution.*\nThe team is aware of the issue.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Cannot notify user: {e}")

    elif action == "severity":
        keyboard = [
            [
                InlineKeyboardButton("🟢 Low", callback_data=f"setsev_low_{ticket_db_id}"),
                InlineKeyboardButton("🟡 Medium", callback_data=f"setsev_medium_{ticket_db_id}"),
            ],
            [
                InlineKeyboardButton("🟠 High", callback_data=f"setsev_high_{ticket_db_id}"),
                InlineKeyboardButton("🔴 Critical", callback_data=f"setsev_critical_{ticket_db_id}"),
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
        f"🔴 *Severity set to {severity.upper()}*\n\n{ticket_card(updated)}",
        parse_mode="Markdown",
        reply_markup=staff_keyboard(ticket_db_id),
    )


# ── /addproject ────────────────────────────────────────────────────────────────

async def addproject_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *New project setup*\n\n"
        "Step 1: What's the *project name*?",
        parse_mode="Markdown",
    )
    return ADD_NAME


async def addproject_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_proj_name"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Name: *{context.user_data['new_proj_name']}*\n\n"
        "Step 2: Add me to the *public group* and send any message there.\n"
        "I'll detect the Group Chat ID automatically.\n\n"
        "_Or send the ID directly (e.g. -1001234567890)_",
        parse_mode="Markdown",
    )
    return ADD_GROUP


async def addproject_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    # Auto-detect: if message comes from a group chat
    if chat.type in ("group", "supergroup"):
        context.user_data["new_proj_group_id"] = str(chat.id)
        await update.message.reply_text(
            f"✅ Public group detected: `{chat.id}` ({chat.title})\n\n"
            "Step 3: Now add me to the *staff group* and send any message there.\n\n"
            "_Or send the ID directly_",
            parse_mode="Markdown",
        )
        return ADD_STAFF

    # Manual entry in DM
    text = update.message.text.strip()
    context.user_data["new_proj_group_id"] = text
    await update.message.reply_text(
        f"✅ Group ID: `{text}`\n\n"
        "Step 3: Now send me the *staff chat ID*\n_(or add me to the staff group and send a message)_",
        parse_mode="Markdown",
    )
    return ADD_STAFF


async def addproject_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        staff_id = str(chat.id)
        staff_name = chat.title
    else:
        staff_id = update.message.text.strip()
        staff_name = staff_id

    project = db.create_project(
        name=context.user_data["new_proj_name"],
        group_chat_id=context.user_data.get("new_proj_group_id"),
        staff_chat_id=staff_id,
    )
    # Notify in the right chat (DM of the admin)
    target = update.effective_user.id
    await context.bot.send_message(
        chat_id=target,
        text=(
            f"✅ *Project created!*\n\n"
            f"Name: *{project['name']}*\n"
            f"Group: `{project.get('group_chat_id','—')}`\n"
            f"Staff: `{project.get('staff_chat_id','—')}`\n\n"
            f"Use /ticket in the public group to test."
        ),
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = db.get_all_projects()
    if not projects:
        await update.message.reply_text("📭 No projects configured. Use /addproject.")
        return
    lines = [f"📋 *Projects ({len(projects)}):*\n"]
    for p in projects:
        lines.append(
            f"• *{p['name']}* (#{p['id']})\n"
            f"  Group: `{p.get('group_chat_id','—')}`\n"
            f"  Staff: `{p.get('staff_chat_id','—')}`\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── main ───────────────────────────────────────────────────────────────────────

async def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    # User ticket conversation
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

    # Add project conversation (per_chat=False so it can catch group messages)
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

    logger.info("Bot starting...")
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await asyncio.Event().wait()
