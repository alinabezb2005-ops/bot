"""
Бот-напоминалкин · Репетитор Котельников И.С.
Расписание берётся с платформы. Бот только уведомляет.
"""
import json, os, asyncio, re, httpx
from datetime import datetime, date, timedelta
from pathlib import Path
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, BotCommand,
                      BotCommandScopeChat, BotCommandScopeDefault)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           MessageHandler, filters, ContextTypes, ConversationHandler)

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН")
ADMIN_ID     = int(os.getenv("ADMIN_ID", "0"))
TZ_OFFSET    = int(os.getenv("TZ_OFFSET", "3"))
PLATFORM_URL = os.getenv("PLATFORM_URL", "https://web-production-aa92f.up.railway.app")
PLATFORM_TOKEN = os.getenv("PLATFORM_ADMIN_TOKEN", "")  # пароль репетитора на платформе
ADMIN_PLATFORM_URL = PLATFORM_URL + "/admin"
REMIND_HOUR  = int(os.getenv("REMIND_HOUR", "9"))
TUTOR_TG     = "@grandvillakotel"
TUTOR_PHONE  = "+7 906 585 7200"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
STUDENTS_FILE = DATA_DIR / "students.json"

# ── ХРАНИЛИЩЕ ─────────────────────────────────────────────────────────────────
# students.json: {"Аня Иванова": {"tg_ids": [123, 456], "platform_name": "Аня Иванова"}}

def load(p, d):
    try: return json.load(open(p, encoding="utf-8")) if p.exists() else d
    except: return d

def save(p, d):
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def get_students():  return load(STUDENTS_FILE, {})
def save_students(d): save(STUDENTS_FILE, d)

def now_local():
    return datetime.utcnow() + timedelta(hours=TZ_OFFSET)

def today_iso():
    return now_local().strftime("%Y-%m-%d")

MONTHS_RU = {
    "января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
    "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12,
    "янв":1,"фев":2,"мар":3,"апр":4,"май":5,"июн":6,
    "июл":7,"авг":8,"сен":9,"окт":10,"ноя":11,"дек":12,
}

