import logging
import json
import os
import re
import time
import threading
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
from datetime import timedelta

from telegram import BotCommand, BotCommandScopeAllChatAdministrators, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.constants import ChatMemberStatus
from library import register_library_handlers
from games import register_games_handlers

from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
SPAM_FILTER = defaultdict(lambda: True)
WELCOME_TEXT = defaultdict(lambda: "🎉 هلا وغلا {name}، نورت كروب المهني!\\n\\n🛡️ نتمنى إلك وقت ممتع ويانه.\\n📌 اقرا قوانين الكروب بكتابة /rules\\n🤝 حياك الله بين أخوانك!")
LINK_FILTER = defaultdict(lambda: True)
CUSTOM_REPLIES = defaultdict(dict)  # chat_id -> {trigger: reply}
PENDING_REPLY = {}  # (chat_id, user_id) -> {"stage": "trigger"|"reply", "trigger": str}
CUSTOM_REPLIES_FILE = "custom_replies.json"
DHIKR_FILE = "dhikr_chats.json"
DHIKR_CHATS = {}  # chat_id -> enabled
DHIKR_MESSAGES = [
    "🌿 أستغفر الله العظيم وأتوب إليه.",
    "🤍 سبحان الله وبحمده، سبحان الله العظيم.",
    "✨ لا إله إلا الله وحده لا شريك له، له الملك وله الحمد وهو على كل شيء قدير.",
    "🕊️ اللهم صلِّ وسلم على نبينا محمد.",
    "🌸 الحمد لله دائماً وأبداً.",
    "🤲 لا حول ولا قوة إلا بالله.",
    "💚 سبحان الله، والحمد لله، ولا إله إلا الله، والله أكبر.",
    "📖 قال الله تعالى: «فَاذْكُرُونِي أَذْكُرْكُمْ» [البقرة: 152].",
    "🌙 تذكير لطيف: أكثروا من الاستغفار، فذكر الله طمأنينة للقلب.",
    "📜 قال رسول الله ﷺ: «الكلمة الطيبة صدقة» (متفق عليه).",
    "📜 قال رسول الله ﷺ: «يسروا ولا تعسروا، وبشروا ولا تنفروا» (متفق عليه).",
]

