import logging
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
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

async def post_init(application: Application):
    """
    Выполняется один раз после запуска бота.
    Инициализирует пулы соединений и клиенты.
    """
    try:
        redis_op_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
        redis_op_client.ping() # Проверяем соединение
        application.bot_data['redis_op_client'] = redis_op_client
        logger.info("Redis connection (db 0) established successfully.")
    except redis.exceptions.ConnectionError as e:
        logger.error(f"FATAL: Could not connect to Redis: {e}")
        # В реальном приложении здесь можно остановить запуск бота
        application.bot_data['redis_op_client'] = None

    await db_manager.init_pool()
    await db_manager.reset_all_topic_ids()
    start_scheduler(application)


async def post_shutdown(application: Application):
    """
    Выполняется при остановке бота.
    Корректно закрывает пул соединений с БД.
    """
    await db_manager.close_pool()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /start."""
    await update.message.reply_text(
        "Добро пожаловать в систему учета рабочего времени!\n"
        "Используйте /on чтобы начать рабочий день."
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
        entry_points=[CommandHandler("on", user_handlers.clock_in)],
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
        entry_points=[CommandHandler("off", user_handlers.clock_out_menu)],
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
    application.add_handler(CommandHandler("report", user_handlers.generate_report_placeholder))

    # Назначаем асинхронные функции, которые будут выполнены при запуске и остановке
    application.post_init = post_init
    application.post_shutdown = post_shutdown

    # Запуск бота
    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()