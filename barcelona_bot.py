"""
Barcelona Trip Bot
--------------------------------------------------
Combines five things for a group of 5 planning a trip to Barcelona:

1) /start       - vote Center vs Urban (Околиця), majority wins once all 5 vote
2) /arrival     - 4 participants (not the organizer) submit flight date+time,
                  bot sends everyone a sorted agenda once all 4 are in
3) /myid        - shows a user's Telegram ID (needed to fill in the config below)
4) /myquestions - fully personalized questionnaire: each person (identified by
                  Telegram ID in PARTICIPANTS) gets their own unique list of
                  questions that nobody else sees
5) Daily broadcast at DAILY_SEND_TIME - countdown to MEETING_DATE + a joke,
   plus a pill reminder for anyone listed in PILL_REMINDER_USER_IDS

SETUP:
  1) pip install "python-telegram-bot[job-queue]" --upgrade
  2) Set BOT_TOKEN below (get one from @BotFather on Telegram)
  3) Set MEETING_DATE and DAILY_SEND_TIME to what you want
  4) Run the bot, have everyone DM /myid, then fill PARTICIPANT_IDS,
     PARTICIPANTS (with each person's own questions), and (optionally)
     PILL_REMINDER_USER_IDS with the returned numbers
  5) python barcelona_bot.py
"""

import logging
import os
import random
import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
# BOT_TOKEN comes from an environment variable named BOT_TOKEN.
# On Railway: Project → Variables → add BOT_TOKEN = <your token from BotFather>
# No need to edit this file at all.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_SIZE = 5  # how many people are voting (includes organizer)
ARRIVAL_PARTICIPANTS = 4  # how many people submit flight info (excludes organizer)

# The date of the meetup/trip - countdown counts down to this.
MEETING_DATE = datetime.date(2026, 8, 20)  # YYYY, M, D - EDIT ME

# Time of day the daily broadcast (countdown + joke) goes out.
# EDIT to whatever time you want - this is just a placeholder.
DAILY_SEND_TIME = datetime.time(hour=9, minute=0)

# All 5 participants' Telegram user IDs go here so the daily broadcast can
# reach people even before they've voted or submitted a flight.
# Use /myid in a chat with the bot to find each person's ID, then fill this in.
PARTICIPANT_IDS = [
    # 111111111,  # e.g. Maksym
    # 222222222,  # e.g. participant 2
]

# Pill reminder recipients - fill in name -> Telegram user ID once known.
# Anyone listed here gets an extra pill-reminder line in the daily broadcast.
PILL_REMINDER_USER_IDS = {
    # "Name": 111111111,
}

# Fully personalized content per participant. Fill in each person's
# Telegram ID (get it via /myid) and their own list of questions/messages.
# Each person only sees THEIR OWN list when they run /myquestions.
PARTICIPANTS = {
    # 111111111: {
    #     "name": "Ім'я",
    #     "questions": [
    #         "Перше особисте питання для цієї людини?",
    #         "Друге особисте питання?",
    #     ],
    # },
}

JOKES = [
    "Чому барселонці не грають в хованки? Бо гарне місце завжди знайдуть 😄",
    "Кажуть, найкращий вид на Барселону — з балкону, який ви ще не обрали 🏙️",
    "Тапас — це коли їжі мало, а щастя багато 🍤",
    "П'ять людей, дві опції — і жодного консенсусу без цього бота 😅",
]

logging.basicConfig(level=logging.INFO)

# in-memory storage: {user_id: {"name":..., "choice":...}}
VOTES = {}

# in-memory storage for /arrival: {user_id: {"name":..., "date":..., "time":...}}
ARRIVALS = {}

ARRIVAL_DATE, ARRIVAL_TIME = range(2)
MY_QUESTIONS = 2  # separate state for the personalized questionnaire

# in-memory storage for /myquestions answers: {user_id: [answers...]}
PERSONAL_ANSWERS = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = ReplyKeyboardMarkup(
        [["Центр", "Околиця"]], one_time_keyboard=True, resize_keyboard=True
    )
    await update.message.reply_text(
        "🏙️ Час вирішувати щодо Барселони!\n\n"
        "Де ви хочете жити: в ЦЕНТРІ Барселони, чи на ОКОЛИЦІ?",
        reply_markup=keyboard,
    )


async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice = update.message.text.strip()

    if choice not in ("Центр", "Околиця"):
        await update.message.reply_text("Будь ласка, натисніть Центр або Околиця 🙂")
        return

    VOTES[user.id] = {"name": user.first_name, "choice": choice}

    await update.message.reply_text(
        f"✅ Записав: {choice}. Очікуємо решту учасників...",
        reply_markup=ReplyKeyboardRemove(),
    )

    await maybe_announce_result(context)


