import asyncio  # asinxron ishlash uchun
import json  # JSON bilan ishlash
import logging  # log yozish
import os  # muhit o'zgaruvchilarini o'qish
import random  # savollarni aralashtirish
import sqlite3  # SQLite baza
import time  # vaqt (timestamp)
import uuid  # noyob sessiya ID
from contextlib import contextmanager  # with bilan ishlatiladigan funksiya yasash
from datetime import datetime, timedelta, timezone  # sana va vaqt
from pathlib import Path  # fayl yo'llari

from aiogram import Bot, Dispatcher, F, Router  # aiogram asosiy klasslari
from aiogram.filters import Command, CommandStart  # buyruq filtrlari
from aiogram.types import (  # Telegram tiplari
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dotenv import load_dotenv  # .env faylini o'qish uchun

load_dotenv()  # .env dagi o'zgaruvchilarni muhitga yuklash

BASE_DIR = Path(__file__).resolve().parent  # shu fayl turgan papka
QUESTIONS_PATH = BASE_DIR / "questions.json"  # savollar fayli yo'li
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "quiz.db"))  # baza yo'li (env yoki standart)
LETTERS = "ABCD"  # variant harflari
TZ = timezone(timedelta(hours=5))  # O'zbekiston vaqti (UTC+5)


router = Router()  # handlerlar to'plami


def load_questions() -> list[dict]:
    with open(QUESTIONS_PATH, encoding="utf-8") as f:  # faylni ochish
        data = json.load(f)  # JSON ni o'qish
    if len(data) < 10:  # savollar soni tekshiruvi
        raise ValueError("Kamida 10 ta savol bo'lishi kerak")
    for i, q in enumerate(data):  # har bir savolni tekshirish
        if len(q["options"]) != 4:  # variantlar 4 ta bo'lishi shart
            raise ValueError(f"{i + 1}-savolda 4 ta variant bo'lishi kerak")
        if q["answer"] not in range(4):  # javob indeksi 0..3 bo'lishi shart
            raise ValueError(f"{i + 1}-savolda 'answer' 0..3 oralig'ida bo'lishi kerak")
    return data  # tekshirilgan savollar


QUESTIONS = load_questions()  # savollarni yuklash
TOTAL = len(QUESTIONS)  # jami savollar soni


@contextmanager
def conn():
    con = sqlite3.connect(DB_PATH)  # bazaga ulanish
    con.row_factory = sqlite3.Row  # natijani nom bo'yicha olish imkoni
    try:
        yield con  # ulanishni berish
        con.commit()  # o'zgarishlarni saqlash
    finally:
        con.close()  # ulanishni yopish


