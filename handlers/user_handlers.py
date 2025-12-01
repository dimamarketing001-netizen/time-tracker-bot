import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from .auth_handlers import VERIFY_2FA_SETUP_CODE, AWAITING_ACTION_TOTP, start_2fa_setup
import db_manager, config
import json
from config import REDIS_OPERATORS_ONLINE_SET, REDIS_OPERATOR_TASK_PREFIX

logger = logging.getLogger(__name__)

def format_deal_info(deal: dict) -> str:
    """Форматирует информацию о сделке для вывода пользователю с экранированием для MarkdownV2."""
    
    # --- ФИНАЛЬНОЕ ИСПРАВЛЕНИЕ: Универсальная функция для экранирования ---
    def escape_v2(text) -> str:
        """Безопасно преобразует в строку и экранирует все известные спецсимволы MarkdownV2."""
        text = str(text)
        # Список символов для экранирования
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        for char in escape_chars:
            text = text.replace(char, f'\\{char}')
        return text

    amount_raw = deal.get('amount_to_get') if deal.get('amount_to_get') is not None else deal.get('amount_to_give', 0)
    
    # Применяем универсальную функцию ко всем полям, которые могут содержать спецсимволы
    amount_escaped = escape_v2(amount_raw)
    currency_escaped = escape_v2(deal.get('currency_to_get') or deal.get('currency_to_give', 'N/A'))
    meeting_time_escaped = escape_v2(deal['datetime_meeting'].strftime('%H:%M %d.%m.%Y'))
    deal_id_escaped = escape_v2(deal.get('deals_id', 'N/A'))
    direction_escaped = escape_v2(deal.get('direction', 'N/A'))
    action_escaped = escape_v2(deal.get('action', 'N/A'))
    status_escaped = escape_v2(deal.get('status', 'N/A'))

    return (
        f"  • ID: `{deal_id_escaped}`\n"
        f"    *Действие:* {direction_escaped}, {action_escaped}\n"
        f"    *Сумма:* {amount_escaped} {currency_escaped}\n"
        f"    *Статус:* {status_escaped}\n"
        f"    *Время:* {meeting_time_escaped}"
    )

# --- Команда /on ---
async def clock_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    if not employee:
        await update.message.reply_text("Ваш профиль не найден в системе.")
        return ConversationHandler.END

    if employee.get('position', '').strip().lower() == 'оператор':
        return await operator_clock_in(update, context)
    
    if not employee['totp_secret']:
        context.user_data['original_callback'] = clock_in
        context.user_data['original_update'] = update
        return await start_2fa_setup(update, context)
    if employee['status'] == 'online':
        await update.message.reply_text("Вы уже на линии.")
        return ConversationHandler.END
    if not await db_manager.has_clocked_in_today(employee['id']):
        context.user_data['pending_action'] = {'type': 'clock_in'}
        await update.message.reply_text("Это ваш первый вход сегодня. Пожалуйста, введите код 2FA для подтверждения.")
        return AWAITING_ACTION_TOTP
    await db_manager.update_employee_status(employee['id'], 'online')
    await db_manager.log_time_event(employee['id'], 'clock_in')
    await update.message.reply_text("✅ Вы снова на линии!")
    return ConversationHandler.END


