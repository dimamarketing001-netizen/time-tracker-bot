import logging
from datetime import datetime, time, timedelta, date
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, ContextTypes
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
import db_manager
import config

logger = logging.getLogger(__name__)

async def check_lateness_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running lateness check job...")
    
    # Получаем ВСЕХ активных сотрудников, которые сегодня еще не отмечались и не уволены
    # Мы не фильтруем по времени в SQL, так как график 2/2 нужно считать в Python
    employees = await db_manager.fetch_all("""
        SELECT id, full_name, position, default_start_time, status, 
               schedule_pattern, schedule_start_date, hire_date, last_lateness_alert_date
        FROM employees 
        WHERE termination_date IS NULL 
          AND status = 'offline'
          AND (last_lateness_alert_date IS NULL OR last_lateness_alert_date != CURDATE())
    """)
    
    now = datetime.now()
    today_date = now.date()

    for emp in employees:
        try:
            # 1. Проверяем график на СЕГОДНЯ для этого сотрудника
            # get_employee_schedule_for_period возвращает список, берем первый (и единственный) элемент
            schedule_info_list = await db_manager.get_employee_schedule_for_period(emp['id'], today_date, today_date)
            
            if not schedule_info_list:
                continue
                
            today_schedule = schedule_info_list[0]
            
            # Если сегодня выходной или отгул - пропускаем
            if today_schedule['status'] in ['Выходной', 'Отгул/Больничный']:
                continue
                
            # Получаем время начала
            start_time_val = today_schedule['start_time']
            
            if not start_time_val:
                continue

            # Приводим start_time к объекту time (из БД может прийти timedelta или str)
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
                logger.warning(f"Could not parse start time for {emp['full_name']}: {start_time_val}")
                continue

            # 2. Сравниваем время
            planned_start_dt = now.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
            
            # Если время старта еще не наступило (+ льготный период) - пропускаем
            grace_period = timedelta(minutes=config.LATENESS_GRACE_PERIOD_MIN)
            
            if now > planned_start_dt + grace_period:
                # ОПОЗДАНИЕ!
                await send_lateness_alert(context, emp, start_time)
                
        except Exception as e:
            logger.error(f"Error checking lateness for {emp.get('full_name')}: {e}")

async def send_lateness_alert(context, emp, start_time):
    try:
        now_str = datetime.now().strftime('%d.%m.%Y')
        topic_name = f"Опоздание: {emp['full_name']} {now_str}"
        
        # Создаем тему
        try:
            topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
            thread_id = topic.message_thread_id
        except Exception as e:
            logger.error(f"Could not create topic: {e}")
            thread_id = None # Если это не супергруппа с темами, шлем в общий чат

        # Экранируем
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
        
        # Обновляем дату последнего оповещения, чтобы не спамить каждые 5 минут
        await db_manager.update_lateness_alert_date(emp['id'])
        logger.warning(f"Lateness alert sent for {emp['full_name']}")
        
    except BadRequest as e:
        logger.error(f"Failed to send lateness alert for {emp['full_name']}: {e}")

async def check_overdue_breaks_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running overdue breaks check job...")
    employees = await db_manager.get_employees_on_break()
    now = datetime.now()

    for emp in employees:
        if not emp['status_change_timestamp']:
            continue
            
        time_since_change = now - emp['status_change_timestamp']
        limit, status_name = (config.BREAK_DURATION_MIN, "перерыв") if emp['status'] == 'on_break' else (config.LUNCH_DURATION_MIN, "обед")
        topic_id = emp.get('current_alert_topic_id')
        
        if time_since_change > timedelta(minutes=limit):
            try:
                message_thread_id = topic_id
                if not topic_id:
                    # Если темы нет, создаем новую и сохраняем ее ID
                    topic_name = f"Превышение {status_name}а: {emp['full_name']} {now.strftime('%d.%m %H:%M')}"
                    try:
                        topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
                        message_thread_id = topic.message_thread_id
                        await db_manager.update_employee_topic_id(emp['id'], message_thread_id)
                    except Exception as e:
                        logger.error(f"Topic error: {e}")
                        message_thread_id = None

                # Уведомление сотруднику
                try:
                    await context.bot.send_message(
                        chat_id=emp['personal_telegram_id'],
                        text=f"❗️Внимание! Ваш {status_name} превысил {limit} минут. Пожалуйста, вернитесь к работе."
                    )
                except Exception:
                    pass # Сотрудник мог заблокировать бота

                # Уведомление в СБ
                full_name_escaped = escape_markdown(emp['full_name'], version=2)
                
                # Считаем превышение
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
    """Запускает все фоновые задачи."""
    # Убедитесь, что часовой пояс совпадает с системным/желаемым
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow") 
    
    # Опоздания проверяем раз в 5 минут
    scheduler.add_job(check_lateness_job, 'interval', minutes=5, args=[application])
    
    # Перерывы проверяем раз в 1 минуту
    scheduler.add_job(check_overdue_breaks_job, 'interval', minutes=1, args=[application])
    
    # Сброс смены в полночь
    scheduler.add_job(auto_clock_out_job, 'cron', hour=0, minute=0, args=[application])
    
    scheduler.start()
    logger.info("Scheduler started with all jobs.")