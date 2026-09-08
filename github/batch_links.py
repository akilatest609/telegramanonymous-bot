"""
Deep-link video batch sharing, with a configurable auto-delete timer.

Flow:
    1. Admin taps "Create Share Link" (or /new_link).
    2. Bot asks how long the delivered videos should stay in the user's
       chat before self-destructing (1h / 6h / 24h / 3 days / never).
    3. Admin sends a single video/photo/document or a whole album.
    4. Bot saves it as one batch (with the chosen timer) and replies with
       a shareable deep link: https://t.me/<BotUsername>?start=batch_<id>
    5. Any user opening that link sees "Preparing your video batch...",
       then receives the album. If a timer was set, those messages are
       auto-deleted from the user's chat after it elapses.
    6. Admin can kill the link itself anytime with /delete_batch <id>
       (stops it from being opened again; doesn't recall copies already
       delivered whose timer hasn't run out yet).
"""

import asyncio
import json
import logging
import sqlite3
import uuid

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaVideo,
    InputMediaPhoto,
    InputMediaDocument,
)
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

BATCH_LINK_PREFIX = "batch_"
ALBUM_DEBOUNCE_SECONDS = 1.5

# (label, seconds) — 0 means never auto-delete
TIMER_OPTIONS = [
    ("1 Hour", 3600),
    ("6 Hours", 21600),
    ("24 Hours", 86400),
    ("3 Days", 259200),
    ("♾️ Never delete", 0),
]


def _format_duration(seconds: int) -> str:
    """Human-readable label for any duration, including custom ones."""
    if not seconds or seconds <= 0:
        return "Never"
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days}d"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours}h"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes}m"
    return f"{seconds}s"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def init_batch_tables(db_path: str) -> None:
    """Call once at startup, using the same db_path as your UserRegistry."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS link_batches (
                batch_id TEXT PRIMARY KEY,
                items TEXT NOT NULL,
                delete_after_seconds INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS link_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
        """)
        conn.commit()


def record_delivery(db_path: str, batch_id: str, chat_id: int, message_ids: list) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO link_deliveries (batch_id, chat_id, message_id) VALUES (?, ?, ?)",
            [(batch_id, chat_id, mid) for mid in message_ids],
        )
        conn.commit()


def pop_deliveries(db_path: str, batch_id: str = None, chat_id: int = None, message_ids: list = None):
    """Fetch matching (chat_id, message_id) rows and remove them from the table.
    Pass batch_id alone to grab every delivery for that batch (used on revoke).
    Pass chat_id + message_ids to grab one specific delivery (used after a
    normal auto-delete fires, so revoke later won't try to re-delete it)."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        if batch_id is not None and chat_id is None:
            cursor.execute("SELECT chat_id, message_id FROM link_deliveries WHERE batch_id = ?", (batch_id,))
            rows = cursor.fetchall()
            conn.execute("DELETE FROM link_deliveries WHERE batch_id = ?", (batch_id,))
        else:
            placeholders = ",".join("?" for _ in message_ids)
            cursor.execute(
                f"SELECT chat_id, message_id FROM link_deliveries WHERE chat_id = ? AND message_id IN ({placeholders})",
                (chat_id, *message_ids),
            )
            rows = cursor.fetchall()
            conn.execute(
                f"DELETE FROM link_deliveries WHERE chat_id = ? AND message_id IN ({placeholders})",
                (chat_id, *message_ids),
            )
        conn.commit()
        return rows


def _new_batch_id() -> str:
    return uuid.uuid4().hex[:10]


def save_batch(db_path: str, items: list, delete_after_seconds: int = 0) -> str:
    batch_id = _new_batch_id()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO link_batches (batch_id, items, delete_after_seconds) VALUES (?, ?, ?)",
            (batch_id, json.dumps(items), delete_after_seconds),
        )
        conn.commit()
    return batch_id


def load_batch(db_path: str, batch_id: str):
    """Returns (items, delete_after_seconds) or (None, None) if not found."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT items, delete_after_seconds FROM link_batches WHERE batch_id = ?",
            (batch_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None, None
        return json.loads(row[0]), row[1]


def delete_batch(db_path: str, batch_id: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM link_batches WHERE batch_id = ?", (batch_id,))
        conn.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Admin flow: choose timer, then ingest media
# ---------------------------------------------------------------------------

def _timer_keyboard():
    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"linktimer_{seconds}")]
        for label, seconds in TIMER_OPTIONS
    ]
    keyboard.append([InlineKeyboardButton("✏️ Custom time", callback_data="linktimer_custom")])
    keyboard.append([InlineKeyboardButton("« Cancel", callback_data="linktimer_cancel")])
    return InlineKeyboardMarkup(keyboard)