# --- Команда /off ---
async def clock_out_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    if not employee:
        await update.message.reply_text("Ваш профиль не найден в системе.")
        return ConversationHandler.END

    if employee.get('position', '').strip().lower() == 'оператор':
        # Для оператора нет меню, сразу пытаемся вывести с линии
        return await operator_clock_out(update, context)

    if not employee['totp_secret']:
        context.user_data['original_callback'] = clock_out_menu
        context.user_data['original_update'] = update
        return await start_2fa_setup(update, context)
    if employee['status'] == 'offline':
        await update.message.reply_text("Вы не на линии.")
        return ConversationHandler.END
        
    breaks_taken = await db_manager.get_today_event_count(employee['id'], 'Перерыв')
    lunches_taken = await db_manager.get_today_event_count(employee['id'], 'Обед')
    breaks_left = max(0, config.BREAK_LIMIT - breaks_taken)
    lunches_left = max(0, config.LUNCH_LIMIT - lunches_taken)
    
    keyboard = [
        [InlineKeyboardButton(f"Перерыв (Осталось: {breaks_left})", callback_data='off_reason_break')],
        [InlineKeyboardButton(f"Обед (Осталось: {lunches_left})", callback_data='off_reason_lunch')],
        [InlineKeyboardButton("Инкассация", callback_data='off_reason_collection')],
        [InlineKeyboardButton("Завершение дня", callback_data='off_reason_endday')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите причину выхода из линии:", reply_markup=reply_markup)
    return 'AWAITING_REASON'

async def clock_out_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает первоначальный выбор причины выхода из линии."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    
    reason_map = {
        'off_reason_break': ('on_break', 'Перерыв', config.BREAK_LIMIT, 15),
        'off_reason_lunch': ('on_lunch', 'Обед', config.LUNCH_LIMIT, 70),
        'off_reason_collection': ('on_collection', 'Инкассация', float('inf'), 80),
        'off_reason_endday': ('offline', 'Завершение дня', float('inf'), 1440),
    }
    
    new_status, reason, limit, time_window = reason_map[query.data]

    # 1. Проверка лимитов (перерывы, обеды)
    if limit != float('inf'):
        count = await db_manager.get_today_event_count(employee['id'], reason)
        if count >= limit:
            await query.edit_message_text(f"Вы уже использовали все попытки для '{reason}' на сегодня.")
            return ConversationHandler.END

    # 2. Проверка сделок для Кассира
    if employee.get('position', '').strip().lower() == 'кассир':
        conflicting_deals = await db_manager.check_conflicting_deals(employee['id'], time_window)
        if conflicting_deals:
            deal_infos = "\n\n".join([format_deal_info(d) for d in conflicting_deals])
            message = (
                f"❌ *Вы не можете уйти на '{reason}'*\n\n"
                f"Обнаружены следующие активные сделки:\n\n"
                f"{deal_infos}\n\n"
                "Вы можете запросить согласование у Службы Безопасности\\."
            )
            # approve_deal_{id}_{reason_key}
            callback_data = f"request_deal_approval_{employee['id']}_{query.data.split('_')[-1]}"
            keyboard = [[InlineKeyboardButton("Согласовать с СБ", callback_data=callback_data)]]
            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2')
            return 'AWAITING_REASON'

    # 3. Запрос на согласование инкассации
    # --- ИСПРАВЛЕНИЕ: Теперь создаем отдельную тему ---
    if reason == 'Инкассация':
        await query.edit_message_text("Для выхода на инкассацию требуется подтверждение от СБ. Запрос отправлен.")
        
        # Создаем новую тему в чате СБ
        topic_name = f"Согласование Инкассации: {employee['full_name']} {datetime.now().strftime('%d.%m %H:%M')}"
        topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
        
        # Отправляем запрос в созданную тему
        keyboard = [[
            InlineKeyboardButton("✅ Согласовать", callback_data=f"approve_sb_inkas_{employee['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_sb_inkas_{employee['id']}")
        ]]
        await context.bot.send_message(
            chat_id=config.SECURITY_CHAT_ID,
            message_thread_id=topic.message_thread_id,
            text=f"Требуется согласование выхода на инкассацию.\n\n*Сотрудник:* {employee['full_name']}\n*Должность:* {employee['position']}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return ConversationHandler.END # Завершаем диалог для кассира, он ждет ответа

    # 4. Если все проверки пройдены - запрашиваем 2FA у сотрудника
    context.user_data['pending_action'] = {'type': 'clock_out', 'status': new_status, 'reason': reason}
    await query.edit_message_text("Для подтверждения действия введите 6-значный код из Authenticator.")
    return AWAITING_ACTION_TOTP


async def request_deal_approval_from_sb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает нажатие кнопки 'Согласовать с СБ' при конфликте сделок."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    employee_id = int(parts[3])
    original_reason_key = parts[4] # 'break', 'collection' etc.
    
    employee = await db_manager.get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Ошибка: сотрудник не найден.")
        return ConversationHandler.END
        
    await query.edit_message_text("Запрос на согласование из-за конфликта сделок отправлен в СБ.")
        
    topic_name = f"Сделка: Согласование ухода {employee['full_name']} {datetime.now().strftime('%d.%m %H:%M')}"
    topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
    
    keyboard = [[
        InlineKeyboardButton("✅ Согласовать", callback_data=f"approve_sb_deal_{employee_id}_{original_reason_key}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_sb_deal_{employee_id}")
    ]]
    await context.bot.send_message(
        chat_id=config.SECURITY_CHAT_ID,
        message_thread_id=topic.message_thread_id,
        text=f"Требуется согласование ухода сотрудника из-за конфликта сделок.\n\n"
             f"*Сотрудник:* {employee['full_name']}\n"
             f"*Причина ухода:* {original_reason_key.capitalize()}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def generate_report_placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Временная заглушка для команды /report."""
    # Проверяем, что команду вызывает админ или СБ
    employee = await db_manager.get_employee_by_telegram_id(update.effective_user.id)

    if not employee or employee['role'].lower() not in ['security', 'admin']:
        await update.message.reply_text(f"У вас нет прав для выполнения этого действия. Роль:{employee['role'].lower()}")
        return 

    await update.message.reply_text(
        "Функция генерации отчетов находится в разработке.\n\n"
        "В будущем здесь можно будет выбрать сотрудника и период для получения детального отчета по отработанному времени."
    )

async def operator_clock_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает /on для Оператора с использованием Redis."""
    user_id = update.effective_user.id
    redis_client = context.bot_data.get('redis_op_client')

    if not redis_client:
        await update.message.reply_text("❌ Ошибка: Сервис Redis недоступен. Не удалось выйти на линию.")
        return ConversationHandler.END

    try:
        # SADD возвращает 1, если элемент был добавлен, и 0, если он уже был в множестве.
        if redis_client.sadd(REDIS_OPERATORS_ONLINE_SET, user_id):
            await update.message.reply_text("✅ Вы успешно вышли на линию. Ожидайте задачи.")
        else:
            await update.message.reply_text("ℹ️ Вы уже находитесь на линии.")
    except Exception as e:
        logger.error(f"Redis error in operator_clock_in for user {user_id}: {e}")
        await update.message.reply_text("❌ Ошибка Redis. Не удалось выйти на линию.")
        
    return ConversationHandler.END


async def operator_clock_out(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает /off для Оператора с использованием Redis."""
    user_id = update.effective_user.id
    redis_client = context.bot_data.get('redis_op_client')

    if not redis_client:
        await update.message.reply_text("❌ Ошибка: Сервис Redis недоступен. Не удалось уйти с линии.")
        return ConversationHandler.END

    try:
        # Проверяем, есть ли у оператора активная задача
        task_key = f"{REDIS_OPERATOR_TASK_PREFIX}{user_id}"
        task_info_json = redis_client.get(task_key)

        if task_info_json:
            try:
                task_info = json.loads(task_info_json)
                status = task_info.get('status')
                deal_id = task_info.get('deal_id')

                if status == 'paused':
                    message_text = f"🚫 Вы не можете уйти с линии. Ваша задача #{deal_id} находится на паузе. Сначала возобновите и завершите ее."
                else:
                    message_text = f"🚫 Вы не можете уйти с линии, у вас активная задача #{deal_id}."
                
                await update.message.reply_text(message_text)
                return ConversationHandler.END
            except (json.JSONDecodeError, TypeError):
                await update.message.reply_text("🚫 Не удалось проверить ваш статус из-за ошибки данных в задаче. Завершите задачу и повторите.")
                return ConversationHandler.END
        
        # Если активной задачи нет, убираем с линии
        # SREM возвращает 1, если элемент был удален, и 0, если его не было.
        if redis_client.srem(REDIS_OPERATORS_ONLINE_SET, user_id):
            await update.message.reply_text("☑️ Вы ушли с линии.")
        else:
            await update.message.reply_text("ℹ️ Вас не было на линии.")

    except Exception as e:
        logger.error(f"Redis error in operator_clock_out for user {user_id}: {e}")
        await update.message.reply_text("❌ Ошибка Redis. Не удалось уйти с линии.")
        
    return ConversationHandler.END