async def maybe_announce_result(context: ContextTypes.DEFAULT_TYPE):
    if len(VOTES) < GROUP_SIZE:
        return  # not everyone has voted yet

    center_votes = sum(1 for v in VOTES.values() if v["choice"] == "Центр")
    urban_votes = sum(1 for v in VOTES.values() if v["choice"] == "Околиця")

    if center_votes > urban_votes:
        result = f"🏆 Перемагає ЦЕНТР! ({center_votes} проти {urban_votes})"
    elif urban_votes > center_votes:
        result = f"🏆 Перемагає ОКОЛИЦЯ! ({urban_votes} проти {center_votes})"
    else:
        result = f"🤝 Нічия! ({center_votes} проти {urban_votes}) — обговоріть і вирішіть разом!"

    summary_lines = [result, "", "Розподіл голосів:"]
    for v in VOTES.values():
        summary_lines.append(f"• {v['name']}: {v['choice']}")
    summary = "\n".join(summary_lines)

    for user_id in VOTES:
        try:
            await context.bot.send_message(chat_id=user_id, text=summary)
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


async def arrival_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ Введіть дату вашого прильоту (наприклад: 20.08.2026)"
    )
    return ARRIVAL_DATE


async def arrival_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["arrival_date"] = update.message.text.strip()
    await update.message.reply_text(
        "А тепер час прильоту (наприклад: 14:30)"
    )
    return ARRIVAL_TIME


async def arrival_get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    time_str = update.message.text.strip()

    ARRIVALS[user.id] = {
        "name": user.first_name,
        "date": context.user_data.get("arrival_date"),
        "time": time_str,
    }

    await update.message.reply_text(
        f"✅ Записав: {ARRIVALS[user.id]['date']} о {time_str}. "
        f"Очікуємо решту учасників..."
    )

    await maybe_announce_agenda(context)
    return ConversationHandler.END


async def maybe_announce_agenda(context: ContextTypes.DEFAULT_TYPE):
    if len(ARRIVALS) < ARRIVAL_PARTICIPANTS:
        return  # not everyone has submitted yet

    # Sort by date then time (both plain strings in DD.MM.YYYY / HH:MM format,
    # so a simple parse keeps this robust even if people format things loosely)
    def sort_key(item):
        info = item[1]
        return (info["date"], info["time"])

    ordered = sorted(ARRIVALS.items(), key=sort_key)

    lines = ["🗓️ Агенда прильотів:", ""]
    for _, info in ordered:
        lines.append(f"• {info['name']}: {info['date']} о {info['time']}")
    agenda = "\n".join(lines)

    for user_id in ARRIVALS:
        try:
            await context.bot.send_message(chat_id=user_id, text=agenda)
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано. Надішліть /arrival, щоб спробувати знову.")
    return ConversationHandler.END


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Ваш Telegram ID: {user.id}\n"
        f"Додайте цей номер у PARTICIPANT_IDS (і в PILL_REMINDER_USER_IDS, якщо треба)."
    )


async def daily_broadcast(context: ContextTypes.DEFAULT_TYPE):
    days_left = (MEETING_DATE - datetime.date.today()).days

    if days_left > 0:
        countdown = f"📅 До зустрічі залишилось {days_left} дн."
    elif days_left == 0:
        countdown = "🎉 Сьогодні той самий день!"
    else:
        countdown = "✅ Зустріч вже відбулась."

    joke = random.choice(JOKES)
    base_message = f"{countdown}\n\n😄 {joke}"

    for user_id in PARTICIPANT_IDS:
        message = base_message
        # Anyone in PILL_REMINDER_USER_IDS gets an extra line appended.
        if user_id in PILL_REMINDER_USER_IDS.values():
            message += "\n\n💊 Не забудьте прийняти таблетки!"
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


async def myquestions_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    profile = PARTICIPANTS.get(user.id)

    if not profile:
        await update.message.reply_text(
            "Я поки не знаю, хто ви 🙂 Напишіть /myid, надішліть цей номер організатору, "
            "і він додасть для вас особисті питання."
        )
        return ConversationHandler.END

    context.user_data["mq_index"] = 0
    context.user_data["mq_questions"] = profile["questions"]
    context.user_data["mq_answers"] = []

    await update.message.reply_text(f"Привіт, {profile['name']}! Почнемо ✍️")
    await ask_next_personal_question(update, context)
    return MY_QUESTIONS


async def ask_next_personal_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data["mq_index"]
    questions = context.user_data["mq_questions"]

    if idx >= len(questions):
        user = update.effective_user
        PERSONAL_ANSWERS[user.id] = context.user_data["mq_answers"]
        await update.message.reply_text("✅ Дякую, всі відповіді записано!")
        return

    await update.message.reply_text(questions[idx])


async def myquestions_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mq_answers"].append(update.message.text.strip())
    context.user_data["mq_index"] += 1

    await ask_next_personal_question(update, context)

    if context.user_data["mq_index"] >= len(context.user_data["mq_questions"]):
        return ConversationHandler.END
    return MY_QUESTIONS


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))

    arrival_conv = ConversationHandler(
        entry_points=[CommandHandler("arrival", arrival_start)],
        states={
            ARRIVAL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_get_date)],
            ARRIVAL_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_get_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(arrival_conv)

    myquestions_conv = ConversationHandler(
        entry_points=[CommandHandler("myquestions", myquestions_start)],
        states={
            MY_QUESTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, myquestions_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(myquestions_conv)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_vote))

    # Daily broadcast: countdown + joke (+ pill reminder for listed users)
    app.job_queue.run_daily(daily_broadcast, time=DAILY_SEND_TIME)

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()

