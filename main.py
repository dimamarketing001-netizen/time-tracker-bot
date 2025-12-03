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
from utils import get_main_keyboard 

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
BTN_REPORT = "📅 Мой график"
BTN_ADMIN = "🔐 Админка"

async def post_init(application: Application):
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
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    role = employee.get('role', '').lower() if employee else 'unknown'
    
    # Используем функцию из utils для получения клавиатуры
    reply_markup = get_main_keyboard(role)

    await update.message.reply_text(
        "Добро пожаловать в систему учета рабочего времени!\n"
        "Используйте кнопки меню для управления статусом.",
        reply_markup=reply_markup
    )
    
def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    # 1. Админские хендлеры
    application.add_handlers(admin_handlers.admin_handlers)

    # 2. Вход на смену
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
        allow_reentry=True # ВАЖНО: Позволяет перезапустить команду, если завис
    )
    application.add_handler(on_handler)

    # 3. Выход со смены
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
            user_handlers.GET_EARLY_LEAVE_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_handlers.get_early_leave_reason)],
            user_handlers.GET_EARLY_LEAVE_PERIOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_handlers.get_early_leave_period)],
            auth_handlers.AWAITING_ACTION_TOTP: [
                MessageHandler(filters.Regex(r'^\d{6}$'), auth_handlers.verify_action_totp)
            ],
            auth_handlers.VERIFY_2FA_SETUP_CODE: [
                MessageHandler(filters.Regex(r'^\d{6}$'), auth_handlers.verify_2fa_setup_code)
            ],
        },
        fallbacks=[CommandHandler('cancel', auth_handlers.cancel)],
        per_user=True,
        allow_reentry=True # ВАЖНО: Позволяет перезапустить команду, если завис
    )
    application.add_handler(off_handler)

    # 4. Обработчик "Мой график"
    my_schedule_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{BTN_REPORT}$"), user_handlers.my_schedule_start),
            CommandHandler("report", user_handlers.my_schedule_start) 
        ],
        states={
            user_handlers.USER_REPORT_SELECT_PERIOD: [
                CallbackQueryHandler(user_handlers.my_schedule_generate, pattern='^my_period_'),
                CallbackQueryHandler(user_handlers.my_schedule_close, pattern='^my_report_close$')
            ],
            user_handlers.USER_REPORT_SHOW: [
                CallbackQueryHandler(user_handlers.my_schedule_back, pattern='^back_to_my_period_select$'),
                CallbackQueryHandler(user_handlers.my_schedule_close, pattern='^my_report_close$')
            ]
        },
        fallbacks=[
            CommandHandler('cancel', user_handlers.my_schedule_close),
            MessageHandler(filters.Regex(f"^({BTN_START_SHIFT}|{BTN_END_SHIFT}|{BTN_ADMIN})$"), user_handlers.my_schedule_close)
        ],
        per_user=True,
        allow_reentry=True # Позволяет открыть отчет заново, если он завис
    )
    
    application.add_handler(my_schedule_handler)

    # 5. Прочие команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("report", user_handlers.generate_report_placeholder))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_REPORT}$"), user_handlers.generate_report_placeholder))

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
