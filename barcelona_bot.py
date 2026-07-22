"""
Retreat Personal Assistant Bot
--------------------------------------------------
1) /start        - introduces itself as an ongoing personal assistant for all
                   retreats (not just this one, first use only). Asks flight
                   number + date (6 or 7 August), tries to auto-look-up the
                   Barcelona landing time via a flight API, asks to confirm
                   (or falls back to manual entry).
2) Agenda notice - right after arrival info is saved, once all
                   ARRIVAL_PARTICIPANTS people finish, everyone gets: the
                   sorted arrival list, the fixed program for 7, 8, 9 August
                   (same for everyone - edit PROGRAM with the real plan),
                   and important info - Sergey's address & phone, taxi
                   services, and a Spanish taxi-driver phrase sent ONLY as a
                   voice message (with a short explanation that it's the
                   home address).
3) /myid         - shows a user's Telegram ID (needed to fill in config below)
4) /myquestions  - fully personalized questionnaire: each person (identified
                   by Telegram ID in PARTICIPANTS) gets their own unique list
                   of questions that nobody else sees
5) Daily broadcast at DAILY_SEND_TIME - countdown to MEETING_DATE + a joke,
   plus a pill reminder for anyone listed in PILL_REMINDER_USER_IDS
6) /info         - re-sends Sergey's contact/address, taxi services, and the
                   voice phrase on demand (same content as after agenda voting)

Each person gets a fixed language (no picker) based on their Telegram ID in
PARTICIPANT_LANG: "uk" (Ukrainian), "ru" (Russian), or "hy" (Armenian).

SETUP:
  1) pip install "python-telegram-bot[job-queue]" requests gTTS --upgrade
  2) Set BOT_TOKEN below (get one from @BotFather on Telegram)
  3) Optional: set FLIGHT_API_KEY (AeroDataBox via RapidAPI) for automatic
     flight-time lookup. Without it, the bot just asks for time manually.
  4) Set MEETING_DATE, DAILY_SEND_TIME, fill in PROGRAM with the real plan
     for 7/8/9 August, and SERGEY_CONTACT / SERGEY_ADDRESS
  5) Persistence: on Railway, add a Volume (Settings → Volumes) mounted at
     /data so answers and mid-conversation progress survive redeploys.
     Locally this just creates a file in the working directory.
  6) Run the bot, have everyone DM /myid, then fill PARTICIPANT_IDS,
     PARTICIPANT_LANG (uk/ru/hy per person), PARTICIPANTS (with each
     person's own questions), and (optionally) PILL_REMINDER_USER_IDS
  7) python barcelona_bot.py
"""

import logging
import os
import random
import datetime
import io
import requests
from gtts import gTTS
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    PicklePersistence,
    filters,
)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
# BOT_TOKEN comes from an environment variable named BOT_TOKEN.
# On Railway: Project → Variables → add BOT_TOKEN = <your token from BotFather>
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Where to save bot data (answers + conversation progress) so a redeploy
# doesn't wipe everyone's answers or make them start over mid-flow.
# On Railway: attach a Volume and mount it at /data (Settings → Volumes).
PERSISTENCE_PATH = os.environ.get("PERSISTENCE_PATH", "/data/bot_persistence.pickle")

# Optional: flight-lookup API key (AeroDataBox via RapidAPI). If not set, the
# bot skips auto-lookup and just asks each person to type their arrival time.
FLIGHT_API_KEY = os.environ.get("FLIGHT_API_KEY")

ARRIVAL_PARTICIPANTS = 4  # how many people go through arrival+agenda (excludes organizer)

# The date of the meetup/trip - countdown counts down to this.
MEETING_DATE = datetime.date(2026, 8, 7)  # YYYY, M, D - first day of the gathering

# Time of day the daily broadcast (countdown + joke) goes out.
DAILY_SEND_TIME = datetime.time(hour=9, minute=0)

