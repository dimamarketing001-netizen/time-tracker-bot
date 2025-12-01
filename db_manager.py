import aiomysql
import logging
from config import DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME
from typing import Optional, Dict, Any, List
from datetime import date, timedelta, time

logger = logging.getLogger(__name__)

pool = None

async def init_pool():
    """Инициализирует ЕДИНСТВЕННЫЙ пул соединений."""
    global pool
    try:
        pool = await aiomysql.create_pool(
            host=DB_HOST, port=DB_PORT,
            user=DB_USER, password=DB_PASS,
            db=DB_NAME, autocommit=True,
            cursorclass=aiomysql.DictCursor
        )
        logger.info("Database connection pool created successfully.")
    except Exception as e:
        logger.error(f"Error creating database connection pool: {e}")
        raise

async def close_pool():
    """Закрывает ЕДИНСТВЕННЫЙ пул соединений."""
    global pool
    if pool:
        pool.close()
        await pool.wait_closed()
        logger.info("Database connection pool closed.")

async def fetch_one(query: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, args)
            return await cursor.fetchone()

async def fetch_all(query: str, args: tuple = ()) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, args)
            return await cursor.fetchall()

async def execute(query: str, args: tuple = ()) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, args)
            return cursor.lastrowid

# --- Employee Functions ---
async def get_employee_by_id(employee_id: int) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM employees WHERE id = %s"
    return await fetch_one(query, (employee_id,))

async def get_employee_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM employees WHERE personal_telegram_id = %s AND termination_date IS NULL"
    return await fetch_one(query, (telegram_id,))

async def update_employee_status(employee_id: int, status: str):
    query = "UPDATE employees SET status = %s, status_change_timestamp = NOW() WHERE id = %s"
    await execute(query, (status, employee_id))
    
    # Если сотрудник вернулся в онлайн, сбрасываем тему с уведомлениями
    if status == 'online':
        await update_employee_topic_id(employee_id, None)

async def set_totp_secret(employee_id: int, secret: str):
    await execute("UPDATE employees SET totp_secret = %s WHERE id = %s", (secret, employee_id))
    
async def update_lateness_alert_date(employee_id: int):
    await execute("UPDATE employees SET last_lateness_alert_date = CURDATE() WHERE id = %s", (employee_id,))

async def update_employee_topic_id(employee_id: int, topic_id: Optional[int]):
    await execute("UPDATE employees SET current_alert_topic_id = %s WHERE id = %s", (topic_id, employee_id))

async def reset_all_topic_ids():
    await execute("UPDATE employees SET current_alert_topic_id = NULL WHERE current_alert_topic_id IS NOT NULL")

# --- Time Log Functions ---
async def log_time_event(employee_id: int, event_type: str, reason: Optional[str] = None):
    query = "INSERT INTO time_log (employee_id, event_type, reason, timestamp) VALUES (%s, %s, %s, NOW())"
    await execute(query, (employee_id, event_type, reason))

async def log_approved_time_event(employee_id: int, event_type: str, reason: str, approver_id: int, approval_reason: str):
    query = "INSERT INTO time_log (employee_id, event_type, reason, timestamp, approver_id, approval_reason) VALUES (%s, %s, %s, NOW(), %s, %s)"
    await execute(query, (employee_id, event_type, reason, approver_id, approval_reason))

async def get_today_event_count(employee_id: int, reason: str) -> int:
    query = "SELECT COUNT(*) as count FROM time_log WHERE employee_id = %s AND reason = %s AND DATE(timestamp) = CURDATE()"
    result = await fetch_one(query, (employee_id, reason))
    return result['count'] if result else 0

async def has_clocked_in_today(employee_id: int) -> bool:
    query = "SELECT 1 FROM time_log WHERE employee_id = %s AND event_type = 'clock_in' AND DATE(timestamp) = CURDATE()"
    return await fetch_one(query, (employee_id,)) is not None

