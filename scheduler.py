import logging
from datetime import datetime, time, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, ContextTypes
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
import db_manager
import config
import pytz

logger = logging.getLogger(__name__)

# Определяем часовой пояс UTC+5 (Екатеринбург)
# Вы можете заменить на 'Asia/Tashkent' или другой город в UTC+5, если нужно
TARGET_TIMEZONE = pytz.timezone('Asia/Yekaterinburg')

async def check_lateness_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running lateness check job...")
    
    # Получаем сотрудников для проверки
    employees = await db_manager.fetch_all("""
        SELECT id, full_name, position, default_start_time, status, 
               schedule_pattern, schedule_start_date, hire_date, last_lateness_alert_date
        FROM employees 
        WHERE termination_date IS NULL 
          AND status = 'offline'
          AND (last_lateness_alert_date IS NULL OR last_lateness_alert_date != CURDATE())
    """)
    
    # Получаем текущее время в нужном часовом поясе
    now = datetime.now(TARGET_TIMEZONE)
    today_date = now.date()

    for emp in employees:
        try:
            # 1. Проверяем график на СЕГОДНЯ
            schedule_info_list = await db_manager.get_employee_schedule_for_period(emp['id'], today_date, today_date)
            
            if not schedule_info_list:
                continue
                
            today_schedule = schedule_info_list[0]
            
            # Если выходной - пропускаем
            if today_schedule['status'] in ['Выходной', 'Отгул/Больничный']:
                continue
                
            start_time_val = today_schedule['start_time']
            if not start_time_val:
                continue

            # Приведение типов времени
            start_time = None
            if isinstance(start_time_val, timedelta):
                total_seconds = int(start_time_val.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                start_time = time(hour=hours, minute=minutes)
            elif isinstance(start_time_val, str):
                try:
                    start_time = datetime.strptime(start_time_val, '%H:%M:%S').time()
                except ValueError:
                    start_time = datetime.strptime(start_time_val, '%H:%M').time()
            elif isinstance(start_time_val, time):
                start_time = start_time_val

            if not start_time:
                continue

            # 2. Сравниваем время
            # Создаем planned_start_dt в том же часовом поясе, что и now
            planned_start_dt = now.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
            
            grace_period = timedelta(minutes=config.LATENESS_GRACE_PERIOD_MIN)
            
            if now > planned_start_dt + grace_period:
                await send_lateness_alert(context, emp, start_time)
                
        except Exception as e:
            logger.error(f"Error checking lateness for {emp.get('full_name')}: {e}")

async def send_lateness_alert(context, emp, start_time):
    try:
        now_str = datetime.now(TARGET_TIMEZONE).strftime('%d.%m.%Y')
        topic_name = f"Опоздание: {emp['full_name']} {now_str}"
        
        thread_id = None
        try:
            topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
            thread_id = topic.message_thread_id
        except Exception as e:
            logger.error(f"Could not create topic: {e}")

        full_name_escaped = escape_markdown(emp['full_name'], version=2)
        position_escaped = escape_markdown(emp.get('position') or 'Не указана', version=2)
        time_str = escape_markdown(start_time.strftime('%H:%M'), version=2)

        message = (
            f"⚠️ *ОПОЗДАНИЕ\\!*\n\n"
            f"Сотрудник: *{full_name_escaped}*\n"
            f"Должность: {position_escaped}\n"
            f"Плановое начало: {time_str}"
        )

        await context.bot.send_message(
            chat_id=config.SECURITY_CHAT_ID,
            text=message,
            message_thread_id=thread_id,
            parse_mode='MarkdownV2'
        )
        
        await db_manager.update_lateness_alert_date(emp['id'])
        logger.warning(f"Lateness alert sent for {emp['full_name']}")
        
    except BadRequest as e:
        logger.error(f"Failed to send lateness alert for {emp['full_name']}: {e}")

async def check_overdue_breaks_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running overdue breaks check job...")
    employees = await db_manager.get_employees_on_break()
    
    # Текущее время в UTC+5
    now = datetime.now(TARGET_TIMEZONE)

    for emp in employees:
        if not emp['status_change_timestamp']:
            continue
        
        # Важно: timestamp из БД обычно наивный (без часового пояса).
        # Если БД в UTC, нужно приводить к UTC+5. 
        # Предположим, что БД хранит локальное время сервера.
        # Для корректного вычитания сделаем now наивным или timestamp aware.
        # Самый надежный способ - привести оба к UTC или оба к локальному без info.
        
        # Получаем время изменения статуса (оно из БД, скорее всего naive)
        status_time = emp['status_change_timestamp']
        
        # Если status_time наивное, считаем что оно было записано в том же часовом поясе, что и сервер
        # Для корректного сравнения приведем now к naive (уберем инфо о зоне), но оставим само время UTC+5
        # ВНИМАНИЕ: Это зависит от того, как настроена БД. Если в БД время по Москве, а тут +5, будет сдвиг.
        # Лучше всего работать с aware objects.
        
        # Простой вариант: считаем разницу в секундах, игнорируя зоны, если серверное время совпадает.
        # Но раз мы меняем зону, лучше сделать так:
        
        # Превращаем время из БД (которое скорее всего системное) в aware, если оно naive
        if status_time.tzinfo is None:
             # Предполагаем, что в БД пишется время сервера. 
             # Если сервер в UTC, то status_time - это UTC.
             # Если сервер в MSK, то MSK.
             # Давайте считать разницу относительно datetime.now() без зоны, так надежнее,
             # так как `status_change_timestamp` ставится SQL функцией NOW().
             pass

        # Чтобы не путаться с зонами БД:
        # Просто берем текущее время сервера (без zones) и сравниваем с БД.
        # Разница во времени (delta) будет одинаковой в любой зоне.
        now_naive = datetime.now() 
        time_since_change = now_naive - status_time
        
        limit, status_name = (config.BREAK_DURATION_MIN, "перерыв") if emp['status'] == 'on_break' else (config.LUNCH_DURATION_MIN, "обед")
        topic_id = emp.get('current_alert_topic_id')
        
        if time_since_change > timedelta(minutes=limit):
            try:
                message_thread_id = topic_id
                if not topic_id:
                    now_str_fmt = now.strftime('%d.%m %H:%M') # Используем время UTC+5 для красоты
                    topic_name = f"Превышение {status_name}а: {emp['full_name']} {now_str_fmt}"
                    try:
                        topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
                        message_thread_id = topic.message_thread_id
                        await db_manager.update_employee_topic_id(emp['id'], message_thread_id)
                    except Exception as e:
                        logger.error(f"Topic error: {e}")
                        message_thread_id = None

                try:
                    await context.bot.send_message(
                        chat_id=emp['personal_telegram_id'],
                        text=f"❗️Внимание! Ваш {status_name} превысил {limit} минут. Пожалуйста, вернитесь к работе."
                    )
                except Exception:
                    pass

                full_name_escaped = escape_markdown(emp['full_name'], version=2)
                overdue_min = (time_since_change.seconds // 60) - limit
                
                message = (
                    f"❗️ *Превышение лимита времени\\!*\n\n"
                    f"Сотрудник: *{full_name_escaped}*\n"
                    f"Статус: {status_name}\n"
                    f"Превышение на: {overdue_min} мин\\."
                )

                await context.bot.send_message(
                    chat_id=config.SECURITY_CHAT_ID,
                    text=message,
                    message_thread_id=message_thread_id,
                    parse_mode='MarkdownV2'
                )
                
            except Exception as e:
                logger.error(f"Failed to send overdue break alert for {emp['full_name']}: {e}")


async def auto_clock_out_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running auto clock-out job...")
    active_employees = await db_manager.get_active_employees_for_reset()
    for emp in active_employees:
        await db_manager.update_employee_status(emp['id'], 'offline')
        await db_manager.log_time_event(emp['id'], 'clock_out', 'Автоматически в 00:00')
        logger.info(f"Auto-clocked out employee ID {emp['id']}")

def start_scheduler(application: Application):
    """Запускает все фоновые задачи в UTC+5."""
    
    # ВАЖНО: Указываем таймзону планировщика
    scheduler = AsyncIOScheduler(timezone=TARGET_TIMEZONE)
    
    scheduler.add_job(check_lateness_job, 'interval', minutes=5, args=[application])
    scheduler.add_job(check_overdue_breaks_job, 'interval', minutes=1, args=[application])
    
    # Сброс в 00:00 именно по Екатеринбургу (UTC+5)
    scheduler.add_job(auto_clock_out_job, 'cron', hour=0, minute=0, args=[application])
    
    scheduler.start()
    logger.info(f"Scheduler started with timezone: {TARGET_TIMEZONE}")