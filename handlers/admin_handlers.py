import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from utils import security_required, verify_totp, get_main_keyboard
import db_manager as db_manager
from telegram.helpers import escape_markdown
import calendar_helper
from datetime import date, timedelta
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove

logger = logging.getLogger(__name__)

BTN_ADMIN_TEXT = "🔐 Админка"

# --- Константы состояний ---
# Главное меню
ADMIN_MAIN_MENU = 0
# --- ЕДИНЫЙ БЛОК СОСТОЯНИЙ ДЛЯ ВСЕЙ АДМИН-ПАНЕЛИ ---
(
    # Меню
    ADMIN_MAIN_MENU,             # 0
    EMPLOYEE_CARD_MENU,          # 1
    SCHEDULE_MAIN_MENU,          # 2

    # Поток добавления сотрудника
    ADD_LAST_NAME, ADD_FIRST_NAME, ADD_MIDDLE_NAME, ADD_CITY, ADD_PHONE, ADD_POSITION, AWAITING_CONTACT, ADD_SCHEDULE_PATTERN, ADD_ROLE,
    ADD_START_TIME, ADD_END_TIME, ADD_EMPLOYEE_MENU, SELECT_FIELD, GET_FIELD_VALUE,
    AWAITING_ADD_EMPLOYEE_2FA,   # 3-13

    # Поток редактирования сотрудника
    SELECT_EMPLOYEE_TO_EDIT, EDIT_MAIN_MENU, EDIT_DATA_SELECT_FIELD,
    EDIT_DATA_GET_VALUE, EDIT_DATA_GET_REASON, AWAITING_RESET_2FA_CONFIRM, # 14-19

    # Поток изменения графика
    SCHEDULE_SELECT_MODE, SCHEDULE_SELECT_TYPE, SCHEDULE_SELECT_DATE_1,
    SCHEDULE_SELECT_DATE_2, SCHEDULE_GET_START_TIME, SCHEDULE_GET_END_TIME, # 20-25
    
    # Поток просмотра графика по сотруднику
    VIEW_SCHEDULE_SELECT_EMPLOYEE, VIEW_SCHEDULE_SELECT_PERIOD, VIEW_SCHEDULE_SHOW_REPORT, # 26-28
    
    # Поток просмотра отгулов
    VIEW_ABSENCES_SELECT_PERIOD, # 29
    VIEW_ABSENCES_SHOW_REPORT,   # 31

    SCHEDULE_CONFIRM_DEAL_MOVE,

    # Состояние для СБ
    AWAITING_SB_2FA, 

    # Родственники сотрудника
    RELATIVES_MENU, REL_ADD_TYPE, REL_ADD_LAST_NAME, REL_ADD_FIRST_NAME, REL_ADD_MIDDLE_NAME, REL_ADD_PHONE, REL_ADD_BIRTH_DATE, REL_ADD_WORKPLACE,
    REL_ADD_POSITION, REL_ADD_REG_ADDRESS, REL_ADD_LIV_ADDRESS,

    AWAITING_FIRE_EMPLOYEE_2FA,
    AWAITING_DELETE_EMPLOYEE_2FA,
) = range(50)


# ========== СЛОВАРИ И ВСПОМОГАТЕЛЬНЫЕ ДАННЫЕ ==========
EDITABLE_FIELDS = {
    'last_name': 'Фамилия', 
    'first_name': 'Имя', 
    'middle_name': 'Отчество',
    'position': 'Должность',
    'personal_phone': 'Личный телефон', 'work_phone': 'Рабочий телефон',
    'city': 'Город', 'role': 'Роль',
    'schedule_pattern': 'График работы (5/2, 2/2)',
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
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Панель администратора:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Панель администратора:", reply_markup=reply_markup)
        
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
        [InlineKeyboardButton("📊 Посмотреть график по сотруднику", callback_data='admin_view_schedule_start')],
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
    """Отменяет админское действие и возвращает главное меню."""
    context.user_data.clear()
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    role = employee.get('role', 'employee') if employee else 'employee'
    
    await update.message.reply_text("Действие отменено. Возврат в главное меню.", reply_markup=get_main_keyboard(role))
    return ConversationHandler.END

async def start_add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['new_employee'] = {}

    cancel_kb = ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)
    
    await query.message.reply_text("Начинаем добавление нового сотрудника.\nВведите **Фамилию** (или нажмите '❌ Отмена' для выхода):", 
                                   reply_markup=cancel_kb, 
                                   parse_mode='Markdown')
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

