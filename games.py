import json
import logging
import random
import re
import time
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

log = logging.getLogger("mehni-guard.games")
DATA_FILE = Path("games_data.json")
DATA = {"points": {}, "messages": {}, "pending": {}, "last_play": {}}

# Small built-in Arabic question bank; admins can expand it later.
QUIZZES = {
    "المختلف": [
        ("تفاحة، موز، برتقال، سيارة", "سيارة"),
        ("قط، كلب، حصان، كرسي", "كرسي"),
        ("بغداد، كركوك، الموصل، باريس", "باريس"),
    ],
    "امثله": [
        ("اذكر مثالاً على حيوان يبدأ بحرف (س)", "سمكة"),
        ("اذكر فاكهة لونها أصفر", "موز"),
        ("اذكر شيئاً نستخدمه للكتابة", "قلم"),
    ],
    "العكس": [
        ("ما عكس كلمة طويل؟", "قصير"),
        ("ما عكس كلمة سريع؟", "بطيء"),
        ("ما عكس كلمة قريب؟", "بعيد"),
    ],
    "حزوره": [
        ("شيء له أسنان ولا يعض، ما هو؟", "مشط"),
        ("كلما أخذت منه كبر، ما هو؟", "حفرة"),
        ("له عين ولا يرى، ما هو؟", "إبرة"),
    ],
    "معاني": [
        ("ما معنى كلمة شجاع؟", "جريء"),
        ("ما مرادف كلمة سعيد؟", "فرحان"),
        ("ما معنى كلمة واسع؟", "فسيح"),
    ],
    "اسئله": [
        ("كم عدد أيام الأسبوع؟", "7"),
        ("ما الكوكب المعروف بالكوكب الأحمر؟", "المريخ"),
        ("كم ضلعاً للمثلث؟", "3"),
    ],
    "اسالني": [
        ("ما أكبر كوكب في المجموعة الشمسية؟", "المشتري"),
        ("ما العضو الذي يضخ الدم؟", "القلب"),
        ("ما الغاز الذي تحتاجه النار للاشتعال؟", "الأوكسجين"),
    ],
    "لغز": [
        ("ما الشيء الذي يكتب ولا يقرأ؟", "القلم"),
        ("ما الشيء الذي يمشي بلا قدمين؟", "الوقت"),
        ("ما الشيء الذي كلما زاد نقص؟", "العمر"),
    ],
    "رياضيات": [
        ("احسب: 12 × 4", "48"),
        ("احسب: 81 ÷ 9", "9"),
        ("احسب: 35 + 27", "62"),
        ("احسب: 100 - 46", "54"),
    ],
    "انكليزي": [
        ("ما معنى Apple بالعربي؟", "تفاحة"),
        ("ما معنى Book بالعربي؟", "كتاب"),
        ("ما معنى Water بالعربي؟", "ماء"),
        ("ما معنى School بالعربي؟", "مدرسة"),
    ],
    "لوخيروك": [
        ("لو خيروك: تسافر للماضي لو للمستقبل؟ اكتب اختيارك.", None),
        ("لو خيروك: تعيش قرب البحر لو الجبال؟ اكتب اختيارك.", None),
        ("لو خيروك: تقرأ الأفكار لو تتكلم كل اللغات؟ اكتب اختيارك.", None),
    ],
    "صراحه": [
        ("سؤال صراحة: شنو أكثر مهارة تتمنى تتعلمها؟", None),
        ("سؤال صراحة: شنو أفضل ذكرى عندك؟", None),
        ("سؤال صراحة: شنو الشيء اللي يضحكك بسرعة؟", None),
    ],
    "اعلام": [
        ("أي دولة علمها أحمر وأبيض وفي وسطه ورقة أرز خضراء؟", "لبنان"),
        ("أي دولة علمها أبيض وأحمر مع دائرة حمراء في الوسط؟", "اليابان"),
        ("أي دولة علمها أخضر وأبيض وأسود مع مثلث أحمر؟", "فلسطين"),
    ],
    "عواصم": [
        ("ما عاصمة العراق؟", "بغداد"),
        ("ما عاصمة فرنسا؟", "باريس"),
        ("ما عاصمة مصر؟", "القاهرة"),
        ("ما عاصمة السعودية؟", "الرياض"),
    ],
    "عربي": [
        ("ما جمع كلمة كتاب؟", "كتب"),
        ("ما مفرد كلمة أقلام؟", "قلم"),
        ("ما جمع كلمة مدينة؟", "مدن"),
    ],
    "دين": [
        ("كم عدد أركان الإسلام؟", "5"),
        ("ما اسم كتاب المسلمين المقدس؟", "القرآن"),
        ("كم عدد الصلوات المفروضة في اليوم؟", "5"),
    ],
    "سيارات": [
        ("شركة سيارات شعارها نجمة ثلاثية داخل دائرة؟", "مرسيدس"),
        ("شركة سيارات شعارها أربع حلقات؟", "أودي"),
        ("شركة سيارات يابانية شعارها حرف H؟", "هوندا"),
    ],
    "ايموجي": [
        ("خمن التعبير: 😂", "ضحك"),
        ("خمن الشيء: 🌧️", "مطر"),
        ("خمن الشيء: 🍎", "تفاحة"),
        ("خمن الشيء: 🚗", "سيارة"),
    ],
    "كت": [
        ("كت تويت: اكتب جملة مضحكة عن الامتحانات 😂", None),
        ("كت تويت: صف يومك بثلاث كلمات.", None),
        ("كت تويت: شنو أول شي تسويه إذا عطلوك أسبوع؟", None),
    ],
    "نشط عقلك": [
        ("إذا عندك 3 تفاحات وأخذت اثنتين، كم تفاحة عندك؟", "2"),
        ("ما العدد التالي: 2، 4، 6، 8، ؟", "10"),
        ("كم شهر بالسنة؟", "12"),
    ],
    "المليون": [
        ("سؤال المليون: ما عاصمة اليابان؟ أ) بكين ب) طوكيو ج) سيول", "ب"),
        ("سؤال المليون: كم دقيقة في الساعة؟ أ) 30 ب) 60 ج) 100", "ب"),
        ("سؤال المليون: أي حيوان يُلقب بسفينة الصحراء؟ أ) حصان ب) جمل ج) أسد", "ب"),
    ],
}

