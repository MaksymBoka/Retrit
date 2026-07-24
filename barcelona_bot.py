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
4) /sos          - asks the person to share their location, then alerts
                   everyone else (or the group, if GROUP_CHAT_ID is set)
                   with "{name} needs help!" plus a location pin
6) Daily broadcast (countdown + joke) at a different random time each day,
   only during DAILY_JOKE_RANGE_START..END. Separately, three day-specific
   reminders run in the final stretch: 4 Aug - individual voice (ES then
   UK); 5 Aug - group voice (ES then UK); 6 Aug - group voice (Polish then
   Ukrainian). Group ones fall back to individual DMs if GROUP_CHAT_ID
   isn't set.
7) Live-location reminders at exact scheduled times (LIVE_LOCATION_REMINDER_SCHEDULE) -
   nudges everyone to re-share their live location in the group chat, since
   Telegram's native live-location share (📎 → Location → Share My Live
   Location) caps out at 8 hours per share. This bot does NOT replace that
   feature - it just reminds people to renew it themselves in the group.
8) /info         - re-sends Sergey's contact/address, taxi services, and the
                   voice phrase on demand (same content as after agenda voting)
9) Group welcome - when the bot is added to a group chat, it greets everyone
                   there (all 3 languages) asking them to DM it and send
                   /start, and logs the group's chat ID (search Railway logs
                   for "GROUP_CHAT_ID=") so you can set it as the GROUP_CHAT_ID
                   env var for SOS alerts and the 23:30 joke.

Each person gets a fixed language (no picker) based on their Telegram ID in
PARTICIPANT_LANG: "uk" (Ukrainian), "hy" (Armenian), or "pl" (Polish).

SETUP:
  1) pip install "python-telegram-bot[job-queue]" requests gTTS --upgrade
  2) Set BOT_TOKEN below (get one from @BotFather on Telegram)
  3) Optional: set FLIGHT_API_KEY (AeroDataBox via RapidAPI) for automatic
     flight-time lookup. Without it, the bot just asks for time manually.
  4) Set MEETING_DATE, DAILY_BROADCAST_WINDOW_START/END, fill in PROGRAM
     with the real plan for 7/8/9 August, and SERGEY_CONTACT / SERGEY_ADDRESS
  5) Persistence: on Railway, add a Volume (Settings → Volumes) mounted at
     /data so answers and mid-conversation progress survive redeploys.
     Locally this just creates a file in the working directory.
  6) Run the bot, have everyone DM /myid, then fill PARTICIPANT_IDS and
     PARTICIPANT_LANG (uk/hy/pl per person)
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
PERSISTENCE_PATH = os.environ.get("PERSISTENCE_PATH", "/data/bot_persistence_v2.pickle")

# Optional: flight-lookup API key (AeroDataBox via RapidAPI). If not set, the
# bot skips auto-lookup and just asks each person to type their arrival time.
FLIGHT_API_KEY = os.environ.get("FLIGHT_API_KEY")

ARRIVAL_PARTICIPANTS = 4  # how many people go through arrival+agenda (excludes organizer)

# Which August dates a flight can land on - shown as date-picker buttons.
AVAILABLE_FLIGHT_DAYS = [1, 6, 7]

# The date of the meetup/trip - countdown counts down to this.
MEETING_DATE = datetime.date(2026, 8, 7)  # YYYY, M, D - first day of the gathering

# The text+joke daily broadcast only runs on these dates (inclusive).
# Voice-only reminders (Aug 4/5/6, below) still run separately on the
# last few days before MEETING_DATE, regardless of this range.
DAILY_JOKE_RANGE_START = datetime.date(2026, 7, 28)
DAILY_JOKE_RANGE_END = datetime.date(2026, 8, 3)

# Time of day the daily broadcast (countdown + joke) goes out.
# Daily broadcast goes out once a day at a different random time each day,
# somewhere within this window (24h format).
DAILY_BROADCAST_WINDOW_START = datetime.time(hour=9, minute=0)
DAILY_BROADCAST_WINDOW_END = datetime.time(hour=21, minute=0)

# Live-location reminder window - bot nudges everyone to re-share live
# location (Telegram caps a single share at 8h) every 8 hours during these
# dates. EDIT if the gathering dates change.
# Exact live-location reminder times (day of August: [times]). Telegram
# caps a single live-location share at 8h, so these are timed to prompt a
# re-share before the previous one expires. EDIT freely - add/remove
# entries as the schedule changes.
LIVE_LOCATION_REMINDER_SCHEDULE = {
    6: [datetime.time(hour=20, minute=30)],
    7: [datetime.time(hour=19, minute=0)],
    8: [datetime.time(hour=12, minute=0)],
}

# All 5 participants' Telegram user IDs - needed for the daily broadcast.
# Use /myid in a chat with the bot to find each person's ID.

# Group chat to post SOS alerts to (get this via a tool like @userinfobot
# added to the group, or by checking update.effective_chat.id when the bot
# is added there). If left as None, /sos alerts go to everyone individually
# instead of one group message.
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")
if GROUP_CHAT_ID:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID)

