"""
Retreat Personal Assistant Bot
--------------------------------------------------
1) /start        - introduces itself as an ongoing personal assistant for all
                   retreats (not just this one, first use only). Asks flight
                   number + date (6 or 7 August), tries to auto-look-up the
                   Barcelona landing time via a flight API, asks to confirm
                   (or falls back to manual entry).
2) Agenda voting - right after arrival info is saved, asks each person to
                   vote on the plan for 7, 8, and 9 August (two options per
                   day - edit AGENDA_OPTIONS with the real choices). Once all
                   ARRIVAL_PARTICIPANTS people finish both arrival + agenda
                   voting, everyone gets: the sorted arrival list, the
                   winning agenda option per day, and important info -
                   Sergey's address & phone, taxi services, and a Spanish
                   taxi-driver phrase sent ONLY as a voice message (with a
                   short explanation that it's the home address).
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
  4) Set MEETING_DATE, DAILY_SEND_TIME, fill in AGENDA_OPTIONS with the real
     choices for 7/8/9 August, and SERGEY_CONTACT / ACCOMMODATION_ADDRESS
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

# Agenda options to vote on for each day - EDIT with the real choices.
# Each day has exactly two options; majority wins.
AGENDA_OPTIONS = {
    "uk": {
        "7": ["Варіант A на 7 серпня", "Варіант B на 7 серпня"],
        "8": ["Варіант A на 8 серпня", "Варіант B на 8 серпня"],
        "9": ["Варіант A на 9 серпня", "Варіант B на 9 серпня"],
    },
    "ru": {
        "7": ["Вариант A на 7 августа", "Вариант B на 7 августа"],
        "8": ["Вариант A на 8 августа", "Вариант B на 8 августа"],
        "9": ["Вариант A на 9 августа", "Вариант B на 9 августа"],
    },
    "hy": {
        "7": ["Տարբերակ Ա՝ օգոստոսի 7", "Տարբերակ Բ՝ օգոստոսի 7"],
        "8": ["Տարբերակ Ա՝ օգոստոսի 8", "Տարբերակ Բ՝ օգոստոսի 8"],
        "9": ["Տարբերակ Ա՝ օգոստոսի 9", "Տարբերակ Բ՝ օգոստոսի 9"],
    },
}

logging.basicConfig(level=logging.INFO)

# Data below lives in context.bot_data (auto-saved by PicklePersistence) so
# it survives restarts/redeploys instead of these module-level dicts.


def get_arrivals(context):
    return context.bot_data.setdefault("arrivals", {})


def get_agenda_votes(context):
    return context.bot_data.setdefault("agenda_votes", {})


def get_welcomed_users(context):
    return context.bot_data.setdefault("welcomed_users", set())


def get_personal_answers(context):
    return context.bot_data.setdefault("personal_answers", {})

(
    ARRIVAL_FLIGHT,
    ARRIVAL_DATE_CHOICE,
    ARRIVAL_CONFIRM,
    ARRIVAL_MANUAL_TIME,
    AGENDA_VOTE_7,
    AGENDA_VOTE_8,
    AGENDA_VOTE_9,
) = range(7)
MY_QUESTIONS = 7  # separate state for the personalized questionnaire


TEXTS = {
    "uk": {
        "assistant_intro": "👋 Привіт! Я ваш особистий асистент не тільки на цю зустріч «v3» (7-9.08), а й на всі наступні. Почнемо з даних про рейс.",
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
        "arrival_recorded": "✅ Записав: рейс {flight}, {date} серпня о {time}.",
        "agenda_intro": "Тепер оберіть план на кожен день зустрічі «v3» 🗓️",
        "agenda_ask_day": "День {day} серпня — який варіант обираєте?",
        "invalid_agenda_choice": "Будь ласка, оберіть один із запропонованих варіантів 🙂",
        "agenda_done_waiting": "✅ Дякую! Всі відповіді записано. Очікуємо решту учасників...",
        "agenda_header": "📋 Програма зустрічі «v3» (за результатами голосування):",
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
        "btn_aug6": "6 августа",
        "btn_aug7": "7 августа",
        "invalid_date_choice": "Пожалуйста, выберите 6 или 7 августа 🙂",
        "flight_lookup_confirm": "По номеру рейса {flight} нашёл время посадки в Барселоне: {time}. Подтверждаете?",
        "btn_confirm_yes": "Да, верно",
        "btn_confirm_no": "Нет, введу вручную",
        "invalid_confirm": "Пожалуйста, выберите один из вариантов 🙂",
        "flight_lookup_failed": "Не удалось автоматически найти время посадки. Введите его вручную (например: 14:30)",
        "arrival_ask_manual_time": "Введите время посадки вручную (например: 14:30)",
        "arrival_recorded": "✅ Записал: рейс {flight}, {date} августа в {time}.",
        "agenda_intro": "Теперь выберите план на каждый день встречи «v3» 🗓️",
        "agenda_ask_day": "День {day} августа — какой вариант выбираете?",
        "invalid_agenda_choice": "Пожалуйста, выберите один из предложенных вариантов 🙂",
        "agenda_done_waiting": "✅ Спасибо! Все ответы записаны. Ждём остальных участников...",
        "agenda_header": "📋 Программа встречи «v3» (по результатам голосования):",
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
        "btn_aug6": "օգոստոսի 6",
        "btn_aug7": "օգոստոսի 7",
        "invalid_date_choice": "Խնդրում ենք ընտրել օգոստոսի 6 կամ 7 🙂",
        "flight_lookup_confirm": "{flight} չվերթի համարով գտա Բարսելոնում վայրէջքի ժամը՝ {time}. Հաստատու՞մ եք։",
        "btn_confirm_yes": "Այո, ճիշտ է",
        "btn_confirm_no": "Ոչ, ինքս կմուտքագրեմ",
        "invalid_confirm": "Խնդրում ենք ընտրել տարբերակներից մեկը 🙂",
        "flight_lookup_failed": "Ավտոմատ չհաջողվեց գտնել վայրէջքի ժամը։ Մուտքագրեք ինքներդ (օրինակ՝ 14:30)",
        "arrival_ask_manual_time": "Մուտքագրեք վայրէջքի ժամը ինքներդ (օրինակ՝ 14:30)",
        "arrival_recorded": "✅ Գրանցվեց՝ չվերթ {flight}, օգոստոսի {date}-ին ժամը {time}.",
        "agenda_intro": "Այժմ ընտրեք ծրագիրը «v3» հանդիպման յուրաքանչյուր օրվա համար 🗓️",
        "agenda_ask_day": "Օգոստոսի {day}-ը — ո՞ր տարբերակն եք ընտրում։",
        "invalid_agenda_choice": "Խնդրում ենք ընտրել առաջարկվող տարբերակներից մեկը 🙂",
        "agenda_done_waiting": "✅ Շնորհակալություն! Բոլոր պատասխանները գրանցված են։ Սպասում ենք մնացած մասնակիցներին...",
        "agenda_header": "📋 «v3» հանդիպման ծրագիր (քվեարկության արդյունքով).",
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
    """Try to look up the Barcelona landing time for a flight via AeroDataBox.
    Returns a "HH:MM" string, or None if the lookup isn't possible/fails."""
    if not FLIGHT_API_KEY:
        logging.warning("Flight lookup skipped: FLIGHT_API_KEY is not set")
        return None
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
            return None
        arrival = flight.get("arrival", {})
        scheduled = arrival.get("scheduledTime", {}).get("local")
        if not scheduled:
            logging.warning(f"Flight lookup: no arrival.scheduledTime.local in response for {flight_number}")
            return None
        return scheduled[11:16]  # "YYYY-MM-DDTHH:MM..." -> "HH:MM"
    except Exception as e:
        logging.warning(f"Flight lookup failed for {flight_number}: {e}")
        return None


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
    arrivals = get_arrivals(context)

    arrivals[user.id] = {
        "name": user.first_name,
        "flight": context.user_data.get("flight_number"),
        "date": context.user_data.get("flight_day"),
        "time": time_str,
    }

    await update.message.reply_text(
        t(user.id, "arrival_recorded",
          flight=arrivals[user.id]["flight"], date=arrivals[user.id]["date"], time=time_str),
        reply_markup=ReplyKeyboardRemove(),
    )

    # Immediately continue into agenda voting for 7 August.
    await update.message.reply_text(t(user.id, "agenda_intro"))
    return await ask_agenda_day(update, context, "7", AGENDA_VOTE_7)