# All 5 participants' Telegram user IDs - needed for the daily broadcast.
# Use /myid in a chat with the bot to find each person's ID.
PARTICIPANT_IDS = [
    470469942,  # Дмитро
    244007332,  # Maksym (організатор)
    278596529,  # Аршак
    394213736,  # Георгій
]

# Language per participant: "uk" (Ukrainian), "ru" (Russian), "hy" (Armenian).
# Default is "uk" for anyone not listed here.
PARTICIPANT_LANG = {
    244007332: "uk",  # Maksym
    470469942: "uk",  # Дмитро
    278596529: "hy",  # Аршак
    394213736: "ru",  # Георгій
}

# Pill reminder recipients - fill in name -> Telegram user ID once known.
PILL_REMINDER_USER_IDS = {
    # "Name": 111111111,
}

# Fully personalized content per participant for /myquestions.
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

# Sergey's local contact details.
SERGEY_CONTACT = "+380933391624"
SERGEY_ADDRESS = "Carrer de Parcerisa, 25, 4th floor, 2nd apartment, Sants-Montjuïc, 08014 Barcelona, Spain"

# Street-level address only, used for the taxi phrase (floor/apartment isn't
# useful for a driver and can confuse the pronunciation/lookup).
STREET_ADDRESS_ONLY = "Carrer de Parcerisa, 25, Sants-Montjuïc, 08014 Barcelona, Spain"

# Taxi apps/services recommended in Barcelona - edit to your preference.
TAXI_SERVICES = ["FreeNow", "Uber", "Cabify"]

# Spanish phrase for taxi drivers. Sent ONLY as a voice message (no text),
# with a short explanation that it's for the home address.
TAXI_PHRASE_ES = f"Amo a Maksym y llévame a {STREET_ADDRESS_ONLY.replace(', Spain', ', España')}"

# Program for the gathering days - EDIT with the real plan for each day.
# Same text goes to everyone in that language; no voting.
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

# Data below lives in context.bot_data (auto-saved by PicklePersistence) so
# it survives restarts/redeploys instead of these module-level dicts.


def get_arrivals(context):
    return context.bot_data.setdefault("arrivals", {})


def get_welcomed_users(context):
    return context.bot_data.setdefault("welcomed_users", set())


def get_personal_answers(context):
    return context.bot_data.setdefault("personal_answers", {})

(
    ARRIVAL_FLIGHT,
    ARRIVAL_DATE_CHOICE,
    ARRIVAL_CONFIRM,
    ARRIVAL_MANUAL_TIME,
    ARRIVAL_FRIEND_CHECK,
) = range(5)
MY_QUESTIONS = 5  # separate state for the personalized questionnaire


