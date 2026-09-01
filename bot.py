import asyncio
import html
import logging
import random
import string

from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = "8951474107:AAHLBI_7fIOjw70mNg5_qcPu2v9UGyCTk6k"
ADMIN_CHAT_ID = 8536087082  # Your personal Telegram ID

# In-memory data stores
active_sessions: dict[int, str] = {}  # userId -> alias
message_mapping: dict[int, int] = {}  # adminMessageId -> userId
blocked_users: set[int] = set()       # set of blocked userIds
message_lock = asyncio.Lock()         # Lock to prevent message interleaving


def generate_alias(user_id: int) -> str:
    if user_id in active_sessions:
        return active_sessions[user_id]

    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    alias = f"User_{suffix}"
    active_sessions[user_id] = alias
    return alias


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user.id == ADMIN_CHAT_ID:
        await update.message.reply_text(
            "👋 Welcome back, Admin!\n\n"
            "**Admin Commands:**\n"
            "• Reply to any message to reply to a user.\n"
            "• `/stats` - View bot statistics.\n"
            "• `/block` - Reply to a message or type `/block <user_id>` to block a user.\n"
            "• `/unblock` - Reply to a message or type `/unblock <user_id>` to unblock.\n"
            "• `/broadcast <message>` - Send an announcement to all users.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "වැල පුරේ බොටා වෙත සාදරයෙන් පිලිගන්නවා......"
            "ඔයාට අඩ්මින්ට එවන්න ඔනේ මසෙජ් එකක් තියෙනවා නම් ටයිප් කරලා එවන්න😘💕"
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    total_users = len(active_sessions)
    await update.message.reply_text(
        f"📊 **Bot Statistics**\n\n"
        f"• Active Anonymous Users: `{total_users}`\n"
        f"• Blocked Users: `{len(blocked_users)}`",
        parse_mode="Markdown"
    )


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    target_user_id = None
    if update.message.reply_to_message:
        target_user_id = message_mapping.get(update.message.reply_to_message.message_id)
    elif context.args:
        try:
            target_user_id = int(context.args[0])
        except ValueError:
            pass

    if not target_user_id:
        await update.message.reply_text("⚠️ Reply to a user's message or provide a User ID: `/block <user_id>`", parse_mode="Markdown")
        return

    blocked_users.add(target_user_id)
    await update.message.reply_text(f"🚫 User `{target_user_id}` has been blocked successfully.", parse_mode="Markdown")


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    target_user_id = None
    if update.message.reply_to_message:
        target_user_id = message_mapping.get(update.message.reply_to_message.message_id)
    elif context.args:
        try:
            target_user_id = int(context.args[0])
        except ValueError:
            pass

    if not target_user_id:
        await update.message.reply_text("⚠️ Reply to a user's message or provide a User ID: `/unblock <user_id>`", parse_mode="Markdown")
        return

    if target_user_id in blocked_users:
        blocked_users.remove(target_user_id)
        await update.message.reply_text(f"✅ User `{target_user_id}` has been unblocked.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ User `{target_user_id}` is not currently blocked.")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/broadcast <your announcement message>`", parse_mode="Markdown")
        return

    broadcast_text = " ".join(context.args)
    success_count = 0
    fail_count = 0

    for user_id in list(active_sessions.keys()):
        if user_id in blocked_users:
            continue
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 **Announcement:**\n\n{broadcast_text}",
                parse_mode="Markdown"
            )
            success_count += 1
        except Exception:
            fail_count += 1

    await update.message.reply_text(
        f"📢 **Broadcast Results**\n\n"
        f"• Successfully delivered: `{success_count}`\n"
        f"• Failed (user blocked bot): `{fail_count}`",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.from_user:
        return

    user = message.from_user
    user_id = user.id
    chat_type = message.chat.type

    # Ignore non-private chats unless it's a message in the admin chat
    if chat_type != "private" and message.chat.id != ADMIN_CHAT_ID:
        return

    # Check if user is blocked
    if user_id in blocked_users:
        if chat_type == "private":
            await message.reply_text("❌ You have been blocked from using this bot.")
        return

    # Admin replying to a message
    if message.chat.id == ADMIN_CHAT_ID and message.reply_to_message:
        target_user_id = message_mapping.get(message.reply_to_message.message_id)
        if target_user_id:
            try:
                admin_text = message.text or message.caption or ""
                badge_text = f"🛡️ **Admin:**\n{admin_text}" if admin_text else "🛡️ **Admin:**"

                if message.text:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=badge_text,
                        parse_mode="Markdown"
                    )
                else:
                    await context.bot.copy_message(
                        chat_id=target_user_id,
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                    )
                    if admin_text:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text=badge_text,
                            parse_mode="Markdown"
                        )
            except Exception as e:
                logging.warning("Failed to deliver reply to user %s: %s", target_user_id, e)
        else:
            await message.reply_text("⚠️ Can't find the original sender for this message.")
        return

    # Regular user message in a private chat
    if chat_type == "private" and user_id != ADMIN_CHAT_ID:
        alias = generate_alias(user_id)
        full_name = user.full_name or "Unknown"
        display_name = f"@{user.username}" if user.username else full_name

        async with message_lock:
            if message.text:
                # Use HTML parse mode to safely handle special characters in usernames/text
                safe_name = html.escape(display_name)
                safe_alias = html.escape(alias)
                safe_text = html.escape(message.text)
                
                combined_text = f"💬 <b>{safe_name}</b> - ({safe_alias}):\n{safe_text}"
                
                sent_msg = await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID, 
                    text=combined_text, 
                    parse_mode="HTML"
                )
                message_mapping[sent_msg.message_id] = user_id
            else:
                # If it's media, send safe HTML header first then copy media
                safe_name = html.escape(display_name)
                safe_alias = html.escape(alias)
                header_text = f"💬 <b>{safe_name}</b> - ({safe_alias})"
                
                header_msg = await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID, 
                    text=header_text, 
                    parse_mode="HTML"
                )
                message_mapping[header_msg.message_id] = user_id

                copied_msg = await context.bot.copy_message(
                    chat_id=ADMIN_CHAT_ID,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                message_mapping[copied_msg.message_id] = user_id

        # Send confirmation message to user, then delete it after 1 second
        confirm_msg = await message.reply_text("✅ Sent to admin.")
        
        async def delete_confirmation():
            await asyncio.sleep(1)
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=confirm_msg.message_id)
            except Exception:
                pass

        asyncio.create_task(delete_confirmation())


async def post_init(application: Application) -> None:
    await application.bot.delete_webhook(drop_pending_updates=True)

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Start the anonymous messaging bot")
        ],
        scope=BotCommandScopeDefault()
    )

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Admin dashboard & info"),
            BotCommand("stats", "View active users & blocked count"),
            BotCommand("block", "Block a user (reply or ID)"),
            BotCommand("unblock", "Unblock a user (reply or ID)"),
            BotCommand("broadcast", "Send announcement to all users"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_CHAT_ID)
    )

    logging.info("Webhook cleared and command menus registered, starting polling...")


def main() -> None:
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)

    application = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("block", block_command))
    application.add_handler(CommandHandler("unblock", unblock_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(MessageHandler(filters.ALL, handle_message))

    print("SecBox Bot running with HTML-safe entity escaping...")
    application.run_polling()


if __name__ == "__main__":
    main()