import logging
import os
import re
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
from datetime import timedelta

from telegram import ChatPermissions, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("mehni-guard")

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set BOT_TOKEN in your hosting environment.")

# This starter version keeps counters in memory; they reset when the bot restarts.
WARNINGS = defaultdict(int)  # (chat_id, user_id) -> count
RECENT = defaultdict(lambda: deque(maxlen=10))  # (chat_id, user_id) -> [(time, text)]
LINK_FILTER = defaultdict(lambda: True)
URL_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
GROUP_TYPES = {"group", "supergroup"}


def in_group(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in GROUP_TYPES)


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        log.exception("Admin check failed")
        return False


async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not in_group(update) or not update.effective_message or not update.effective_user:
        if update.effective_message:
            await update.effective_message.reply_text("هذا الأمر يُستخدم داخل الكروب فقط.")
        return False
    if not await is_admin(update, context, update.effective_user.id):
        await update.effective_message.reply_text("عذراً، هذا الأمر للمشرفين فقط.")
        return False
    return True


async def bot_has_restrict_rights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat.id, me.id)
    return member.status == ChatMemberStatus.OWNER or (
        member.status == ChatMemberStatus.ADMINISTRATOR
        and bool(getattr(member, "can_restrict_members", False))
    )


def replied_user(update: Update):
    msg = update.effective_message
    if msg and msg.reply_to_message:
        user = msg.reply_to_message.from_user
        if user and not user.is_bot:
            return user
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "أهلاً بيك، أني حارس المهني 🛡️\n"
        "بوت حماية وإدارة كروب حماية كروب المهني.\n"
        "اكتب /help حتى تشوف الأوامر."
    )


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome members when Telegram reports that they joined the group."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type not in GROUP_TYPES or not msg.new_chat_members:
        return

    for member in msg.new_chat_members:
        if member.is_bot:
            continue
        name = member.mention_html()
        await msg.reply_text(
            f"🎉 هلا وغلا {name}، نورت كروب المهني!\n\n"
            "🛡️ نتمنى إلك وقت ممتع ويانه.\n"
            "📌 اقرا قوانين الكروب بكتابة /rules\n"
            "🤝 حياك الله بين أخوانك!",
            parse_mode="HTML",
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🛡️ أوامر حارس المهني\n\n"
        "للمشرفين، استخدم الرد على رسالة العضو ثم الأمر:\n"
        "/warn — تحذير\n"
        "/warnings — عدد التحذيرات\n"
        "/mute [دقائق] — كتم (10 دقائق افتراضياً)\n"
        "/unmute — فك الكتم\n"
        "/ban — حظر\n"
        "/unban USER_ID — فك الحظر\n"
        "/links_on — منع الروابط\n"
        "/links_off — السماح بالروابط\n"
        "/rules — قوانين الكروب\n\n"
        "لازم تضيف البوت مشرف وتمنحه الصلاحيات المناسبة."
    )


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "📌 قوانين كروب حماية كروب المهني\n"
        "1. الاحترام المتبادل ومنع السب والإساءة.\n"
        "2. ممنوع السبام وتكرار الرسائل.\n"
        "3. ممنوع نشر الروابط دون موافقة الإدارة.\n"
        "4. الالتزام بتوجيهات مشرفي الكروب."
    )


async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    target = replied_user(update)
    if not target:
        await update.effective_message.reply_text("رد على رسالة العضو واكتب /warn.")
        return
    key = (update.effective_chat.id, target.id)
    WARNINGS[key] += 1
    count = WARNINGS[key]
    reply = f"⚠️ تم تحذير {target.full_name} ({count}/3)."
    if count >= 3:
        try:
            if not await bot_has_restrict_rights(update, context):
                reply += "\nوصل إلى 3 تحذيرات، لكن البوت لا يملك صلاحية التقييد."
            else:
                await context.bot.restrict_chat_member(
                    update.effective_chat.id,
                    target.id,
                    ChatPermissions(can_send_messages=False),
                    until_date=timedelta(minutes=10),
                )
                WARNINGS[key] = 0
                reply += "\nتم كتمه 10 دقائق وتصفير التحذيرات."
        except Exception:
            log.exception("Auto-mute failed")
            reply += "\nتعذر الكتم؛ تحقق من صلاحيات البوت."
    await update.effective_message.reply_text(reply)


async def warnings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not in_group(update):
        await update.effective_message.reply_text("هذا الأمر داخل الكروب فقط.")
        return
    target = replied_user(update)
    user = target or update.effective_user
    count = WARNINGS[(update.effective_chat.id, user.id)]
    await update.effective_message.reply_text(f"تحذيرات {user.full_name}: {count}/3")