async def get_schedule_pattern(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pattern = query.data.split('_', 1)[1]
    context.user_data['new_employee']['schedule_pattern'] = pattern
    keyboard = [
        [InlineKeyboardButton("Admin", callback_data='role_Admin')],
        [InlineKeyboardButton("Security", callback_data='role_Security')],
        [InlineKeyboardButton("Employee", callback_data='role_Employee')],
    ]
    await query.edit_message_text(f"График '{pattern}' установлен. Теперь выберите роль:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_ROLE
    
async def get_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['new_employee']['role'] = query.data.split('_', 1)[1]
    
    reply_keyboard = [["09:00", "11:00", "13:00"]]
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
    
    reply_keyboard = [["18:00", "21:00", "23:00"]]
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
    text_parts = ["*Данные для добавления:*\n"]
    for key, value in employee_data.items():
        field_name = EDITABLE_FIELDS.get(key, key.replace('_', ' ').capitalize())
        text_parts.append(f"{field_name}: {value}")
    text = "\n".join(text_parts) + "\n\nВыберите дальнейшее действие."
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
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
        reply_keyboard = [["09:00", "11:00", "13:00"]]
    elif field == 'default_end_time':
        reply_keyboard = [["18:00", "21:00", "23:00"]]
        
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

    keyboard.append([InlineKeyboardButton("⬅️ Назад к списку сотрудников", callback_data="back_to_employee_list")])
    
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
    if query: await query.answer()

    employee_id = context.user_data['employee_to_edit_id']
    employee = await db_manager.get_employee_by_id(employee_id)

    buttons = []
    for field, name in EDITABLE_FIELDS.items():
        # Исключаем старые поля relatives, если они остались в словаре
        if 'relative' not in field: 
            buttons.append([InlineKeyboardButton(name, callback_data=f"edit_data_field_{field}")])
    
    buttons.insert(0, [InlineKeyboardButton("👨‍👩‍👧 Управление родственниками", callback_data='manage_relatives')])

    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data='back_to_edit_menu')])

    text = f"Редактирование данных: *{employee['full_name']}*\nВыберите поле:"
    
    reply_markup = InlineKeyboardMarkup(buttons)
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    return EDIT_DATA_SELECT_FIELD