TEXTS = {
    "uk": {
        "assistant_intro": "👋 Привіт! Я ваш особистий асистент не тільки на цю зустріч «v3» (7-9.08), а й на всі наступні. Почнемо з даних про рейс.",
        "arrival_ask_flight": "✈️ Введіть номер вашого рейсу (наприклад: LH1234)",
        "arrival_ask_date_choice": "На яку дату цей рейс?",
        "date_label_template": "{d} серпня",
        "invalid_date_choice": "Будь ласка, оберіть дату зі списку 🙂",
        "flight_lookup_confirm": "За номером рейсу {flight} знайшов час посадки: {time}. Підтверджуєте?",
        "btn_confirm_yes": "Так, вірно",
        "btn_confirm_no": "Ні, введу вручну",
        "invalid_confirm": "Будь ласка, оберіть один з варіантів 🙂",
        "flight_lookup_failed": "Не вдалося автоматично знайти час посадки. Введіть його вручну (наприклад: 14:30)",
        "arrival_ask_manual_time": "Введіть час посадки вручну (наприклад: 14:30)",
        "friend_check": "Хм, цей рейс приземляється не в Барселоні. Ви точно мій друг? 🤔",
        "btn_friend_yes": "Так",
        "btn_friend_no": "Ні",
        "invalid_friend_check": "Будь ласка, оберіть Так або Ні 🙂",
        "arrival_ask_flight_again": "Добре, введіть номер рейсу саме до Барселони ✈️",
        "arrival_recorded": "✅ Записав: рейс {flight}, {date} серпня о {time}.",
        "waiting_for_others": "Очікуємо решту учасників...",
        "agenda_header": "📋 Програма зустрічі «v3»:",
        "arrivals_header": "🗓️ Агенда прильотів:",
        "cancel_msg": "Скасовано. Надішліть /start, щоб спробувати знову.",
        "myid_msg": "Ваш Telegram ID: {id}\nНадішліть цей номер організатору.",
        "daily_countdown": "📅 До зустрічі залишилось {n} дн.",
        "daily_today": "🎉 Сьогодні той самий день!",
        "daily_done": "✅ Зустріч вже відбулась.",
        "pill_reminder": "\n\n💊 Не забудьте прийняти таблетки!",
        "info_header": "ℹ️ Важлива інформація",
        "info_sergey_address": "🏠 Адреса і контакт Сергія: {address}\n📞 {contact}",
        "info_taxi": "🚕 Таксі-сервіси: {services}",
        "info_phrase_intro": "🗣️ Голосове повідомлення нижче — фраза іспанською для таксиста. Це домашня адреса, скажіть таксисту ввімкнути звук і прослухати:",
    },
    "ru": {
        "assistant_intro": "👋 Привет! Я ваш личный ассистент не только на эту встречу «v3» (7-9.08), но и на все последующие. Начнём с данных о рейсе.",
        "arrival_ask_flight": "✈️ Введите номер вашего рейса (например: LH1234)",
        "arrival_ask_date_choice": "На какую дату этот рейс?",
        "date_label_template": "{d} августа",
        "invalid_date_choice": "Пожалуйста, выберите дату из списка 🙂",
        "flight_lookup_confirm": "По номеру рейса {flight} нашёл время посадки: {time}. Подтверждаете?",
        "btn_confirm_yes": "Да, верно",
        "btn_confirm_no": "Нет, введу вручную",
        "invalid_confirm": "Пожалуйста, выберите один из вариантов 🙂",
        "flight_lookup_failed": "Не удалось автоматически найти время посадки. Введите его вручную (например: 14:30)",
        "arrival_ask_manual_time": "Введите время посадки вручную (например: 14:30)",
        "friend_check": "Хм, этот рейс приземляется не в Барселоне. Вы точно мой друг? 🤔",
        "btn_friend_yes": "Да",
        "btn_friend_no": "Нет",
        "invalid_friend_check": "Пожалуйста, выберите Да или Нет 🙂",
        "arrival_ask_flight_again": "Хорошо, введите номер рейса именно в Барселону ✈️",
        "arrival_recorded": "✅ Записал: рейс {flight}, {date} августа в {time}.",
        "waiting_for_others": "Ждём остальных участников...",
        "agenda_header": "📋 Программа встречи «v3»:",
        "arrivals_header": "🗓️ Агенда прилётов:",
        "cancel_msg": "Отменено. Отправьте /start, чтобы попробовать снова.",
        "myid_msg": "Ваш Telegram ID: {id}\nОтправьте этот номер организатору.",
        "daily_countdown": "📅 До встречи осталось {n} дн.",
        "daily_today": "🎉 Сегодня тот самый день!",
        "daily_done": "✅ Встреча уже прошла.",
        "pill_reminder": "\n\n💊 Не забудьте принять таблетки!",
        "info_header": "ℹ️ Важная информация",
        "info_sergey_address": "🏠 Адрес и контакт Сергея: {address}\n📞 {contact}",
        "info_taxi": "🚕 Такси-сервисы: {services}",
        "info_phrase_intro": "🗣️ Голосовое сообщение ниже — фраза на испанском для таксиста. Это домашний адрес, попросите таксиста включить звук и прослушать:",
    },
    "hy": {
        "assistant_intro": "👋 Բարև! Ես ձեր անձնական օգնականն եմ ոչ միայն այս «v3» հանդիպման (7-9.08), այլ նաև բոլոր հաջորդների համար։ Սկսենք չվերթի տվյալներից։",
        "arrival_ask_flight": "✈️ Մուտքագրեք ձեր չվերթի համարը (օրինակ՝ LH1234)",
        "arrival_ask_date_choice": "Ո՞ր ամսաթվին է այս չվերթը։",
        "date_label_template": "օգոստոսի {d}",
        "invalid_date_choice": "Խնդրում ենք ընտրել ամսաթիվը ցանկից 🙂",
        "flight_lookup_confirm": "{flight} չվերթի համարով գտա վայրէջքի ժամը՝ {time}. Հաստատու՞մ եք։",
        "btn_confirm_yes": "Այո, ճիշտ է",
        "btn_confirm_no": "Ոչ, ինքս կմուտքագրեմ",
        "invalid_confirm": "Խնդրում ենք ընտրել տարբերակներից մեկը 🙂",
        "flight_lookup_failed": "Ավտոմատ չհաջողվեց գտնել վայրէջքի ժամը։ Մուտքագրեք ինքներդ (օրինակ՝ 14:30)",
        "arrival_ask_manual_time": "Մուտքագրեք վայրէջքի ժամը ինքներդ (օրինակ՝ 14:30)",
        "friend_check": "Հմ, այս չվերթը վայրէջք չի կատարում Բարսելոնում։ Դուք իսկապե՞ս իմ ընկերն եք 🤔",
        "btn_friend_yes": "Այո",
        "btn_friend_no": "Ոչ",
        "invalid_friend_check": "Խնդրում ենք ընտրել Այո կամ Ոչ 🙂",
        "arrival_ask_flight_again": "Լավ, մուտքագրեք հենց Բարսելոն ուղևորվող չվերթի համարը ✈️",
        "arrival_recorded": "✅ Գրանցվեց՝ չվերթ {flight}, օգոստոսի {date}-ին ժամը {time}.",
        "waiting_for_others": "Սպասում ենք մնացած մասնակիցներին...",
        "agenda_header": "📋 «v3» հանդիպման ծրագիր.",
        "arrivals_header": "🗓️ Ժամանման օրակարգ.",
        "cancel_msg": "Չեղարկվեց։ Ուղարկեք /start՝ նորից փորձելու համար։",
        "myid_msg": "Ձեր Telegram ID-ն է՝ {id}\nՈւղարկեք այս համարը կազմակերպչին։",
        "daily_countdown": "📅 Հանդիպմանը մնացել է {n} օր.",
        "daily_today": "🎉 Այսօր հենց այն օրն է!",
        "daily_done": "✅ Հանդիպումն արդեն կայացել է։",
        "pill_reminder": "\n\n💊 Մի մոռացեք ընդունել դեղահաբերը!",
        "info_header": "ℹ️ Կարևոր տեղեկություն",
        "info_sergey_address": "🏠 Սերգեյի հասցեն և կոնտակտը՝ {address}\n📞 {contact}",
        "info_taxi": "🚕 Տաքսի ծառայություններ՝ {services}",
        "info_phrase_intro": "🗣️ Ստորև ձայնագրությունը՝ իսպաներեն արտահայտություն տաքսու վարորդի համար։ Սա տան հասցեն է, խնդրեք վարորդին միացնել ձայնը և լսել.",
    },
}


