"""
Бот-напоминалкин · Репетитор Котельников И.С.
"""
import json, os, asyncio, re
from datetime import datetime, date, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           MessageHandler, filters, ContextTypes, ConversationHandler)

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН")
ADMIN_ID     = int(os.getenv("ADMIN_ID", "0"))
TZ_OFFSET    = int(os.getenv("TZ_OFFSET", "3"))
PLATFORM_URL       = os.getenv("PLATFORM_URL", "https://web-production-aa92f.up.railway.app")
ADMIN_PLATFORM_URL = PLATFORM_URL + "/admin"  # ссылка для репетитора
REMIND_HOUR  = int(os.getenv("REMIND_HOUR", "9"))
TUTOR_TG     = "@grandvillakotel"
TUTOR_PHONE  = "+7 906 585 7200"

def admin_lesson_kb(lesson_id, show_done=True):
    """Кнопки для Ильи под каждым сообщением о занятии"""
    row1 = []
    if show_done:
        row1.append(InlineKeyboardButton("✅ Провёл", callback_data=f"done_{lesson_id}"))
    row1.append(InlineKeyboardButton("🔄 Перенести", callback_data=f"admin_move_{lesson_id}"))
    row1.append(InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_{lesson_id}"))
    return InlineKeyboardMarkup([row1])

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
LESSONS_FILE  = DATA_DIR / "lessons.json"
STUDENTS_FILE = DATA_DIR / "students.json"

def load(p, d):
    try: return json.load(open(p, encoding="utf-8")) if p.exists() else d
    except: return d

def save(p, d):
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def get_lessons():   return load(LESSONS_FILE, [])
def save_lessons(d): save(LESSONS_FILE, d)
def get_students():  return load(STUDENTS_FILE, {})
def save_students(d):save(STUDENTS_FILE, d)

def now_local():
    return datetime.utcnow() + timedelta(hours=TZ_OFFSET)

def today_iso():
    return now_local().strftime("%Y-%m-%d")

def next_id(lst):
    return max((x.get("id", 0) for x in lst), default=0) + 1

MONTHS_RU = {
    "января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
    "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12,
    "янв":1,"фев":2,"мар":3,"апр":4,"май":5,"июн":6,
    "июл":7,"авг":8,"сен":9,"окт":10,"ноя":11,"дек":12,
}
DAYS_RU = {"пн":0,"вт":1,"ср":2,"чт":3,"пт":4,"сб":5,"вс":6,
           "понедельник":0,"вторник":1,"среда":2,"четверг":3,
           "пятница":4,"суббота":5,"воскресенье":6}

def parse_date(s):
    s = s.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s[:10])
    m = re.match(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", s)
    if m:
        d2, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or now_local().year)
        try: return date(y, mo, d2)
        except: pass
    m = re.match(r"(\d{1,2})\s+(\S+)(?:\s+(\d{4}))?", s.lower())
    if m:
        d2, mon, y = int(m.group(1)), m.group(2).rstrip(".,"), int(m.group(3) or now_local().year)
        mo = MONTHS_RU.get(mon)
        if mo:
            try: return date(y, mo, d2)
            except: pass
    return None

def parse_time(s):
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m: return int(m.group(1)), int(m.group(2))
    return None, None

def lesson_datetime(lesson):
    try:
        d = parse_date(lesson.get("date", ""))
        h, mi = parse_time(lesson.get("time", ""))
        if d and h is not None:
            return datetime(d.year, d.month, d.day, h, mi)
    except: pass
    return None

def get_student_name(uid):
    for name, st in get_students().items():
        if st.get("tg_id") == uid:
            return name
    return None

def is_admin(update): return update.effective_user.id == ADMIN_ID

def fmt_date(iso):
    try:
        d = date.fromisoformat(iso)
        days = ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
        months = ["января","февраля","марта","апреля","мая","июня",
                  "июля","августа","сентября","октября","ноября","декабря"]
        return f"{days[d.weekday()]}, {d.day} {months[d.month-1]}"
    except: return iso

# ── СОСТОЯНИЯ ─────────────────────────────────────────────────────────────────
(ADD_STUDENT_NAME, ADD_STUDENT_TG,
 ADD_LESSON_STUDENT, ADD_LESSON_DATE, ADD_LESSON_MORE_DATE,
 ADD_LESSON_TIME, ADD_LESSON_ZOOM, ADD_LESSON_SUB, ADD_LESSON_RECURRING,
 RESCHEDULE_DATE, RESCHEDULE_TIME) = range(11)

# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        await update.message.reply_text(
            "👋 Привет, Илья!\n\n"
            "Я твой бот-расписание.\n"
            "Каждое утро в 9:00 — сводка на день.\n"
            "За 4 часа — напомню ученику, за 15 минут — нам обоим.\n\n"
            "Используй кнопки внизу 👇",
            reply_markup=ADMIN_REPLY_KB)
        return

    name = get_student_name(uid)
    if name:
        lessons = [l for l in get_lessons()
                   if l.get("student")==name and l.get("status","active")=="active"
                   and (d:=parse_date(l.get("date",""))) and d >= now_local().date()]
        lessons.sort(key=lambda l: (l.get("date",""), l.get("time","")))
        next_l = lessons[0] if lessons else None
        text = f"👋 Привет, {name.split()[0]}!\n\n"
        if next_l:
            text += (f"Твоё ближайшее занятие:\n"
                     f"📅 {fmt_date(next_l['date'])}\n"
                     f"🕐 {next_l['time']} (по МСК)\n\n"
                     f"Все занятия и материалы — на платформе 👇")
        else:
            text += "Ближайших занятий пока нет."
        kb = [[InlineKeyboardButton("📅 Мои занятия", callback_data="my_lessons"),
               InlineKeyboardButton("🖥 Платформа", url=PLATFORM_URL)]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(
            "👋 Привет!\n\n"
            "Ты ещё не подключён к боту.\n"
            f"Скажи репетитору свой Telegram ID:\n\n"
            f"`{uid}`\n\n"
            "После этого напоминания о занятиях будут приходить сюда 🙂",
            parse_mode="Markdown")

# ── ДОБАВИТЬ УЧЕНИКА ──────────────────────────────────────────────────────────
async def cmd_addstudent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("Введи имя ученика (Имя Фамилия):")
    return ADD_STUDENT_NAME

async def got_student_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_student"] = update.message.text.strip()
    await update.message.reply_text(
        f"Имя: *{ctx.user_data['new_student']}*\n\n"
        "Теперь введи Telegram ID ученика.\n"
        "Ученик должен написать боту /start — тогда бот покажет ему его ID.",
        parse_mode="Markdown")
    return ADD_STUDENT_TG

async def got_student_tg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: tg_id = int(update.message.text.strip())
    except:
        await update.message.reply_text("ID должен быть числом. Попробуй ещё раз:")
        return ADD_STUDENT_TG
    name = ctx.user_data.pop("new_student")
    students = get_students()
    students[name] = {"tg_id": tg_id, "subscription_left": 0}
    save_students(students)
    await update.message.reply_text(f"✅ {name} добавлен!")
    try:
        await ctx.bot.send_message(tg_id,
            f"👋 Привет, {name.split()[0]}!\n\n"
            "Репетитор Илья Котельников подключил тебя к боту.\n"
            "Теперь сюда будут приходить напоминания о занятиях.\n\n"
            f"🖥 Платформа: {PLATFORM_URL}")
    except: pass
    return ConversationHandler.END

# ── СПИСОК УЧЕНИКОВ ───────────────────────────────────────────────────────────
async def cmd_students(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    students = get_students()
    if not students:
        await update.message.reply_text("Учеников пока нет. Добавь через /addstudent")
        return
    text = "👥 *Ученики:*\n\n"
    for name, st in students.items():
        sub = st.get("subscription_left", 0)
        text += f"• *{name}* — TG: `{st.get('tg_id','?')}` · Абонемент: {sub} ур.\n"
    text += "\nЧтобы удалить ученика: /removestudent"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_removestudent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    students = get_students()
    if not students:
        await update.message.reply_text("Учеников нет.")
        return
    kb = [[InlineKeyboardButton(f"🗑 {n}", callback_data=f"remove_student_{n}")]
          for n in sorted(students.keys())]
    kb.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_conv")])
    await update.message.reply_text(
        "Выбери ученика для удаления из расписания:\n"
        "_(Все его занятия тоже удалятся)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

# ── ДОБАВИТЬ ЗАНЯТИЕ (диалог) ─────────────────────────────────────────────────
async def cmd_addlesson(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    students = get_students()
    if not students:
        msg = "Сначала добавь учеников через /addstudent"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg); return
        await update.message.reply_text(msg); return
    kb = [[InlineKeyboardButton(n, callback_data=f"ls_{n}")]
          for n in sorted(students.keys())]
    kb.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_conv")])
    msg = "Выбери ученика:"
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
    return ADD_LESSON_STUDENT

async def got_lesson_student(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    name = q.data.replace("ls_","")
    ctx.user_data["lesson"] = {"student": name, "dates": [], "zoom": ""}
    await q.edit_message_text(
        f"👤 Ученик: *{name}*\n\n"
        "Введи дату занятия по МСК:\n"
        "Примеры: `22.08`, `22 августа`, `22.08.2026`",
        parse_mode="Markdown")
    return ADD_LESSON_DATE

async def got_lesson_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = parse_date(update.message.text.strip())
    if not d:
        await update.message.reply_text(
            "Не понял дату. Попробуй: `22.08` или `22 августа`",
            parse_mode="Markdown")
        return ADD_LESSON_DATE
    ctx.user_data["lesson"]["dates"].append(d.isoformat())
    kb = [[InlineKeyboardButton("✅ Да, добавить ещё день", callback_data="add_more_date"),
           InlineKeyboardButton("➡️ Нет, продолжить", callback_data="no_more_date")]]
    await update.message.reply_text(
        f"📅 {fmt_date(d.isoformat())} — добавлено!\n\n"
        "Этот ученик занимается ещё в другие дни?",
        reply_markup=InlineKeyboardMarkup(kb))
    return ADD_LESSON_MORE_DATE

async def more_date_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "Введи следующую дату:\n"
        "Примеры: `24.08`, `24 августа`",
        parse_mode="Markdown")
    return ADD_LESSON_DATE

async def more_date_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    dates = ctx.user_data["lesson"]["dates"]
    dates_str = ", ".join(fmt_date(d) for d in dates)
    await q.edit_message_text(
        f"📅 Даты: {dates_str}\n\n"
        "Введи время занятия по МСК:\n"
        "Пример: `16:00`",
        parse_mode="Markdown")
    return ADD_LESSON_TIME

async def got_lesson_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    h, mi = parse_time(update.message.text.strip())
    if h is None:
        await update.message.reply_text(
            "Не понял время. Попробуй: `16:00`", parse_mode="Markdown")
        return ADD_LESSON_TIME
    ctx.user_data["lesson"]["time"] = f"{h:02d}:{mi:02d}"
    await update.message.reply_text(
        "Ссылка на Zoom для этого занятия?\n"
        "(Отправь ссылку или напиши `-` чтобы пропустить)")
    return ADD_LESSON_ZOOM

async def got_lesson_zoom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    zoom = update.message.text.strip()
    if zoom == "-": zoom = ""
    ctx.user_data["lesson"]["zoom"] = zoom
    await update.message.reply_text(
        "Сколько занятий в абонементе у ученика?\n"
        "(Введи число или `-` чтобы пропустить)")
    return ADD_LESSON_SUB

async def got_lesson_sub(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    sub = 0
    if text != "-":
        try: sub = int(text)
        except: pass
    ctx.user_data["lesson"]["sub"] = sub
    # Спрашиваем про постоянное расписание
    kb = [[InlineKeyboardButton("🔁 Да, каждую неделю", callback_data="recurring_yes"),
           InlineKeyboardButton("➡️ Нет, разовые", callback_data="recurring_no")]]
    await update.message.reply_text(
        "Это постоянное расписание?\n"
        "Если да — создам занятия на 4 недели вперёд автоматически.",
        reply_markup=InlineKeyboardMarkup(kb))
    return ADD_LESSON_RECURRING

async def got_lesson_recurring(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    recurring = q.data == "recurring_yes"
    lesson_data = ctx.user_data.pop("lesson")
    sub = lesson_data.pop("sub", 0)
    lessons = get_lessons()
    students = get_students()
    added = []
    all_dates = list(lesson_data["dates"])

    if recurring:
        # Разворачиваем каждую дату на 4 недели вперёд
        expanded = []
        for d_iso in lesson_data["dates"]:
            base = date.fromisoformat(d_iso)
            for week in range(4):
                expanded.append((base + timedelta(weeks=week)).isoformat())
        all_dates = expanded

    for d in all_dates:
        lesson = {
            "id": next_id(lessons),
            "student": lesson_data["student"],
            "date": d,
            "time": lesson_data["time"],
            "zoom": lesson_data["zoom"],
            "status": "active",
            "recurring": recurring
        }
        lessons.append(lesson)
        added.append(lesson)
    save_lessons(lessons)

    if sub > 0 and lesson_data["student"] in students:
        students[lesson_data["student"]]["subscription_left"] = sub
        save_students(students)

    dates_str = "\n".join(f"  📅 {fmt_date(l['date'])} · 🕐 {l['time']}"
                           for l in added[:8])
    if len(added) > 8:
        dates_str += f"\n  ...и ещё {len(added)-8} занятий"
    text = (f"✅ Занятия добавлены!\n\n"
            f"👤 {lesson_data['student']}\n{dates_str}")
    if lesson_data["zoom"]: text += f"\n🔗 Zoom: {lesson_data['zoom']}"
    if sub > 0: text += f"\n📦 Абонемент: {sub} занятий"
    if recurring: text += "\n🔁 Постоянное расписание на 4 недели"
    kb = [[InlineKeyboardButton("➕ Ещё занятие", callback_data="cb_addlesson"),
           InlineKeyboardButton("📅 Сегодня", callback_data="show_today")]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    # Уведомляем ученика о новых занятиях
    students_data = get_students()
    st = students_data.get(lesson_data["student"], {})
    if st.get("tg_id"):
        fname = lesson_data["student"].split()[0]
        s_dates = "\n".join(
            f"📅 {fmt_date(l['date'])} · 🕐 {l['time']}"
            for l in added[:5])
        if len(added) > 5:
            s_dates += f"\n...и ещё {len(added)-5} занятий"
        try:
            await q.get_bot().send_message(st["tg_id"],
                f"Привет, {fname}! 👋\n\n"
                f"Репетитор Илья назначил тебе занятия:\n\n"
                f"{s_dates}\n\n"
                f"Я буду напоминать тебе о каждом уроке заранее 🔔\n"
                f"Все материалы и задания — на платформе:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🖥 Открыть платформу", url=PLATFORM_URL),
                    InlineKeyboardButton("📱 Написать репетитору",
                        url=f"https://t.me/{TUTOR_TG.lstrip('@')}")
                ]]))
        except: pass
    return ConversationHandler.END

async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    if update.callback_query:
        await update.callback_query.edit_message_text("Отменено.")
    else:
        await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ── РАСПИСАНИЕ ────────────────────────────────────────────────────────────────
async def show_today(bot_or_update, ctx=None, chat_id=None):
    """Показать занятия сегодня"""
    is_cb = isinstance(bot_or_update, Update) and bot_or_update.callback_query
    is_cmd = isinstance(bot_or_update, Update) and bot_or_update.message
    today = today_iso()
    lessons = [l for l in get_lessons()
               if l.get("date","")[:10]==today and l.get("status","active")=="active"]
    lessons.sort(key=lambda l: l.get("time",""))

    if not lessons:
        text = f"📅 Сегодня ({now_local().strftime('%d.%m')}) занятий нет."
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📆 Неделя", callback_data="show_week"),
            InlineKeyboardButton("➕ Занятие", callback_data="cb_addlesson")
        ]])
        if is_cb:
            await bot_or_update.callback_query.edit_message_text(text, reply_markup=kb)
        elif is_cmd:
            await bot_or_update.message.reply_text(text, reply_markup=kb)
        else:
            await bot_or_update.send_message(chat_id or ADMIN_ID, text, reply_markup=kb)
        return

    # Заголовок
    header = f"📅 *Занятия сегодня, {now_local().strftime('%d.%m.%Y')}:*"
    top_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📆 Неделя", callback_data="show_week"),
        InlineKeyboardButton("✅ Провёл", callback_data="done_pick")
    ]])
    if is_cb:
        await bot_or_update.callback_query.edit_message_text(
            header, parse_mode="Markdown", reply_markup=top_kb)
        send_fn = bot_or_update.callback_query.get_bot().send_message
        cid = bot_or_update.callback_query.message.chat_id
    elif is_cmd:
        await bot_or_update.message.reply_text(
            header, parse_mode="Markdown", reply_markup=top_kb)
        send_fn = bot_or_update.message.reply_text
        cid = None
    else:
        await bot_or_update.send_message(
            chat_id or ADMIN_ID, header, parse_mode="Markdown", reply_markup=top_kb)
        send_fn = bot_or_update.send_message
        cid = chat_id or ADMIN_ID

    # Карточка на каждое занятие
    for l in lessons:
        card = f"🕐 *{l['time']}* — {l['student']}"
        if l.get("zoom"): card += f"\n🔗 [Zoom]({l['zoom']})"
        card_kb = admin_lesson_kb(l["id"])
        try:
            if cid:
                await send_fn(cid, card, parse_mode="Markdown",
                    reply_markup=card_kb, disable_web_page_preview=True)
            else:
                await send_fn(card, parse_mode="Markdown",
                    reply_markup=card_kb, disable_web_page_preview=True)
        except: pass

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await show_today(update, ctx)