ALIASES = {
    "المختلف": "المختلف", "امثله": "امثله", "أمثله": "امثله", "امثلة": "امثله", "أمثلة": "امثله",
    "العكس": "العكس", "حزوره": "حزوره", "حزورة": "حزوره", "معاني": "معاني", "بات": "امثله",
    "خمن": "ايموجي", "ترتيب": "ترتيب", "سمايلات": "ايموجي", "اسئله": "اسئله", "أسئلة": "اسئله",
    "اسالني": "اسالني", "اسألني": "اسالني", "لغز": "لغز", "رياضيات": "رياضيات",
    "انكليزي": "انكليزي", "إنكليزي": "انكليزي", "كت": "كت", "كت تويت": "كت", "لو خيروك": "لوخيروك",
    "صراحه": "صراحه", "صراحة": "صراحه", "اعلام": "اعلام", "أعلام": "اعلام", "عواصم": "عواصم",
    "كلمات": "كلمات", "كلمه": "كلمات", "كلمة": "كلمات", "الحظ": "الحظ", "حظي": "حظي", "عربي": "عربي", "دين": "دين", "فكك": "فكك",
    "حجره": "حجره", "حجرة": "حجره", "صور": "صور", "سيارات": "سيارات", "ايموجي": "ايموجي",
    "إيموجي": "ايموجي", "اغاني": "اغاني", "أغاني": "اغاني", "تحدي": "تحدي", "ارقام": "ارقام",
    "أرقام": "ارقام", "رقم": "ارقام", "المليون": "المليون", "نشط عقلك": "نشط عقلك",
}
MENU = (
    "⌔︙قائمــة ألــعاب حــارس المهني 🎮\n"
    "—————————————\n"
    "⌔︙المختلف — لعبة المختلف\n⌔︙امثله — لعبة الأمثلة\n⌔︙العكس — عكس الكلمة\n"
    "⌔︙حزوره — لعبة الحزورة\n⌔︙معاني — لعبة المعاني\n⌔︙بات — تحدي الأمثلة\n"
    "⌔︙خمن — لعبة التخمين\n⌔︙ترتيب — ترتيب الحروف\n⌔︙سمايلات — خمن الإيموجي\n"
    "⌔︙اسئله — أسئلة منوعة\n⌔︙اسالني — أسئلة عامة\n⌔︙لغز — ألغاز الذكاء\n"
    "⌔︙روليت — اختيار عشوائي (اكتب روليت)\n⌔︙رياضيات — مسائل رياضية\n⌔︙انكليزي — معاني الكلمات\n"
    "⌔︙كت تويت / كت — أسئلة ترفيهية\n⌔︙لو خيروك — لعبة لو خيروك\n⌔︙صراحه — لعبة الصراحة\n"
    "⌔︙اعلام — أعلام الدول\n⌔︙عواصم — عواصم الدول\n⌔︙كلمات — كتابة الكلمات\n"
    "⌔︙الحظ — لعبة الحظ\n⌔︙حظي — ربح أو خسارة\n⌔︙عربي — مفرد وجمع\n"
    "⌔︙دين — أسئلة دينية\n⌔︙فكك — تفكيك الحروف\n⌔︙حجره — حجر ورق مقص\n"
    "⌔︙صور — لعبة الصور (غير مفعلة بعد)\n⌔︙سيارات — أنواع السيارات\n⌔︙ايموجي — اسم الإيموجي\n"
    "⌔︙اغاني — خمن الفنان (أسئلة نصية مؤقتاً)\n⌔︙تحدي — تحدي عشوائي\n"
    "⌔︙لعبة xo — غير مفعلة بعد\n⌔︙رقم / ارقام — أرقام عشوائية\n⌔︙المليون — من سيربح المليون\n"
    "⌔︙نشط عقلك — أسئلة تنشيط العقل\n"
    "—————————————\n⌔︙نقاطي — عرض نقاطك\n⌔︙بيع نقاطي + العدد — استبدال كل نقطة بـ50 رسالة نشاط محتسبة من البوت."
)

