import json, logging, os
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mehni-education")
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("BOT_OWNER_ID", "7996478878"))
FILE = Path("education_data.json")
DEPARTMENTS = ["صيانة وتجميع الحاسوب", "الشبكات", "الكهرباء", "الميكانيك والسيارات", "التجاري", "الزراعي", "السياحة والفندقة", "الفنون التطبيقية", "البناء والإنشاءات", "أقسام مهنية أخرى"]
STAGES = ["الأول المهني", "الثاني المهني", "الثالث المهني"]
DATA = {"teachers": [], "resources": [], "admins": []}
PENDING = {}

def load():
    global DATA
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            DATA.update({k: d.get(k, []) for k in DATA})
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Failed to load data")

def save():
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DATA, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE)

def owner(uid): return uid == OWNER_ID
def admin(uid): return owner(uid) or uid in [int(x) for x in DATA["admins"]]
def kb(rows): return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for t,d in rows])
def home():
    return kb([("👨‍🏫 المدرسون","browse:teachers"),("📚 الكتب والملازم","browse:resources"),("ℹ️ المساعدة","help")])
def panel():
    return kb([("➕ إضافة مدرس","add:teacher"),("➕ إضافة كتاب/ملزمة","add:resource"),("📋 عرض المحتوى","list"),("👥 إدارة المشرفين","admins")])

async def start(u,c):
    await u.effective_message.reply_text("🎓 أهلاً بيك ببوت المهني التعليمي\nاختار المدرسين أو الكتب والملازم.", reply_markup=home())
async def myid(u,c):
    await u.effective_message.reply_text(f"معرّفك الرقمي: {u.effective_user.id}")
async def adminpanel(u,c):
    if not admin(u.effective_user.id):
        await u.effective_message.reply_text("⛔ لوحة الإدارة للمالك والمشرفين فقط.")
        return
    await u.effective_message.reply_text("🛠 إدارة المحتوى", reply_markup=panel())
async def addadmin(u,c):
    if not owner(u.effective_user.id):
        await u.effective_message.reply_text("هذا الأمر للمالك فقط."); return
    if not c.args or not c.args[0].isdigit():
        await u.effective_message.reply_text("الاستخدام: /admin_add USER_ID"); return
    uid=int(c.args[0])
    if uid not in DATA["admins"] and uid != OWNER_ID: DATA["admins"].append(uid); save()
    await u.effective_message.reply_text("✅ تمت إضافة المشرف.")
async def deladmin(u,c):
    if not owner(u.effective_user.id):
        await u.effective_message.reply_text("هذا الأمر للمالك فقط."); return
    if not c.args or not c.args[0].isdigit():
        await u.effective_message.reply_text("الاستخدام: /admin_remove USER_ID"); return
    DATA["admins"]=[x for x in DATA["admins"] if int(x)!=int(c.args[0])]; save()
    await u.effective_message.reply_text("تم حذف صلاحية المشرف.")