def init_db() -> None:
    with conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                user_id    INTEGER PRIMARY KEY,
                sid        TEXT    NOT NULL,
                order_json TEXT    NOT NULL,
                idx        INTEGER NOT NULL DEFAULT 0,
                score      INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                score       INTEGER NOT NULL,
                total       INTEGER NOT NULL,
                percent     INTEGER NOT NULL,
                grade       TEXT    NOT NULL,
                finished_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_results_user ON results (user_id, id DESC);
            """
        )  # jadvallar: sessions (joriy test), results (natijalar)


def grade_for(percent: int) -> str:
    if percent >= 90:  # 90% va undan yuqori
        return "A'lo (5)"
    if percent >= 70:  # 70-89%
        return "Yaxshi (4)"
    if percent >= 50:  # 50-69%
        return "Qoniqarli (3)"
    return "Qoniqarsiz (2)"  # 50% dan past


def question_text(number: int, q: dict) -> str:
    return f"Savol {number}/{TOTAL}\n\n{q['question']}"  # savol matni


def question_kb(sid: str, idx: int, q: dict) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{LETTERS[i]}) {opt}", callback_data=f"a:{sid}:{idx}:{i}"  # a:sessiya:savol:variant
            )
        ]
        for i, opt in enumerate(q["options"])  # har variant uchun tugma
    ]
    rows.append([InlineKeyboardButton(text="🔄 Qaytadan boshlash", callback_data="restart")])  # restart tugmasi
    return InlineKeyboardMarkup(inline_keyboard=rows)


def begin_kb(text: str = "▶️ Testni boshlash") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="begin")]]  # bitta "boshlash" tugmasi
    )


async def send_question(bot: Bot, chat_id: int, sid: str, order: list[int], idx: int) -> None:
    q = QUESTIONS[order[idx]]  # aralash tartibdagi joriy savol
    await bot.send_message(
        chat_id, question_text(idx + 1, q), reply_markup=question_kb(sid, idx, q)  # savolni yuborish
    )


async def start_quiz(bot: Bot, chat_id: int, user_id: int) -> None:
    """Yangi urinish boshlaydi. Eski tugallanmagan urinish bekor qilinadi."""
    order = random.sample(range(TOTAL), TOTAL)  # savollar tartibini aralashtirish
    sid = uuid.uuid4().hex[:8]  # yangi sessiya ID
    with conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO sessions (user_id, sid, order_json, idx, score) "
            "VALUES (?, ?, ?, 0, 0)",
            (user_id, sid, json.dumps(order)),  # sessiyani saqlash (eskisi o'chadi)
        )
    await send_question(bot, chat_id, sid, order, 0)  # birinchi savol


async def finish_quiz(bot: Bot, chat_id: int, user_id: int, score: int) -> None:
    percent = round(score / TOTAL * 100)  # foizni hisoblash
    grade = grade_for(percent)  # bahoni aniqlash
    with conn() as con:
        con.execute(
            "INSERT INTO results (user_id, score, total, percent, grade, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, score, TOTAL, percent, grade, int(time.time())),  # natijani bazaga yozish
        )
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # sessiyani o'chirish
    await bot_send_result(bot, chat_id, score, percent, grade)  # natijani yuborish


async def bot_send_result(bot: Bot, chat_id: int, score: int, percent: int, grade: str) -> None:
    await bot.send_message(
        chat_id,
        f"🏁 Test yakunlandi!\n\n"
        f"Natija: {score}/{TOTAL}\n"
        f"Foiz: {percent}%\n"
        f"Baho: {grade}\n\n"
        f"Oxirgi natijalaringiz: /natijalarim",
        reply_markup=begin_kb("🔄 Qaytadan boshlash"),  # natija + qayta boshlash tugmasi
    )


@router.message(CommandStart())  # /start buyrug'i
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name if message.from_user else "do'stim"  # foydalanuvchi ismi
    await message.answer(
        f"Salom, {name}! 👋\n\n"
        f"Bu bot kiberxavsizlik bo'yicha {TOTAL} ta savoldan iborat test o'tkazadi.\n"
        f"Savollar har safar tasodifiy tartibda chiqadi.\n\n"
        f"Boshlash uchun tugmani bosing.",
        reply_markup=begin_kb(),  # salomlashish + boshlash tugmasi
    )


@router.message(Command("test", "restart"))  # /test va /restart buyruqlari
async def cmd_test(message: Message) -> None:
    await start_quiz(message.bot, message.chat.id, message.from_user.id)  # testni boshlash


@router.callback_query(F.data.in_({"begin", "restart"}))  # "boshlash"/"qaytadan" tugmalari
async def on_begin(cb: CallbackQuery) -> None:
    await cb.answer()  # tugma "yuklanmoqda" holatini o'chirish
    try:
        await cb.message.edit_reply_markup(reply_markup=None)  # eski tugmalarni olib tashlash
    except Exception:
        pass  # xabar o'zgartirib bo'lmasa, e'tibor bermaymiz
    await start_quiz(cb.bot, cb.message.chat.id, cb.from_user.id)  # yangi test


@router.callback_query(F.data.startswith("a:"))  # javob tugmalari
async def on_answer(cb: CallbackQuery) -> None:
    try:
        _, sid, idx_s, opt_s = cb.data.split(":")  # callback ma'lumotini ajratish
        idx, opt = int(idx_s), int(opt_s)  # raqamga o'tkazish
    except ValueError:
        await cb.answer()  # noto'g'ri format bo'lsa chiqib ketish
        return

    user_id = cb.from_user.id  # foydalanuvchi ID
    with conn() as con:
        s = con.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,)).fetchone()  # joriy sessiya
        valid = bool(s) and s["sid"] == sid and s["idx"] == idx  # sessiya va savol mosligi
        if valid:
            order = json.loads(s["order_json"])  # savollar tartibi
            q = QUESTIONS[order[idx]]  # joriy savol
            correct = opt == q["answer"]  # javob to'g'rimi
            cur = con.execute(
                "UPDATE sessions SET idx = idx + 1, score = score + ? "
                "WHERE user_id = ? AND sid = ? AND idx = ?",
                (int(correct), user_id, sid, idx),  # keyingi savolga o'tish va ballni oshirish
            )
            valid = cur.rowcount == 1  # ikki marta bosishdan himoya

    if not valid:
        await cb.answer(
            "Bu savolga allaqachon javob bergansiz yoki test qayta boshlangan.",
            show_alert=True,  # ogohlantirish oynasi
        )
        try:
            await cb.message.edit_reply_markup(reply_markup=None)  # eski tugmalarni o'chirish
        except Exception:
            pass
        return

    score = s["score"] + int(correct)  # yangilangan ball
    chosen = f"{LETTERS[opt]}) {q['options'][opt]}"  # tanlangan variant matni
    if correct:
        verdict = f"Sizning javobingiz: {chosen}\n\n✅ To'g'ri!"  # to'g'ri javob xabari
    else:
        right = f"{LETTERS[q['answer']]}) {q['options'][q['answer']]}"  # to'g'ri variant matni
        verdict = (
            f"Sizning javobingiz: {chosen}\n\n"
            f"❌ Noto'g'ri.\nTo'g'ri javob: {right}"  # noto'g'ri javob xabari
        )

    await cb.answer()  # callbackka javob berish
    try:
        await cb.message.edit_text(
            f"{question_text(idx + 1, q)}\n\n{verdict}", reply_markup=None  # savolga natijani qo'shish
        )
    except Exception:
        pass

    next_idx = idx + 1  # keyingi savol raqami
    if next_idx < TOTAL:
        await send_question(cb.bot, cb.message.chat.id, sid, order, next_idx)  # keyingi savol
    else:
        await finish_quiz(cb.bot, cb.message.chat.id, user_id, score)  # test tugadi


@router.message(Command("natijalarim"))  # /natijalarim buyrug'i
async def cmd_results(message: Message) -> None:
    with conn() as con:
        rows = con.execute(
            "SELECT score, total, percent, grade, finished_at FROM results "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (message.from_user.id,),  # oxirgi 5 ta natija
        ).fetchall()

    if not rows:
        await message.answer(
            "Hali natijalar yo'q. Testni boshlang 👇", reply_markup=begin_kb()  # natija yo'q bo'lsa
        )
        return

    lines = ["📊 Oxirgi natijalaringiz:\n"]  # sarlavha
    for n, r in enumerate(rows, 1):
        when = datetime.fromtimestamp(r["finished_at"], TZ).strftime("%d.%m.%Y %H:%M")  # sanani formatlash
        lines.append(f"{n}. {when} — {r['score']}/{r['total']} ({r['percent']}%) — {r['grade']}")  # bitta natija qatori
    await message.answer("\n".join(lines), reply_markup=begin_kb("🔄 Yana urinish"))  # ro'yxatni yuborish


async def main() -> None:
    logging.basicConfig(level=logging.INFO)  # loglarni yoqish
    token = os.getenv("BOT_TOKEN")  # tokenni muhit o'zgaruvchisidan olish (kodga yozilmaydi!)
    if not token:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")  # token bo'lmasa to'xtash

    init_db()  # bazani tayyorlash
    bot = Bot(token)  # bot obyekti
    dp = Dispatcher()  # dispatcher
    dp.include_router(router)  # handlerlarni ulash
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Botni ishga tushirish"),
            BotCommand(command="test", description="Testni (qaytadan) boshlash"),
            BotCommand(command="natijalarim", description="Oxirgi 5 ta natija"),
        ]  # menyudagi buyruqlar
    )
    await dp.start_polling(bot)  # botni ishga tushirish


if __name__ == "__main__":
    asyncio.run(main())  # dasturni boshlash