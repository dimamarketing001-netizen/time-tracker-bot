import pyotp
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
import logging
import db_manager
import config
from utils import generate_totp_qr_code, verify_totp, get_main_keyboard, generate_simple_six_digit_code, send_user_code_to_api

logger = logging.getLogger(__name__)

VERIFY_2FA_SETUP_CODE, AWAITING_ACTION_TOTP = range(2)

async def start_2fa_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    if not employee:
        return ConversationHandler.END

    secret = pyotp.random_base32()
    context.user_data['temp_totp_secret'] = secret
    
    username = update.effective_user.username or f"user_{user_id}"
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name="TimeTrackerBot")
    qr_code_bio = generate_totp_qr_code(uri)
    
    message_sender = update.message or update.callback_query.message

    await message_sender.reply_text(
        "Для защиты вашего аккаунта необходимо настроить двухфакторную аутентификацию..."
    )
    await message_sender.reply_photo(
        photo=qr_code_bio,
        caption=f"Ключ для ручного ввода: `{secret}`\n\nПосле добавления аккаунта, отправьте 6-значный код для подтверждения.",
        parse_mode='Markdown'
    )
    return VERIFY_2FA_SETUP_CODE

async def verify_2fa_setup_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip()
    secret = context.user_data.get('temp_totp_secret')

    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    role = employee.get('role', 'employee') if employee else 'employee'

    if not secret:
        await update.message.reply_text("Произошла ошибка. Попробуйте начать сначала.", reply_markup=get_main_keyboard(role))
        return ConversationHandler.END

    if verify_totp(secret, code):
        await db_manager.set_totp_secret(employee['id'], secret)
        
        await update.message.reply_text("✅ Двухфакторная аутентификация успешно настроена!")
        
        original_update = context.user_data.pop('original_update', None)
        
        # Если это было первоначальное действие, выполняем его и возвращаем клавиатуру
        if original_update and original_update.message and (original_update.message.text == '/on' or "Начать смену" in original_update.message.text):
            await update.message.reply_text("Выполняю ваш первоначальный вход в линию...")
            await db_manager.update_employee_status(employee['id'], 'online')
            await db_manager.log_time_event(employee['id'], 'clock_in')
            await update.message.reply_text("✅ Вы успешно вошли в линию. Продуктивного дня!", reply_markup=get_main_keyboard(role))

            simple_code = generate_simple_six_digit_code()
            await update.message.reply_text(f"Вот тебе код для формы на сегодняшний день: `{simple_code}`", parse_mode='Markdown')

            await send_user_code_to_api(employee['id'], simple_code)
        else:
            await update.message.reply_text("Теперь, когда 2FA настроен, пожалуйста, повторите ваше действие.", reply_markup=get_main_keyboard(role))

        context.user_data.clear()
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный код. Попробуйте еще раз.")
        return VERIFY_2FA_SETUP_CODE


async def verify_action_totp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Проверяет TOTP и выполняет сохраненное действие."""
    code = update.message.text.strip()
    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    
    if not employee or not employee['totp_secret']:
        await update.message.reply_text("Ошибка: 2FA не настроен.")
        return ConversationHandler.END

    if verify_totp(employee['totp_secret'], code):
        pending_action = context.user_data.pop('pending_action', None)
        if not pending_action:
            await update.message.reply_text("Не найдено ожидающее действие.")
            return ConversationHandler.END

        action_type = pending_action['type']
        
        if action_type == 'clock_out':
            reason = pending_action['reason']
            await db_manager.update_employee_status(employee['id'], pending_action['status'])
            await db_manager.log_time_event(employee['id'], 'clock_out', reason)
            
            messages = {
                "Обед": f"Приятного аппетита! Вы вышли на обед на {config.LUNCH_DURATION_MIN} минут.",
                "Перерыв": f"Вы взяли короткий перерыв на {config.BREAK_DURATION_MIN} минут. Пожалуйста, не задерживайтесь.",
                "Инкассация": "Вы вышли на инкассацию.",
                "Завершение дня": "Рабочий день завершен. Хорошего отдыха!"
            }
            await update.message.reply_text(f"✅ {messages.get(reason, 'Статус обновлен.')}")

        elif action_type == 'clock_in':
            await db_manager.update_employee_status(employee['id'], 'online')
            await db_manager.log_time_event(employee['id'], 'clock_in')
            await update.message.reply_text("✅ Вы успешно вошли в линию. Продуктивного дня!")

            simple_code = generate_simple_six_digit_code()
            await update.message.reply_text(f"Вот тебе код для формы на сегодняшний день: `{simple_code}`", parse_mode='Markdown')

            await send_user_code_to_api(employee['id'], simple_code)
        
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный код. Попробуйте еще раз или введите /cancel для отмены.")
        return AWAITING_ACTION_TOTP


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущую операцию и возвращает кнопки."""
    context.user_data.clear()
    

    user_id = update.effective_user.id
    employee = await db_manager.get_employee_by_telegram_id(user_id)
    role = employee.get('role', 'employee') if employee else 'employee'
    
    await update.message.reply_text("Операция отменена.", reply_markup=get_main_keyboard(role))
    return ConversationHandler.END