async def show_week_fn(update: Update, ctx: ContextTypes.DEFAULT_TYPE, edit=False):
    if not is_admin(update): return
    today = now_local().date()
    week_end = today + timedelta(days=7)
    lessons = [l for l in get_lessons()
               if l.get("status","active")=="active" and
               (d:=parse_date(l.get("date",""))) and today <= d <= week_end]
    if not lessons:
        text = "📆 На ближайшую неделю занятий нет."
    else:
        lessons.sort(key=lambda l: (l.get("date",""), l.get("time","")))
        text = "📆 *Расписание на неделю:*\n\n"
        cur = ""
        for l in lessons:
            d = l.get("date","")[:10]
            if d != cur:
                text += f"*{fmt_date(d)}*\n"; cur = d
            text += f"  🕐 {l['time']} — {l['student']}"
            if l.get("zoom"): text += f" · [Zoom]({l['zoom']})"
            text += "\n"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 Сегодня", callback_data="show_today"),
        InlineKeyboardButton("➕ Занятие", callback_data="cb_addlesson")
    ]])
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=kb,
            disable_web_page_preview=True)
    else:
        send = update.message.reply_text if update.message else update.callback_query.edit_message_text
        await send(text, parse_mode="Markdown", reply_markup=kb,
                   disable_web_page_preview=True)

async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await show_week_fn(update, ctx)

