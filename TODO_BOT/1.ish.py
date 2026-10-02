import asyncio  # asinxron ishlash uchun
import os  # muhit o'zgaruvchilarini o'qish
import re  # sanani regex bilan tekshirish
import sqlite3  # SQLite baza
from contextlib import closing  # ulanishni avtomatik yopish
from datetime import date, datetime  # sana va vaqt

from aiogram import Bot, Dispatcher, F, Router, html  # aiogram asosiy klasslari
from aiogram.client.default import DefaultBotProperties  # botning standart sozlamalari
from aiogram.enums import ParseMode  # xabar formati (HTML)
from aiogram.filters import CommandStart  # /start filtri
from aiogram.filters.callback_data import CallbackData  # tipli callback ma'lumot
from aiogram.fsm.context import FSMContext  # holat (state) boshqaruvi
from aiogram.fsm.state import State, StatesGroup  # holatlar guruhi
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message  # Telegram tiplari
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder  # tugma yasagichlar
from dotenv import load_dotenv  # .env faylini o'qish

load_dotenv()  # .env dagi o'zgaruvchilarni yuklash
TOKEN = os.getenv("BOT_TOKEN")  # tokenni .env dan olish (kodga yozilmaydi!)
if not TOKEN:
    raise SystemExit("BOT_TOKEN o'rnatilmagan")  # token bo'lmasa to'xtash

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),  # xabarlar HTML formatda
)
dp = Dispatcher()  # dispatcher
router = Router()  # handlerlar to'plami

DB_FILE = "todo.db"  # baza fayli nomi

ADD_BTN = "➕ Vazifa qo'shish"  # menyu tugmasi: qo'shish
LIST_BTN = "📋 Vazifalarim"  # menyu tugmasi: ro'yxat
STATS_BTN = "📊 Statistika"  # menyu tugmasi: statistika
SKIP_BTN = "⏭ O'tkazib yuborish"  # muddatni o'tkazib yuborish


class AddTask(StatesGroup):
    title = State()  # vazifa nomini kutish holati
    deadline = State()  # muddatni kutish holati


class TaskCB(CallbackData, prefix="task"):
    action: str  # amal: done yoki delete
    id: int  # vazifa ID


def run(sql, params=(), fetch=False):
    with closing(sqlite3.connect(DB_FILE)) as conn:  # bazaga ulanish (oxirida yopiladi)
        conn.row_factory = sqlite3.Row  # natijani nom bo'yicha olish
        cur = conn.execute(sql, params)  # so'rovni bajarish
        conn.commit()  # o'zgarishlarni saqlash
        return cur.fetchall() if fetch else None  # kerak bo'lsa natijani qaytarish


def init_db():
    run("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        full_name TEXT,
        joined_at TEXT
    )""")  # foydalanuvchilar jadvali
    run("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        deadline TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT
    )""")  # vazifalar jadvali


def get_task(task_id, user_id):
    rows = run("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id), fetch=True)  # faqat o'z vazifasi
    return rows[0] if rows else None  # topilmasa None


def now():
    return datetime.now().isoformat(timespec="seconds")  # hozirgi vaqt (soniyagacha)


def deadline_text(deadline):
    if not deadline:
        return "belgilanmagan"  # muddat yo'q bo'lsa
    return date.fromisoformat(deadline).strftime("%d.%m.%Y")  # KK.OO.YYYY ko'rinishi


def task_text(task):
    if task["done"]:
        status = "✅ Bajarilgan"  # bajarilgan
    elif task["deadline"] and date.fromisoformat(task["deadline"]) < date.today():
        status = "⚠️ Muddati o'tgan"  # muddati o'tib ketgan
    else:
        status = "⏳ Bajarilmagan"  # hali bajarilmagan
    return (
        f"📌 <b>{html.quote(task['title'])}</b>\n"  # nom (HTML xavfsiz)
        f"⏰ Muddat: {deadline_text(task['deadline'])}\n"
        f"{status}"
    )


def menu_kb():
    builder = ReplyKeyboardBuilder()  # pastki menyu yasagich
    builder.button(text=ADD_BTN)
    builder.button(text=LIST_BTN)
    builder.button(text=STATS_BTN)
    builder.adjust(1, 2)  # 1-qatorda 1 ta, 2-qatorda 2 ta tugma
    return builder.as_markup(resize_keyboard=True)  # ixcham klaviatura


def skip_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text=SKIP_BTN)  # faqat "o'tkazib yuborish" tugmasi
    return builder.as_markup(resize_keyboard=True)


def task_kb(task) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()  # xabar ostidagi tugmalar
    if not task["done"]:
        builder.button(text="✅ Bajarildi", callback_data=TaskCB(action="done", id=task["id"]))  # faqat bajarilmaganda
    builder.button(text="🗑 O'chirish", callback_data=TaskCB(action="delete", id=task["id"]))
    builder.adjust(2)  # bir qatorda 2 tadan
    return builder.as_markup()


@router.message(CommandStart())  # /start buyrug'i
async def start(message: Message, state: FSMContext):
    await state.clear()  # eski holatni tozalash
    run(
        "INSERT OR IGNORE INTO users (user_id, full_name, joined_at) VALUES (?, ?, ?)",
        (message.from_user.id, message.from_user.full_name, now()),  # foydalanuvchini saqlash (takror bo'lmaydi)
    )
    await message.answer(
        f"Assalomu alaykum, <b>{html.quote(message.from_user.full_name)}</b>! 👋\n\n"
        "Men vazifalaringizni boshqarishda yordam beraman.\n"
        "Pastdagi menyudan tanlang 👇",
        reply_markup=menu_kb(),  # menyuni ko'rsatish
    )


