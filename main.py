import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    JobQueue,
)
import aiohttp
from datetime import datetime, timedelta
import sqlite3
import pytz
import re
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
(
    ADDING_HABIT,
    MARKING_DONE,
    SETTING_REMINDER,
    ADDING_DEADLINE,
    SETTING_DEADLINE_DATE,
    SETTING_DEADLINE_TIME,
    DELETING_ITEM,
    SETTING_REMINDER_INTERVAL,
) = range(8)


def init_db():
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS habits
        (
            id
            INTEGER
            PRIMARY
            KEY
            AUTOINCREMENT,
            user_id
            INTEGER,
            habit
            TEXT,
            is_done
            BOOLEAN,
            created_at
            TIMESTAMP,
            reminder_interval
            INTEGER
            DEFAULT
            3600,
            is_deadline
            BOOLEAN
            DEFAULT
            FALSE,
            deadline_date
            TIMESTAMP
            NULL
        )
        """
    )
    conn.commit()
    conn.close()


def add_habit_to_db(user_id, habit_text, is_deadline=False, deadline_date=None):
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO habits (user_id, habit, is_done, created_at, is_deadline, deadline_date) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, habit_text, False, datetime.now(pytz.utc), is_deadline, deadline_date),
    )
    habit_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return habit_id


def get_user_habits(user_id):
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, habit, is_done, reminder_interval, is_deadline, deadline_date FROM habits WHERE user_id = ?",
        (user_id,)
    )
    habits = cursor.fetchall()
    conn.close()
    return habits


def get_habit(habit_id):
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, habit, is_done, reminder_interval, is_deadline, deadline_date FROM habits WHERE id = ?",
        (habit_id,)
    )
    habit = cursor.fetchone()
    conn.close()
    return habit


def update_habit_status(habit_id, is_done):
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE habits SET is_done = ? WHERE id = ?", (is_done, habit_id)
    )
    conn.commit()
    conn.close()


def update_reminder_interval(habit_id, interval_seconds):
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE habits SET reminder_interval = ? WHERE id = ?",
        (interval_seconds, habit_id)
    )
    conn.commit()
    conn.close()


def delete_habit(habit_id):
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
    conn.commit()
    conn.close()


def get_main_menu():
    return ReplyKeyboardMarkup(
        [
            ["Добавить привычку", "Мои привычки"],
            ["Добавить дедлайн", "Удалить"],
            ["Настроить напоминания", "Отметить дедлайн"],
            ["Погода", "Помощь"],
        ],
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для трекинга привычек и дедлайнов.\n"
        "Выберите действие:",
        reply_markup=get_main_menu(),
    )


async def add_habit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите название привычки:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADDING_HABIT


async def habit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    habit_text = update.message.text
    user_id = update.message.from_user.id

    habit_id = add_habit_to_db(user_id, habit_text)
    context.job_queue.run_repeating(
        send_reminder,
        interval=timedelta(seconds=3600),
        first=10,
        chat_id=update.message.chat_id,
        data={"habit_id": habit_id, "habit_text": habit_text},
        name=f"reminder_{habit_id}",
    )

    await update.message.reply_text(
        f"Привычка '{habit_text}' добавлена! По умолчанию напоминания приходят каждый час.\n"
        "Вы можете изменить интервал напоминаний через меню 'Настроить напоминания'.",
        reply_markup=get_main_menu(),
    )
    return ConversationHandler.END


async def add_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите название дедлайна:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADDING_DEADLINE


async def deadline_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["deadline_name"] = update.message.text
    await update.message.reply_text(
        "Введите дату дедлайна в формате ДД.ММ.ГГГГ (например, 31.12.2023):",
    )
    return SETTING_DEADLINE_DATE


async def deadline_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        deadline_date = datetime.strptime(update.message.text, "%d.%m.%Y").date()
        today = datetime.now().date()

        if deadline_date < today:
            await update.message.reply_text(
                "❌ Дата дедлайна не может быть раньше сегодняшнего дня! Введите корректную дату:",
                reply_markup=get_main_menu(),
            )
            return SETTING_DEADLINE_DATE

        context.user_data["deadline_date"] = deadline_date
        await update.message.reply_text(
            "⏰ Введите время напоминания в формате ЧЧ:ММ (например, 09:30):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return SETTING_DEADLINE_TIME

    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ:",
            reply_markup=get_main_menu(),
        )
        return SETTING_DEADLINE_DATE


async def deadline_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reminder_time = datetime.strptime(update.message.text, "%H:%M").time()
        deadline_date = context.user_data["deadline_date"]
        deadline_datetime = datetime.combine(deadline_date, reminder_time)
        deadline_datetime = pytz.utc.localize(deadline_datetime)
        if deadline_datetime < datetime.now(pytz.utc):
            await update.message.reply_text(
                "❌ Выбранное время уже прошло! Введите новое время:",
                reply_markup=get_main_menu(),
            )
            return SETTING_DEADLINE_TIME

        habit_id = add_habit_to_db(
            update.message.from_user.id,
            context.user_data["deadline_name"],
            is_deadline=True,
            deadline_date=deadline_datetime,
        )

        context.job_queue.run_daily(
            send_deadline_reminder,
            time=reminder_time,
            days=tuple(range(7)),
            chat_id=update.message.chat_id,
            data={
                "habit_id": habit_id,
                "deadline_name": context.user_data["deadline_name"],
                "deadline_date": deadline_datetime
            },
            name=f"deadline_{habit_id}",
        )

        await update.message.reply_text(
            f"✅ Дедлайн '{context.user_data['deadline_name']}' успешно добавлен!\n"
            f"📅 Дата: {deadline_date.strftime('%d.%m.%Y')}\n"
            f"⏰ Напоминание: ежедневно в {reminder_time.strftime('%H:%M')}",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ:",
            reply_markup=get_main_menu(),
        )
        return SETTING_DEADLINE_TIME


async def send_deadline_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    deadline_name = job.data["deadline_name"]
    deadline_date = job.data["deadline_date"]
    habit_id = job.data["habit_id"]
    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_done FROM habits WHERE id = ?", (habit_id,))
    is_done = cursor.fetchone()[0]
    conn.close()

    if not is_done:
        now = datetime.now(pytz.utc)
        days_left = (deadline_date - now).days

        if days_left > 0:
            message = (
                f"⏳ Дедлайн: {deadline_name}\n"
                f"📅 Дата: {deadline_date.strftime('%d.%m.%Y')}\n"
                f"⏱ Осталось дней: {days_left}"
            )
        elif days_left == 0:
            message = (
                f"⚠️ Сегодня последний день!\n"
                f"Дедлайн: {deadline_name}\n"
                f"Не забудьте выполнить!"
            )
        else:
            message = (
                f"🚨 Дедлайн просрочен!\n"
                f"{deadline_name} должен был быть выполнен {deadline_date.strftime('%d.%m.%Y')}\n"
                f"Просрочено на {-days_left} дней"
            )
            update_habit_status(habit_id, True)
            job.schedule_removal()

        try:
            await context.bot.send_message(
                job.chat_id,
                message,
                reply_markup=get_main_menu(),
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания: {e}")


async def my_habits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    habits = get_user_habits(user_id)

    if not habits:
        await update.message.reply_text(
            "У вас пока нет привычек или дедлайнов.",
            reply_markup=get_main_menu(),
        )
        return

    message = "📋 Ваши привычки и дедлайны:\n\n"
    for habit_id, habit_text, is_done, interval, is_deadline, deadline_date in habits:
        if is_deadline:
            deadline_str = deadline_date.strftime("%d.%m.%Y") if deadline_date else "???"
            status = f"📅 Дедлайн: {deadline_str}"
        else:
            interval_hours = interval // 3600
            interval_min = (interval % 3600) // 60
            status = f"⏰ Напоминание: каждые {interval_hours}ч {interval_min}м"

        done_status = "✅ Выполнен" if is_done else "❌ Не выполнен"
        message += f"{habit_id}. {habit_text}\n{status} - {done_status}\n\n"

    await update.message.reply_text(
        message,
        reply_markup=get_main_menu(),
    )


async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    habits = get_user_habits(user_id)
    deadlines = [h for h in habits if h[4]]  # is_deadline=True

    if not deadlines:
        await update.message.reply_text(
            "У вас нет дедлайнов для отметки",
            reply_markup=get_main_menu(),
        )
        return

    keyboard = [[str(habit_id)] for habit_id, _, _, _, _, _ in deadlines]
    keyboard.append(["Отмена"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "Выберите ID дедлайна, который выполнили:",
        reply_markup=reply_markup,
    )
    return MARKING_DONE


async def deadline_done_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text(
            "Действие отменено",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    try:
        habit_id = int(update.message.text)
        habit = get_habit(habit_id)

        if not habit or not habit[5]:
            await update.message.reply_text(
                "Дедлайн не найден",
                reply_markup=get_main_menu(),
            )
            return ConversationHandler.END

        update_habit_status(habit_id, True)
        for job in context.job_queue.get_jobs_by_name(f"deadline_{habit_id}"):
            job.schedule_removal()

        await update.message.reply_text(
            f"Дедлайн '{habit[2]}' отмечен как выполненный! Напоминания отключены.",
            reply_markup=get_main_menu(),
        )
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите корректный ID",
            reply_markup=get_main_menu(),
        )
        return MARKING_DONE

    return ConversationHandler.END


async def delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    habits = get_user_habits(user_id)

    if not habits:
        await update.message.reply_text(
            "У вас нет привычек или дедлайнов для удаления",
            reply_markup=get_main_menu(),
        )
        return

    keyboard = [[str(habit_id)] for habit_id, _, _, _, _, _ in habits]
    keyboard.append(["Отмена"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "Выберите ID привычки/дедлайна для удаления:",
        reply_markup=reply_markup,
    )
    return DELETING_ITEM


async def delete_item_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text(
            "Действие отменено",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    try:
        habit_id = int(update.message.text)
        habit = get_habit(habit_id)

        if not habit:
            await update.message.reply_text(
                "Привычка/дедлайн не найден",
                reply_markup=get_main_menu(),
            )
            return ConversationHandler.END
        for job in context.job_queue.get_jobs_by_name(f"reminder_{habit_id}"):
            job.schedule_removal()
        for job in context.job_queue.get_jobs_by_name(f"deadline_{habit_id}"):
            job.schedule_removal()
        delete_habit(habit_id)

        await update.message.reply_text(
            f"Привычка/дедлайн '{habit[2]}' успешно удалён!",
            reply_markup=get_main_menu(),
        )
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите корректный ID",
            reply_markup=get_main_menu(),
        )
        return DELETING_ITEM

    return ConversationHandler.END


async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    habits = get_user_habits(user_id)
    regular_habits = [h for h in habits if not h[4]]

    if not regular_habits:
        await update.message.reply_text(
            "У вас нет привычек для настройки напоминаний",
            reply_markup=get_main_menu(),
        )
        return

    keyboard = [[str(habit_id)] for habit_id, _, _, _, _, _ in regular_habits]
    keyboard.append(["Отмена"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "Выберите ID привычки для настройки напоминаний:",
        reply_markup=reply_markup,
    )
    return SETTING_REMINDER


async def set_reminder_habit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text(
            "Действие отменено",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    try:
        habit_id = int(update.message.text)
        habit = get_habit(habit_id)

        if not habit or habit[5]:
            await update.message.reply_text(
                "Привычка не найдена или это дедлайн",
                reply_markup=get_main_menu(),
            )
            return ConversationHandler.END

        context.user_data["reminder_habit_id"] = habit_id
        await update.message.reply_text(
            f"Настройка напоминаний для привычки: {habit[2]}\n"
            "Введите новый интервал в формате дни:часы:минуты:секунды\n"
            "Например:\n"
            "0:1:0:0 - каждый час\n"
            "0:0:30:0 - каждые 30 минут\n"
            "1:0:0:0 - каждый день",
            reply_markup=ReplyKeyboardRemove(),
        )
        return SETTING_REMINDER_INTERVAL
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите корректный ID привычки",
            reply_markup=get_main_menu(),
        )
        return SETTING_REMINDER


async def set_reminder_interval_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interval_pattern = re.compile(r"^(\d+):(\d+):(\d+):(\d+)$")
    match = interval_pattern.match(update.message.text)

    if not match:
        await update.message.reply_text(
            "Неверный формат. Используйте дни:часы:минуты:секунды (например, 0:1:0:0)",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    days, hours, minutes, seconds = map(int, match.groups())
    total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds

    if total_seconds <= 0:
        await update.message.reply_text(
            "Интервал должен быть больше 0 секунд",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    habit_id = context.user_data["reminder_habit_id"]
    habit = get_habit(habit_id)

    update_reminder_interval(habit_id, total_seconds)

    for job in context.job_queue.get_jobs_by_name(f"reminder_{habit_id}"):
        job.schedule_removal()

    context.job_queue.run_repeating(
        send_reminder,
        interval=timedelta(seconds=total_seconds),
        first=10,
        chat_id=update.message.chat_id,
        data={"habit_id": habit_id, "habit_text": habit[2]},
        name=f"reminder_{habit_id}",
    )

    await update.message.reply_text(
        f"Напоминания для привычки '{habit[2]}' настроены на интервал: {days}д {hours}ч {minutes}м {seconds}с",
        reply_markup=get_main_menu(),
    )
    return ConversationHandler.END


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    habit_text = job.data["habit_text"]
    habit_id = job.data["habit_id"]

    conn = sqlite3.connect("habits.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_done FROM habits WHERE id = ?", (habit_id,))
    is_done = cursor.fetchone()[0]
    conn.close()

    if not is_done:
        await context.bot.send_message(
            job.chat_id,
            f"🔔 Напоминание: не забудьте выполнить привычку '{habit_text}'!",
            reply_markup=get_main_menu(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Помощь по боту:\n\n"
        "📌 Основные функции:\n"
        "• Добавить привычку - создать новую привычку с напоминаниями\n"
        "• Добавить дедлайн - создать дедлайн с ежедневными напоминаниями\n"
        "• Мои привычки - просмотреть список привычек и дедлайнов\n"
        "• Отметить дедлайн - отметить дедлайн как выполненный\n"
        "• Удалить - удалить привычку или дедлайн\n"
        "• Настроить напоминания - изменить интервал напоминаний для привычки\n\n"
        "📅 Формат даты для дедлайнов: ДД.ММ.ГГГГ (например, 31.12.2023)\n"
        "⏱ Формат интервала напоминаний: дни:часы:минуты:секунды (например, 0:1:30:0)",
        reply_markup=get_main_menu(),
    )


async def get_current_weather(location: str):
    url = "http://api.openweathermap.org/data/2.5/weather?id=524901&lang=ru&units=metric&APPID=2faa30a09d31ad5474a216fee4d6897a";
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

            if response.status != 200:
                raise Exception(data.get('message', 'Unknown error'))

            weather = data['weather'][0]
            main = data['main']
            wind = data['wind']
            visibility = data.get('visibility', 'нет данных')

            sunrise = datetime.fromtimestamp(data['sys']['sunrise']).strftime('%H:%M')
            sunset = datetime.fromtimestamp(data['sys']['sunset']).strftime('%H:%M')

            return (
                f"🌦 Погода в Москве:\n"
                f"• {weather['description'].capitalize()}\n"
                f"• Температура: {main['temp']}°C (ощущается как {main['feels_like']}°C)\n"
                f"• Влажность: {main['humidity']}%\n"
                f"• Ветер: {wind['speed']} м/с, порывы до {wind.get('gust', wind['speed'])} м/с\n"
                f"• Давление: {main['pressure']} hPa\n"
                f"• Видимость: {visibility} м\n"
                f"• Восход: {sunrise}, закат: {sunset}"
            )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "habit_id" in context.user_data:
        context.user_data.clear()
    await update.message.reply_text(
        "Действие отменено",
        reply_markup=get_main_menu(),
    )
    return ConversationHandler.END


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Добавить привычку":
        return await add_habit(update, context)
    elif text == "Мои привычки":
        return await my_habits(update, context)
    elif text == "Отметить дедлайн":
        return await mark_done(update, context)
    elif text == "Добавить дедлайн":
        return await add_deadline(update, context)
    elif text == "Удалить":
        return await delete_item(update, context)
    elif text == "Настроить напоминания":
        return await set_reminder(update, context)
    elif text == "Помощь":
        return await help_command(update, context)
    elif text == "Погода":
        try:
            weather_info = await get_current_weather()
            await update.message.reply_text(
                weather_info,
                reply_markup=get_main_menu(),
            )
        except Exception as e:
            logger.error(f"Weather error: {e}")
            await update.message.reply_text(
                "Не удалось получить данные о погоде. Попробуйте позже.",
                reply_markup=get_main_menu(),
            )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Я не понимаю эту команду. Используйте кнопки меню.",
            reply_markup=get_main_menu(),
        )


def main():
    init_db()

    application = Application.builder().token("7964089903:AAGDN4NER4KgGJa18OwVp_HeIc3eEoO7a3g").build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        ],
        states={
            ADDING_HABIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, habit_input)],
            ADDING_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, deadline_input)],
            SETTING_DEADLINE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, deadline_date_input)],
            SETTING_DEADLINE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, deadline_time_input)],
            MARKING_DONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, deadline_done_input)],
            DELETING_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_item_input)],
            SETTING_REMINDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_reminder_habit_selected)],
            SETTING_REMINDER_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_reminder_interval_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))

    application.run_polling()


if __name__ == "__main__":
    main()