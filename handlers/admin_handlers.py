import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from utils import security_required, verify_totp, get_main_keyboard, generate_table_image
import db_manager as db_manager
from telegram.helpers import escape_markdown
import calendar_helper
from datetime import date, timedelta,datetime, time
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
import csv
import io
import json


logger = logging.getLogger(__name__)

BTN_ADMIN_TEXT = "🔐 Админка"

# --- Константы состояний ---
# Главное меню
ADMIN_MAIN_MENU = 0
# --- ЕДИНЫЙ БЛОК СОСТОЯНИЙ ДЛЯ ВСЕЙ АДМИН-ПАНЕЛИ ---
(
    # Меню
    ADMIN_MAIN_MENU,             
    EMPLOYEE_CARD_MENU,          
    SCHEDULE_MAIN_MENU,          

    SELECT_POSITION,             
    SELECT_EMPLOYEE_FROM_LIST,

    VIEW_ALL_SCHEDULE_SELECT_PERIOD,

    # Поток добавления сотрудника
    ADD_LAST_NAME, ADD_FIRST_NAME, ADD_MIDDLE_NAME, ADD_CITY, ADD_PHONE, ADD_POSITION, AWAITING_CONTACT, ADD_SCHEDULE_PATTERN, ADD_SCHEDULE_ANCHOR, ADD_ROLE,
    ADD_START_TIME, ADD_END_TIME, ADD_EMPLOYEE_MENU, SELECT_FIELD, GET_FIELD_VALUE,
    AWAITING_ADD_EMPLOYEE_2FA,   

    # Поток редактирования сотрудника
    SELECT_EMPLOYEE_TO_EDIT, EDIT_MAIN_MENU, EDIT_DATA_SELECT_FIELD,
    EDIT_DATA_GET_VALUE, EDIT_DATA_GET_REASON, AWAITING_RESET_2FA_CONFIRM, 

    # Поток изменения графика
    SCHEDULE_SELECT_MODE, SCHEDULE_SELECT_TYPE, SCHEDULE_SELECT_DATE_1,
    SCHEDULE_SELECT_DATE_2, SCHEDULE_GET_START_TIME, SCHEDULE_GET_END_TIME,
    
    # Поток просмотра графика по сотруднику
    VIEW_SCHEDULE_SELECT_EMPLOYEE, VIEW_SCHEDULE_SELECT_PERIOD, VIEW_SCHEDULE_SHOW_REPORT, 
    
    # Поток просмотра отгулов
    VIEW_ABSENCES_SELECT_PERIOD, 
    VIEW_ABSENCES_SHOW_REPORT,   

    SCHEDULE_CONFIRM_DEAL_MOVE,

    # Состояние для СБ
    AWAITING_SB_2FA, 
    SB_CHANGE_TIME,

    # Родственники сотрудника
    RELATIVES_MENU, REL_ADD_TYPE, REL_ADD_LAST_NAME, REL_ADD_FIRST_NAME, REL_ADD_MIDDLE_NAME, REL_ADD_PHONE, REL_ADD_BIRTH_DATE, REL_ADD_WORKPLACE,
    REL_ADD_POSITION, REL_ADD_REG_ADDRESS, REL_ADD_LIV_ADDRESS,

    AWAITING_FIRE_EMPLOYEE_2FA,
    AWAITING_DELETE_EMPLOYEE_2FA,
) = range(55)


# ========== СЛОВАРИ И ВСПОМОГАТЕЛЬНЫЕ ДАННЫЕ ==========
EDITABLE_FIELDS = {
    'last_name': 'Фамилия', 
    'first_name': 'Имя', 
    'middle_name': 'Отчество',
    'position': 'Должность',
    'personal_phone': 'Личный телефон', 'work_phone': 'Рабочий телефон',
    'personal_telegram_id': 'Telegram Аккаунт (ID)',
    'city': 'Город', 'role': 'Роль',
    'schedule_pattern': 'График работы (5/2, 2/2)',
    'schedule_start_date': 'Дата первой смены (для 2/2)',
    'default_start_time': 'Начало работы (ЧЧ:ММ)', 'default_end_time': 'Конец работы (ЧЧ:ММ)',
    'passport_data': 'Паспорт (Серия и Номер)',
    'passport_issued_by': 'Кем выдан паспорт',
    'passport_dept_code': 'Код подразделения',
    'birth_date': 'Дата рождения (ГГГГ-ММ-ДД)',
    'registration_address': 'Адрес регистрации',
    'living_address': 'Адрес проживания',
}

async def remove_reply_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Отправляет сообщение с удалением кастомной клавиатуры."""
    await update.message.reply_text(text, reply_markup=ReplyKeyboardRemove())

# ========== ГЛАВНОЕ АДМИН-МЕНЮ ==========
@security_required
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("👤 Карточка сотрудника", callback_data='go_to_employee_card_menu')],
        [InlineKeyboardButton("📅 Рабочий график", callback_data='go_to_schedule_menu')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        msg = await update.message.reply_text("Панель администратора:", reply_markup=reply_markup)
        context.user_data['admin_menu_message_id'] = msg.message_id
        
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Панель администратора:", reply_markup=reply_markup)
        context.user_data['admin_menu_message_id'] = update.callback_query.message.message_id
        
    return ADMIN_MAIN_MENU

async def show_employee_card_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает меню 'Карточка сотрудника'."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить сотрудника", callback_data='admin_add_start')],
        [InlineKeyboardButton("✏️ Изменить карточку", callback_data='admin_edit_start')],
        [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data='back_to_admin_panel')],
    ]
    await query.edit_message_text(
        "Меню: Карточка сотрудника",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EMPLOYEE_CARD_MENU

async def show_schedule_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает меню 'Рабочий график'."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📊 По сотруднику", callback_data='admin_view_schedule_start')],
        [InlineKeyboardButton("📥 График ВСЕХ (файл)", callback_data='view_all_schedule_start')],
        [InlineKeyboardButton("✏️ Изменить график сотрудника", callback_data='admin_edit_schedule_start')],
        [InlineKeyboardButton("🗓️ Посмотреть отгулы/больничные", callback_data='view_absences_start')],
        [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data='back_to_admin_panel')],
    ]
    await query.edit_message_text(
        "Меню: Рабочий график",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SCHEDULE_MAIN_MENU


# ========== ЛОГИКА ДОБАВЛЕНИЯ СОТРУДНИКА ==========
async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет админское действие, удаляет старое меню и возвращает главные кнопки."""
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    role = employee.get('role', 'employee') if employee else 'employee'
    
    admin_msg_id = context.user_data.get('admin_menu_message_id')
    if admin_msg_id:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=admin_msg_id)
        except Exception:
            pass

    context.user_data.clear()
    
    await update.message.reply_text(
        "❌ Действие отменено. Вы вернулись в главное меню.", 
        reply_markup=get_main_keyboard(role)
    )
    return ConversationHandler.END