@router.message(F.text == ADD_BTN)  # "Vazifa qo'shish" bosilganda
async def add_start(message: Message, state: FSMContext):
    await state.set_state(AddTask.title)  # nom kutish holatiga o'tish
    await message.answer("📝 Vazifa nomini yozing:")


@router.message(F.text == LIST_BTN)  # "Vazifalarim" bosilganda
async def my_tasks(message: Message, state: FSMContext):
    await state.clear()  # holatni tozalash
    tasks = run(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY done, id DESC LIMIT 20",
        (message.from_user.id,),  # bajarilmaganlar birinchi, oxirgi 20 ta
        fetch=True,
    )
    if not tasks:
        await message.answer(
            "📭 Sizda hali vazifa yo'q.\n"
            f"«{ADD_BTN}» tugmasi bilan birinchi vazifangizni qo'shing!"  # bo'sh ro'yxat xabari
        )
        return

    await message.answer(f"📋 <b>Vazifalaringiz</b> ({len(tasks)} ta):")  # sarlavha
    for task in tasks:
        await message.answer(task_text(task), reply_markup=task_kb(task))  # har vazifa alohida xabar


@router.message(F.text == STATS_BTN)  # "Statistika" bosilganda
async def stats(message: Message, state: FSMContext):
    await state.clear()
    row = run(
        "SELECT COUNT(*) AS total, COALESCE(SUM(done), 0) AS done FROM tasks WHERE user_id = ?",
        (message.from_user.id,),  # jami va bajarilganlar soni
        fetch=True,
    )[0]
    total, done = row["total"], row["done"]
    left = total - done  # qolganlar
    percent = done * 100 // total if total else 0  # bajarilish foizi
    bar = "🟩" * (percent // 10) + "⬜" * (10 - percent // 10)  # progress bar

    await message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"📌 Jami: {total}\n"
        f"✅ Bajarilgan: {done}\n"
        f"⏳ Qolgan: {left}\n\n"
        f"{bar} {percent}%"
    )


@router.message(AddTask.title)  # nom kutilayotganda kelgan xabar
async def add_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()  # matnni tozalash
    if not title or title.startswith("/") or len(title) > 100:  # tekshiruv
        await message.answer("⚠️ Vazifa nomini matn bilan yozing (1 dan 100 tagacha belgi).")
        return

    await state.update_data(title=title)  # nomni vaqtincha saqlash
    await state.set_state(AddTask.deadline)  # muddat kutish holatiga o'tish
    await message.answer(
        "⏰ Muddatini kiriting: <code>KK.OO.YYYY</code>\n"
        "Masalan: 25.10.2026\n\n"
        "Yoki pastdagi tugma bilan o'tkazib yuboring.",
        reply_markup=skip_kb(),  # "o'tkazib yuborish" tugmasi
    )


@router.message(AddTask.deadline)  # muddat kutilayotganda kelgan xabar
async def add_deadline(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    deadline = None  # standart: muddat yo'q

    if text != SKIP_BTN:  # o'tkazib yubormagan bo'lsa
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):  # format tekshiruvi
            await message.answer(
                "⚠️ Format noto'g'ri. Sanani <code>KK.OO.YYYY</code> ko'rinishida yozing.\n"
                "Masalan: 25.10.2026"
            )
            return
        try:
            deadline = datetime.strptime(text, "%d.%m.%Y").date()  # matnni sanaga aylantirish
        except ValueError:
            await message.answer("⚠️ Bunday sana mavjud emas. Qaytadan kiriting.")  # masalan 31.02
            return
        if deadline < date.today():  # o'tgan sana bo'lmasin
            await message.answer(
                "⚠️ O'tib ketgan sanani kiritib bo'lmaydi. Bugungi yoki keyingi sanani yozing."
            )
            return

    data = await state.get_data()  # saqlangan nomni olish
    await state.clear()  # holatni tugatish
    run(
        "INSERT INTO tasks (user_id, title, deadline, created_at) VALUES (?, ?, ?, ?)",
        (message.from_user.id, data["title"], deadline.isoformat() if deadline else None, now()),  # vazifani saqlash
    )
    await message.answer(
        "✅ <b>Vazifa qo'shildi!</b>\n\n"
        f"📌 {html.quote(data['title'])}\n"
        f"⏰ Muddat: {deadline.strftime('%d.%m.%Y') if deadline else 'belgilanmagan'}",
        reply_markup=menu_kb(),  # asosiy menyuga qaytish
    )


@router.callback_query(TaskCB.filter())  # "Bajarildi" / "O'chirish" tugmalari
async def task_action(callback: CallbackQuery, callback_data: TaskCB):
    user_id = callback.from_user.id
    task = get_task(callback_data.id, user_id)  # vazifani topish

    if task is None:
        await callback.message.edit_text("🗑 Bu vazifa allaqachon o'chirilgan.")  # topilmadi
        await callback.answer()
        return

    if callback_data.action == "done":
        run("UPDATE tasks SET done = 1 WHERE id = ? AND user_id = ?", (task["id"], user_id))  # bajarilgan deb belgilash
        task = get_task(task["id"], user_id)  # yangilangan holatni olish
        await callback.message.edit_text(task_text(task), reply_markup=task_kb(task))  # xabarni yangilash
        await callback.answer("✅ Bajarildi!")
    else:
        run("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task["id"], user_id))  # vazifani o'chirish
        await callback.message.edit_text("🗑 Vazifa o'chirildi.")
        await callback.answer("O'chirildi")


async def main():
    init_db()  # bazani tayyorlash
    print("todo bot ishga tushdi >>>")
    dp.include_router(router)  # handlerlarni ulash
    await dp.start_polling(bot)  # botni ishga tushirish


if __name__ == "__main__":
    asyncio.run(main())  # dasturni boshlash