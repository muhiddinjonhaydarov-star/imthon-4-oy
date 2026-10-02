import asyncio
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, time as dtime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("BOT_TOKEN o'rnatilmagan")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()

DB_FILE = "todo.db"
TZ = timezone(timedelta(hours=5))

ADD_BTN = "➕ Vazifa qo'shish"
LIST_BTN = "📋 Vazifalarim"
STATS_BTN = "📊 Statistika"
DELETED_BTN = "🗑 O'chirilganlar"
SKIP_BTN = "⏭ O'tkazib yuborish"
CANCEL_BTN = "❌ Bekor qilish"

DEADLINE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{1,2}:\d{2}))?")


class AddTask(StatesGroup):
    title = State()
    deadline = State()


class TaskCB(CallbackData, prefix="task"):
    action: str
    id: int


def run(sql, params=(), fetch=False):
    with closing(sqlite3.connect(DB_FILE)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.fetchall() if fetch else None


def init_db():
    run("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        full_name TEXT,
        joined_at TEXT
    )""")
    run("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        deadline TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        deleted INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT
    )""")
    cols = [r["name"] for r in run("PRAGMA table_info(tasks)", fetch=True)]
    if "deleted" not in cols:
        run("ALTER TABLE tasks ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    if "deleted_at" not in cols:
        run("ALTER TABLE tasks ADD COLUMN deleted_at TEXT")


def get_task(task_id, user_id):
    rows = run("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id), fetch=True)
    return rows[0] if rows else None


def now():
    return datetime.now(TZ).replace(tzinfo=None)


def parse_deadline(value):
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), dtime(23, 59))
    return datetime.fromisoformat(value)


def deadline_text(deadline):
    if not deadline:
        return "belgilanmagan"
    if len(deadline) == 10:
        return date.fromisoformat(deadline).strftime("%d.%m.%Y")
    return datetime.fromisoformat(deadline).strftime("%d.%m.%Y %H:%M")


def task_text(task):
    if task["deleted"]:
        status = "🗑 O'chirilgan"
    elif task["done"]:
        status = "✅ Bajarilgan"
    elif task["deadline"] and parse_deadline(task["deadline"]) < now():
        status = "⚠️ Muddati o'tgan"
    else:
        status = "⏳ Bajarilmagan"
    return (
        f"📌 <b>{html.quote(task['title'])}</b>\n"
        f"⏰ Muddat: {deadline_text(task['deadline'])}\n"
        f"{status}"
    )


def menu_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text=ADD_BTN)
    builder.button(text=LIST_BTN)
    builder.button(text=STATS_BTN)
    builder.button(text=DELETED_BTN)
    builder.adjust(1, 2, 1)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def cancel_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text=CANCEL_BTN)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def skip_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text=SKIP_BTN)
    builder.button(text=CANCEL_BTN)
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def task_kb(task) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if task["deleted"]:
        builder.button(text="♻️ Tiklash", callback_data=TaskCB(action="restore", id=task["id"]))
    else:
        if task["done"]:
            builder.button(text="↩️ Qaytarish", callback_data=TaskCB(action="undo", id=task["id"]))
        else:
            builder.button(text="✅ Bajarildi", callback_data=TaskCB(action="done", id=task["id"]))
        builder.button(text="🗑 O'chirish", callback_data=TaskCB(action="delete", id=task["id"]))
    builder.adjust(2)
    return builder.as_markup()