async def cb(u,c):
    q=u.callback_query; await q.answer(); d=q.data or ""; uid=q.from_user.id
    if d=="help":
        await q.edit_message_text("اختار المدرسين أو الكتب، ثم القسم والمرحلة. للإدارة استخدم /admin.", reply_markup=home()); return
    if d=="home":
        await q.edit_message_text("🎓 بوت المهني التعليمي", reply_markup=home()); return
    if d.startswith("browse:"):
        mode=d.split(":")[1]
        rows=[(x,f"dept:{mode}:{i}") for i,x in enumerate(DEPARTMENTS)]
        rows.append(("🏠 الرئيسية","home"))
        await q.edit_message_text("اختار القسم:", reply_markup=kb(rows)); return
    if d.startswith("dept:"):
        _,mode,di=d.split(":")
        rows=[(x,f"stage:{mode}:{di}:{i}") for i,x in enumerate(STAGES)]
        rows.append(("⬅️ رجوع",f"browse:{mode}"))
        await q.edit_message_text("اختار المرحلة:", reply_markup=kb(rows)); return
    if d.startswith("stage:"):
        _,mode,di,si=d.split(":"); dept=DEPARTMENTS[int(di)]; stage=STAGES[int(si)]
        key="teachers" if mode=="teachers" else "resources"
        items=[x for x in DATA[key] if x.get("department")==dept and x.get("stage")==stage]
        if not items:
            await q.edit_message_text("📭 ماكو محتوى مضاف لهذا القسم والمرحلة بعد.", reply_markup=kb([("⬅️ الرئيسية","home")])); return
        lines=[f"📚 {dept} — {stage}"]; rows=[]
        for x in items[:30]:
            title=x.get("name") if mode=="teachers" else x.get("title","كتاب")
            lines.append(f"• {title}"+(f" — {x.get('subject','')}" if x.get("subject") else ""))
            if x.get("url"): rows.append((f"فتح: {title[:30]}", "url:"+str(x["id"])))
        rows += [("⬅️ رجوع",f"browse:{mode}"),("🏠 الرئيسية","home")]
        await q.edit_message_text("\n".join(lines)[:3900], reply_markup=kb(rows)); return
    if d.startswith("url:"):
        itemid=int(d.split(":")[1]); item=next((x for k in ("teachers","resources") for x in DATA[k] if x.get("id")==itemid),None)
        if item and item.get("url"):
            await q.message.reply_text(item["url"])
        return
    if d.startswith(("add:","list","admins")):
        if not admin(uid):
            await q.answer("ما عندك صلاحية.", show_alert=True); return
        if d=="admins":
            if not owner(uid): await q.message.reply_text("إدارة المشرفين للمالك فقط."); return
            await q.message.reply_text("أضف مشرف: /admin_add USER_ID\nاحذف مشرف: /admin_remove USER_ID\nالشخص يكتب /id حتى تعرف رقمه."); return
        if d=="list":
            lines=["المدرسون:"]+[f"#{x['id']} {x.get('name')} | {x.get('department')} | {x.get('stage')}" for x in DATA["teachers"]]
            lines+=["\nالكتب والملازم:"]+[f"#{x['id']} {x.get('title')} | {x.get('department')} | {x.get('stage')}" for x in DATA["resources"]]
            await q.message.reply_text("\n".join(lines)[:4000] if len(lines)>2 else "المكتبة فارغة."); return
        kind=d.split(":")[1]; PENDING[uid]={"kind":kind,"step":0,"data":{}}
        await q.message.reply_text("اكتب اسم المدرس:" if kind=="teacher" else "اكتب عنوان الكتاب أو الملزمة:")
async def flow(u,c):
    uid=u.effective_user.id; msg=u.effective_message
    if not admin(uid) or uid not in PENDING or not msg.text: return
    f=PENDING[uid]; x=f["data"]; step=f["step"]; kind=f["kind"]; body=msg.text.strip()
    if step==0:
        x["name" if kind=="teacher" else "title"]=body; f["step"]=1
        await msg.reply_text("اختار رقم القسم:\n"+"\n".join(f"{i+1}. {v}" for i,v in enumerate(DEPARTMENTS)))
    elif step==1:
        if not body.isdigit() or not 1<=int(body)<=len(DEPARTMENTS): await msg.reply_text("اكتب رقم قسم صحيح."); return
        x["department"]=DEPARTMENTS[int(body)-1]; f["step"]=2
        await msg.reply_text("اختار رقم المرحلة:\n"+"\n".join(f"{i+1}. {v}" for i,v in enumerate(STAGES)))
    elif step==2:
        if not body.isdigit() or not 1<=int(body)<=len(STAGES): await msg.reply_text("اكتب رقم مرحلة صحيح."); return
        x["stage"]=STAGES[int(body)-1]; f["step"]=3; await msg.reply_text("اكتب اسم المادة:")
    elif step==3:
        x["subject"]=body; f["step"]=4; await msg.reply_text("أرسل رابط يبدأ بـ https://")
    else:
        if not body.startswith("https://"): await msg.reply_text("أرسل رابط صحيح يبدأ بـ https://"); return
        x["url"]=body; x["id"]=max([z.get("id",0) for k in ("teachers","resources") for z in DATA[k]]+[0])+1
        DATA["teachers" if kind=="teacher" else "resources"].append(x.copy()); save(); PENDING.pop(uid,None)
        await msg.reply_text("✅ تمت الإضافة بنجاح.", reply_markup=panel())
async def err(u,c): log.exception("Unhandled error", exc_info=c.error)
def main():
    if not TOKEN: raise RuntimeError("Set BOT_TOKEN in hosting environment.")
    load(); app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("id",myid)); app.add_handler(CommandHandler("admin",adminpanel))
    app.add_handler(CommandHandler("admin_add",addadmin)); app.add_handler(CommandHandler("admin_remove",deladmin))
    app.add_handler(CallbackQueryHandler(cb)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,flow))
    app.add_error_handler(err); log.info("Educational bot started"); app.run_polling(allowed_updates=Update.ALL_TYPES)
if __name__=="__main__": main()