async def cmd_new_link_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wrap with your existing @restricted decorator when registering.
    Works from both a /new_link command and a menu button callback."""
    context.bot_data.pop('link_capture_armed', None)
    context.bot_data.pop('link_capture_items', None)
    old_task = context.bot_data.pop('link_capture_task', None)
    if old_task and not old_task.done():
        old_task.cancel()

    text = (
        "🔗 **Create Share Link**\n\n"
        "⏱ How long should the videos stay in the user's chat after they "
        "open the link, before auto-deleting?"
    )
    markup = _timer_keyboard()

    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def handle_link_timer_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register with CallbackQueryHandler(pattern='^linktimer_')."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "linktimer_cancel":
        context.bot_data.pop('link_capture_armed', None)
        context.bot_data.pop('link_capture_items', None)
        await query.message.edit_text("❌ Cancelled — no link was created.")
        return

    if data == "linktimer_custom":
        context.bot_data['awaiting_custom_timer'] = True
        await query.message.edit_text(
            "✏️ **Custom Auto-Delete Time**\n\n"
            "Type a number of minutes (e.g. `90` for 1.5 hours, `10080` for 7 days).\n"
            "Type `0` for never auto-delete.",
            parse_mode="Markdown"
        )
        return

    seconds = int(data.split("_", 1)[1])
    context.bot_data['link_capture_armed'] = True
    context.bot_data['link_capture_timer'] = seconds
    context.bot_data.pop('link_capture_items', None)

    await query.message.edit_text(
        f"✅ Auto-delete set to: **{_format_duration(seconds)}**\n\n"
        "Now send (or forward) a single video/photo/document, or an entire "
        "album. I'll capture it and hand you back a shareable link.",
        parse_mode="Markdown"
    )


async def try_capture_custom_timer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Call this at the top of your admin message handler, BEFORE
    try_capture_for_link_batch. Returns True if this text message was the
    admin's custom-minutes reply (caller should `return` in that case)."""
    if not context.bot_data.get('awaiting_custom_timer'):
        return False

    message = update.message
    text = (message.text or "").strip()

    if not text.isdigit():
        await message.reply_text("⚠️ Please send a plain number of minutes (e.g. `90`), or `0` for never.", parse_mode="Markdown")
        return True

    minutes = int(text)
    seconds = minutes * 60
    context.bot_data['awaiting_custom_timer'] = False
    context.bot_data['link_capture_armed'] = True
    context.bot_data['link_capture_timer'] = seconds
    context.bot_data.pop('link_capture_items', None)

    await message.reply_text(
        f"✅ Auto-delete set to: **{_format_duration(seconds)}**\n\n"
        "Now send (or forward) a single video/photo/document, or an entire "
        "album. I'll capture it and hand you back a shareable link.",
        parse_mode="Markdown"
    )
    return True


def _extract_media_item(message):
    if message.video:
        return {"type": "video", "file_id": message.video.file_id}
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id}
    if message.document:
        return {"type": "document", "file_id": message.document.file_id}
    return None


async def try_capture_for_link_batch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    bot_username: str,
    db_path: str,
) -> bool:
    """Call this FIRST inside your existing admin message handler.
    Returns True if the message was consumed by link-batch capture —
    your caller should `return` immediately in that case."""
    if not context.bot_data.get('link_capture_armed'):
        return False

    message = update.message
    item = _extract_media_item(message)
    if not item:
        return False

    context.bot_data.setdefault('link_capture_items', []).append(item)

    old_task = context.bot_data.get('link_capture_task')
    if old_task and not old_task.done():
        old_task.cancel()
    context.bot_data['link_capture_task'] = asyncio.create_task(
        _finalize_link_batch(context, message.chat_id, bot_username, db_path)
    )

    try:
        await message.set_reaction("👀")
    except Exception:
        pass

    return True


