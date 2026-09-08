import asyncio
import logging
import sqlite3
from functools import wraps
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
    InputMediaVideo,
    InputMediaPhoto,
    InputMediaDocument,
    InputMediaAudio,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import RetryAfter, Forbidden
import batch_links

import os

# === Config comes from environment variables — see .env.example ===
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS custom_buttons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    btn_text TEXT,
                    btn_url TEXT
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO bot_settings (key, value) VALUES 
                ('welcome_text', 'ආයුබෝවන් යාලුවනේ 💋✨\nඇඩ්මින් ට එවන්න ඔනෙ මැසෙජ් එක ටයිප් කරලා එවන්න.'),
                ('btn_pkg', '📦 Buy a Package'),
                ('btn_contact', '💬 Contact Admin')
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

    def delete_user(self, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM chat_map WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
            conn.commit()

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

    def get_setting(self, key: str) -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else ""

    def set_setting(self, key: str, value: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit()

    def get_custom_buttons(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, btn_text, btn_url FROM custom_buttons")
            return cursor.fetchall()

    def add_custom_button(self, text: str, url: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO custom_buttons (btn_text, btn_url) VALUES (?, ?)", (text, url))
            conn.commit()

    def delete_custom_button(self, btn_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM custom_buttons WHERE id = ?", (btn_id,))
            conn.commit()

user_registry = UserRegistry()
batch_links.init_batch_tables(user_registry.db_path)

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
        [InlineKeyboardButton("📊 Stats & Blocked Count", callback_data="stats_page_0")],
        [InlineKeyboardButton("⚙️ Edit Welcome Message & Buttons", callback_data="menu_edit_welcome")],
        [InlineKeyboardButton("📦 Start Batch (Choose User)", callback_data="menu_batch_select")],
        [InlineKeyboardButton("🔗 Create Share Link", callback_data="menu_new_link")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="menu_broadcast_select")],
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
    context.user_data.pop('waiting_for', None)
    is_batch_active = context.user_data.get('batch_active', False)
    text = (
        "👑 **Telegram File Share & Admin Control Panel**\n\n"
        "Welcome back, Admin! Use the interactive menu below or commands to manage your bot.\n\n"
        "*(Tip: Send `/start 2` or click 'Preview User Panel' in the menu to test the user-facing welcome panel)*"
    )
    markup = get_admin_menu_keyboard(batch_active=is_batch_active)
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

@restricted
async def cmd_start_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_preview_panel(update)

async def send_preview_panel(update: Update):
    welcome_text = user_registry.get_setting("welcome_text")
    btn_pkg_text = user_registry.get_setting("btn_pkg")
    btn_contact_text = user_registry.get_setting("btn_contact")
    custom_buttons = user_registry.get_custom_buttons()

    keyboard = [
        [InlineKeyboardButton(btn_pkg_text, callback_data="preview_buy_package")],
        [InlineKeyboardButton(btn_contact_text, callback_data="preview_contact_admin")]
    ]

    for cid, btext, burl in custom_buttons:
        keyboard.append([InlineKeyboardButton(btext, url=burl)])

    keyboard.extend([
        [InlineKeyboardButton("✏️ Edit Welcome & Buttons", callback_data="menu_edit_welcome")],
        [InlineKeyboardButton("« Back to Admin Menu", callback_data="menu_main")]
    ])

    await update.message.reply_text(
        f"👀 **[Admin Preview of User Start Menu]**\n\n{welcome_text}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def show_stats_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0, is_edit: bool = False):
    users = user_registry.get_all_users()
    blocked_count = user_registry.get_blocked_count()
    total_users = len(users)

    ITEMS_PER_PAGE = 15
    total_pages = (total_users + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE if total_users > 0 else 1
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_users = users[start_idx:end_idx]

    keyboard = []
    if not users:
        text = f"📊 **Bot Statistics**\n\n• Total Users: `0`\n• Blocked Users: `{blocked_count}`"
    else:
        text = f"📊 **Bot Statistics** (Page {page + 1}/{total_pages})\n\n• Total Users: `{total_users}`\n• Blocked Users: `{blocked_count}`\n\n"
        for uid, uname, fname, joined in current_users:
            username_str = f"@{uname}" if uname else "No username"
            status_tag = " 🚫 [BLOCKED]" if user_registry.is_blocked(uid) else ""
            text += f"• **{fname or 'Unknown'}** ({username_str}){status_tag}\n  ID: `{uid}` | Joined: `{joined}`\n\n"
            keyboard.append([
                InlineKeyboardButton(f"{fname or 'Unknown'} [ID: {uid}]", callback_data=f"noop_{uid}"),
                InlineKeyboardButton("❌", callback_data=f"confirm_del_user_{uid}")
            ])

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("« Prev", callback_data=f"stats_page_{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="noop_page"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next »", callback_data=f"stats_page_{page + 1}"))
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="menu_main")])
    markup = InlineKeyboardMarkup(keyboard)

    if is_edit:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

@restricted
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_stats_page(update, context, page=0, is_edit=False)

async def show_block_user_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0, is_edit=False):
    users = user_registry.get_all_users()
    active_users = [u for u in users if not user_registry.is_blocked(u[0])]
    total_users = len(active_users)
    
    ITEMS_PER_PAGE = 10
    total_pages = (total_users + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE if total_users > 0 else 1
    page = max(0, min(page, total_pages - 1))
    
    current_users = active_users[page * ITEMS_PER_PAGE : (page + 1) * ITEMS_PER_PAGE]

    if not active_users:
        text = "📂 No active unblocked users found to block."
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
        markup = InlineKeyboardMarkup(keyboard)
        if is_edit:
            await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        return

    text = f"🚫 **Select User to Block:** (Page {page + 1}/{total_pages})"
    keyboard = []
    for uid, uname, fname, _ in current_users:
        name = fname or "Unknown"
        uname_str = f"(@{uname})" if uname else ""
        keyboard.append([
            InlineKeyboardButton(f"{name} {uname_str} [ID: {uid}]", callback_data=f"block_noop_{uid}"),
            InlineKeyboardButton("🚫", callback_data=f"block_action_{uid}")
        ])
        
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("« Prev", callback_data=f"block_page_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next »", callback_data=f"block_page_{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="menu_main")])
    markup = InlineKeyboardMarkup(keyboard)

    if is_edit:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
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
        await show_block_user_selection(update, context, page=0, is_edit=False)
        return

    user_registry.block_user(target_user_id)
    await update.message.reply_text(f"🚫 User ID `{target_user_id}` has been blocked successfully.", parse_mode="Markdown")

async def show_unblock_user_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0, is_edit=False):
    users = user_registry.get_all_users()
    blocked_users = [u for u in users if user_registry.is_blocked(u[0])]
    total_users = len(blocked_users)
    
    ITEMS_PER_PAGE = 10
    total_pages = (total_users + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE if total_users > 0 else 1
    page = max(0, min(page, total_pages - 1))
    
    current_users = blocked_users[page * ITEMS_PER_PAGE : (page + 1) * ITEMS_PER_PAGE]

    if not blocked_users:
        text = "📂 No blocked users found."
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
        markup = InlineKeyboardMarkup(keyboard)
        if is_edit:
            await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        return

    text = f"✅ **Select User to Unblock:** (Page {page + 1}/{total_pages})"
    keyboard = []
    for uid, uname, fname, _ in current_users:
        name = fname or "Unknown"
        uname_str = f"(@{uname})" if uname else ""
        keyboard.append([
            InlineKeyboardButton(f"{name} {uname_str} [ID: {uid}]", callback_data=f"unblock_noop_{uid}"),
            InlineKeyboardButton("✅", callback_data=f"unblock_action_{uid}")
        ])
        
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("« Prev", callback_data=f"unblock_page_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next »", callback_data=f"unblock_page_{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="menu_main")])
    markup = InlineKeyboardMarkup(keyboard)

    if is_edit:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

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
        await show_unblock_user_selection(update, context, page=0, is_edit=False)
        return

    user_registry.unblock_user(target_user_id)
    await update.message.reply_text(f"✅ User ID `{target_user_id}` has been unblocked.", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# BROADCAST (fixed markdown-mangling bug + new selective recipient picker)
# ---------------------------------------------------------------------------

async def show_broadcast_user_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0, is_edit: bool = True):
    users = user_registry.get_all_users()
    active_users = [u for u in users if not user_registry.is_blocked(u[0])]
    total_users = len(active_users)
    excluded = context.user_data.setdefault('broadcast_excluded', set())

    if not active_users:
        text = "📂 No active unblocked users found to broadcast to."
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
        markup = InlineKeyboardMarkup(keyboard)
        if is_edit:
            await update.callback_query.message.edit_text(text, reply_markup=markup)
        else:
            await update.message.reply_text(text, reply_markup=markup)
        return

    ITEMS_PER_PAGE = 10
    total_pages = (total_users + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    current_users = active_users[page * ITEMS_PER_PAGE:(page + 1) * ITEMS_PER_PAGE]

    included_count = sum(1 for u in active_users if u[0] not in excluded)

    text = (
        f"📢 **Select Broadcast Recipients** (Page {page + 1}/{total_pages})\n\n"
        f"✅ = will receive the message   🚫 = excluded\n"
        f"Tap a user's icon to toggle. Currently selected: `{included_count}/{total_users}`"
    )
    keyboard = []
    for uid, uname, fname, _ in current_users:
        name = fname or "Unknown"
        uname_str = f"(@{uname})" if uname else ""
        icon = "🚫" if uid in excluded else "✅"
        keyboard.append([
            InlineKeyboardButton(f"{name} {uname_str} [ID: {uid}]", callback_data=f"bc_noop_{uid}"),
            InlineKeyboardButton(icon, callback_data=f"bc_toggle_{uid}_{page}")
        ])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("« Prev", callback_data=f"bc_page_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next »", callback_data=f"bc_page_{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton(f"▶️ Continue ({included_count} recipients)", callback_data="bc_continue")])
    keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="menu_main")])
    markup = InlineKeyboardMarkup(keyboard)

    if is_edit:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def show_broadcast_timer_panel(query, delete_timer_sec: int, recipient_count: int):
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
        f"📢 **Broadcast Auto-Delete Timer**\n\n"
        f"Recipients selected: `{recipient_count}`\n"
        f"• **Auto-Delete Timer:** `{current_timer_text}`\n\n"
        "Tap to stack time, or reset to off. When ready, tap 'Next: Type Message'.\n"
        "The broadcast will auto-delete from each recipient's chat after this delay."
    )
    keyboard = [
        [
            InlineKeyboardButton("30s", callback_data="bc_timer_30"),
            InlineKeyboardButton("5m", callback_data="bc_timer_300"),
            InlineKeyboardButton("1h", callback_data="bc_timer_3600"),
            InlineKeyboardButton("1d", callback_data="bc_timer_86400")
        ],
        [InlineKeyboardButton("Reset Timer (Off)", callback_data="bc_timer_reset")],
        [InlineKeyboardButton("➡️ Next: Type Message", callback_data="bc_timer_go")],
        [InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@restricted
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # No args -> open the interactive recipient picker.
    if not context.args:
        context.user_data['broadcast_excluded'] = set()
        await show_broadcast_user_selection(update, context, page=0, is_edit=False)
        return

    # Quick path: /broadcast <text> sends to everyone unblocked, as plain text
    # (no parse_mode) so underscores/asterisks/links are sent exactly as typed.
    broadcast_text = update.message.text.partition(" ")[2]
    users = user_registry.get_all_users()
    success_count = 0
    fail_count = 0

    for uid, _, _, _ in users:
        if user_registry.is_blocked(uid):
            continue
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 Announcement:\n\n{broadcast_text}"
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

    text = "👥 **Select Target User for Batch Mode:**\nChoose a user from the list below:"
    keyboard = []
    for uid, uname, fname, _ in active_users:
        name = fname or "Unknown"
        uname_str = f"(@{uname})" if uname else ""
        keyboard.append([
            InlineKeyboardButton(f"{name} {uname_str} [ID: {uid}]", callback_data=f"batch_choose_{uid}"),
            InlineKeyboardButton("❌", callback_data=f"confirm_del_user_{uid}")
        ])

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

async def show_edit_welcome_menu(query):
    current_text = user_registry.get_setting("welcome_text")
    btn1 = user_registry.get_setting("btn_pkg")
    btn2 = user_registry.get_setting("btn_contact")
    custom_buttons = user_registry.get_custom_buttons()

    text = (
        "⚙️ **Edit Welcome Message & Buttons**\n\n"
        f"• **Current Welcome Text:**\n`{current_text}`\n\n"
        f"• **Button 1 (Package):** `{btn1}`\n"
        f"• **Button 2 (Contact):** `{btn2}`\n"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Change Welcome Message", callback_data="edit_msg_prompt")],
        [InlineKeyboardButton("✏️ Change Button 1 Name", callback_data="edit_btn1_prompt")],
        [InlineKeyboardButton("✏️ Change Button 2 Name", callback_data="edit_btn2_prompt")],
        [InlineKeyboardButton("➕ Add Custom Button", callback_data="edit_add_custom_btn")]
    ]

    if custom_buttons:
        text += "\n📦 **Custom Buttons Configured:**\n"
        for cid, btext, burl in custom_buttons:
            text += f"• `{btext}` -> `{burl}`\n"
            keyboard.append([InlineKeyboardButton(f"🗑️ Delete Button: {btext}", callback_data=f"confirm_del_btn_{cid}")])

    keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="menu_main")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@restricted
async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_main":
        context.user_data.pop('waiting_for', None)
        await cmd_start(update, context)
    elif data.startswith("stats_page_"):
        page_num = int(data.split("_")[2])
        await show_stats_page(update, context, page=page_num, is_edit=True)
    elif data.startswith("block_page_"):
        page_num = int(data.split("_")[2])
        await show_block_user_selection(update, context, page=page_num, is_edit=True)
    elif data.startswith("unblock_page_"):
        page_num = int(data.split("_")[2])
        await show_unblock_user_selection(update, context, page=page_num, is_edit=True)
    elif data == "noop_page":
        await query.answer("Current page")
    elif data == "menu_batch_select":
        await menu_batch_select(update, context)
    elif data == "menu_new_link":
        await batch_links.cmd_new_link_batch(update, context)
    elif data == "menu_edit_welcome":
        context.user_data.pop('waiting_for', None)
        await show_edit_welcome_menu(query)

    # --- Broadcast recipient picker ---
    elif data == "menu_broadcast_select":
        context.user_data.pop('waiting_for', None)
        context.user_data['broadcast_excluded'] = set()
        await show_broadcast_user_selection(update, context, page=0, is_edit=True)
    elif data.startswith("bc_page_"):
        page_num = int(data.split("_")[2])
        await show_broadcast_user_selection(update, context, page=page_num, is_edit=True)
    elif data.startswith("bc_toggle_"):
        parts = data.split("_")
        target_id = int(parts[2])
        page_num = int(parts[3])
        excluded = context.user_data.setdefault('broadcast_excluded', set())
        if target_id in excluded:
            excluded.discard(target_id)
        else:
            excluded.add(target_id)
        await show_broadcast_user_selection(update, context, page=page_num, is_edit=True)
    elif data.startswith("bc_noop_"):
        await query.answer("ℹ️ Tap the ✅/🚫 icon to toggle this user.")
    elif data == "bc_continue":
        users = user_registry.get_all_users()
        active_users = [u for u in users if not user_registry.is_blocked(u[0])]
        excluded = context.user_data.get('broadcast_excluded', set())
        targets = [u[0] for u in active_users if u[0] not in excluded]
        if not targets:
            await query.answer("⚠️ No recipients selected!", show_alert=True)
            return
        context.user_data['broadcast_targets'] = targets
        context.user_data['broadcast_delete_timer'] = 0
        await show_broadcast_timer_panel(query, 0, len(targets))
    elif data.startswith("bc_timer_"):
        suffix = data[len("bc_timer_"):]
        targets = context.user_data.get('broadcast_targets', [])
        if suffix == "reset":
            context.user_data['broadcast_delete_timer'] = 0
            await show_broadcast_timer_panel(query, 0, len(targets))
        elif suffix == "go":
            context.user_data['waiting_for'] = 'broadcast_message'
            timer_sec = context.user_data.get('broadcast_delete_timer', 0)
            timer_note = f"⏱️ auto-deleting after {timer_sec}s" if timer_sec > 0 else "no auto-delete"
            keyboard = [[InlineKeyboardButton("« Cancel", callback_data="menu_main")]]
            await query.message.edit_text(
                f"📝 Send the message to broadcast to `{len(targets)}` selected user(s) ({timer_note}).\n\n"
                "You can send plain text (sent exactly as typed, links auto-clickable), or "
                "a single photo, video, audio, voice note, document, animation/GIF, or "
                "sticker — any caption on it will be included too.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            seconds_to_add = int(suffix)
            current_timer = context.user_data.get('broadcast_delete_timer', 0)
            new_timer = current_timer + seconds_to_add
            context.user_data['broadcast_delete_timer'] = new_timer
            await show_broadcast_timer_panel(query, new_timer, len(targets))

    elif data.startswith("confirm_del_user_"):
        target_id = int(data.split("_")[3])
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"del_user_{target_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="menu_edit_welcome")
            ]
        ]
        await query.message.edit_text(
            f"⚠️ **Are you sure you want to completely delete User ID `{target_id}` from the database?**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif data.startswith("confirm_del_btn_"):
        cid = int(data.split("_")[3])
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"del_custom_btn_{cid}"),
                InlineKeyboardButton("❌ Cancel", callback_data="menu_edit_welcome")
            ]
        ]
        await query.message.edit_text(
            "⚠️ **Are you sure you want to delete this custom button?**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "edit_msg_prompt":
        context.user_data['waiting_for'] = 'update_welcome'
        keyboard = [[InlineKeyboardButton("« Back", callback_data="menu_edit_welcome")]]
        await query.message.edit_text(
            "📝 **Send the new Welcome Message** you want to set right now in the chat:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif data == "edit_btn1_prompt":
        context.user_data['waiting_for'] = 'update_btn1'
        keyboard = [[InlineKeyboardButton("« Back", callback_data="menu_edit_welcome")]]
        await query.message.edit_text(
            "📝 **Send the new name for Button 1**:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif data == "edit_btn2_prompt":
        context.user_data['waiting_for'] = 'update_btn2'
        keyboard = [[InlineKeyboardButton("« Back", callback_data="menu_edit_welcome")]]
        await query.message.edit_text(
            "📝 **Send the new name for Button 2**:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif data == "edit_add_custom_btn":
        context.user_data['waiting_for'] = 'custom_btn_name'
        keyboard = [[InlineKeyboardButton("« Back", callback_data="menu_edit_welcome")]]
        await query.message.edit_text(
            "➕ **Step 1/2: Send the Button Name** for your new button:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif data.startswith("del_custom_btn_"):
        cid = int(data.split("_")[3])
        user_registry.delete_custom_button(cid)
        await query.answer("🗑️ Custom button deleted successfully!", show_alert=True)
        await show_edit_welcome_menu(query)

    elif data.startswith("noop_") or data.startswith("block_noop_") or data.startswith("unblock_noop_"):
        await query.answer("ℹ️ User info button.")
    elif data.startswith("del_user_"):
        target_id = int(data.split("_")[2])
        user_registry.delete_user(target_id)
        await query.answer(f"🗑️ User {target_id} deleted from database.", show_alert=True)
        try:
            await show_stats_page(update, context, page=0, is_edit=True)
        except Exception:
            await show_batch_user_selection(update, context, is_edit=True)
    elif data.startswith("block_action_"):
        target_id = int(data.split("_")[2])
        user_registry.block_user(target_id)
        await query.answer(f"🚫 User {target_id} has been blocked.", show_alert=True)
        await show_block_user_selection(update, context, page=0, is_edit=True)
    elif data.startswith("unblock_action_"):
        target_id = int(data.split("_")[2])
        user_registry.unblock_user(target_id)
        await query.answer(f"✅ User {target_id} has been unblocked.", show_alert=True)
        await show_unblock_user_selection(update, context, page=0, is_edit=True)
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
            "4. Use `/broadcast` to open the recipient picker, or `/broadcast <message>` to message everyone."
        )
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_main")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data == "menu_batch_guide":
        text = (
            "📦 **Batch Dispatch Mode Guide**\n\n"
            "• **Start Batch:** Click 'Start Batch' or type `/batch_start`.\n"
            "• **Auto-Delete Timer:** Tap buttons to stack auto-delete time.\n"
            "• **Record & Dispatch:** Automatically groups photos and videos into albums of up to 10 items."
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
        "Every file you send now will be recorded silently. Click 'End & Dispatch Batch' when ready."
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
        "Every file you send now will be recorded silently. Click 'End & Dispatch Batch' when ready.",
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

async def finalize_album_broadcast(context: ContextTypes.DEFAULT_TYPE, admin_chat_id: int):
    # Debounce: wait for more album parts to arrive before sending.
    await asyncio.sleep(1.5)

    items = context.user_data.pop('broadcast_album_items', [])
    context.user_data.pop('broadcast_album_task', None)
    targets = context.user_data.pop('broadcast_targets', [])
    delete_timer = context.user_data.pop('broadcast_delete_timer', 0)
    context.user_data.pop('waiting_for', None)
    context.user_data.pop('broadcast_excluded', None)

    if not items or not targets:
        return

    media_group = []
    for i, m in enumerate(items):
        caption = m.caption if i == 0 else None  # Telegram only shows the first item's caption
        if m.photo:
            media_group.append(InputMediaPhoto(m.photo[-1].file_id, caption=caption))
        elif m.video:
            media_group.append(InputMediaVideo(m.video.file_id, caption=caption))
        elif m.document:
            media_group.append(InputMediaDocument(m.document.file_id, caption=caption))
        elif m.audio:
            media_group.append(InputMediaAudio(m.audio.file_id, caption=caption))

    success_count = 0
    fail_count = 0
    for uid in targets:
        if user_registry.is_blocked(uid):
            continue
        try:
            sent_list = await context.bot.send_media_group(chat_id=uid, media=media_group)
            success_count += 1
            if delete_timer > 0:
                msg_ids = [s.message_id for s in sent_list]
                asyncio.create_task(
                    delete_messages_after_delay(context.bot, uid, msg_ids, delete_timer)
                )
        except Exception as e:
            logger.error(f"Album broadcast to {uid} failed: {e}")
            fail_count += 1

    timer_line = f"• Auto-Delete: `⏱️ {delete_timer}s`\n" if delete_timer > 0 else ""
    await context.bot.send_message(
        chat_id=admin_chat_id,
        text=(
            f"📢 **Broadcast Results** (album, {len(media_group)} item(s))\n\n"
            f"• Sent to: `{success_count}` user(s)\n"
            f"• Failed: `{fail_count}` user(s)\n"
            f"{timer_line}"
        ),
        parse_mode="Markdown"
    )

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
    status_msg = await source_msg.reply_text(f"🚀 Processing 0/{total_items} items for user `{target_id}`...")

    media_batch = []
    admin_msg_ids = []
    failed_items = 0

    for item_data in queue:
        try:
            mtype = item_data.get("type")
            file_id = item_data.get("file_id")
            adm_id = item_data.get("admin_msg_id")
            if adm_id:
                admin_msg_ids.append(adm_id)

            if mtype == "video":
                media_batch.append(InputMediaVideo(media=file_id))
            elif mtype == "photo":
                media_batch.append(InputMediaPhoto(media=file_id))
            elif mtype == "document":
                media_batch.append(InputMediaDocument(media=file_id))
            else:
                failed_items += 1
        except Exception as e:
            logger.error(f"Error parsing queue item: {e}")
            failed_items += 1

    actual_sent_ids = []
    album_items = [item for item in media_batch if isinstance(item, (InputMediaVideo, InputMediaPhoto))]
    document_items = [item for item in media_batch if isinstance(item, InputMediaDocument)]

    processed_count = 0
    for i in range(0, len(album_items), 10):
        chunk = album_items[i:i + 10]
        try:
            sent_messages = await context.bot.send_media_group(
                chat_id=target_id,
                media=chunk
            )
            actual_sent_ids.extend([m.message_id for m in sent_messages])
            processed_count += len(chunk)
            await status_msg.edit_text(f"🚀 Processing... Sent {processed_count}/{total_items} items.")
        except Exception as e:
            logger.warning(f"Media group chunk failed ({e}), falling back to individual sends")
            for item in chunk:
                try:
                    if isinstance(item, InputMediaVideo):
                        sent = await context.bot.send_video(chat_id=target_id, video=item.media)
                    else:
                        sent = await context.bot.send_photo(chat_id=target_id, photo=item.media)
                    actual_sent_ids.append(sent.message_id)
                    processed_count += 1
                    await status_msg.edit_text(f"🚀 Processing... Sent {processed_count}/{total_items} items.")
                except Exception as inner_e:
                    logger.error(f"Fallback item send failed: {inner_e}")
                    failed_items += 1
                await asyncio.sleep(0.2)

        await asyncio.sleep(1.0)

    for doc in document_items:
        try:
            sent = await context.bot.send_document(chat_id=target_id, document=doc.media)
            actual_sent_ids.append(sent.message_id)
            processed_count += 1
            await status_msg.edit_text(f"🚀 Processing... Sent {processed_count}/{total_items} items.")
        except Exception as e:
            logger.error(f"Document send failed: {e}")
            failed_items += 1
        await asyncio.sleep(0.2)

    if delete_timer > 0:
        if actual_sent_ids:
            asyncio.create_task(delete_messages_after_delay(context.bot, target_id, actual_sent_ids, delete_timer))
        if admin_msg_ids:
            asyncio.create_task(delete_messages_after_delay(context.bot, ALLOWED_USER_ID, admin_msg_ids, delete_timer))
        timer_str = f"⏱️ Auto-deleting in {delete_timer}s (User & Admin chat)"
    else:
        timer_str = "No auto-delete"

    result_text = (
        f"✅ **Batch Album Delivery Finished**\n\n"
        f"• Target ID: `{target_id}`\n"
        f"• Successfully Sent: `{len(actual_sent_ids)}` files\n"
        f"• Timer: `{timer_str}`"
    )
    if failed_items > 0:
        result_text += f"\n\n⚠️ Failed/Skipped items: `{failed_items}`"

    await status_msg.edit_text(result_text, parse_mode="Markdown")

@restricted
async def cmd_batch_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await execute_batch_end(update, context, is_callback=False)

@restricted
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    bot_username = context.bot_data.get('bot_username')
    if await batch_links.try_capture_custom_timer(update, context):
        return
    if await batch_links.try_capture_for_link_batch(update, context, bot_username, user_registry.db_path):
        return

    waiting_state = context.user_data.get('waiting_for')
    if waiting_state:
        text_input = message.text
        if waiting_state == 'update_welcome':
            user_registry.set_setting("welcome_text", text_input)
            context.user_data.pop('waiting_for', None)
            await message.reply_text(f"✅ Welcome message successfully updated to:\n`{text_input}`", parse_mode="Markdown")
            return
        elif waiting_state == 'update_btn1':
            user_registry.set_setting("btn_pkg", text_input)
            context.user_data.pop('waiting_for', None)
            await message.reply_text(f"✅ Button 1 name updated to: `{text_input}`", parse_mode="Markdown")
            return
        elif waiting_state == 'update_btn2':
            user_registry.set_setting("btn_contact", text_input)
            context.user_data.pop('waiting_for', None)
            await message.reply_text(f"✅ Button 2 name updated to: `{text_input}`", parse_mode="Markdown")
            return
        elif waiting_state == 'custom_btn_name':
            context.user_data['temp_custom_btn_name'] = text_input
            context.user_data['waiting_for'] = 'custom_btn_url'
            keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="menu_edit_welcome")]]
            await message.reply_text(
                f"🔗 **Step 2/2: Send the URL/Link** for button `{text_input}`:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return
        elif waiting_state == 'custom_btn_url':
            bname = context.user_data.pop('temp_custom_btn_name', 'Button')
            burl = text_input
            context.user_data.pop('waiting_for', None)
            user_registry.add_custom_button(bname, burl)
            await message.reply_text(
                f"✅ **Custom Button Added Successfully!**\n• Name: `{bname}`\n• Link: `{burl}`",
                parse_mode="Markdown"
            )
            return
        elif waiting_state == 'broadcast_message':
            targets = context.user_data.get('broadcast_targets', [])
            if not targets:
                context.user_data.pop('waiting_for', None)
                context.user_data.pop('broadcast_targets', None)
                context.user_data.pop('broadcast_excluded', None)
                context.user_data.pop('broadcast_delete_timer', None)
                await message.reply_text("⚠️ No recipients were selected. Nothing was sent.")
                return

            if message.media_group_id:
                # Part of an album: buffer it and (re)start the debounce timer.
                # Telegram sends each album item as a separate update, so we wait
                # briefly to collect them all before sending as one media group.
                context.user_data.setdefault('broadcast_album_items', []).append(message)
                old_task = context.user_data.get('broadcast_album_task')
                if old_task and not old_task.done():
                    old_task.cancel()
                context.user_data['broadcast_album_task'] = asyncio.create_task(
                    finalize_album_broadcast(context, message.chat_id)
                )
                return

            delete_timer = context.user_data.get('broadcast_delete_timer', 0)
            context.user_data.pop('waiting_for', None)
            context.user_data.pop('broadcast_targets', None)
            context.user_data.pop('broadcast_excluded', None)
            context.user_data.pop('broadcast_delete_timer', None)

            is_plain_text = bool(message.text) and not (
                message.photo or message.video or message.document or
                message.audio or message.voice or message.animation or
                message.video_note or message.sticker
            )

            success_count = 0
            fail_count = 0
            for uid in targets:
                if user_registry.is_blocked(uid):
                    continue
                try:
                    if is_plain_text:
                        # Plain text (no parse_mode): sends exactly what was typed,
                        # underscores/asterisks aren't treated as formatting, and
                        # Telegram still auto-links any URLs in the text.
                        sent = await context.bot.send_message(
                            chat_id=uid,
                            text=f"📢 Announcement:\n\n{message.text}"
                        )
                    else:
                        # copy_message handles photo/video/audio/document/voice/
                        # animation/sticker/etc. and preserves the original caption,
                        # without leaving a "Forwarded from" tag on the recipient's side.
                        sent = await context.bot.copy_message(
                            chat_id=uid,
                            from_chat_id=message.chat_id,
                            message_id=message.message_id
                        )
                    success_count += 1
                    if delete_timer > 0:
                        asyncio.create_task(
                            delete_messages_after_delay(context.bot, uid, [sent.message_id], delete_timer)
                        )
                except Exception as e:
                    logger.error(f"Broadcast to {uid} failed: {e}")
                    fail_count += 1

            timer_line = f"• Auto-Delete: `⏱️ {delete_timer}s`\n" if delete_timer > 0 else ""
            await message.reply_text(
                f"📢 **Broadcast Results**\n\n"
                f"• Sent to: `{success_count}` user(s)\n"
                f"• Failed: `{fail_count}` user(s)\n"
                f"{timer_line}",
                parse_mode="Markdown"
            )
            return

    if context.user_data.get('batch_active'):
        if message.video:
            context.user_data['batch_queue'].append({
                "type": "video", 
                "file_id": message.video.file_id, 
                "admin_msg_id": message.message_id
            })
        elif message.photo:
            context.user_data['batch_queue'].append({
                "type": "photo", 
                "file_id": message.photo[-1].file_id, 
                "admin_msg_id": message.message_id
            })
        elif message.document:
            context.user_data['batch_queue'].append({
                "type": "document", 
                "file_id": message.document.file_id, 
                "admin_msg_id": message.message_id
            })
        
        try:
            await message.set_reaction("👀")
        except Exception:
            pass
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

async def cmd_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id == ALLOWED_USER_ID:
        return

    if user_registry.is_blocked(user.id):
        await update.message.reply_text("❌ You have been blocked from using this bot.")
        return

    user_registry.add_user(user.id, user.username, user.first_name)
    args = context.args

    if args and args[0].startswith(batch_links.BATCH_LINK_PREFIX):
        await batch_links.deliver_batch_to_user(update, context, args[0], user_registry.db_path)
        return

    if args and args[0] == "2":
        welcome_text = user_registry.get_setting("welcome_text")
        btn_pkg_text = user_registry.get_setting("btn_pkg")
        btn_contact_text = user_registry.get_setting("btn_contact")
        custom_buttons = user_registry.get_custom_buttons()

        keyboard = [
            [InlineKeyboardButton(btn_pkg_text, callback_data="user_buy_package")],
            [InlineKeyboardButton(btn_contact_text, callback_data="user_contact_admin")]
        ]
        for cid, btext, burl in custom_buttons:
            keyboard.append([InlineKeyboardButton(btext, url=burl)])

        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text("සාදරයෙන් පිලිගන්නවා 💕, ඇඩ්මින් ට එවන්න ඔනෙ මැසෙජ් එක ටයිප් කරලා එවන්න,")

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

    user_registry.add_user(user.id, user.username, user.first_name)

    clickable_name = f"[{user.first_name}](tg://user?id={user.id})"
    header = f"📩 **msg/ From:** {clickable_name} (`{user.id}`)\n\n"

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

async def handle_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "user_start_menu":
        welcome_text = user_registry.get_setting("welcome_text")
        btn_pkg_text = user_registry.get_setting("btn_pkg")
        btn_contact_text = user_registry.get_setting("btn_contact")
        custom_buttons = user_registry.get_custom_buttons()

        keyboard = [
            [InlineKeyboardButton(btn_pkg_text, callback_data="user_buy_package")],
            [InlineKeyboardButton(btn_contact_text, callback_data="user_contact_admin")]
        ]
        for cid, btext, burl in custom_buttons:
            keyboard.append([InlineKeyboardButton(btext, url=burl)])

        await query.message.edit_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "user_buy_package":
        keyboard = [
            [InlineKeyboardButton("5$ Package", callback_data="pkg_5")],
            [InlineKeyboardButton("8$ Package", callback_data="pkg_8")],
            [InlineKeyboardButton("13$ Package", callback_data="pkg_13")],
            [InlineKeyboardButton("« Back", callback_data="user_start_menu")]
        ]
        await query.message.edit_text("What package are you buying?", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["pkg_5", "pkg_8", "pkg_13"]:
        amount = data.split("_")[1]
        context.user_data['selected_pkg'] = amount
        keyboard = [
            [InlineKeyboardButton("Binance Payment", callback_data="pay_binance")],
            [InlineKeyboardButton("Star Payment", callback_data="pay_star")],
            [InlineKeyboardButton("« Back", callback_data="user_buy_package")]
        ]
        await query.message.edit_text("Choose your payment method:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ["pay_binance", "pay_star"]:
        method = "binance payment" if data == "pay_binance" else "star payment"
        pkg_amount = context.user_data.get('selected_pkg', 'unknown')
        
        clickable_name = f"[{user.first_name}](tg://user?id={user.id})"
        admin_text = f"📩 **Purchase Request** from {clickable_name} (`{user.id}`)\n\nAdmin i need to buy {pkg_amount}$ package with {method}"
        
        sent_to_admin = await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=admin_text, parse_mode="Markdown")
        user_registry.map_admin_message(sent_to_admin.message_id, user.id)

        await query.message.reply_text("✅ Your request has been sent to the admin! They will contact you shortly.")

    elif data == "user_contact_admin":
        clickable_name = f"[{user.first_name}](tg://user?id={user.id})"
        admin_text = f"📩 **Contact Request** from {clickable_name} (`{user.id}`)\n\nAdmin i need to know details package"
        
        sent_to_admin = await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=admin_text, parse_mode="Markdown")
        user_registry.map_admin_message(sent_to_admin.message_id, user.id)

        await query.message.reply_text("✅ Your message has been sent to the admin!")

async def handle_preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "preview_start_menu":
        welcome_text = user_registry.get_setting("welcome_text")
        btn_pkg_text = user_registry.get_setting("btn_pkg")
        btn_contact_text = user_registry.get_setting("btn_contact")
        custom_buttons = user_registry.get_custom_buttons()

        keyboard = [
            [InlineKeyboardButton(btn_pkg_text, callback_data="preview_buy_package")],
            [InlineKeyboardButton(btn_contact_text, callback_data="preview_contact_admin")]
        ]
        for cid, btext, burl in custom_buttons:
            keyboard.append([InlineKeyboardButton(btext, url=burl)])
        keyboard.append([InlineKeyboardButton("« Back to Admin Menu", callback_data="menu_main")])

        await query.message.edit_text(
            f"👀 **[Admin Preview of User Start Menu]**\n\n{welcome_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "preview_buy_package":
        keyboard = [
            [InlineKeyboardButton("5$ Package", callback_data="preview_pkg_5")],
            [InlineKeyboardButton("8$ Package", callback_data="preview_pkg_8")],
            [InlineKeyboardButton("13$ Package", callback_data="preview_pkg_13")],
            [InlineKeyboardButton("« Back", callback_data="preview_start_menu")]
        ]
        await query.message.edit_text(
            "👀 **[Preview]** What package are you buying?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data in ["preview_pkg_5", "preview_pkg_8", "preview_pkg_13"]:
        amount = data.split("_")[2]
        keyboard = [
            [InlineKeyboardButton("Binance Payment", callback_data=f"preview_pay_binance_{amount}")],
            [InlineKeyboardButton("Star Payment", callback_data=f"preview_pay_star_{amount}")],
            [InlineKeyboardButton("« Back", callback_data="preview_buy_package")]
        ]
        await query.message.edit_text(
            "👀 **[Preview]** Choose your payment method:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("preview_pay_"):
        parts = data.split("_")
        method_label = "Binance Payment" if parts[2] == "binance" else "Star Payment"
        amount = parts[3]
        keyboard = [[InlineKeyboardButton("« Back to Admin Menu", callback_data="menu_main")]]
        await query.message.edit_text(
            f"👀 **[Preview only — nothing was sent]**\n\n"
            f"This is exactly what a real buyer would trigger: a message to you reading\n"
            f"`admin i need to buy {amount}$ package with {method_label.lower()}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "preview_contact_admin":
        keyboard = [[InlineKeyboardButton("« Back to Admin Menu", callback_data="menu_main")]]
        await query.message.edit_text(
            "👀 **[Preview only — nothing was sent]**\n\n"
            "This is exactly what a real user would trigger: a message to you reading\n"
            "`admin i need to know details package`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("start_2", cmd_start_2, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("stats", cmd_stats, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("block", cmd_block, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("unblock", cmd_unblock, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("batch_start", cmd_batch_start, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("batch_end", cmd_batch_end, filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler("new_link", restricted(batch_links.cmd_new_link_batch), filters.User(ALLOWED_USER_ID)))
    application.add_handler(CommandHandler(
        "delete_batch",
        restricted(lambda u, c: batch_links.cmd_delete_batch(u, c, user_registry.db_path)),
        filters.User(ALLOWED_USER_ID)
    ))

    application.add_handler(CommandHandler("start", cmd_user_start, ~filters.User(ALLOWED_USER_ID)))

    application.add_handler(CallbackQueryHandler(
        menu_callback_handler,
        pattern="^(menu_|batch_choose_|batch_timer_|del_user_|confirm_del_|block_action_|unblock_action_|noop_|block_noop_|unblock_noop_|edit_|del_custom_btn_|stats_page_|block_page_|unblock_page_|noop_page|bc_)"
    ))
    application.add_handler(CallbackQueryHandler(batch_links.handle_link_timer_choice, pattern="^linktimer_"))
    application.add_handler(CallbackQueryHandler(handle_preview_callback, pattern="^preview_"))
    application.add_handler(CallbackQueryHandler(handle_user_callback, pattern="^(user_|pkg_|pay_)"))

    application.add_handler(MessageHandler(filters.User(ALLOWED_USER_ID) & ~filters.COMMAND, handle_admin_message))
    application.add_handler(MessageHandler(~filters.User(ALLOWED_USER_ID), handle_user_message))

    logger.info("File Share Bot started successfully with enhanced user-friendliness features.")
    await application.initialize()
    application.bot_data['bot_username'] = application.bot.username
    await application.start()

    await application.bot.set_my_commands(
        [BotCommand("start", "Start the bot")],
        scope=BotCommandScopeDefault()
    )

    await application.bot.set_my_commands([
        BotCommand("start", "Open Admin Control Panel"),
        BotCommand("start_2", "Preview User Welcome Panel"),
        BotCommand("stats", "View Active Users & Blocked Count"),
        BotCommand("block", "Block a user (reply or ID)"),
        BotCommand("unblock", "Unblock a user (reply or ID)"),
        BotCommand("broadcast", "Send announcement (recipient picker)"),
        BotCommand("batch_start", "Start a File Batch"),
        BotCommand("batch_end", "Dispatch Active Batch")
    ], scope=BotCommandScopeChat(chat_id=ALLOWED_USER_ID))

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