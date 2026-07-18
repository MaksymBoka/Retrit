"""
Retreat Personal Assistant Bot
--------------------------------------------------
1) /start       - vote Center vs Urban (Околиця), majority wins once all 5 vote
2) /arrival     - introduces itself as an ongoing personal assistant for all
                  retreats (not just this one). Asks flight number + date
                  (6 or 7 August), tries to auto-look-up the Barcelona landing
                  time via a flight API, asks to confirm (or falls back to
                  manual entry). Once all 4 non-organizer participants are in,
                  sends everyone the sorted arrival agenda AND the program
                  for 7, 8, 9 August.
3) /myid        - shows a user's Telegram ID (needed to fill in the config below)
4) /myquestions - fully personalized questionnaire: each person (identified by
                  Telegram ID in PARTICIPANTS) gets their own unique list of
                  questions that nobody else sees
5) Daily broadcast at DAILY_SEND_TIME - countdown to MEETING_DATE + a joke,
   plus a pill reminder for anyone listed in PILL_REMINDER_USER_IDS
6) /info        - sends Sergey's local contact, the accommodation address,
                  recommended taxi apps, and a Spanish taxi-driver phrase
                  as both text and a generated voice message

Each person gets a fixed language (no picker) based on their Telegram ID in
PARTICIPANT_LANG: "uk" (Ukrainian), "ru" (Russian), or "hy" (Armenian).

SETUP:
  1) pip install "python-telegram-bot[job-queue]" requests --upgrade
  2) Set BOT_TOKEN below (get one from @BotFather on Telegram)
  3) Optional: set FLIGHT_API_KEY (AeroDataBox via RapidAPI) for automatic
     flight-time lookup. Without it, the bot just asks for time manually.
  4) Set MEETING_DATE, DAILY_SEND_TIME, and fill in PROGRAM with the real
     plan for 7/8/9 August
  5) Run the bot, have everyone DM /myid, then fill PARTICIPANT_IDS,
     PARTICIPANT_LANG (uk/ru/hy per person), PARTICIPANTS (with each
     person's own questions), and (optionally) PILL_REMINDER_USER_IDS
     with the returned numbers
  6) python barcelona_bot.py
"""

import logging
import os
import random
import datetime
import requests
import io
from gtts import gTTS
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

# Optional: flight-lookup API key (AeroDataBox via RapidAPI). If not set, the
# bot skips auto-lookup and just asks each person to type their arrival time.
# On Railway: Variables → add FLIGHT_API_KEY = <your RapidAPI key>
FLIGHT_API_KEY = os.environ.get("FLIGHT_API_KEY")

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
    470469942,  # Дмитро
    244007332,  # Maksym (організатор)
    278596529,  # Аршак
]

# Language per participant: "uk" (Ukrainian), "ru" (Russian), "hy" (Armenian).
# Fill in each person's ID as you get it via /myid. Default is "uk" for anyone
# not listed here.
PARTICIPANT_LANG = {
    244007332: "uk",  # Maksym
    470469942: "uk",  # Дмитро
    278596529: "hy",  # Аршак
    # "Жора" (Георгій) - add his ID here with "ru" once known
}

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
    "Вірменському радіо задають питання:\n— Що таке справжня дружба?\nВідповідь:\n— Це коли друг знає про тебе все найгірше — і все одно телефонує першим.",
    "Вірменському радіо задають питання:\n— Чим друг відрізняється від брата?\nВідповідь:\n— Брата дає доля. Друга обирають самі.",
]

# Local contact person during the retreat.
SERGEY_CONTACT = "+380933391624"

# Accommodation address in Barcelona (full, including floor/apartment - for display).
ACCOMMODATION_ADDRESS = "Carrer de Parcerisa, 25, 4th floor, 2nd apartment, Sants-Montjuïc, 08014 Barcelona, Spain"

# Street-level address only, used for the taxi phrase (floor/apartment isn't
# useful for a driver, and can even confuse the pronunciation/lookup).
ACCOMMODATION_STREET_ADDRESS = "Carrer de Parcerisa, 25, Sants-Montjuïc, 08014 Barcelona, Spain"

# Taxi apps/services recommended in Barcelona - edit to your preference.
TAXI_SERVICES = ["FreeNow", "Uber", "Cabify"]

