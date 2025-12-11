import io
import pyotp
import qrcode
from functools import wraps
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
import db_manager as db_manager
import matplotlib
import matplotlib.pyplot as plt
import io
import pytz
from config import CITY_TIMEZONES, DEFAULT_TIMEZONE

matplotlib.use('Agg')

BTN_MY_CARD = "👤 Моя карточка"

def get_timezone_for_city(city_name: str) -> pytz.timezone:
    """
    Возвращает объект timezone на основе названия города.
    Если город не найден, возвращает дефолтный (Москва).
    """
    if not city_name:
        return pytz.timezone(DEFAULT_TIMEZONE)
    
    clean_city = city_name.strip().title()
    
    # Ищем прямое совпадение
    tz_str = CITY_TIMEZONES.get(clean_city)
    
    if not tz_str:
        # Попытка найти дефолт, если города нет в списке
        return pytz.timezone(DEFAULT_TIMEZONE)
    
    return pytz.timezone(tz_str)

def generate_table_image(headers: list, data: list, title: str = "") -> io.BytesIO:
    """
    Генерирует изображение таблицы с данными.
    """
    # Расчет высоты фигуры в зависимости от количества строк
    # Базовая высота заголовка + высота на каждую строку
    row_height = 0.4
    fig_width = 12
    fig_height = len(data) * row_height + 1.5
    
    # Создаем фигуру
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    
    # Убираем оси
    ax.axis('off')
    
    if title:
        plt.title(title, fontsize=16, pad=20, weight='bold')

    # Цвета для шапки и строк
    header_color = '#40466e'
    row_colors = ['#f1f1f2', 'w']
    edge_color = 'w'

    # Создаем таблицу
    table = ax.table(
        cellText=data,
        colLabels=headers,
        loc='center',
        cellLoc='left',
        edges='closed'
    )

    # Настройка стилей
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.8) # Масштабируем высоту строк

    # Стилизация ячеек
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(edge_color)
        cell.set_linewidth(1)
        
        if row == 0:
            cell.set_text_props(weight='bold', color='w')
            cell.set_facecolor(header_color)
            cell.set_edgecolor('w')
        else:
            cell.set_facecolor(row_colors[row % len(row_colors)])
            
        # Увеличиваем отступы текста внутри ячеек
        cell.set_text_props(verticalalignment='center')
        
        # Настройка ширины колонок (примерная)
        # 0:Дата, 1:День, 2:Время, 3:Статус, 4:Комментарий
        if col == 4: # Комментарий шире
            cell.set_width(0.4)
        elif col == 0: # Дата
            cell.set_width(0.1)
        else:
            cell.set_width(0.15)

    # Сохраняем в буфер
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    plt.close(fig) # Обязательно закрываем фигуру, чтобы не забивать память
    
    return buf

def get_main_keyboard(role: str) -> ReplyKeyboardMarkup:
    """
    Генерирует главную клавиатуру в зависимости от роли.
    Приводит роль к нижнему регистру для надежности проверки.
    """
    safe_role = str(role).strip().lower() if role else 'employee'

    keyboard = [
        [KeyboardButton("🟢 Начать смену"), KeyboardButton("🔴 Закончить смену")],
        [KeyboardButton("📅 Мой график"), KeyboardButton("👤 Моя карточка")],
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