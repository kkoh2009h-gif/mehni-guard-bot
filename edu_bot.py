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
DATA = {"teachers": [], "booklets": [], "books": [], "info": [], "admissions": [], "admins": []}
PENDING = {}

def load():
    global DATA
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            DATA.update({k: d.get(k, []) for k in DATA})
            if "resources" in d and "booklets" not in d:
                DATA["booklets"] = d.get("resources", [])
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
    return kb([("👨‍🏫 المدرسون","browse:teachers"),("📝 الملازم","browse:booklets"),("📚 الكتب","browse:books"),("💡 معلومات الدراسة","browse:info"),("🎓 قبولات الجامعات","admissions:start"),("ℹ️ المساعدة","help")])
def panel():
    return kb([("➕ إضافة مدرس","add:teacher"),("➕ إضافة ملزمة","add:booklet"),("➕ إضافة كتاب","add:book"),("➕ إضافة معلومة","add:info"),("➕ إضافة سجل قبول","add:admission"),("📋 عرض المحتوى","list"),("👥 إدارة المشرفين","admins")])

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
        await q.edit_message_text("اختار المدرسين أو الكتب أو قبولات الجامعات. نتائج القبول تعتمد على البيانات الرسمية المتاحة. للإدارة استخدم /admin.", reply_markup=home()); return
    if d=="admissions:start":
        await q.message.reply_text("🎓 قبولات الجامعات 2026-2027\\nدليل الطالب الحكومي: https://dirasat.mohesr.gov.iq/download-center.html?lang=ar\\nدليل الأهلي: https://cent.mohesr.gov.iq/Ahly2026v3.pdf\\nنتائج أوائل المهني: https://mohesr.gov.iq/ar/post/التعليم-تعلن-نتائج-قبول-الطلبة-الأوائل-من-خريجي-الدراسة-المهنية-2026-10-22-13\\n\\nتگدر تبحث بالسجلات اللي يضيفها الأدمن أو تراجع الدليل الرسمي.").reply_markup if False else None
        PENDING[uid]={"kind":"admission_search","step":0,"data":{}}
        await q.message.reply_text("حتى نطابق القبولات، اكتب اختصاصك المهني:")
        return
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
        key=mode if mode in ("teachers", "booklets", "books", "info") else "booklets"
        items=[x for x in DATA[key] if x.get("department")==dept and x.get("stage")==stage]
        if not items:
            await q.edit_message_text("📭 ماكو محتوى مضاف لهذا القسم والمرحلة بعد.", reply_markup=kb([("⬅️ الرئيسية","home")])); return
        section_names={"teachers":"المدرسون","booklets":"الملازم","books":"الكتب","info":"معلومات الدراسة"}
        lines=[f"{section_names.get(mode, mode)} — {dept} — {stage}"]; rows=[]
        for x in items[:30]:
            title=x.get("name") if mode=="teachers" else x.get("title", "معلومة" if mode=="info" else "محتوى")
            lines.append(f"• {title}"+(f" — {x.get('subject','')}" if x.get("subject") else ""))
            if x.get("url"): rows.append((f"فتح: {title[:30]}", "url:"+str(x["id"])))
        rows += [("⬅️ رجوع",f"browse:{mode}"),("🏠 الرئيسية","home")]
        await q.edit_message_text("\n".join(lines)[:3900], reply_markup=kb(rows)); return
    if d.startswith("url:"):
        itemid=int(d.split(":")[1]); item=next((x for k in ("teachers","booklets","books","info") for x in DATA[k] if x.get("id")==itemid),None)
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
            for section, label in (("booklets","الملازم"),("books","الكتب"),("info","المعلومات")):
                lines += [f"\n{label}:"] + [f"#{x['id']} {x.get('title')} | {x.get('department')} | {x.get('stage')}" for x in DATA[section]]
            await q.message.reply_text("\n".join(lines)[:4000] if len(lines)>2 else "المكتبة فارغة."); return
        kind=d.split(":")[1]; PENDING[uid]={"kind":kind,"step":0,"data":{}}
        prompts={"teacher":"اكتب اسم المدرس:","booklet":"اكتب عنوان الملزمة:","book":"اكتب عنوان الكتاب:","info":"اكتب عنوان المعلومة:","admission":"اكتب اسم الجامعة أو الكلية:"}
        await q.message.reply_text(prompts.get(kind,"اكتب العنوان:"))
