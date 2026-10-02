import asyncio
import json
import logging
import os
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = BASE_DIR / "questions.json"
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "quiz.db"))
LETTERS = "ABCD"
TZ = timezone(timedelta(hours=5))


router = Router()


def load_questions() -> list[dict]:
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if len(data) < 10:
        raise ValueError("Kamida 10 ta savol bo'lishi kerak")
    for i, q in enumerate(data):
        if len(q["options"]) != 4:
            raise ValueError(f"{i + 1}-savolda 4 ta variant bo'lishi kerak")
        if q["answer"] not in range(4):
            raise ValueError(f"{i + 1}-savolda 'answer' 0..3 oralig'ida bo'lishi kerak")
    return data


QUESTIONS = load_questions()
TOTAL = len(QUESTIONS)


@contextmanager
def conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


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
        )


def grade_for(percent: int) -> str:
    if percent >= 90:
        return "A'lo (5)"
    if percent >= 70:
        return "Yaxshi (4)"
    if percent >= 50:
        return "Qoniqarli (3)"
    return "Qoniqarsiz (2)"


def question_text(number: int, q: dict) -> str:
    return f"Savol {number}/{TOTAL}\n\n{q['question']}"


def question_kb(sid: str, idx: int, q: dict) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{LETTERS[i]}) {opt}", callback_data=f"a:{sid}:{idx}:{i}"
            )
        ]
        for i, opt in enumerate(q["options"])
    ]
    rows.append([InlineKeyboardButton(text="🔄 Qaytadan boshlash", callback_data="restart")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def begin_kb(text: str = "▶️ Testni boshlash") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="begin")]]
    )


async def send_question(bot: Bot, chat_id: int, sid: str, order: list[int], idx: int) -> None:
    q = QUESTIONS[order[idx]]
    await bot.send_message(
        chat_id, question_text(idx + 1, q), reply_markup=question_kb(sid, idx, q)
    )


async def start_quiz(bot: Bot, chat_id: int, user_id: int) -> None:
    """Yangi urinish boshlaydi. Eski tugallanmagan urinish bekor qilinadi."""
    order = random.sample(range(TOTAL), TOTAL)
    sid = uuid.uuid4().hex[:8]
    with conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO sessions (user_id, sid, order_json, idx, score) "
            "VALUES (?, ?, ?, 0, 0)",
            (user_id, sid, json.dumps(order)),
        )
    await send_question(bot, chat_id, sid, order, 0)


async def finish_quiz(bot: Bot, chat_id: int, user_id: int, score: int) -> None:
    percent = round(score / TOTAL * 100)
    grade = grade_for(percent)
    with conn() as con:
        con.execute(
            "INSERT INTO results (user_id, score, total, percent, grade, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, score, TOTAL, percent, grade, int(time.time())),
        )
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await bot_send_result(bot, chat_id, score, percent, grade)


async def bot_send_result(bot: Bot, chat_id: int, score: int, percent: int, grade: str) -> None:
    await bot.send_message(
        chat_id,
        f"🏁 Test yakunlandi!\n\n"
        f"Natija: {score}/{TOTAL}\n"
        f"Foiz: {percent}%\n"
        f"Baho: {grade}\n\n"
        f"Oxirgi natijalaringiz: /natijalarim",
        reply_markup=begin_kb("🔄 Qaytadan boshlash"),
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name if message.from_user else "do'stim"
    await message.answer(
        f"Salom, {name}! 👋\n\n"
        f"Bu bot kiberxavsizlik bo'yicha {TOTAL} ta savoldan iborat test o'tkazadi.\n"
        f"Savollar har safar tasodifiy tartibda chiqadi.\n\n"
        f"Boshlash uchun tugmani bosing.",
        reply_markup=begin_kb(),
    )


@router.message(Command("test", "restart"))
async def cmd_test(message: Message) -> None:
    await start_quiz(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data.in_({"begin", "restart"}))
async def on_begin(cb: CallbackQuery) -> None:
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await start_quiz(cb.bot, cb.message.chat.id, cb.from_user.id)


@router.callback_query(F.data.startswith("a:"))
async def on_answer(cb: CallbackQuery) -> None:
    try:
        _, sid, idx_s, opt_s = cb.data.split(":")
        idx, opt = int(idx_s), int(opt_s)
    except ValueError:
        await cb.answer()
        return

    user_id = cb.from_user.id
    with conn() as con:
        s = con.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
        valid = bool(s) and s["sid"] == sid and s["idx"] == idx
        if valid:
            order = json.loads(s["order_json"])
            q = QUESTIONS[order[idx]]
            correct = opt == q["answer"]
            cur = con.execute(
                "UPDATE sessions SET idx = idx + 1, score = score + ? "
                "WHERE user_id = ? AND sid = ? AND idx = ?",
                (int(correct), user_id, sid, idx),
            )
            valid = cur.rowcount == 1

    if not valid:
        await cb.answer(
            "Bu savolga allaqachon javob bergansiz yoki test qayta boshlangan.",
            show_alert=True,
        )
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    score = s["score"] + int(correct)
    chosen = f"{LETTERS[opt]}) {q['options'][opt]}"
    if correct:
        verdict = f"Sizning javobingiz: {chosen}\n\n✅ To'g'ri!"
    else:
        right = f"{LETTERS[q['answer']]}) {q['options'][q['answer']]}"
        verdict = (
            f"Sizning javobingiz: {chosen}\n\n"
            f"❌ Noto'g'ri.\nTo'g'ri javob: {right}"
        )

    await cb.answer()
    try:
        await cb.message.edit_text(
            f"{question_text(idx + 1, q)}\n\n{verdict}", reply_markup=None
        )
    except Exception:
        pass

    next_idx = idx + 1
    if next_idx < TOTAL:
        await send_question(cb.bot, cb.message.chat.id, sid, order, next_idx)
    else:
        await finish_quiz(cb.bot, cb.message.chat.id, user_id, score)


@router.message(Command("natijalarim"))
async def cmd_results(message: Message) -> None:
    with conn() as con:
        rows = con.execute(
            "SELECT score, total, percent, grade, finished_at FROM results "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (message.from_user.id,),
        ).fetchall()

    if not rows:
        await message.answer(
            "Hali natijalar yo'q. Testni boshlang 👇", reply_markup=begin_kb()
        )
        return

    lines = ["📊 Oxirgi natijalaringiz:\n"]
    for n, r in enumerate(rows, 1):
        when = datetime.fromtimestamp(r["finished_at"], TZ).strftime("%d.%m.%Y %H:%M")
        lines.append(f"{n}. {when} — {r['score']}/{r['total']} ({r['percent']}%) — {r['grade']}")
    await message.answer("\n".join(lines), reply_markup=begin_kb("🔄 Yana urinish"))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")

    init_db()
    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Botni ishga tushirish"),
            BotCommand(command="test", description="Testni (qaytadan) boshlash"),
            BotCommand(command="natijalarim", description="Oxirgi 5 ta natija"),
        ]
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())