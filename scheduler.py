import logging
from datetime import datetime, time, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, ContextTypes
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
import db_manager
import config

logger = logging.getLogger(__name__)

async def check_lateness_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running lateness check job...")
    employees = await db_manager.get_employees_for_lateness_check()
    now = datetime.now()

    for emp in employees:
        start_time_str = emp['override_start_time'] or emp['default_start_time']
        if emp.get('is_day_off'):
            continue
        
        start_time = datetime.strptime(str(start_time_str), '%H:%M:%S').time()
        planned_start_dt = now.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
        
        if now > planned_start_dt + timedelta(minutes=config.LATENESS_GRACE_PERIOD_MIN):
            try:
                topic_name = f"Опоздание: {emp['full_name']} {now.strftime('%d.%m.%Y')}"
                topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
                
                # --- ИСПРАВЛЕНИЕ: Экранируем переменные ---
                full_name_escaped = escape_markdown(emp['full_name'])
                position_escaped = escape_markdown(emp['position'] or 'Не указана')

                message = (f"⚠️ ОПОЗДАНИЕ!\n\n"
                           f"Сотрудник: *{full_name_escaped}*\n"
                           f"Должность: {position_escaped}\n"
                           f"Плановое время начала: {start_time.strftime('%H:%M')}")

                await context.bot.send_message(
                    chat_id=config.SECURITY_CHAT_ID,
                    text=message,
                    message_thread_id=topic.message_thread_id,
                    parse_mode='Markdown'
                )
                await db_manager.update_lateness_alert_date(emp['id'])
                logger.warning(f"Lateness alert sent for {emp['full_name']}")
            except BadRequest as e:
                logger.error(f"Failed to send lateness alert for {emp['full_name']}: {e}")
            except Exception as e:
                logger.error(f"An unexpected error occurred in lateness job for {emp['full_name']}: {e}")

async def check_overdue_breaks_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running overdue breaks check job...")
    employees = await db_manager.get_employees_on_break()
    now = datetime.now()

    for emp in employees:
        time_since_change = now - emp['status_change_timestamp']
        limit, status_name = (config.BREAK_DURATION_MIN, "перерыв") if emp['status'] == 'on_break' else (config.LUNCH_DURATION_MIN, "обед")
        topic_id = emp.get('current_alert_topic_id')
        
        if time_since_change > timedelta(minutes=limit):
            try:
                message_thread_id = topic_id
                if not topic_id:
                    # Если темы нет, создаем новую и сохраняем ее ID
                    topic_name = f"Превышение {status_name}а: {emp['full_name']} {now.strftime('%d.%m %H:%M')}"
                    topic = await context.bot.create_forum_topic(chat_id=config.SECURITY_CHAT_ID, name=topic_name)
                    message_thread_id = topic.message_thread_id
                    await db_manager.update_employee_topic_id(emp['id'], message_thread_id)
                    logger.info(f"Created new topic {message_thread_id} for overdue break for {emp['full_name']}")

                # Уведомление сотруднику (каждую минуту)
                await context.bot.send_message(
                    chat_id=emp['personal_telegram_id'],
                    text=f"❗️Внимание! Ваш {status_name} превысил {limit} минут."
                )

                # Уведомление в СБ (в одну и ту же тему)
                full_name_escaped = escape_markdown(emp['full_name'])
                status_escaped = escape_markdown(emp['status'])
                message = (f"❗️Превышение лимита времени!\n\n"
                           f"Сотрудник: *{full_name_escaped}*\n"
                           f"Статус: {status_escaped}\n"
                           f"Прошло времени: {time_since_change.seconds // 60} мин.")

                await context.bot.send_message(
                    chat_id=config.SECURITY_CHAT_ID,
                    text=message,
                    message_thread_id=message_thread_id,
                    parse_mode='Markdown'
                )
                
            except Exception as e:
                logger.error(f"Failed to send overdue break alert for {emp['full_name']}: {e}")
        elif topic_id:
            pass


async def auto_clock_out_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running auto clock-out job...")
    active_employees = await db_manager.get_active_employees_for_reset()
    for emp in active_employees:
        await db_manager.update_employee_status(emp['id'], 'offline')
        await db_manager.log_time_event(emp['id'], 'clock_out', 'Автоматически в 00:00')
        logger.info(f"Auto-clocked out employee ID {emp['id']}")

def start_scheduler(application: Application):
    """Запускает все фоновые задачи, передавая им application в качестве контекста."""
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow") # Укажите ваш часовой пояс
    
    scheduler.add_job(check_lateness_job, 'interval', minutes=5, args=[application])
    scheduler.add_job(check_overdue_breaks_job, 'interval', minutes=1, args=[application])
    scheduler.add_job(auto_clock_out_job, 'cron', hour=0, minute=0, args=[application])
    
    scheduler.start()
    logger.info("Scheduler started with all jobs.")