async def ask_agenda_day(update: Update, context: ContextTypes.DEFAULT_TYPE, day: str, next_state: int):
    user = update.effective_user
    lang = PARTICIPANT_LANG.get(user.id, "uk")
    options = AGENDA_OPTIONS.get(lang, AGENDA_OPTIONS["uk"])[day]

    keyboard = ReplyKeyboardMarkup([options], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(t(user.id, "agenda_ask_day", day=day), reply_markup=keyboard)
    return next_state


async def agenda_vote_7(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await record_agenda_vote(update, context, "7", "8", AGENDA_VOTE_8)


async def agenda_vote_8(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await record_agenda_vote(update, context, "8", "9", AGENDA_VOTE_9)


async def agenda_vote_9(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await record_agenda_vote(update, context, "9", None, None)


async def record_agenda_vote(update: Update, context: ContextTypes.DEFAULT_TYPE, day: str, next_day, next_state):
    user = update.effective_user
    lang = PARTICIPANT_LANG.get(user.id, "uk")
    options = AGENDA_OPTIONS.get(lang, AGENDA_OPTIONS["uk"])[day]
    choice_text = update.message.text.strip()

    if choice_text not in options:
        await update.message.reply_text(t(user.id, "invalid_agenda_choice"))
        return AGENDA_VOTE_7 if day == "7" else AGENDA_VOTE_8 if day == "8" else AGENDA_VOTE_9

    agenda_votes = get_agenda_votes(context)
    agenda_votes.setdefault(user.id, {})[day] = options.index(choice_text)

    if next_day:
        return await ask_agenda_day(update, context, next_day, next_state)

    # Last day (9) answered - done with this person's flow.
    await update.message.reply_text(t(user.id, "agenda_done_waiting"), reply_markup=ReplyKeyboardRemove())
    await maybe_finalize(context)
    return ConversationHandler.END


async def maybe_finalize(context: ContextTypes.DEFAULT_TYPE):
    arrivals = get_arrivals(context)
    agenda_votes = get_agenda_votes(context)

    done = set(arrivals.keys()) & set(agenda_votes.keys())
    done = {uid for uid in done if len(agenda_votes[uid]) == 3}
    if len(done) < ARRIVAL_PARTICIPANTS:
        return  # not everyone has finished both steps yet

    def sort_key(item):
        info = item[1]
        return (info["date"], info["time"])

    ordered_arrivals = sorted(arrivals.items(), key=sort_key)

    # Tally majority per day (index 0 or 1) among everyone who finished.
    winning_index = {}
    for day in ("7", "8", "9"):
        counts = [0, 0]
        for uid in done:
            counts[agenda_votes[uid][day]] += 1
        winning_index[day] = 0 if counts[0] >= counts[1] else 1

    for user_id in done:
        lang = PARTICIPANT_LANG.get(user_id, "uk")
        options = AGENDA_OPTIONS.get(lang, AGENDA_OPTIONS["uk"])

        lines = [t(user_id, "arrivals_header"), ""]
        for _, info in ordered_arrivals:
            lines.append(f"• {info['name']}: {info['flight']}, {info['date']} серпня — {info['time']}")

        lines += ["", t(user_id, "agenda_header"), ""]
        for day in ("7", "8", "9"):
            lines.append(options[day][winning_index[day]])

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
            AGENDA_VOTE_7: [MessageHandler(filters.TEXT & ~filters.COMMAND, agenda_vote_7)],
            AGENDA_VOTE_8: [MessageHandler(filters.TEXT & ~filters.COMMAND, agenda_vote_8)],
            AGENDA_VOTE_9: [MessageHandler(filters.TEXT & ~filters.COMMAND, agenda_vote_9)],
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