# Organizer's own ID - gets a special notice once literally everyone has
# finished registration (flight info submitted).
ORGANIZER_ID = 244007332

PARTICIPANT_IDS = [
    470469942,  # Дмитро
    244007332,  # Maksym (організатор)
    278596529,  # Аршак
    394213736,  # Георгій
    329102299,  # Maksym (другий)
]

# Language per participant: "uk" (Ukrainian), "hy" (Armenian), "pl" (Polish).
# Default is "uk" for anyone not listed here.
PARTICIPANT_LANG = {
    244007332: "uk",  # Maksym
    470469942: "uk",  # Дмитро
    278596529: "hy",  # Аршак
    394213736: "pl",  # Георгій
    329102299: "uk",  # Maksym (другий)
}

JOKES = [
    "Чому барселонці не грають в хованки? Бо гарне місце завжди знайдуть 😄",
    "Кажуть, найкращий вид на Барселону — з балкону, який ви ще не обрали 🏙️",
    "Тапас — це коли їжі мало, а щастя багато 🍤",
    "П'ять людей, дві опції — і жодного консенсусу без цього бота 😅",
]

# Spoken reminder for the last few days before departure: Spanish first,
# then the same idea in Ukrainian, both sent as voice messages.
# Day-specific reminders for the final stretch before departure.
# 4 Aug - individual voice reminder to each person, Spanish then Ukrainian.
AUG4_VOICE_TEXT_ES = "Te espera un viaje inolvidable"
AUG4_VOICE_TEXT_UK = "На тебе чекає незабутня подорож"
AUG4_REMINDER_TIME = datetime.time(hour=11, minute=0)

# 5 Aug - group voice message, Spanish then Ukrainian (falls back to
# individual DMs if GROUP_CHAT_ID isn't set).
AUG5_VOICE_TEXT_UK = "Просто розслабляємося, чекаємо на зустріч! Макс, тобі ще додатково треба витримати один день і нічого не наговорити Наташі, щоб спокійно сісти на літак."
AUG5_VOICE_TEXT_ES = "Simplemente relajémonos, esperamos el encuentro. Max, todavía tienes que aguantar un día más y no decirle nada a Natasha, para poder subir tranquilo al avión."
AUG5_REMINDER_TIME = datetime.time(hour=11, minute=0)

# 6 Aug - group message: Polish voice + Ukrainian text (falls back to
# individual DMs if GROUP_CHAT_ID isn't set).
AUG6_VOICE_TEXT_PL = "Ten dzień nadszedł! Wkrótce się zobaczymy!"
AUG6_TEXT_UK = "Цей день настав! Скоро побачимось!"
AUG6_REMINDER_TIME = datetime.time(hour=11, minute=0)

# Personal one-off jokes sent right after each specific person's own
# arrival is recorded. All written in Ukrainian regardless of the person's
# usual bot language. Keyed by Telegram ID.
PERSONAL_ARRIVAL_JOKES = {
    329102299: "😄 Максиме, першу задачу ми з тобою пройшли. Наташа тебе відпустила, тепер тримайся і просто поводься чемно якихось декілька днів.",
    470469942: "😄 Вася, зі мною будеш говорити 24/7.",  # Дмитро
    394213736: "😄 Жора, ти класний!",  # Георгій
    278596529: "😄 Дякую що довірив мені програму і можливість потурбуватися про тебе. Тепер просто розслабляємось.",  # Аршак
}

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
        "6": "🛬 Приземлились — і одразу трансфер на квартиру, залишаємо речі\n22:00 — вечір стартуємо на даху: Azimut Rooftop 🌆\nhttps://www.almanachotels.com/barcelona/food-drinks/azimuth-rooftop-bar/",
        "7": "☕ Сніданок і кава прямо біля апартаментів — не поспішаємо\nВільний час до обіду\n15:00 — пізній обід у Barceloneta 🍽️\nhttps://restaurantbarceloneta.com/\n18:00 — морозиво і прогулянка набережною 🍦\n21:00 — стартуємо вечірню програму",
        "8": "🏖️ День відпочинку — без зайвого поспіху\n14:00 — Bastian Beach Club 🌊\nhttps://bastianbeach.com/en/\nДалі просто продовжуємо відпочивати",
        "9": "9 серпня: [заповніть програму дня]",
    },
    "hy": {
        "6": "🛬 Վայրէջք — և անմիջապես տեղափոխում բնակարան, թողնում ենք իրերը\n22:00 — երեկոն սկսում ենք տանիքից. Azimut Rooftop 🌆\nhttps://www.almanachotels.com/barcelona/food-drinks/azimuth-rooftop-bar/",
        "7": "☕ Նախաճաշ և սուրճ հենց բնակարանի մոտ — չենք շտապում\nԱզատ ժամանակ մինչև ճաշ\n15:00 — ուշ ճաշ Barceloneta-ում 🍽️\nhttps://restaurantbarceloneta.com/\n18:00 — պաղպաղակ և զբոսանք ափով 🍦\n21:00 — սկսում ենք երեկոյան ծրագիրը",
        "8": "🏖️ Հանգստի օր — առանց շտապելու\n14:00 — Bastian Beach Club 🌊\nhttps://bastianbeach.com/en/\nՀետո պարզապես շարունակում ենք հանգստանալ",
        "9": "օգոստոսի 9. [լրացրեք օրվա ծրագիրը]",
    },
    "pl": {
        "6": "🛬 Wylądowaliśmy — od razu transfer do mieszkania, zostawiamy rzeczy\n22:00 — wieczór zaczynamy na dachu: Azimut Rooftop 🌆\nhttps://www.almanachotels.com/barcelona/food-drinks/azimuth-rooftop-bar/",
        "7": "☕ Śniadanie i kawa tuż przy apartamentach — nie spieszymy się\nCzas wolny do obiadu\n15:00 — późny obiad w Barcelonecie 🍽️\nhttps://restaurantbarceloneta.com/\n18:00 — lody i spacer nadmorski 🍦\n21:00 — startujemy program wieczorny",
        "8": "🏖️ Dzień odpoczynku — bez pośpiechu\n14:00 — Bastian Beach Club 🌊\nhttps://bastianbeach.com/en/\nDalej po prostu odpoczywamy",
        "9": "9 sierpnia: [uzupełnij program dnia]",
    },
}