async def start_select_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Универсальная функция старта выбора.
    Использует индексы вместо названий должностей, чтобы избежать ошибки Button_data_invalid.
    """
    query = update.callback_query
    await query.answer()
    
    # Определяем тип действия по нажатой кнопке
    action_map = {
        'admin_edit_start': 'edit_card',
        'admin_view_schedule_start': 'view_schedule',
        'admin_edit_schedule_start': 'edit_schedule'
    }
    
    # Если мы пришли из кнопки "Назад" (из списка сотрудников), то тип действия уже в памяти
    action_type = action_map.get(query.data)
    if not action_type:
        action_type = context.user_data.get('admin_action_type')
    else:
        context.user_data['admin_action_type'] = action_type

    # Получаем должности
    positions = await db_manager.get_unique_positions()
    
    if not positions:
        await query.edit_message_text(
            "В базе нет сотрудников с указанными должностями.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data='back_to_admin_panel')]])
        )
        return ADMIN_MAIN_MENU

    # === ИСПРАВЛЕНИЕ НАЧАЛО ===
    # Создаем словарь { "0": "Должность1", "1": "Должность2" } и сохраняем в память
    # Это позволяет передавать в кнопке только короткий индекс "0", "1" и т.д.
    position_map = {str(i): pos for i, pos in enumerate(positions)}
    context.user_data['position_map'] = position_map

    keyboard = []
    row = []
    for i, pos in enumerate(positions):
        # В callback_data пишем sel_pos_0, sel_pos_1 и т.д. Это занимает очень мало байт.
        # Само название (pos) остается только в тексте кнопки.
        row.append(InlineKeyboardButton(pos, callback_data=f"sel_pos_{i}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    # === ИСПРАВЛЕНИЕ КОНЕЦ ===
        
    # Кнопка назад зависит от того, откуда пришли
    back_callback = 'go_to_employee_card_menu' if action_type == 'edit_card' else 'go_to_schedule_menu'
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)])
    
    titles = {
        'edit_card': "Изменение карточки",
        'view_schedule': "Просмотр графика",
        'edit_schedule': "Изменение графика"
    }
    
    await query.edit_message_text(
        f"*{titles.get(action_type, 'Выбор')}*\nВыберите должность:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return SELECT_POSITION

async def select_employee_by_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает список сотрудников выбранной должности."""
    query = update.callback_query
    await query.answer()
    
    # === ИСПРАВЛЕНИЕ НАЧАЛО ===
    # Получаем индекс из callback_data (например, '0' из 'sel_pos_0')
    try:
        pos_index = query.data.split('_', 2)[2] 
        # Достаем реальное название из памяти
        position_map = context.user_data.get('position_map', {})
        position = position_map.get(pos_index)
    except Exception:
        position = None

    # Если бот перезагрузился и память очистилась, отправляем назад
    if not position:
        await query.edit_message_text(
            "⚠️ Данные устарели. Пожалуйста, начните выбор сначала.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 В начало", callback_data='back_to_admin_panel')]])
        )
        return ADMIN_MAIN_MENU
    # === ИСПРАВЛЕНИЕ КОНЕЦ ===

    employees = await db_manager.get_employees_by_position(position)
    
    keyboard = []
    for emp in employees:
        # callback: sel_emp_ID
        keyboard.append([InlineKeyboardButton(emp['full_name'], callback_data=f"sel_emp_{emp['id']}")])
        
    # Кнопка назад к выбору должностей
    keyboard.append([InlineKeyboardButton("⬅️ Назад к должностям", callback_data='back_to_positions')])
    
    # Экранируем название должности для Markdown, чтобы не ломалось на символах вроде "-", "."
    safe_position = escape_markdown(position, version=1)

    await query.edit_message_text(
        f"Сотрудники в должности *{safe_position}*:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return SELECT_EMPLOYEE_FROM_LIST

async def route_selected_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Принимает выбранного сотрудника и направляет в нужное русло
    в зависимости от сохраненного action_type.
    """
    query = update.callback_query
    await query.answer()
    
    employee_id = int(query.data.split('_')[2])
    action_type = context.user_data.get('admin_action_type')
    
    if action_type == 'edit_card':
        # Логика редактирования карточки
        context.user_data['employee_to_edit_id'] = employee_id
        # Вызываем функцию показа меню редактирования (нужно убедиться, что она принимает update)
        # Нам нужно подменить update или просто вызвать логику
        # Проще всего вызвать функцию show_employee_edit_menu, но она ожидает callback edit_emp_ или сохраненный ID
        # ID мы сохранили выше.
        return await show_employee_edit_menu(update, context)
        
    elif action_type == 'view_schedule':
        # Логика просмотра графика
        context.user_data['view_employee_id'] = employee_id
        
        # Сразу переходим к выбору периода (минуя старый шаг выбора сотрудника из всех)
        # Копируем логику из view_schedule_select_employee
        keyboard = [
            [InlineKeyboardButton("Текущая неделя", callback_data='view_period_week')],
            [InlineKeyboardButton("Текущий месяц", callback_data='view_period_month')],
            [InlineKeyboardButton("Текущий квартал", callback_data='view_period_quarter')],
            [InlineKeyboardButton("⬅️ Назад к списку", callback_data=f"sel_pos_RETURN")], # Хитрость: вернемся в список сотрудников этой должности
        ]
        # Нам нужно знать должность, чтобы вернуться назад. 
        # Проще вернуть в список должностей или главное меню.
        # Давайте сделаем кнопку "Назад к выбору сотрудника", которая вызовет start_select_position
        
        keyboard = [
            [InlineKeyboardButton("Текущая неделя", callback_data='view_period_week')],
            [InlineKeyboardButton("Текущий месяц", callback_data='view_period_month')],
            [InlineKeyboardButton("Текущий квартал", callback_data='view_period_quarter')],
            [InlineKeyboardButton("⬅️ Назад к выбору должности", callback_data='back_to_positions')],
        ]
        
        await query.edit_message_text("Выберите период для просмотра:", reply_markup=InlineKeyboardMarkup(keyboard))
        return VIEW_SCHEDULE_SELECT_PERIOD
        
    elif action_type == 'edit_schedule':
        # Логика изменения графика
        context.user_data['employee_to_edit_id'] = employee_id
        return await schedule_start(update, context)
        
    else:
        await query.edit_message_text("Ошибка: неизвестное действие.")
        return ADMIN_MAIN_MENU
    
async def start_add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    # Сохраняем ID меню перед тем, как отправить текстовое сообщение
    context.user_data['admin_menu_message_id'] = query.message.message_id
    
    context.user_data['new_employee'] = {}
    cancel_kb = ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)
    
    await query.message.reply_text("Начинаем добавление нового сотрудника.\nВведите **Фамилию** (или нажмите '❌ Отмена' для выхода):", reply_markup=cancel_kb, parse_mode='Markdown')
    return ADD_LAST_NAME

async def get_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_employee']['last_name'] = update.message.text.strip()
    await update.message.reply_text("Отлично. Теперь введите **Имя**:", parse_mode='Markdown')
    return ADD_FIRST_NAME

async def get_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_employee']['first_name'] = update.message.text.strip()
    await update.message.reply_text("Хорошо. Введите **Отчество** (если нет, поставьте прочерк '-'):", parse_mode='Markdown')
    return ADD_MIDDLE_NAME

async def get_middle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == '-':
        context.user_data['new_employee']['middle_name'] = ""
    else:
        context.user_data['new_employee']['middle_name'] = text

    await update.message.reply_text("Принято. Введите **Город** проживания сотрудника:", parse_mode='Markdown')
    return ADD_CITY

async def get_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    city = update.message.text.strip()
    context.user_data['new_employee']['city'] = city
    
    await update.message.reply_text(
        "Город сохранен.\n\n"
        "Введите **Личный номер телефона** (текстом, например: +79990001122):", 
        parse_mode='Markdown'
    )
    return ADD_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text.strip()
    context.user_data['new_employee']['personal_phone'] = phone
    
    positions = ["Кассир", "Инспектор ФБ", "Оператор", "Чат менеджер", "СБ", "Администратор", "Логист", "Менеджер АХО"]
    buttons = [InlineKeyboardButton(pos, callback_data=f"pos_{pos}") for pos in positions]
    keyboard_rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    reply_markup = InlineKeyboardMarkup(keyboard_rows)
    
    await update.message.reply_text("Телефон сохранен. Выберите **Должность**:", reply_markup=reply_markup, parse_mode='Markdown')
    return ADD_POSITION

async def get_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    position = query.data.split('_', 1)[1]
    context.user_data['new_employee']['position'] = position
    await query.edit_message_text(
        f"Должность '{position}' установлена.\n\n"
        "Теперь, пожалуйста, **отправьте контакт сотрудника**. Для этого нажмите на 📎 (скрепку), выберите 'Контакт' и найдите нужного пользователя в списке."
    )
    return AWAITING_CONTACT

async def get_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    contact = update.message.contact
    if not contact or not contact.user_id:
        await update.message.reply_text("❌ **Ошибка.** Пожалуйста, отправьте именно контакт пользователя Telegram.")
        return AWAITING_CONTACT

    telegram_id = contact.user_id
    try:
        chat = await context.bot.get_chat(telegram_id)
        username = chat.username
    except Exception:
        username = None

    existing_employee = await db_manager.get_employee_by_telegram_id(telegram_id)
    if existing_employee:
        await update.message.reply_text(
            f"❌ **Дубликат!** Сотрудник с таким Telegram ID ({telegram_id}) уже существует: *{existing_employee['full_name']}*.\n\n"
            "Пожалуйста, отправьте контакт другого пользователя."
        )
        return AWAITING_CONTACT

    context.user_data['new_employee']['personal_telegram_id'] = telegram_id
    if username:
        context.user_data['new_employee']['personal_telegram_username'] = username

    keyboard = [
        [
            InlineKeyboardButton("5/2", callback_data='sched_5/2'),
            InlineKeyboardButton("2/2", callback_data='sched_2/2'),
            InlineKeyboardButton("6/1", callback_data='sched_6/1'),
            InlineKeyboardButton("7/0", callback_data='sched_7/0')
        ]
    ]
    await update.message.reply_text("✅ ID получен. Теперь выберите стандартный график работы:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_SCHEDULE_PATTERN

async def wrong_input_in_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пожалуйста, не отправляйте текст. Мне нужен именно **контакт** сотрудника.\nНажмите на 📎 и выберите 'Контакт'.")

async def get_schedule_anchor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_text = update.message.text.strip()
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_text):
        await update.message.reply_text("❌ Неверный формат даты. Пожалуйста, введите дату в формате *ГГГГ-ММ-ДД* (например, *2024-01-31*) или нажмите '❌ Отмена'.", parse_mode='Markdown')

        return ADD_SCHEDULE_ANCHOR
        
    context.user_data['new_employee']['schedule_start_date'] = date_text
    
    # Убираем клавиатуру отмены
    await update.message.reply_text("Дата отсчета сохранена.", reply_markup=ReplyKeyboardRemove())
    
    # Переходим к выбору роли
    return await ask_role_step(update, context)

async def get_schedule_pattern(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pattern = query.data.split('_', 1)[1]
    context.user_data['new_employee']['schedule_pattern'] = pattern
    
    # Если выбрали 2/2, спрашиваем дату отсчета
    if pattern == '2/2':
        # Создаем кнопку отмены для следующего шага
        cancel_kb = ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)
        # Удаляем старое инлайн-меню, т.к. переходим к тексту
        try:
            await query.message.delete()
        except:
            pass
            
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Выбран график 2/2.\n\nВведите **Дату первой рабочей смены** (точку отсчета) в формате ГГГГ-ММ-ДД (например, {date.today()}):",
            reply_markup=cancel_kb,
            parse_mode='Markdown'
        )
        return ADD_SCHEDULE_ANCHOR
    
    # Для остальных графиков сразу идем к выбору роли
    return await ask_role_step(update, context)

async def ask_role_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вспомогательная функция для показа выбора роли."""
    keyboard = [
        [InlineKeyboardButton("Admin", callback_data='role_Admin')],
        [InlineKeyboardButton("Security", callback_data='role_Security')],
        [InlineKeyboardButton("Employee", callback_data='role_Employee')],
    ]
    # Если мы пришли из функции get_schedule_pattern (где был query), редактируем сообщение
    # Если из get_schedule_anchor (где был текст), отправляем новое
    if update.callback_query:
        await update.callback_query.edit_message_text("График установлен. Выберите роль:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        # Сохраняем ID меню, чтобы потом удалить при отмене
        msg = await update.message.reply_text("График установлен. Выберите роль:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['admin_menu_message_id'] = msg.message_id
    
    return ADD_ROLE

async def get_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['new_employee']['role'] = query.data.split('_', 1)[1]
    
    reply_keyboard = [["09:00", "10:00", "11:00", "12:00", "13:00"]]

    await query.edit_message_text(
        "Роль установлена. Выберите или введите стандартное время начала работы:",
        reply_markup=InlineKeyboardMarkup([]) # Убираем старые инлайн-кнопки
    )
    # Отправляем новое сообщение с обычной клавиатурой
    await query.message.reply_text(
        "Варианты времени:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    
    return ADD_START_TIME

async def get_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_employee']['default_start_time'] = update.message.text
    
    reply_keyboard = [["18:00", "20:00", "21:00", "22:00", "23:00"]]

    await remove_reply_keyboard(update, context, "Время начала сохранено.")
    
    await update.message.reply_text(
        "Теперь выберите или введите стандартное время окончания работы:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    
    return ADD_END_TIME

async def get_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_employee']['default_end_time'] = update.message.text
    
    await update.message.reply_text("Время окончания сохранено.", reply_markup=ReplyKeyboardRemove())
    return await show_add_employee_menu(update, context)

async def show_add_employee_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("➕ Добавить/изменить поля", callback_data='action_edit')],
        [InlineKeyboardButton("✅ Завершить и добавить", callback_data='action_confirm')],
    ]
    employee_data = context.user_data['new_employee']
    
    # Заголовок жирным
    text_parts = ["*Данные для добавления:*\n"]
    
    for key, value in employee_data.items():
        # Получаем красивое название поля
        field_name = EDITABLE_FIELDS.get(key, key.replace('_', ' ').capitalize())
        
        # Экранируем значение, чтобы спецсимволы (например _ в нике или * в имени) не ломали Markdown
        # Если value None, превращаем в пустую строку или '-'
        val_str = str(value) if value is not None else "-"
        safe_value = escape_markdown(val_str, version=1)
        
        text_parts.append(f"{field_name}: {safe_value}")
        
    text = "\n".join(text_parts) + "\n\nВыберите дальнейшее действие."
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, 
                reply_markup=reply_markup, 
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                text, 
                reply_markup=reply_markup, 
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error sending add employee menu: {e}")
        # Если вдруг Markdown все равно сломался, отправляем без него
        text_no_md = text.replace('*', '')
        if update.callback_query:
            await update.callback_query.edit_message_text(text_no_md, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text_no_md, reply_markup=reply_markup)

    return ADD_EMPLOYEE_MENU

async def select_field_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    buttons = [[InlineKeyboardButton(name, callback_data=f"field_{field}")] for field, name in EDITABLE_FIELDS.items()]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data='back_to_menu')])
    await query.edit_message_text("Выберите поле для изменения:", reply_markup=InlineKeyboardMarkup(buttons))
    return SELECT_FIELD