def load_data():
    global DATA
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            DATA.update(loaded)
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not load games data")

def save_data():
    try:
        with DATA_FILE.open("w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Could not save games data")

def _key(chat_id, user_id):
    return f"{chat_id}:{user_id}"

def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower()).replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")

def _award(chat_id, user_id, amount=1):
    key = _key(chat_id, user_id)
    DATA["points"][key] = int(DATA["points"].get(key, 0)) + amount
    save_data()

def _start_quiz(chat_id, user, game):
    if game == "ترتيب":
        word, answer = random.choice([("باتك", "كتاب"), ("ملق", "قلم"), ("ةسردم", "مدرسة")])
        question = f"🔤 رتّب الحروف لتكوين كلمة صحيحة: {word}"
    elif game == "كلمات":
        word, answer = random.choice([("م", "م"), ("ك", "ك"), ("س", "س")])
        question = f"✍️ اكتب كلمة تبدأ بحرف: {word}"
        answer = None
    elif game == "فكك":
        word, answer = random.choice([("م د ر س ة", "مدرسة"), ("ك ت ا ب", "كتاب"), ("ق ل م", "قلم")])
        question = f"🧩 اجمع الحروف لتكوين كلمة: {word}"
    elif game == "حجره":
        answer = random.choice(["حجر", "ورق", "مقص"])
        question = "✊ اختر: حجر أو ورق أو مقص"
        answer = None
    elif game == "اغاني":
        question, answer = random.choice([("خمن الفنان: أغنية «أنت معلم» (اكتب اسم الفنان)", "سعد لمجرد"), ("خمن الفنان: أغنية «بشرة خير»", "حسين الجسمي")])
    elif game == "صور":
        question, answer = "لعبة الصور تحتاج إضافة ملفات صور أولاً؛ هذه الميزة غير مفعلة حالياً.", None
    else:
        question, answer = random.choice(QUIZZES.get(game, QUIZZES["اسئله"]))
    DATA["pending"][_key(chat_id, user.id)] = {"game": game, "answer": answer, "started": time.time()}
    save_data()
    return question