# --- Schedule Functions ---
async def get_employees_for_lateness_check() -> List[Dict[str, Any]]:
    query = """
        SELECT e.id, e.full_name, e.position, e.default_start_time, e.status, so.start_time AS override_start_time, so.is_day_off
        FROM employees e
        LEFT JOIN schedule_overrides so ON e.id = so.employee_id AND so.work_date = CURDATE()
        WHERE e.termination_date IS NULL
        AND (e.last_lateness_alert_date IS NULL OR e.last_lateness_alert_date != CURDATE())
        AND e.status = 'offline'
    """
    return await fetch_all(query)

async def get_employees_on_break() -> List[Dict[str, Any]]:
    # --- ИСПРАВЛЕНИЕ: Добавляем current_alert_topic_id ---
    query = "SELECT id, full_name, personal_telegram_id, status, status_change_timestamp, current_alert_topic_id FROM employees WHERE status IN ('on_break', 'on_lunch')"
    return await fetch_all(query)

async def get_active_employees_for_reset() -> List[Dict[str, Any]]:
    return await fetch_all("SELECT id FROM employees WHERE status != 'offline' AND termination_date IS NULL")

# --- Deals Table Functions ---
async def check_conflicting_deals(employee_id: int, time_window_minutes: int) -> List[Dict[str, Any]]:
    query = """
        SELECT deals_id, direction, action, amount_to_get, currency_to_get, amount_to_give, currency_to_give, status, datetime_meeting
        FROM CryptoDeals
        WHERE employee_id = %s
          AND status != 'closed'
          AND datetime_meeting BETWEEN NOW() AND NOW() + INTERVAL %s MINUTE
    """

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (employee_id, time_window_minutes))
            return await cursor.fetchall()
        
async def add_employee(employee_data: dict) -> int:
    """Гибко добавляет нового сотрудника, включая необязательные поля."""
    fields = []
    values = []
    
    # Собираем поля и значения из словаря
    for key, value in employee_data.items():
        if value is not None:
            fields.append(f"`{key}`")
            values.append(value)
    
    # Добавляем дату найма
    fields.append("`hire_date`")
    values.append("CURDATE()") # Используем SQL-функцию

    fields_str = ", ".join(fields)
    placeholders = ", ".join(["%s"] * (len(values) - 1)) + (", CURDATE()" if "CURDATE()" in values else "")
    
    # Фикс для правильного формирования плейсхолдеров
    placeholders_list = []
    args_list = []
    for val in values:
        if val == "CURDATE()":
            placeholders_list.append(val)
        else:
            placeholders_list.append("%s")
            args_list.append(val)
    
    placeholders = ", ".join(placeholders_list)

    query = f"INSERT INTO employees ({fields_str}) VALUES ({placeholders})"
    
    return await execute(query, tuple(args_list))


async def update_employee_field(employee_id: int, field: str, value: Any):
    """Безопасно обновляет одно поле для указанного сотрудника."""
    allowed_fields = [
        'last_name', 'first_name', 'middle_name',
        'position', 'personal_phone', 'work_phone', 'city',
        'role', 'schedule_pattern', 'default_start_time', 'default_end_time'
    ]
    if field not in allowed_fields:
        raise ValueError(f"Field '{field}' is not allowed for update.")

    query = f"UPDATE employees SET `{field}` = %s WHERE id = %s"
    await execute(query, (value, employee_id))

async def sync_employee_full_name(employee_id: int):
    """
    Принудительно обновляет поле full_name на основе last_name, first_name, middle_name.
    Используется после редактирования частей имени.
    """
    query = """
        UPDATE employees 
        SET full_name = TRIM(CONCAT(IFNULL(last_name, ''), ' ', IFNULL(first_name, ''), ' ', IFNULL(middle_name, '')))
        WHERE id = %s
    """
    await execute(query, (employee_id,))
    