# ── ОТМЕНА И ПЕРЕНОС ──────────────────────────────────────────────────────────
async def handle_lesson_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает /cancel_ID и /move_ID"""
    if not is_admin(update): return
    text = update.message.text
    m = re.match(r"/(cancel|move)_(\d+)", text)
    if not m: return
    action, lid = m.group(1), int(m.group(2))
    lessons = get_lessons()
    lesson = next((l for l in lessons if l["id"]==lid), None)
    if not lesson:
        await update.message.reply_text("Занятие не найдено.")
        return

    if action == "cancel":
        lesson["status"] = "cancelled"
        save_lessons(lessons)
        student = lesson.get("student","")
        # Уведомляем ученика
        st = get_students().get(student,{})
        if st.get("tg_id"):
            try:
                await ctx.bot.send_message(st["tg_id"],
                    f"Привет, {student.split()[0]}! 👋\n\n"
                    f"К сожалению, занятие {fmt_date(lesson['date'])} в {lesson['time']} "
                    "отменяется.\n\n"
                    "Репетитор Илья свяжется с тобой для переноса:\n"
                    f"📱 Telegram: {TUTOR_TG}\n"
                    f"📞 Телефон: {TUTOR_PHONE}")
            except: pass
        await update.message.reply_text(
            f"✅ Занятие с {student} {fmt_date(lesson['date'])} отменено.\n"
            f"Ученик уведомлён.")

    elif action == "move":
        ctx.user_data["move_id"] = lid
        ctx.user_data["move_lesson"] = lesson
        await update.message.reply_text(
            f"Перенос занятия:\n"
            f"👤 {lesson['student']} · 📅 {fmt_date(lesson['date'])} · 🕐 {lesson['time']}\n\n"
            "Введи новую дату:",
            parse_mode="Markdown")
        return

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает свободный текст вне диалогов (перенос занятия)"""
    awaiting = ctx.user_data.get("awaiting_move")
    if not awaiting: return

    if awaiting == "date":
        d = parse_date(update.message.text.strip())
        if not d:
            await update.message.reply_text(
                "Не понял дату. Попробуй: `22.08` или `22 августа`",
                parse_mode="Markdown")
            return
        ctx.user_data["new_date"] = d.isoformat()
        ctx.user_data["awaiting_move"] = "time"
        await update.message.reply_text("Новое время (пример: `16:00`):", parse_mode="Markdown")

    elif awaiting == "time":
        h, mi = parse_time(update.message.text.strip())
        if h is None:
            await update.message.reply_text("Не понял время. Попробуй: `16:00`", parse_mode="Markdown")
            return
        lid = ctx.user_data.pop("move_id", None)
        old = ctx.user_data.pop("move_lesson", {})
        new_date = ctx.user_data.pop("new_date", "")
        new_time = f"{h:02d}:{mi:02d}"
        ctx.user_data.pop("awaiting_move", None)
        lessons = get_lessons()
        for l in lessons:
            if l["id"] == lid:
                l["date"] = new_date; l["time"] = new_time; break
        save_lessons(lessons)
        student = old.get("student","")
        st = get_students().get(student,{})
        if st.get("tg_id"):
            try:
                await ctx.bot.send_message(st["tg_id"],
                    f"Привет, {student.split()[0]}! 📅\n\n"
                    f"Занятие перенесено:\n"
                    f"Было: {fmt_date(old.get('date',''))} в {old.get('time','')}\n"
                    f"Стало: {fmt_date(new_date)} в {new_time}\n\n"
                    f"Если остались вопросы — свяжись с репетитором:\n"
                    f"📱 Telegram: {TUTOR_TG}\n"
                    f"📞 Телефон: {TUTOR_PHONE}")
            except: pass
        await update.message.reply_text(
            f"✅ Занятие с {student} перенесено!\n"
            f"Стало: {fmt_date(new_date)} в {new_time}\n"
            f"Ученик уведомлён.")