async def request_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field = query.data.split('_', 1)[1]
    context.user_data['current_field'] = field
    
    reply_keyboard = None
    message_text = f"Введите новое значение для поля '{EDITABLE_FIELDS[field]}':"

    if field == 'default_start_time':
        reply_keyboard = [["09:00", "10:00", "11:00", "12:00", "13:00"]]
    elif field == 'default_end_time':
        reply_keyboard = [["18:00", "20:00", "21:00", "22:00", "23:00"]]
        
    await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup([]))
    if reply_keyboard:
        await query.message.reply_text(
            "Варианты:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        
    return GET_FIELD_VALUE

async def get_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field = context.user_data['current_field']
    value = update.message.text
    unique_fields = ['personal_phone', 'work_phone']
    if field in unique_fields:
        existing_employee = await db_manager.find_employee_by_field(field, value)
        if existing_employee:
            await update.message.reply_text(f"❌ **Дубликат!** ...\nВведите другое.")
            return GET_FIELD_VALUE
            
    context.user_data.pop('current_field')
    context.user_data['new_employee'][field] = value
    
    await update.message.reply_text("Значение сохранено.", reply_markup=ReplyKeyboardRemove())
    
    return await show_add_employee_menu(update, context)

async def confirm_add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Для подтверждения добавления введите ваш код 2FA.")
    return AWAITING_ADD_EMPLOYEE_2FA

async def finalize_add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    admin_employee = await db_manager.get_employee_by_telegram_id(update.effective_user.id)
    role = admin_employee.get('role', 'admin')

    if admin_employee and admin_employee.get('totp_secret') and verify_totp(admin_employee['totp_secret'], update.message.text):
        employee_data = context.user_data['new_employee']

        l = employee_data.get('last_name', '')
        f = employee_data.get('first_name', '')
        m = employee_data.get('middle_name', '')

        full_name = f"{l} {f} {m}".strip()
        employee_data['full_name'] = full_name

        try:
            await db_manager.add_employee(employee_data)
            await update.message.reply_text(f"✅ Сотрудник {full_name} успешно добавлен!", reply_markup=get_main_keyboard(role))

            admin_msg_id = context.user_data.get('admin_menu_message_id')
            if admin_msg_id:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=admin_msg_id)
                except Exception:
                    pass

        except Exception as e:
            await update.message.reply_text(f"❌ Произошла ошибка при добавлении в базу данных: {e}")
    else:
        await update.message.reply_text("❌ Неверный код 2FA. Операция отменена.", reply_markup=get_main_keyboard(role))
    context.user_data.clear()
    return ConversationHandler.END


# ========== ЛОГИКА РЕДАКТИРОВАНИЯ СОТРУДНИКА ==========
async def edit_schedule_start_select_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Точка входа для изменения графика: сначала выбор сотрудника."""
    query = update.callback_query
    await query.answer()
    
    employees = await db_manager.get_all_employees()
    if not employees:
        await query.edit_message_text("В системе нет сотрудников.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')]]))
        return SELECT_EMPLOYEE_TO_EDIT # Можно использовать это состояние
        
    keyboard = [[InlineKeyboardButton(f"{emp['full_name']}", callback_data=f"edit_sched_emp_{emp['id']}")] for emp in employees]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')])
    
    await query.edit_message_text("Выберите сотрудника для изменения графика:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_EMPLOYEE_TO_EDIT

async def edit_schedule_selected_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сотрудник для изменения графика выбран, запускаем основной диалог."""
    query = update.callback_query
    await query.answer()
    
    employee_id = int(query.data.split('_')[3])
    context.user_data['employee_to_edit_id'] = employee_id
    
    # Передаем управление функции, которая начинает диалог изменения графика
    return await schedule_start(update, context)