async def flow(u,c):
    uid=u.effective_user.id; msg=u.effective_message
    if uid not in PENDING or not msg.text: return
    f=PENDING[uid]; x=f["data"]; step=f["step"]; kind=f["kind"]; body=msg.text.strip()
    if kind=="admission_search":
        if step==0: x["department"]=body; f["step"]=1; await msg.reply_text("نوع الدراسة: صباحي أو مسائي أو أهلي؟"); return
        if step==1:
            v=body.lower()
            if not any(t in v for t in ("صباح","مساء","اهلي","أهلي","اهلية","أهلية")): await msg.reply_text("اكتب صباحي أو مسائي أو أهلي."); return
            x["mode"]="صباحي" if "صباح" in v else ("مسائي" if "مساء" in v else "أهلي"); f["step"]=2; await msg.reply_text("اكتب المحافظة:"); return
        if step==2: x["governorate"]=body; f["step"]=3; await msg.reply_text("اكتب معدلك من 0 إلى 100:"); return
        try: x["average"]=float(body.replace(",",".")) 
        except ValueError: await msg.reply_text("اكتب المعدل بالأرقام مثل 62."); return
        PENDING.pop(uid,None)
        found=[]
        for a in DATA.get("admissions",[]):
            try: mn=float(a.get("min_average"))
            except (TypeError,ValueError): continue
            br=a.get("department","")
            if a.get("mode")==x["mode"] and a.get("governorate","").casefold()==x["governorate"].casefold() and x["average"]>=mn and (not br or br.casefold()=="الكل" or br.casefold() in x["department"].casefold() or x["department"].casefold() in br.casefold()):
                found.append(a)
        if found:
            lines=["نتائج مطابقة للسجلات التي أدخلها الأدمن (ليست ضمان قبول):"]
            for a in found[:15]: lines.append(f"\\n{a.get('college')} — {a.get('major')}\\nالمحافظة: {a.get('governorate')} | الدراسة: {a.get('mode')}\\nأقل معدل مسجل: {a.get('min_average')}\\nالمصدر: {a.get('url','غير مضاف')}")
            await msg.reply_text("\\n".join(lines)[:3900],reply_markup=home())
        else:
            await msg.reply_text("ماكو سجل قبول مدخل ومطابق حالياً؛ هذا لا يعني أنك غير مقبول. راجع الدليل الرسمي: https://dirasat.mohesr.gov.iq/download-center.html?lang=ar\\nودليل الأهلي: https://cent.mohesr.gov.iq/Ahly2026v3.pdf",reply_markup=home())
        return
    if not admin(uid): return
    if kind=="admission":
        fields=[("college","اكتب اسم الجامعة/الكلية:"),("major","اكتب الاختصاص:"),("department","اكتب الفرع المهني المقبول أو اكتب الكل:"),("mode","اكتب صباحي أو مسائي أو أهلي:"),("governorate","اكتب المحافظة:"),("min_average","أقل معدل حسب المصدر (رقم):"),("url","رابط المصدر الرسمي https:// أو اكتب لا:")]
        key,prompt=fields[step]
        if key=="min_average":
            try: x[key]=float(body.replace(",",".")) 
            except ValueError: await msg.reply_text("اكتب المعدل كرقم."); return
            if not 0<=x[key]<=100: await msg.reply_text("المعدل بين 0 و100."); return
        elif key=="mode":
            v=body.lower()
            if not any(t in v for t in ("صباح","مساء","اهلي","أهلي","اهلية","أهلية")): await msg.reply_text("اكتب صباحي أو مسائي أو أهلي."); return
            x[key]="صباحي" if "صباح" in v else ("مسائي" if "مساء" in v else "أهلي")
        elif key=="department": x[key]="" if body in ("الكل","كل") else body
        elif key=="url":
            if body.lower() in ("لا","بدون","لا يوجد"): x[key]=""
            elif body.startswith("https://"): x[key]=body
            else: await msg.reply_text("الرابط لازم يبدأ https:// أو اكتب لا."); return
        else: x[key]=body
        f["step"]+=1
        if f["step"]<len(fields): await msg.reply_text(fields[f["step"]][1]); return
        x["id"]=max([z.get("id",0) for k in ("teachers","booklets","books","info","admissions") for z in DATA.get(k,[])]+[0])+1
        DATA["admissions"].append(x.copy()); save(); PENDING.pop(uid,None)
        await msg.reply_text("تم حفظ سجل القبول. تأكد أن المعدل من مصدر رسمي.",reply_markup=panel()); return
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
        x["url"]=body; x["id"]=max([z.get("id",0) for k in ("teachers","booklets","books","info") for z in DATA[k]]+[0])+1
        target={"teacher":"teachers","booklet":"booklets","book":"books","info":"info"}[kind]
        DATA[target].append(x.copy()); save(); PENDING.pop(uid,None)
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
