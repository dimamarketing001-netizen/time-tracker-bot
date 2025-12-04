import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
SECURITY_CHAT_ID = int(os.getenv("SECURITY_CHAT_ID"))

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_OPERATORS_ONLINE_SET = "operators_online"
REDIS_OPERATOR_TASK_PREFIX = "operator_task:"

BREAK_LIMIT = 8
LUNCH_LIMIT = 1
BREAK_DURATION_MIN = 10
LUNCH_DURATION_MIN = 60
LATENESS_GRACE_PERIOD_MIN = 5

DEFAULT_TIMEZONE = "Europe/Moscow"

CITY_TIMEZONES = {
  "Калининград": "Europe/Kaliningrad",
  
  "Москва": "Europe/Moscow",
  "Санкт-Петербург": "Europe/Moscow",
  "Сочи": "Europe/Moscow",
  "Краснодар": "Europe/Moscow",
  "Казань": "Europe/Moscow",
  "Нижний Новгород": "Europe/Moscow",
  "Ростов-на-Дону": "Europe/Moscow",
  "Воронеж": "Europe/Moscow",
  "Волгоград": "Europe/Moscow",
  "Ярославль": "Europe/Moscow",
  "Грозный": "Europe/Moscow",
  "Махачкала": "Europe/Moscow",
  "Севастополь": "Europe/Moscow",
  "Симферополь": "Europe/Moscow",
  "Иваново": "Europe/Moscow",
  "Кострома": "Europe/Moscow",
  "Рязань": "Europe/Moscow",
  "Тверь": "Europe/Moscow",
  "Тула": "Europe/Moscow",
  
  "Самара": "Europe/Samara",
  "Тольятти": "Europe/Samara",
  "Саратов": "Europe/Saratov",
  "Ижевск": "Europe/Samara",
  "Ульяновск": "Europe/Ulyanovsk",
  "Астрахань": "Europe/Astrakhan",
  
  "Екатеринбург": "Asia/Yekaterinburg",
  "Нижний Тагил": "Asia/Yekaterinburg",
  "Челябинск": "Asia/Yekaterinburg",
  "Тюмень": "Asia/Yekaterinburg",
  "Уфа": "Asia/Yekaterinburg",
  "Пермь": "Asia/Yekaterinburg",
  "Сургут": "Asia/Yekaterinburg",
  "Курган": "Asia/Yekaterinburg",
  "Магнитогорск": "Asia/Yekaterinburg",
  "Оренбург": "Asia/Yekaterinburg",
  
  "Омск": "Asia/Omsk",
  
  "Новосибирск": "Asia/Novosibirsk",
  "Барнаул": "Asia/Barnaul",
  "Томск": "Asia/Tomsk",
  
  "Красноярск": "Asia/Krasnoyarsk",
  "Кемерово": "Asia/Novokuznetsk",
  "Новокузнецк": "Asia/Novokuznetsk",
  
  "Иркутск": "Asia/Irkutsk",
  "Улан-Удэ": "Asia/Irkutsk",
  
  "Якутск": "Asia/Yakutsk",
  "Чита": "Asia/Chita",
  "Благовещенск": "Asia/Yakutsk",
  
  "Владивосток": "Asia/Vladivostok",
  "Хабаровск": "Asia/Vladivostok",
  
  "Магадан": "Asia/Magadan",
  "Южно-Сахалинск": "Asia/Sakhalin",
  
  "Петропавловск-Камчатский": "Asia/Kamchatka"
}