import io
import pyotp
import qrcode
from functools import wraps
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
import db_manager as db_manager

def get_main_keyboard(role: str) -> ReplyKeyboardMarkup:
    """
    Генерирует главную клавиатуру в зависимости от роли.
    Приводит роль к нижнему регистру для надежности проверки.
    """
    safe_role = str(role).strip().lower() if role else 'employee'

    keyboard = [
        [KeyboardButton("🟢 Начать смену"), KeyboardButton("🔴 Закончить смену")],
        [KeyboardButton("📅 Мой график")]
    ]
    
    if safe_role in ['admin', 'security']:
        keyboard.append([KeyboardButton("🔐 Админка")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def generate_totp_qr_code(uri: str) -> io.BytesIO:
    """Генерирует QR-код в виде байтового потока."""
    img = qrcode.make(uri)
    bio = io.BytesIO()
    bio.name = 'qr_code.png'
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

def security_required(func):
    """Декоратор для проверки роли 'security' или 'admin'."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        employee = await db_manager.get_employee_by_telegram_id(user_id)
        
        # Тоже приводим к нижнему регистру
        role = employee.get('role', '').lower() if employee else 'unknown'
        
        if role not in {'security', 'admin'}:
            await update.message.reply_text(f"У вас нет прав для выполнения этой команды. Роль: {role}")
            return

        return await func(update, context, *args, **kwargs)
        
    return wrapped

def verify_totp(secret: str, code: str) -> bool:
    """Проверяет TOTP код."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code)