def load_dhikr_chats() -> None:
    try:
        with open(DHIKR_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        DHIKR_CHATS.update({int(chat_id): bool(enabled) for chat_id, enabled in data.items()})
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not load dhikr settings")

def save_dhikr_chats() -> None:
    try:
        with open(DHIKR_FILE, "w", encoding="utf-8") as file:
            json.dump({str(chat_id): enabled for chat_id, enabled in DHIKR_CHATS.items()}, file, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Could not save dhikr settings")

async def remember_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat and chat.type in GROUP_TYPES:
        DHIKR_CHATS.setdefault(chat.id, False)
        save_dhikr_chats()

async def dhikr_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    DHIKR_CHATS[update.effective_chat.id] = True
    save_dhikr_chats()
    await update.effective_message.reply_text("✅ تم تفعيل التذكير الديني لهذا الكروب. راح أرسل ذكر أو استغفار أو حديث كل ساعة تقريباً. لإيقافه اكتب /adhkar_off")

async def dhikr_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    DHIKR_CHATS[update.effective_chat.id] = False
    save_dhikr_chats()
    await update.effective_message.reply_text("⏸️ تم إيقاف التذكير الديني بهذا الكروب.")

async def send_periodic_dhikr(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not DHIKR_CHATS:
        return
    # Rotate through reminders deterministically; avoid random repeated messages.
    index = context.application.bot_data.get("dhikr_index", 0)
    message = DHIKR_MESSAGES[index % len(DHIKR_MESSAGES)]
    context.application.bot_data["dhikr_index"] = index + 1
    for chat_id, enabled in list(DHIKR_CHATS.items()):
        if enabled:
            try:
                await context.bot.send_message(chat_id=chat_id, text=message)
            except Exception:
                log.exception("Could not send dhikr to chat %s", chat_id)



def load_custom_replies() -> None:
    try:
        with open(CUSTOM_REPLIES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        for chat_id, replies in data.items():
            if isinstance(replies, dict):
                CUSTOM_REPLIES[int(chat_id)].update(
                    {str(trigger): str(reply) for trigger, reply in replies.items()}
                )
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not load custom replies")


def save_custom_replies() -> None:
    try:
        with open(CUSTOM_REPLIES_FILE, "w", encoding="utf-8") as file:
            json.dump(
                {str(chat_id): replies for chat_id, replies in CUSTOM_REPLIES.items()},
                file,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        log.exception("Could not save custom replies")


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
    msg = update.effective_message
    if not msg:
        return
    # Deep-link whisper: the sender opens the bot privately to compose a message.
    if context.args and context.args[0].startswith("whisper_"):
        parts = context.args[0].split("_")
        if len(parts) == 4:
            try:
                target_id, sender_id, group_id = map(int, parts[1:])
            except ValueError:
                await msg.reply_text("رابط الهمسة غير صالح. ارجع للكروب وحاول من جديد.")
                return
            if update.effective_user and update.effective_user.id != sender_id:
                await msg.reply_text("هذا الرابط مخصص للشخص اللي طلب الهمسة، مو إلك.")
                return
            if target_id == sender_id:
                await msg.reply_text("ما تگدر ترسل همسة لنفسك 🙂")
                return
            context.user_data["pending_whisper"] = {
                "target_id": target_id, "sender_id": sender_id, "group_id": group_id
            }
            await msg.reply_text(
                "🤫 نظام الهمسات — حارس المهني\n\n"
                "اكتب الهمسة برسالة نصية وحدة، وراح أوصلها للشخص بدون ما أذكر اسمك. "
                "لا ترسل معلومات حساسة أو تهديدات. الحد الأقصى 1000 حرف.\n"
                "للإلغاء اكتب /cancel."
            )
            return
    await msg.reply_text(
        "أهلاً بيك، أني حارس المهني 🛡️\n"
        "بوت حماية وإدارة كروب حماية كروب المهني.\n"
        "اكتب /help حتى تشوف الأوامر.\n"
        "🤫 لإرسال همسة: رد على رسالة الشخص بالكروب واكتب /whisper."
    )


async def whisper_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not chat or not user:
        return
    if chat.type not in GROUP_TYPES:
        await msg.reply_text("🤫 ارجع للكروب ورد على رسالة الشخص واكتب «اهمس».")
        return
    target = replied_user(update)
    if not target:
        await msg.reply_text("رد على رسالة الشخص اللي تريد تهمس له، وبعدها اكتب «اهمس».")
        return
    if target.id == user.id:
        await msg.reply_text("ما تگدر ترسل همسة لنفسك 🙂")
        return
    try:
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=whisper_{target.id}_{user.id}_{chat.id}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🤫 اكتب الهمسة بالخاص", url=link)]])
        await msg.reply_text(
            f"🤫 حتى ترسل همسة إلى {target.first_name}، اضغط الزر واكتبها بالخاص.\n"
            "بعد الإرسال راح تظهر رسالة بالكروب بيها زر «👁️ أظهر الهمسة»، "
            "وهذا الزر يگدر يستخدمه المستلم المحدد فقط.",
            reply_markup=keyboard,
            reply_to_message_id=msg.message_id,
        )
    except Exception:
        log.exception("Could not create whisper link")
        await msg.reply_text("تعذر إنشاء رابط الهمسة حالياً. حاول بعدين.")


async def whisper_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow the plain Arabic word 'اهمس' as a reply to a member."""
    await whisper_command(update, context)


async def show_whisper_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 2:
        await query.answer("الهمسة غير صالحة.", show_alert=True)
        return
    token = parts[1]
    payload = context.bot_data.get("pending_group_whispers", {}).get(token)
    if not payload:
        await query.answer("انتهت صلاحية الهمسة أو تم عرضها سابقاً.", show_alert=True)
        return
    if query.from_user.id != payload["target_id"]:
        await query.answer("هاي الهمسة مخصصة لشخص ثاني 🤫", show_alert=True)
        return
    # The popup is visible only to the person who tapped the button.
    # Telegram limits callback alert text, so whisper messages are capped at 180 characters.
    try:
        await query.answer("🤫 الهمسة:\n" + payload["text"], show_alert=True)
        context.bot_data["pending_group_whispers"].pop(token, None)
    except Exception:
        log.exception("Could not reveal whisper in callback alert")
        await query.answer("تعذر إظهار الهمسة. جرّب مرة ثانية.", show_alert=True)


async def cancel_whisper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_whisper", None)
    if update.effective_message:
        await update.effective_message.reply_text("تم إلغاء الهمسة.")


async def whisper_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not chat or chat.type != "private" or not user or user.is_bot:
        return
    pending = context.user_data.get("pending_whisper")
    if not pending:
        return
    whisper_text = (msg.text or "").strip()
    if not whisper_text:
        await msg.reply_text("الهمسة لازم تكون نصية. اكتب نصاً أو /cancel للإلغاء.")
        return
    if len(whisper_text) > 180:
        await msg.reply_text("حتى تبقى بالكروب وتظهر الهمسة بنافذة خاصة إلك، خليها 180 حرف أو أقل.")
        return
    if whisper_text.startswith("/"):
        await msg.reply_text("حتى ما ينفذ البوت أوامر بالخطأ، اكتب نص الهمسة بدون بداية / أو اكتب /cancel.")
        return
    if user.id != pending["sender_id"]:
        context.user_data.pop("pending_whisper", None)
        await msg.reply_text("انتهت صلاحية رابط الهمسة. ارجع للكروب واطلب رابطاً جديداً.")
        return
    try:
        token = secrets.token_urlsafe(12)
        context.bot_data.setdefault("pending_group_whispers", {})[token] = {
            "target_id": pending["target_id"],
            "sender_id": user.id,
            "group_id": pending["group_id"],
            "text": whisper_text,
        }
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("👁️ أظهر الهمسة", callback_data=f"whisper_show:{token}")
        ]])
        await context.bot.send_message(
            chat_id=pending["group_id"],
            text="🤫 عندك همسة سرية!\nاضغط «👁️ أظهر الهمسة» حتى تقراها بنافذة خاصة إلك، وتبقى بالكروب.\n"
                 "🔒 محتوى الهمسة يظهر للمستلم فقط، والحد الأقصى 180 حرف.",
            reply_markup=keyboard,
        )
        context.user_data.pop("pending_whisper", None)
        await msg.reply_text("✅ تم إرسال الهمسة للكروب. المستلم يگدر يقراها بنافذة خاصة بدون ما يطلع من الكروب.")
    except Exception:
        log.exception("Could not post group whisper reveal button")
        await msg.reply_text("تعذر نشر زر الهمسة بالكروب. تأكد أن البوت ما زال موجوداً بالكروب وحاول مرة ثانية.")


async def report_whisper_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or query.message.chat.type != "private":
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer("تعذر قراءة البلاغ.", show_alert=True)
        return
    try:
        group_id, sender_id = int(parts[1]), int(parts[2])
    except ValueError:
        await query.answer("بيانات البلاغ غير صالحة.", show_alert=True)
        return
    recipient_id = query.from_user.id
    body = query.message.text or "[همسة غير نصية]"
    try:
        admins = await context.bot.get_chat_administrators(group_id)
    except Exception:
        await query.answer("تعذر الوصول لمشرفي الكروب.", show_alert=True)
        return
    delivered = 0
    report = (
        "🚩 بلاغ عن همسة مجهولة\n"
        f"الكروب: {group_id}\n"
        f"مرسل الهمسة (للمشرفين فقط): {sender_id}\n"
        f"المستلم: {recipient_id}\n\n"
        f"نص الهمسة المبلّغ عنها:\n{body}"
    )
    for admin in admins:
        if getattr(admin, "user", None) and not admin.user.is_bot:
            try:
                await context.bot.send_message(admin.user.id, report)
                delivered += 1
            except Exception:
                # Telegram only permits private delivery after an admin has started the bot.
                continue
    if delivered:
        await query.answer("تم إرسال البلاغ للمشرفين اللي فاتحين البوت.", show_alert=True)
    else:
        await query.answer("ما وصل البلاغ. لازم أحد المشرفين يفتح البوت بالخاص أولاً.", show_alert=True)


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete Telegram's join service message, then welcome human members separately."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type not in GROUP_TYPES or not msg.new_chat_members:
        return

    # Telegram creates a service message such as "X added Y". Bots cannot prevent
    # it from being created, but an admin bot with Delete Messages permission can
    # remove it as soon as it receives the update.
    try:
        await msg.delete()
    except Exception:
        log.warning(
            "Could not delete join service message in chat %s; "
            "make the bot an admin with Delete Messages permission.",
            chat.id,
            exc_info=True,
        )

    # Keep the existing welcome feature for people, but do not welcome bots.
    for member in msg.new_chat_members:
        if member.is_bot:
            continue
        name = member.mention_html()
        welcome = WELCOME_TEXT[chat.id].format(name=name)
        await context.bot.send_message(
            chat_id=chat.id, text=welcome, parse_mode="HTML"
        )


def admin_menu_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    links = LINK_FILTER[chat_id]
    spam = SPAM_FILTER[chat_id]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔗 منع الروابط: {'مفعّل' if links else 'متوقف'}", callback_data="menu:links")],
        [InlineKeyboardButton(f"🔁 منع التكرار: {'مفعّل' if spam else 'متوقف'}", callback_data="menu:spam")],
        [InlineKeyboardButton("👋 تغيير الترحيب", callback_data="menu:welcome"),
         InlineKeyboardButton("📊 الإحصائيات", callback_data="menu:stats")],
        [InlineKeyboardButton("💬 الردود التلقائية", callback_data="menu:replies"),
         InlineKeyboardButton("📌 قوانين الكروب", callback_data="menu:rules")],
        [InlineKeyboardButton("🛠️ شرح أوامر الإدارة", callback_data="menu:commands")],
    ])


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    await update.effective_message.reply_text(
        "🛡️ لوحة تحكم حارس المهني\\n\\n"
        "اختار الميزة حتى تشوف شتسوي وتتحكم بيها. هاي الأزرار للمشرفين فقط.",
        reply_markup=admin_menu_keyboard(update.effective_chat.id),
    )


async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    chat = query.message.chat
    user = query.from_user
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await query.answer("هاي اللوحة للمشرفين فقط 🛡️", show_alert=True)
            return
    except Exception:
        await query.answer("ما گدرت أتأكد من صلاحياتك.", show_alert=True)
        return

    action = (query.data or "").split(":", 1)[-1]
    if action == "open":
        await query.edit_message_text(
            "🛡️ قائمة أوامر حارس المهني — للمشرفين فقط",
            reply_markup=admin_menu_keyboard(chat.id),
        )
        return
    elif action == "help_warn":
        detail = "⚠️ للتحذير: رد على رسالة العضو واكتب /warn"
    elif action == "help_mute":
        detail = "🔇 للكتم: رد على رسالة العضو واكتب /mute"
    elif action == "help_unmute":
        detail = "🔊 لفك الكتم: رد على رسالة العضو واكتب /unmute"
    elif action == "help_ban":
        detail = "⛔ للحظر: رد على رسالة العضو واكتب /ban"
    elif action == "help_unban":
        detail = "🔓 لفك الحظر: اكتب /unban متبوعاً بمعرّف العضو (ID)"
    elif action == "help_whisper":
        detail = "🤫 للهمس: رد على رسالة الشخص واكتب اهمس، ثم اتبع الزر"
    elif action == "links":
        LINK_FILTER[chat.id] = not LINK_FILTER[chat.id]
        status = "مفعّل" if LINK_FILTER[chat.id] else "متوقف"
        detail = f"🔗 منع الروابط صار {status}.\\nمنع الروابط يحذف الرسائل التي تحتوي على روابط من الأعضاء غير المشرفين."
    elif action == "spam":
        SPAM_FILTER[chat.id] = not SPAM_FILTER[chat.id]
        status = "مفعّل" if SPAM_FILTER[chat.id] else "متوقف"
        detail = f"🔁 منع التكرار صار {status}.\\nإذا العضو كرر نفس الرسالة 4 مرات خلال 20 ثانية، يحذف البوت الرسالة ويحاول يكتمه دقيقتين."
    elif action == "stats":
        warning_count = sum(count for (cid, _), count in WARNINGS.items() if cid == chat.id)
        detail = (
            "📊 إحصائيات الكروب\\n"
            f"🔗 منع الروابط: {'مفعّل' if LINK_FILTER[chat.id] else 'متوقف'}\\n"
            f"🔁 منع التكرار: {'مفعّل' if SPAM_FILTER[chat.id] else 'متوقف'}\\n"
            f"⚠️ التحذيرات الحالية: {warning_count}\\n"
            f"💬 الردود التلقائية: {len(CUSTOM_REPLIES.get(chat.id, {}))}"
        )
    elif action == "welcome":
        context.user_data["await_welcome_chat"] = chat.id
        detail = "✍️ اكتب رسالة الترحيب الجديدة برسالة منفصلة. استخدم {name} حتى يظهر اسم العضو.\\nمثال: هلا {name} نورت كروبنا!\\nللإلغاء اكتب إلغاء."
    elif action == "replies":
        replies = CUSTOM_REPLIES.get(chat.id, {})
        detail = "💬 الردود التلقائية:\\n" + ("\\n".join(f"• {key} ← {value[:100]}" for key, value in sorted(replies.items())) if replies else "ماكو ردود مضافة بعد.")
        detail += "\\n\\nللإضافة اكتب «اضف رد»، وللحذف استخدم /delreply الكلمة"
    elif action == "rules":
        detail = "📌 قوانين الكروب\\n1. الاحترام المتبادل.\\n2. ممنوع السبام وتكرار الرسائل.\\n3. الروابط حسب إعدادات الإدارة.\\n4. الالتزام بتوجيهات المشرفين."
    elif action == "commands":
        detail = (
            "🛠️ أوامر الإدارة وشنو تسوي:\\n"
            "/warn — تحذير عضو (رد على رسالته)؛ 3 تحذيرات تحاول تكتمه 10 دقائق.\\n"
            "/warnings — عرض تحذيراتك أو تحذيرات العضو بالرد عليه.\\n"
            "/mute 10 — كتم العضو 10 دقائق (رد على رسالته).\\n"
            "/unmute — فك الكتم (رد على رسالته).\\n"
            "/ban — حظر عضو (رد على رسالته).\\n"
            "/unban USER_ID — فك الحظر.\\n"
            "/links_on و /links_off — تشغيل/إيقاف منع الروابط.\\n"
            "/spam_on و /spam_off — تشغيل/إيقاف منع التكرار.\\n"
            "/setwelcome نص — تغيير الترحيب.\\n"
            "/replies و /delreply كلمة — إدارة الردود.\\n"
            "/stats — الإحصائيات."
        )
    else:
        detail = "ما تعرفت على هذا الخيار."
    try:
        await query.edit_message_text(
            "🛡️ لوحة تحكم حارس المهني\\n\\n" + detail,
            reply_markup=admin_menu_keyboard(chat.id),
        )
    except Exception:
        await query.message.reply_text(
            "🛡️ لوحة تحكم حارس المهني\\n\\n" + detail,
            reply_markup=admin_menu_keyboard(chat.id),
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
        "/spam_on — تفعيل حماية التكرار\n"
        "/spam_off — إيقاف حماية التكرار\n"
        "/setwelcome نص الترحيب — تعديل الترحيب (استخدم {name} للاسم)\n"
        "/stats — إحصائيات الحماية لهذا الكروب\n"
        "/replies — عرض الردود التلقائية المضافة\n"
        "/delreply كلمة — حذف رد تلقائي (مثال: /delreply شلونكم)\n"
        "اكتب «اضف رد» لإضافة رد جديد\n"
        "/rules — قوانين الكروب\n"
        "/whisper — رد على رسالة عضو واكتب الأمر لإرسال همسة مجهولة\n"
        "/cancel — إلغاء كتابة الهمسة\n"
        "🎮 اكتب «الالعاب» لعرض قائمة الألعاب، و«نقاطي» لعرض رصيدك.\n"
        "الألعاب تشمل الألغاز والأسئلة والرياضيات والعواصم والحظ وحجر ورق مقص.\n\n"
        "لازم تضيف البوت مشرف وتمنحه الصلاحيات المناسبة.\n"
        "ملاحظة: الهمسات نصية فقط، بحد أقصى 1000 حرف، ويمكن للمستلم الإبلاغ عنها للمشرفين."
    )


async def list_replies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    replies = CUSTOM_REPLIES.get(update.effective_chat.id, {})
    if not replies:
        await update.effective_message.reply_text(
            "ماكو ردود مخصصة حالياً. أضف أول رد بكتابة «اضف رد»."
        )
        return
    lines = ["📋 الردود التلقائية بهذا الكروب:"]
    for index, (trigger, reply) in enumerate(sorted(replies.items()), start=1):
        short_reply = reply if len(reply) <= 180 else reply[:177] + "..."
        lines.append(f"\n{index}. إذا كتبوا: {trigger}\n↳ الرد: {short_reply}")
    await update.effective_message.reply_text("\n".join(lines))


async def delete_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    trigger = " ".join(context.args).strip().lower()
    if not trigger:
        await update.effective_message.reply_text(
            "اكتب الكلمة بعد الأمر، مثال:\n/delreply شلونكم\n"
            "ولعرض الردود الحالية استخدم /replies"
        )
        return
    replies = CUSTOM_REPLIES.get(update.effective_chat.id, {})
    if trigger not in replies:
        await update.effective_message.reply_text(
            "ما لكيت رد محفوظ بهالكلمة. استخدم /replies حتى تشوف الكلمات المسجلة."
        )
        return
    del replies[trigger]
    save_custom_replies()
    await update.effective_message.reply_text(f"🗑️ تم حذف الرد المرتبط بـ: {trigger}")


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


async def spam_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await admin_only(update, context):
        SPAM_FILTER[update.effective_chat.id] = True
        await update.effective_message.reply_text("🛡️ تم تفعيل حماية تكرار الرسائل.")


async def spam_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await admin_only(update, context):
        SPAM_FILTER[update.effective_chat.id] = False
        await update.effective_message.reply_text("⚠️ تم إيقاف حماية تكرار الرسائل.")


async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    template = " ".join(context.args).strip()
    if not template:
        await update.effective_message.reply_text(
            "اكتب نص الترحيب بعد الأمر. مثال:\\n"
            "/setwelcome هلا {name} نورت كروبنا!\\n"
            "استخدم {name} حتى يظهر اسم العضو."
        )
        return
    if "{name}" not in template:
        template += "\\n\\n👋 نورت الكروب!"
    WELCOME_TEXT[update.effective_chat.id] = template
    await update.effective_message.reply_text("✅ تم تحديث رسالة الترحيب لهذا التشغيل.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_only(update, context):
        return
    chat_id = update.effective_chat.id
    warning_count = sum(count for (cid, _), count in WARNINGS.items() if cid == chat_id)
    reply_count = len(CUSTOM_REPLIES.get(chat_id, {}))
    link_state = "مفعّل" if LINK_FILTER[chat_id] else "متوقف"
    spam_state = "مفعّلة" if SPAM_FILTER[chat_id] else "متوقفة"
    await update.effective_message.reply_text(
        "📊 إحصائيات حماية الكروب\\n\\n"
        f"🔗 منع الروابط: {link_state}\\n"
        f"🔁 منع التكرار: {spam_state}\\n"
        f"⚠️ التحذيرات الحالية: {warning_count}\\n"
        f"💬 الردود التلقائية: {reply_count}"
    )


async def auto_replies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user or user.is_bot or chat.type not in GROUP_TYPES:
        return

    body = (msg.text or "").strip().lower()
    state_key = (chat.id, user.id)

    # Developer signature: respond when members ask who built the bot.
    developer_questions = {
        "من المطور", "منو المطور", "من مطور البوت", "منو مطور البوت",
        "مطور البوت", "من صنع البوت", "منو صنع البوت", "من برمج البوت",
        "منو برمج البوت", "من صاحب البوت", "منو صاحب البوت", "من المبرمج",
        "منو المبرمج", "مين المطور", "مين مطور البوت", "المطور", "المطوّر",
    }
    if body.strip("؟?!،. ") in developer_questions:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("👨‍💻 حساب المهندس مچاوي", url="https://t.me/IBIBNI1")
        ]])
        await msg.reply_text(
            "👨‍💻 مطوّر حارس المهني\n\n"
            "المهندس مچاوي\n"
            "📱 Telegram: @IBIBNI1\n"
            "✨ بصمة مچاوي — تطوير وبرمجة البوت",
            reply_markup=keyboard,
        )
        return

    # The welcome editor button asks the admin for a new welcome message.
    if context.user_data.get("await_welcome_chat") == chat.id:
        if not await is_admin(update, context, user.id):
            context.user_data.pop("await_welcome_chat", None)
        elif body in {"الغاء", "إلغاء"}:
            context.user_data.pop("await_welcome_chat", None)
            await msg.reply_text("تم إلغاء تغيير الترحيب.")
            return
        else:
            template = (msg.text or "").strip()
            if not template:
                await msg.reply_text("اكتب رسالة ترحيب نصية، أو اكتب إلغاء.")
                return
            if "{name}" not in template:
                template += "\\n\\n👋 نورت الكروب!"
            WELCOME_TEXT[chat.id] = template
            context.user_data.pop("await_welcome_chat", None)
            await msg.reply_text("✅ تم تحديث رسالة الترحيب لهذا التشغيل.")
            return

    # Show the transparent inline admin command menu when an admin types "الاوامر".
    if body in {"الاوامر", "الأوامر", "اوامر"}:
        if await is_admin(update, context, user.id):
            await msg.reply_text(
                "🛡️ قائمة أوامر حارس المهني — للمشرفين فقط",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛡️ لوحة التحكم", callback_data="menu:open")],
                    [InlineKeyboardButton("⚠️ تحذير عضو", callback_data="menu:help_warn"), InlineKeyboardButton("🔇 كتم عضو", callback_data="menu:help_mute")],
                    [InlineKeyboardButton("🔊 فك الكتم", callback_data="menu:help_unmute"), InlineKeyboardButton("⛔ حظر عضو", callback_data="menu:help_ban")],
                    [InlineKeyboardButton("🔓 فك الحظر", callback_data="menu:help_unban"), InlineKeyboardButton("📊 الإحصائيات", callback_data="menu:stats")],
                    [InlineKeyboardButton("🔗 منع الروابط", callback_data="menu:links"), InlineKeyboardButton("🔁 منع التكرار", callback_data="menu:spam")],
                    [InlineKeyboardButton("👋 تغيير الترحيب", callback_data="menu:welcome"), InlineKeyboardButton("💬 الردود", callback_data="menu:replies")],
                    [InlineKeyboardButton("📌 القوانين", callback_data="menu:rules"), InlineKeyboardButton("🤫 طريقة الهمس", callback_data="menu:help_whisper")],
                ]),
            )
        return

    # Let group admins create custom automatic replies through a short conversation.
    if body in {"الغاء", "إلغاء", "الغاء الرد", "إلغاء الرد"} and state_key in PENDING_REPLY:
        if await is_admin(update, context, user.id):
            PENDING_REPLY.pop(state_key, None)
            await msg.reply_text("تم إلغاء إضافة الرد.")
            return

    if state_key in PENDING_REPLY:
        if not await is_admin(update, context, user.id):
            PENDING_REPLY.pop(state_key, None)
        else:
            state = PENDING_REPLY[state_key]
            if not body:
                await msg.reply_text("اكتب نصاً صالحاً، أو اكتب «إلغاء» للإلغاء.")
                return
            if state["stage"] == "trigger":
                state["trigger"] = body
                state["stage"] = "reply"
                await msg.reply_text(
                    "تمام 👍 هسه اكتب الرد اللي تريدني أرسله من أحد يكتب هالكلمة أو الجملة."
                )
                return
            trigger = state["trigger"]
            CUSTOM_REPLIES[chat.id][trigger] = (msg.text or "").strip()
            save_custom_replies()
            PENDING_REPLY.pop(state_key, None)
            await msg.reply_text(
                f"✅ تمت إضافة الرد بنجاح!\n\n"
                f"🔹 الكلمة أو الجملة: {trigger}\n"
                f"🔹 الرد: {(msg.text or '').strip()}\n\n"
                "تگدر تضيف رد ثاني بكتابة «اضف رد»."
            )
            return

    if body in {"اضف رد", "أضف رد", "اضافة رد", "إضافة رد"}:
        if await is_admin(update, context, user.id):
            PENDING_REPLY[state_key] = {"stage": "trigger"}
            await msg.reply_text(
                "✍️ اكتب الكلمة أو الجملة اللي تريدني أرد عليها.\n"
                "مثال: شلونكم\n\n"
                "للإلغاء اكتب «إلغاء»."
            )
        else:
            await msg.reply_text("عذراً، إضافة الردود للمشرفين فقط 🛡️")
        return

    # Custom replies are specific to this group and survive restarts when the host keeps files.
    custom = CUSTOM_REPLIES.get(chat.id, {})
    for trigger in sorted(custom, key=len, reverse=True):
        if trigger and trigger in body:
            await msg.reply_text(custom[trigger])
            return

    # Match greetings and common friendly phrases without replying to every message.
    replies = [
        (("حارس",),
         "ها يروحا"),
        (("بوت", "يا بوت", "بوتنا"),
         "عيون البوت 👀🛡️\nآمروني، حاضر وياكم!"),
        (("mehni guard bot", "mehniguardbot", "حارس المهني"),
         "هلا بيكم، حارس المهني حاضر بالخدمة 🛡️🤖"),
        (("السلام عليكم ورحمة الله وبركاته", "السلام عليكم", "سلام عليكم", "السّلام عليكم"),
         "وعليكم السلام ورحمة الله وبركاته 🌹\nحياك الله بكروب المهني 🛡️"),
        (("صباح الخير", "صباحو"),
         "صباح النور والسرور ☀️🌹\nالله يجعل يومكم خير وبركة."),
        (("مساء الخير", "مسا الخير", "مساء النور"),
         "مساء الورد والياسمين 🌙🌹\nنورتوا الكروب."),
        (("شلونكم", "شخباركم", "كيفكم", "شلونك"),
         "الحمد لله بخير، عساكم دوم بخير وعافية ❤️\nإنتوا شلونكم؟"),
        (("شكرا", "شكراً", "مشكور", "مشكورة", "تسلم", "تسلمين"),
         "العفو، بالخدمة دائماً 🌹❤️"),
        (("مبروك", "مبارك", "الف مبروك", "ألف مبروك"),
         "ألف ألف مبروك 🎉❤️\nربي يتممها عليكم بالخير والفرح."),
        (("عيدكم مبارك", "كل عام وانتم بخير", "كل عام وأنتم بخير"),
         "وإنتوا بألف خير وصحة وسلامة 🌙✨"),
        (("تصبحون على خير", "تصبح على خير"),
         "وإنت من أهله 🌙🤍\nأحلام سعيدة."),
        (("باي", "مع السلامة", "اشوفكم على خير", "أشوفكم على خير"),
         "الله وياك ويحفظك 🤍👋\nنشوفك على خير."),
        (("وين القوانين", "قوانين الكروب", "قوانين الكروب؟"),
         "📌 قوانين الكروب تگدر تشوفها بكتابة /rules"),
        (("منو الادمن", "منو المشرف", "منو المشرفين"),
         "للاستفسارات أو الشكاوى، راسل أحد مشرفي الكروب بكل احترام 🛡️"),
    ]
    for phrases, reply in replies:
        if any(phrase in body for phrase in phrases):
            await msg.reply_text(reply)
            return


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
    if not SPAM_FILTER[chat.id]:
        return
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


async def configure_bot_menu(app: Application) -> None:
    """Publish the command list and make Telegram's menu button open it."""
    commands = [
        BotCommand("start", "بدء تشغيل حارس المهني"),
        BotCommand("help", "عرض المساعدة والأوامر"),
        BotCommand("rules", "عرض قوانين الكروب"),
        BotCommand("whisper", "إرسال همسة بالرد على رسالة"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
        BotCommand("panel", "لوحة تحكم المشرفين"),
        BotCommand("warn", "تحذير عضو بالرد على رسالته"),
        BotCommand("warnings", "عرض التحذيرات"),
        BotCommand("mute", "كتم عضو بالرد على رسالته"),
        BotCommand("unmute", "فك كتم عضو بالرد على رسالته"),
        BotCommand("ban", "حظر عضو بالرد على رسالته"),
        BotCommand("unban", "فك حظر باستخدام ID"),
        BotCommand("stats", "إحصائيات الكروب"),
        BotCommand("replies", "عرض الردود التلقائية"),
        BotCommand("delreply", "حذف رد تلقائي"),
        BotCommand("links_on", "تفعيل منع الروابط"),
        BotCommand("links_off", "إيقاف منع الروابط"),
        BotCommand("spam_on", "تفعيل حماية التكرار"),
        BotCommand("spam_off", "إيقاف حماية التكرار"),
        BotCommand("setwelcome", "تغيير رسالة الترحيب"),
        BotCommand("adhkar_on", "تفعيل الأذكار والاستغفار والأحاديث"),
        BotCommand("adhkar_off", "إيقاف التذكير الديني"),
    ]
    try:
        # Keep group command suggestions visible only to chat administrators.
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllChatAdministrators())
        # Keep a default command list for private chats with the bot.
        await app.bot.set_my_commands(commands)
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        log.info("Telegram command menu configured")
    except Exception:
        log.exception("Could not configure Telegram command menu")


def main() -> None:
    start_health_server()
    app = Application.builder().token(TOKEN).post_init(configure_bot_menu).build()
    load_dhikr_chats()
    if app.job_queue:
        app.job_queue.run_repeating(send_periodic_dhikr, interval=60 * 60, first=60, name="periodic_dhikr")
    else:
        log.error("JobQueue unavailable; install python-telegram-bot[job-queue]")
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, remember_group), group=-2)
    app.add_handler(CommandHandler("adhkar_on", dhikr_on))
    app.add_handler(CommandHandler("adhkar_off", dhikr_off))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whisper", whisper_command))
    app.add_handler(CommandHandler("cancel", cancel_whisper))
    app.add_handler(CallbackQueryHandler(report_whisper_callback, pattern=r"^whisper_report:"))
    app.add_handler(CallbackQueryHandler(show_whisper_callback, pattern=r"^whisper_show:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, whisper_private_text))
    app.add_handler(CommandHandler("panel", admin_menu))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(admin_menu_callback, pattern=r"^menu:"))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("warnings", warnings))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("links_on", links_on))
    app.add_handler(CommandHandler("links_off", links_off))
    app.add_handler(CommandHandler("spam_on", spam_on))
    app.add_handler(CommandHandler("spam_off", spam_off))
    app.add_handler(CommandHandler("setwelcome", set_welcome))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("replies", list_replies))
    app.add_handler(CommandHandler("delreply", delete_reply))
    load_custom_replies()
    register_library_handlers(app)
    register_games_handlers(app)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.Regex(r"(?i)^اهمس$"), whisper_text_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_replies))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, moderate))
    app.add_error_handler(on_error)
    log.info("Starting Mehni Guard")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