async def _finalize_link_batch(
    context: ContextTypes.DEFAULT_TYPE,
    admin_chat_id: int,
    bot_username: str,
    db_path: str,
):
    await asyncio.sleep(ALBUM_DEBOUNCE_SECONDS)

    items = context.bot_data.pop('link_capture_items', [])
    context.bot_data.pop('link_capture_task', None)
    context.bot_data['link_capture_armed'] = False
    delete_after_seconds = context.bot_data.pop('link_capture_timer', 0)

    if not items:
        return

    batch_id = save_batch(db_path, items, delete_after_seconds)
    deep_link = f"https://t.me/{bot_username}?start={BATCH_LINK_PREFIX}{batch_id}"

    await context.bot.send_message(
        chat_id=admin_chat_id,
        text=(
            f"✅ **Batch Ready** (`{len(items)}` item(s))\n\n"
            f"🔗 Shareable link:\n`{deep_link}`\n\n"
            f"⏱ Delivered copies auto-delete after: **{_format_duration(delete_after_seconds)}**\n"
            f"Batch ID: `{batch_id}`\n"
            f"Revoke the link anytime with `/delete_batch {batch_id}`"
        ),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# Admin revocation
# ---------------------------------------------------------------------------

async def cmd_delete_batch(update: Update, context: ContextTypes.DEFAULT_TYPE, db_path: str):
    """Wrap with @restricted when registering."""
    if not context.args:
        await update.message.reply_text(
            "Usage: `/delete_batch <batch_id>`\n"
            "(with or without the `batch_` prefix)",
            parse_mode="Markdown"
        )
        return

    batch_id = context.args[0].strip()
    if batch_id.startswith(BATCH_LINK_PREFIX):
        batch_id = batch_id[len(BATCH_LINK_PREFIX):]

    deliveries = pop_deliveries(db_path, batch_id=batch_id)

    removed_count = 0
    failed_count = 0
    for chat_id, message_id in deliveries:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            removed_count += 1
        except Exception as e:
            logger.error(f"Revoke: couldn't delete msg {message_id} in {chat_id}: {e}")
            failed_count += 1

    if delete_batch(db_path, batch_id):
        extra = ""
        if deliveries:
            extra = f"\n🧹 Removed `{removed_count}` already-delivered copy/copies from user chats."
            if failed_count:
                extra += f" (`{failed_count}` couldn't be removed — likely too old for Telegram to delete, or the chat was blocked.)"
        await update.message.reply_text(
            f"🗑️ Batch `{batch_id}` deleted — its link is now dead.{extra}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"⚠️ No batch found with ID `{batch_id}`.",
            parse_mode="Markdown"
        )


# ---------------------------------------------------------------------------
# User-facing delivery + self-destruct
# ---------------------------------------------------------------------------

async def _delete_messages_after_delay(db_path, batch_id, bot, chat_id, message_ids, delay_seconds):
    await asyncio.sleep(delay_seconds)
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.error(f"Link-batch auto-delete failed for msg {msg_id} in {chat_id}: {e}")
    # Clean up the tracking rows now that these are gone, so a later
    # /delete_batch on this batch doesn't try to re-delete them.
    pop_deliveries(db_path, chat_id=chat_id, message_ids=message_ids)


async def deliver_batch_to_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    payload: str,
    db_path: str,
):
    """Call from your /start handler when args[0].startswith(BATCH_LINK_PREFIX)."""
    message = update.message
    batch_id = payload[len(BATCH_LINK_PREFIX):]

    status_msg = await message.reply_text("⏳ Preparing your video batch...")

    items, delete_after_seconds = load_batch(db_path, batch_id)
    if not items:
        await status_msg.edit_text(
            "❌ This link is invalid, expired, or has already been removed.\n"
            "Please contact the admin if you think this is a mistake."
        )
        return

    media_items = []
    for item in items:
        if item["type"] == "video":
            media_items.append(InputMediaVideo(item["file_id"]))
        elif item["type"] == "photo":
            media_items.append(InputMediaPhoto(item["file_id"]))
        elif item["type"] == "document":
            media_items.append(InputMediaDocument(item["file_id"]))

    if not media_items:
        await status_msg.edit_text("❌ This batch has no deliverable items. Contact the admin.")
        return

    # Telegram allows at most 10 items per send_media_group call, so batches
    # bigger than that are delivered as multiple back-to-back albums.
    CHUNK_SIZE = 10
    delivered_ids = []
    total = len(media_items)

    try:
        for i in range(0, total, CHUNK_SIZE):
            chunk = media_items[i:i + CHUNK_SIZE]
            if total > CHUNK_SIZE:
                await status_msg.edit_text(f"⏳ Preparing your video batch... ({i}/{total})")
            sent_messages = await context.bot.send_media_group(chat_id=message.chat_id, media=chunk)
            delivered_ids.extend(m.message_id for m in sent_messages)
            if i + CHUNK_SIZE < total:
                await asyncio.sleep(1.0)  # brief pause between albums, avoids flooding

        await status_msg.delete()

        if delete_after_seconds and delete_after_seconds > 0:
            notice = await context.bot.send_message(
                chat_id=message.chat_id,
                text=f"⏱️ These videos will auto-delete from this chat in {_format_duration(delete_after_seconds)}."
            )
            delivered_ids.append(notice.message_id)  # notice disappears together with the videos

            record_delivery(db_path, batch_id, message.chat_id, delivered_ids)
            asyncio.create_task(
                _delete_messages_after_delay(db_path, batch_id, context.bot, message.chat_id, delivered_ids, delete_after_seconds)
            )
        else:
            record_delivery(db_path, batch_id, message.chat_id, delivered_ids)
    except Exception as e:
        logger.error(f"Failed to deliver batch {batch_id} to {message.chat_id}: {e}")
        await status_msg.edit_text(
            "⚠️ Something went wrong delivering your files. Please contact the admin."
        )