async def handle_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not chat or not user or user.is_bot or chat.type not in ("group", "supergroup") or not msg.text:
        return
    raw = msg.text.strip()
    normalized = _norm(raw)
    if normalized in {"الالعاب", "الالعاب", "قائمة الالعاب", "قائمه الالعاب", "العاب"}:
        await msg.reply_text(MENU)
        return
    if normalized == "نقاطي":
        pts = int(DATA["points"].get(_key(chat.id, user.id), 0))
        count = int(DATA["messages"].get(_key(chat.id, user.id), 0))
        await msg.reply_text(f"⭐ نقاطك: {pts}\n💬 رسائلك المحتسبة: {count}")
        return
    if normalized.startswith("بيع نقاطي"):
        parts = raw.split()
        if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) < 1:
            await msg.reply_text("اكتب: بيع نقاطي 2 — كل نقطة تُستبدل بـ50 رسالة نشاط محتسبة.")
            return
        amount = int(parts[2])
        key = _key(chat.id, user.id)
        points = int(DATA["points"].get(key, 0))
        if amount > points:
            await msg.reply_text(f"ما عندك نقاط كافية. رصيدك الحالي {points} نقطة.")
            return
        DATA["points"][key] = points - amount
        DATA["messages"][key] = int(DATA["messages"].get(key, 0)) + amount * 50
        save_data()
        await msg.reply_text(f"✅ استبدلت {amount} نقطة مقابل {amount * 50} رسالة نشاط محتسبة.")
        return
    # Any ordinary group message counts toward the activity counter.
    key = _key(chat.id, user.id)
    DATA["messages"][key] = int(DATA["messages"].get(key, 0)) + 1
    save_data()

    # Resolve an answer to the active game first.
    pending = DATA["pending"].get(key)
    if pending and pending.get("answer") is not None:
        if _norm(raw) == _norm(str(pending["answer"])):
            DATA["pending"].pop(key, None)
            _award(chat.id, user.id, 1)
            await msg.reply_text(f"✅ صحيح يا {user.first_name}! ربحت نقطة ⭐")
            return
    if normalized in {"روليت", "الروليت"}:
        try:
            members = await context.bot.get_chat_administrators(chat.id)
            # Telegram doesn't expose all group members through Bot API; select from recent participants instead.
            candidates = [str(uid) for uid in DATA["messages"] if uid.startswith(str(chat.id) + ":")]
            if candidates:
                chosen = random.choice(candidates).split(":", 1)[1]
                await msg.reply_text(f"🎯 روليت عشوائي: المستخدم ID {chosen}")
            else:
                await msg.reply_text("ما عندي مشاركين مسجلين بعد. خلّوا الأعضاء يكتبون بالكروب.")
        except Exception:
            await msg.reply_text("تعذر تشغيل الروليت حالياً.")
        return
    if normalized in {"الحظ", "حظي"}:
        if random.random() < 0.5:
            _award(chat.id, user.id, 1)
            await msg.reply_text("🍀 حظك حلو! ربحت نقطة ⭐")
        else:
            key = _key(chat.id, user.id)
            DATA["points"][key] = max(0, int(DATA["points"].get(key, 0)) - 1)
            save_data()
            await msg.reply_text("🙃 حظ أوفر! خسرت نقطة إذا كان عندك نقاط.")
        return
    if normalized in {"تحدي", "اسالني"}:
        game = "اسالني" if normalized == "اسالني" else random.choice(["صراحه", "رياضيات", "عواصم", "حزوره"])
        await msg.reply_text(_start_quiz(chat.id, user, game))
        return
    if normalized in {"حجره", "حجر", "ورق", "مقص"}:
        choice = random.choice(["حجر", "ورق", "مقص"])
        if normalized == choice:
            result = "تعادل!"
        elif (normalized, choice) in {("حجر", "مقص"), ("ورق", "حجر"), ("مقص", "ورق")}:
            _award(chat.id, user.id, 1)
            result = "فزت وربحت نقطة ⭐"
        else:
            result = "خسرت هالمرة."
        await msg.reply_text(f"✊ اختيار البوت: {choice}\n{result}")
        return
    game = ALIASES.get(raw) or ALIASES.get(normalized)
    if game:
        await msg.reply_text(_start_quiz(chat.id, user, game))
        return
    # Free-response games: accept one response, award participation points.
    if pending and pending.get("answer") is None:
        DATA["pending"].pop(key, None)
        _award(chat.id, user.id, 1)
        await msg.reply_text("👏 مشاركة حلوة! ربحت نقطة ⭐")
        return

def register_games_handlers(app):
    load_data()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_games), group=-1)
