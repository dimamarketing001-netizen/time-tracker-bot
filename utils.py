import io
import pyotp
import qrcode
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
import db_manager as db_manager

def generate_totp_qr_code(uri: str) -> io.BytesIO:
    """Генерирует QR-код в виде байтового потока."""
    img = qrcode.make(uri)
    bio = io.BytesIO()
    bio.name = 'qr_code.png'
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

def security_required(func):
    """Декоратор для проверки роли 'security'."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        employee = await db_manager.get_employee_by_telegram_id(user_id)
        if employee and employee['role'] == 'security':
            return await func(update, context, *args, **kwargs)
        else:
            await update.message.reply_text("У вас нет прав для выполнения этой команды.")
            return
    return wrapped

def verify_totp(secret: str, code: str) -> bool:
    """Проверяет TOTP код."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code)