def t(user_id: int, key: str, **kwargs) -> str:
    """Look up a translated string for this user's language, with fallback to Ukrainian."""
    lang = PARTICIPANT_LANG.get(user_id, "uk")
    template = TEXTS.get(lang, TEXTS["uk"]).get(key, TEXTS["uk"][key])
    return template.format(**kwargs) if kwargs else template


def lookup_flight_arrival_time(flight_number: str, flight_date: datetime.date):
    """Try to look up the landing time and arrival airport for a flight via
    AeroDataBox. Returns (time_str, airport_iata) or (None, None) if the
    lookup isn't possible/fails."""
    if not FLIGHT_API_KEY:
        logging.warning("Flight lookup skipped: FLIGHT_API_KEY is not set")
        return None, None
    try:
        url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_number}/{flight_date.isoformat()}"
        headers = {
            "X-RapidAPI-Key": FLIGHT_API_KEY,
            "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com",
        }
        resp = requests.get(url, headers=headers, timeout=8)
        logging.warning(f"Flight lookup HTTP status for {flight_number}: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        logging.warning(f"Flight lookup raw response for {flight_number}: {data}")
        flight = data[0] if isinstance(data, list) and data else None
        if not flight:
            logging.warning(f"Flight lookup: no flight entries returned for {flight_number} on {flight_date}")
            return None, None
        arrival = flight.get("arrival", {})
        scheduled = arrival.get("scheduledTime", {}).get("local")
        airport_iata = arrival.get("airport", {}).get("iata")
        if not scheduled:
            logging.warning(f"Flight lookup: no arrival.scheduledTime.local in response for {flight_number}")
            return None, None
        return scheduled[11:16], airport_iata  # "YYYY-MM-DDTHH:MM..." -> "HH:MM", "BCN"
    except Exception as e:
        logging.warning(f"Flight lookup failed for {flight_number}: {e}")
        return None, None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcomed = get_welcomed_users(context)
    if user.id not in welcomed:
        welcomed.add(user.id)
        await update.message.reply_text(t(user.id, "assistant_intro"))

    await update.message.reply_text(t(user.id, "arrival_ask_flight"))
    return ARRIVAL_FLIGHT


async def arrival_get_flight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["flight_number"] = update.message.text.strip().upper()

    date_labels = [t(user.id, "date_label_template", d=d) for d in range(1, 8)]
    # Table layout: 4 buttons on the first row, 3 on the second.
    keyboard = ReplyKeyboardMarkup(
        [date_labels[:4], date_labels[4:]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(t(user.id, "arrival_ask_date_choice"), reply_markup=keyboard)
    return ARRIVAL_DATE_CHOICE


async def arrival_get_date_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    date_labels = {t(user.id, "date_label_template", d=d): d for d in range(1, 8)}
    day = date_labels.get(choice_text)

    if day is None:
        await update.message.reply_text(t(user.id, "invalid_date_choice"))
        return ARRIVAL_DATE_CHOICE

    context.user_data["flight_day"] = str(day)
    flight_date = datetime.date(MEETING_DATE.year, 8, day)

    found_time, airport_iata = lookup_flight_arrival_time(context.user_data["flight_number"], flight_date)

    if found_time and airport_iata != "BCN":
        # Wrong destination - double-check this is really the right person/flight.
        keyboard = ReplyKeyboardMarkup(
            [[t(user.id, "btn_friend_yes"), t(user.id, "btn_friend_no")]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
        await update.message.reply_text(t(user.id, "friend_check"), reply_markup=keyboard)
        return ARRIVAL_FRIEND_CHECK

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


async def arrival_friend_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    if choice_text == t(user.id, "btn_friend_yes"):
        await update.message.reply_text(t(user.id, "arrival_ask_flight_again"), reply_markup=ReplyKeyboardRemove())
        return ARRIVAL_FLIGHT
    elif choice_text == t(user.id, "btn_friend_no"):
        # Not actually a friend - fall back to manual entry rather than guessing further.
        await update.message.reply_text(t(user.id, "arrival_ask_manual_time"), reply_markup=ReplyKeyboardRemove())
        return ARRIVAL_MANUAL_TIME
    else:
        await update.message.reply_text(t(user.id, "invalid_friend_check"))
        return ARRIVAL_FRIEND_CHECK


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
    arrivals = get_arrivals(context)

    arrivals[user.id] = {
        "name": user.first_name,
        "flight": context.user_data.get("flight_number"),
        "date": context.user_data.get("flight_day"),
        "time": time_str,
    }

    await update.message.reply_text(
        t(user.id, "arrival_recorded",
          flight=arrivals[user.id]["flight"], date=arrivals[user.id]["date"], time=time_str)
        + "\n" + t(user.id, "waiting_for_others"),
        reply_markup=ReplyKeyboardRemove(),
    )

    await maybe_finalize(context)
    return ConversationHandler.END


async def maybe_finalize(context: ContextTypes.DEFAULT_TYPE):
    arrivals = get_arrivals(context)

    if len(arrivals) < ARRIVAL_PARTICIPANTS:
        return  # not everyone has submitted their flight yet

    def sort_key(item):
        info = item[1]
        return (info["date"], info["time"])

    ordered_arrivals = sorted(arrivals.items(), key=sort_key)

    for user_id in arrivals:
        lang = PARTICIPANT_LANG.get(user_id, "uk")
        program = PROGRAM.get(lang, PROGRAM["uk"])

        lines = [t(user_id, "arrivals_header"), ""]
        for _, info in ordered_arrivals:
            lines.append(f"• {info['name']}: {info['flight']}, {info['date']} серпня — {info['time']}")

        lines += ["", t(user_id, "agenda_header"), ""]
        for day in ("7", "8", "9"):
            lines.append(program[day])

        lines += [
            "",
            t(user_id, "info_header"),
            t(user_id, "info_sergey_address", address=SERGEY_ADDRESS, contact=SERGEY_CONTACT),
            t(user_id, "info_taxi", services=", ".join(TAXI_SERVICES)),
            "",
            t(user_id, "info_phrase_intro"),
        ]
        message = "\n".join(lines)

        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            await send_taxi_phrase_voice(context, user_id)
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


async def send_taxi_phrase_voice(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Generate the Spanish taxi phrase as speech and send it as a voice message only."""
    try:
        tts = gTTS(text=TAXI_PHRASE_ES, lang="es")
        audio_buffer = io.BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        await context.bot.send_voice(chat_id=chat_id, voice=audio_buffer)
    except Exception as e:
        logging.warning(f"Could not generate/send TTS for {chat_id}: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_user.id, "cancel_msg"))
    return ConversationHandler.END


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(t(user.id, "myid_msg", id=user.id))


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """On-demand resend of Sergey's contact/address, taxi services, and the voice phrase."""
    user = update.effective_user
    lines = [
        t(user.id, "info_header"),
        t(user.id, "info_sergey_address", address=SERGEY_ADDRESS, contact=SERGEY_CONTACT),
        t(user.id, "info_taxi", services=", ".join(TAXI_SERVICES)),
        "",
        t(user.id, "info_phrase_intro"),
    ]
    await update.message.reply_text("\n".join(lines))
    await send_taxi_phrase_voice(context, user.id)


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
        get_personal_answers(context)[user.id] = context.user_data["mq_answers"]
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
    # Make sure the folder for the persistence file exists (Railway Volume
    # mounted at /data, or wherever PERSISTENCE_PATH points).
    os.makedirs(os.path.dirname(PERSISTENCE_PATH), exist_ok=True)
    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)

    app = ApplicationBuilder().token(BOT_TOKEN).persistence(persistence).build()
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("info", info))

    main_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ARRIVAL_FLIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_get_flight)],
            ARRIVAL_DATE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_get_date_choice)],
            ARRIVAL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_confirm_time)],
            ARRIVAL_MANUAL_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_manual_time)],
            ARRIVAL_FRIEND_CHECK: [MessageHandler(filters.TEXT & ~filters.COMMAND, arrival_friend_check)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="main_conv",
        persistent=True,
    )
    app.add_handler(main_conv)

    myquestions_conv = ConversationHandler(
        entry_points=[CommandHandler("myquestions", myquestions_start)],
        states={
            MY_QUESTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, myquestions_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="myquestions_conv",
        persistent=True,
    )
    app.add_handler(myquestions_conv)

    # Daily broadcast: countdown + joke (+ pill reminder for listed users)
    app.job_queue.run_daily(daily_broadcast, time=DAILY_SEND_TIME)

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