# Spanish phrase for taxi drivers who don't speak English/Ukrainian/Russian.
# Bot sends this both as text and as a generated voice message.
TAXI_PHRASE_ES = f"Amo a Maksym y llévame a {ACCOMMODATION_STREET_ADDRESS.replace(', Spain', ', España')}"

# Program for the retreat days - fill in the real plan per day.
# Shown to everyone once all 4 flight submissions are in.
PROGRAM = {
    "uk": {
        "7": "7 серпня: [заповніть програму дня]",
        "8": "8 серпня: [заповніть програму дня]",
        "9": "9 серпня: [заповніть програму дня]",
    },
    "ru": {
        "7": "7 августа: [заполните программу дня]",
        "8": "8 августа: [заполните программу дня]",
        "9": "9 августа: [заполните программу дня]",
    },
    "hy": {
        "7": "օգոստոսի 7. [լրացրեք օրվա ծրագիրը]",
        "8": "օգոստոսի 8. [լրացրեք օրվա ծրագիրը]",
        "9": "օգոստոսի 9. [լրացրեք օրվա ծրագիրը]",
    },
}

logging.basicConfig(level=logging.INFO)

# in-memory storage: {user_id: {"name":..., "choice":...}}
VOTES = {}

# in-memory storage for /arrival: {user_id: {"name":..., "flight":..., "date":..., "time":...}}
ARRIVALS = {}

# tracks who has already seen the "personal assistant for our retreats" intro
WELCOMED_USERS = set()

ARRIVAL_FLIGHT, ARRIVAL_DATE_CHOICE, ARRIVAL_CONFIRM, ARRIVAL_MANUAL_TIME = range(4)
MY_QUESTIONS = 4  # separate state for the personalized questionnaire

# in-memory storage for /myquestions answers: {user_id: [answers...]}
PERSONAL_ANSWERS = {}


