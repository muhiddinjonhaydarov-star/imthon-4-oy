import asyncio
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN = "8805968573:AAFVLOmUh2eECc3LTz1eBWcCIWxqaPOtc-4"

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()

DB_FILE = "todo.db"

ADD_BTN = "➕ Vazifa qo'shish"
LIST_BTN = "📋 Vazifalarim"
STATS_BTN = "📊 Statistika"
SKIP_BTN = "⏭ O'tkazib yuborish"


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
        created_at TEXT
    )""")


def get_task(task_id, user_id):
    rows = run("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id), fetch=True)
    return rows[0] if rows else None


def now():
    return datetime.now().isoformat(timespec="seconds")


def deadline_text(deadline):
    if not deadline:
        return "belgilanmagan"
    return date.fromisoformat(deadline).strftime("%d.%m.%Y")


def task_text(task):
    if task["done"]:
        status = "✅ Bajarilgan"
    elif task["deadline"] and date.fromisoformat(task["deadline"]) < date.today():
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
    builder.adjust(1, 2)
    return builder.as_markup(resize_keyboard=True)


def skip_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text=SKIP_BTN)
    return builder.as_markup(resize_keyboard=True)


def task_kb(task) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not task["done"]:
        builder.button(text="✅ Bajarildi", callback_data=TaskCB(action="done", id=task["id"]))
    builder.button(text="🗑 O'chirish", callback_data=TaskCB(action="delete", id=task["id"]))
    builder.adjust(2)
    return builder.as_markup()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    run(
        "INSERT OR IGNORE INTO users (user_id, full_name, joined_at) VALUES (?, ?, ?)",
        (message.from_user.id, message.from_user.full_name, now()),
    )
    await message.answer(
        f"Assalomu alaykum, <b>{html.quote(message.from_user.full_name)}</b>! 👋\n\n"
        "Men vazifalaringizni boshqarishda yordam beraman.\n"
        "Pastdagi menyudan tanlang 👇",
        reply_markup=menu_kb(),
    )


@router.message(F.text == ADD_BTN)
async def add_start(message: Message, state: FSMContext):
    await state.set_state(AddTask.title)
    await message.answer("📝 Vazifa nomini yozing:")


@router.message(F.text == LIST_BTN)
async def my_tasks(message: Message, state: FSMContext):
    await state.clear()
    tasks = run(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY done, id DESC LIMIT 20",
        (message.from_user.id,),
        fetch=True,
    )
    if not tasks:
        await message.answer(
            "📭 Sizda hali vazifa yo'q.\n"
            f"«{ADD_BTN}» tugmasi bilan birinchi vazifangizni qo'shing!"
        )
        return

    await message.answer(f"📋 <b>Vazifalaringiz</b> ({len(tasks)} ta):")
    for task in tasks:
        await message.answer(task_text(task), reply_markup=task_kb(task))


@router.message(F.text == STATS_BTN)
async def stats(message: Message, state: FSMContext):
    await state.clear()
    row = run(
        "SELECT COUNT(*) AS total, COALESCE(SUM(done), 0) AS done FROM tasks WHERE user_id = ?",
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
        f"{bar} {percent}%"
    )


@router.message(AddTask.title)
async def add_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title or title.startswith("/") or len(title) > 100:
        await message.answer("⚠️ Vazifa nomini matn bilan yozing (1 dan 100 tagacha belgi).")
        return

    await state.update_data(title=title)
    await state.set_state(AddTask.deadline)
    await message.answer(
        "⏰ Muddatini kiriting: <code>KK.OO.YYYY</code>\n"
        "Masalan: 25.10.2026\n\n"
        "Yoki pastdagi tugma bilan o'tkazib yuboring.",
        reply_markup=skip_kb(),
    )


@router.message(AddTask.deadline)
async def add_deadline(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    deadline = None

    if text != SKIP_BTN:
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
            await message.answer(
                "⚠️ Format noto'g'ri. Sanani <code>KK.OO.YYYY</code> ko'rinishida yozing.\n"
                "Masalan: 25.10.2026"
            )
            return
        try:
            deadline = datetime.strptime(text, "%d.%m.%Y").date()
        except ValueError:
            await message.answer("⚠️ Bunday sana mavjud emas. Qaytadan kiriting.")
            return
        if deadline < date.today():
            await message.answer(
                "⚠️ O'tib ketgan sanani kiritib bo'lmaydi. Bugungi yoki keyingi sanani yozing."
            )
            return

    data = await state.get_data()
    await state.clear()
    run(
        "INSERT INTO tasks (user_id, title, deadline, created_at) VALUES (?, ?, ?, ?)",
        (message.from_user.id, data["title"], deadline.isoformat() if deadline else None, now()),
    )
    await message.answer(
        "✅ <b>Vazifa qo'shildi!</b>\n\n"
        f"📌 {html.quote(data['title'])}\n"
        f"⏰ Muddat: {deadline.strftime('%d.%m.%Y') if deadline else 'belgilanmagan'}",
        reply_markup=menu_kb(),
    )


@router.callback_query(TaskCB.filter())
async def task_action(callback: CallbackQuery, callback_data: TaskCB):
    user_id = callback.from_user.id
    task = get_task(callback_data.id, user_id)

    if task is None:
        await callback.message.edit_text("🗑 Bu vazifa allaqachon o'chirilgan.")
        await callback.answer()
        return

    if callback_data.action == "done":
        run("UPDATE tasks SET done = 1 WHERE id = ? AND user_id = ?", (task["id"], user_id))
        task = get_task(task["id"], user_id)
        await callback.message.edit_text(task_text(task), reply_markup=task_kb(task))
        await callback.answer("✅ Bajarildi!")
    else:
        run("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task["id"], user_id))
        await callback.message.edit_text("🗑 Vazifa o'chirildi.")
        await callback.answer("O'chirildi")


async def main():
    init_db()
    print("todo bot ishga tushdi >>>")
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())