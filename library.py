import json
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

log = logging.getLogger("mehni-guard.library")
LIBRARY_FILE = "library_books.json"
BOOKS = []
PENDING_UPLOAD = set()

DEPARTMENTS = [
    "الصناعي", "الحاسوب وتقنية المعلومات", "التجاري", "الزراعي",
    "الفنون التطبيقية", "السياحة والفندقة", "الإدارة والاقتصاد",
    "الاتصالات والكهرباء", "الميكانيك والسيارات", "البناء والإنشاءات",
    "التمريض والاختصاصات الصحية", "أقسام مهنية أخرى",
]


def load_books():
    global BOOKS
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        BOOKS = data if isinstance(data, list) else []
    except FileNotFoundError:
        BOOKS = []
    except Exception:
        log.exception("Could not load library catalog")
        BOOKS = []


def save_books():
    try:
        with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
            json.dump(BOOKS, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Could not save library catalog")


async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat, user = update.effective_chat, update.effective_user
    if not chat or chat.type not in ("group", "supergroup") or not user:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        log.exception("Library admin check failed")
        return False


def home_keyboard():
    rows = []
    for i in range(0, len(DEPARTMENTS), 2):
        row = [InlineKeyboardButton("📚 " + DEPARTMENTS[i], callback_data=f"library:dept:{i}")]
        if i + 1 < len(DEPARTMENTS):
            row.append(InlineKeyboardButton("📚 " + DEPARTMENTS[i + 1], callback_data=f"library:dept:{i+1}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔄 تحديث المكتبة", callback_data="library:home")])
    return InlineKeyboardMarkup(rows)


async def library(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    load_books()
    msg = update.effective_message
    await msg.reply_text(
        "📚 مكتبة طلاب المهني\n\nاختار القسم حتى تشوف الكتب والملازم المتوفرة.\n"
        "الملفات راح تظهر هنا بعد ما يضيفها المشرفون؛ المكتبة ما تحتوي كتباً تلقائياً بعد.",
        reply_markup=home_keyboard(),
    )


async def addbook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_group_admin(update, context):
        await update.effective_message.reply_text("إضافة الكتب للمشرفين فقط 🛡️")
        return
    PENDING_UPLOAD.add((update.effective_chat.id, update.effective_user.id))
    await update.effective_message.reply_text(
        "📥 إضافة كتاب أو ملزمة\n\n"
        "هسه أرسل ملف PDF برسالة واحدة، واكتب بالوصف 4 معلومات مفصولة بعلامة | بهذا الترتيب:\n"
        "القسم | المرحلة | المادة | عنوان الملف\n\n"
        "مثال:\nالصناعي | الثالث المهني | صيانة الحاسوب | ملزمة الفصل الأول\n\n"
        "للإلغاء اكتب /cancelbook"
    )


async def cancelbook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    PENDING_UPLOAD.discard((update.effective_chat.id, update.effective_user.id))
    await update.effective_message.reply_text("تم إلغاء إضافة الملف.")


async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not chat or not user or (chat.id, user.id) not in PENDING_UPLOAD:
        return
    if not await is_group_admin(update, context):
        PENDING_UPLOAD.discard((chat.id, user.id))
        await msg.reply_text("إضافة الملفات للمشرفين فقط 🛡️")
        return
    doc = msg.document
    caption = (msg.caption or "").strip()
    parts = [part.strip() for part in caption.split("|")]
    if not doc or not doc.file_name or not doc.file_name.lower().endswith(".pdf"):
        await msg.reply_text("أرسل ملف PDF. تگدر تعيد المحاولة أو تكتب /cancelbook.")
        return
    if len(parts) != 4 or any(not p for p in parts):
        await msg.reply_text(
            "وصف الملف غير مكتمل. اكتب بالوصف:\nالقسم | المرحلة | المادة | عنوان الملف\n"
            "مثال: التجاري | الثاني المهني | محاسبة | ملزمة المحاسبة"
        )
        return
    department, stage, subject, title = parts
    BOOKS.append({
        "file_id": doc.file_id,
        "file_name": doc.file_name,
        "department": department,
        "stage": stage,
        "subject": subject,
        "title": title,
        "chat_id": chat.id,
    })
    save_books()
    PENDING_UPLOAD.discard((chat.id, user.id))
    await msg.reply_text(
        f"✅ انضاف الملف للمكتبة!\n📚 القسم: {department}\n🎓 المرحلة: {stage}\n"
        f"📖 المادة: {subject}\n📄 العنوان: {title}\n\n"
        "الطلاب يگدرون يوصلوله من /library."
    )


async def library_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    load_books()
    data = query.data or ""
    if data == "library:home":
        await query.edit_message_text(
            "📚 مكتبة طلاب المهني\nاختار القسم:",
            reply_markup=home_keyboard(),
        )
        return
    pieces = data.split(":")
    if len(pieces) == 3 and pieces[1] == "dept":
        try:
            index = int(pieces[2])
            department = DEPARTMENTS[index]
        except (ValueError, IndexError):
            await query.edit_message_text("القسم غير موجود.", reply_markup=home_keyboard())
            return
        matches = [(i, b) for i, b in enumerate(BOOKS) if b.get("department", "").strip().casefold() == department.casefold()]
        # Include materials entered under a more specific branch name, e.g. a department within industrial.
        if not matches:
            matches = [(i, b) for i, b in enumerate(BOOKS) if department.casefold() in b.get("department", "").casefold()]
        if not matches:
            await query.edit_message_text(
                f"📚 {department}\n\nماكو ملفات مضافة لهذا القسم حالياً. المشرف يگدر يضيف ملفات باستخدام /addbook.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع للأقسام", callback_data="library:home")]]),
            )
            return
        rows = []
        for book_index, book in matches[:80]:
            label = f"📄 {book.get('stage','')} • {book.get('subject','')}: {book.get('title','')}"
            rows.append([InlineKeyboardButton(label[:60], callback_data=f"library:file:{book_index}")])
        rows.append([InlineKeyboardButton("⬅️ رجوع للأقسام", callback_data="library:home")])
        await query.edit_message_text(
            f"📚 قسم {department}\nاختار الكتاب أو الملزمة:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return
    if len(pieces) == 3 and pieces[1] == "file":
        try:
            index = int(pieces[2])
            book = BOOKS[index]
        except (ValueError, IndexError):
            await query.edit_message_text("الملف غير موجود، حدّث المكتبة وحاول مرة ثانية.", reply_markup=home_keyboard())
            return
        await context.bot.send_document(
            chat_id=query.message.chat.id,
            document=book["file_id"],
            caption=(
                f"📚 {book.get('title','كتاب')}\n"
                f"القسم: {book.get('department','')}\n"
                f"المرحلة: {book.get('stage','')}\n"
                f"المادة: {book.get('subject','')}"
            ),
        )
        return
    await query.edit_message_text("اختار القسم من القائمة:", reply_markup=home_keyboard())


def register_library_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters
    load_books()
    app.add_handler(CommandHandler("library", library))
    app.add_handler(CommandHandler("addbook", addbook))
    app.add_handler(CommandHandler("cancelbook", cancelbook))
    app.add_handler(CallbackQueryHandler(library_callback, pattern=r"^library:"))
    app.add_handler(MessageHandler(filters.Document.PDF, receive_document), group=0)