async def start_edit_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    employees = await db_manager.get_all_employees()
    if not employees:
        await query.edit_message_text("В системе нет сотрудников для редактирования.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data='back_to_admin_panel')]]))
        return SELECT_EMPLOYEE_TO_EDIT
        
    keyboard = [[InlineKeyboardButton(f"{emp['full_name']} ({emp.get('position', 'N/A')})", callback_data=f"edit_emp_{emp['id']}")] for emp in employees]
    keyboard.append([InlineKeyboardButton("⬅️ Назад в админ-панель", callback_data='back_to_admin_panel')])
    
    await query.edit_message_text("Выберите сотрудника для редактирования:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_EMPLOYEE_TO_EDIT

async def show_employee_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    # Определяем, кто вызывает меню (сообщение или callback)
    if query:
        await query.answer()
        user_id = query.from_user.id
        message_sender = query
    else:
        user_id = update.message.from_user.id
        message_sender = update.message
    
    # Получаем ID редактируемого сотрудника
    if query and query.data.startswith('edit_emp_'):
        employee_id = int(query.data.split('_')[2])
        context.user_data['employee_to_edit_id'] = employee_id
    else:
        employee_id = context.user_data.get('employee_to_edit_id')

    if not employee_id:
        await context.bot.send_message(chat_id=user_id, text="Ошибка: ID сотрудника не найден.")
        return await start_edit_employee(update, context)

    target_employee = await db_manager.get_employee_by_id(employee_id)
    if not target_employee:
        await context.bot.send_message(chat_id=user_id, text="Ошибка: сотрудник не найден.")
        return await start_edit_employee(update, context)

    admin_employee = await db_manager.get_employee_by_telegram_id(user_id)
    admin_role = admin_employee['role'].lower() if admin_employee else 'employee'

    keyboard = [
        [InlineKeyboardButton("📝 Изменить данные", callback_data="edit_data_start")],
        [InlineKeyboardButton("🔄 Сбросить 2FA", callback_data="reset_2fa_start")],
    ]

    if admin_role in ['admin', 'security']:
        keyboard.append([InlineKeyboardButton("❌ Уволить сотрудника", callback_data="fire_employee_start")])

    if admin_role == 'admin':
        keyboard.append([InlineKeyboardButton("🗑 УДАЛИТЬ ИЗ БД", callback_data="delete_employee_start")])

    keyboard.append([InlineKeyboardButton("⬅️ Назад к выбору", callback_data="back_to_positions")])
    
    text = f"Редактирование: *{target_employee['full_name']}*\nДолжность: {target_employee.get('position', '-')}\n\nВыберите действие:"
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
    return EDIT_MAIN_MENU

async def show_relatives_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает список родственников и кнопку добавления."""
    query = update.callback_query
    await query.answer()
    
    employee_id = context.user_data['employee_to_edit_id']
    relatives = await db_manager.get_employee_relatives(employee_id)
    
    text = "*Список родственников:*\n\n"
    keyboard = []
    
    if not relatives:
        text += "Нет добавленных родственников."
    else:
        for rel in relatives:
            # Формируем строку: Мама: Иванова И.И.
            info = f"{rel['relationship_type']}: {rel['last_name']} {rel['first_name']}"
            text += f"• {info}\n"
            # Кнопка удаления (опционально)
            # keyboard.append([InlineKeyboardButton(f"❌ Удалить {rel['relationship_type']}", callback_data=f"del_rel_{rel['id']}")])

    text += "\n\nВыберите действие:"
    
    keyboard.append([InlineKeyboardButton("➕ Добавить родственника", callback_data='add_new_relative')])
    keyboard.append([InlineKeyboardButton("⬅️ Назад к полям", callback_data='back_to_fields')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return RELATIVES_MENU

# --- ЦЕПОЧКА ДОБАВЛЕНИЯ ---

async def start_add_relative(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['new_relative'] = {} # Инициализируем словарь
    
    # Спрашиваем тип родства
    buttons = [
        [InlineKeyboardButton("Мама", callback_data="rel_type_Мама"), InlineKeyboardButton("Папа", callback_data="rel_type_Папа")],
        [InlineKeyboardButton("Муж", callback_data="rel_type_Муж"), InlineKeyboardButton("Жена", callback_data="rel_type_Жена")],
        [InlineKeyboardButton("Сын", callback_data="rel_type_Сын"), InlineKeyboardButton("Дочь", callback_data="rel_type_Дочь")],
        [InlineKeyboardButton("Брат", callback_data="rel_type_Брат"), InlineKeyboardButton("Сестра", callback_data="rel_type_Сестра")],
    ]
    await query.edit_message_text("Кем приходится этот человек сотруднику?", reply_markup=InlineKeyboardMarkup(buttons))
    return REL_ADD_TYPE

async def get_rel_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    rel_type = query.data.split('_')[2]
    context.user_data['new_relative']['relationship_type'] = rel_type
    
    await query.edit_message_text(f"Выбрано: {rel_type}.\n\nВведите **Фамилию** родственника:", parse_mode='Markdown')
    return REL_ADD_LAST_NAME

async def get_rel_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_relative']['last_name'] = update.message.text
    await update.message.reply_text("Введите **Имя** родственника:")
    return REL_ADD_FIRST_NAME

async def get_rel_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_relative']['first_name'] = update.message.text
    await update.message.reply_text("Введите **Отчество** (или '-' если нет):")
    return REL_ADD_MIDDLE_NAME

async def get_rel_middle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    context.user_data['new_relative']['middle_name'] = "" if text == '-' else text
    await update.message.reply_text("Введите **Номер телефона** родственника:")
    return REL_ADD_PHONE

async def get_rel_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_relative']['phone_number'] = update.message.text
    await update.message.reply_text("Введите **Дату рождения** (формат ГГГГ-ММ-ДД, например 1975-05-20):")
    return REL_ADD_BIRTH_DATE

async def get_rel_birth_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    import re
    date_text = update.message.text
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_text):
        await update.message.reply_text("❌ Неверный формат. Попробуйте еще раз (ГГГГ-ММ-ДД):")
        return REL_ADD_BIRTH_DATE
        
    context.user_data['new_relative']['birth_date'] = date_text
    await update.message.reply_text("Введите **Место работы** (Название компании):")
    return REL_ADD_WORKPLACE

async def get_rel_workplace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_relative']['workplace'] = update.message.text
    await update.message.reply_text("Введите **Должность**:")
    return REL_ADD_POSITION

async def get_rel_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_relative']['position'] = update.message.text
    await update.message.reply_text("Введите **Адрес регистрации** (по прописке):")
    return REL_ADD_REG_ADDRESS

async def get_rel_reg_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_relative']['registration_address'] = update.message.text
    
    keyboard = [[InlineKeyboardButton("Совпадает с регистрацией", callback_data="same_address")]]
    await update.message.reply_text(
        "Введите **Адрес проживания** (фактический):\n(Или нажмите кнопку, если совпадает)", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return REL_ADD_LIV_ADDRESS

async def get_rel_liv_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Может прийти текст или коллбек
    if update.callback_query:
        await update.callback_query.answer()
        # Копируем адрес регистрации
        context.user_data['new_relative']['living_address'] = context.user_data['new_relative']['registration_address']
        # Т.к. это callback, нам нужно отправить новое сообщение для финала или отредактировать старое
        await update.callback_query.edit_message_text("Адрес скопирован.") 
    else:
        context.user_data['new_relative']['living_address'] = update.message.text

    # Финализация
    employee_id = context.user_data['employee_to_edit_id']
    relative_data = context.user_data['new_relative']
    
    try:
        await db_manager.add_relative(employee_id, relative_data)
        success_text = f"✅ Родственник ({relative_data['relationship_type']}) успешно добавлен!"
    except Exception as e:
        logger.error(f"Error adding relative: {e}")
        success_text = f"❌ Ошибка при сохранении: {e}"
    
    # Отправляем сообщение
    if update.callback_query:
        # Если нажали кнопку "Совпадает", мы уже ответили, шлем новое меню
        pass 
    else:
        await update.message.reply_text(success_text)
        
    # Возвращаемся в меню родственников (нужно обновить update для вызова функции или отправить сообщение вручную)
    # Проще вызвать функцию меню, но нужно подготовить dummy update или просто отправить текст с кнопками.
    # Давайте отправим текст с кнопкой возврата.
    
    keyboard = [[InlineKeyboardButton("🔙 К списку родственников", callback_data='manage_relatives')]]
    # Если это было текстовое сообщение
    if not update.callback_query:
        await update.message.reply_text("Готово.", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
         await update.callback_query.message.reply_text("Готово.", reply_markup=InlineKeyboardMarkup(keyboard))
         
    return RELATIVES_MENU

async def start_edit_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    # Если вызов через callback (нажатие кнопки)
    if query: 
        await query.answer()
        # Сохраняем ID текущего меню (для последующего удаления)
        context.user_data['admin_menu_message_id'] = query.message.message_id

    employee_id = context.user_data['employee_to_edit_id']
    employee = await db_manager.get_employee_by_id(employee_id)

    buttons = []
    for field, name in EDITABLE_FIELDS.items():
        if 'relative' not in field: 
            buttons.append([InlineKeyboardButton(name, callback_data=f"edit_data_field_{field}")])
    
    buttons.insert(0, [InlineKeyboardButton("👨‍👩‍👧 Управление родственниками", callback_data='manage_relatives')])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data='back_to_edit_menu')])

    text = f"Редактирование данных: *{employee['full_name']}*\nВыберите поле:"
    reply_markup = InlineKeyboardMarkup(buttons)

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # Если вызов после текстового сообщения (например, после успешного сохранения)
        msg = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        # ВАЖНО: Запоминаем ID этого нового сообщения меню!
        context.user_data['admin_menu_message_id'] = msg.message_id

    return EDIT_DATA_SELECT_FIELD

async def request_edit_data_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрашивает новое значение для выбранного поля."""
    query = update.callback_query
    await query.answer()
    field = query.data.split('_', 3)[3]
    context.user_data['current_edit_field'] = field
    context.user_data['admin_menu_message_id'] = query.message.message_id

    reply_keyboard = None
    field_name = EDITABLE_FIELDS.get(field, field)
    message_text = f"Введите новое значение для поля '{field_name}'"

    if field == 'personal_telegram_id':
        message_text = (
            f"Редактирование **{field_name}**.\n\n"
            "Пожалуйста, нажмите на 📎 (скрепку), выберите **'Контакт'** и отправьте контакт нужного сотрудника.\n"
            "Бот автоматически извлечет новый ID."
        )

    if 'date' in field:
        message_text += " в формате ГГГГ-ММ-ДД (например, 2025-12-31)"
        
    message_text += "\n(или нажмите '❌ Отмена'):"

    if field == 'default_start_time':
        reply_keyboard = [["09:00", "10:00", "11:00", "12:00", "13:00"], ["❌ Отмена"]]
    elif field == 'default_end_time':
        reply_keyboard = [["18:00", "20:00", "21:00", "22:00", "23:00"], ["❌ Отмена"]]
    else:
        reply_keyboard = [["❌ Отмена"]]

    await query.edit_message_text(f"Редактирование поля: {EDITABLE_FIELDS.get(field, field)}", reply_markup=InlineKeyboardMarkup([]))
    await query.message.reply_text(
        message_text,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode='Markdown'
    )

    return EDIT_DATA_GET_VALUE

async def get_edited_data_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает новое значение и запрашивает причину изменения."""
    field = context.user_data['current_edit_field']
    employee_id = context.user_data['employee_to_edit_id']
    
    value = None

    if update.message.contact:
        if field != 'personal_telegram_id':
             await update.message.reply_text("❌ Для этого поля ввод контактом не поддерживается. Введите текст.")
             return EDIT_DATA_GET_VALUE
        
        contact = update.message.contact
        if not contact.user_id:
             await update.message.reply_text("❌ В этом контакте нет Telegram ID. Попробуйте другой.")
             return EDIT_DATA_GET_VALUE
             
        existing = await db_manager.find_employee_by_field('personal_telegram_id', contact.user_id)
        if existing and existing['id'] != employee_id:
            await update.message.reply_text(
                f"❌ Дубликат! Этот Telegram ID уже привязан к сотруднику: {existing['full_name']}.",
                reply_markup=ReplyKeyboardRemove()
            )
            return EDIT_DATA_GET_VALUE
            
        value = str(contact.user_id)
        
        try:
            chat = await context.bot.get_chat(contact.user_id)
            if chat.username:
                 context.user_data['new_telegram_username'] = chat.username
        except:
            pass
            
    elif update.message.text:
        value = update.message.text.strip()
        
        if field == 'personal_telegram_id':
             if not value.isdigit():
                 await update.message.reply_text("❌ ID должен состоять только из цифр. Лучше отправьте контакт через скрепку.")
                 return EDIT_DATA_GET_VALUE
    else:
        await update.message.reply_text("❌ Непонятный формат данных.")
        return EDIT_DATA_GET_VALUE

    if 'date' in field:
        import re
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', value):
            await update.message.reply_text(
                "❌ Неверный формат даты. Пожалуйста, введите дату в формате *ГГГГ-ММ-ДД* (например, *2024-01-31*) или нажмите '❌ Отмена'.",
                parse_mode='Markdown'
            )
            return EDIT_DATA_GET_VALUE

    unique_fields = ['personal_phone', 'work_phone']
    if field in unique_fields:
        existing_employee = await db_manager.find_employee_by_field(field, value)
        if existing_employee and existing_employee['id'] != employee_id:
            await update.message.reply_text(f"❌ *Дубликат!* Такой номер уже есть в базе у сотрудника {existing_employee['full_name']}.\nВведите другое значение или нажмите '❌ Отмена'.",
                parse_mode='Markdown')
            return EDIT_DATA_GET_VALUE
    
    context.user_data['new_field_value'] = value
    
    cancel_kb = ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
    
    await update.message.reply_text(
        "Значение принято. Теперь введите *краткую причину* изменения (например, 'Ошибка при вводе').",
        reply_markup=cancel_kb,
        parse_mode='Markdown'
    )
    
    return EDIT_DATA_GET_REASON

async def save_data_with_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет измененное значение и причину в БД и лог."""
    reason = update.message.text
    field = context.user_data.pop('current_edit_field')
    new_value = context.user_data.pop('new_field_value')
    employee_id = context.user_data['employee_to_edit_id']
    
    admin_telegram_id = update.effective_user.id
    admin_employee = await db_manager.get_employee_by_telegram_id(admin_telegram_id)
    admin_id_for_log = admin_employee['id'] if admin_employee else None
    role = admin_employee.get('role', 'employee') if admin_employee else 'employee'

    try:
        # Получаем старое значение
        employee = await db_manager.get_employee_by_id(employee_id)
        old_value = employee.get(field)

        # Обновляем поле
        await db_manager.update_employee_field(employee_id, field, new_value)

        if field == 'personal_telegram_id':
             new_username = context.user_data.pop('new_telegram_username', None)
             if new_username:
                 await db_manager.update_employee_field(employee_id, 'personal_telegram_username', new_username)
        
        # --- СИНХРОНИЗАЦИЯ FULL_NAME ---
        if field in ['last_name', 'first_name', 'middle_name']:
            await db_manager.sync_employee_full_name(employee_id)

        # Лог аудита
        await db_manager.log_employee_change(admin_id_for_log, employee_id, field, old_value, new_value, reason)

        # 1. Удаляем старое сообщение с меню (если оно есть), так как сейчас мы создадим новое
        old_menu_id = context.user_data.get('admin_menu_message_id')
        if old_menu_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_menu_id)
            except Exception:
                pass

        # 2. Успех: Отправляем сообщение и ВОССТАНАВЛИВАЕМ ГЛАВНУЮ КЛАВИАТУРУ
        await update.message.reply_text(
            f"✅ Поле '{EDITABLE_FIELDS.get(field, field)}' успешно обновлено.", 
            reply_markup=get_main_keyboard(role)
        )

    except Exception as e:
        logger.error(f"Edit error: {e}")
        await update.message.reply_text(
            f"❌ Ошибка при сохранении: {e}", 
            reply_markup=get_main_keyboard(role)
        )

    # Возвращаемся в меню редактирования (там появится новое инлайн-меню)
    return await start_edit_data(update, context)

# --- ЛОГИКА ИЗМЕНЕНИЯ ГРАФИКА ---
async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 1: Выбор режима (одна дата / период)."""
    query = update.callback_query
    await query.answer()

    context.user_data['admin_menu_message_id'] = query.message.message_id

    keyboard = [
        [InlineKeyboardButton("Одна дата", callback_data='sched_mode_single')],
        [InlineKeyboardButton("Период дат", callback_data='sched_mode_period')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_edit_menu')],
    ]
    await query.edit_message_text(
        "Выберите режим изменения графика:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SCHEDULE_SELECT_MODE

async def schedule_select_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 2: Сохранение режима и показ календаря для первой даты."""
    query = update.callback_query
    await query.answer()
    
    mode = query.data.split('_')[2]  # single или period
    context.user_data['schedule_edit_mode'] = mode
    
    message = "Выберите дату:" if mode == 'single' else "Выберите ДАТУ НАЧАЛА периода:"
    
    await query.edit_message_text(
        text=message,
        reply_markup=calendar_helper.create_calendar()
    )
    return SCHEDULE_SELECT_DATE_1

async def schedule_select_date_1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 3: Сохранение первой даты. Если режим 'period' - ждем вторую, иначе - выбираем тип."""
    query = update.callback_query
    await query.answer()
    
    # Обработка навигации по календарю
    if not query.data.startswith('cal_day_'):
        year, month = calendar_helper.process_calendar_selection(update)
        await query.edit_message_text(
            text=query.message.text,
            reply_markup=calendar_helper.create_calendar(year, month)
        )
        return SCHEDULE_SELECT_DATE_1 # Остаемся в этом же состоянии
    
    # Сохраняем первую дату
    selected_date = query.data.split('_', 2)[2]
    context.user_data['schedule_date_1'] = selected_date
    
    mode = context.user_data['schedule_edit_mode']
    if mode == 'period':
        await query.edit_message_text(
            text=f"Дата начала: {selected_date}. Теперь выберите ДАТУ ОКОНЧАНИЯ периода:",
            reply_markup=calendar_helper.create_calendar()
        )
        return SCHEDULE_SELECT_DATE_2
    else: # single
        return await schedule_show_type_selector(update, context)

async def schedule_select_date_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 4 (для периода): Сохранение второй даты и переход к выбору типа."""
    query = update.callback_query
    await query.answer()

    if not query.data.startswith('cal_day_'):
        year, month = calendar_helper.process_calendar_selection(update)
        await query.edit_message_text(
            text=query.message.text,
            reply_markup=calendar_helper.create_calendar(year, month)
        )
        return SCHEDULE_SELECT_DATE_2

    selected_date = query.data.split('_', 2)[2]
    context.user_data['schedule_date_2'] = selected_date
    
    return await schedule_show_type_selector(update, context)

async def schedule_show_type_selector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 5: Показывает кнопки выбора типа изменения (Выходной, Рабочее время)."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("Полностью выходной/отгул", callback_data='sched_type_DAY_OFF')],
        [InlineKeyboardButton("Больничный", callback_data='sched_type_SICK_LEAVE')],
        [InlineKeyboardButton("Изменить рабочее время", callback_data='sched_type_WORK_TIME')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_positions')],
    ]
    
    date1 = context.user_data['schedule_date_1']
    date2 = context.user_data.get('schedule_date_2')
    period_text = f"c {date1} по {date2}" if date2 else f"на {date1}"

    await query.edit_message_text(
        f"Вы выбрали период {period_text}.\n\nКакое изменение применить?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SCHEDULE_SELECT_TYPE

# Файл: handlers/admin_handlers.py

async def show_deal_conflict_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, deals: list) -> int:
    """Показывает сообщение о конфликтующих сделках и кнопки подтверждения."""
    deal_list_str = "\n".join([f"- Сделка `{d['deals_id']}` на {d['datetime_meeting'].strftime('%d.%m.%Y %H:%M')}" for d in deals])
    
    text = (
        f"⚠️ *Обнаружен конфликт!*"
        f"\n\nСледующие сделки сотрудника попадают в установленное нерабочее время:\n"
        f"{deal_list_str}\n\n"
        f"Изменение графика будет сохранено, но вам необходимо будет вручную перенести эти сделки на другое время. Продолжить?"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, сохранить и перенести", callback_data='confirm_deal_move_yes')],
        [InlineKeyboardButton("❌ Нет, отменить изменение", callback_data='confirm_deal_move_no')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Определяем, как отправить сообщение (отредактировать или отправить новое)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
    return SCHEDULE_CONFIRM_DEAL_MOVE

async def save_schedule_changes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Финальная функция сохранения изменений в расписании."""
    change_type = context.user_data['schedule_change_type']
    employee_id = context.user_data['employee_to_edit_id']
    date1 = context.user_data['schedule_date_1']
    date2 = context.user_data.get('schedule_date_2', date1)
    
    is_day_off = False
    start_time = None
    end_time = None
    
    if change_type in ['DAY_OFF', 'SICK_LEAVE']:
        is_day_off = True
    elif change_type == 'WORK_TIME':
        is_day_off = False
        start_time = context.user_data['schedule_start_time']
        end_time = context.user_data['schedule_end_time']
    
    try:
        await db_manager.set_schedule_override_for_period(
            employee_id=employee_id,
            start_date_str=date1,
            end_date_str=date2,
            is_day_off=is_day_off,
            start_time=start_time,
            end_time=end_time
        )
        success_message = f"✅ График успешно изменен для периода с {date1} по {date2}."
        if update.callback_query:
            await update.callback_query.edit_message_text(success_message)
        else:
            await update.message.reply_text(success_message)
            
    except Exception as e:
        logger.error(f"Error in save_schedule_changes: {e}")
        error_message = f"❌ Произошла ошибка при сохранении: {e}"
        if update.callback_query:
            await update.callback_query.edit_message_text(error_message)
        else:
            await update.message.reply_text(error_message)
            
    # Очищаем все временные данные по изменению графика
    for key in ['schedule_edit_mode', 'schedule_date_1', 'schedule_date_2', 'schedule_change_type', 'schedule_start_time', 'schedule_end_time']:
        context.user_data.pop(key, None)
        
    return await show_schedule_main_menu(update, context)

async def handle_deal_move_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ответ пользователя на конфликт сделок."""
    query = update.callback_query
    await query.answer()

    decision = query.data.split('_')[-1] # yes или no

    if decision == 'yes':
        # Отправляем уведомление и сохраняем
        await query.edit_message_text("Сохраняю изменения... Вам придет уведомление о необходимости переноса сделок.")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="❗️*Напоминание:*\nНе забудьте перенести сделки, которые конфликтуют с новым графиком сотрудника.",
            parse_mode='Markdown'
        )
        return await save_schedule_changes(update, context)
    else: # no
        # Отменяем и возвращаем в меню "Рабочий график"
        await query.edit_message_text("Изменение графика отменено.")
        return await show_schedule_main_menu(update, context)


async def schedule_process_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 6: Обработка выбора типа. Либо сохраняем, либо запрашиваем время."""
    query = update.callback_query
    await query.answer()
    
    change_type = query.data.split('_', 2)[2]
    context.user_data['schedule_change_type'] = change_type
    
    if change_type == 'WORK_TIME':
        reply_keyboard = [["09:00", "10:00", "11:00", "12:00", "13:00"]]

        await query.edit_message_text(
            "Выберите или введите новое ВРЕМЯ НАЧАЛА работы (в формате ЧЧ:ММ):",
            reply_markup=InlineKeyboardMarkup([])
        )
        await query.message.reply_text(
            "Варианты:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return SCHEDULE_GET_START_TIME
    
    else: # DAY_OFF или SICK_LEAVE
        is_day_off = True
        employee_id = context.user_data['employee_to_edit_id']
        date1 = context.user_data['schedule_date_1']
        date2 = context.user_data.get('schedule_date_2', date1) # Если второй даты нет, используем первую

        conflicting_deals = await db_manager.find_conflicting_deals_for_schedule(
            employee_id=employee_id,
            start_date_str=date1,
            end_date_str=date2
        )
        
        if conflicting_deals:
            # Если есть конфликты, показываем их и ждем подтверждения
            return await show_deal_conflict_confirmation(update, context, conflicting_deals)
        else:
            # Если конфликтов нет, сохраняем сразу
            return await save_schedule_changes(update, context)


async def schedule_get_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 7: Получаем время начала и запрашиваем время окончания."""
    context.user_data['schedule_start_time'] = update.message.text
    reply_keyboard = [["18:00", "19:00", "20:00"]]
    await remove_reply_keyboard(update, context, "Время начала сохранено.")
    
    await update.message.reply_text(
        "Теперь выберите или введите ВРЕМЯ ОКОНЧАНИЯ (в формате ЧЧ:ММ):",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SCHEDULE_GET_END_TIME

async def schedule_finalize_work_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 8: Получаем время окончания и сохраняем все в БД."""
    end_time = update.message.text
    start_time = context.user_data['schedule_start_time']
    context.user_data['schedule_end_time'] = end_time
    
    employee_id = context.user_data['employee_to_edit_id']
    date1 = context.user_data['schedule_date_1']
    date2 = context.user_data.get('schedule_date_2', date1)

    await update.message.reply_text("Проверяю конфликты со сделками...", reply_markup=ReplyKeyboardRemove())

    conflicting_deals = await db_manager.find_conflicting_deals_for_schedule(
        employee_id=employee_id,
        start_date_str=date1,
        end_date_str=date2,
        work_start_time_str=start_time,
        work_end_time_str=end_time
    )
    
    if conflicting_deals:
        # Если есть конфликты, показываем их и ждем подтверждения
        return await show_deal_conflict_confirmation(update, context, conflicting_deals)
    else:
        # Если конфликтов нет, сохраняем сразу
        return await save_schedule_changes(update, context)
    
 
# --- ЛОГИКА СБРОСА 2FA ВНУТРИ ДИАЛОГА РЕДАКТИРОВАНИЯ ---

async def start_reset_2fa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    employee_id = context.user_data['employee_to_edit_id']
    employee = await db_manager.get_employee_by_id(employee_id)

    keyboard = [
        [InlineKeyboardButton("Да, сбросить 2FA", callback_data='confirm_reset_yes')],
        [InlineKeyboardButton("Нет, отмена", callback_data='back_to_edit_menu')],
    ]
    await query.edit_message_text(f"Вы уверены, что хотите сбросить 2FA для *{employee['full_name']}*?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return AWAITING_RESET_2FA_CONFIRM

async def finalize_reset_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == 'confirm_reset_yes':
        employee_id = context.user_data.get('employee_to_edit_id') # Используем .get для безопасности
        
        if not employee_id:
            await query.edit_message_text("Ошибка: ID сотрудника потерян. Попробуйте выбрать сотрудника заново.")
            return SELECT_EMPLOYEE_TO_EDIT

        employee = await db_manager.get_employee_by_id(employee_id)
        
        # Сбрасываем секрет в БД
        await db_manager.set_totp_secret(employee_id, None)
        
        # Отправляем подтверждение (редактируем сообщение с вопросом)
        await query.edit_message_text(f"✅ 2FA для сотрудника *{employee['full_name']}* успешно сброшен.", parse_mode='Markdown')

        # Удаляем старое сообщение меню, если оно было сохранено, чтобы не дублировать
        admin_msg_id = context.user_data.get('admin_menu_message_id')
        if admin_msg_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=admin_msg_id)
            except Exception:
                pass
    else: # если нажали "Нет, отмена"
        await query.edit_message_text("Сброс 2FA отменен.")
    
    return await show_employee_edit_menu(update, context)

# ========== ЛОГИКА ПРОСМОТРА ГРАФИКА ==========
# Словарь для дней недели
WEEKDAY_NAMES_RU = {0: "ПН", 1: "ВТ", 2: "СР", 3: "ЧТ", 4: "ПТ", 5: "СБ", 6: "ВС"}

async def view_schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога просмотра: выбор сотрудника."""
    query = update.callback_query
    await query.answer()
    
    employees = await db_manager.get_all_employees()
    if not employees:
        await query.edit_message_text("В системе нет сотрудников.")
        return ConversationHandler.END
        
    keyboard = [[InlineKeyboardButton(f"{emp['full_name']}", callback_data=f"view_emp_{emp['id']}")] for emp in employees]
    keyboard.append([InlineKeyboardButton("⬅️ Назад в админ-панель", callback_data='back_to_admin_panel')])
    
    await query.edit_message_text("Выберите сотрудника для просмотра графика:", reply_markup=InlineKeyboardMarkup(keyboard))
    return VIEW_SCHEDULE_SELECT_EMPLOYEE

async def view_schedule_back_to_period_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Возвращает пользователя к меню выбора периода."""
    query = update.callback_query
    await query.answer()

    # ID сотрудника уже сохранен в context.user_data, поэтому мы просто показываем меню
    keyboard = [
        [InlineKeyboardButton("Текущая неделя", callback_data='view_period_week')],
        [InlineKeyboardButton("Текущий месяц", callback_data='view_period_month')],
        [InlineKeyboardButton("Текущий квартал", callback_data='view_period_quarter')],
        [InlineKeyboardButton("⬅️ Назад к выбору сотрудника", callback_data='back_to_view_list')],
    ]
    await query.edit_message_text("Выберите период для просмотра:", reply_markup=InlineKeyboardMarkup(keyboard))
    return VIEW_SCHEDULE_SELECT_PERIOD # Возвращаемся в состояние выбора периода

async def view_schedule_select_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор периода для просмотра."""
    query = update.callback_query
    await query.answer()
    
    employee_id = int(query.data.split('_')[2])
    context.user_data['view_employee_id'] = employee_id
    
    keyboard = [
        [InlineKeyboardButton("Текущая неделя", callback_data='view_period_week')],
        [InlineKeyboardButton("Текущий месяц", callback_data='view_period_month')],
        [InlineKeyboardButton("Текущий квартал", callback_data='view_period_quarter')],
        [InlineKeyboardButton("⬅️ Назад к выбору сотрудника", callback_data='back_to_view_list')],
    ]
    await query.edit_message_text("Выберите период для просмотра:", reply_markup=InlineKeyboardMarkup(keyboard))
    return VIEW_SCHEDULE_SELECT_PERIOD

# --- ОТЧЕТ ПО ВСЕМ СОТРУДНИКАМ ---

async def view_all_schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрашивает период для общего отчета."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Текущая неделя", callback_data='all_period_week')],
        [InlineKeyboardButton("Текущий месяц", callback_data='all_period_month')],
        [InlineKeyboardButton("Текущий квартал", callback_data='all_period_quarter')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')],
    ]
    await query.edit_message_text(
        "Выберите период для выгрузки общего графика (CSV):", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return VIEW_ALL_SCHEDULE_SELECT_PERIOD

async def view_all_schedule_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Генерирует CSV файл с графиком всех сотрудников и отправляет его."""
    query = update.callback_query
    await query.answer("Генерация файла...")
    
    period = query.data.split('_')[2]
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

    employees = await db_manager.get_all_employees()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    # ДОБАВИЛИ КОЛОНКУ 'Комментарий'
    writer.writerow(['Город', 'Должность', 'ФИО', 'Дата', 'День недели', 'Время работы', 'Статус', 'Комментарий'])
    
    for emp in employees:
        schedule = await db_manager.get_employee_schedule_for_period(emp['id'], start_date, end_date)
        
        for day in schedule:
            dt = day['date']
            date_str = dt.strftime('%d.%m.%Y')
            weekday_str = WEEKDAY_NAMES_RU[dt.weekday()]
            
            # Безопасное форматирование времени (локально, чтобы не зависеть от user_handlers)
            start_t = day['start_time']
            end_t = day['end_time']
            s_str = ""
            e_str = ""
            if start_t: s_str = str(start_t)[:5]
            if end_t: e_str = str(end_t)[:5]

            time_str = f"{s_str}-{e_str}" if s_str and e_str else "-"
            comment = day.get('comment', '') or ""
                
            writer.writerow([
                emp.get('city', '-'),
                emp.get('position', '-'),
                emp['full_name'],
                date_str,
                weekday_str,
                time_str,
                day['status'],
                comment # Записываем комментарий
            ])
            
    output.seek(0)
    bio = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    bio.name = f"Schedule_{period}_{today.strftime('%Y%m%d')}.csv"
    
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=bio,
        caption=f"📅 График всех сотрудников за период: {start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m')}"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад в меню графиков", callback_data='go_to_schedule_menu')]]
    await query.edit_message_text("Файл сформирован и отправлен.", reply_markup=InlineKeyboardMarkup(keyboard))
    return VIEW_ALL_SCHEDULE_SELECT_PERIOD

async def view_schedule_generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Формирую отчет...")
    
    period = query.data.split('_')[2]
    employee_id = context.user_data['view_employee_id']
    employee = await db_manager.get_employee_by_id(employee_id)
    today = date.today()

    # ... (логика дат week/month/quarter без изменений) ...
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
    
    # Готовим данные для таблицы
    headers = ['Дата', 'День', 'Время', 'Статус', 'Комментарий']
    rows = []
    
    def safe_fmt(val): return str(val)[:5] if val else "-"

    for day in schedule_data:
        dt = day['date']
        date_str = dt.strftime('%d.%m')
        weekday = WEEKDAY_NAMES_RU[dt.weekday()]
        
        start_t = day['start_time']
        end_t = day['end_time']
        comment = day.get('comment') or ""

        if start_t and end_t:
            time_str = f"{safe_fmt(start_t)}-{safe_fmt(end_t)}"
        else:
            time_str = "-"
            
        rows.append([date_str, weekday, time_str, day['status'], comment])
        
    title = f"Сотрудник: {employee['full_name']}\nПериод: {start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m')}"
    image_bio = generate_table_image(headers, rows, title)
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Другой период", callback_data='back_to_period_select')],
        [InlineKeyboardButton("👤 Другой сотрудник", callback_data='back_to_view_list')],
        [InlineKeyboardButton("🏠 Меню", callback_data='back_to_admin_panel')],
    ]
    
    # Удаляем старое текстовое меню и шлем фото
    try:
        await query.delete_message()
    except:
        pass

    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=image_bio,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return VIEW_SCHEDULE_SHOW_REPORT

# ========== ОБЩИЕ ФУНКЦИИ И ХЕНДЛЕРЫ ==========
# (Согласования СБ, которые не являются частью админ-диалога, остаются здесь)

# ... (Код для sb_approval_start, sb_approval_2fa, sb_reject_request и т.д. остается здесь без изменений)
async def sb_approval_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс согласования от СБ для любого типа заявки."""
    query = update.callback_query
    sb_user_id = query.from_user.id
    sb_employee = await db_manager.get_employee_by_telegram_id(sb_user_id)

    if not sb_employee or sb_employee['role'].lower() not in ['security', 'admin']:
        await query.answer(f"У вас нет прав для выполнения этого действия. Роль:{sb_employee['role'].lower()}", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    
    parts = query.data.split('_')
    approval_type = parts[2]
    target_employee_id = int(parts[3])
    original_reason = parts[4] if len(parts) > 4 else approval_type

    context.user_data['sb_approval'] = {
        'target_employee_id': target_employee_id,
        'approval_type': approval_type, # 'inkas' или 'deal'
        'original_reason': original_reason # 'inkas', 'break', 'lunch' и т.д.
    }
    
    await query.edit_message_text(f"Для согласования заявки ({original_reason}) введите ваш код 2FA.")
    return AWAITING_SB_2FA

async def sb_approval_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Проверяет 2FA сотрудника СБ и выполняет согласование."""
    sb_user_id = update.effective_user.id
    sb_employee = await db_manager.get_employee_by_telegram_id(sb_user_id)
    
    if not sb_employee or sb_employee['role'].lower() not in ['security', 'admin']:
        await update.message.reply_text(f"У вас нет прав для выполнения этого действия. Роль:{sb_employee['role'].lower()}")
        return ConversationHandler.END

    code = update.message.text.strip()
    approval_data = context.user_data.get('sb_approval')

    if not approval_data:
        await update.message.reply_text("Ошибка: не найдены данные для согласования.")
        return ConversationHandler.END

    if sb_employee['totp_secret'] and verify_totp(sb_employee['totp_secret'], code):
        target_employee_id = approval_data['target_employee_id']
        approval_type = approval_data['approval_type']
        original_reason = approval_data['original_reason']

        target_employee = await db_manager.get_employee_by_id(target_employee_id)
        if not target_employee:
            await update.message.reply_text("Ошибка: целевой сотрудник не найден.")
            context.user_data.clear()
            return ConversationHandler.END

        reason_map = {
            'inkas': ('on_collection', 'Инкассация', 'Инкассация'),
            'deal': ({'break': 'on_break', 'lunch': 'on_lunch'}.get(original_reason, 'offline'), original_reason.capitalize(), 'Наличие сделки')
        }
        final_status, final_reason, approval_reason_log = reason_map[approval_type]

        await db_manager.update_employee_status(target_employee_id, final_status)
        await db_manager.log_approved_time_event(
            employee_id=target_employee_id, event_type='clock_out', reason=final_reason,
            approver_id=sb_employee['id'], approval_reason=approval_reason_log
        )
        
        await update.message.reply_text(f"✅ Вы согласовали '{final_reason}' для {target_employee['full_name']}.")
        await context.bot.send_message(target_employee['personal_telegram_id'], f"✅ Ваша заявка на '{final_reason}' согласована.")
        
    else:
        await update.message.reply_text("❌ Неверный код 2FA. Попробуйте еще раз.")
        return AWAITING_SB_2FA

    context.user_data.clear()
    return ConversationHandler.END

async def sb_reject_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    sb_user_id = query.from_user.id
    sb_employee = await db_manager.get_employee_by_telegram_id(sb_user_id)

    if not sb_employee or sb_employee['role'].lower() not in ['security', 'admin']:
        await query.answer(f"У вас нет прав для выполнения этого действия. Роль:{sb_employee['role'].lower()}", show_alert=True)
        return 

    await query.answer("Заявка отклонена")
    
    parts = query.data.split('_')
    target_employee_id = int(parts[-1])
    target_employee = await db_manager.get_employee_by_id(target_employee_id)
    
    if target_employee:
        sb_name_escaped = escape_markdown(sb_employee['full_name'], version=2)
        sb_user_link = f"[{sb_name_escaped}](tg://user?id={sb_employee['personal_telegram_id']})"
        message = f"❌ Ваша заявка была отклонена сотрудником СБ\\. Для уточнений свяжитесь с {sb_user_link}\\."
        await context.bot.send_message(
            chat_id=target_employee['personal_telegram_id'], text=message, parse_mode='MarkdownV2'
        )
    
    await query.edit_message_text(f"Вы отклонили заявку сотрудника {target_employee.get('full_name', 'Неизвестно')}.")

# Файл: handlers/admin_handlers.py

# ... (в конец файла, перед регистрацией хендлеров)

# ========== ЛОГИКА ПРОСМОТРА ОТГУЛОВ ==========

async def view_absences_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога просмотра отгулов: выбор периода."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Текущая неделя", callback_data='abs_period_week')],
        [InlineKeyboardButton("Текущий месяц", callback_data='abs_period_month')],
        [InlineKeyboardButton("Текущий квартал", callback_data='abs_period_quarter')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')],
    ]
    await query.edit_message_text("Выберите период для просмотра отгулов/изменений графика:", reply_markup=InlineKeyboardMarkup(keyboard))
    return VIEW_ABSENCES_SELECT_PERIOD

async def view_absences_generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Формирую отчет...")
    
    period = query.data.split('_')[2]
    today = date.today()
    
    # ... (логика дат без изменений) ...
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
    
    overrides_data = await db_manager.get_all_schedule_overrides_for_period(start_date, end_date)
    
    if not overrides_data:
        await query.edit_message_text(
            f"За период {start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m')} изменений нет.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')]])
        )
        return VIEW_ABSENCES_SHOW_REPORT

    # Готовим таблицу для картинки
    # Добавим колонку "Сотрудник"
    headers = ['Сотрудник', 'Дата', 'Статус/Время', 'Комментарий']
    rows = []
    
    def safe_fmt(val): return str(val)[:5] if val else ""

    for record in overrides_data:
        # Фамилия и инициалы (чтобы влезло)
        full_name = record['full_name']
        parts = full_name.split()
        short_name = full_name
        if len(parts) >= 2:
            short_name = f"{parts[0]} {parts[1][0]}."
        
        dt = record['work_date']
        date_str = dt.strftime('%d.%m')
        comment = record.get('comment') or ""

        if record['is_day_off']:
            info_str = "Отгул"
        else:
            start_t = safe_fmt(record['start_time'])
            end_t = safe_fmt(record['end_time'])
            info_str = f"{start_t}-{end_t}"
            
        rows.append([short_name, date_str, info_str, comment])

    title = f"Изменения в графике: {start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m')}"
    image_bio = generate_table_image(headers, rows, title)
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')]]
    
    try:
        await query.delete_message()
    except:
        pass

    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=image_bio,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return VIEW_ABSENCES_SHOW_REPORT

async def start_fire_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    context.user_data['admin_menu_message_id'] = query.message.message_id
    
    employee_id = context.user_data['employee_to_edit_id']
    employee = await db_manager.get_employee_by_id(employee_id)
    
    await query.edit_message_text(
        f"⚠️ Вы собираетесь **УВОЛИТЬ** сотрудника *{employee['full_name']}*.\n"
        f"Статус сменится на 'Уволен', доступ к боту будет закрыт.\n\n"
        f"Введите ваш код 2FA для подтверждения:",
        parse_mode='Markdown'
    )
    return AWAITING_FIRE_EMPLOYEE_2FA

async def finalize_fire_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Проверка 2FA админа
    admin_employee = await db_manager.get_employee_by_telegram_id(update.effective_user.id)
    role = admin_employee.get('role', 'admin')
    code = update.message.text.strip()
    
    if admin_employee and admin_employee.get('totp_secret') and verify_totp(admin_employee['totp_secret'], code):
        employee_id = context.user_data['employee_to_edit_id']
        target_employee = await db_manager.get_employee_by_id(employee_id)
        
        try:
            await db_manager.fire_employee(employee_id)
            await update.message.reply_text(f"✅ Сотрудник *{target_employee['full_name']}* успешно уволен.", parse_mode='Markdown', reply_markup=get_main_keyboard(role))
            
            admin_msg_id = context.user_data.get('admin_menu_message_id')
            if admin_msg_id:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=admin_msg_id)
                except Exception:
                    pass

            await db_manager.log_employee_change(
                admin_id=admin_employee['id'], 
                employee_id=employee_id, 
                field="employment_status", 
                old_value="active", 
                new_value="fired", 
                reason="Admin panel fire action"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при увольнении: {e}", reply_markup=get_main_keyboard(role))
            
        context.user_data.clear()
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный код 2FA. Попробуйте снова", reply_markup=get_main_keyboard(role))
        return AWAITING_FIRE_EMPLOYEE_2FA

# --- ЛОГИКА УДАЛЕНИЯ ---

async def start_delete_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    context.user_data['admin_menu_message_id'] = query.message.message_id
    
    employee_id = context.user_data['employee_to_edit_id']
    employee = await db_manager.get_employee_by_id(employee_id)
    
    await query.edit_message_text(
        f"⛔️☢️ **ВНИМАНИЕ! УДАЛЕНИЕ!** ☢️⛔️\n\n"
        f"Вы собираетесь **ПОЛНОСТЬЮ УДАЛИТЬ** сотрудника *{employee['full_name']}* из базы данных.\n"
        f"История смен, график, родственники — всё будет удалено безвозвратно.\n\n"
        f"Введите ваш код 2FA для подтверждения удаления:",
        parse_mode='Markdown'
    )
    return AWAITING_DELETE_EMPLOYEE_2FA

async def finalize_delete_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    admin_employee = await db_manager.get_employee_by_telegram_id(update.effective_user.id)
    role = admin_employee.get('role', 'admin')
    code = update.message.text.strip()
    
    if admin_employee and admin_employee.get('totp_secret') and verify_totp(admin_employee['totp_secret'], code):
        employee_id = context.user_data['employee_to_edit_id']
        target_employee = await db_manager.get_employee_by_id(employee_id)
        
        try:
            await db_manager.delete_employee_permanently(employee_id)
            await update.message.reply_text(f"🗑 Сотрудник *{target_employee['full_name']}* был полностью удален из БД.", parse_mode='Markdown', reply_markup=get_main_keyboard(role))
            admin_msg_id = context.user_data.get('admin_menu_message_id')
            if admin_msg_id:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=admin_msg_id)
                except Exception:
                    pass
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка БД при удалении: {e}", reply_markup=get_main_keyboard(role))
            
        context.user_data.clear()
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный код 2FA. Попробуйте снова.", reply_markup=get_main_keyboard(role))
        return AWAITING_DELETE_EMPLOYEE_2FA


async def sb_approve_early_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """СБ нажал 'Согласовать' (с автоматическим изменением графика)."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Проверка прав СБ
    sb_employee = await db_manager.get_employee_by_telegram_id(user_id)
    if not sb_employee or sb_employee['role'].lower() not in ['security', 'admin']:
        await query.answer("Нет прав!", show_alert=True)
        return

    await query.answer()
    
    # data: approve_early_{emp_id}
    employee_id = int(query.data.split('_')[2])
    
    # 1. Получаем заявку
    request = await db_manager.get_last_pending_request(employee_id, 'early_leave')
    
    # 2. Выпускаем сотрудника (меняем статус)
    await db_manager.update_employee_status(employee_id, 'offline')
    
    log_reason = 'Ранний уход (согласовано)'
    schedule_change_info = ""

    if request:
        data = json.loads(request['data_json'])
        mode = data.get('mode')
        
        # Получаем даты заявки
        if mode == 'today_end':
            # "Сегодня до конца" - это один день
            req_date_start = date.today()
            req_date_end = date.today()
            # Время отсутствия: с "сейчас" (или фактического выхода) до конца смены
            # Но для изменения графика нам важно знать, что конец смены теперь = времени ухода.
            # Мы возьмем время из actual_end, который сохранили при заявке
            leave_start_time_str = data.get('actual_end') # Например "17:00"
            leave_end_time_str = "23:59" # До конца дня
        else:
            # Custom период
            req_date_start = date.fromisoformat(data.get('date_start'))
            req_date_end = date.fromisoformat(data.get('date_end'))
            leave_start_time_str = data.get('time_start') # "11:00"
            leave_end_time_str = data.get('time_end')     # "12:00"

        # Проходим по дням периода
        curr_date = req_date_start
        while curr_date <= req_date_end:
            # 1. Получаем текущий (базовый) график сотрудника на этот день
            # get_employee_schedule_for_period вернет массив из 1 дня с учетом дефолтов
            base_schedule_list = await db_manager.get_employee_schedule_for_period(employee_id, curr_date, curr_date)
            
            if base_schedule_list:
                day_sched = base_schedule_list[0]
                
                # Если это рабочий день и есть время начала/конца
                if day_sched['status'] == 'Работа' and day_sched['start_time'] and day_sched['end_time']:
                    # Базовые границы рабочего дня
                    work_start = day_sched['start_time'] # timedelta или time
                    work_end = day_sched['end_time']     # timedelta или time

                    # Приводим к типу datetime.time для сравнения
                    def to_time(val):
                        if isinstance(val, str): 
                            try: return datetime.strptime(val, '%H:%M:%S').time()
                            except: return datetime.strptime(val, '%H:%M').time()
                        if isinstance(val, timedelta): return (datetime.min + val).time()
                        return val

                    ws = to_time(work_start)
                    we = to_time(work_end)
                    ls = to_time(leave_start_time_str)
                    le = to_time(leave_end_time_str)
                    
                    new_start = ws
                    new_end = we
                    comment = None
                    is_day_off = False

                    # ЛОГИКА ПЕРЕСЕЧЕНИЙ
                    
                    # 1. Отсутствие перекрывает ВЕСЬ день (или больше)
                    if ls <= ws and le >= we:
                        is_day_off = True
                        comment = "Отгул на весь день"

                    # 2. Ранний уход (Early Leave): Отсутствие начинается внутри дня и идет до конца
                    # Пример: Работа 09-18, Ушел в 17:00 (Absence 17:00-18:00)
                    elif ls > ws and ls < we and le >= we:
                        new_end = ls # Конец работы теперь равен началу отсутствия
                        comment = f"Уход раньше ({ls.strftime('%H:%M')})"

                    # 3. Опоздание/Поздний приход: Отсутствие начинается до работы и заканчивается внутри
                    # Пример: Работа 09-18, Пришел в 10:00 (Absence 09:00-10:00)
                    elif ls <= ws and le > ws and le < we:
                        new_start = le # Начало работы теперь равно концу отсутствия
                        comment = f"Поздний приход (с {le.strftime('%H:%M')})"

                    # 4. Отсутствие в середине (Split shift)
                    # Пример: Работа 09-18, Отсутствие 11-12
                    elif ls > ws and le < we:
                        # Мы не можем разделить start/end в БД, поэтому оставляем границы 09-18
                        # НО пишем специальный комментарий для отчета
                        # new_start и new_end остаются прежними (ws, we)
                        comment = f"Отсутствие {ls.strftime('%H:%M')}-{le.strftime('%H:%M')}"

                    # Применяем изменение в БД
                    # Важно: преобразуем time обратно в строку
                    await db_manager.set_schedule_override_for_period(
                        employee_id, 
                        curr_date.isoformat(), 
                        curr_date.isoformat(),
                        is_day_off=is_day_off,
                        start_time=new_start.strftime('%H:%M'),
                        end_time=new_end.strftime('%H:%M'),
                        comment=comment
                    )
                    schedule_change_info = "(График обновлен)"

            curr_date += timedelta(days=1)
        
        await db_manager.update_request_status(request['id'], 'approved')

    # 4. Логируем
    await db_manager.log_approved_time_event(
        employee_id=employee_id, event_type='clock_out', reason=log_reason,
        approver_id=sb_employee['id'], approval_reason=f'Согласование СБ {schedule_change_info}'
    )
    
    await query.edit_message_text(f"✅ Заявка согласована (СБ: {sb_employee['full_name']}).\nСотрудник отпущен. {schedule_change_info}")
    
    target_emp = await db_manager.get_employee_by_id(employee_id)
    if target_emp:
        try:
            await context.bot.send_message(target_emp['personal_telegram_id'], f"✅ Ваш запрос согласован. График скорректирован.")
        except: pass

async def sb_reject_early_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки 'Не согласовать'."""
    query = update.callback_query
    
    user_id = query.from_user.id
    sb_employee = await db_manager.get_employee_by_telegram_id(user_id)
    if not sb_employee or sb_employee['role'].lower() not in ['security', 'admin']:
        await query.answer("Нет прав!", show_alert=True)
        return

    await query.answer()
    employee_id = int(query.data.split('_')[2])
    
    # Закрываем заявку в БД
    request = await db_manager.get_last_pending_request(employee_id, 'early_leave')
    if request:
        await db_manager.update_request_status(request['id'], 'rejected')

    await query.edit_message_text(f"❌ Заявка отклонена (СБ: {sb_employee['full_name']}).")
    
    target_emp = await db_manager.get_employee_by_id(employee_id)
    if target_emp:
        try:
            await context.bot.send_message(target_emp['personal_telegram_id'], "❌ Ваш запрос отклонен.")
        except: pass

# --- ЛОГИКА "ИЗМЕНИТЬ ВРЕМЯ" (Для СБ) ---

async def sb_change_time_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """СБ нажал 'Изменить время'. Запрашиваем ввод."""
    query = update.callback_query
    user_id = query.from_user.id
    sb_employee = await db_manager.get_employee_by_telegram_id(user_id)
    
    if not sb_employee or sb_employee['role'].lower() not in ['security', 'admin']:
        await query.answer("Нет прав!", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    employee_id = int(query.data.split('_')[2])
    context.user_data['sb_edit_emp_id'] = employee_id
    
    # Сохраняем ID сообщения, чтобы потом его обновить
    context.user_data['sb_msg_id'] = query.message.message_id
    context.user_data['sb_chat_id'] = query.message.chat.id

    # Спрашиваем СБ
    # Мы используем force_reply, чтобы ответ СБ пришел именно сюда (если это супергруппа)
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=f"✏️ Введите новые параметры (даты/время) и комментарий для сотрудника.\nНапример: 'Разрешено уйти в 17:00, завтра отработать час'.",
        reply_to_message_id=query.message.message_id
    )
    return SB_CHANGE_TIME

async def sb_change_time_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получаем текст от СБ, меняем статус и уведомляем."""
    text = update.message.text
    employee_id = context.user_data.get('sb_edit_emp_id')
    sb_user_id = update.effective_user.id
    sb_employee = await db_manager.get_employee_by_telegram_id(sb_user_id)
    
    if not employee_id:
        await update.message.reply_text("Ошибка контекста.")
        return ConversationHandler.END

    # 1. Выпускаем сотрудника (так как СБ разрешил, но с условиями)
    await db_manager.update_employee_status(employee_id, 'offline')
    
    # 2. Логируем с комментарием СБ
    await db_manager.log_approved_time_event(
        employee_id=employee_id, event_type='clock_out', reason='Изменено СБ',
        approver_id=sb_employee['id'], approval_reason=f"СБ изменил: {text}"
    )
    
    # 3. Закрываем заявку
    request = await db_manager.get_last_pending_request(employee_id, 'early_leave')
    if request:
        await db_manager.update_request_status(request['id'], 'changed_by_sb')

    # 4. Обновляем исходное сообщение в топике
    try:
        await context.bot.edit_message_text(
            chat_id=context.user_data['sb_chat_id'],
            message_id=context.user_data['sb_msg_id'],
            text=f"✏️ Условия изменены СБ ({sb_employee['full_name']}).\nКомментарий: {text}\nСотрудник отпущен."
        )
    except: pass
    
    await update.message.reply_text("✅ Изменения приняты, сотрудник уведомлен.")

    # 5. Уведомляем сотрудника
    target_emp = await db_manager.get_employee_by_id(employee_id)
    if target_emp:
        try:
            await context.bot.send_message(
                chat_id=target_emp['personal_telegram_id'], 
                text=f"⚠️ Ваша заявка изменена СБ.\nКомментарий: {text}\nСмена завершена."
            )
        except: pass
        
    return ConversationHandler.END

# ========== РЕГИСТРАЦИЯ ConversationHandler'ов ==========
admin_conv = ConversationHandler(
    entry_points=[
        CommandHandler("admin", admin_panel),
        MessageHandler(filters.Regex(f"^{BTN_ADMIN_TEXT}$"), admin_panel)
    ],
    states={
        # === УРОВЕНЬ 1: ГЛАВНОЕ МЕНЮ ===
        ADMIN_MAIN_MENU: [
            CallbackQueryHandler(show_employee_card_menu, pattern='^go_to_employee_card_menu$'),
            CallbackQueryHandler(show_schedule_main_menu, pattern='^go_to_schedule_menu$'),
        ],
        
        # === УРОВЕНЬ 2: ПОДМЕНЮ ===
        EMPLOYEE_CARD_MENU: [
            CallbackQueryHandler(start_add_employee, pattern='^admin_add_start$'),
            CallbackQueryHandler(start_select_position, pattern='^admin_edit_start$'), 
            CallbackQueryHandler(admin_panel, pattern='^back_to_admin_panel$'),
        ],
        SCHEDULE_MAIN_MENU: [
            CallbackQueryHandler(start_select_position, pattern='^admin_view_schedule_start$'),
            CallbackQueryHandler(view_all_schedule_start, pattern='^view_all_schedule_start$'),
            CallbackQueryHandler(start_select_position, pattern='^admin_edit_schedule_start$'),
            CallbackQueryHandler(view_absences_start, pattern='^view_absences_start$'),
            CallbackQueryHandler(admin_panel, pattern='^back_to_admin_panel$'),
        ],
        SELECT_POSITION: [
            CallbackQueryHandler(select_employee_by_position, pattern='^sel_pos_'),
            CallbackQueryHandler(show_employee_card_menu, pattern='^go_to_employee_card_menu$'),
            CallbackQueryHandler(show_schedule_main_menu, pattern='^go_to_schedule_menu$'),
        ],
        SELECT_EMPLOYEE_FROM_LIST: [
            CallbackQueryHandler(route_selected_employee, pattern='^sel_emp_'),
            CallbackQueryHandler(start_select_position, pattern='^back_to_positions$'),
        ],
        VIEW_ALL_SCHEDULE_SELECT_PERIOD: [
            CallbackQueryHandler(view_all_schedule_generate, pattern='^all_period_'),
            CallbackQueryHandler(show_schedule_main_menu, pattern='^go_to_schedule_menu$'),
        ],
        
        # === ПОТОК: Добавление сотрудника ===
        ADD_LAST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_last_name)],
        ADD_FIRST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_first_name)],
        ADD_MIDDLE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_middle_name)],
        ADD_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_city)],
        ADD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_phone)],
        
        ADD_POSITION: [CallbackQueryHandler(get_position, pattern='^pos_')],
        AWAITING_CONTACT: [
            MessageHandler(filters.CONTACT, get_contact), 
            MessageHandler(filters.TEXT & ~filters.Regex("^❌ Отмена$"), wrong_input_in_contact_step)
            ],
        ADD_SCHEDULE_PATTERN: [CallbackQueryHandler(get_schedule_pattern, pattern='^sched_')],
        ADD_SCHEDULE_ANCHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_schedule_anchor)],
        ADD_ROLE: [CallbackQueryHandler(get_role, pattern='^role_')],

        ADD_START_TIME: [MessageHandler(filters.Regex(r'^\d{2}:\d{2}$'), get_start_time)],
        ADD_END_TIME: [MessageHandler(filters.Regex(r'^\d{2}:\d{2}$'), get_end_time)],
        
        ADD_EMPLOYEE_MENU: [
            CallbackQueryHandler(select_field_menu, pattern='^action_edit$'), 
            CallbackQueryHandler(confirm_add_employee, pattern='^action_confirm$')
        ],
        SELECT_FIELD: [
            CallbackQueryHandler(request_field_value, pattern='^field_'), 
            CallbackQueryHandler(show_add_employee_menu, pattern='^back_to_menu$')
        ],
        
        GET_FIELD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_field_value)],
        
        AWAITING_ADD_EMPLOYEE_2FA: [MessageHandler(filters.Regex(r'^\d{6}$'), finalize_add_employee)],

        # === ПОТОК: Редактирование карточки ===
        SELECT_EMPLOYEE_TO_EDIT: [
            CallbackQueryHandler(show_employee_edit_menu, pattern='^edit_emp_'),
            CallbackQueryHandler(edit_schedule_selected_employee, pattern='^edit_sched_emp_'),
            CallbackQueryHandler(admin_panel, pattern='^back_to_admin_panel$'),
            CallbackQueryHandler(show_schedule_main_menu, pattern='^go_to_schedule_menu$'),
        ],
        EDIT_MAIN_MENU: [
            CallbackQueryHandler(start_edit_data, pattern='^edit_data_start$'),
            CallbackQueryHandler(start_reset_2fa_confirm, pattern='^reset_2fa_start$'),
            CallbackQueryHandler(start_fire_employee, pattern='^fire_employee_start$'),
            CallbackQueryHandler(start_delete_employee, pattern='^delete_employee_start$'),
            CallbackQueryHandler(start_select_position, pattern='^back_to_positions$'), 
        ],
        EDIT_DATA_SELECT_FIELD: [
            CallbackQueryHandler(request_edit_data_value, pattern='^edit_data_field_'),
            CallbackQueryHandler(show_relatives_menu, pattern='^manage_relatives$'),
            CallbackQueryHandler(show_employee_edit_menu, pattern='^back_to_edit_menu$')
        ],
        RELATIVES_MENU: [
            CallbackQueryHandler(start_add_relative, pattern='^add_new_relative$'),
            CallbackQueryHandler(start_edit_data, pattern='^back_to_fields$'), 
            CallbackQueryHandler(show_relatives_menu, pattern='^manage_relatives$'), 
        ],
        REL_ADD_TYPE: [CallbackQueryHandler(get_rel_type, pattern='^rel_type_')],
        REL_ADD_LAST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_last_name)],
        REL_ADD_FIRST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_first_name)],
        REL_ADD_MIDDLE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_middle_name)],
        REL_ADD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_phone)],
        REL_ADD_BIRTH_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_birth_date)],
        REL_ADD_WORKPLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_workplace)],
        REL_ADD_POSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_position)],
        REL_ADD_REG_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_reg_address)],
        REL_ADD_LIV_ADDRESS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_rel_liv_address),
            CallbackQueryHandler(get_rel_liv_address, pattern='^same_address$')
        ],
        EDIT_DATA_GET_VALUE: [MessageHandler((filters.TEXT | filters.CONTACT) & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_edited_data_value)],
        EDIT_DATA_GET_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), save_data_with_reason)],
        AWAITING_RESET_2FA_CONFIRM: [
            CallbackQueryHandler(finalize_reset_2fa, pattern='^confirm_reset_yes$'), 
            CallbackQueryHandler(show_employee_edit_menu, pattern='^back_to_edit_menu$')
        ],
        
        # === ПОТОК: Изменение графика ===
        SCHEDULE_SELECT_MODE: [
            CallbackQueryHandler(schedule_select_mode, pattern='^sched_mode_'), 
            CallbackQueryHandler(start_select_position, pattern='^back_to_edit_menu$')
        ],
        SCHEDULE_SELECT_DATE_1: [
            CallbackQueryHandler(schedule_select_date_1, pattern='^cal_'), 
            CallbackQueryHandler(schedule_start, pattern='^back_to_schedule_type_select$')
        ],
        SCHEDULE_SELECT_DATE_2: [
            CallbackQueryHandler(schedule_select_date_2, pattern='^cal_'), 
            CallbackQueryHandler(schedule_start, pattern='^back_to_schedule_type_select$')
        ],
        SCHEDULE_SELECT_TYPE: [
            CallbackQueryHandler(schedule_process_type, pattern='^sched_type_'), 
            CallbackQueryHandler(show_employee_edit_menu, pattern='^back_to_edit_menu$')
        ],
        SCHEDULE_GET_START_TIME: [MessageHandler(filters.Regex(r'^\d{2}:\d{2}$'), schedule_get_start_time)],
        SCHEDULE_GET_END_TIME: [MessageHandler(filters.Regex(r'^\d{2}:\d{2}$'), schedule_finalize_work_time)],
        SCHEDULE_CONFIRM_DEAL_MOVE: [
            CallbackQueryHandler(handle_deal_move_confirmation, pattern='^confirm_deal_move_')
        ],
        
        # === ПОТОК: Просмотр графика по сотруднику ===
        VIEW_SCHEDULE_SELECT_EMPLOYEE: [
            CallbackQueryHandler(view_schedule_select_employee, pattern='^view_emp_'),
            CallbackQueryHandler(show_schedule_main_menu, pattern='^back_to_view_list$'),
            CallbackQueryHandler(admin_panel, pattern='^back_to_admin_panel$'),
        ],
        VIEW_SCHEDULE_SELECT_PERIOD: [
            CallbackQueryHandler(view_schedule_generate_report, pattern='^view_period_'),
            CallbackQueryHandler(start_select_position, pattern='^back_to_positions$'),
            CallbackQueryHandler(start_select_position, pattern='^back_to_view_list$'), 
        ],
        VIEW_SCHEDULE_SHOW_REPORT: [
            CallbackQueryHandler(view_schedule_back_to_period_select, pattern='^back_to_period_select$'),
            CallbackQueryHandler(start_select_position, pattern='^back_to_view_list$'), 
            CallbackQueryHandler(admin_panel, pattern='^back_to_admin_panel$'),
        ],
        VIEW_ABSENCES_SELECT_PERIOD: [
            CallbackQueryHandler(view_absences_generate_report, pattern='^abs_period_'),
            CallbackQueryHandler(show_schedule_main_menu, pattern='^go_to_schedule_menu$')
        ],
        VIEW_ABSENCES_SHOW_REPORT: [
            CallbackQueryHandler(show_schedule_main_menu, pattern='^go_to_schedule_menu$')
        ],
        AWAITING_FIRE_EMPLOYEE_2FA: [MessageHandler(filters.Regex(r'^\d{6}$'), finalize_fire_employee)],
        AWAITING_DELETE_EMPLOYEE_2FA: [MessageHandler(filters.Regex(r'^\d{6}$'), finalize_delete_employee)],
    },
    fallbacks=[
        CommandHandler('cancel', admin_cancel),
        MessageHandler(filters.Regex("^❌ Отмена$"), admin_cancel) 
    ],
    per_user=True,
    allow_reentry=True
)

sb_approval_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(sb_approval_start, pattern='^approve_sb_')
    ],
    states={
        AWAITING_SB_2FA: [MessageHandler(filters.Regex(r'^\d{6}$'), sb_approval_2fa)]
    },
    fallbacks=[
        CommandHandler('cancel', admin_cancel),
        MessageHandler(filters.Regex("^❌ Отмена$"), admin_cancel) 
    ],
    per_user=True,
)

sb_action_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(sb_change_time_start, pattern='^change_early_')
    ],
    states={
        SB_CHANGE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, sb_change_time_save)]
    },
    fallbacks=[CommandHandler('cancel', admin_cancel)], 
    per_user=True 
)

admin_handlers = [
    admin_conv,          
    sb_approval_handler,
    sb_action_handler,
    
    CallbackQueryHandler(sb_approve_early_leave, pattern='^approve_early_'),
    CallbackQueryHandler(sb_reject_early_leave, pattern='^reject_early_'),
    CallbackQueryHandler(sb_reject_request, pattern='^reject_sb_')
]