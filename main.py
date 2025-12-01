import logging
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    InlineQueryHandler
)
from config import BOT_TOKEN
import db_manager
from scheduler import start_scheduler
import uuid
import json
import redis
from config import BOT_TOKEN, REDIS_HOST, REDIS_PORT
from handlers import user_handlers, admin_handlers, auth_handlers

# Настройка логирования для отладки
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
# Уменьшаем "шум" от библиотеки httpx, которую использует python-telegram-bot
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

BTN_START_SHIFT = "🟢 Начать смену"
BTN_END_SHIFT = "🔴 Закончить смену"
BTN_REPORT = "📊 Отчет"
BTN_ADMIN = "🔐 Админка"

async def post_init(application: Application):
    """Инициализация при запуске."""
    try:
        redis_op_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
        redis_op_client.ping()
        application.bot_data['redis_op_client'] = redis_op_client
        logger.info("Redis connection (db 0) established successfully.")
    except redis.exceptions.ConnectionError as e:
        logger.error(f"FATAL: Could not connect to Redis: {e}")
        application.bot_data['redis_op_client'] = None

    await db_manager.init_pool()
    await db_manager.reset_all_topic_ids()
    start_scheduler(application)


async def post_shutdown(application: Application):
    """Очистка при остановке."""
    await db_manager.close_pool()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает команду /start.
    Проверяет роль пользователя и выдает соответствующую клавиатуру.
    """
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    
    # Базовые кнопки для всех
    keyboard = [
        [KeyboardButton(BTN_START_SHIFT), KeyboardButton(BTN_END_SHIFT)],
        [KeyboardButton(BTN_REPORT)]
    ]

    # Добавляем кнопку админки, если есть права
    if employee and employee.get('role', '').lower() in ['admin', 'security']:
        keyboard.append([KeyboardButton(BTN_ADMIN)])

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "Добро пожаловать в систему учета рабочего времени!\n"
        "Используйте кнопки меню для управления статусом.",
        reply_markup=reply_markup
    )

def main() -> None:
    """Основная функция для запуска бота."""
    application = Application.builder().token(BOT_TOKEN).build()

    # --- Регистрация обработчиков ---

    # 1. Админские хендлеры
    # Мы добавляем все обработчики, определенные в admin_handlers, включая ConversationHandler'ы
    application.add_handlers(admin_handlers.admin_handlers)

    # 2. Обработчик для команды /on (в виде диалога)
    # Это позволяет боту запомнить, что он ждет TOTP-код именно для входа,
    # а не для чего-то другого.
    on_handler = ConversationHandler(
        entry_points=[
            CommandHandler("on", user_handlers.clock_in),
            MessageHandler(filters.Regex(f"^{BTN_START_SHIFT}$"), user_handlers.clock_in)
        ],
        states={
            auth_handlers.AWAITING_ACTION_TOTP: [
                MessageHandler(filters.Regex(r'^\d{6}$'), auth_handlers.verify_action_totp)
            ],
            auth_handlers.VERIFY_2FA_SETUP_CODE: [
                MessageHandler(filters.Regex(r'^\d{6}$'), auth_handlers.verify_2fa_setup_code)
            ],
        },
        fallbacks=[CommandHandler('cancel', auth_handlers.cancel)],
        per_user=True,
    )
    application.add_handler(on_handler)

    # 3. Обработчик для команды /off (также в виде диалога)
    off_handler = ConversationHandler(
        entry_points=[
            CommandHandler("off", user_handlers.clock_out_menu),
            MessageHandler(filters.Regex(f"^{BTN_END_SHIFT}$"), user_handlers.clock_out_menu)
        ],
        states={
            'AWAITING_REASON': [
                CallbackQueryHandler(user_handlers.clock_out_callback, pattern='^off_reason_'),
                CallbackQueryHandler(user_handlers.request_deal_approval_from_sb, pattern='^request_deal_approval_')
            ],
            auth_handlers.AWAITING_ACTION_TOTP: [
                MessageHandler(filters.Regex(r'^\d{6}$'), auth_handlers.verify_action_totp)
            ],
            auth_handlers.VERIFY_2FA_SETUP_CODE: [
                MessageHandler(filters.Regex(r'^\d{6}$'), auth_handlers.verify_2fa_setup_code)
            ],
        },
        fallbacks=[CommandHandler('cancel', auth_handlers.cancel)],
        per_user=True,
    )
    application.add_handler(off_handler)

    # 4. Простая команда /start
    application.add_handler(CommandHandler("start", start))

    # Обработчик кнопки "📊 Отчет" и команды /report
    application.add_handler(CommandHandler("report", user_handlers.generate_report_placeholder))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_REPORT}$"), user_handlers.generate_report_placeholder))

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()