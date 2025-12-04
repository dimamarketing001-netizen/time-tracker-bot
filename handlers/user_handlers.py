import logging
from datetime import datetime,date, timedelta, time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes, ConversationHandler
from .auth_handlers import VERIFY_2FA_SETUP_CODE, AWAITING_ACTION_TOTP, start_2fa_setup
import db_manager, config
import json
from config import REDIS_OPERATORS_ONLINE_SET, REDIS_OPERATOR_TASK_PREFIX
from utils import generate_totp_qr_code, verify_totp, get_main_keyboard
import pytz
import calendar_helper 

logger = logging.getLogger(__name__)

VERIFY_2FA_SETUP_CODE, AWAITING_ACTION_TOTP = range(2)
USER_REPORT_SELECT_PERIOD, USER_REPORT_SHOW = range(2)
(
    GET_EARLY_LEAVE_REASON, 
    GET_EARLY_LEAVE_PERIOD, 
    SELECT_LEAVE_TYPE, 
    SELECT_LEAVE_DATE_START, 
    SELECT_LEAVE_DATE_END, 
    GET_LEAVE_TIME_START, 
    GET_LEAVE_TIME_END
) = range(10, 17)

WEEKDAY_NAMES_RU = {0: "ПН", 1: "ВТ", 2: "СР", 3: "ЧТ", 4: "ПТ", 5: "СБ", 6: "ВС"}

TARGET_TIMEZONE = pytz.timezone('Asia/Yekaterinburg') 

async def my_schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога просмотра своего графика."""
    # Получаем сотрудника (на всякий случай проверяем регистрацию)
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    if not employee:
        await update.message.reply_text("Ваш профиль не найден.")
        return ConversationHandler.END

    # Сохраняем ID сотрудника (себя)
    context.user_data['my_schedule_emp_id'] = employee['id']
    
    keyboard = [
        [InlineKeyboardButton("Текущая неделя", callback_data='my_period_week')],
        [InlineKeyboardButton("Текущий месяц", callback_data='my_period_month')],
        [InlineKeyboardButton("Текущий квартал", callback_data='my_period_quarter')],
        [InlineKeyboardButton("❌ Закрыть", callback_data='my_report_close')],
    ]
    
    # Отправляем сообщение с инлайн-кнопками. 
    # Основная клавиатура (внизу) остается, так как мы не делаем ReplyKeyboardRemove
    await update.message.reply_text(
        "Выберите период для просмотра вашего графика:", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return USER_REPORT_SELECT_PERIOD

async def my_schedule_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Генерирует отчет для пользователя."""
    query = update.callback_query
    await query.answer("Загружаю график...")
    
    period = query.data.split('_')[2]
    employee_id = context.user_data['my_schedule_emp_id']
    
    # ... (логика определения дат period == 'week' и т.д. остается прежней) ...
    today = date.today()
    if period == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif period == 'month':
        start_date = today.replace(day=1)
        next_month = start_date.replace(day=28) + timedelta(days=4)
        end_date = next_month - timedelta(days=next_month.day)
    elif period == 'quarter':
        current_quarter = (today.month - 1) // 3 + 1
        start_month = 3 * current_quarter - 2
        start_date = date(today.year, start_month, 1)
        end_month = start_month + 2
        next_q = date(today.year, end_month, 28) + timedelta(days=4)
        end_date = next_q - timedelta(days=next_q.day)
        
    schedule_data = await db_manager.get_employee_schedule_for_period(employee_id, start_date, end_date)
    
    header = (
        f"📅 *Мой график*\n"
        f"Период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}\n\n"
    )
    
    # Расширяем таблицу, статус может быть длинным из-за комментария
    table = "```\n"
    table += "| Дата      | День | Время/Инфо      |\n"
    table += "|-----------|------|-----------------|\n"
    
    for day in schedule_data:
        dt = day['date']
        date_str = dt.strftime('%d.%m.%y')
        weekday_str = WEEKDAY_NAMES_RU[dt.weekday()]
        
        start_t = day['start_time']
        end_t = day['end_time']
        
        # Основная строка времени
        if start_t and end_t:
            if isinstance(start_t, timedelta): start_t = (datetime.min + start_t).time()
            if isinstance(end_t, timedelta): end_t = (datetime.min + end_t).time()
            time_str = f"{start_t.strftime('%H:%M')}-{end_t.strftime('%H:%M')}"
        else:
            time_str = "-"

        status_str = day['status']
        comment = day.get('comment')

        # Если есть комментарий (например "Отсутствие 11-12"), показываем его вместо статуса "Работа" или добавляем
        info_str = time_str
        
        # Если это стандартный рабочий день, но есть коммент - выводим коммент во второй строке или вместо статуса
        # Для компактности таблицы сделаем так:
        # Если есть комментарий, пишем его. Если нет, пишем время и статус.
        
        row_content = f"{time_str}"
        if comment:
             # Если строка слишком длинная, переносим? В Markdown таблице сложно.
             # Просто заменим время на "* " если оно есть в комменте, или добавим.
             pass

        table += f"| {date_str:<9} | {weekday_str:<4} | {row_content:<15} |\n"
        
        # Если есть комментарий, добавляем его отдельной строкой в таблицу для читаемости
        if comment:
             table += f"|           |      | {comment:<15} |\n"
        elif status_str != 'Работа' and time_str == '-':
             # Если выходной/отгул
             table += f"|           |      | {status_str:<15} |\n"
        
        table += "|-----------|------|-----------------|\n"

    table += "```"
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Выбрать другой период", callback_data='back_to_my_period_select')],
        [InlineKeyboardButton("❌ Закрыть", callback_data='my_report_close')]
    ]
    
    await query.edit_message_text(
        header + table, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode='Markdown'
    )
    return USER_REPORT_SHOW