def parse_date(s):
    if not s: return None
    s = s.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        try: return date.fromisoformat(s[:10])
        except: pass
    m = re.match(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", s)
    if m:
        try: return date(int(m.group(3) or now_local().year), int(m.group(2)), int(m.group(1)))
        except: pass
    m = re.match(r"(\d{1,2})\s+(\S+)(?:\s+(\d{4}))?", s.lower())
    if m:
        mo = MONTHS_RU.get(m.group(2).rstrip(".,"))
        if mo:
            try: return date(int(m.group(3) or now_local().year), mo, int(m.group(1)))
            except: pass
    # "Понедельник, 22 августа" — берём дату после запятой
    if "," in s:
        return parse_date(s.split(",",1)[1].strip())
    return None

def parse_time(s):
    m = re.search(r"(\d{1,2}):(\d{2})", s or "")
    if m: return int(m.group(1)), int(m.group(2))
    return None, None

def fmt_date(d):
    if not d: return ""
    if isinstance(d, str):
        d = parse_date(d)
    if not d: return ""
    days = ["пн","вт","ср","чт","пт","сб","вс"]
    months = ["янв","фев","мар","апр","мая","июн","июл","авг","сен","окт","ноя","дек"]
    return f"{days[d.weekday()]} {d.day} {months[d.month-1]}"

def is_admin(update): return update.effective_user.id == ADMIN_ID

def get_student_by_tg(uid):
    """Возвращает (name, student_data) по Telegram ID"""
    for name, st in get_students().items():
        tg_ids = st.get("tg_ids", [])
        if isinstance(tg_ids, int): tg_ids = [tg_ids]
        if uid in tg_ids:
            return name, st
    return None, None

# ── ПЛАТФОРМА API ─────────────────────────────────────────────────────────────

async def fetch_platform_students():
    """Подгружает всех учеников с расписанием с платформы"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Сначала логинимся чтобы получить токен
            r = await client.post(f"{PLATFORM_URL}/api/admin/login",
                json={"password": PLATFORM_TOKEN},
                headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                return {}
            token = r.json().get("token", "")
            # Получаем учеников
            r2 = await client.get(f"{PLATFORM_URL}/api/students",
                headers={"X-Admin-Token": token})
            if r2.status_code != 200:
                return {}
            students_list = r2.json()
            return {s["name"]: s for s in students_list}
    except Exception as e:
        print(f"Platform API error: {e}")
        return {}

async def get_student_schedule(platform_name):
    """Возвращает расписание ученика с платформы"""
    platform_data = await fetch_platform_students()
    st = platform_data.get(platform_name, {})
    return st.get("schedule", [])

def lesson_from_platform(lesson, student_name):
    """Преобразует занятие с платформы в удобный формат"""
    day_str = lesson.get("day", "")
    time_str = lesson.get("time", "").split("–")[0].strip().split("-")[0].strip()
    d = parse_date(day_str)
    h, mi = parse_time(time_str)
    if not d or h is None:
        return None
    return {
        "student": student_name,
        "date": d.isoformat(),
        "time": f"{h:02d}:{mi:02d}",
        "zoom": lesson.get("zoom_link", ""),
        "status": lesson.get("status", "planned"),
        "subject": lesson.get("name", ""),
        "id": lesson.get("id", 0)
    }

async def get_all_upcoming_lessons():
    """Все ближайшие занятия всех учеников из бота (данные с платформы)"""
    platform_data = await fetch_platform_students()
    bot_students = get_students()
    today = now_local().date()
    lessons = []
    for bot_name, bot_st in bot_students.items():
        pname = bot_st.get("platform_name", bot_name)
        pdata = platform_data.get(pname, {})
        for lesson in pdata.get("schedule", []):
            converted = lesson_from_platform(lesson, bot_name)
            if not converted: continue
            d = parse_date(converted["date"])
            if not d or d < today: continue
            if converted["status"] not in ("planned", "active", ""):
                continue
            lessons.append(converted)
    lessons.sort(key=lambda l: (l["date"], l["time"]))
    return lessons

async def get_todays_lessons():
    """Занятия только сегодня"""
    today = today_iso()
    all_l = await get_all_upcoming_lessons()
    return [l for l in all_l if l["date"] == today]

# ── КЛАВИАТУРА РЕПЕТИТОРА ─────────────────────────────────────────────────────

ADMIN_REPLY_KB = ReplyKeyboardMarkup([
    ["📅 Сегодня",        "📆 Неделя"],
    ["📚 Напомнить о ДЗ", "👥 Ученики"],
], resize_keyboard=True)

# ── СОСТОЯНИЯ ─────────────────────────────────────────────────────────────────
ADD_STUDENT_NAME, ADD_STUDENT_PLATFORM, ADD_STUDENT_TG, ADD_STUDENT_MORE_TG = range(4)

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        await update.message.reply_text(
            "👋 Привет, Илья!\n\n"
            "Я показываю расписание с платформы и напоминаю о занятиях.\n"
            "За 4 часа — напомню ученику, за 15 минут — нам обоим.\n\n"
            "Используй кнопки внизу 👇",
            reply_markup=ADMIN_REPLY_KB)
        return

    name, st = get_student_by_tg(uid)
    if name:
        pname = st.get("platform_name", name)
        schedule = await get_student_schedule(pname)
        today = now_local().date()
        upcoming = []
        for lesson in schedule:
            conv = lesson_from_platform(lesson, name)
            if not conv: continue
            d = parse_date(conv["date"])
            if d and d >= today and conv["status"] in ("planned","active",""):
                upcoming.append(conv)
        upcoming.sort(key=lambda l: (l["date"], l["time"]))
        next_l = upcoming[0] if upcoming else None
        fname = name.split()[0]
        text = f"👋 Привет, {fname}!\n\n"
        if next_l:
            text += (f"Ближайшее занятие:\n"
                     f"📅 {fmt_date(next_l['date'])}\n"
                     f"🕐 {next_l['time']} (МСК)\n\n"
                     f"Все материалы и задания — на платформе:")
        else:
            text += "Ближайших занятий пока нет."
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📅 Мои занятия", callback_data="my_lessons"),
            InlineKeyboardButton("🖥 Платформа", url=PLATFORM_URL)
        ]])
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(
            "👋 Привет!\n\n"
            "Ты ещё не подключён к боту.\n"
            f"Скажи репетитору свой Telegram ID:\n\n"
            f"`{uid}`",
            parse_mode="Markdown")

# ── КОМАНДЫ РЕПЕТИТОРА ────────────────────────────────────────────────────────

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    lessons = await get_todays_lessons()
    date_str = now_local().strftime("%d.%m.%Y")
    if not lessons:
        await update.message.reply_text(
            f"📅 Сегодня ({date_str}) занятий нет.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📆 Неделя", callback_data="show_week")
            ]]))
        return
    text = f"📅 *Занятия сегодня, {date_str}:*\n\n"
    for l in lessons:
        text += f"🕐 {l['time']} — *{l['student']}*\n"
        if l.get("subject"): text += f"   📚 {l['subject']}\n"
    text += f"\n🖥 [Открыть платформу]({ADMIN_PLATFORM_URL})"
    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📆 Неделя", callback_data="show_week"),
            InlineKeyboardButton("🖥 Платформа", url=ADMIN_PLATFORM_URL)
        ]]), disable_web_page_preview=True)

async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    today = now_local().date()
    week_end = today + timedelta(days=7)
    all_l = await get_all_upcoming_lessons()
    lessons = [l for l in all_l
               if (d:=parse_date(l["date"])) and today <= d <= week_end]
    if not lessons:
        await (update.message or update.callback_query.message).reply_text(
            "📆 На ближайшую неделю занятий нет.")
        return
    text = "📆 *Расписание на неделю:*\n\n"
    cur = ""
    for l in lessons:
        d = l["date"]
        if d != cur:
            text += f"*{fmt_date(d)}*\n"; cur = d
        text += f"  🕐 {l['time']} — {l['student']}"
        if l.get("subject"): text += f" ({l['subject']})"
        text += "\n"
    text += f"\n_Занятиями управляй на платформе_\n🖥 {ADMIN_PLATFORM_URL}"
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_homework(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    students = get_students()
    if not students:
        await update.message.reply_text("Учеников нет. Добавь через /addstudent")
        return
    kb = [[InlineKeyboardButton(n, callback_data=f"hw_{n}")]
          for n in sorted(students.keys())]
    kb.append([InlineKeyboardButton("📢 Всем сразу", callback_data="hw_ALL")])
    await update.message.reply_text(
        "Кому напомнить о домашнем задании?",
        reply_markup=InlineKeyboardMarkup(kb))

async def hw_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    target = q.data.replace("hw_","")
    students = get_students()
    sent = 0
    targets = students.items() if target=="ALL" else [(target, students.get(target,{}))]
    for name, st in targets:
        tg_ids = st.get("tg_ids", [])
        if isinstance(tg_ids, int): tg_ids = [tg_ids]
        for tg_id in tg_ids:
            try:
                await ctx.bot.send_message(tg_id,
                    f"Привет, {name.split()[0]}! 📚\n\n"
                    f"Не забудь про домашнее задание — посмотри его на платформе:\n"
                    f"🖥 {PLATFORM_URL}")
                sent += 1
            except: pass
    await q.edit_message_text(f"✅ Отправлено {sent} контакт(ам).")

async def cmd_students(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    students = get_students()
    if not students:
        await update.message.reply_text("Учеников нет. Добавь через /addstudent")
        return
    text = "👥 *Ученики в боте:*\n\n"
    for name, st in students.items():
        pname = st.get("platform_name", name)
        tg_ids = st.get("tg_ids", [])
        if isinstance(tg_ids, int): tg_ids = [tg_ids]
        text += f"• *{name}*"
        if pname != name: text += f" → платформа: _{pname}_"
        text += f"\n  TG: {', '.join(str(i) for i in tg_ids)}\n"
    text += "\nУдалить ученика: /removestudent"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_removestudent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    students = get_students()
    if not students:
        await update.message.reply_text("Учеников нет.")
        return
    kb = [[InlineKeyboardButton(f"🗑 {n}", callback_data=f"remove_student_{n}")]
          for n in sorted(students.keys())]
    kb.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_action")])
    await update.message.reply_text(
        "Выбери ученика для удаления:",
        reply_markup=InlineKeyboardMarkup(kb))

# ── ДОБАВИТЬ УЧЕНИКА (диалог) ─────────────────────────────────────────────────

async def cmd_addstudent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    # Подгружаем учеников с платформы для выбора
    await update.message.reply_text("Загружаю список учеников с платформы… ⏳")
    platform_data = await fetch_platform_students()
    if not platform_data:
        await update.message.reply_text(
            "⚠️ Не удалось загрузить учеников с платформы.\n"
            "Проверь переменную PLATFORM_ADMIN_TOKEN на Railway.\n\n"
            "Введи имя ученика вручную (точно как на платформе):")
        ctx.user_data["platform_students"] = {}
        return ADD_STUDENT_NAME

    ctx.user_data["platform_students"] = platform_data
    kb = [[InlineKeyboardButton(name, callback_data=f"sp_{name}")]
          for name in sorted(platform_data.keys())]
    kb.append([InlineKeyboardButton("✍️ Ввести вручную", callback_data="sp_manual")])
    await update.message.reply_text(
        "Выбери ученика с платформы:",
        reply_markup=InlineKeyboardMarkup(kb))
    return ADD_STUDENT_PLATFORM

async def student_platform_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "sp_manual":
        await q.edit_message_text("Введи имя ученика (точно как на платформе):")
        return ADD_STUDENT_NAME
    pname = q.data.replace("sp_","")
    ctx.user_data["platform_name"] = pname
    ctx.user_data["bot_name"] = pname  # по умолчанию то же имя
    ctx.user_data["tg_ids"] = []
    await q.edit_message_text(
        f"👤 Ученик: *{pname}*\n\n"
        "Введи Telegram ID первого контакта\n"
        "(ученик должен написать боту /start и прислать свой ID):",
        parse_mode="Markdown")
    return ADD_STUDENT_TG

async def student_name_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pname = update.message.text.strip()
    ctx.user_data["platform_name"] = pname
    ctx.user_data["bot_name"] = pname
    ctx.user_data["tg_ids"] = []
    await update.message.reply_text(
        f"👤 Ученик: *{pname}*\n\n"
        "Введи Telegram ID первого контакта:",
        parse_mode="Markdown")
    return ADD_STUDENT_TG

async def student_tg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: tg_id = int(update.message.text.strip())
    except:
        await update.message.reply_text("ID должен быть числом. Попробуй ещё раз:")
        return ADD_STUDENT_TG
    ctx.user_data["tg_ids"].append(tg_id)
    kb = [[InlineKeyboardButton("➕ Добавить ещё контакт (мама/папа)", callback_data="add_more_tg"),
           InlineKeyboardButton("✅ Готово", callback_data="finish_student")]]
    await update.message.reply_text(
        f"✅ Добавлен TG: `{tg_id}`\n\n"
        "Добавить ещё один контакт для этого ученика?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))
    return ADD_STUDENT_MORE_TG

async def student_more_tg_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "add_more_tg":
        await q.edit_message_text(
            "Введи Telegram ID второго контакта:")
        return ADD_STUDENT_TG
    # finish_student
    pname = ctx.user_data["platform_name"]
    bot_name = ctx.user_data["bot_name"]
    tg_ids = ctx.user_data["tg_ids"]
    students = get_students()
    students[bot_name] = {
        "tg_ids": tg_ids,
        "platform_name": pname
    }
    save_students(students)
    ctx.user_data.clear()
    tg_list = ", ".join(f"`{i}`" for i in tg_ids)
    await q.edit_message_text(
        f"✅ Ученик добавлен!\n\n"
        f"👤 *{bot_name}*\n"
        f"🔗 Платформа: _{pname}_\n"
        f"📱 TG: {tg_list}",
        parse_mode="Markdown")
    # Уведомляем всех контактов
    for tg_id in tg_ids:
        try:
            await ctx.bot.send_message(tg_id,
                f"👋 Привет, {bot_name.split()[0]}!\n\n"
                f"Репетитор Илья Котельников подключил тебя к боту.\n"
                f"Теперь сюда будут приходить напоминания о занятиях 🔔\n\n"
                f"🖥 Платформа: {PLATFORM_URL}")
        except: pass
    return ConversationHandler.END

async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    msg = "Отменено."
    if update.callback_query:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg, reply_markup=ADMIN_REPLY_KB)
    return ConversationHandler.END

# ── КОМАНДЫ УЧЕНИКА ───────────────────────────────────────────────────────────

async def cmd_mylessons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name, st = get_student_by_tg(uid)
    if not name:
        await update.message.reply_text(
            "Ты не подключён к боту.\n"
            f"Скажи репетитору свой ID: `{uid}`",
            parse_mode="Markdown")
        return
    pname = st.get("platform_name", name)
    schedule = await get_student_schedule(pname)
    today = now_local().date()
    upcoming = []
    for lesson in schedule:
        conv = lesson_from_platform(lesson, name)
        if not conv: continue
        d = parse_date(conv["date"])
        if d and d >= today and conv["status"] in ("planned","active",""):
            upcoming.append(conv)
    upcoming.sort(key=lambda l: (l["date"], l["time"]))
    if not upcoming:
        await update.message.reply_text(
            "📅 Ближайших занятий пока нет.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🖥 Платформа", url=PLATFORM_URL)
            ]]))
        return
    fname = name.split()[0]
    text = f"📅 *{fname}, твои ближайшие занятия:*\n\n"
    for l in upcoming[:5]:
        text += f"📅 {fmt_date(l['date'])} · 🕐 {l['time']}"
        if l.get("subject"): text += f" · {l['subject']}"
        text += "\n"
    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🖥 Открыть платформу", url=PLATFORM_URL),
            InlineKeyboardButton("📱 Репетитор", url=f"https://t.me/{TUTOR_TG.lstrip('@')}")
        ]]))

async def cmd_platform(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    url = ADMIN_PLATFORM_URL if uid == ADMIN_ID else PLATFORM_URL
    await update.message.reply_text(
        f"🖥 Ссылка на платформу:\n{url}")

# ── CALLBACK РОУТЕР ───────────────────────────────────────────────────────────

async def handle_reply_kb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    text = update.message.text
    if text == "📅 Сегодня":
        await cmd_today(update, ctx)
    elif text == "📆 Неделя":
        await cmd_week(update, ctx)
    elif text == "📚 Напомнить о ДЗ":
        await cmd_homework(update, ctx)
    elif text == "👥 Ученики":
        await cmd_students(update, ctx)

async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data == "show_today":
        await q.answer()
        await cmd_today(update, ctx)
        return
    if data == "show_week":
        await q.answer()
        await cmd_week(update, ctx)
        return
    if data == "my_lessons":
        await q.answer()
        uid = q.from_user.id
        name, st = get_student_by_tg(uid)
        if not name:
            await q.answer("Ты не подключён.", show_alert=True); return
        pname = st.get("platform_name", name)
        schedule = await get_student_schedule(pname)
        today = now_local().date()
        upcoming = []
        for lesson in schedule:
            conv = lesson_from_platform(lesson, name)
            if not conv: continue
            d = parse_date(conv["date"])
            if d and d >= today and conv["status"] in ("planned","active",""):
                upcoming.append(conv)
        upcoming.sort(key=lambda l: (l["date"], l["time"]))
        if not upcoming:
            text = "📅 Ближайших занятий нет."
        else:
            text = f"📅 *Твои ближайшие занятия:*\n\n"
            for l in upcoming[:5]:
                text += f"📅 {fmt_date(l['date'])} · 🕐 {l['time']}\n"
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🖥 Платформа", url=PLATFORM_URL)
            ]]))
        return
    if data.startswith("hw_"):
        await hw_callback(update, ctx)
        return
    if data.startswith("remove_student_"):
        await q.answer()
        name = data.replace("remove_student_","")
        students = get_students()
        if name in students:
            del students[name]
            save_students(students)
        await q.edit_message_text(f"✅ Ученик *{name}* удалён.", parse_mode="Markdown")
        return
    if data == "cancel_action":
        await q.answer()
        await q.edit_message_text("Отменено.")
        return
    if data.startswith("student_move_") or data.startswith("student_cancel_"):
        await q.answer()
        uid = q.from_user.id
        name, _ = get_student_by_tg(uid)
        action = "перенести" if "move" in data else "отменить"
        try:
            await ctx.bot.send_message(ADMIN_ID,
                f"📨 Запрос от ученика!\n\n"
                f"👤 *{name}* хочет *{action}* занятие.\n\n"
                f"Внеси изменения на платформе:\n"
                f"🖥 {ADMIN_PLATFORM_URL}",
                parse_mode="Markdown")
        except: pass
        await q.edit_message_text(
            f"✅ Запрос отправлен репетитору!\n\n"
            f"Илья свяжется с тобой:\n"
            f"📱 Telegram: {TUTOR_TG}\n"
            f"📞 Телефон: {TUTOR_PHONE}")
        return
    await q.answer()

# ── ПЛАНИРОВЩИК ───────────────────────────────────────────────────────────────

async def morning_summary(bot):
    lessons = await get_todays_lessons()
    date_str = now_local().strftime("%d.%m.%Y")
    if not lessons:
        try:
            await bot.send_message(ADMIN_ID,
                f"☀️ Илья, доброе утро!\n\nСегодня занятий нет — можно отдохнуть 😊",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📆 Неделя", callback_data="show_week"),
                    InlineKeyboardButton("🖥 Платформа", url=ADMIN_PLATFORM_URL)
                ]]))
        except: pass
        return

    n = len(lessons)
    ending = "е" if n==1 else "я" if n<5 else "й"
    text = (f"☀️ *Илья, доброе утро!*\n\n"
            f"Сегодня {date_str} у тебя {n} занятий{ending}:\n\n")
    for l in lessons:
        text += f"🕐 {l['time']} — *{l['student']}*\n"
        if l.get("subject"): text += f"   📚 {l['subject']}\n"
    text += f"\n🖥 Всё на платформе — не забудь проверить"
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📅 Подробнее", callback_data="show_today"),
                InlineKeyboardButton("📆 Неделя", callback_data="show_week")
            ],[
                InlineKeyboardButton("🖥 Открыть платформу", url=ADMIN_PLATFORM_URL)
            ]]),
            disable_web_page_preview=True)
    except: pass

async def send_reminders(bot, sent_set: set):
    now = now_local()
    bot_students = get_students()
    platform_data = await fetch_platform_students()
    today = now_local().date()

    for bot_name, bot_st in bot_students.items():
        pname = bot_st.get("platform_name", bot_name)
        tg_ids = bot_st.get("tg_ids", [])
        if isinstance(tg_ids, int): tg_ids = [tg_ids]
        pdata = platform_data.get(pname, {})

        for lesson in pdata.get("schedule", []):
            conv = lesson_from_platform(lesson, bot_name)
            if not conv: continue
            if conv["status"] not in ("planned","active",""): continue
            d = parse_date(conv["date"])
            if not d or d < today: continue

            try:
                h, mi = parse_time(conv["time"])
                if h is None: continue
                ldt = datetime(d.year, d.month, d.day, h, mi)
                diff = (ldt - now).total_seconds() / 60
            except: continue

            lesson_key = f"{pname}_{conv['date']}_{conv['time']}"
            time_str = conv["time"]
            fname = bot_name.split()[0]

            # За 4 часа — только ученикам
            key_4h = f"{lesson_key}:4h"
            if 238 <= diff <= 242 and key_4h not in sent_set:
                sent_set.add(key_4h)
                for tg_id in tg_ids:
                    try:
                        await bot.send_message(tg_id,
                            f"Привет, {fname}! 👋\n\n"
                            f"Напоминаю — сегодня в *{time_str}* урок.\n"
                            f"Готовься, увидимся! 📚\n\n"
                            f"Ссылка для входа — на платформе:\n"
                            f"🖥 {PLATFORM_URL}",
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("🖥 Платформа", url=PLATFORM_URL),
                                InlineKeyboardButton("📱 Репетитор",
                                    url=f"https://t.me/{TUTOR_TG.lstrip('@')}")
                            ],[
                                InlineKeyboardButton("🔄 Хочу перенести",
                                    callback_data=f"student_move_{lesson_key}"),
                                InlineKeyboardButton("❌ Хочу отменить",
                                    callback_data=f"student_cancel_{lesson_key}")
                            ]]))
                    except: pass

            # За 15 минут — Илье и ученикам
            key_15m = f"{lesson_key}:15m"
            if 13 <= diff <= 17 and key_15m not in sent_set:
                sent_set.add(key_15m)
                # Репетитору
                try:
                    msg = (f"🔔 Через 15 минут — *{bot_name}*!\n\n"
                           f"🕐 {time_str}\n"
                           f"🖥 [Открыть платформу]({ADMIN_PLATFORM_URL})\n"
                           f"_Ссылку Zoom найдёшь на платформе_")
                    await bot.send_message(ADMIN_ID, msg, parse_mode="Markdown",
                        disable_web_page_preview=True)
                except: pass
                # Ученикам
                for tg_id in tg_ids:
                    try:
                        msg = (f"🔔 {fname}, урок начинается через 15 минут!\n\n"
                               f"🕐 {time_str} (МСК)\n"
                               f"🖥 [Открыть платформу]({PLATFORM_URL})")
                        if conv.get("zoom"):
                            msg += f"\n🔗 [Войти в Zoom]({conv['zoom']})"
                        await bot.send_message(tg_id, msg, parse_mode="Markdown",
                            disable_web_page_preview=True)
                    except: pass

async def scheduler(bot):
    sent_morning = None
    sent_reminders_set = set()
    last_reset_day = None
    while True:
        now = now_local()
        day_key = now.strftime("%Y-%m-%d")
        if day_key != last_reset_day:
            sent_reminders_set = set()
            last_reset_day = day_key
        if now.hour == REMIND_HOUR and now.minute == 0 and sent_morning != day_key:
            sent_morning = day_key
            await morning_summary(bot)
        await send_reminders(bot, sent_reminders_set)
        await asyncio.sleep(60)

# ── ЗАПУСК ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Диалог: добавить ученика
    add_student_conv = ConversationHandler(
        entry_points=[CommandHandler("addstudent", cmd_addstudent)],
        states={
            ADD_STUDENT_PLATFORM: [CallbackQueryHandler(student_platform_cb, pattern="^sp_")],
            ADD_STUDENT_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, student_name_manual)],
            ADD_STUDENT_TG:       [MessageHandler(filters.TEXT & ~filters.COMMAND, student_tg)],
            ADD_STUDENT_MORE_TG:  [
                CallbackQueryHandler(student_more_tg_cb, pattern="^(add_more_tg|finish_student)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        allow_reentry=True)

    app.add_handler(add_student_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("homework", cmd_homework))
    app.add_handler(CommandHandler("students", cmd_students))
    app.add_handler(CommandHandler("removestudent", cmd_removestudent))
    app.add_handler(CommandHandler("mylessons", cmd_mylessons))
    app.add_handler(CommandHandler("platform", cmd_platform))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(
            r"^(📅 Сегодня|📆 Неделя|📚 Напомнить о ДЗ|👥 Ученики)$"),
        handle_reply_kb))
    app.add_handler(CallbackQueryHandler(callback_router))

    async def post_init(application):
        asyncio.create_task(scheduler(application.bot))
        admin_commands = [
            BotCommand("today",         "📅 Занятия сегодня"),
            BotCommand("week",          "📆 Расписание на неделю"),
            BotCommand("homework",      "📚 Напомнить о ДЗ"),
            BotCommand("addstudent",    "👤 Добавить ученика"),
            BotCommand("students",      "👥 Список учеников"),
            BotCommand("removestudent", "🗑 Удалить ученика"),
            BotCommand("cancel",        "❌ Отмена"),
        ]
        student_commands = [
            BotCommand("start",    "🏠 Главное меню"),
            BotCommand("mylessons","📅 Мои занятия"),
            BotCommand("platform", "🖥 Открыть платформу"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
            await application.bot.set_my_commands(
                student_commands, scope=BotCommandScopeDefault())
        except Exception as e:
            print(f"Меню: {e}")
        try:
            await application.bot.send_message(ADMIN_ID,
                "✅ Бот перезапущен и готов к работе!\n"
                "Расписание подгружается с платформы автоматически.",
                reply_markup=ADMIN_REPLY_KB)
        except: pass
        print("✅ Бот-напоминалкин запущен!")

    app.post_init = post_init
    print("🚀 Запускаю бота...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