# ── НАПОМНИТЬ О ДЗ ────────────────────────────────────────────────────────────
async def cmd_homework(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    students = get_students()
    if not students:
        await update.message.reply_text("Учеников нет.")
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
    targets = students.items() if target=="ALL" else [(target, students[target])]
    for name, st in targets:
        if st.get("tg_id"):
            try:
                await ctx.bot.send_message(st["tg_id"],
                    f"Привет, {name.split()[0]}! 📚\n\n"
                    f"Тебе задано домашнее задание — проверь его на платформе:\n"
                    f"🖥 {PLATFORM_URL}")
                sent += 1
            except: pass
    await q.edit_message_text(f"✅ Отправлено {sent} ученик(ам).")

# ── ОТМЕТИТЬ ЗАНЯТИЕ КАК ПРОВЕДЁННОЕ ─────────────────────────────────────────
# ── ОТМЕНА / ПЕРЕНОС / РЕДАКТИРОВАНИЕ (выбор из ближайших 4) ────────────────

def get_upcoming_lessons(n=4):
    """Возвращает n ближайших активных занятий по дате"""
    today = now_local().date()
    lessons = [l for l in get_lessons()
               if l.get("status", "active") in {"active", "planned"}
               and (d := parse_date(l.get("date", ""))) and d >= today]
    lessons.sort(key=lambda l: (l.get("date", ""), l.get("time", "")))
    return lessons[:n]

async def cmd_cancel_lesson(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отменить занятие — показывает ближайшие 4"""
    if not is_admin(update): return
    lessons = get_upcoming_lessons(4)
    if not lessons:
        await update.message.reply_text("Ближайших занятий нет.")
        return
    kb = [[InlineKeyboardButton(
        f"❌ {fmt_date(l['date'])} {l['time']} — {l['student']}",
        callback_data=f"cancel_{l['id']}")]
        for l in lessons]
    await update.message.reply_text(
        "Какое занятие отменить?",
        reply_markup=InlineKeyboardMarkup(kb))

async def cmd_move_lesson(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Перенести занятие — показывает ближайшие 4"""
    if not is_admin(update): return
    lessons = get_upcoming_lessons(4)
    if not lessons:
        await update.message.reply_text("Ближайших занятий нет.")
        return
    kb = [[InlineKeyboardButton(
        f"🔄 {fmt_date(l['date'])} {l['time']} — {l['student']}",
        callback_data=f"admin_move_{l['id']}")]
        for l in lessons]
    await update.message.reply_text(
        "Какое занятие перенести?",
        reply_markup=InlineKeyboardMarkup(kb))

# Состояния для редактирования занятия
EDIT_LESSON_PICK, EDIT_LESSON_FIELD, EDIT_LESSON_VALUE = 20, 21, 22

async def cmd_edit_lesson(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Изменить занятие — показывает ближайшие 4"""
    if not is_admin(update): return
    lessons = get_upcoming_lessons(4)
    if not lessons:
        await update.message.reply_text("Ближайших занятий нет.")
        return
    kb = [[InlineKeyboardButton(
        f"✏️ {fmt_date(l['date'])} {l['time']} — {l['student']}",
        callback_data=f"edit_pick_{l['id']}")]
        for l in lessons]
    await update.message.reply_text(
        "Какое занятие изменить?",
        reply_markup=InlineKeyboardMarkup(kb))
    return EDIT_LESSON_PICK

async def edit_pick_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    lid = int(q.data.replace("edit_pick_", ""))
    lesson = next((l for l in get_lessons() if l["id"] == lid), None)
    if not lesson:
        await q.edit_message_text("Занятие не найдено."); return ConversationHandler.END
    ctx.user_data["edit_lid"] = lid
    ctx.user_data["edit_lesson"] = lesson
    kb = [[InlineKeyboardButton("📅 Дата", callback_data="edit_field_date"),
           InlineKeyboardButton("🕐 Время", callback_data="edit_field_time")],
          [InlineKeyboardButton("🔗 Zoom-ссылка", callback_data="edit_field_zoom"),
           InlineKeyboardButton("❌ Отмена", callback_data="cancel_conv")]]
    await q.edit_message_text(
        f"✏️ Занятие: *{lesson['student']}*\n"
        f"📅 {fmt_date(lesson['date'])} · 🕐 {lesson['time']}\n\n"
        "Что изменить?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))
    return EDIT_LESSON_FIELD

async def edit_field_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    field = q.data.replace("edit_field_", "")
    ctx.user_data["edit_field"] = field
    prompts = {
        "date": "Введи новую дату (пример: `25.08` или `25 августа`):",
        "time": "Введи новое время (пример: `16:00`):",
        "zoom": "Введи новую ссылку на Zoom (или `-` чтобы убрать):"
    }
    await q.edit_message_text(prompts.get(field, "Введи значение:"), parse_mode="Markdown")
    return EDIT_LESSON_VALUE

async def edit_value_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = ctx.user_data.get("edit_field")
    lid = ctx.user_data.get("edit_lid")
    text = update.message.text.strip()
    lessons = get_lessons()
    lesson = next((l for l in lessons if l["id"] == lid), None)
    if not lesson:
        await update.message.reply_text("Занятие не найдено.")
        return ConversationHandler.END

    if field == "date":
        d = parse_date(text)
        if not d:
            await update.message.reply_text(
                "Не понял дату. Попробуй: `25.08`", parse_mode="Markdown")
            return EDIT_LESSON_VALUE
        lesson["date"] = d.isoformat()
    elif field == "time":
        h, mi = parse_time(text)
        if h is None:
            await update.message.reply_text(
                "Не понял время. Попробуй: `16:00`", parse_mode="Markdown")
            return EDIT_LESSON_VALUE
        lesson["time"] = f"{h:02d}:{mi:02d}"
    elif field == "zoom":
        lesson["zoom"] = "" if text == "-" else text

    save_lessons(lessons)
    # Уведомляем ученика если дата или время изменились
    if field in ("date", "time"):
        students = get_students()
        st = students.get(lesson.get("student", ""), {})
        if st.get("tg_id"):
            try:
                await ctx.bot.send_message(st["tg_id"],
                    "📅 Занятие обновлено!\n\n"
                    f"{lesson['student'].split()[0]}, репетитор изменил расписание:\n"
                    f"📅 {fmt_date(lesson['date'])} · 🕐 {lesson['time']}\n\n"
                    f"Если есть вопросы — свяжись с Ильёй:\n"
                    f"📱 {TUTOR_TG} · 📞 {TUTOR_PHONE}",
                    parse_mode="Markdown")

            except: pass
    await update.message.reply_text(
        "✅ Занятие обновлено!\n"
        f"👤 {lesson['student']}\n"
        f"📅 {fmt_date(lesson['date'])} · 🕐 {lesson['time']}",
        reply_markup=ADMIN_REPLY_KB)
    return ConversationHandler.END

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    today = today_iso()
    lessons = [l for l in get_lessons()
               if l.get("date","")[:10]==today and l.get("status","active")=="active"]
    if not lessons:
        await update.message.reply_text("Сегодня нет активных занятий для отметки.")
        return
    kb = [[InlineKeyboardButton(
        f"{l['time']} — {l['student']}", callback_data=f"done_{l['id']}")]
        for l in lessons]
    await update.message.reply_text(
        "Отметь проведённое занятие:",
        reply_markup=InlineKeyboardMarkup(kb))

async def done_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    lid = int(q.data.replace("done_",""))
    lessons = get_lessons()
    lesson = next((l for l in lessons if l["id"]==lid), None)
    if not lesson:
        await q.edit_message_text("Занятие не найдено."); return
    lesson["status"] = "done"
    save_lessons(lessons)
    # Списываем занятие из абонемента ученика
    students = get_students()
    student = lesson.get("student","")
    st = students.get(student,{})
    sub = st.get("subscription_left", 0)
    if sub > 0:
        st["subscription_left"] = sub - 1
        save_students(students)
        # Уведомляем ученика об остатке
        remaining = st["subscription_left"]
        if st.get("tg_id"):
            try:
                msg = (f"Привет, {student.split()[0]}! ✅\n\n"
                       f"Сегодняшнее занятие отмечено как проведённое.\n\n"
                       f"📦 В твоём абонементе осталось: *{remaining}* занятий.")
                if remaining <= 2:
                    msg += ("\n\n⚠️ Скоро закончится абонемент — "
                            f"свяжись с репетитором для продления:\n"
                            f"📱 {TUTOR_TG} · 📞 {TUTOR_PHONE}")
                await ctx.bot.send_message(st["tg_id"], msg, parse_mode="Markdown")
            except: pass
    await q.edit_message_text(
        f"✅ Занятие с {student} отмечено как проведённое!\n"
        f"Абонемент: {st.get('subscription_left', '—')} ур.")

# ── КАБИНЕТ УЧЕНИКА ───────────────────────────────────────────────────────────
async def my_lessons_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    name = get_student_name(uid)
    if not name:
        await q.answer("Ты не подключён.", show_alert=True); return
    today = now_local().date()
    lessons = [l for l in get_lessons()
               if l.get("student")==name and l.get("status","active")=="active"
               and (d:=parse_date(l.get("date",""))) and d >= today]
    lessons.sort(key=lambda l: (l.get("date",""), l.get("time","")))
    if not lessons:
        text = "📅 Ближайших занятий пока нет."
    else:
        text = f"📅 *Твои ближайшие занятия:*\n\n"
        for l in lessons[:5]:
            text += f"📅 {fmt_date(l['date'])} · 🕐 {l['time']}\n"
        text += f"\n🖥 [Все занятия и материалы на платформе]({PLATFORM_URL})"
    # Остаток абонемента
    sub = get_students().get(name,{}).get("subscription_left", 0)
    if sub: text += f"\n\n📦 Осталось в абонементе: *{sub}* занятий"
    await q.edit_message_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🖥 Платформа", url=PLATFORM_URL)
        ]]), disable_web_page_preview=True)

# ── CALLBACK РОУТЕР ──────────────────────────────────────────────────────────
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data == "show_today":
        if is_admin(update):
            await show_today(update, ctx)
        return
    if data == "done_pick":
        # Показываем выбор занятия для отметки
        await q.answer()
        today = today_iso()
        lessons = [l for l in get_lessons()
                   if l.get("date","")[:10]==today and l.get("status","active")=="active"]
        if not lessons:
            await q.answer("Сегодня нет активных занятий.", show_alert=True)
            return
        kb = [[InlineKeyboardButton(
            f"{l['time']} — {l['student']}", callback_data=f"done_{l['id']}")]
            for l in lessons]
        await ctx.bot.send_message(q.message.chat_id,
            "Отметь проведённое занятие:",
            reply_markup=InlineKeyboardMarkup(kb))
        return
    if data.startswith("admin_move_"):
        await q.answer()
        lid = int(data.replace("admin_move_",""))
        lessons = get_lessons()
        lesson = next((l for l in lessons if l["id"]==lid), None)
        if not lesson:
            await q.answer("Занятие не найдено.", show_alert=True); return
        ctx.user_data["move_id"] = lid
        ctx.user_data["move_lesson"] = lesson
        ctx.user_data["awaiting_move"] = "date"
        await ctx.bot.send_message(q.message.chat_id,
            f"Перенос занятия:\n"
            f"👤 {lesson['student']} · 🕐 {lesson['time']}\n\n"
            "Введи новую дату (пример: `25.08`):",
            parse_mode="Markdown")
        return
    if data.startswith("cancel_"):
        # Отмена занятия через кнопку
        await q.answer()
        lid = int(data.replace("cancel_",""))
        lessons = get_lessons()
        lesson = next((l for l in lessons if l["id"]==lid), None)
        if not lesson:
            await q.answer("Занятие не найдено.", show_alert=True); return
        lesson["status"] = "cancelled"
        save_lessons(lessons)
        student = lesson.get("student","")
        st = get_students().get(student,{})
        if st.get("tg_id"):
            try:
                await ctx.bot.send_message(st["tg_id"],
                    f"Привет, {student.split()[0]}! 👋\n\n"
                    f"К сожалению, занятие {fmt_date(lesson['date'])} в {lesson['time']} "
                    "отменяется.\n\n"
                    "Репетитор Илья свяжется с тобой для переноса:\n"
                    f"📱 Telegram: {TUTOR_TG}\n"
                    f"📞 Телефон: {TUTOR_PHONE}")
            except: pass
        await q.edit_message_reply_markup(reply_markup=None)
        await ctx.bot.send_message(q.message.chat_id,
            f"✅ Занятие с {student} отменено. Ученик уведомлён.")
        return
    if data == "show_week":
        if is_admin(update):
            await show_week_fn(update, ctx, edit=True)
        return
    if data == "cb_students":
        await q.answer()
        students = get_students()
        if not students:
            await q.edit_message_text("Учеников нет. Добавь через /addstudent")
            return
        text = "👥 *Ученики:*\n\n"
        for name, st in students.items():
            sub = st.get("subscription_left", 0)
            text += f"• *{name}* · {sub} ур. в абонементе\n"
        await q.edit_message_text(text, parse_mode="Markdown")
        return
    if data == "my_lessons":
        await my_lessons_callback(update, ctx)
        return
    if data.startswith("hw_"):
        await hw_callback(update, ctx)
        return
    # Продлить повторяющееся расписание на 4 недели
    if data.startswith("edit_pick_"):
        await edit_pick_cb(update, ctx)
        return
    if data.startswith("edit_field_"):
        await edit_field_cb(update, ctx)
        return
    if data.startswith("extend_"):
        await q.answer()
        parts = data.split("_", 4)  # extend_ИМЯ_ВРЕМЯ_ZOOM_ДАТА
        if len(parts) < 5:
            await q.edit_message_text("Ошибка данных."); return
        _, student, time_str, zoom_str, last_date_str = parts
        try:
            last_date = date.fromisoformat(last_date_str)
        except:
            await q.edit_message_text("Ошибка даты."); return
        lessons = get_lessons()
        added = []
        for week in range(1, 5):  # следующие 4 недели после последнего
            new_date = last_date + timedelta(weeks=week)
            lesson = {
                "id": next_id(lessons),
                "student": student,
                "date": new_date.isoformat(),
                "time": time_str,
                "zoom": zoom_str,
                "status": "active",
                "recurring": True
            }
            lessons.append(lesson)
            added.append(lesson)
        save_lessons(lessons)
        dates_str = "\n".join(
            f"  📅 {fmt_date(l['date'])} · 🕐 {l['time']}"
            for l in added)
        await q.edit_message_text(
            f"✅ Расписание продлено!\n\n"
            f"👤 *{student}*\n{dates_str}",
            parse_mode="Markdown")
        return

    # Удалить все повторяющиеся занятия ученика (истёкшие)
    if data.startswith("expire_"):
        await q.answer()
        student = data.replace("expire_","")
        lessons = get_lessons()
        today = now_local().date()
        # Удаляем только будущие recurring занятия этого ученика
        lessons = [l for l in lessons
                   if not (l.get("student")==student and l.get("recurring")
                   and (d:=parse_date(l.get("date",""))) and d >= today)]
        save_lessons(lessons)
        await q.edit_message_text(
            f"🗑 Расписание *{student}* завершено.\n"
            "Прошедшие занятия сохранены.",
            parse_mode="Markdown")
        return

    if data.startswith("remove_student_"):
        await q.answer()
        name = data.replace("remove_student_","")
        students = get_students()
        if name not in students:
            await q.edit_message_text("Ученик не найден."); return
        # Удаляем все занятия ученика
        lessons = [l for l in get_lessons() if l.get("student") != name]
        save_lessons(lessons)
        del students[name]
        save_students(students)
        await q.edit_message_text(
            f"✅ Ученик *{name}* удалён из расписания.\n"
            f"Все его занятия также удалены.",
            parse_mode="Markdown")
        return
    if data.startswith("recurring_yes") or data.startswith("recurring_no"):
        await got_lesson_recurring(update, ctx)
        return
    if data.startswith("done_"):
        await done_callback(update, ctx)
        return
    if data == "cancel_conv":
        await conv_cancel(update, ctx)
        return
    if data == "add_more_date":
        await more_date_yes(update, ctx)
        return
    if data == "no_more_date":
        await more_date_no(update, ctx)
        return
    # Запрос на перенос от ученика
    if data.startswith("student_move_") or data.startswith("student_cancel_"):
        await student_request(update, ctx)
        return
    await q.answer()

async def student_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ученик просит перенести/отменить — уведомляем репетитора"""
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    name = get_student_name(uid)
    if not name: return
    action = "перенести" if "move" in q.data else "отменить"
    lid = int(q.data.split("_")[-1])
    lessons = get_lessons()
    lesson = next((l for l in lessons if l["id"]==lid), None)
    if not lesson: return

    # Помечаем занятие как "ожидает решения" — напоминания прекращаются
    lesson["status"] = "pending_cancel"
    save_lessons(lessons)

    # Уведомляем репетитора
    try:
        await ctx.bot.send_message(ADMIN_ID,
            f"📨 Запрос от ученика!\n\n"
            f"👤 *{name}* хочет *{action}* занятие:\n"
            f"📅 {fmt_date(lesson['date'])} · 🕐 {lesson['time']}\n\n"
            f"Свяжись с учеником в Telegram.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Подтвердить отмену", callback_data=f"cancel_{lid}"),
                InlineKeyboardButton("🔄 Перенести", callback_data=f"admin_move_{lid}")
            ]]))
    except: pass
    await q.edit_message_text(
        f"✅ Уведомление отправлено репетитору!\n\n"
        f"Напоминания об этом занятии приостановлены.\n"
        f"Илья свяжется с тобой:\n"
        f"📱 Telegram: {TUTOR_TG}\n"
        f"📞 Телефон: {TUTOR_PHONE}")