async def save_task(message: Message, state: FSMContext, deadline):
    data = await state.get_data()
    await state.clear()
    value = deadline.isoformat(timespec="minutes") if deadline else None
    run(
        "INSERT INTO tasks (user_id, title, deadline, created_at) VALUES (?, ?, ?, ?)",
        (message.from_user.id, data["title"], value, now().isoformat(timespec="seconds")),
    )
    await message.answer(
        "✅ <b>Vazifa qo'shildi!</b>\n\n"
        f"📌 {html.quote(data['title'])}\n"
        f"⏰ Muddat: {deadline_text(value)}",
        reply_markup=menu_kb(),
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    run(
        "INSERT OR IGNORE INTO users (user_id, full_name, joined_at) VALUES (?, ?, ?)",
        (message.from_user.id, message.from_user.full_name, now().isoformat(timespec="seconds")),
    )
    await message.answer(
        f"Assalomu alaykum, <b>{html.quote(message.from_user.full_name)}</b>! 👋\n\n"
        "Men vazifalaringizni boshqarishda yordam beraman.\n"
        "Menyudan tanlang 👇",
        reply_markup=menu_kb(),
    )


@router.message(Command("menu"))
async def menu_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Menyudan tanlang 👇", reply_markup=menu_kb())


@router.message(F.text == CANCEL_BTN)
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Bekor qilindi.", reply_markup=menu_kb())


@router.message(F.text == ADD_BTN)
async def add_start(message: Message, state: FSMContext):
    await state.set_state(AddTask.title)
    await message.answer("📝 Vazifa nomini yozing:", reply_markup=cancel_kb())


@router.message(F.text == LIST_BTN)
async def my_tasks(message: Message, state: FSMContext):
    await state.clear()
    tasks = run(
        "SELECT * FROM tasks WHERE user_id = ? AND deleted = 0 ORDER BY done, id DESC LIMIT 20",
        (message.from_user.id,),
        fetch=True,
    )
    if not tasks:
        await message.answer(
            "📭 Sizda hali vazifa yo'q.\n"
            f"«{ADD_BTN}» tugmasi bilan birinchi vazifangizni qo'shing!",
            reply_markup=menu_kb(),
        )
        return

    await message.answer(f"📋 <b>Vazifalaringiz</b> ({len(tasks)} ta):", reply_markup=ReplyKeyboardRemove())
    for task in tasks:
        await message.answer(task_text(task), reply_markup=task_kb(task))


@router.message(F.text == DELETED_BTN)
async def deleted_tasks(message: Message, state: FSMContext):
    await state.clear()
    tasks = run(
        "SELECT * FROM tasks WHERE user_id = ? AND deleted = 1 ORDER BY deleted_at DESC LIMIT 20",
        (message.from_user.id,),
        fetch=True,
    )
    if not tasks:
        await message.answer("📭 O'chirilgan vazifalar yo'q.", reply_markup=menu_kb())
        return

    await message.answer(f"🗑 <b>O'chirilganlar</b> ({len(tasks)} ta):", reply_markup=ReplyKeyboardRemove())
    for task in tasks:
        await message.answer(task_text(task), reply_markup=task_kb(task))


@router.message(F.text == STATS_BTN)
async def stats(message: Message, state: FSMContext):
    await state.clear()
    row = run(
        "SELECT COUNT(*) AS total, COALESCE(SUM(done), 0) AS done FROM tasks WHERE user_id = ? AND deleted = 0",
        (message.from_user.id,),
        fetch=True,
    )[0]
    total, done = row["total"], row["done"]
    left = total - done
    percent = done * 100 // total if total else 0
    bar = "🟩" * (percent // 10) + "⬜" * (10 - percent // 10)

    await message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"📌 Jami: {total}\n"
        f"✅ Bajarilgan: {done}\n"
        f"⏳ Qolgan: {left}\n\n"
        f"{bar} {percent}%",
        reply_markup=menu_kb(),
    )


@router.message(AddTask.title)
async def add_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title or title.startswith("/") or len(title) > 100:
        await message.answer("⚠️ Vazifa nomini matn bilan yozing (1 dan 100 tagacha belgi).", reply_markup=cancel_kb())
        return

    await state.update_data(title=title)
    await state.set_state(AddTask.deadline)
    await message.answer(
        "⏰ Muddatini kiriting: <code>KK.OO.YYYY SS:DD</code>\n"
        "Masalan: 25.10.2026 18:30\n\n"
        "Soat yozilmasa, 23:59 deb olinadi.\n"
        "Yoki pastdagi tugma bilan o'tkazib yuboring.",
        reply_markup=skip_kb(),
    )


@router.message(AddTask.deadline)
async def add_deadline(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if text == SKIP_BTN:
        await save_task(message, state, None)
        return

    match = DEADLINE_RE.fullmatch(text)
    if not match:
        await message.answer(
            "⚠️ Format noto'g'ri. Muddatni <code>KK.OO.YYYY SS:DD</code> ko'rinishida yozing.\n"
            "Masalan: 25.10.2026 18:30",
            reply_markup=skip_kb(),
        )
        return

    day, hour = match.group(1), match.group(2) or "23:59"
    try:
        deadline = datetime.strptime(f"{day} {hour}", "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer("⚠️ Bunday sana yoki soat mavjud emas. Qaytadan kiriting.", reply_markup=skip_kb())
        return
    if deadline <= now():
        await message.answer(
            "⚠️ O'tib ketgan vaqtni kiritib bo'lmaydi. Kelajakdagi sana va soatni yozing.",
            reply_markup=skip_kb(),
        )
        return

    await save_task(message, state, deadline)


@router.callback_query(TaskCB.filter())
async def task_action(callback: CallbackQuery, callback_data: TaskCB):
    user_id = callback.from_user.id
    task = get_task(callback_data.id, user_id)

    if task is None:
        await callback.message.edit_text("❌ Bu vazifa topilmadi.")
        await callback.answer()
        return

    action = callback_data.action
    if action == "done":
        run("UPDATE tasks SET done = 1 WHERE id = ? AND user_id = ?", (task["id"], user_id))
        toast = "✅ Bajarildi!"
    elif action == "undo":
        run("UPDATE tasks SET done = 0 WHERE id = ? AND user_id = ?", (task["id"], user_id))
        toast = "↩️ Qaytarildi"
    elif action == "delete":
        run(
            "UPDATE tasks SET deleted = 1, deleted_at = ? WHERE id = ? AND user_id = ?",
            (now().isoformat(timespec="seconds"), task["id"], user_id),
        )
        toast = "🗑 O'chirildi"
    else:
        run("UPDATE tasks SET deleted = 0, deleted_at = NULL WHERE id = ? AND user_id = ?", (task["id"], user_id))
        toast = "♻️ Tiklandi"

    task = get_task(task["id"], user_id)
    await callback.message.edit_text(task_text(task), reply_markup=task_kb(task))
    await callback.answer(toast)


@router.message()
async def other(message: Message):
    await message.answer("Menyudan tanlang 👇", reply_markup=menu_kb())


async def main():
    init_db()
    print("todo bot ishga tushdi >>>")
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())