TEXTS = {
    "uk": {
        "start_prompt": "🏙️ Час вирішувати щодо Барселони!\n\nДе ви хочете жити: в ЦЕНТРІ Барселони, чи на ОКОЛИЦІ?",
        "btn_center": "Центр",
        "btn_urban": "Околиця",
        "invalid_choice": "Будь ласка, натисніть Центр або Околиця 🙂",
        "vote_recorded": "✅ Записав: {choice}. Очікуємо решту учасників...",
        "result_center": "🏆 Перемагає ЦЕНТР! ({a} проти {b})",
        "result_urban": "🏆 Перемагає ОКОЛИЦЯ! ({a} проти {b})",
        "result_tie": "🤝 Нічия! ({a} проти {b}) — обговоріть і вирішіть разом!",
        "breakdown_header": "Розподіл голосів:",
        "assistant_intro": "👋 Привіт! Я ваш особистий асистент не тільки на цей ретрит (7-9.08), а й на всі наступні. Почнемо з даних про рейс.",
        "arrival_ask_flight": "✈️ Введіть номер вашого рейсу (наприклад: LH1234)",
        "arrival_ask_date_choice": "На яку дату цей рейс?",
        "btn_aug6": "6 серпня",
        "btn_aug7": "7 серпня",
        "invalid_date_choice": "Будь ласка, оберіть 6 або 7 серпня 🙂",
        "flight_lookup_confirm": "За номером рейсу {flight} знайшов час посадки в Барселоні: {time}. Підтверджуєте?",
        "btn_confirm_yes": "Так, вірно",
        "btn_confirm_no": "Ні, введу вручну",
        "invalid_confirm": "Будь ласка, оберіть один з варіантів 🙂",
        "flight_lookup_failed": "Не вдалося автоматично знайти час посадки. Введіть його вручну (наприклад: 14:30)",
        "arrival_ask_manual_time": "Введіть час посадки вручну (наприклад: 14:30)",
        "arrival_recorded": "✅ Записав: рейс {flight}, {date} серпня о {time}. Очікуємо решту учасників...",
        "agenda_header": "🗓️ Агенда прильотів:",
        "program_header": "📋 Програма ретриту:",
        "cancel_msg": "Скасовано. Надішліть /arrival, щоб спробувати знову.",
        "myid_msg": "Ваш Telegram ID: {id}\nНадішліть цей номер організатору.",
        "daily_countdown": "📅 До зустрічі залишилось {n} дн.",
        "daily_today": "🎉 Сьогодні той самий день!",
        "daily_done": "✅ Зустріч вже відбулась.",
        "pill_reminder": "\n\n💊 Не забудьте прийняти таблетки!",
        "info_header": "ℹ️ Корисна інформація",
        "info_sergey": "📞 Контакт на місці (Сергій): {contact}",
        "info_address": "🏠 Адреса проживання: {address}",
        "info_taxi": "🚕 Таксі-сервіси: {services}",
        "info_phrase_intro": "🗣️ Якщо водій не розуміє — покажіть цю фразу іспанською (текст і голосове нижче):",
    },
    "ru": {
        "start_prompt": "🏙️ Время решать насчёт Барселоны!\n\nГде вы хотите жить: в ЦЕНТРЕ Барселоны или на ОКРАИНЕ?",
        "btn_center": "Центр",
        "btn_urban": "Окраина",
        "invalid_choice": "Пожалуйста, нажмите Центр или Окраина 🙂",
        "vote_recorded": "✅ Записал: {choice}. Ждём остальных участников...",
        "result_center": "🏆 Побеждает ЦЕНТР! ({a} против {b})",
        "result_urban": "🏆 Побеждает ОКРАИНА! ({a} против {b})",
        "result_tie": "🤝 Ничья! ({a} против {b}) — обсудите и решите вместе!",
        "breakdown_header": "Распределение голосов:",
        "assistant_intro": "👋 Привет! Я ваш личный ассистент не только на этот ретрит (7-9.08), но и на все последующие. Начнём с данных о рейсе.",
        "arrival_ask_flight": "✈️ Введите номер вашего рейса (например: LH1234)",
        "arrival_ask_date_choice": "На какую дату этот рейс?",
        "btn_aug6": "6 августа",
        "btn_aug7": "7 августа",
        "invalid_date_choice": "Пожалуйста, выберите 6 или 7 августа 🙂",
        "flight_lookup_confirm": "По номеру рейса {flight} нашёл время посадки в Барселоне: {time}. Подтверждаете?",
        "btn_confirm_yes": "Да, верно",
        "btn_confirm_no": "Нет, введу вручную",
        "invalid_confirm": "Пожалуйста, выберите один из вариантов 🙂",
        "flight_lookup_failed": "Не удалось автоматически найти время посадки. Введите его вручную (например: 14:30)",
        "arrival_ask_manual_time": "Введите время посадки вручную (например: 14:30)",
        "arrival_recorded": "✅ Записал: рейс {flight}, {date} августа в {time}. Ждём остальных участников...",
        "agenda_header": "🗓️ Агенда прилётов:",
        "program_header": "📋 Программа ретрита:",
        "cancel_msg": "Отменено. Отправьте /arrival, чтобы попробовать снова.",
        "myid_msg": "Ваш Telegram ID: {id}\nОтправьте этот номер организатору.",
        "daily_countdown": "📅 До встречи осталось {n} дн.",
        "daily_today": "🎉 Сегодня тот самый день!",
        "daily_done": "✅ Встреча уже прошла.",
        "pill_reminder": "\n\n💊 Не забудьте принять таблетки!",
        "info_header": "ℹ️ Полезная информация",
        "info_sergey": "📞 Контакт на месте (Сергей): {contact}",
        "info_address": "🏠 Адрес проживания: {address}",
        "info_taxi": "🚕 Такси-сервисы: {services}",
        "info_phrase_intro": "🗣️ Если водитель не понимает — покажите эту фразу на испанском (текст и голосовое ниже):",
    },
    "hy": {
        "start_prompt": "🏙️ Ժամանակն է որոշելու Բարսելոնի մասին!\n\nՈւր եք ուզում ապրել՝ Բարսելոնի ԿԵՆՏՐՈՆՈՒՄ, թե ԱՐՎԱՐՁԱՆՈՒՄ։",
        "btn_center": "Կենտրոն",
        "btn_urban": "Արվարձան",
        "invalid_choice": "Խնդրում ենք սեղմել Կենտրոն կամ Արվարձան 🙂",
        "vote_recorded": "✅ Գրանցվեց՝ {choice}. Սպասում ենք մնացած մասնակիցներին...",
        "result_center": "🏆 Հաղթում է ԿԵՆՏՐՈՆԸ! ({a} ընդդեմ {b})",
        "result_urban": "🏆 Հաղթում է ԱՐՎԱՐՁԱՆԸ! ({a} ընդդեմ {b})",
        "result_tie": "🤝 Ոչ-ոքի! ({a} ընդդեմ {b}) — քննարկեք և որոշեք միասին!",
        "breakdown_header": "Ձայների բաշխում.",
        "assistant_intro": "👋 Բարև! Ես ձեր անձնական օգնականն եմ ոչ միայն այս ռետրիտի (7-9.08), այլ նաև բոլոր հաջորդների համար։ Սկսենք չվերթի տվյալներից։",
        "arrival_ask_flight": "✈️ Մուտքագրեք ձեր չվերթի համարը (օրինակ՝ LH1234)",
        "arrival_ask_date_choice": "Ո՞ր ամսաթվին է այս չվերթը։",
        "btn_aug6": "օգոստոսի 6",
        "btn_aug7": "օգոստոսի 7",
        "invalid_date_choice": "Խնդրում ենք ընտրել օգոստոսի 6 կամ 7 🙂",
        "flight_lookup_confirm": "{flight} չվերթի համարով գտա Բարսելոնում վայրէջքի ժամը՝ {time}. Հաստատու՞մ եք։",
        "btn_confirm_yes": "Այո, ճիշտ է",
        "btn_confirm_no": "Ոչ, ինքս կմուտքագրեմ",
        "invalid_confirm": "Խնդրում ենք ընտրել տարբերակներից մեկը 🙂",
        "flight_lookup_failed": "Ավտոմատ չհաջողվեց գտնել վայրէջքի ժամը։ Մուտքագրեք ինքներդ (օրինակ՝ 14:30)",
        "arrival_ask_manual_time": "Մուտքագրեք վայրէջքի ժամը ինքներդ (օրինակ՝ 14:30)",
        "arrival_recorded": "✅ Գրանցվեց՝ չվերթ {flight}, օգոստոսի {date}-ին ժամը {time}. Սպասում ենք մնացածին...",
        "agenda_header": "🗓️ Ժամանման օրակարգ.",
        "program_header": "📋 Ռետրիտի ծրագիր.",
        "cancel_msg": "Չեղարկվեց։ Ուղարկեք /arrival՝ նորից փորձելու համար։",
        "myid_msg": "Ձեր Telegram ID-ն է՝ {id}\nՈւղարկեք այս համարը կազմակերպչին։",
        "daily_countdown": "📅 Հանդիպմանը մնացել է {n} օր.",
        "daily_today": "🎉 Այսօր հենց այն օրն է!",
        "daily_done": "✅ Հանդիպումն արդեն կայացել է։",
        "pill_reminder": "\n\n💊 Մի մոռացեք ընդունել դեղահաբերը!",
        "info_header": "ℹ️ Օգտակար տեղեկություն",
        "info_sergey": "📞 Կոնտակտ տեղում (Սերգեյ)՝ {contact}",
        "info_address": "🏠 Բնակության հասցե՝ {address}",
        "info_taxi": "🚕 Տաքսի ծառայություններ՝ {services}",
        "info_phrase_intro": "🗣️ Եթե վարորդը չի հասկանում՝ ցույց տվեք այս իսպաներեն արտահայտությունը (տեքստը և ձայնագրությունը ներքևում).",
    },
}