async def my_schedule_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Возврат к выбору периода."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Текущая неделя", callback_data='my_period_week')],
        [InlineKeyboardButton("Текущий месяц", callback_data='my_period_month')],
        [InlineKeyboardButton("Текущий квартал", callback_data='my_period_quarter')],
        [InlineKeyboardButton("❌ Закрыть", callback_data='my_report_close')],
    ]
    await query.edit_message_text("Выберите период для просмотра вашего графика:", reply_markup=InlineKeyboardMarkup(keyboard))
    return USER_REPORT_SELECT_PERIOD

async def my_schedule_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Закрывает отчет (удаляет сообщение)."""
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except:
        pass
    # Мы просто завершаем диалог, основные кнопки и так на месте
    return ConversationHandler.END

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
        await update.message.reply_text("Вы уже на линии.", reply_markup=get_main_keyboard(employee.get('role', 'employee')))
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
    
    # Формат: (new_status, reason, limit, time_window_for_cashier_check)
    # Если проверка кассира не нужна, ставим 0 или None
    reason_map = {
        'off_reason_break': ('on_break', 'Перерыв', config.BREAK_LIMIT, 15),
        'off_reason_lunch': ('on_lunch', 'Обед', config.LUNCH_LIMIT, 70),
        'off_reason_collection': ('on_collection', 'Инкассация', float('inf'), 80),
        'off_reason_endday': ('offline', 'Завершение дня', float('inf'), 1440),
    }
    
    # Распаковываем все 4 значения
    if query.data not in reason_map:
        await query.edit_message_text("Ошибка: Неизвестная причина.")
        return ConversationHandler.END

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
            callback_data = f"request_deal_approval_{employee['id']}_{query.data.split('_')[-1]}"
            keyboard = [[InlineKeyboardButton("Согласовать с СБ", callback_data=callback_data)]]
            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2')
            return 'AWAITING_REASON'

    # 3. Запрос на согласование инкассации
    if reason == 'Инкассация':
        await query.edit_message_text("Для выхода на инкассацию требуется подтверждение от СБ. Запрос отправлен.")
        
        topic_name = f"Согласование Инкассации: {employee['full_name']} {datetime.now().strftime('%d.%m %H:%M')}"
        topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
        
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
        return ConversationHandler.END 
    
    # --- ЛОГИКА РАННЕГО УХОДА ---
    if reason == 'Завершение дня':
        today_schedule = await db_manager.get_today_schedule(employee['id'])
        
        # Если сегодня рабочий день
        if today_schedule and today_schedule['status'] == 'Работа':
            end_time_val = today_schedule['end_time']
            
            # Получаем текущее время в правильном часовом поясе
            now = datetime.now(TARGET_TIMEZONE) 
            
            planned_end_dt = None
            
            if end_time_val:
                # Преобразуем end_time_val в time
                et = None
                if isinstance(end_time_val, str):
                    try: et = datetime.strptime(end_time_val, '%H:%M:%S').time()
                    except: 
                        try: et = datetime.strptime(end_time_val, '%H:%M').time()
                        except: pass
                elif isinstance(end_time_val, timedelta):
                    total_seconds = int(end_time_val.total_seconds())
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    et = time(hour=hours, minute=minutes)
                elif isinstance(end_time_val, time):
                     et = end_time_val

                if et:
                    # Создаем datetime с правильной датой и таймзоной
                    planned_end_dt = now.replace(hour=et.hour, minute=et.minute, second=0, microsecond=0)
            
            # Если плановое время определено и сейчас РАНЬШЕ (с запасом 5 минут)
            if planned_end_dt:
                if now < planned_end_dt - timedelta(minutes=5):
                    # Сохраняем данные строкой
                    context.user_data['early_leave_data'] = {
                        'planned_end': str(end_time_val),
                        'actual_end': now.strftime('%H:%M')
                    }
                    
                    await query.edit_message_text(
                        f"⚠️ Вы завершаете смену раньше времени (план: {end_time_val}).\n\n"
                        f"Пожалуйста, укажите **причину раннего ухода** (отправьте текстовое сообщение):",
                        parse_mode='Markdown'
                    )
                    return GET_EARLY_LEAVE_REASON

    # 4. Если все проверки пройдены - запрашиваем 2FA у сотрудника
    context.user_data['pending_action'] = {'type': 'clock_out', 'status': new_status, 'reason': reason}
    await query.edit_message_text("Для подтверждения действия введите 6-значный код из Authenticator.")

    return AWAITING_ACTION_TOTP

async def get_early_leave_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили причину. Спрашиваем тип отсутствия."""
    context.user_data['early_leave_data']['reason'] = update.message.text
    
    keyboard = [
        [InlineKeyboardButton("Сегодня до конца смены", callback_data='leave_type_today_end')],
        [InlineKeyboardButton("Выбрать другое время/дату", callback_data='leave_type_custom')],
    ]
    await update.message.reply_text(
        "Как вы планируете отсутствовать?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_LEAVE_TYPE

async def select_leave_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    
    if choice == 'leave_type_today_end':
        # Сразу формируем заявку (как раньше)
        # Период: "Сегодня c {текущее время} до конца"
        context.user_data['early_leave_data']['mode'] = 'today_end'
        return await send_early_leave_request_to_sb(update, context)
        
    else: # custom
        context.user_data['early_leave_data']['mode'] = 'custom'
        await query.edit_message_text(
            "Выберите ДАТУ начала отсутствия:",
            reply_markup=calendar_helper.create_calendar()
        )
        return SELECT_LEAVE_DATE_START

async def leave_date_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    # Обработка навигации календаря
    if not query.data.startswith('cal_day_'):
        year, month = calendar_helper.process_calendar_selection(update)
        await query.edit_message_text(text=query.message.text, reply_markup=calendar_helper.create_calendar(year, month))
        return SELECT_LEAVE_DATE_START

    selected_date = query.data.split('_')[2]
    context.user_data['early_leave_data']['date_start'] = selected_date
    
    # Спрашиваем: это один день или период?
    # Для простоты давайте сразу спросим дату конца (если один день - выберет ту же)
    await query.edit_message_text(
        f"Начало: {selected_date}\nТеперь выберите ДАТУ ОКОНЧАНИЯ (если один день — выберите ту же):",
        reply_markup=calendar_helper.create_calendar()
    )
    return SELECT_LEAVE_DATE_END

async def leave_date_end_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    # Обработка навигации календаря
    if not query.data.startswith('cal_day_'):
        year, month = calendar_helper.process_calendar_selection(update)
        await query.edit_message_text(text=query.message.text, reply_markup=calendar_helper.create_calendar(year, month))
        return SELECT_LEAVE_DATE_END

    selected_date = query.data.split('_')[2]
    context.user_data['early_leave_data']['date_end'] = selected_date
    
    # Теперь время начала отсутствия
    await query.edit_message_text(
        "Введите ВРЕМЯ НАЧАЛА отсутствия (в формате ЧЧ:ММ, например 11:00):"
    )
    return GET_LEAVE_TIME_START

async def get_leave_time_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_str = update.message.text.strip()
    
    # Простая валидация времени
    import re
    if not re.match(r'^\d{2}:\d{2}$', time_str):
        await update.message.reply_text("❌ Неверный формат времени. Введите в формате ЧЧ:ММ (например 11:00).")
        return GET_LEAVE_TIME_START
        
    context.user_data['early_leave_data']['time_start'] = time_str
    
    await update.message.reply_text("Введите ВРЕМЯ ОКОНЧАНИЯ отсутствия (например 12:00):")
    return GET_LEAVE_TIME_END

async def get_leave_time_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_str = update.message.text.strip()
    
    # Простая валидация
    import re
    if not re.match(r'^\d{2}:\d{2}$', time_str):
        await update.message.reply_text("❌ Неверный формат времени. Введите в формате ЧЧ:ММ (например 18:00).")
        return GET_LEAVE_TIME_END
        
    context.user_data['early_leave_data']['time_end'] = time_str
    
    # Все данные собраны, отправляем в СБ
    return await send_early_leave_request_to_sb(update, context)

async def send_early_leave_request_to_sb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Финальная отправка в СБ."""
    # 1. Получаем данные
    data = context.user_data['early_leave_data']
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    
    # 2. Формируем строку периода
    if data.get('mode') == 'today_end':
        period_str = f"Сегодня до конца смены (план: {data.get('planned_end', '?')})"
    else:
        period_str = f"{data.get('date_start')} {data.get('time_start')} — {data.get('date_end')} {data.get('time_end')}"
    
    # 3. Сохраняем заявку в БД
    # ВАЖНО: Мы сохраняем json, чтобы при согласовании СБ мы знали, что именно применять
    await db_manager.save_employee_request(employee['id'], 'early_leave', json.dumps(data))
    
    # 4. Уведомляем пользователя (это может быть CallbackQuery или Message)
    user_response_text = "✅ Заявка на ранний уход отправлена в СБ. Ожидайте решения (бот уведомит вас)."
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(user_response_text)
    else:
        await update.message.reply_text(user_response_text)
    
    # 5. Отправляем сообщение в чат СБ
    try:
        topic_name = f"Ранний уход: {employee['full_name']} {datetime.now().strftime('%d.%m')}"
        topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
        thread_id = topic.message_thread_id
    except Exception as e:
        logger.error(f"Error creating topic for early leave: {e}")
        thread_id = None # Если топики не работают, шлем в общий чат
    
    keyboard = [
        [InlineKeyboardButton("✅ Согласовать", callback_data=f"approve_early_{employee['id']}")],
        [InlineKeyboardButton("❌ Не согласовать", callback_data=f"reject_early_{employee['id']}")],
        [InlineKeyboardButton("✏️ Изменить время", callback_data=f"change_early_{employee['id']}")]
    ]
    
    # Хелпер для экранирования MarkdownV2 (локальный)
    def esc(text):
        return escape_markdown(str(text), version=2)

    msg_text = (
        f"⚠️ *Заявка на ранний уход*\n\n"
        f"Сотрудник: *{esc(employee['full_name'])}*\n"
        f"Должность: {esc(employee.get('position', '-'))}\n"
        f"Плановый конец: {esc(data.get('planned_end', '?'))}\n"
        f"Текущее время: {esc(data.get('actual_end', '?'))}\n\n"
        f"*Причина:* {esc(data.get('reason', '-'))}\n"
        f"*Запрашиваемый период:* {esc(period_str)}"
    )
    
    await context.bot.send_message(
        chat_id=config.SECURITY_CHAT_ID,
        message_thread_id=thread_id,
        text=msg_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='MarkdownV2'
    )
    
    # 6. Очищаем данные и завершаем диалог
    context.user_data.pop('early_leave_data', None)
    return ConversationHandler.END

async def get_early_leave_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получаем период и отправляем в СБ."""
    period_text = update.message.text
    user_data = context.user_data['early_leave_data']
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    
    # Формируем заявку для СБ
    await update.message.reply_text("Заявка на ранний уход отправлена в СБ. Ожидайте решения.")
    
    # Отправляем в СБ
    topic_name = f"Ранний уход: {employee['full_name']} {datetime.now().strftime('%d.%m')}"
    topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
    
    # Кнопки для СБ
    # approve_early_{emp_id}
    # reject_early_{emp_id}
    # change_early_{emp_id}
    keyboard = [
        [InlineKeyboardButton("✅ Согласовать", callback_data=f"approve_early_{employee['id']}")],
        [InlineKeyboardButton("❌ Не согласовать", callback_data=f"reject_early_{employee['id']}")],
        [InlineKeyboardButton("✏️ Изменить время", callback_data=f"change_early_{employee['id']}")]
    ]
    
    msg_text = (
        f"⚠️ *Заявка на ранний уход*\n\n"
        f"Сотрудник: *{escape_markdown(employee['full_name'], version=2)}*\n"
        f"Должность: {escape_markdown(employee['position'], version=2)}\n"
        f"Плановый конец: {user_data['planned_end']}\n"
        f"Текущее время: {user_data['actual_end']}\n\n"
        f"*Причина:* {escape_markdown(user_data['reason'], version=2)}\n"
        f"*Запрашиваемый период:* {escape_markdown(period_text, version=2)}"
    )
    
    await context.bot.send_message(
        chat_id=config.SECURITY_CHAT_ID,
        message_thread_id=topic.message_thread_id,
        text=msg_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='MarkdownV2'
    )
    
    return ConversationHandler.END

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
        employee = await db_manager.get_employee_by_telegram_id(update.effective_user.id)
        role = employee.get('role', 'employee')

        if redis_client.sadd(REDIS_OPERATORS_ONLINE_SET, user_id):
            await update.message.reply_text("✅ Вы успешно вышли на линию. Ожидайте задачи.", reply_markup=get_main_keyboard(role))
        else:
            await update.message.reply_text("ℹ️ Вы уже находитесь на линии.", reply_markup=get_main_keyboard(role))
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

        employee = await db_manager.get_employee_by_telegram_id(update.effective_user.id)
        role = employee.get('role', 'employee')
        
        if redis_client.srem(REDIS_OPERATORS_ONLINE_SET, user_id):
            await update.message.reply_text("☑️ Вы ушли с линии.", reply_markup=get_main_keyboard(role))
        else:
            await update.message.reply_text("ℹ️ Вас не было на линии.", reply_markup=get_main_keyboard(role))

    except Exception as e:
        logger.error(f"Redis error in operator_clock_out for user {user_id}: {e}")
        await update.message.reply_text("❌ Ошибка Redis. Не удалось уйти с линии.")
        
    return ConversationHandler.END