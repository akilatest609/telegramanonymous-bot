import asyncio
import logging
import sqlite3
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# === PASTE YOUR NEW TOKEN AND ADMIN ID HERE ===
BOT_TOKEN = "8951474107:AAHLBI_7fIOjw70mNg5_qcPu2v9UGyCTk6k"
ALLOWED_USER_ID = 8536087082
# ===============================================

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

class UserRegistry:
    def __init__(self, db_path="bot_users.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_map (
                    admin_msg_id INTEGER PRIMARY KEY,
                    user_id INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY
                )
            """)
            conn.commit()

    def add_user(self, user_id: int, username: str, first_name: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (user_id, username, first_name)
                VALUES (?, ?, ?)
            """, (user_id, username, first_name))
            conn.commit()

    def get_all_users(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, username, first_name, joined_at FROM users")
            return cursor.fetchall()

    def map_admin_message(self, admin_msg_id: int, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO chat_map (admin_msg_id, user_id)
                VALUES (?, ?)
            """, (admin_msg_id, user_id))
            conn.commit()

    def get_user_by_admin_msg(self, admin_msg_id: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM chat_map WHERE admin_msg_id = ?", (admin_msg_id,))
            row = cursor.fetchone()
            return row[0] if row else None

    def block_user(self, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO blocked_users (user_id) VALUES (?)", (user_id,))
            conn.commit()

    def unblock_user(self, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
            conn.commit()

    def is_blocked(self, user_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM blocked_users WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None

    def get_blocked_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM blocked_users")
            row = cursor.fetchone()
            return row[0] if row else 0

user_registry = UserRegistry()

def restricted(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
        if user_id != ALLOWED_USER_ID:
            if update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def get_admin_menu_keyboard(batch_active=False):
    buttons = [
        [InlineKeyboardButton("📊 Stats & Blocked Count", callback_data="menu_stats")],
        [InlineKeyboardButton("📦 Start Batch (Choose User)", callback_data="menu_batch_select")],
    ]
    if batch_active:
        buttons.append([InlineKeyboardButton("🛑 End & Dispatch Active Batch", callback_data="menu_batch_end_action")])
    
    buttons.extend([
        [InlineKeyboardButton("💬 Chat Relay Guide", callback_data="menu_chat_guide")],
        [InlineKeyboardButton("📦 Batch Mode Guide", callback_data="menu_batch_guide")],
        [InlineKeyboardButton("❌ Close Menu", callback_data="menu_close")]
    ])
    return InlineKeyboardMarkup(buttons)

@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_batch_active = context.user_data.get('batch_active', False)
    text = (
        "👑 **Telegram File Share & Admin Control Panel**\n\n"
        "Welcome back, Admin! Use the interactive menu below or commands like `/block`, `/unblock`, and `/broadcast` to manage your bot."
    )
    markup = get_admin_menu_keyboard(batch_active=is_batch_active)
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

@restricted
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = user_registry.get_all_users()
    blocked_count = user_registry.get_blocked_count()
    if not users:
        text = f"📂 **Bot Statistics**\n\n• Total Active Users: `0`\n• Blocked Users: `{blocked_count}`"
    else:
        text = f"📊 **Bot Statistics**\n\n• Total Users: `{len(users)}`\n• Blocked Users: `{blocked_count}`\n\n"
        for uid, uname, fname, joined in users[:15]:  # Show latest 15 users to prevent message overflow
            username_str = f"@{uname}" if uname else "No username"
            status_tag = " 🚫 [BLOCKED]" if user_registry.is_blocked(uid) else ""
            text += f"• **{fname or 'Unknown'}** ({username_str}){status_tag}\n  ID: `{uid}` | Joined: `{joined}`\n\n"
    
    keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

@restricted
async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user_id = None
    if update.message.reply_to_message:
        target_user_id = user_registry.get_user_by_admin_msg(update.message.reply_to_message.message_id)
    elif context.args:
        try:
            target_user_id = int(context.args[0])
        except ValueError:
            pass

    if not target_user_id:
        await update.message.reply_text("⚠️ Reply to a user's forwarded message or provide a User ID: `/block <user_id>`", parse_mode="Markdown")
        return

    user_registry.block_user(target_user_id)
    await update.message.reply_text(f"🚫 User ID `{target_user_id}` has been blocked successfully.", parse_mode="Markdown")

@restricted
async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user_id = None
    if update.message.reply_to_message:
        target_user_id = user_registry.get_user_by_admin_msg(update.message.reply_to_message.message_id)
    elif context.args:
        try:
            target_user_id = int(context.args[0])
        except ValueError:
            pass

    if not target_user_id:
        await update.message.reply_text("⚠️ Reply to a user's forwarded message or provide a User ID: `/unblock <user_id>`", parse_mode="Markdown")
        return

    user_registry.unblock_user(target_user_id)
    await update.message.reply_text(f"✅ User ID `{target_user_id}` has been unblocked.", parse_mode="Markdown")

@restricted
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/broadcast <your announcement message>`", parse_mode="Markdown")
        return

    broadcast_text = " ".join(context.args)
    users = user_registry.get_all_users()
    success_count = 0
    fail_count = 0

    for uid, _, _, _ in users:
        if user_registry.is_blocked(uid):
            continue
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 **Announcement:**\n\n{broadcast_text}",
                parse_mode="Markdown"
            )
            success_count += 1
        except Exception:
            fail_count += 1

    await update.message.reply_text(
        f"📢 **Broadcast Results**\n\n"
        f"• Successfully delivered: `{success_count}`\n"
        f"• Failed / Blocked bot: `{fail_count}`",
        parse_mode="Markdown"
    )

async def show_batch_user_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit=False):
    users = user_registry.get_all_users()
    active_users = [u for u in users if not user_registry.is_blocked(u[0])]
    if not active_users:
        text = "📂 No active unblocked users found in database yet."
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
        markup = InlineKeyboardMarkup(keyboard)
        if is_edit:
            await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        return

    text = "👥 **Select Target User for Batch Mode:**\nChoose a user from the list below to begin recording files:"
    keyboard = []
    for uid, uname, fname, _ in active_users:
        name = fname or "Unknown"
        uname_str = f"(@{uname})" if uname else ""
        keyboard.append([InlineKeyboardButton(f"{name} {uname_str} [ID: {uid}]", callback_data=f"batch_choose_{uid}")])
    
    keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="menu_main")])
    markup = InlineKeyboardMarkup(keyboard)
    
    if is_edit:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

@restricted
async def menu_batch_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_batch_user_selection(update, context, is_edit=True)

@restricted
async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_main":
        await cmd_start(update, context)
    elif data == "menu_stats":
        await cmd_stats(update, context)
    elif data == "menu_batch_select":
        await menu_batch_select(update, context)
    elif data.startswith("batch_choose_"):
        target_id = int(data.split("_")[2])
        context.user_data['batch_active'] = True
        context.user_data['batch_target_id'] = target_id
        context.user_data['batch_queue'] = []
        context.user_data['batch_delete_timer'] = 0

        await update_batch_panel(query, target_id, 0)

    elif data.startswith("batch_timer_"):
        seconds_to_add = int(data.split("_")[2])
        current_timer = context.user_data.get('batch_delete_timer', 0)
        
        if seconds_to_add == 0:
            new_timer = 0
        else:
            new_timer = current_timer + seconds_to_add
            
        context.user_data['batch_delete_timer'] = new_timer
        target_id = context.user_data.get('batch_target_id')
        if target_id:
            await update_batch_panel(query, target_id, new_timer)

    elif data == "menu_batch_end_action":
        await execute_batch_end(update, context, is_callback=True)
    elif data == "menu_chat_guide":
        text = (
            "💬 **Chat Relay & Moderation Guide**\n\n"
            "1. Reply directly to any user message in admin chat to reply back.\n"
            "2. Use `/block` (by replying or ID) to block disruptive users.\n"
            "3. Use `/unblock <user_id>` to restore access.\n"
            "4. Use `/broadcast <message>` to send updates to everyone."
        )
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data == "menu_batch_guide":
        text = (
            "📦 **Batch Dispatch Mode Guide**\n\n"
            "• **Start Batch:** Click 'Start Batch' or type `/batch_start`.\n"
            "• **Auto-Delete Timer:** Tap buttons to stack auto-delete time.\n"
            "• **Record & Dispatch:** Send files safely in automated chunks of 100."
        )
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data == "menu_close":
        await query.message.delete()

async def update_batch_panel(query, target_id, delete_timer_sec):
    if delete_timer_sec == 0:
        current_timer_text = "❌ Off (Never Delete)"
    else:
        hours = delete_timer_sec // 3600
        minutes = (delete_timer_sec % 3600) // 60
        seconds = delete_timer_sec % 60
        
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0:
            parts.append(f"{seconds}s")
        current_timer_text = f"⏱️ {' '.join(parts)}"

    text = (
        f"🔴 **Batch Mode Started** for Target ID: `{target_id}`\n\n"
        f"• **Auto-Delete Timer:** `{current_timer_text}`\n\n"
        "Every file you send now will be recorded. Click 'End & Dispatch Batch' when ready."
    )
    keyboard = [
        [
            InlineKeyboardButton("30s", callback_data="batch_timer_30"),
            InlineKeyboardButton("5m", callback_data="batch_timer_300"),
            InlineKeyboardButton("1h", callback_data="batch_timer_3600"),
            InlineKeyboardButton("1d", callback_data="batch_timer_86400")
        ],
        [InlineKeyboardButton("Reset Timer (Off)", callback_data="batch_timer_0")],
        [InlineKeyboardButton("🚀 End & Dispatch Batch", callback_data="menu_batch_end_action")],
        [InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@restricted
async def cmd_batch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    target_id = None

    args = context.args
    if args:
        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("⚠️ Invalid User ID provided. Usage: `/batch_start <user_id>` or reply to a message.")
            return
    elif message.reply_to_message:
        reply_id = message.reply_to_message.message_id
        target_id = user_registry.get_user_by_admin_msg(reply_id)

    if not target_id:
        await show_batch_user_selection(update, context, is_edit=False)
        return

    context.user_data['batch_active'] = True
    context.user_data['batch_target_id'] = target_id
    context.user_data['batch_queue'] = []
    context.user_data['batch_delete_timer'] = 0

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Off", callback_data="batch_timer_0"),
            InlineKeyboardButton("1m", callback_data="batch_timer_60"),
            InlineKeyboardButton("5m", callback_data="batch_timer_300"),
            InlineKeyboardButton("1h", callback_data="batch_timer_3600")
        ],
        [InlineKeyboardButton("🚀 End & Dispatch Batch", callback_data="menu_batch_end_action")]
    ])
    await message.reply_text(
        f"🔴 **Batch Mode Started** for Target ID: `{target_id}`\n\n"
        f"• **Auto-Delete Timer:** `❌ Off (Never Delete)`\n\n"
        "Every file you send now will be recorded. Click 'End & Dispatch Batch' when ready.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def delete_messages_after_delay(bot, chat_id, message_ids, delay_seconds):
    await asyncio.sleep(delay_seconds)
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.error(f"Failed to auto-delete message {msg_id}: {e}")

async def execute_batch_end(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback=False):
    if not context.user_data.get('batch_active'):
        msg_text = "⚠️ No active batch session found. Start one from the menu or with `/batch_start`."
        if is_callback:
            await update.callback_query.message.reply_text(msg_text)
        else:
            await update.message.reply_text(msg_text)
        return

    target_id = context.user_data['batch_target_id']
    queue = context.user_data.get('batch_queue', [])
    delete_timer = context.user_data.get('batch_delete_timer', 0)

    context.user_data['batch_active'] = False
    context.user_data.pop('batch_target_id', None)
    context.user_data.pop('batch_queue', None)
    context.user_data.pop('batch_delete_timer', None)

    source_msg = update.callback_query.message if is_callback else update.message

    if not queue:
        await source_msg.reply_text("⚠️ Batch was empty. No files were sent.")
        return

    total_items = len(queue)
    status_msg = await source_msg.reply_text(f"🚀 Dispatching {total_items} files in safe chunks to user `{target_id}`...")

    actual_sent_ids = []
    chunk_size = 100

    try:
        for i in range(0, total_items, chunk_size):
            chunk = queue[i:i + chunk_size]
            
            sent_msg_ids = await context.bot.copy_messages(
                chat_id=target_id,
                from_chat_id=ALLOWED_USER_ID,
                message_ids=chunk
            )
            
            actual_sent_ids.extend([m.message_id for m in sent_msg_ids])

            if i + chunk_size < total_items:
                await asyncio.sleep(2.5)

        if delete_timer > 0:
            asyncio.create_task(delete_messages_after_delay(context.bot, target_id, actual_sent_ids, delete_timer))
            timer_str = f"⏱️ Auto-deleting in {delete_timer}s"
        else:
            timer_str = "No auto-delete"

        await status_msg.edit_text(
            f"✅ **Batch Delivery Complete**\n\n"
            f"• Target ID: `{target_id}`\n"
            f"• Successfully Sent: `{total_items}` files\n"
            f"• Timer: `{timer_str}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to copy batch messages: {e}")
        await status_msg.edit_text(f"❌ Failed to dispatch batch: {e}")

@restricted
async def cmd_batch_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await execute_batch_end(update, context, is_callback=False)

@restricted
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    if context.user_data.get('batch_active'):
        context.user_data['batch_queue'].append(message.message_id)
        await message.set_reaction("👀")
        return

    if message.reply_to_message:
        reply_id = message.reply_to_message.message_id
        target_user_id = user_registry.get_user_by_admin_msg(reply_id)
        
        if target_user_id:
            try:
                sent_msg = await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=ALLOWED_USER_ID,
                    message_id=message.message_id
                )
                user_registry.map_admin_message(message.message_id, target_user_id)
                await message.set_reaction("👍")
            except Exception as e:
                await message.reply_text(f"⚠️ Error: {e}")

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if not message:
        return

    if user.id == ALLOWED_USER_ID:
        return

    if user_registry.is_blocked(user.id):
        await message.reply_text("❌ You have been blocked from using this bot.")
        return

    if message.text and message.text.startswith("/start"):
        await message.reply_text(
            "සාදරයෙන් පිලිගන්නවා 💕, ඇඩ්මින් ට එවන්න ඔනෙ මැසෙජ් එක ටයිප් කරලා එවන්න, ."
        )
        return

    user_registry.add_user(user.id, user.username, user.first_name)
    header = f"📩 **msg / From:** {user.first_name} (`{user.id}`)\n\n"
    
    try:
        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=header, parse_mode="Markdown")
        forwarded_msg = await context.bot.copy_message(
            chat_id=ALLOWED_USER_ID,
            from_chat_id=user.id,
            message_id=message.message_id
        )
        user_registry.map_admin_message(forwarded_msg.message_id, user.id)
    except Exception as e:
        logger.error(f"User message forward error: {e}")

async def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("stats", cmd_stats, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("block", cmd_block, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("unblock", cmd_unblock, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("batch_start", cmd_batch_start, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("batch_end", cmd_batch_end, filters.User(ALLOWED_USER_ID)))
    
    application.add_handler(CallbackQueryHandler(menu_callback_handler, pattern="^(menu_|batch_choose_|batch_timer_)"))

    application.add_handler(MessageHandler(filters.User(ALLOWED_USER_ID) & ~filters.COMMAND, handle_admin_message))
    application.add_handler(MessageHandler(~filters.User(ALLOWED_USER_ID), handle_user_message))

    logger.info("File Share Bot started successfully with Block/Unblock, Broadcast, and Batch capabilities.")
    await application.initialize()
    await application.start()

    await application.bot.set_my_commands([
        BotCommand("start", "Open Admin Control Panel"),
        BotCommand("stats", "View Active Users & Blocked Count"),
        BotCommand("block", "Block a user (reply or ID)"),
        BotCommand("unblock", "Unblock a user (reply or ID)"),
        BotCommand("broadcast", "Send announcement to all users"),
        BotCommand("batch_start", "Start a File Batch"),
        BotCommand("batch_end", "Dispatch Active Batch")
    ])
    await application.bot.set_chat_menu_button(
        chat_id=ALLOWED_USER_ID,
        menu_button=MenuButtonCommands()
    )

    await application.updater.start_polling()

    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