async def request_edit_data_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрашивает новое значение для выбранного поля."""
    query = update.callback_query
    await query.answer()
    field = query.data.split('_', 3)[3]
    context.user_data['current_edit_field'] = field
    
    reply_keyboard = None
    message_text = f"Введите новое значение для поля '{EDITABLE_FIELDS[field]}':"

    if field == 'default_start_time':
        reply_keyboard = [["09:00", "11:00", "13:00"]]
    elif field == 'default_end_time':
        reply_keyboard = [["18:00", "21:00", "23:00"]]
        
    await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup([])) # Убираем инлайн-клавиатуру
    if reply_keyboard:
        # Отправляем обычную клавиатуру для выбора
        await query.message.reply_text(
            "Варианты:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        
    return EDIT_DATA_GET_VALUE

async def get_edited_data_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает новое значение и запрашивает причину изменения."""
    field = context.user_data['current_edit_field']
    value = update.message.text
    employee_id = context.user_data['employee_to_edit_id']
    
    unique_fields = ['personal_phone', 'work_phone']
    if field in unique_fields:
        existing_employee = await db_manager.find_employee_by_field(field, value)
        if existing_employee and existing_employee['id'] != employee_id:
            await update.message.reply_text(f"❌ **Дубликат!** ...\nВведите другое.")
            return EDIT_DATA_GET_VALUE
    
    context.user_data['new_field_value'] = value
    
    # Убираем клавиатуру с вариантами времени перед запросом причины
    await update.message.reply_text(
        "Значение принято. Теперь введите краткую причину изменения (например, 'Сотрудник сменил номер').",
        reply_markup=ReplyKeyboardRemove()
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

    try:
        # Получаем старое значение
        employee = await db_manager.get_employee_by_id(employee_id)
        old_value = employee.get(field)

        # Обновляем поле
        await db_manager.update_employee_field(employee_id, field, new_value)
        
        # --- СИНХРОНИЗАЦИЯ FULL_NAME ---
        # Если изменили часть имени, нужно пересобрать full_name в БД
        if field in ['last_name', 'first_name', 'middle_name']:
            await db_manager.sync_employee_full_name(employee_id)

        # Лог аудита
        await db_manager.log_employee_change(admin_id_for_log, employee_id, field, old_value, new_value, reason)

        await update.message.reply_text(f"✅ Поле '{EDITABLE_FIELDS.get(field, field)}' успешно обновлено.")
    except Exception as e:
        logger.error(f"Edit error: {e}")
        await update.message.reply_text(f"❌ Ошибка при сохранении: {e}")

    return await start_edit_data(update, context)

# --- ЛОГИКА ИЗМЕНЕНИЯ ГРАФИКА ---
async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 1: Выбор режима (одна дата / период)."""
    query = update.callback_query
    await query.answer()

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
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_edit_menu')],
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
        reply_keyboard = [["09:00", "10:00", "11:00"]]
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
        employee_id = context.user_data['employee_to_edit_id']
        employee = await db_manager.get_employee_by_id(employee_id)
        await db_manager.set_totp_secret(employee_id, None)
        await query.edit_message_text(f"✅ 2FA для сотрудника *{employee['full_name']}* успешно сброшен.")
    else: # отмена
        await query.edit_message_text("Сброс 2FA отменен.")
    
    context.user_data.clear()
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

# Файл: handlers/admin_handlers.py

async def view_schedule_generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Генерация и отправка отчета с кнопками навигации."""
    query = update.callback_query
    await query.answer("Формирую отчет...")
    
    period = query.data.split('_')[2]
    employee_id = context.user_data['view_employee_id']
    employee = await db_manager.get_employee_by_id(employee_id)
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
        f"График работы: {employee['full_name']}\n"
        f"Период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}\n\n"
    )
    table = "```\n"
    table += "| Дата      | День | Время         | Статус          |\n"
    table += "|-----------|------|---------------|-----------------|\n"
    
    for day in schedule_data:
        dt = day['date']
        date_str = dt.strftime('%d.%m.%y')
        weekday_str = WEEKDAY_NAMES_RU[dt.weekday()]
        
        start_t = day['start_time']
        end_t = day['end_time']
        if start_t and isinstance(start_t, timedelta): start_t = str(start_t)[:-3]
        if end_t and isinstance(end_t, timedelta): end_t = str(end_t)[:-3]

        time_str = f"{start_t or '--:--'} - {end_t or '--:--'}"
        status_str = day['status']
        
        table += f"| {date_str:<9} | {weekday_str:<4} | {time_str:<13} | {status_str:<15} |\n"
        
    table += "```"
    
    # --- КЛАВИАТУРА ДЛЯ НАВИГАЦИИ ---
    keyboard = [
        [InlineKeyboardButton("⬅️ Другой период", callback_data='back_to_period_select')],
        [InlineKeyboardButton("👤 Другой сотрудник", callback_data='back_to_view_list')],
        [InlineKeyboardButton("🏠 В главное меню", callback_data='back_to_admin_panel')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(header + table, reply_markup=reply_markup, parse_mode='Markdown')
    
    # Переходим в состояние ожидания нажатия навигационной кнопки
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
    """Генерация отчета по отгулам в виде таблицы."""
    query = update.callback_query
    await query.answer("Формирую отчет...")
    
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
    
    overrides_data = await db_manager.get_all_schedule_overrides_for_period(start_date, end_date)
    
    if not overrides_data:
        await query.edit_message_text(
            f"За период с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')} изменений в графиках не найдено.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')]])
        )
        return VIEW_ABSENCES_SHOW_REPORT

    # Группировка по сотруднику
    report_by_employee = {}
    for row in overrides_data:
        if row['full_name'] not in report_by_employee:
            report_by_employee[row['full_name']] = []
        report_by_employee[row['full_name']].append(row)
        
    report_text = f"*Отчет по изменениям в графике*\n*Период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}*\n\n"
    
    for name, records in report_by_employee.items():
        report_text += f"👤 *{escape_markdown(name)}*\n"
        table = "```\n"
        table += "| Дата      | День | Статус/Время      |\n"
        table += "|-----------|------|-------------------|\n"

        for record in records:
            dt = record['work_date']
            date_str = dt.strftime('%d.%m.%y')
            weekday_str = WEEKDAY_NAMES_RU[dt.weekday()]
            
            status_str = ""
            if record['is_day_off']:
                status_str = "Отгул/Больничный"
            else:
                start_t = record['start_time']
                end_t = record['end_time']
                # Обработка формата времени из БД
                if isinstance(start_t, timedelta): start_t = str(start_t)[:-3]
                if isinstance(end_t, timedelta): end_t = str(end_t)[:-3]
                status_str = f"Время: {start_t}-{end_t}"

            table += f"| {date_str:<9} | {weekday_str:<4} | {status_str:<17} |\n"
            
        table += "```\n"
        report_text += table

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data='go_to_schedule_menu')]]
    
    await query.edit_message_text(report_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return VIEW_ABSENCES_SHOW_REPORT

async def start_fire_employee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
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
            # Логируем действие
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
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка БД при удалении: {e}", reply_markup=get_main_keyboard(role))
            
        context.user_data.clear()
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный код 2FA. Попробуйте снова.", reply_markup=get_main_keyboard(role))
        return AWAITING_DELETE_EMPLOYEE_2FA
    
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
            CallbackQueryHandler(start_edit_employee, pattern='^admin_edit_start$'),
            CallbackQueryHandler(admin_panel, pattern='^back_to_admin_panel$'),
        ],
        SCHEDULE_MAIN_MENU: [
            CallbackQueryHandler(view_schedule_start, pattern='^admin_view_schedule_start$'),
            CallbackQueryHandler(edit_schedule_start_select_employee, pattern='^admin_edit_schedule_start$'),
            CallbackQueryHandler(view_absences_start, pattern='^view_absences_start$'),
            CallbackQueryHandler(admin_panel, pattern='^back_to_admin_panel$'),
        ],
        
        # === ПОТОК: Добавление сотрудника ===
        ADD_LAST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_last_name)],
        ADD_FIRST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_first_name)],
        ADD_MIDDLE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_middle_name)],
        ADD_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_city)],
        ADD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_phone)],
        
        ADD_POSITION: [CallbackQueryHandler(get_position, pattern='^pos_')],
        AWAITING_CONTACT: [MessageHandler(filters.CONTACT, get_contact), MessageHandler(filters.TEXT & ~filters.Regex("^❌ Отмена$"), wrong_input_in_contact_step)],
        ADD_SCHEDULE_PATTERN: [CallbackQueryHandler(get_schedule_pattern, pattern='^sched_')],
        ADD_ROLE: [CallbackQueryHandler(get_role, pattern='^role_')],

        ADD_START_TIME: [MessageHandler(filters.Regex(r'^\d{2}:\d{2}$'), get_start_time)],
        ADD_END_TIME: [MessageHandler(filters.Regex(r'^\d{2}:\d{2}$'), get_end_time)],
        
        ADD_EMPLOYEE_MENU: [CallbackQueryHandler(select_field_menu, pattern='^action_edit$'), CallbackQueryHandler(confirm_add_employee, pattern='^action_confirm$')],
        SELECT_FIELD: [CallbackQueryHandler(request_field_value, pattern='^field_'), CallbackQueryHandler(show_add_employee_menu, pattern='^back_to_menu$')],
        
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
            CallbackQueryHandler(start_edit_employee, pattern='^back_to_employee_list$'),
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
        EDIT_DATA_GET_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), get_edited_data_value)],
        EDIT_DATA_GET_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"), save_data_with_reason)],
        AWAITING_RESET_2FA_CONFIRM: [CallbackQueryHandler(finalize_reset_2fa, pattern='^confirm_reset_yes$'), CallbackQueryHandler(show_employee_edit_menu, pattern='^back_to_edit_menu$')],
        
        # === ПОТОК: Изменение графика ===
        SCHEDULE_SELECT_MODE: [CallbackQueryHandler(schedule_select_mode, pattern='^sched_mode_'), CallbackQueryHandler(show_employee_edit_menu, pattern='^back_to_edit_menu$')],
        SCHEDULE_SELECT_DATE_1: [CallbackQueryHandler(schedule_select_date_1, pattern='^cal_'), CallbackQueryHandler(schedule_start, pattern='^back_to_schedule_type_select$')],
        SCHEDULE_SELECT_DATE_2: [CallbackQueryHandler(schedule_select_date_2, pattern='^cal_'), CallbackQueryHandler(schedule_start, pattern='^back_to_schedule_type_select$')],
        SCHEDULE_SELECT_TYPE: [CallbackQueryHandler(schedule_process_type, pattern='^sched_type_'), CallbackQueryHandler(show_employee_edit_menu, pattern='^back_to_edit_menu$')],
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
            CallbackQueryHandler(view_schedule_start, pattern='^back_to_view_list$'),
        ],
        VIEW_SCHEDULE_SHOW_REPORT: [
            CallbackQueryHandler(view_schedule_back_to_period_select, pattern='^back_to_period_select$'),
            CallbackQueryHandler(view_schedule_start, pattern='^back_to_view_list$'),
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

admin_handlers = [
    admin_conv,
    sb_approval_handler,
    CallbackQueryHandler(sb_reject_request, pattern='^reject_sb_')
]