logging.basicConfig(level=logging.INFO)

# Data below lives in context.bot_data (auto-saved by PicklePersistence) so
# it survives restarts/redeploys instead of these module-level dicts.


def get_arrivals(context):
    return context.bot_data.setdefault("arrivals", {})


def get_welcomed_users(context):
    return context.bot_data.setdefault("welcomed_users", set())


(
    ARRIVAL_FLIGHT,
    ARRIVAL_DATE_CHOICE,
    ARRIVAL_CONFIRM,
    ARRIVAL_MANUAL_TIME,
    ARRIVAL_FRIEND_CHECK,
) = range(5)


TEXTS = {
    "uk": {
        "assistant_intro": "👋 Привіт! Я ваш особистий асистент не тільки на цю зустріч «v3» (7-9.08), а й на всі наступні. Почнемо з даних про рейс.",
        "commands_list": "ℹ️ Корисні команди:\n/info — адреса, контакт Сергія, таксі\n/sos — надіслати сигнал допомоги з локацією",
        "arrival_ask_flight": "✈️ Введіть номер вашого рейсу (наприклад: LH1234)",
        "arrival_ask_date_choice": "На яку дату цей рейс?",
        "date_label_template": "{d} серпня",
        "invalid_date_choice": "Будь ласка, оберіть дату зі списку 🙂",
        "flight_lookup_confirm": "За номером рейсу {flight} знайшов час посадки: {time}. Підтверджуєте?",
        "btn_confirm_yes": "Так, вірно",
        "btn_confirm_no": "Ні, введу вручну",
        "invalid_confirm": "Будь ласка, оберіть один з варіантів 🙂",
        "flight_lookup_failed": "Не вдалося автоматично знайти час посадки. Але зазвичай у Максима розумні друзі — значить, і ти зможеш! Спробуй ввести номер рейсу ще раз:",
        "arrival_ask_manual_time": "Введіть час посадки вручну (наприклад: 14:30)",
        "friend_check": "Хм, цей рейс приземляється не в Барселоні. Ви точно мій друг? 🤔",
        "btn_friend_yes": "Так",
        "btn_friend_no": "Ні",
        "invalid_friend_check": "Будь ласка, оберіть Так або Ні 🙂",
        "arrival_ask_flight_again": "Добре, введіть номер рейсу саме до Барселони ✈️",
        "friend_congrats": "🎉 Вітаю! Велика честь бути другом Максима!",
        "friend_reject": "Тоді, будь ласка, зверніться напряму до Максима 🙂 А поки що, чесно кажучи, раджу вийти з групи.",
        "arrival_recorded": "✅ Записав: рейс {flight}, {date} серпня о {time}.",
        "waiting_for_others": "Очікуємо решту учасників...",
        "agenda_header": "📋 Програма зустрічі «v3»:",
        "arrivals_header": "🗓️ Агенда прильотів:",
        "cancel_msg": "Скасовано. Надішліть /start, щоб спробувати знову.",
        "myid_msg": "Ваш Telegram ID: {id}\nНадішліть цей номер організатору.",
        "sos_ask_location": "🆘 Надішліть свою локацію (📎 → Локація), і я одразу сповіщу всіх.",
        "sos_no_location": "Це не локація 🙂 Натисніть 📎 → Локація і надішліть.",
        "sos_confirmed": "✅ Сигнал відправлено всім учасникам разом з вашою локацією.",
        "sos_alert": "🆘 УВАГА! {name} потребує допомоги!",
        "daily_countdown": "📅 До зустрічі залишилось {n} дн.",
        "daily_today": "🎉 Сьогодні той самий день!",
        "daily_done": "✅ Зустріч вже відбулась.",
        "live_location_reminder": "📍 Не забудьте оновити live-локацію в груповому чаті ще на 8 годин, щоб ми всі бачили одне одного на карті!",
        "info_header": "ℹ️ Важлива інформація",
        "info_sergey_address": "🏠 Адреса і контакт Сергія: {address}\n📞 {contact}",
        "info_taxi": "🚕 Таксі-сервіси: {services}",
        "info_phrase_intro": "🗣️ Голосове повідомлення нижче — фраза іспанською для таксиста. Це домашня адреса, скажіть таксисту ввімкнути звук і прослухати:",
        "friend_vs_brother_joke": "😄 Чим друг відрізняється від брата?\nБрата дає доля. Друга обирають самі.",
        "group_welcome": "👋 Привіт усім! Я особистий асистент на зустріч «v3». Щоб я міг допомогти з рейсом, датами і всім іншим — напишіть мені особисто @{bot_username} і надішліть /start 🙂",
    },
    "pl": {
        "assistant_intro": "👋 Cześć! Jestem twoim osobistym asystentem nie tylko na to spotkanie «v3» (7-9.08), ale też na wszystkie kolejne. Zacznijmy od danych o locie.",
        "commands_list": "ℹ️ Przydatne komendy:\n/info — adres, kontakt do Serhija, taksówki\n/sos — wysłać sygnał pomocy z lokalizacją",
        "arrival_ask_flight": "✈️ Podaj numer swojego lotu (np: LH1234)",
        "arrival_ask_date_choice": "Na jaką datę jest ten lot?",
        "date_label_template": "{d} sierpnia",
        "invalid_date_choice": "Proszę wybrać datę z listy 🙂",
        "flight_lookup_confirm": "Dla numeru lotu {flight} znalazłem godzinę lądowania: {time}. Potwierdzasz?",
        "btn_confirm_yes": "Tak, zgadza się",
        "btn_confirm_no": "Nie, wpiszę ręcznie",
        "invalid_confirm": "Proszę wybrać jedną z opcji 🙂",
        "flight_lookup_failed": "Nie udało się automatycznie znaleźć godziny lądowania. Ale zwykle Maksym ma mądrych przyjaciół — więc na pewno dasz radę! Spróbuj podać numer lotu jeszcze raz:",
        "arrival_ask_manual_time": "Podaj godzinę lądowania ręcznie (np: 14:30)",
        "friend_check": "Hmm, ten lot nie ląduje w Barcelonie. Na pewno jesteś moim przyjacielem? 🤔",
        "btn_friend_yes": "Tak",
        "btn_friend_no": "Nie",
        "invalid_friend_check": "Proszę wybrać Tak lub Nie 🙂",
        "arrival_ask_flight_again": "Dobrze, podaj numer lotu właśnie do Barcelony ✈️",
        "friend_congrats": "🎉 Gratulacje! To wielki zaszczyt być przyjacielem Maksyma!",
        "friend_reject": "W takim razie, proszę, skontaktuj się bezpośrednio z Maksymem 🙂 A na razie, szczerze mówiąc, radzę opuścić grupę.",
        "arrival_recorded": "✅ Zapisano: lot {flight}, {date} sierpnia o {time}.",
        "waiting_for_others": "Czekamy na pozostałych uczestników...",
        "agenda_header": "📋 Program spotkania «v3»:",
        "arrivals_header": "🗓️ Harmonogram przylotów:",
        "cancel_msg": "Anulowano. Wyślij /start, aby spróbować ponownie.",
        "myid_msg": "Twoje Telegram ID: {id}\nWyślij ten numer organizatorowi.",
        "sos_ask_location": "🆘 Wyślij swoją lokalizację (📎 → Lokalizacja), a od razu powiadomię wszystkich.",
        "sos_no_location": "To nie jest lokalizacja 🙂 Naciśnij 📎 → Lokalizacja i wyślij.",
        "sos_confirmed": "✅ Sygnał wysłany do wszystkich uczestników razem z twoją lokalizacją.",
        "sos_alert": "🆘 UWAGA! {name} potrzebuje pomocy!",
        "daily_countdown": "📅 Do spotkania zostało {n} dni.",
        "daily_today": "🎉 Dziś jest ten dzień!",
        "daily_done": "✅ Spotkanie już się odbyło.",
        "live_location_reminder": "📍 Nie zapomnij odnowić live-lokalizacji w czacie grupowym na kolejne 8 godzin, żebyśmy wszyscy widzieli się na mapie!",
        "info_header": "ℹ️ Ważne informacje",
        "info_sergey_address": "🏠 Adres i kontakt do Serhija: {address}\n📞 {contact}",
        "info_taxi": "🚕 Serwisy taxi: {services}",
        "info_phrase_intro": "🗣️ Wiadomość głosowa poniżej — fraza po hiszpańsku dla taksówkarza. To adres domowy, poproś taksówkarza o włączenie dźwięku i odsłuchanie:",
        "friend_vs_brother_joke": "😄 Czym różni się przyjaciel od brata?\nBrata daje los. Przyjaciela wybiera się samemu.",
        "group_welcome": "👋 Cześć wszystkim! Jestem osobistym asystentem na spotkanie «v3». Aby pomóc z lotem, datami i resztą — napiszcie do mnie prywatnie @{bot_username} i wyślijcie /start 🙂",
    },
    "hy": {
        "assistant_intro": "👋 Բարև! Ես ձեր անձնական օգնականն եմ ոչ միայն այս «v3» հանդիպման (7-9.08), այլ նաև բոլոր հաջորդների համար։ Սկսենք չվերթի տվյալներից։",
        "commands_list": "ℹ️ Օգտակար հրամաններ.\n/info — հասցե, Սերգեյի կոնտակտ, տաքսի\n/sos — ուղարկել օգնության ազդանշան՝ տեղադրությամբ",
        "arrival_ask_flight": "✈️ Մուտքագրեք ձեր չվերթի համարը (օրինակ՝ LH1234)",
        "arrival_ask_date_choice": "Ո՞ր ամսաթվին է այս չվերթը։",
        "date_label_template": "օգոստոսի {d}",
        "invalid_date_choice": "Խնդրում ենք ընտրել ամսաթիվը ցանկից 🙂",
        "flight_lookup_confirm": "{flight} չվերթի համարով գտա վայրէջքի ժամը՝ {time}. Հաստատու՞մ եք։",
        "btn_confirm_yes": "Այո, ճիշտ է",
        "btn_confirm_no": "Ոչ, ինքս կմուտքագրեմ",
        "invalid_confirm": "Խնդրում ենք ընտրել տարբերակներից մեկը 🙂",
        "flight_lookup_failed": "Ավտոմատ չհաջողվեց գտնել վայրէջքի ժամը։ Բայց սովորաբար Մաքսիմն ունի խելացի ընկերներ, ուրեմն դու էլ կկարողանաս! Փորձիր նորից մուտքագրել չվերթի համարը.",
        "arrival_ask_manual_time": "Մուտքագրեք վայրէջքի ժամը ինքներդ (օրինակ՝ 14:30)",
        "friend_check": "Հմ, այս չվերթը վայրէջք չի կատարում Բարսելոնում։ Դուք իսկապե՞ս իմ ընկերն եք 🤔",
        "btn_friend_yes": "Այո",
        "btn_friend_no": "Ոչ",
        "invalid_friend_check": "Խնդրում ենք ընտրել Այո կամ Ոչ 🙂",
        "arrival_ask_flight_again": "Լավ, մուտքագրեք հենց Բարսելոն ուղևորվող չվերթի համարը ✈️",
        "friend_congrats": "🎉 Շնորհավորում եմ! Մեծ պատիվ է լինել Մաքսիմի ընկերը!",
        "friend_reject": "Այդ դեպքում, խնդրում ենք դիմել ուղիղ Մաքսիմին 🙂 Իսկ առայժմ, անկեղծ ասած, խորհուրդ եմ տալիս լքել խումբը.",
        "arrival_recorded": "✅ Գրանցվեց՝ չվերթ {flight}, օգոստոսի {date}-ին ժամը {time}.",
        "waiting_for_others": "Սպասում ենք մնացած մասնակիցներին...",
        "agenda_header": "📋 «v3» հանդիպման ծրագիր.",
        "arrivals_header": "🗓️ Ժամանման օրակարգ.",
        "cancel_msg": "Չեղարկվեց։ Ուղարկեք /start՝ նորից փորձելու համար։",
        "myid_msg": "Ձեր Telegram ID-ն է՝ {id}\nՈւղարկեք այս համարը կազմակերպչին։",
        "sos_ask_location": "🆘 Ուղարկեք ձեր տեղադրությունը (📎 → Location), և ես անմիջապես կտեղեկացնեմ բոլորին։",
        "sos_no_location": "Սա տեղադրություն չէ 🙂 Սեղմեք 📎 → Location և ուղարկեք։",
        "sos_confirmed": "✅ Ազդանշանն ուղարկվեց բոլոր մասնակիցներին՝ ձեր տեղադրության հետ միասին։",
        "sos_alert": "🆘 ՈՒՇԱԴՐՈՒԹՅՈՒՆ! {name}-ին օգնություն է անհրաժեշտ!",
        "daily_countdown": "📅 Հանդիպմանը մնացել է {n} օր.",
        "daily_today": "🎉 Այսօր հենց այն օրն է!",
        "daily_done": "✅ Հանդիպումն արդեն կայացել է։",
        "live_location_reminder": "📍 Մի մոռացեք թարմացնել live-տեղադրությունը խմբային չաթում ևս 8 ժամով, որպեսզի բոլորս տեսնենք միմյանց քարտեզի վրա!",
        "info_header": "ℹ️ Կարևոր տեղեկություն",
        "info_sergey_address": "🏠 Սերգեյի հասցեն և կոնտակտը՝ {address}\n📞 {contact}",
        "info_taxi": "🚕 Տաքսի ծառայություններ՝ {services}",
        "info_phrase_intro": "🗣️ Ստորև ձայնագրությունը՝ իսպաներեն արտահայտություն տաքսու վարորդի համար։ Սա տան հասցեն է, խնդրեք վարորդին միացնել ձայնը և լսել.",
        "friend_vs_brother_joke": "😄 Ինչո՞վ է ընկերը տարբերվում եղբորից։\nԵղբորը տալիս է ճակատագիրը։ Ընկերոջն ընտրում են ինքնուրույն։",
        "group_welcome": "👋 Բարև բոլորին! Ես անձնական օգնականն եմ «v3» հանդիպման համար։ Որպեսզի կարողանամ օգնել չվերթի, ամսաթվերի և մնացած ամեն ինչի հետ՝ գրեք ինձ անձամբ @{bot_username} և ուղարկեք /start 🙂",
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
        await update.message.reply_text(t(user.id, "commands_list"))

    await update.message.reply_text(t(user.id, "arrival_ask_flight"))
    return ARRIVAL_FLIGHT


async def arrival_get_flight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["flight_number"] = update.message.text.strip().upper()

    date_labels = [t(user.id, "date_label_template", d=d) for d in AVAILABLE_FLIGHT_DAYS]
    keyboard = ReplyKeyboardMarkup(
        [date_labels],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(t(user.id, "arrival_ask_date_choice"), reply_markup=keyboard)
    return ARRIVAL_DATE_CHOICE


async def arrival_get_date_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    date_labels = {t(user.id, "date_label_template", d=d): d for d in AVAILABLE_FLIGHT_DAYS}
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
        joke = random.choice(JOKES)
        await update.message.reply_text(
            f"{t(user.id, 'flight_lookup_failed')}\n\n😄 {joke}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ARRIVAL_FLIGHT


async def arrival_friend_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    if choice_text == t(user.id, "btn_friend_yes"):
        await update.message.reply_text(t(user.id, "friend_congrats"))
        await update.message.reply_text(t(user.id, "arrival_ask_flight_again"), reply_markup=ReplyKeyboardRemove())
        return ARRIVAL_FLIGHT
    elif choice_text == t(user.id, "btn_friend_no"):
        await update.message.reply_text(t(user.id, "friend_reject"), reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    else:
        await update.message.reply_text(t(user.id, "invalid_friend_check"))
        return ARRIVAL_FRIEND_CHECK


async def arrival_confirm_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    choice_text = update.message.text.strip()

    if choice_text == t(user.id, "btn_confirm_yes"):
        return await save_arrival(update, context, context.user_data["looked_up_time"])
    elif choice_text == t(user.id, "btn_confirm_no"):
        keyboard = ReplyKeyboardMarkup(
            [[t(user.id, "btn_friend_yes"), t(user.id, "btn_friend_no")]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
        await update.message.reply_text(t(user.id, "friend_check"), reply_markup=keyboard)
        return ARRIVAL_FRIEND_CHECK
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

    if user.id in PERSONAL_ARRIVAL_JOKES:
        await update.message.reply_text(PERSONAL_ARRIVAL_JOKES[user.id])

    if set(arrivals.keys()) == set(PARTICIPANT_IDS):
        try:
            await context.bot.send_message(
                chat_id=ORGANIZER_ID,
                text="✅ Усі учасники пройшли реєстрацію рейсу!",
            )
        except Exception as e:
            logging.warning(f"Could not notify organizer: {e}")

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
        for day in ("6", "7", "8", "9"):
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


async def send_tts_voice(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, lang: str):
    """Generate text as speech (gTTS) and send it as a voice message."""
    try:
        tts = gTTS(text=text, lang=lang)
        audio_buffer = io.BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        await context.bot.send_voice(chat_id=chat_id, voice=audio_buffer)
    except Exception as e:
        logging.warning(f"Could not generate/send TTS for {chat_id}: {e}")


async def send_combined_tts_voice(context: ContextTypes.DEFAULT_TYPE, chat_id: int, segments):
    """Generate several (text, lang) segments and send them as ONE combined
    voice message (segments play back-to-back in a single audio file)."""
    try:
        combined = io.BytesIO()
        for text, lang in segments:
            tts = gTTS(text=text, lang=lang)
            tts.write_to_fp(combined)
        combined.seek(0)
        await context.bot.send_voice(chat_id=chat_id, voice=combined)
    except Exception as e:
        logging.warning(f"Could not generate/send combined TTS for {chat_id}: {e}")


async def send_taxi_phrase_voice(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Generate the Spanish taxi phrase as speech and send it as a voice message only."""
    await send_tts_voice(context, chat_id, TAXI_PHRASE_ES, "es")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_user.id, "cancel_msg"))
    return ConversationHandler.END


SOS_LOCATION = 100  # separate state, well clear of other conversation states


async def sos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(t(user.id, "sos_ask_location"))
    return SOS_LOCATION


async def sos_receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not update.message.location:
        await update.message.reply_text(t(user.id, "sos_no_location"))
        return SOS_LOCATION

    loc = update.message.location

    recipients = [GROUP_CHAT_ID] if GROUP_CHAT_ID else [uid for uid in PARTICIPANT_IDS if uid != user.id]

    for chat_id in recipients:
        try:
            await context.bot.send_message(chat_id=chat_id, text=t(chat_id, "sos_alert", name=user.first_name))
            await context.bot.send_location(chat_id=chat_id, latitude=loc.latitude, longitude=loc.longitude)
        except Exception as e:
            logging.warning(f"Could not send SOS alert to {chat_id}: {e}")

    await update.message.reply_text(t(user.id, "sos_confirmed"))
    return ConversationHandler.END


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(t(user.id, "myid_msg", id=user.id))


async def testall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Preview every scheduled/automated message the bot sends, all at once,
    to whoever ran the command - restricted to the organizer only."""
    user = update.effective_user
    if user.id != ORGANIZER_ID:
        return

    chat_id = update.effective_chat.id

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: щоденна розсилка")
    joke = random.choice(JOKES)
    await context.bot.send_message(chat_id=chat_id, text=f"📅 До зустрічі залишилось N дн.\n\n😄 {joke}")

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: 4 серпня (індивідуально)")
    await send_combined_tts_voice(context, chat_id, [(AUG4_VOICE_TEXT_ES, "es"), (AUG4_VOICE_TEXT_UK, "uk")])

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: 5 серпня (група)")
    await send_combined_tts_voice(context, chat_id, [(AUG5_VOICE_TEXT_ES, "es"), (AUG5_VOICE_TEXT_UK, "uk")])

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: 6 серпня (група)")
    await send_combined_tts_voice(context, chat_id, [(AUG6_VOICE_TEXT_PL, "pl"), (AUG6_TEXT_UK, "uk")])

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: фінальна агенда (після реєстрації всіх)")
    program = PROGRAM.get("uk", PROGRAM["uk"])
    agenda_lines = [
        t(user.id, "arrivals_header"), "",
        "• Приклад: LH1234, 6 серпня — 14:30",
        "", t(user.id, "agenda_header"), "",
    ]
    for day in ("6", "7", "8", "9"):
        agenda_lines.append(program[day])
    agenda_lines += [
        "", t(user.id, "info_header"),
        t(user.id, "info_sergey_address", address=SERGEY_ADDRESS, contact=SERGEY_CONTACT),
        t(user.id, "info_taxi", services=", ".join(TAXI_SERVICES)),
        "", t(user.id, "info_phrase_intro"),
    ]
    await context.bot.send_message(chat_id=chat_id, text="\n".join(agenda_lines))
    await send_taxi_phrase_voice(context, chat_id)

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: нагадування live-локації")
    await context.bot.send_message(chat_id=chat_id, text=t(user.id, "live_location_reminder"))

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: жарт 'друг vs брат' (23:30, 7.08)")
    await context.bot.send_message(chat_id=chat_id, text=t(user.id, "friend_vs_brother_joke"))

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: /info")
    await info(update, context)

    await context.bot.send_message(chat_id=chat_id, text="🧪 ТЕСТ: групове вітання")
    combined_welcome = "\n\n".join(
        TEXTS[lang]["group_welcome"].format(bot_username=context.bot.username) for lang in ("uk", "pl", "hy")
    )
    await context.bot.send_message(chat_id=chat_id, text=combined_welcome)

    await context.bot.send_message(chat_id=chat_id, text="✅ ТЕСТ завершено.")


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


def random_time_in_window(start: datetime.time, end: datetime.time) -> datetime.time:
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    picked = random.randint(start_minutes, end_minutes)
    return datetime.time(hour=picked // 60, minute=picked % 60)


async def daily_broadcast(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.date.today()
    days_left = (MEETING_DATE - today).days
    send_text = DAILY_JOKE_RANGE_START <= today <= DAILY_JOKE_RANGE_END

    for user_id in PARTICIPANT_IDS:
        if send_text:
            if days_left > 0:
                countdown = t(user_id, "daily_countdown", n=days_left)
            elif days_left == 0:
                countdown = t(user_id, "daily_today")
            else:
                countdown = t(user_id, "daily_done")

            joke = random.choice(JOKES)
            message = f"{countdown}\n\n😄 {joke}"

            try:
                await context.bot.send_message(chat_id=user_id, text=message)
            except Exception as e:
                logging.warning(f"Could not message {user_id}: {e}")

    # Keep scheduling until the gathering starts; no need to run forever after.
    if today < MEETING_DATE:
        schedule_next_daily_broadcast(context.job_queue)


def schedule_next_daily_broadcast(job_queue):
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    next_time = random_time_in_window(DAILY_BROADCAST_WINDOW_START, DAILY_BROADCAST_WINDOW_END)
    when = datetime.datetime.combine(tomorrow, next_time)
    job_queue.run_once(daily_broadcast, when=when)


async def live_location_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Nudge everyone to re-share their live location (a single Telegram
    share maxes out at 8h). Called only at the exact scheduled times."""
    for user_id in PARTICIPANT_IDS:
        try:
            await context.bot.send_message(chat_id=user_id, text=t(user_id, "live_location_reminder"))
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


async def aug4_individual_voice_job(context: ContextTypes.DEFAULT_TYPE):
    """4 August - individual voice reminder to each person, Spanish + Ukrainian in one file."""
    for user_id in PARTICIPANT_IDS:
        await send_combined_tts_voice(
            context, user_id, [(AUG4_VOICE_TEXT_ES, "es"), (AUG4_VOICE_TEXT_UK, "uk")]
        )


async def aug5_group_voice_job(context: ContextTypes.DEFAULT_TYPE):
    """5 August - group voice message, Spanish + Ukrainian in one file (falls
    back to individual DMs if GROUP_CHAT_ID isn't set)."""
    targets = [GROUP_CHAT_ID] if GROUP_CHAT_ID else PARTICIPANT_IDS
    for chat_id in targets:
        await send_combined_tts_voice(
            context, chat_id, [(AUG5_VOICE_TEXT_ES, "es"), (AUG5_VOICE_TEXT_UK, "uk")]
        )


async def aug6_group_job(context: ContextTypes.DEFAULT_TYPE):
    """6 August - group voice message: Polish + Ukrainian in one file (falls
    back to individual DMs if GROUP_CHAT_ID isn't set)."""
    targets = [GROUP_CHAT_ID] if GROUP_CHAT_ID else PARTICIPANT_IDS
    for chat_id in targets:
        await send_combined_tts_voice(context, chat_id, [(AUG6_VOICE_TEXT_PL, "pl"), (AUG6_TEXT_UK, "uk")])


async def friend_vs_brother_joke_job(context: ContextTypes.DEFAULT_TYPE):
    """One-off scheduled joke. Goes to the group chat (all languages combined)
    if GROUP_CHAT_ID is set, otherwise to everyone individually in their own language."""
    if GROUP_CHAT_ID:
        combined = "\n\n".join(TEXTS[lang]["friend_vs_brother_joke"] for lang in ("uk", "pl", "hy"))
        try:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=combined)
        except Exception as e:
            logging.warning(f"Could not send joke to group chat {GROUP_CHAT_ID}: {e}")
        return

    for user_id in PARTICIPANT_IDS:
        try:
            await context.bot.send_message(chat_id=user_id, text=t(user_id, "friend_vs_brother_joke"))
        except Exception as e:
            logging.warning(f"Could not message {user_id}: {e}")


async def group_welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fires when the bot (or anyone) is added to a group. If it's the bot
    itself, greet the group and log the chat ID so it can be set as
    GROUP_CHAT_ID for the SOS alerts and other group-wide messages."""
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            chat_id = update.effective_chat.id
            logging.warning(f"Bot was added to group chat. GROUP_CHAT_ID={chat_id}")

            combined = "\n\n".join(
                TEXTS[lang]["group_welcome"].format(bot_username=context.bot.username)
                for lang in ("uk", "pl", "hy")
            )
            await context.bot.send_message(chat_id=chat_id, text=combined)

            combined_commands = "\n\n".join(TEXTS[lang]["commands_list"] for lang in ("uk", "pl", "hy"))
            await context.bot.send_message(chat_id=chat_id, text=combined_commands)



def main():
    # Make sure the folder for the persistence file exists (Railway Volume
    # mounted at /data, or wherever PERSISTENCE_PATH points).
    os.makedirs(os.path.dirname(PERSISTENCE_PATH), exist_ok=True)
    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)

    app = ApplicationBuilder().token(BOT_TOKEN).persistence(persistence).build()
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("testall", testall))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, group_welcome_handler))

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

    sos_conv = ConversationHandler(
        entry_points=[CommandHandler("sos", sos_start)],
        states={
            SOS_LOCATION: [MessageHandler(filters.LOCATION, sos_receive_location)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="sos_conv",
        persistent=True,
    )
    app.add_handler(sos_conv)

    # Daily broadcast: countdown + joke, at a different random time each day
    # Start from tomorrow, not launch day - avoids sending a broadcast on
    # the same day the bot goes live.
    first_time = random_time_in_window(DAILY_BROADCAST_WINDOW_START, DAILY_BROADCAST_WINDOW_END)
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    first_when = datetime.datetime.combine(tomorrow, first_time)
    app.job_queue.run_once(daily_broadcast, when=first_when)

    # Live-location re-share reminders at the exact scheduled times.
    for day, times in LIVE_LOCATION_REMINDER_SCHEDULE.items():
        for reminder_time in times:
            when = datetime.datetime.combine(datetime.date(MEETING_DATE.year, 8, day), reminder_time)
            app.job_queue.run_once(live_location_reminder_job, when=when)

    # One-off "friend vs brother" joke, 7 August at 23:30.
    joke_when = datetime.datetime.combine(datetime.date(MEETING_DATE.year, 8, 7), datetime.time(hour=23, minute=30))
    app.job_queue.run_once(friend_vs_brother_joke_job, when=joke_when)

    # Final-stretch day-specific reminders: 4, 5, 6 August.
    app.job_queue.run_once(
        aug4_individual_voice_job,
        when=datetime.datetime.combine(datetime.date(MEETING_DATE.year, 8, 4), AUG4_REMINDER_TIME),
    )
    app.job_queue.run_once(
        aug5_group_voice_job,
        when=datetime.datetime.combine(datetime.date(MEETING_DATE.year, 8, 5), AUG5_REMINDER_TIME),
    )
    app.job_queue.run_once(
        aug6_group_job,
        when=datetime.datetime.combine(datetime.date(MEETING_DATE.year, 8, 6), AUG6_REMINDER_TIME),
    )

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