async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    target = replied_user(update)
    if not target:
        await update.effective_message.reply_text("رد على رسالة العضو واكتب /mute أو /mute 15.")
        return
    try:
        minutes = int(context.args[0]) if context.args else 10
        if not 1 <= minutes <= 10080:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("المدة لازم تكون من 1 إلى 10080 دقيقة.")
        return
    try:
        if not await bot_has_restrict_rights(update, context):
            await update.effective_message.reply_text("البوت ما عنده صلاحية تقييد الأعضاء.")
            return
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target.id,
            ChatPermissions(can_send_messages=False),
            until_date=timedelta(minutes=minutes),
        )
        await update.effective_message.reply_text(f"🔇 تم كتم {target.full_name} لمدة {minutes} دقيقة.")
    except Exception:
        log.exception("Mute failed")
        await update.effective_message.reply_text("تعذر الكتم؛ تحقق من صلاحيات البوت ورتبة العضو.")


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    target = replied_user(update)
    if not target:
        await update.effective_message.reply_text("رد على رسالة العضو واكتب /unmute.")
        return
    try:
        if not await bot_has_restrict_rights(update, context):
            await update.effective_message.reply_text("البوت ما عنده صلاحية تقييد الأعضاء.")
            return
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target.id,
            ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await update.effective_message.reply_text(f"🔊 تم فك كتم {target.full_name}.")
    except Exception:
        log.exception("Unmute failed")
        await update.effective_message.reply_text("تعذر فك الكتم؛ تحقق من الصلاحيات.")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    target = replied_user(update)
    if not target:
        await update.effective_message.reply_text("رد على رسالة العضو واكتب /ban.")
        return
    try:
        if not await bot_has_restrict_rights(update, context):
            await update.effective_message.reply_text("البوت ما عنده صلاحية حظر الأعضاء.")
            return
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.effective_message.reply_text(f"⛔ تم حظر {target.full_name}.")
    except Exception:
        log.exception("Ban failed")
        await update.effective_message.reply_text("تعذر الحظر؛ تحقق من صلاحيات البوت ورتبة العضو.")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    target = replied_user(update)
    user_id = target.id if target else None
    if user_id is None and context.args:
        try:
            user_id = int(context.args[0])
        except ValueError:
            user_id = None
    if user_id is None:
        await update.effective_message.reply_text("استخدم /unban USER_ID أو رد على رسالة العضو.")
        return
    try:
        if not await bot_has_restrict_rights(update, context):
            await update.effective_message.reply_text("البوت ما عنده صلاحية الحظر.")
            return
        await context.bot.unban_chat_member(update.effective_chat.id, user_id, only_if_banned=True)
        await update.effective_message.reply_text("تم فك الحظر إذا كان العضو محظوراً.")
    except Exception:
        log.exception("Unban failed")
        await update.effective_message.reply_text("تعذر فك الحظر؛ تحقق من الصلاحيات والـ ID.")


async def links_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await admin_only(update, context):
        LINK_FILTER[update.effective_chat.id] = True
        await update.effective_message.reply_text("🔗 تم تفعيل منع الروابط.")


async def links_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await admin_only(update, context):
        LINK_FILTER[update.effective_chat.id] = False
        await update.effective_message.reply_text("🔗 تم إيقاف منع الروابط.")


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not chat or not user or user.is_bot or chat.type not in GROUP_TYPES:
        return
    if await is_admin(update, context, user.id):
        return

    body = msg.text or msg.caption or ""
    if LINK_FILTER[chat.id] and URL_RE.search(body):
        try:
            await msg.delete()
            await context.bot.send_message(chat.id, "🚫 الروابط ممنوعة بهذا الكروب.")
        except Exception:
            log.exception("Link removal failed; check delete-message permission")
        return

    now = time.monotonic()
    recent = RECENT[(chat.id, user.id)]
    normalized = body.strip()
    recent.append((now, normalized))
    duplicates = sum(1 for stamp, text in recent if normalized and text == normalized and now - stamp <= 20)
    if duplicates >= 4:
        try:
            await msg.delete()
            if await bot_has_restrict_rights(update, context):
                await context.bot.restrict_chat_member(
                    chat.id, user.id,
                    ChatPermissions(can_send_messages=False),
                    until_date=timedelta(minutes=2),
                )
                await context.bot.send_message(chat.id, f"🛡️ تم كتم {user.full_name} دقيقتين بسبب تكرار الرسائل.")
            recent.clear()
        except Exception:
            log.exception("Anti-spam action failed; check bot permissions")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled bot error", exc_info=context.error)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Mehni Guard is running")

    def log_message(self, format, *args):
        return


def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server listening on port %s", port)


def main() -> None:
    start_health_server()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("warnings", warnings))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("links_on", links_on))
    app.add_handler(CommandHandler("links_off", links_off))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, moderate))
    app.add_error_handler(on_error)
    log.info("Starting Mehni Guard")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
