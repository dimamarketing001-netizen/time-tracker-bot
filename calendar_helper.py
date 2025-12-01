# Файл: utils/calendar_helper.py

import calendar
from datetime import date, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def create_calendar(year: int = None, month: int = None) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с календарем на заданный год и месяц.
    """
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    # Header with month name and navigation arrows
    header = [
        InlineKeyboardButton("<<", callback_data=f"cal_prev_{year}_{month}"),
        InlineKeyboardButton(f"{calendar.month_name[month]} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton(">>", callback_data=f"cal_next_{year}_{month}")
    ]

    # Weekday names
    weekdays = [InlineKeyboardButton(day, callback_data="cal_ignore") for day in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]]

    # Calendar days
    month_calendar = calendar.monthcalendar(year, month)
    days = []
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
            else:
                # Format: cal_day_YYYY-MM-DD
                callback_data = f"cal_day_{year}-{month:02d}-{day:02d}"
                row.append(InlineKeyboardButton(str(day), callback_data=callback_data))
        days.append(row)

    # Footer with a "Back" button
    footer = [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_schedule_type_select")]

    keyboard = [header, weekdays] + days + [footer]
    return InlineKeyboardMarkup(keyboard)

def process_calendar_selection(update) -> tuple[int, int] | tuple[None, None]:
    """
    Обрабатывает нажатие на кнопки навигации календаря.
    Возвращает (новый год, новый месяц) или (None, None), если была нажата кнопка с датой.
    """
    query = update.callback_query
    action, year_str, month_str = query.data.split('_')[1:]
    year, month = int(year_str), int(month_str)

    if action == "next":
        month += 1
        if month > 12:
            month = 1
            year += 1
    elif action == "prev":
        month -= 1
        if month < 1:
            month = 12
            year -= 1
            
    return year, month