# ── ПЛАНИРОВЩИК ───────────────────────────────────────────────────────────────
# ── ГЛАВНОЕ МЕНЮ РЕПЕТИТОРА (постоянные кнопки) ──────────────────────────────
from telegram import ReplyKeyboardMarkup, KeyboardButton

ADMIN_REPLY_KB = ReplyKeyboardMarkup([
    ["📅 Сегодня",        "📆 Неделя"],
    ["➕ Добавить занятие", "✅ Провёл занятие"],
    ["📚 Напомнить о ДЗ", "👥 Ученики"],
], resize_keyboard=True)

async def send_admin_main_menu(bot, text="Выбери действие:"):
    """Отправляет сообщение с постоянной клавиатурой репетитору"""
    try:
        await bot.send_message(ADMIN_ID, text,
            reply_markup=ADMIN_REPLY_KB)
    except: pass

async def handle_reply_kb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на кнопки постоянной клавиатуры (кроме ➕ — он в ConvHandler)"""
    if not is_admin(update): return
    text = update.message.text
    if text == "📅 Сегодня":
        await cmd_today(update, ctx)
    elif text == "📆 Неделя":
        await cmd_week(update, ctx)
    elif text == "✅ Провёл занятие":
        await cmd_done(update, ctx)
    elif text == "📚 Напомнить о ДЗ":
        await cmd_homework(update, ctx)
    elif text == "👥 Ученики":
        await cmd_students(update, ctx)

async def morning_summary(bot):
    today = today_iso()
    lessons = [l for l in get_lessons()
               if l.get("date","")[:10]==today and l.get("status","active")=="active"]
    lessons.sort(key=lambda l: l.get("time",""))
    if not lessons:
        try:
            await bot.send_message(ADMIN_ID,
                "☀️ Илья, доброе утро!\n\nСегодня занятий нет — можно отдохнуть 😊",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📆 Расписание на неделю", callback_data="show_week")
                ]]))
        except: pass
        return
    n = len(lessons)
    ending = "е" if n == 1 else "я" if n < 5 else "й"
    date_str = now_local().strftime("%d.%m.%Y")
    text = (f"☀️ *Илья, доброе утро!*\n\n"
            f"Сегодня {date_str} у тебя {n} занятий{ending}:\n\n")
    for l in lessons:
        text += f"🕐 {l['time']} — *{l['student']}*\n"
    text += "\nВсе ссылки на Zoom найдёшь на платформе 🖥\n\nПродуктивного дня! 💪"
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Провёл занятие", callback_data="done_pick"),
                InlineKeyboardButton("📆 Неделя", callback_data="show_week")
            ]]), disable_web_page_preview=True)
    except: pass
    # Карточка на каждое занятие с кнопками
    for l in lessons:
        card = f"🕐 *{l['time']}* — {l['student']}"
        if l.get("zoom"): card += f"\n🔗 [Zoom]({l['zoom']})"
        try:
            await bot.send_message(ADMIN_ID, card, parse_mode="Markdown",
                reply_markup=admin_lesson_kb(l["id"]),
                disable_web_page_preview=True)
        except: pass

async def send_reminders(bot, sent_set: set):
    """
    sent_set — множество ключей уже отправленных напоминаний.
    Формат ключа: "lesson_id:type" где type = "4h" или "15m"
    Это предотвращает дублирование при повторных вызовах в ту же минуту.
    """
    now = now_local()
    # Пропускаем занятия со статусом cancelled, done, pending_cancel
    active_statuses = {"active"}
    lessons = [l for l in get_lessons() if l.get("status","active") in active_statuses]
    students = get_students()
    for lesson in lessons:
        ldt = lesson_datetime(lesson)
        if not ldt: continue
        diff = (ldt - now).total_seconds() / 60
        student = lesson.get("student","")
        st = students.get(student, {})
        time_str = lesson.get("time","")
        zoom = lesson.get("zoom","")
        lid = lesson["id"]

        # За 4 часа — только ученику
        key_4h = f"{lid}:4h"
        if 238 <= diff <= 242 and key_4h not in sent_set:
            sent_set.add(key_4h)
            if st.get("tg_id"):
                try:
                    msg = (f"Привет, {student.split()[0]}! 👋\n\n"
                           f"Напоминаю — сегодня в *{time_str}* урок.\n"
                           f"Готовься, увидимся! 📚\n\n"
                           f"Ссылка для входа — на платформе:\n"
                           f"🖥 {PLATFORM_URL}")
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("📅 Мои занятия", callback_data="my_lessons"),
                        InlineKeyboardButton("🖥 Платформа", url=PLATFORM_URL)
                    ], [
                        InlineKeyboardButton("🔄 Хочу перенести", callback_data=f"student_move_{lid}"),
                        InlineKeyboardButton("❌ Хочу отменить", callback_data=f"student_cancel_{lid}")
                    ]])
                    await bot.send_message(st["tg_id"], msg, parse_mode="Markdown",
                        reply_markup=kb, disable_web_page_preview=True)
                except: pass

        # За 15 минут — обоим
        key_15m = f"{lid}:15m"
        if 13 <= diff <= 17 and key_15m not in sent_set:
            sent_set.add(key_15m)
            # Репетитору
            try:
                msg = (f"🔔 Через 15 минут — *{student}*!\n\n"
                       f"🕐 {time_str}\n"
                       f"🖥 [Открыть платформу]({ADMIN_PLATFORM_URL})\n"
                       f"_Не забудь проверить ссылку Zoom на платформе_")
                await bot.send_message(ADMIN_ID, msg, parse_mode="Markdown",
                    reply_markup=admin_lesson_kb(lid),
                    disable_web_page_preview=True)
            except: pass
            # Ученику
            if st.get("tg_id"):
                try:
                    msg = (f"🔔 {student.split()[0]}, урок начинается через 15 минут!\n\n"
                           f"🕐 {time_str} (по МСК)\n")
                    if zoom: msg += f"🔗 [Войти в Zoom]({zoom})\n"
                    msg += f"🖥 [Открыть платформу]({PLATFORM_URL})"
                    await bot.send_message(st["tg_id"], msg, parse_mode="Markdown",
                        disable_web_page_preview=True)
                except: pass

async def check_recurring_expiry(bot):
    """
    Каждое утро проверяем: есть ли ученики у которых последнее
    повторяющееся занятие через 3 дня. Если да — уведомляем Илью.
    """
    today = now_local().date()
    warn_date = today + timedelta(days=3)
    lessons = get_lessons()

    # Группируем повторяющиеся занятия по ученику
    from collections import defaultdict
    recurring_by_student = defaultdict(list)
    for l in lessons:
        if l.get("recurring") and l.get("status","active") == "active":
            d = parse_date(l.get("date",""))
            if d:
                recurring_by_student[l["student"]].append(d)

    sent_keys = set()
    for student, dates in recurring_by_student.items():
        last_date = max(dates)
        # Если последнее занятие через 3 дня — предупреждаем
        if last_date == warn_date:
            key = f"expiry_{student}_{last_date}"
            if key in sent_keys: continue
            sent_keys.add(key)

            # Считаем день недели последнего занятия для продления
            # Найдём время занятия
            last_lesson = next(
                (l for l in lessons
                 if l.get("student")==student and l.get("recurring")
                 and parse_date(l.get("date",""))==last_date),
                None)
            time_str = last_lesson.get("time","") if last_lesson else ""
            zoom_str = last_lesson.get("zoom","") if last_lesson else ""

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔁 Продлить ещё на 4 недели",
                    callback_data=f"extend_{student}_{time_str}_{zoom_str}_{last_date}"),
                InlineKeyboardButton(
                    "🗑 Удалить расписание",
                    callback_data=f"expire_{student}")
            ]])
            try:
                await bot.send_message(ADMIN_ID,
                    f"⚠️ Расписание заканчивается!\n\n"
                    f"👤 *{student}*\n"
                    f"Последнее занятие: {fmt_date(last_date.isoformat())} в {time_str}\n\n"
                    "Продлить расписание ещё на 4 недели?",
                    parse_mode="Markdown",
                    reply_markup=kb)

            except: pass
async def scheduler(bot):
    sent_morning = None
    sent_reminders = set()  # ключи уже отправленных напоминаний
    last_reset_day = None
    while True:
        now = now_local()
        day_key = now.strftime("%Y-%m-%d")
        # Сбрасываем множество отправленных в начале каждого дня
        if day_key != last_reset_day:
            sent_reminders = set()
            last_reset_day = day_key
        # Утренняя сводка в REMIND_HOUR:00
        if now.hour == REMIND_HOUR and now.minute == 0 and sent_morning != day_key:
            sent_morning = day_key
            await morning_summary(bot)
            await check_recurring_expiry(bot)
        # Напоминания о занятиях (с дедупликацией)
        await send_reminders(bot, sent_reminders)
        await asyncio.sleep(60)

# ── ЗАПУСК ────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Диалог: добавить ученика
    add_student_conv = ConversationHandler(
        entry_points=[CommandHandler("addstudent", cmd_addstudent)],
        states={
            ADD_STUDENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_student_name)],
            ADD_STUDENT_TG:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_student_tg)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        allow_reentry=True)

    # Диалог: добавить занятие
    add_lesson_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addlesson", cmd_addlesson),
            CallbackQueryHandler(cmd_addlesson, pattern="^cb_addlesson$"),
            MessageHandler(filters.TEXT & filters.Regex(r"^➕ Добавить занятие$"), cmd_addlesson),
        ],
        states={
            ADD_LESSON_STUDENT:   [CallbackQueryHandler(got_lesson_student, pattern="^ls_")],
            ADD_LESSON_DATE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, got_lesson_date)],
            ADD_LESSON_MORE_DATE: [
                CallbackQueryHandler(more_date_yes, pattern="^add_more_date$"),
                CallbackQueryHandler(more_date_no, pattern="^no_more_date$"),
            ],
            ADD_LESSON_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_lesson_time)],
            ADD_LESSON_ZOOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_lesson_zoom)],
            ADD_LESSON_SUB:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_lesson_sub)],
            ADD_LESSON_RECURRING: [
                CallbackQueryHandler(got_lesson_recurring, pattern="^recurring_(yes|no)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", conv_cancel),
            CallbackQueryHandler(conv_cancel, pattern="^cancel_conv$"),
        ],
        allow_reentry=True)

    # Диалог: редактировать занятие
    edit_lesson_conv = ConversationHandler(
        entry_points=[
            CommandHandler("editlesson", cmd_edit_lesson),
            MessageHandler(filters.TEXT & filters.Regex(r"^✏️ Изменить занятие$"), cmd_edit_lesson),
        ],
        states={
            EDIT_LESSON_PICK:  [CallbackQueryHandler(edit_pick_cb, pattern="^edit_pick_")],
            EDIT_LESSON_FIELD: [CallbackQueryHandler(edit_field_cb, pattern="^edit_field_")],
            EDIT_LESSON_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_msg)],
        },
        fallbacks=[
            CommandHandler("cancel", conv_cancel),
            CallbackQueryHandler(conv_cancel, pattern="^cancel_conv$"),
        ],
        allow_reentry=True)

    app.add_handler(add_student_conv)
    app.add_handler(add_lesson_conv)
    app.add_handler(edit_lesson_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("homework", cmd_homework))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("students", cmd_students))
    app.add_handler(CommandHandler("removestudent", cmd_removestudent))
    app.add_handler(CommandHandler("cancelesson", cmd_cancel_lesson))
    app.add_handler(CommandHandler("movelesson", cmd_move_lesson))
    app.add_handler(CommandHandler("editlesson", cmd_edit_lesson))
    # Кнопки постоянной клавиатуры репетитора
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(
            r"^(📅 Сегодня|📆 Неделя|✅ Провёл занятие|📚 Напомнить о ДЗ|👥 Ученики)$"),
        handle_reply_kb), group=0)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/(cancel|move)_\d+"),
        handle_lesson_command))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def post_init(application):
        asyncio.create_task(scheduler(application.bot))
        from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
        admin_commands = [
            BotCommand("today",         "📅 Занятия сегодня"),
            BotCommand("week",          "📆 Расписание на неделю"),
            BotCommand("addlesson",     "➕ Добавить занятие"),
            BotCommand("done",          "✅ Провёл занятие"),
            BotCommand("homework",      "📚 Напомнить о ДЗ"),
            BotCommand("addstudent",    "👤 Добавить ученика"),
            BotCommand("students",      "👥 Список учеников"),
            BotCommand("removestudent", "🗑 Удалить ученика"),
            BotCommand("cancelesson",   "❌ Отменить занятие"),
            BotCommand("movelesson",    "🔄 Перенести занятие"),
            BotCommand("editlesson",    "✏️ Изменить занятие"),
            BotCommand("cancel",        "❌ Отмена диалога"),
        ]
        student_commands = [
            BotCommand("start",    "🏠 Главное меню"),
            BotCommand("platform", "🖥 Открыть платформу"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=ADMIN_ID))
            await application.bot.set_my_commands(
                student_commands,
                scope=BotCommandScopeDefault())
        except Exception as e:
            print(f"Меню команд: {e}")
        try:
            await send_admin_main_menu(application.bot)
        except Exception as e:
            print(f"Главное меню: {e}")
        print("✅ Бот-напоминалкин запущен!")


    app.post_init = post_init
    print("🚀 Запускаю бота...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