async def find_employee_by_field(field: str, value: Any) -> Optional[Dict[str, Any]]:
    """
    Ищет сотрудника по заданному полю и значению.
    Использует белый список полей для безопасности.
    """
    # Белый список разрешенных для поиска полей
    allowed_fields = ['personal_phone', 'work_phone', 'personal_telegram_id']
    if field not in allowed_fields:
        logger.error(f"Attempted to search by a non-allowed field: {field}")
        return None # Или можно вызвать исключение ValueError

    query = f"SELECT id, full_name FROM employees WHERE `{field}` = %s AND termination_date IS NULL"
    return await fetch_one(query, (value,))

async def get_all_employees() -> List[Dict[str, Any]]:
    """Возвращает список всех не уволенных сотрудников."""
    query = "SELECT id, full_name, position FROM employees WHERE termination_date IS NULL ORDER BY full_name"
    return await fetch_all(query)

async def set_schedule_override(employee_id: int, work_date: str, is_day_off: bool, start_time: str = None, end_time: str = None):
    """Устанавливает или обновляет исключение в графике."""
    # --- ИСПРАВЛЕНИЕ: VALUES() заменено на современный синтаксис с псевдонимом ---
    query = """
        INSERT INTO schedule_overrides (employee_id, work_date, is_day_off, start_time, end_time)
        VALUES (%s, %s, %s, %s, %s) AS new_values
        ON DUPLICATE KEY UPDATE
        is_day_off = new_values.is_day_off, 
        start_time = new_values.start_time, 
        end_time = new_values.end_time
    """
    await execute(query, (employee_id, work_date, is_day_off, start_time, end_time))

async def log_employee_change(admin_id: int, employee_id: int, field: str, old_value: Any, new_value: Any, reason: str):
    """Записывает изменение данных сотрудника в лог аудита."""
    query = """
        INSERT INTO employee_audit_log (admin_id, employee_id, field_changed, old_value, new_value, reason, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
    """
    # Преобразуем значения в строки для универсального хранения
    old_value_str = str(old_value) if old_value is not None else 'NULL'
    new_value_str = str(new_value) if new_value is not None else 'NULL'
    
    await execute(query, (admin_id, employee_id, field, old_value_str, new_value_str, reason))

async def set_schedule_override_for_period(employee_id: int, start_date_str: str, end_date_str: str, is_day_off: bool, start_time: str = None, end_time: str = None):
    """Устанавливает или обновляет исключения в графике для целого периода дат."""
    
    start_date = date.fromisoformat(start_date_str)
    end_date = date.fromisoformat(end_date_str)
    delta = end_date - start_date
    
    # Собираем все даты в диапазоне
    dates_to_update = [(start_date + timedelta(days=i)).isoformat() for i in range(delta.days + 1)]
    
    # Готовим один большой запрос для эффективности
    query = """
        INSERT INTO schedule_overrides (employee_id, work_date, is_day_off, start_time, end_time)
        VALUES (%s, %s, %s, %s, %s) AS new_values
        ON DUPLICATE KEY UPDATE
        is_day_off = new_values.is_day_off, 
        start_time = new_values.start_time, 
        end_time = new_values.end_time
    """
    
    # Создаем список кортежей с данными для каждой даты
    args_list = [
        (employee_id, d, is_day_off, start_time, end_time) for d in dates_to_update
    ]
    
    # Выполняем множество вставок/обновлений за один раз
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.executemany(query, args_list)