def t(user_id: int, key: str, **kwargs) -> str:
    """Look up a translated string for this user's language, with fallback to Ukrainian."""
    lang = PARTICIPANT_LANG.get(user_id, "uk")
    template = TEXTS.get(lang, TEXTS["uk"]).get(key, TEXTS["uk"][key])
    return template.format(**kwargs) if kwargs else template


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = ReplyKeyboardMarkup(
        [[t(user.id, "btn_center"), t(user.id, "btn_urban")]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(t(user.id, "start_prompt"), reply_markup=keyboard)


async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    if choice_text == t(user.id, "btn_center"):
        choice = "center"
    elif choice_text == t(user.id, "btn_urban"):
        choice = "urban"
    else:
        await update.message.reply_text(t(user.id, "invalid_choice"))
        return

    VOTES[user.id] = {"name": user.first_name, "choice": choice}

    await update.message.reply_text(
        t(user.id, "vote_recorded", choice=choice_text),
        reply_markup=ReplyKeyboardRemove(),
    )

    await maybe_announce_result(context)


async def maybe_announce_result(context: ContextTypes.DEFAULT_TYPE):
    if len(VOTES) < GROUP_SIZE:
        return  # not everyone has voted yet

    center_votes = sum(1 for v in VOTES.values() if v["choice"] == "center")
    urban_votes = sum(1 for v in VOTES.values() if v["choice"] == "urban")

    for user_id in VOTES:
        if center_votes > urban_votes:
            result = t(user_id, "result_center", a=center_votes, b=urban_votes)
        elif urban_votes > center_votes:
            result = t(user_id, "result_urban", a=urban_votes, b=center_votes)
        else:
            result = t(user_id, "result_tie", a=center_votes, b=urban_votes)

        lines = [result, "", t(user_id, "breakdown_header")]
        for v in VOTES.values():
            choice_label = t(user_id, "btn_center") if v["choice"] == "center" else t(user_id, "btn_urban")
            lines.append(f"• {v['name']}: {choice_label}")
        summary = "\n".join(lines)

        try:
            await context.bot.send_message(chat_id=user_id, text=summary)
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


def lookup_flight_arrival_time(flight_number: str, flight_date: datetime.date):
    """Try to look up the Barcelona landing time for a flight via AeroDataBox.
    Returns a "HH:MM" string, or None if the lookup isn't possible/fails."""
    if not FLIGHT_API_KEY:
        return None
    try:
        url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_number}/{flight_date.isoformat()}"
        headers = {
            "X-RapidAPI-Key": FLIGHT_API_KEY,
            "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com",
        }
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        flight = data[0] if isinstance(data, list) and data else None
        if not flight:
            return None
        arrival = flight.get("arrival", {})
        # Prefer the Barcelona airport leg if present, else just take arrival time
        scheduled = arrival.get("scheduledTime", {}).get("local")
        if not scheduled:
            return None
        return scheduled[11:16]  # "YYYY-MM-DDTHH:MM..." -> "HH:MM"
    except Exception as e:
        logging.warning(f"Flight lookup failed for {flight_number}: {e}")
        return None


async def arrival_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in WELCOMED_USERS:
        WELCOMED_USERS.add(user.id)
        await update.message.reply_text(t(user.id, "assistant_intro"))

    await update.message.reply_text(t(user.id, "arrival_ask_flight"))
    return ARRIVAL_FLIGHT


async def arrival_get_flight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["flight_number"] = update.message.text.strip().upper()

    keyboard = ReplyKeyboardMarkup(
        [[t(user.id, "btn_aug6"), t(user.id, "btn_aug7")]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(t(user.id, "arrival_ask_date_choice"), reply_markup=keyboard)
    return ARRIVAL_DATE_CHOICE


async def arrival_get_date_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    if choice_text == t(user.id, "btn_aug6"):
        day = "6"
    elif choice_text == t(user.id, "btn_aug7"):
        day = "7"
    else:
        await update.message.reply_text(t(user.id, "invalid_date_choice"))
        return ARRIVAL_DATE_CHOICE

    context.user_data["flight_day"] = day
    flight_date = datetime.date(MEETING_DATE.year, 8, int(day))

    found_time = lookup_flight_arrival_time(context.user_data["flight_number"], flight_date)

    if found_time:
        context.user_data["looked_up_time"] = found_time
        keyboard = ReplyKeyboardMarkup(
            [[t(user.id, "btn_confirm_yes"), t(user.id, "btn_confirm_no")]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
        await update.message.reply_text(
            t(user.id, "flight_lookup_confirm", flight=context.user_data["flight_number"], time=found_time),
            reply_markup=keyboard,
        )
        return ARRIVAL_CONFIRM
    else:
        await update.message.reply_text(t(user.id, "flight_lookup_failed"), reply_markup=ReplyKeyboardRemove())
        return ARRIVAL_MANUAL_TIME


async def arrival_confirm_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    if choice_text == t(user.id, "btn_confirm_yes"):
        return await save_arrival(update, context, context.user_data["looked_up_time"])
    elif choice_text == t(user.id, "btn_confirm_no"):
        await update.message.reply_text(t(user.id, "arrival_ask_manual_time"), reply_markup=ReplyKeyboardRemove())
        return ARRIVAL_MANUAL_TIME
    else:
        await update.message.reply_text(t(user.id, "invalid_confirm"))
        return ARRIVAL_CONFIRM


async def arrival_manual_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await save_arrival(update, context, update.message.text.strip())


async def save_arrival(update: Update, context: ContextTypes.DEFAULT_TYPE, time_str: str):
    user = update.effective_user

    ARRIVALS[user.id] = {
        "name": user.first_name,
        "flight": context.user_data.get("flight_number"),
        "date": context.user_data.get("flight_day"),
        "time": time_str,
    }

    await update.message.reply_text(
        t(user.id, "arrival_recorded",
          flight=ARRIVALS[user.id]["flight"], date=ARRIVALS[user.id]["date"], time=time_str),
        reply_markup=ReplyKeyboardRemove(),
    )

    await maybe_announce_agenda(context)
    return ConversationHandler.END


async def maybe_announce_agenda(context: ContextTypes.DEFAULT_TYPE):
    if len(ARRIVALS) < ARRIVAL_PARTICIPANTS:
        return  # not everyone has submitted yet

    def sort_key(item):
        info = item[1]
        return (info["date"], info["time"])

    ordered = sorted(ARRIVALS.items(), key=sort_key)

    for user_id in ARRIVALS:
        lang = PARTICIPANT_LANG.get(user_id, "uk")

        lines = [t(user_id, "agenda_header"), ""]
        for _, info in ordered:
            lines.append(f"• {info['name']}: {info['flight']}, {info['date']} серпня — {info['time']}")

        lines += ["", t(user_id, "program_header"), ""]
        program = PROGRAM.get(lang, PROGRAM["uk"])
        for day in ("7", "8", "9"):
            lines.append(program[day])

        message = "\n".join(lines)
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_user.id, "cancel_msg"))
    return ConversationHandler.END


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(t(user.id, "myid_msg", id=user.id))


async def daily_broadcast(context: ContextTypes.DEFAULT_TYPE):
    days_left = (MEETING_DATE - datetime.date.today()).days

    for user_id in PARTICIPANT_IDS:
        if days_left > 0:
            countdown = t(user_id, "daily_countdown", n=days_left)
        elif days_left == 0:
            countdown = t(user_id, "daily_today")
        else:
            countdown = t(user_id, "daily_done")

        joke = random.choice(JOKES)
        message = f"{countdown}\n\n😄 {joke}"

        if user_id in PILL_REMINDER_USER_IDS.values():
            message += t(user_id, "pill_reminder")
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


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    lines = [
        t(user.id, "info_header"),
        "",
        t(user.id, "info_sergey", contact=SERGEY_CONTACT),
        t(user.id, "info_address", address=ACCOMMODATION_ADDRESS),
        t(user.id, "info_taxi", services=", ".join(TAXI_SERVICES)),
        "",
        t(user.id, "info_phrase_intro"),
        f"«{TAXI_PHRASE_ES}»",
    ]
    await update.message.reply_text("\n".join(lines))

    # Generate and send the Spanish phrase as a voice message too.
    try:
        tts = gTTS(text=TAXI_PHRASE_ES, lang="es")
        audio_buffer = io.BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        await context.bot.send_voice(chat_id=user.id, voice=audio_buffer)
    except Exception as e:
        logging.warning(f"Could not generate/send TTS for {user.id}: {e}")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("info", info))

    arrival_conv = ConversationHandler(
        entry_points=[CommandHandler("arrival", arrival_start)],
        states={
            ARRIVAL_FLIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_get_flight)],
            ARRIVAL_DATE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_get_date_choice)],
            ARRIVAL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_confirm_time)],
            ARRIVAL_MANUAL_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_manual_time)],
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

