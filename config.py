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