async def get_employee_schedule_for_period(employee_id: int, start_date: date, end_date: date) -> List[Dict[str, Any]]:
    """
    Собирает полный график сотрудника на заданный период,
    учитывая базовый график (5/2, 2/2 и т.д.) и все исключения.
    """
    employee = await get_employee_by_id(employee_id)
    if not employee:
        return []

    # 1. Получаем все исключения для этого сотрудника в заданном диапазоне
    query = "SELECT * FROM schedule_overrides WHERE employee_id = %s AND work_date BETWEEN %s AND %s"
    overrides_list = await fetch_all(query, (employee_id, start_date, end_date))
    overrides = {ov['work_date'].isoformat(): ov for ov in overrides_list}

    # 2. Определяем рабочие и выходные дни по-умолчанию
    schedule_pattern = employee.get('schedule_pattern', '5/2')
    work_days, off_days = map(int, schedule_pattern.split('/'))
    
    # 0 = Понедельник, 6 = Воскресенье
    if schedule_pattern == '5/2':
        default_weekend_days = [5, 6] # СБ, ВС
    elif schedule_pattern == '6/1':
        default_weekend_days = [6] # ВС
    elif schedule_pattern == '7/0':
        default_weekend_days = [] # Нет выходных
    # График 2/2 требует более сложной логики с точкой отсчета, для простоты реализуем его как 5/2
    else: # 2/2 и другие
        default_weekend_days = [5, 6] 

    final_schedule = []
    current_date = start_date
    while current_date <= end_date:
        day_info = {'date': current_date}
        date_str = current_date.isoformat()

        # 3. Проверяем, есть ли исключение для этого дня
        if date_str in overrides:
            override = overrides[date_str]
            if override['is_day_off']:
                day_info['status'] = 'Отгул/Больничный'
                day_info['start_time'] = None
                day_info['end_time'] = None
            else: # Рабочий день с измененным временем
                day_info['status'] = 'Работа'
                day_info['start_time'] = override['start_time']
                day_info['end_time'] = override['end_time']
        else:
            # 4. Если исключения нет, применяем базовый график
            if current_date.weekday() in default_weekend_days:
                day_info['status'] = 'Выходной'
                day_info['start_time'] = None
                day_info['end_time'] = None
            else:
                day_info['status'] = 'Работа'
                day_info['start_time'] = employee['default_start_time']
                day_info['end_time'] = employee['default_end_time']
        
        final_schedule.append(day_info)
        current_date += timedelta(days=1)
        
    return final_schedule

# Файл: db_manager.py

async def get_all_schedule_overrides_for_period(start_date: date, end_date: date) -> List[Dict[str, Any]]:
    """
    Возвращает все исключения из графика для всех сотрудников за указанный период,
    объединяя с информацией о сотруднике.
    """
    query = """
        SELECT 
            so.work_date,
            so.is_day_off,
            so.start_time,
            so.end_time,
            e.full_name
        FROM schedule_overrides so
        JOIN employees e ON so.employee_id = e.id
        WHERE so.work_date BETWEEN %s AND %s
        ORDER BY e.full_name, so.work_date
    """
    return await fetch_all(query, (start_date, end_date))

# Файл: db_manager.py

async def find_conflicting_deals_for_schedule(employee_id: int, start_date_str: str, end_date_str: str, work_start_time_str: str = None, work_end_time_str: str = None) -> List[Dict[str, Any]]:
    """
    Ищет сделки, которые попадают в нерабочее время в указанном диапазоне дат.
    Если время не указано, ищет любые сделки в эти дни (т.к. они считаются выходными).
    """
    
    # Если время указано, ищем сделки, которые находятся ВНЕ этого рабочего интервала
    if work_start_time_str and work_end_time_str:
        time_condition = "AND (TIME(datetime_meeting) < %s OR TIME(datetime_meeting) > %s)"
        args = (employee_id, start_date_str, end_date_str, work_start_time_str, work_end_time_str)
    else: # Если время не указано, значит это выходные, и любая сделка в эти дни - конфликт
        time_condition = ""
        args = (employee_id, start_date_str, end_date_str)
        
    query = f"""
        SELECT deals_id, datetime_meeting
        FROM CryptoDeals
        WHERE employee_id = %s
          AND status != 'closed'
          AND DATE(datetime_meeting) BETWEEN %s AND %s
          {time_condition}
        ORDER BY datetime_meeting
    """

    return await fetch_all(query, args)