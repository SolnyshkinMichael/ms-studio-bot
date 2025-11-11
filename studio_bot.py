import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, ConversationHandler, CallbackQueryHandler, JobQueue
import sqlite3
from datetime import datetime, timedelta
import os
import asyncio
import csv
import io

# Токен бота
TOKEN = os.environ.get('BOT_TOKEN')

# Состояния для бронирования
SELECT_BOOKING_TYPE, SELECT_DAY, SELECT_TIME, SELECT_DURATION = range(4)

# Состояния для рассылки
BROADCAST_MESSAGE, BROADCAST_CONFIRM = range(4, 6)

# Состояния для аналитики
ANALYTICS_MENU, ANALYTICS_PERIOD = range(6, 8)

# Состояния для админ расписания
ADMIN_SCHEDULE_MENU, ADMIN_SCHEDULE_DATE = range(8, 10)

# Состояния для добавления записи админом (ОБНОВЛЕНО)
ADMIN_ADD_DAY, ADMIN_ADD_TIME, ADMIN_ADD_DURATION, ADMIN_ADD_CLIENT_NAME, ADMIN_ADD_CLIENT_CONTACT = range(10, 15)

# Состояния для отмены записей админом
ADMIN_CANCEL_DAY, ADMIN_CANCEL_SELECT = range(15, 17)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ID администратора
ADMIN_ID = 407671600

# Функция для создания клавиатуры с учетом прав администратора
def get_main_keyboard(user_id: int):
    keyboard = [
        ['📅 Расписание', '🎵 Забронировать'],
        ['💰 Цены', '👨‍💻 Связь']
    ]
    
    # Добавляем кнопку админ-панели только для администратора
    if user_id == ADMIN_ID:
        keyboard.append(['👑 Админ панель'])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Полностью пересоздаем базу данных
def init_db():
    # Удаляем старую базу данных если она есть
    if os.path.exists('studio_schedule.db'):
        os.remove('studio_schedule.db')
        print("🗑️ Старая база данных удалена")
    
    conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Таблица бронирований (ОБНОВЛЕНО: добавлено поле client_contact)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            day TEXT,
            time TEXT,
            duration INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            added_by_admin BOOLEAN DEFAULT FALSE,
            client_contact TEXT
        )
    ''')
    
    # Таблица пользователей для статистики
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            first_seen TEXT,
            last_activity TEXT,
            bookings_count INTEGER DEFAULT 0,
            total_hours INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Новая база данных создана с правильной структурой")

# Функция для получения текущего времени в правильном формате
def get_current_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# Функция для преобразования строки времени в datetime объект
def parse_db_time(time_str):
    try:
        return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Error parsing time {time_str}: {e}")
        return datetime.now()

# Функция для обновления статистики пользователя
def update_user_stats(user_id: int, username: str, first_name: str, last_name: str = None):
    try:
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        current_time = get_current_time()
        
        # Проверяем, существует ли пользователь
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user_exists = cursor.fetchone()
        
        if user_exists:
            # Обновляем последнюю активность и username если изменился
            cursor.execute('''
                UPDATE users 
                SET last_activity = ?,
                    username = ?,
                    first_name = ?,
                    last_name = ?
                WHERE user_id = ?
            ''', (current_time, username, first_name, last_name, user_id))
        else:
            # Добавляем нового пользователя
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_activity)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, current_time, current_time))
        
        conn.commit()
        conn.close()
        print(f"✅ Статистика обновлена для пользователя {user_id} в {current_time}")
        
    except Exception as e:
        logger.error(f"Error updating user stats: {e}")

# Функция для обновления статистики бронирований пользователя
def update_user_booking_stats(user_id: int):
    try:
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Считаем количество бронирований и общее время
        cursor.execute('''
            SELECT COUNT(*), COALESCE(SUM(duration), 0) 
            FROM bookings 
            WHERE user_id = ? AND status = 'confirmed'
        ''', (user_id,))
        
        result = cursor.fetchone()
        bookings_count = result[0] if result else 0
        total_hours = result[1] if result else 0
        
        # Обновляем статистику пользователя
        cursor.execute('''
            UPDATE users 
            SET bookings_count = ?, total_hours = ?
            WHERE user_id = ?
        ''', (bookings_count, total_hours, user_id))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Error updating user booking stats: {e}")

# Функция для получения всех пользователей
def get_all_users():
    try:
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users
    except Exception as e:
        logger.error(f"Error getting users: {e}")
        return []

# Функция для получения расширенной аналитики
def get_advanced_analytics(period_days=30):
    try:
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=period_days)
        start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
        end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
        
        # Основная статистика
        cursor.execute('''
            SELECT 
                COUNT(*) as total_bookings,
                SUM(duration) as total_hours,
                AVG(duration) as avg_session_length,
                COUNT(DISTINCT user_id) as unique_clients
            FROM bookings 
            WHERE status = 'confirmed' 
            AND created_at BETWEEN ? AND ?
        ''', (start_date_str, end_date_str))
        
        stats = cursor.fetchone()
        total_bookings, total_hours, avg_session_length, unique_clients = stats
        
        # Статистика по дням недели
        cursor.execute('''
            SELECT 
                CASE strftime('%w', created_at)
                    WHEN '0' THEN 'Воскресенье'
                    WHEN '1' THEN 'Понедельник'
                    WHEN '2' THEN 'Вторник'
                    WHEN '3' THEN 'Среда'
                    WHEN '4' THEN 'Четверг'
                    WHEN '5' THEN 'Пятница'
                    WHEN '6' THEN 'Суббота'
                END as day_name,
                COUNT(*) as bookings_count,
                SUM(duration) as hours_count
            FROM bookings 
            WHERE status = 'confirmed' 
            AND created_at BETWEEN ? AND ?
            GROUP BY day_name
            ORDER BY bookings_count DESC
        ''', (start_date_str, end_date_str))
        
        days_stats = cursor.fetchall()
        
        # Статистика по времени суток
        cursor.execute('''
            SELECT 
                substr(time, 1, 2) as hour,
                COUNT(*) as bookings_count
            FROM bookings 
            WHERE status = 'confirmed' 
            AND created_at BETWEEN ? AND ?
            GROUP BY substr(time, 1, 2)
            ORDER BY bookings_count DESC
            LIMIT 5
        ''', (start_date_str, end_date_str))
        
        hours_stats = cursor.fetchall()
        
        # Самые активные клиенты
        cursor.execute('''
            SELECT 
                u.user_id,
                u.first_name,
                u.last_name,
                b.user_name,
                COUNT(b.id) as bookings_count,
                SUM(b.duration) as total_hours
            FROM bookings b
            LEFT JOIN users u ON b.user_id = u.user_id
            WHERE b.status = 'confirmed' 
            AND b.created_at BETWEEN ? AND ?
            GROUP BY b.user_name, u.user_id, u.first_name, u.last_name
            ORDER BY bookings_count DESC
            LIMIT 10
        ''', (start_date_str, end_date_str))
        
        top_clients = cursor.fetchall()
        
        # Статистика отмен
        cursor.execute('''
            SELECT 
                COUNT(*) as cancelled_count,
                (SELECT COUNT(*) FROM bookings WHERE created_at BETWEEN ? AND ?) as total_count
            FROM bookings 
            WHERE status = 'cancelled' 
            AND created_at BETWEEN ? AND ?
        ''', (start_date_str, end_date_str, start_date_str, end_date_str))
        
        cancel_stats = cursor.fetchone()
        cancelled_count, total_count = cancel_stats
        
        # Ежемесячная динамика
        cursor.execute('''
            SELECT 
                strftime('%Y-%m', created_at) as month,
                COUNT(*) as bookings_count,
                SUM(duration) as hours_count
            FROM bookings 
            WHERE status = 'confirmed'
            GROUP BY strftime('%Y-%m', created_at)
            ORDER BY month DESC
            LIMIT 6
        ''')
        
        monthly_stats = cursor.fetchall()
        
        conn.close()
        
        return {
            'period_days': period_days,
            'total_bookings': total_bookings or 0,
            'total_hours': total_hours or 0,
            'avg_session_length': round(avg_session_length or 0, 1),
            'unique_clients': unique_clients or 0,
            'days_stats': days_stats,
            'hours_stats': hours_stats,
            'top_clients': top_clients,
            'cancelled_count': cancelled_count or 0,
            'total_count': total_count or 0,
            'monthly_stats': monthly_stats
        }
        
    except Exception as e:
        logger.error(f"Error in get_advanced_analytics: {e}")
        return None

# Функция для экспорта данных в CSV
def export_analytics_to_csv(period_days=30):
    """Экспорт данных аналитики в CSV файлы"""
    try:
        analytics = get_advanced_analytics(period_days)
        if not analytics:
            return None
        
        # Создаем временную папку для экспорта
        export_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 1. Экспорт основной статистики
        main_stats_data = [
            ['Показатель', 'Значение'],
            ['Период анализа (дни)', analytics['period_days']],
            ['Всего бронирований', analytics['total_bookings']],
            ['Всего часов', analytics['total_hours']],
            ['Средняя продолжительность сессии', analytics['avg_session_length']],
            ['Уникальных клиентов', analytics['unique_clients']],
            ['Отменено броней', analytics['cancelled_count']],
            ['Процент отмен (%)', round((analytics['cancelled_count'] / analytics['total_count']) * 100, 1) if analytics['total_count'] > 0 else 0]
        ]
        
        main_stats_csv = io.StringIO()
        main_writer = csv.writer(main_stats_csv)
        main_writer.writerows(main_stats_data)
        main_stats_content = main_stats_csv.getvalue()
        main_stats_csv.close()
        
        # 2. Экспорт статистика по дням недели
        days_stats_data = [['День недели', 'Количество бронирований', 'Всего часов']]
        for day_name, bookings_count, hours_count in analytics['days_stats']:
            days_stats_data.append([day_name, bookings_count, hours_count])
        
        days_stats_csv = io.StringIO()
        days_writer = csv.writer(days_stats_csv)
        days_writer.writerows(days_stats_data)
        days_stats_content = days_stats_csv.getvalue()
        days_stats_csv.close()
        
        # 3. Экспорт статистики по времени суток
        hours_stats_data = [['Час', 'Количество бронирований']]
        for hour, bookings_count in analytics['hours_stats']:
            hours_stats_data.append([f"{hour}:00", bookings_count])
        
        hours_stats_csv = io.StringIO()
        hours_writer = csv.writer(hours_stats_csv)
        hours_writer.writerows(hours_stats_data)
        hours_stats_content = hours_stats_csv.getvalue()
        hours_stats_csv.close()
        
        # 4. Экспорт топа клиентов
        top_clients_data = [['ID', 'Имя', 'Фамилия', 'Имя клиента', 'Количество бронирований', 'Всего часов']]
        for client_id, first_name, last_name, client_name, bookings_count, total_hours in analytics['top_clients']:
            top_clients_data.append([
                client_id, 
                first_name or '', 
                last_name or '', 
                client_name or '',
                bookings_count, 
                total_hours
            ])
        
        top_clients_csv = io.StringIO()
        clients_writer = csv.writer(top_clients_csv)
        clients_writer.writerows(top_clients_data)
        top_clients_content = top_clients_csv.getvalue()
        top_clients_csv.close()
        
        # 5. Экспорт месячной динамики
        monthly_stats_data = [['Месяц', 'Количество бронирований', 'Всего часов']]
        for month, bookings_count, hours_count in analytics['monthly_stats']:
            month_date = datetime.strptime(month, '%Y-%m')
            month_name = month_date.strftime('%B %Y')
            monthly_stats_data.append([month_name, bookings_count, hours_count])
        
        monthly_stats_csv = io.StringIO()
        monthly_writer = csv.writer(monthly_stats_csv)
        monthly_writer.writerows(monthly_stats_data)
        monthly_stats_content = monthly_stats_csv.getvalue()
        monthly_stats_csv.close()
        
        # 6. Экспорт всех бронирований за период
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=period_days)
        start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
        end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
            SELECT 
                b.id,
                b.user_id,
                b.user_name,
                b.day,
                b.time,
                b.duration,
                b.status,
                b.created_at,
                b.added_by_admin,
                b.client_contact
            FROM bookings b
            WHERE b.created_at BETWEEN ? AND ?
            ORDER BY b.created_at DESC
        ''', (start_date_str, end_date_str))
        
        all_bookings = cursor.fetchall()
        conn.close()
        
        bookings_data = [['ID брони', 'ID клиента', 'Имя клиента', 'Дата', 'Время', 'Продолжительность', 'Статус', 'Дата создания', 'Добавлено админом', 'Контакт клиента']]
        for booking in all_bookings:
            bookings_data.append(list(booking))
        
        bookings_csv = io.StringIO()
        bookings_writer = csv.writer(bookings_csv)
        bookings_writer.writerows(bookings_data)
        bookings_content = bookings_csv.getvalue()
        bookings_csv.close()
        
        return {
            'main_stats': main_stats_content,
            'days_stats': days_stats_content,
            'hours_stats': hours_stats_content,
            'top_clients': top_clients_content,
            'monthly_stats': monthly_stats_content,
            'all_bookings': bookings_content,
            'export_time': export_time,
            'period_days': period_days
        }
        
    except Exception as e:
        logger.error(f"Error in export_analytics_to_csv: {e}")
        return None

# Функция для экспорта пользователей
def export_users_to_csv():
    """Экспорт данных пользователей в CSV"""
    try:
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                user_id,
                username,
                first_name,
                last_name,
                first_seen,
                last_activity,
                bookings_count,
                total_hours
            FROM users 
            ORDER BY last_activity DESC
        ''')
        
        users = cursor.fetchall()
        conn.close()
        
        users_data = [['ID пользователя', 'Username', 'Имя', 'Фамилия', 'Первое посещение', 'Последняя активность', 'Количество бронирований', 'Всего часов']]
        
        for user in users:
            user_id, username, first_name, last_name, first_seen, last_activity, bookings_count, total_hours = user
            users_data.append([
                user_id,
                username or '',
                first_name or '',
                last_name or '',
                first_seen,
                last_activity,
                bookings_count,
                total_hours
            ])
        
        users_csv = io.StringIO()
        users_writer = csv.writer(users_csv)
        users_writer.writerows(users_data)
        users_content = users_csv.getvalue()
        users_csv.close()
        
        return users_content
        
    except Exception as e:
        logger.error(f"Error in export_users_to_csv: {e}")
        return None

# Генерация дат на 7 дней вперед (НАЧИНАЯ С СЕГОДНЯШНЕГО ДНЯ)
def generate_dates():
    dates = []
    today = datetime.now()
    
    for i in range(0, 7):  # Начинаем с 0 (сегодня)
        date = today + timedelta(days=i)
        date_str = date.strftime("%d.%m.%Y")
        day_name = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][date.weekday()]
        
        # Для сегодняшнего дня добавляем пометку
        if i == 0:
            dates.append(f"{date_str} ({day_name}) - Сегодня")
        else:
            dates.append(f"{date_str} ({day_name})")
    
    return dates

# Получение занятого времени на конкретную дату
def get_booked_times(selected_date):
    try:
        # Убираем пометку " - Сегодня" из даты для поиска в базе
        clean_date = selected_date.replace(" - Сегодня", "")
        # Убираем день недели в скобках, оставляем только дату
        clean_date = clean_date.split(' (')[0]
        
        print(f"🔍 Поиск бронирований для даты: '{clean_date}'")
        
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT time, duration FROM bookings 
            WHERE day = ? AND status = 'confirmed'
        ''', (clean_date,))
        
        booked_slots = cursor.fetchall()
        conn.close()
        
        # Преобразуем в список занятых часов
        booked_hours = []
        for time_slot, duration in booked_slots:
            start_hour = int(time_slot.split(':')[0])
            for i in range(duration):
                booked_hours.append(start_hour + i)
        
        print(f"📅 Занятые часы на {clean_date}: {booked_hours}")
        return booked_hours
    except Exception as e:
        logger.error(f"Error in get_booked_times: {e}")
        return []

# Получение списка свободного времени на конкретную дату
def get_available_times(selected_date):
    try:
        # Убираем пометку " - Сегодня" из даты для поиска в базе
        clean_date = selected_date.replace(" - Сегодня", "")
        # Убираем день недели в скобках, оставляем только дату
        clean_date = clean_date.split(' (')[0]
        
        booked_hours = get_booked_times(clean_date)
        available_times = []
        
        # Определяем минимальное доступное время
        current_hour = datetime.now().hour
        current_minute = datetime.now().minute
        
        # Для сегодняшнего дня начинаем со следующего часа
        if " - Сегодня" in selected_date:
            start_hour = current_hour + 1 if current_minute > 0 else current_hour
            start_hour = max(start_hour, 9)  # Не раньше 9:00
        else:
            start_hour = 9  # Для других дней начинаем с 9:00
        
        # Все возможные временные слоты с start_hour до 21:00
        for hour in range(start_hour, 22):
            time_slot = f"{hour:02d}:00"
            if hour not in booked_hours:
                available_times.append(time_slot)
        
        print(f"📅 Свободные слоты на {clean_date}: {available_times}")
        return available_times
    except Exception as e:
        logger.error(f"Error in get_available_times: {e}")
        return []

# Проверка доступности времени на выбранную дату с учетом продолжительности
def is_time_available(selected_date, selected_time, duration):
    try:
        # Убираем пометку " - Сегодня" из даты для поиска в базе
        clean_date = selected_date.replace(" - Сегодня", "")
        # Убираем день недели в скобках, оставляем только дату
        clean_date = clean_date.split(' (')[0]
        
        booked_hours = get_booked_times(clean_date)
        start_hour = int(selected_time.split(':')[0])
        
        # Проверяем все часы в выбранном диапазоне
        for i in range(duration):
            check_hour = start_hour + i
            if check_hour in booked_hours:
                print(f"❌ Час {check_hour} занят на дату {clean_date}")
                return False
        
        print(f"✅ Время {selected_time} продолжительностью {duration} часов доступно на {clean_date}")
        return True
    except Exception as e:
        logger.error(f"Error in is_time_available: {e}")
        return False

# Функция для отправки напоминания администратору (каждые 30 минут)
async def send_reminder_to_admin(context: CallbackContext):
    job = context.job
    booking_id = job.data['booking_id']
    user_name = job.data['user_name']
    selected_date = job.data['selected_date']
    selected_time = job.data['selected_time']
    duration = job.data['duration']
    
    # Проверяем статус брони перед отправкой напоминания
    conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM bookings WHERE id = ?', (booking_id,))
    result = cursor.fetchone()
    conn.close()
    
    # Если бронь уже подтверждена или отменена, не отправляем напоминание
    if result and result[0] != 'pending':
        print(f"🔕 Напоминание администратору отменено - бронь {booking_id} уже обработана")
        job.schedule_removal()
        return
    
    reminder_text = f"""🔔 <b>НАПОМИНАНИЕ О НЕПОДТВЕРЖДЕННОЙ ЗАЯВКЕ!</b>

Заявка ожидает подтверждения уже более 30 минут:

👤 <b>Клиент</b>: {user_name}
📅 <b>Дата</b>: {selected_date}
🕐 <b>Время</b>: {selected_time}
⏱ <b>Продолжительность</b>: {duration} час(а)
🆔 <b>ID заявки</b>: {booking_id}

❗ <i>Пожалуйста, подтвердите или отклоните заявку как можно скорее!</i>"""

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить бронь", callback_data=f"confirm_{booking_id}"),
            InlineKeyboardButton("❌ Отклонить бронь", callback_data=f"cancel_{booking_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=reminder_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        print(f"🔔 Напоминание отправлено администратору о брони {booking_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить напоминание админу: {e}")

# Функция для отправки напоминания клиенту за 24 часа
async def send_24h_reminder_to_client(context: CallbackContext):
    try:
        job = context.job
        user_id = job.data['user_id']
        selected_date = job.data['selected_date']
        selected_time = job.data['selected_time']
        duration = job.data['duration']
        
        reminder_text = f"""🎵 <b>НАПОМИНАНИЕ О ЗАПИСИ</b>

⏰ До вашей сессии в студии осталось <b>24 часа</b>!

📅 <b>Дата</b>: {selected_date}
🕐 <b>Время</b>: {selected_time}
⏱ <b>Продолжительность</b>: {duration} час(а)

🏢 <b>MS Studio</b>
📍 <b>Адрес</b>: г. Ставрополь, ул. Спартака 8, 2-ой этаж

💡 <i>Пожалуйста, планируйте свое время заранее.</i>
🚗 <i>Рекомендуем приехать за 10-15 минут до начала сессии.</i>

📞 <b>По всем вопросам:</b> +7 (918) 880-52-92

🎶 <i>Ждем вас в студии!</i>"""
        
        await context.bot.send_message(
            chat_id=user_id,
            text=reminder_text,
            parse_mode='HTML'
        )
        print(f"🔔 24-часовое напоминание отправлено клиенту {user_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить 24-часовое напоминание клиенту {user_id}: {e}")

# Функция для отправки напоминания клиенту за 2 часа
async def send_2h_reminder_to_client(context: CallbackContext):
    try:
        job = context.job
        user_id = job.data['user_id']
        selected_date = job.data['selected_date']
        selected_time = job.data['selected_time']
        duration = job.data['duration']
        
        reminder_text = f"""🎵 <b>НАПОМИНАНИЕ О ЗАПИСИ</b>

⏰ До вашей сессии в студии осталось <b>2 часа</b>!

📅 <b>Дата</b>: {selected_date}
🕐 <b>Время</b>: {selected_time}
⏱ <b>Продолжительность</b>: {duration} час(а)

🏢 <b>MS Studio</b>
📍 <b>Адрес</b>: г. Ставрополь, ул. Спартака 8, 2-ой этаж

🚗 <i>Скоро начинаем! Рекомендуем приехать за 10-15 минут до начала.</i>
🎤 <i>Не забудьте взять все необходимое для записи!</i>

📞 <b>Если опаздываете:</b> +7 (918) 880-52-92

🎶 <i>До скорой встречи в студии!</i>"""
        
        await context.bot.send_message(
            chat_id=user_id,
            text=reminder_text,
            parse_mode='HTML'
        )
        print(f"🔔 2-часовое напоминание отправлено клиенту {user_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить 2-часовое напоминание клиенту {user_id}: {e}")

# Расчет времени для напоминаний
def calculate_reminder_times(selected_date, selected_time):
    try:
        # Убираем пометку " - Сегодня" и день недели из даты
        clean_date = selected_date.replace(" - Сегодня", "")
        # Убираем день недели в скобках, оставляем только дату
        clean_date = clean_date.split(' (')[0]
        
        print(f"🔧 Очистка даты: '{selected_date}' -> '{clean_date}'")
        
        # Парсим дату и время
        date_obj = datetime.strptime(clean_date, "%d.%m.%Y")
        time_obj = datetime.strptime(selected_time, "%H:%M")
        
        # Создаем datetime объекта начала сессии
        session_datetime = datetime(
            date_obj.year, date_obj.month, date_obj.day,
            time_obj.hour, time_obj.minute
        )
        
        current_datetime = datetime.now()
        
        # Время за 24 часа до сессии
        reminder_24h = session_datetime - timedelta(hours=24)
        
        # Время за 2 часа до сессии
        reminder_2h = session_datetime - timedelta(hours=2)
        
        # Проверяем, что напоминания будут в будущем
        delay_24h = (reminder_24h - current_datetime).total_seconds()
        delay_2h = (reminder_2h - current_datetime).total_seconds()
        
        print(f"⏰ Расчет напоминаний для {selected_date} {selected_time}:")
        print(f"   Сессия: {session_datetime}")
        print(f"   Сейчас: {current_datetime}")
        print(f"   Напоминание за 24ч: {reminder_24h} (через {delay_24h} сек)")
        print(f"   Напоминание за 2ч: {reminder_2h} (через {delay_2h} сек)")
        
        # Возвращаем только положительные задержки
        return (
            delay_24h if delay_24h > 0 else None,
            delay_2h if delay_2h > 0 else None
        )
        
    except Exception as e:
        logger.error(f"Error in calculate_reminder_times: {e}")
        print(f"❌ Ошибка парсинга даты: '{selected_date}'")
        return None, None

# Отправка уведомления администратору о новой заявке
async def send_admin_notification(context: CallbackContext, booking_id: int, user_name: str, selected_date: str, selected_time: str, duration: int, user_id: int, username: str):
    admin_message = f"""🎵 <b>НОВАЯ ЗАПИСЬ!</b>

👤 <b>Клиент</b>: {user_name}
📱 <b>Telegram</b>: @{username or 'без username'}
📅 <b>Дата</b>: {selected_date}
🕐 <b>Время</b>: {selected_time}
⏱ <b>Продолжительность</b>: {duration} час(а)
🆔 <b>ID клиента</b>: {user_id}
📋 <b>ID заявки</b>: {booking_id}

⏰ <i>Заявка ожидает подтверждения!</i>"""

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить бронь", callback_data=f"confirm_{booking_id}"),
            InlineKeyboardButton("❌ Отклонить бронь", callback_data=f"cancel_{booking_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=admin_message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        print(f"✅ Уведомление отправлено администратору {ADMIN_ID} о бронировании {booking_id}")
        return True
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление админу: {e}")
        print(f"❌ Ошибка отправки уведомления администратору: {e}")
        return False

# Команда /start
async def start(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    reply_markup = get_main_keyboard(user_id)
    
    await update.message.reply_text(
        '🎧 Добро пожаловать в бот студии звукозаписи MS Studio!\n\n'
        'Выберите действие:',
        reply_markup=reply_markup
    )

# Показываем расписание
async def show_schedule(update: Update, context: CallbackContext) -> None:
    try:
        user_id = update.message.from_user.id
        username = update.message.from_user.username or 'без username'
        first_name = update.message.from_user.first_name
        last_name = update.message.from_user.last_name or ''
        
        # Обновляем статистику пользователя
        update_user_stats(user_id, username, first_name, last_name)
        
        dates = generate_dates()
        
        # Создаем красивое расписание с эмодзи и форматированием
        schedule_text = "🎵 <b>РАСПИСАНИЕ СТУДИИ НА 7 ДНЕЙ</b> 🎵\n\n"
        schedule_text += "⏰ <i>Часы работы: 9:00 - 21:00</i>\n\n"
        
        for date in dates:
            schedule_text += f"🎯 <b>{date}</b>\n"
            
            # Получаем занятые часы для этой даты
            booked_hours = get_booked_times(date)
            
            # Определяем минимальное доступное время для отображения
            current_hour = datetime.now().hour
            current_minute = datetime.now().minute
            
            # Для сегодняшнего дня начинаем со следующего часа
            if " - Сегодня" in date:
                start_hour = current_hour + 1 if current_minute > 0 else current_hour
                start_hour = max(start_hour, 9)  # Не раньше 9:00
            else:
                start_hour = 9  # Для других дней начинаем с 9:00
            
            # Показываем все часы с start_hour до 21:00
            for hour in range(start_hour, 22):
                time_slot = f"{hour:02d}:00"
                if hour in booked_hours:
                    schedule_text += f"   ❌ {time_slot} - <i>Занято</i>\n"
                else:
                    schedule_text += f"   ✅ {time_slot} - <b>Свободно</b>\n"
            
            schedule_text += "\n" + "─" * 40 + "\n\n"
        
        schedule_text += "💡 <b>Для бронирования нажмите '🎵 Забронировать'</b>"
        
        await update.message.reply_text(schedule_text, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error in show_schedule: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при загрузке расписания. Попробуйте позже."
        )

# Показываем цены
async def show_prices(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    prices_text = """🎹 <b>ПРАЙС-ЛИСТ СТУДИИ ЗВУКОЗАПИСИ</b> 🎹

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎤 <b>ОСНОВНЫЕ УСЛУГИ ЗАПИСИ</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎵 <b>Запись</b>
💰 <i>1000 ₽/час</i>
📝 Запись вокала или многодорожечная запись любых музыкальных инструментов (до 16-ти каналов)

🎵 <b>Трек под минус</b>
💰 <i>5 000 ₽</i>
📝 Запись вокала под ваш готовый минус
✅ В услугу входит:
   • 2 часа записи
   • Ручная коррекция вокала
   • Сведение вокала с минусом
   • Мастеринг трека

🎵 <b>Запись песни</b>
💰 <i>12 000 ₽</i>
📝 Полный цикл производства трека от записи до мастеринга
✅ В услугу входит:
   • Запись вокала и инструментов (до 7 часов)
   • Ручная коррекция вокала
   • Ритмические коррекции инструментов
   • Эффекты и саундизайн
   • Сведение и мастеринг
   • До 3-х пакетов правок

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎧 <b>ОБРАБОТКА И ПРОДАКШЕН</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎵 <b>Сведение и мастеринг</b>
💰 <i>7 500 ₽</i>
📝 Полное сведение мультитрека
✅ В услугу входит:
   • Ручная коррекция вокала
   • Ритмические коррекции инструментов
   • Эффекты и саундизайн
   • Сведение и мастеринг
   • До 3-х пакетов правок

🎵 <b>Сведение вместе с артистом</b>
💰 <i>1 500 ₽/час</i>
📝 Совместная работа над сведением с участием артиста
✅ Идеально для:
   • Точной реализации вашего видения звука
   • Обучения процессу сведения
   • Быстрой обратной связи и правок

🎵 <b>Аранжировка</b>
💰 <i>от 10 000 ₽</i>
📝 Написание бита/аранжировки с нуля по референсу или вместе с артистом
✅ В услугу входит:
   • Непосредственное написание аранжировки
   • Эффекты, саундизайн
   • Типовое сведение
   • Экспорт мультитрека
   • Полные права
   • До 3-х пакетов правок

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👨‍🏫 <b>ОБУЧЕНИЕ</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎵 <b>Обучение звукорежиссуре</b>
💰 <i>1 500 ₽/занятие</i>
📝 Обучение звукорежиссуре, написанию аранжировок, битов и студийной работе

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛠️ <b>ДОПОЛНИТЕЛЬНЫЕ УСЛУГИ</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• 🎛️ <b>Мастеринг</b>: 2 000 ₽
• 🎤 <b>Ручная коррекция вокала</b>: 750 ₽/трек
• 🥁 <b>Ритмические коррекции инструментов</b>: 1 500 ₽/инструмент
• 📝 <b>Написание текста</b>: от 5 000 ₽
• 🎨 <b>Обложка к релизу</b>: от 3 000 ₽
• 🌐 <b>Дистрибуция</b>: 750 ₽
• 💼 <b>Коммерческий трек</b>: цены обсуждаются индивидуально

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏢 <b>АРЕНДА СТУДИИ</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎵 <b>Для самостоятельной работы:</b>
• ⏱️ 3 часа — 3 000 ₽
• ⏱️ 6 часов — 5 500 ₽
• ⏱️ 12 часов — 10 000 ₽

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 <i>Индивидуальный подход к каждому клиенту</i>

📞 <b>Для записи и консультации - свяжитесь с администратором!</b>"""
    
    await update.message.reply_text(prices_text, parse_mode='HTML')

# Связь с администратором
async def contact_admin(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    admin_info = """👨‍💻 <b>Связь с администратором</b>

📞 <b>Телефон</b>: +7 (918) 880-52-92
📱 <b>Telegram</b>: @Solnyshkin_Mikhail
🔗 <b>Telegram канал студии</b>: https://t.me/+UPYAZ7ULL403YmEy
👥 <b>VK группа</b>: https://vk.com/m_s_studio?from=groups
🌐 <b>Сайт студии</b>: https://msstudio-stav.ru/
⏰ <b>Время связи</b>: 9:00-22:00

💬 <i>Напишите или позвоните для уточнения деталей!</i>
🎵 <i>Поможем подобрать оптимальное решение для вашего проекта</i>"""
    
    await update.message.reply_text(admin_info, parse_mode='HTML')

# Админ панель
async def show_admin_panel(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    
    # Проверяем, является ли пользователь администратором
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    # НОВАЯ РАССТАНОВКА КНОПОК
    admin_keyboard = [
        ['📝 Добавить запись', '❌ Отменить запись'],
        ['🗓️ Админ расписание', '📢 Рассылка'],
        ['📈 Аналитика', '📊 Статистика пользователей'],
        ['🔙 В главное меню']
    ]
    reply_markup = ReplyKeyboardMarkup(admin_keyboard, resize_keyboard=True)
    
    admin_text = """👑 <b>АДМИН ПАНЕЛЬ</b>

📝 <b>Добавить запись</b> - ручное добавление бронирования для клиента
❌ <b>Отменить запись</b> - отмена бронирований пользователей
🗓️ <b>Админ расписание</b> - управление бронированиями
📢 <b>Рассылка</b> - отправка сообщений всем пользователям
📈 <b>Аналитика</b> - детальные отчеты и тренды
📊 <b>Статистика пользователей</b> - базовая статистика

Выберите действие:"""
    
    await update.message.reply_text(admin_text, parse_mode='HTML', reply_markup=reply_markup)

# Меню добавления записи
async def show_add_booking_menu(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "📝 <b>ДОБАВЛЕНИЕ ЗАПИСИ ДЛЯ КЛИЕНТА</b>\n\n"
        "📅 <b>Введите дату для записи</b>\n\n"
        "Формат: <b>ДД.ММ.ГГГГ</b>\n"
        "Например: 25.12.2024\n\n"
        "📝 Введите дату в указанном формате:",
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
    )
    
    return ADMIN_ADD_DAY

# Обработка ввода даты для добавления записи
async def handle_admin_add_date(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    user_input = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if user_input == '🔙 Назад':
        await show_admin_panel(update, context)
        return ConversationHandler.END
    
    try:
        selected_date = datetime.strptime(user_input, "%d.%m.%Y")
        today = datetime.now()
        
        # Проверяем что дата не в прошлом
        if selected_date.date() < today.date():
            await update.message.reply_text(
                '❌ Нельзя выбрать прошедшую дату.\n'
                'Пожалуйста, введите сегодняшнюю или будущую дату:',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return ADMIN_ADD_DAY
        
        # Проверяем что дата не слишком далеко (максимум 3 месяца)
        max_date = today + timedelta(days=90)
        if selected_date > max_date:
            await update.message.reply_text(
                '❌ Бронирование доступно только на 3 месяца вперед.\n'
                'Пожалуйста, введите более близкую дату:',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return ADMIN_ADD_DAY
        
        # Форматируем дату для отображения
        date_str = selected_date.strftime("%d.%m.%Y")
        day_name = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][selected_date.weekday()]
        
        # Добавляем пометку "Сегодня" если это сегодняшняя дата
        if selected_date.date() == today.date():
            formatted_date = f"{date_str} ({day_name}) - Сегодня"
        else:
            formatted_date = f"{date_str} ({day_name})"
        
        # Сохраняем ОБЕ версии даты
        context.user_data['admin_booking_day'] = formatted_date  # для отображения
        context.user_data['admin_booking_clean_date'] = date_str  # для базы данных
        
        # Показываем доступное время для выбранной даты - используем форматированную дату для проверки
        available_times = get_available_times(formatted_date)
        
        if not available_times:
            await update.message.reply_text(
                f'❌ На {formatted_date} нет свободного времени.\n'
                f'Пожалуйста, выберите другую дату:',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return ADMIN_ADD_DAY
        
        time_keyboard = []
        row = []
        for i, time_slot in enumerate(available_times):
            row.append(time_slot)
            if len(row) == 2 or i == len(available_times) - 1:
                time_keyboard.append(row)
                row = []
        
        time_keyboard.append(['🔙 Назад'])
        
        reply_markup = ReplyKeyboardMarkup(time_keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f'📅 Выбрана дата: <b>{formatted_date}</b>\n'
            f'🕐 <b>ВЫБЕРИТЕ ВРЕМЯ НАЧАЛА СЕССИИ</b>\n\n'
            f'🎯 Доступное время:',
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        return ADMIN_ADD_TIME
        
    except ValueError:
        await update.message.reply_text(
            '❌ Неправильный формат даты.\n'
            'Пожалуйста, введите дату в формате <b>ДД.ММ.ГГГГ</b>\n'
            'Например: 25.12.2024',
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return ADMIN_ADD_DAY

# Обработка выбора времени для админского добавления записи
async def handle_admin_add_time(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    selected_time = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ADMIN_ADD_TIME
    
    if selected_time == '🔙 Назад':
        await show_add_booking_menu(update, context)
        return ADMIN_ADD_DAY
    
    formatted_date = context.user_data['admin_booking_day']
    available_times = get_available_times(formatted_date)
    
    if selected_time not in available_times:
        await update.message.reply_text(
            f'❌ Время {selected_time} недоступно.\n'
            f'Пожалуйста, выберите другое время:',
            reply_markup=ReplyKeyboardMarkup([available_times[i:i+2] for i in range(0, len(available_times), 2)] + [['🔙 Назад']], resize_keyboard=True)
        )
        return ADMIN_ADD_TIME
    
    context.user_data['admin_booking_time'] = selected_time
    
    duration_keyboard = [
        ['1 час', '2 часа'],
        ['3 часа', '4 часа'],
        ['🔙 Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(duration_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f'📅 Дата: <b>{formatted_date}</b>\n'
        f'🕐 Время: <b>{selected_time}</b>\n\n'
        f'⏱ <b>ВЫБЕРИТЕ ПРОДОЛЖИТЕЛЬНОСТЬ СЕССИИ</b>:',
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
    return ADMIN_ADD_DURATION

# Обработка выбора продолжительности для админского добавления записи
async def handle_admin_add_duration(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    duration_text = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ADMIN_ADD_DURATION
    
    if duration_text == '🔙 Назад':
        formatted_date = context.user_data['admin_booking_day']
        available_times = get_available_times(formatted_date)
        
        time_keyboard = []
        row = []
        for i, time_slot in enumerate(available_times):
            row.append(time_slot)
            if len(row) == 2 or i == len(available_times) - 1:
                time_keyboard.append(row)
                row = []
        
        time_keyboard.append(['🔙 Назад'])
        
        reply_markup = ReplyKeyboardMarkup(time_keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f'📅 Выбрана дата: <b>{formatted_date}</b>\n'
            f'🕐 <b>ВЫБЕРИТЕ ВРЕМЯ НАЧАЛА СЕССИИ</b>\n\n'
            f'🎯 Доступное время:',
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return ADMIN_ADD_TIME
    
    duration_map = {
        '1 час': 1, 
        '2 часа': 2, 
        '3 часа': 3, 
        '4 часа': 4
    }
    
    if duration_text not in duration_map:
        await update.message.reply_text(
            '❌ Пожалуйста, выберите продолжительность из списка:',
            reply_markup=ReplyKeyboardMarkup([
                ['1 час', '2 часа'],
                ['3 часа', '4 часа'],
                ['🔙 Назад']
            ], resize_keyboard=True)
        )
        return ADMIN_ADD_DURATION
    
    duration = duration_map[duration_text]
    formatted_date = context.user_data['admin_booking_day']
    selected_time = context.user_data['admin_booking_time']
    
    # ПРОВЕРЯЕМ ДОСТУПНОСТЬ ВРЕМЕНИ С УЧЕТОМ ПРОДОЛЖИТЕЛЬНОСТИ
    if not is_time_available(formatted_date, selected_time, duration):
        await update.message.reply_text(
            f'❌ Время {selected_time} продолжительностью {duration} час(а) недоступно.\n'
            f'Пожалуйста, выберите другое время или продолжительность.',
            reply_markup=ReplyKeyboardMarkup([
                ['1 час', '2 часа'],
                ['3 часа', '4 часа'],
                ['🔙 Назад']
            ], resize_keyboard=True)
        )
        return ADMIN_ADD_DURATION
    
    context.user_data['admin_booking_duration'] = duration
    
    await update.message.reply_text(
        f'📋 <b>ВВЕДИТЕ ИМЯ КЛИЕНТА</b>\n\n'
        f'📅 Дата: <b>{formatted_date}</b>\n'
        f'🕐 Время: <b>{selected_time}</b>\n'
        f'⏱ Продолжительность: <b>{duration} час(а)</b>\n\n'
        f'✍️ <b>Введите имя клиента:</b>',
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
    )
    
    return ADMIN_ADD_CLIENT_NAME

# Обработка ввода имени клиента
async def handle_admin_add_client_name(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    client_name = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if client_name == '🔙 Назад':
        formatted_date = context.user_data['admin_booking_day']
        selected_time = context.user_data['admin_booking_time']
        
        duration_keyboard = [
            ['1 час', '2 часа'],
            ['3 часа', '4 часа'],
            ['🔙 Назад']
        ]
        reply_markup = ReplyKeyboardMarkup(duration_keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f'📅 Дата: <b>{formatted_date}</b>\n'
            f'🕐 Время: <b>{selected_time}</b>\n\n'
            f'⏱ <b>ВЫБЕРИТЕ ПРОДОЛЖИТЕЛЬНОСТЬ СЕССИИ</b>:',
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return ADMIN_ADD_DURATION
    
    context.user_data['admin_booking_client_name'] = client_name
    
    await update.message.reply_text(
        f'📋 <b>ВВЕДИТЕ КОНТАКТ КЛИЕНТА</b>\n\n'
        f'📅 Дата: <b>{context.user_data["admin_booking_day"]}</b>\n'
        f'🕐 Время: <b>{context.user_data["admin_booking_time"]}</b>\n'
        f'⏱ Продолжительность: <b>{context.user_data["admin_booking_duration"]} час(а)</b>\n'
        f'👤 Имя клиента: <b>{client_name}</b>\n\n'
        f'📞 <b>Введите контакт клиента (телефон или Telegram):</b>\n'
        f'💡 <i>Эта информация будет отображаться в админ расписании</i>',
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
    )
    
    return ADMIN_ADD_CLIENT_CONTACT

# Обработка ввода контакта клиента и завершение добавления записи
async def handle_admin_add_client_contact(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    client_contact = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if client_contact == '🔙 Назад':
        await update.message.reply_text(
            f'📋 <b>ВВЕДИТЕ ИМЯ КЛИЕНТА</b>\n\n'
            f'📅 Дата: <b>{context.user_data["admin_booking_day"]}</b>\n'
            f'🕐 Время: <b>{context.user_data["admin_booking_time"]}</b>\n'
            f'⏱ Продолжительность: <b>{context.user_data["admin_booking_duration"]} час(а)</b>\n\n'
            f'✍️ <b>Введите имя клиента:</b>',
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return ADMIN_ADD_CLIENT_NAME
    
    # Извлекаем данные из контекста
    clean_date = context.user_data['admin_booking_clean_date']
    formatted_date = context.user_data['admin_booking_day']
    selected_time = context.user_data['admin_booking_time']
    duration = context.user_data['admin_booking_duration']
    client_name = context.user_data['admin_booking_client_name']
    
    # ФИНАЛЬНАЯ ПРОВЕРКА ДОСТУПНОСТИ ПЕРЕД СОХРАНЕНИЕМ
    if not is_time_available(formatted_date, selected_time, duration):
        await update.message.reply_text(
            f'❌ К сожалению, время {selected_time} продолжительностью {duration} час(а) стало недоступно.\n'
            f'Пожалуйста, начните процесс заново и выберите другое время.',
            parse_mode='HTML',
            reply_markup=get_main_keyboard(user_id)
        )
        
        # Очищаем данные из контекста
        context.user_data.pop('admin_booking_day', None)
        context.user_data.pop('admin_booking_clean_date', None)
        context.user_data.pop('admin_booking_time', None)
        context.user_data.pop('admin_booking_duration', None)
        context.user_data.pop('admin_booking_client_name', None)
        
        return ConversationHandler.END
    
    # Сохраняем бронирование в базу данных с пометкой, что добавлено админом
    conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO bookings (user_id, user_name, day, time, duration, status, created_at, added_by_admin, client_contact)
        VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?, ?)
    ''', (None, client_name, clean_date, selected_time, duration, get_current_time(), True, client_contact))
    booking_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Сообщение администратору об успешном добавлении
    success_text = f"""✅ <b>ЗАПИСЬ УСПЕШНО ДОБАВЛЕНА!</b>

📅 <b>Дата</b>: {formatted_date}
🕐 <b>Время</b>: {selected_time}
⏱ <b>Продолжительность</b>: {duration} час(а)
👤 <b>Клиент</b>: {client_name}
📞 <b>Контакт</b>: {client_contact}
🆔 <b>ID брони</b>: {booking_id}

✅ <i>Запись добавлена в расписание и отмечена как подтвержденная</i>"""
    
    await update.message.reply_text(
        success_text,
        parse_mode='HTML',
        reply_markup=get_main_keyboard(user_id)
    )
    
    # Очищаем данные из контекста
    context.user_data.pop('admin_booking_day', None)
    context.user_data.pop('admin_booking_clean_date', None)
    context.user_data.pop('admin_booking_time', None)
    context.user_data.pop('admin_booking_duration', None)
    context.user_data.pop('admin_booking_client_name', None)
    
    return ConversationHandler.END

# Статистика пользователей
async def show_user_statistics(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    
    # Проверяем, является ли пользователь администратором
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    try:
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Получаем общую статистику
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # Используем локальное время для подсчета активных пользователей
        current_time = datetime.now()
        week_ago = current_time - timedelta(days=7)
        month_ago = current_time - timedelta(days=30)
        
        # Получаем всех пользователей и фильтруем локально
        cursor.execute('''
            SELECT user_id, username, first_name, last_name, first_seen, last_activity, bookings_count, total_hours
            FROM users 
            ORDER BY last_activity DESC
        ''')
        users = cursor.fetchall()
        
        # Подсчитываем активных пользователей
        active_users_7d = 0
        active_users_30d = 0
        
        for user in users:
            last_activity = parse_db_time(user[5])
            if last_activity >= week_ago:
                active_users_7d += 1
            if last_activity >= month_ago:
                active_users_30d += 1
        
        cursor.execute('SELECT COUNT(*) FROM bookings WHERE status = "confirmed"')
        total_bookings = cursor.fetchone()[0]
        
        cursor.execute('SELECT SUM(duration) FROM bookings WHERE status = "confirmed"')
        total_hours = cursor.fetchone()[0] or 0
        
        conn.close()
        
        # Формируем сообщение со статистикой
        stats_text = f"""📊 <b>СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ</b>

👥 <b>Общая статистика:</b>
• Всего пользователей: <b>{total_users}</b>
• Активных за 7 дней: <b>{active_users_7d}</b>
• Активных за 30 дней: <b>{active_users_30d}</b>
• Всего бронирований: <b>{total_bookings}</b>
• Всего часов: <b>{total_hours}</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 <b>СПИСОК ПОЛЬЗОВАТЕЛЕЙ</b> (отсортирован по активности):
"""
        
        # Добавляем информацию о каждом пользователе
        for i, user in enumerate(users, 1):
            user_id, username, first_name, last_name, first_seen, last_activity, bookings_count, total_hours = user
            
            # Парсим время из базы данных
            first_seen_dt = parse_db_time(first_seen)
            last_activity_dt = parse_db_time(last_activity)
            
            # Форматируем даты для отображения
            first_seen_date = first_seen_dt.strftime('%d.%m.%Y %H:%M')
            last_activity_date = last_activity_dt.strftime('%d.%m.%Y %H:%M')
            
            # Определяем активность
            days_since_activity = (datetime.now() - last_activity_dt).days
            
            if days_since_activity == 0:
                activity_status = "🟢 Сегодня"
            elif days_since_activity == 1:
                activity_status = "🟢 Вчера"
            elif days_since_activity <= 7:
                activity_status = "🟡 Неделю назад"
            elif days_since_activity <= 30:
                activity_status = "🟠 Месяц назад"
            else:
                activity_status = "🔴 Давно"
            
            # Формируем ссылку на пользователя
            user_link = f"<a href=\"tg://user?id={user_id}\">{first_name} {last_name}</a>" if first_name or last_name else f"<a href=\"tg://user?id={user_id}\">Пользователь</a>"
            username_display = f"@{username}" if username else "без username"
            
            stats_text += f"""
{i}. {user_link}
   📱 {username_display}
   📅 Первый визит: {first_seen_date}
   ⏰ Последняя активность: {last_activity_date}
   🎵 Бронирований: {bookings_count}
   ⏱️ Всего часов: {total_hours}
   🔄 {activity_status}
"""
            
            # Ограничиваем вывод чтобы сообщение не было слишком длинным
            if i >= 20:
                stats_text += f"\n... и еще {len(users) - 20} пользователей"
                break
        
        stats_text += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 <i>Всего пользователей в базе: {len(users)}</i>
🔄 <i>Для обновления статистики нажмите "📊 Статистика пользователей" еще раз</i>"""
        
        await update.message.reply_text(stats_text, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error in show_user_statistics: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при загрузке статистики пользователей."
        )

# Меню рассылки
async def show_broadcast_menu(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    # Получаем статистику пользователей
    all_users = get_all_users()
    total_users = len(all_users)
    
    broadcast_text = f"""📢 <b>РАССЫЛКА СООБЩЕНИЙ</b>

👥 Всего пользователей в базе: <b>{total_users}</b>

💡 <b>Как работает рассылка:</b>
• Вы пишете сообщение (текст, фото, видео)
• Бот отправляет его всем пользователям
• Вы получаете статистику доставки

⚠️ <b>Внимание!</b> Рассылка работает только для пользователей, которые начали диалог с ботом.

✍️ <b>Введите ваше сообщение для рассылки:</b>"""
    
    await update.message.reply_text(
        broadcast_text,
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
    )
    
    return BROADCAST_MESSAGE

# Обработка сообщения для рассылки
async def handle_broadcast_message(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    # Сохраняем сообщение для рассылки
    context.user_data['broadcast_message'] = update.message.text
    context.user_data['broadcast_message_type'] = 'text'
    
    # Получаем список пользователей
    all_users = get_all_users()
    total_users = len(all_users)
    
    # Показываем предпросмотр и подтверждение
    preview_text = f"""📢 <b>ПРЕДПРОСМОТР РАССЫЛКИ</b>

👥 <b>Будет отправлено:</b> {total_users} пользователям

💬 <b>Сообщение:</b>
{update.message.text}

✅ <b>Подтвердите отправку?</b>"""
    
    keyboard = [
        ['✅ Да, отправить всем', '🔙 Назад']
    ]
    
    await update.message.reply_text(
        preview_text,
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    
    return BROADCAST_CONFIRM

# Обработка медиа-сообщений для рассылки
async def handle_broadcast_media(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    # Определяем тип медиа
    if update.message.photo:
        context.user_data['broadcast_media'] = update.message.photo[-1].file_id
        context.user_data['broadcast_message_type'] = 'photo'
        caption = update.message.caption or ''
        context.user_data['broadcast_caption'] = caption
    elif update.message.video:
        context.user_data['broadcast_media'] = update.message.video.file_id
        context.user_data['broadcast_message_type'] = 'video'
        caption = update.message.caption or ''
        context.user_data['broadcast_caption'] = caption
    else:
        await update.message.reply_text(
            "❌ Неподдерживаемый тип сообщения. Используйте текст, фото или видео.",
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return BROADCAST_MESSAGE
    
    # Получаем список пользователей
    all_users = get_all_users()
    total_users = len(all_users)
    
    # Показываем предпросмотр и подтверждение
    media_type = "📷 Фото" if context.user_data['broadcast_message_type'] == 'photo' else "🎥 Видео"
    
    preview_text = f"""📢 <b>ПРЕДПРОСМОТР РАССЫЛКИ</b>

👥 <b>Будет отправлено:</b> {total_users} пользователям

📋 <b>Тип:</b> {media_type}
💬 <b>Подпись:</b> {caption if caption else 'Без подписи'}

✅ <b>Подтвердите отправку?</b>"""
    
    keyboard = [
        ['✅ Да, отправить всем', '🔙 Назад']
    ]
    
    await update.message.reply_text(
        preview_text,
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    
    return BROADCAST_CONFIRM

# Подтверждение и отправка рассылки
async def handle_broadcast_confirmation(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    choice = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if choice == '🔙 Назад':
        await update.message.reply_text(
            "❌ Рассылка отменена.",
            reply_markup=get_main_keyboard(user_id)
        )
        return ConversationHandler.END
    
    if choice == '✅ Да, отправить всем':
        # Начинаем рассылку
        all_users = get_all_users()
        total_users = len(all_users)
        
        progress_message = await update.message.reply_text(
            f"🔄 <b>НАЧИНАЮ РАССЫЛКУ...</b>\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Отправлено: 0/{total_users}\n"
            f"❌ Ошибок: 0",
            parse_mode='HTML'
        )
        
        success_count = 0
        error_count = 0
        message_type = context.user_data.get('broadcast_message_type', 'text')
        
        # Отправляем сообщение каждому пользователю
        for i, user_id in enumerate(all_users, 1):
            try:
                if message_type == 'text':
                    message_text = context.user_data.get('broadcast_message', '')
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode='HTML'
                    )
                elif message_type == 'photo':
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=context.user_data.get('broadcast_media'),
                        caption=context.user_data.get('broadcast_caption', ''),
                        parse_mode='HTML'
                    )
                elif message_type == 'video':
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=context.user_data.get('broadcast_media'),
                        caption=context.user_data.get('broadcast_caption', ''),
                        parse_mode='HTML'
                    )
                
                success_count += 1
                
                # Обновляем прогресс каждые 10 отправок или для последнего сообщения
                if i % 10 == 0 or i == total_users:
                    await context.bot.edit_message_text(
                        chat_id=update.message.chat_id,
                        message_id=progress_message.message_id,
                        text=f"🔄 <b>РАССЫЛКА В ПРОЦЕССЕ...</b>\n\n"
                             f"👥 Всего пользователей: {total_users}\n"
                             f"✅ Отправлено: {i}/{total_users}\n"
                             f"❌ Ошибок: {error_count}",
                        parse_mode='HTML'
                    )
                
                # Небольшая задержка чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.1)
                
            except Exception as e:
                error_count += 1
                logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
                continue
        
        # Финальная статистика
        result_text = f"""📊 <b>РАССЫЛКА ЗАВЕРШЕНА</b>

👥 Всего пользователей: {total_users}
✅ Успешно отправлено: {success_count}
❌ Не удалось отправить: {error_count}
📊 Процент доставки: {round((success_count / total_users) * 100, 2) if total_users > 0 else 0}%

💡 <i>Сообщение не доставляется пользователям, которые:\n• Заблокировали бота\n• Никогда не начинали диалог</i>"""
        
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=progress_message.message_id,
            text=result_text,
            parse_mode='HTML'
        )
        
        # Очищаем данные рассылки
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('broadcast_media', None)
        context.user_data.pop('broadcast_message_type', None)
        context.user_data.pop('broadcast_caption', None)
        
        await update.message.reply_text(
            "✅ Рассылка завершена!",
            reply_markup=get_main_keyboard(user_id)
        )
    
    return ConversationHandler.END

# Отмена рассылки
async def cancel_broadcast(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    await update.message.reply_text(
        "❌ Рассылка отменена.",
        reply_markup=get_main_keyboard(user_id)
    )
    
    # Очищаем данные рассылки
    context.user_data.pop('broadcast_message', None)
    context.user_data.pop('broadcast_media', None)
    context.user_data.pop('broadcast_message_type', None)
    context.user_data.pop('broadcast_caption', None)
    
    return ConversationHandler.END

# Меню аналитики
async def show_analytics_menu(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    analytics_keyboard = [
        ['📈 Аналитика за 7 дней', '📊 Аналитика за 30 дней'],
        ['📅 Аналитика за 90 дней', '🎯 Произвольный период'],
        ['🔙 Назад в админ-панель']
    ]
    reply_markup = ReplyKeyboardMarkup(analytics_keyboard, resize_keyboard=True)
    
    analytics_text = """📈 <b>АНАЛИТИКА</b>

Выберите период для анализа:

📈 <b>Аналитика за 7 дней</b> - краткосрочная статистика
📊 <b>Аналитика за 30 дней</b> - стандартный отчет за месяц  
📅 <b>Аналитика за 90 дней</b> - долгосрочный анализ трендов
🎯 <b>Произвольный период</b> - ввод любого количества дней

Отчет включает:
• Общую статистику бронирований
• Распределение по дням недели
• Популярные часы записи
• Топ клиентов
• Динамику отмен
• Ежемесячные тренды"""

    await update.message.reply_text(
        analytics_text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
    return ANALYTICS_MENU

# Обработка выбора периода аналитики
async def handle_analytics_period(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    choice = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    period_map = {
        '📈 Аналитика за 7 дней': 7,
        '📊 Аналитика за 30 дней': 30,
        '📅 Аналитика за 90 дней': 90
    }
    
    if choice in period_map:
        period_days = period_map[choice]
        await show_advanced_analytics(update, context, period_days)
        return ConversationHandler.END
    
    elif choice == '🎯 Произвольный период':
        await update.message.reply_text(
            "📅 <b>ВВЕДИТЕ КОЛИЧЕСТВО ДНЕЙ ДЛЯ АНАЛИЗА</b>\n\n"
            "Например: <b>14</b> (для анализа за 2 недели)\n"
            "Максимум: 365 дней\n\n"
            "Введите число:",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return ANALYTICS_PERIOD
    
    elif choice == '🔙 Назад в админ-панель':
        await show_admin_panel(update, context)
        return ConversationHandler.END
    
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите период из меню:",
            reply_markup=ReplyKeyboardMarkup([
                ['📈 Аналитика за 7 дней', '📊 Аналитика за 30 дней'],
                ['📅 Аналитика за 90 дней', '🎯 Произвольный период'],
                ['🔙 Назад в админ-панель']
            ], resize_keyboard=True)
        )
        return ANALYTICS_MENU

# Обработка произвольного периода
async def handle_custom_period(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    user_input = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if user_input == '🔙 Назад':
        await show_analytics_menu(update, context)
        return ANALYTICS_MENU
    
    try:
        period_days = int(user_input)
        if period_days <= 0:
            await update.message.reply_text(
                "❌ Число должно быть положительным.\nПожалуйста, введите корректное число:",
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return ANALYTICS_PERIOD
        
        if period_days > 365:
            await update.message.reply_text(
                "❌ Максимальный период - 365 дней.\nПожалуйста, введите меньшее число:",
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return ANALYTICS_PERIOD
        
        await show_advanced_analytics(update, context, period_days)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ Пожалуйста, введите корректное число:\n\n"
            "Например: <b>14</b> (для анализа за 2 недели)",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return ANALYTICS_PERIOD

# Показ расширенной аналитики
async def show_advanced_analytics(update: Update, context: CallbackContext, period_days: int):
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    # Показываем сообщение о загрузке
    loading_message = await update.message.reply_text(
        f"📊 <b>ЗАГРУЖАЮ АНАЛИТИКУ...</b>\n\n"
        f"Период: последние <b>{period_days}</b> дней\n"
        f"⏳ Это может занять несколько секунд",
        parse_mode='HTML'
    )
    
    # Получаем аналитику
    analytics = get_advanced_analytics(period_days)
    
    if not analytics:
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=loading_message.message_id,
            text="❌ Произошла ошибка при загрузке аналитики."
        )
        return
    
    # Формируем отчет
    report_text = f"""📈 <b>АНАЛИТИКА СТУДИИ</b>
    
⏰ <b>Период анализа:</b> последние {period_days} дней
📅 <b>Дата отчета:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>ОСНОВНЫЕ ПОКАЗАТЕЛИ</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• 🎵 <b>Всего бронирований:</b> {analytics['total_bookings']}
• ⏱️ <b>Всего часов:</b> {analytics['total_hours']}
• 📍 <b>Средняя продолжительность:</b> {analytics['avg_session_length']} часа
• 👥 <b>Уникальных клиентов:</b> {analytics['unique_clients']}
• ❌ <b>Отменено броней:</b> {analytics['cancelled_count']} ({round((analytics['cancelled_count'] / analytics['total_count']) * 100, 1) if analytics['total_count'] > 0 else 0}%)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 <b>ПО ДНЯМ НЕДЕЛИ</b> (топ-5)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # Добавляем статистику по дням недели
    days_to_show = analytics['days_stats'][:5] if len(analytics['days_stats']) > 5 else analytics['days_stats']
    if days_to_show:
        for i, (day_name, bookings_count, hours_count) in enumerate(days_to_show, 1):
            report_text += f"• {day_name}: {bookings_count} записей, {hours_count} часов\n"
    else:
        report_text += "• Нет данных за выбранный период\n"
    
    report_text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🕐 <b>ПОПУЛЯРНЫЕ ЧАСЫ</b> (топ-5)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # Добавляем статистику по часам
    if analytics['hours_stats']:
        for i, (hour, bookings_count) in enumerate(analytics['hours_stats'], 1):
            report_text += f"• {hour}:00 - {bookings_count} записей\n"
    else:
        report_text += "• Нет данных за выбранный период\n"
    
    report_text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👑 <b>ТОП КЛИЕНТОВ</b> (по количеству записей)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # Добавляем топ клиентов
    if analytics['top_clients']:
        for i, (client_id, first_name, last_name, client_name, bookings_count, total_hours) in enumerate(analytics['top_clients'], 1):
            display_name = client_name if client_name else f"{first_name} {last_name}".strip()
            if not display_name:
                display_name = f"Клиент ID {client_id}" if client_id else "Клиент"
            
            report_text += f"{i}. {display_name}\n   📞 {bookings_count} записей, ⏱️ {total_hours} часов\n"
    else:
        report_text += "• Нет данных за выбранный период\n"
    
    report_text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 <b>МЕСЯЧНАЯ ДИНАМИКА</b> (последние 6 месяцев)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # Добавляем месячную динамику
    if analytics['monthly_stats']:
        for month, bookings_count, hours_count in analytics['monthly_stats']:
            month_date = datetime.strptime(month, '%Y-%m')
            month_name = month_date.strftime('%B %Y')
            report_text += f"• {month_name}: {bookings_count} записей, {hours_count} часов\n"
    else:
        report_text += "• Нет данных за выбранный период\n"
    
    report_text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 <b>РЕКОМЕНДАЦИИ</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    # Простые рекомендации на основе данных
    recommendations = []
    
    # Рекомендация по популярным дням
    if analytics['days_stats']:
        best_day = analytics['days_stats'][0]
        worst_day = analytics['days_stats'][-1] if len(analytics['days_stats']) > 1 else None
        
        recommendations.append(f"• 🎯 Самый популярный день: {best_day[0]} ({best_day[1]} записей)")
        
        if worst_day and worst_day[1] < best_day[1] * 0.5:
            recommendations.append(f"• 💡 Продвигайте {worst_day[0]} - самый непопулярный день")
    
    # Рекомендация по отменам
    cancel_rate = (analytics['cancelled_count'] / analytics['total_count']) * 100 if analytics['total_count'] > 0 else 0
    if cancel_rate > 10:
        recommendations.append(f"• ⚠️ Высокий процент отмен: {cancel_rate:.1f}% - улучшите коммуникацию")
    
    # Рекомендация по повторным клиентам
    repeat_rate = (analytics['unique_clients'] / analytics['total_bookings']) * 100 if analytics['total_bookings'] > 0 else 0
    if repeat_rate < 30:
        recommendations.append("• 🔄 Низкий процент повторных клиентов - внедрите программу лояльности")
    else:
        recommendations.append(f"• ✅ Отличная лояльность клиентов: {repeat_rate:.1f}% повторных обращений")
    
    report_text += "\n".join(recommendations)
    report_text += f"\n\n🔄 <i>Для обновления данных выберите другой период</i>"
    
    # Отправляем отчет
    await context.bot.edit_message_text(
        chat_id=update.message.chat_id,
        message_id=loading_message.message_id,
        text=report_text,
        parse_mode='HTML'
    )
    
    # Сохраняем период для возможного экспорта
    context.user_data['last_analytics_period'] = period_days
    
    # Предлагаем дополнительные действия
    action_keyboard = [
        ['📈 Новая аналитика', '📊 Экспорт данных'],
        ['🔙 В админ-панель']
    ]
    reply_markup = ReplyKeyboardMarkup(action_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "💡 <b>Что дальше?</b>\n\n"
        "📈 <b>Новая аналитика</b> - выбрать другой период\n"
        "📊 <b>Экспорт данных</b> - получить данные в виде файлов CSV\n"
        "🔙 <b>В админ-панель</b> - вернуться к управлению",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# Функция для экспорта данных
async def export_analytics_data(update: Update, context: CallbackContext):
    """Экспорт данных аналитики в CSV файлы"""
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к этой функции")
        return
    
    # Получаем период из контекста или используем по умолчанию 30 дней
    period_days = context.user_data.get('last_analytics_period', 30)
    
    # Показываем сообщение о начале экспорта
    export_message = await update.message.reply_text(
        f"📊 <b>НАЧИНАЮ ЭКСПОРТ ДАННЫХ...</b>\n\n"
        f"Период: последние <b>{period_days}</b> дней\n"
        f"⏳ Подготавливаю CSV файлы...",
        parse_mode='HTML'
    )
    
    try:
        # Экспортируем данные аналитики
        analytics_data = export_analytics_to_csv(period_days)
        
        if not analytics_data:
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=export_message.message_id,
                text="❌ Произошла ошибка при экспорте данных аналитики."
            )
            return
        
        # Экспортируем данные пользователей
        users_data = export_users_to_csv()
        
        if not users_data:
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=export_message.message_id,
                text="❌ Произошла ошибка при экспорте данных пользователей."
            )
            return
        
        # Обновляем сообщение о прогрессе
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=export_message.message_id,
            text=f"📊 <b>ЭКСПОРТ ДАННЫХ ЗАВЕРШЕН</b>\n\n"
                 f"✅ Подготовлено 6 CSV файлов:\n"
                 f"• 📈 Основная статистика\n"
                 f"• 📅 Статистика по дням недели\n"
                 f"• 🕐 Популярные часы\n"
                 f"• 👑 Топ клиентов\n"
                 f"• 📈 Месячная динамика\n"
                 f"• 📋 Все бронирования\n"
                 f"• 👥 Все пользователи\n\n"
                 f"📥 <i>Отправляю файлы...</i>",
            parse_mode='HTML'
        )
        
        # Отправляем файлы пользователю
        export_time = analytics_data['export_time']
        
        # 1. Основная статистика
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(analytics_data['main_stats'].encode('utf-8')),
            filename=f"main_stats_{export_time}.csv",
            caption="📈 Основная статистика"
        )
        
        # 2. Статистика по дням недели
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(analytics_data['days_stats'].encode('utf-8')),
            filename=f"days_stats_{export_time}.csv",
            caption="📅 Статистика по дням недели"
        )
        
        # 3. Популярные часы
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(analytics_data['hours_stats'].encode('utf-8')),
            filename=f"hours_stats_{export_time}.csv",
            caption="🕐 Популярные часы записи"
        )
        
        # 4. Топ клиентов
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(analytics_data['top_clients'].encode('utf-8')),
            filename=f"top_clients_{export_time}.csv",
            caption="👑 Топ клиентов по активности"
        )
        
        # 5. Месячная динамика
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(analytics_data['monthly_stats'].encode('utf-8')),
            filename=f"monthly_stats_{export_time}.csv",
            caption="📈 Месячная динамика бронирований"
        )
        
        # 6. Все бронирования за период
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(analytics_data['all_bookings'].encode('utf-8')),
            filename=f"all_bookings_{export_time}.csv",
            caption="📋 Все бронирования за период"
        )
        
        # 7. Все пользователи
        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(users_data.encode('utf-8')),
            filename=f"all_users_{export_time}.csv",
            caption="👥 Все пользователи бота"
        )
        
        # Финальное сообщение
        await update.message.reply_text(
            f"✅ <b>ЭКСПОРТ ДАННЫХ УСПЕШНО ЗАВЕРШЕН!</b>\n\n"
            f"📁 <b>Всего отправлено файлов:</b> 7\n"
            f"📅 <b>Период анализа:</b> {period_days} дней\n"
            f"⏰ <b>Время экспорта:</b> {export_time}\n\n"
            f"💡 <b>Что можно сделать с данными:</b>\n"
            f"• 📊 Импортировать в Excel/Google Sheets\n"
            f"• 📈 Строить графики и диаграммы\n"
            f"• 🔍 Проводить углубленный анализ\n"
            f"• 💾 Сохранить для архива\n\n"
            f"🔄 <i>Для нового экспорта выполните анализ за нужный период и нажмите '📊 Экспорт данных'</i>",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([
                ['📈 Новая аналитика', '📊 Экспорт данных'],
                ['🔙 В админ-панель']
            ], resize_keyboard=True)
        )
        
    except Exception as e:
        logger.error(f"Error in export_analytics_data: {e}")
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=export_message.message_id,
            text=f"❌ <b>ОШИБКА ПРИ ЭКСПОРТЕ ДАННЫХ</b>\n\n"
                 f"Произошла ошибка: {str(e)}\n\n"
                 f"Пожалуйста, попробуйте еще раз.",
            parse_mode='HTML'
        )

# Меню админ расписания
async def show_admin_schedule_menu(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    admin_schedule_keyboard = [
        ['🗓️ Расписание на сегодня', '📅 Выбрать другую дату'],
        ['🔙 Назад в админ-панель']
    ]
    reply_markup = ReplyKeyboardMarkup(admin_schedule_keyboard, resize_keyboard=True)
    
    admin_schedule_text = """🗓️ <b>АДМИН РАСПИСАНИЕ</b>

Выберите действие:

🗓️ <b>Расписание на сегодня</b> - просмотр и управление бронированиями на сегодня
📅 <b>Выбрать другую дату</b> - просмотр расписания на конкретную дату

Здесь вы можете:
• Просматривать все бронирования на выбранную дату
• Видеть детальную информацию о клиентах
• Управлять статусами бронирований"""

    await update.message.reply_text(
        admin_schedule_text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
    return ADMIN_SCHEDULE_MENU

# Обработка выбора в меню админ расписания
async def handle_admin_schedule_choice(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    choice = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if choice == '🗓️ Расписание на сегодня':
        # Получаем сегодняшнюю дату в нужном формате
        today = datetime.now()
        date_str = today.strftime("%d.%m.%Y")
        day_name = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][today.weekday()]
        formatted_date = f"{date_str} ({day_name}) - Сегодня"
        
        await show_admin_schedule_for_date(update, context, formatted_date)
        return ConversationHandler.END
    
    elif choice == '📅 Выбрать другую дату':
        await update.message.reply_text(
            "📅 <b>ВВЕДИТЕ ДАТУ ДЛЯ ПРОСМОТРА РАСПИСАНИЯ</b>\n\n"
            "Формат: <b>ДД.ММ.ГГГГ</b>\n"
            "Например: 25.12.2024\n\n"
            "📝 Введите дату в указанном формате:",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return ADMIN_SCHEDULE_DATE
    
    elif choice == '🔙 Назад в админ-панель':
        await show_admin_panel(update, context)
        return ConversationHandler.END
    
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup([
                ['🗓️ Расписание на сегодня', '📅 Выбрать другую дату'],
                ['🔙 Назад в админ-панель']
            ], resize_keyboard=True)
        )
        return ADMIN_SCHEDULE_MENU

# Обработка ввода даты для админ расписания
async def handle_admin_schedule_date(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    user_input = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if user_input == '🔙 Назад':
        await show_admin_schedule_menu(update, context)
        return ADMIN_SCHEDULE_MENU
    
    try:
        selected_date = datetime.strptime(user_input, "%d.%m.%Y")
        today = datetime.now()
        
        # Проверяем что дата не в прошлом (можно смотреть только сегодня и будущие даты)
        if selected_date.date() < today.date():
            await update.message.reply_text(
                '❌ Нельзя просматривать прошедшие даты.\n'
                'Пожалуйста, введите сегодняшнюю или будущую дату:',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return ADMIN_SCHEDULE_DATE
        
        # Форматируем дату для отображения
        date_str = selected_date.strftime("%d.%m.%Y")
        day_name = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][selected_date.weekday()]
        
        # Добавляем пометку "Сегодня" если это сегодняшняя дата
        if selected_date.date() == today.date():
            formatted_date = f"{date_str} ({day_name}) - Сегодня"
        else:
            formatted_date = f"{date_str} ({day_name})"
        
        # Очищаем предыдущие данные
        if 'admin_schedule_date' in context.user_data:
            context.user_data.pop('admin_schedule_date')
        
        await show_admin_schedule_for_date(update, context, formatted_date)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            '❌ Неправильный формат даты.\n'
            'Пожалуйста, введите дату в формате <b>ДД.ММ.ГГГГ</b>\n'
            'Например: 25.12.2024',
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return ADMIN_SCHEDULE_DATE

# Показ расписания для конкретной даты (админ версия) - ИСПРАВЛЕННАЯ ВЕРСИЯ
async def show_admin_schedule_for_date(update: Update, context: CallbackContext, selected_date: str):
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    try:
        # Убираем пометку " - Сегодня" из даты для поиска в базе
        clean_date = selected_date.replace(" - Сегодня", "")
        # Убираем день недели в скобках, оставляем только дату
        clean_date = clean_date.split(' (')[0]
        
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Получаем все бронирования на эту дату
        cursor.execute('''
            SELECT b.id, b.user_id, b.user_name, b.time, b.duration, b.status, b.created_at, b.added_by_admin, u.username, b.client_contact
            FROM bookings b
            LEFT JOIN users u ON b.user_id = u.user_id
            WHERE b.day = ?
            ORDER BY b.time
        ''', (clean_date,))
        
        bookings = cursor.fetchall()
        conn.close()
        
        # Создаем красивое расписание с эмодзи и форматированием
        schedule_text = f"""🗓️ <b>АДМИН РАСПИСАНИЕ</b>

📅 <b>Дата:</b> {selected_date}
⏰ <b>Часы работы:</b> 9:00 - 21:00

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎵 <b>БРОНИРОВАНИЯ НА ЭТУ ДАТУ</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        if not bookings:
            schedule_text += "\n📝 <b>На эту дату нет бронирований.</b>\n\n"
            schedule_text += "💡 <i>Все временные слоты свободны для записи.</i>"
        else:
            # Группируем бронирования по статусам
            confirmed_bookings = [b for b in bookings if b[5] == 'confirmed']
            pending_bookings = [b for b in bookings if b[5] == 'pending']
            cancelled_bookings = [b for b in bookings if b[5] == 'cancelled']
            cancelled_by_admin_bookings = [b for b in bookings if b[5] == 'cancelled_by_admin']
            
            # Показываем подтвержденные брони
            if confirmed_bookings:
                schedule_text += "\n✅ <b>ПОДТВЕРЖДЕННЫЕ БРОНИ:</b>\n\n"
                for booking in confirmed_bookings:
                    booking_id, user_id, user_name, time, duration, status, created_at, added_by_admin, username, client_contact = booking
                    
                    # Определяем источник записи
                    source = "👤 (админ)" if added_by_admin else "🤖 (бот)"
                    
                    # Формируем ссылку на пользователя если есть user_id
                    if user_id:
                        user_display = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                        username_display = f"(@{username})" if username else ""
                    else:
                        user_display = user_name
                        username_display = ""
                    
                    # Добавляем контакт если есть
                    contact_display = f"\n   📞 Контакт: {client_contact}" if client_contact else ""
                    
                    schedule_text += f"🕐 <b>{time}</b> - {duration} час(а) {source}\n"
                    schedule_text += f"   👤 {user_display} {username_display}{contact_display}\n"
                    if user_id:
                        schedule_text += f"   📞 ID: {user_id}\n"
                    schedule_text += f"   📋 ID брони: {booking_id}\n\n"
            
            # Показываем ожидающие подтверждения брони
            if pending_bookings:
                schedule_text += "⏳ <b>ОЖИДАЮТ ПОДТВЕРЖДЕНИЯ:</b>\n\n"
                for booking in pending_bookings:
                    booking_id, user_id, user_name, time, duration, status, created_at, added_by_admin, username, client_contact = booking
                    
                    # Определяем источник записи
                    source = "👤 (админ)" if added_by_admin else "🤖 (бот)"
                    
                    # Формируем ссылку на пользователя если есть user_id
                    if user_id:
                        user_display = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                        username_display = f"(@{username})" if username else ""
                    else:
                        user_display = user_name
                        username_display = ""
                    
                    # Добавляем контакт если есть
                    contact_display = f"\n   📞 Контакт: {client_contact}" if client_contact else ""
                    
                    schedule_text += f"🕐 <b>{time}</b> - {duration} час(а) {source}\n"
                    schedule_text += f"   👤 {user_display} {username_display}{contact_display}\n"
                    if user_id:
                        schedule_text += f"   📞 ID: {user_id}\n"
                    schedule_text += f"   📋 ID брони: {booking_id}\n\n"
            
            # Показываем отмененные брони
            if cancelled_bookings or cancelled_by_admin_bookings:
                schedule_text += "❌ <b>ОТМЕНЕННЫЕ БРОНИ:</b>\n\n"
                all_cancelled = cancelled_bookings + cancelled_by_admin_bookings
                for booking in all_cancelled:
                    booking_id, user_id, user_name, time, duration, status, created_at, added_by_admin, username, client_contact = booking
                    
                    # Определяем источник записи
                    source = "👤 (админ)" if added_by_admin else "🤖 (бот)"
                    cancel_source = " (админом)" if status == 'cancelled_by_admin' else " (клиентом)"
                    
                    # Формируем ссылку на пользователя если есть user_id
                    if user_id:
                        user_display = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                        username_display = f"(@{username})" if username else ""
                    else:
                        user_display = user_name
                        username_display = ""
                    
                    # Добавляем контакт если есть
                    contact_display = f"\n   📞 Контакт: {client_contact}" if client_contact else ""
                    
                    schedule_text += f"🕐 <b>{time}</b> - {duration} час(а) {source}{cancel_source}\n"
                    schedule_text += f"   👤 {user_display} {username_display}{contact_display}\n"
                    if user_id:
                        schedule_text += f"   📞 ID: {user_id}\n"
                    schedule_text += f"   📋 ID брони: {booking_id}\n\n"
        
        # Добавляем информацию о свободных слотах
        schedule_text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        schedule_text += "🆓 <b>СВОБОДНЫЕ ВРЕМЕННЫЕ СЛОТЫ</b>\n"
        schedule_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        available_times = get_available_times(selected_date)
        if available_times:
            for time_slot in available_times:
                schedule_text += f"✅ {time_slot} - Свободно\n"
        else:
            schedule_text += "❌ На эту дату нет свободного времени.\n"
        
        # Статистика по дате
        confirmed_count = len([b for b in bookings if b[5] == 'confirmed'])
        pending_count = len([b for b in bookings if b[5] == 'pending'])
        cancelled_count = len([b for b in bookings if b[5] in ['cancelled', 'cancelled_by_admin']])
        
        schedule_text += f"\n💡 <b>Статистика по дате:</b>\n"
        schedule_text += f"• ✅ Подтверждено: {confirmed_count}\n"
        schedule_text += f"• ⏳ Ожидание: {pending_count}\n"
        schedule_text += f"• ❌ Отменено: {cancelled_count}\n"
        schedule_text += f"• 🆓 Свободных слотов: {len(available_times)}"
        
        await update.message.reply_text(schedule_text, parse_mode='HTML')
        
        # ИСПРАВЛЕНИЕ ЗАДАЧИ 3: Заменяем кнопку "Назад в админ-панель" на "Главное меню"
        action_keyboard = [
            ['🔙 Главное меню']
        ]
        reply_markup = ReplyKeyboardMarkup(action_keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "💡 <b>Что дальше?</b>\n\n"
            "🔙 <b>Главное меню</b> - вернуться к основному меню",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_schedule_for_date: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при загрузке расписания.",
            reply_markup=get_main_keyboard(user_id)
        )

# ФУНКЦИЯ ДЛЯ ОТМЕНЫ ЗАПИСИ АДМИНИСТРАТОРОМ
async def show_cancel_booking_menu(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "❌ <b>ОТМЕНА ЗАПИСИ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
        "📅 <b>Введите дату для просмотра записей</b>\n\n"
        "Формат: <b>ДД.ММ.ГГГГ</b>\n"
        "Например: 25.12.2024\n\n"
        "📝 Введите дату в указанном формате:",
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
    )
    
    return ADMIN_CANCEL_DAY

# Обработка ввода даты для отмены записей
async def handle_admin_cancel_date(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    user_input = update.message.text
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return ConversationHandler.END
    
    if user_input == '🔙 Назад':
        await show_admin_panel(update, context)
        return ConversationHandler.END
    
    try:
        selected_date = datetime.strptime(user_input, "%d.%m.%Y")
        today = datetime.now()
        
        # Проверяем что дата не в прошлом (можно отменять только сегодняшние и будущие записи)
        if selected_date.date() < today.date():
            await update.message.reply_text(
                '❌ Нельзя отменять прошедшие записи.\n'
                'Пожалуйста, введите сегодняшнюю или будущую дату:',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return ADMIN_CANCEL_DAY
        
        # Форматируем дату для отображения
        date_str = selected_date.strftime("%d.%m.%Y")
        day_name = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][selected_date.weekday()]
        
        # Добавляем пометку "Сегодня" если это сегодняшняя дата
        if selected_date.date() == today.date():
            formatted_date = f"{date_str} ({day_name}) - Сегодня"
        else:
            formatted_date = f"{date_str} ({day_name})"
        
        # Сохраняем дату в контексте
        context.user_data['admin_cancel_date'] = formatted_date
        context.user_data['admin_cancel_clean_date'] = date_str
        
        # Получаем активные записи на эту дату
        await show_bookings_for_cancellation(update, context, formatted_date, date_str)
        return ADMIN_CANCEL_SELECT
        
    except ValueError:
        await update.message.reply_text(
            '❌ Неправильный формат даты.\n'
            'Пожалуйста, введите дату в формате <b>ДД.ММ.ГГГГ</b>\n'
            'Например: 25.12.2024',
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return ADMIN_CANCEL_DAY

# Показ записей для отмены
async def show_bookings_for_cancellation(update: Update, context: CallbackContext, formatted_date: str, clean_date: str):
    try:
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Получаем все активные бронирования на эту дату (подтвержденные и ожидающие)
        cursor.execute('''
            SELECT b.id, b.user_id, b.user_name, b.time, b.duration, b.status, u.username, b.client_contact
            FROM bookings b
            LEFT JOIN users u ON b.user_id = u.user_id
            WHERE b.day = ? AND b.status IN ('confirmed', 'pending')
            ORDER BY b.time
        ''', (clean_date,))
        
        bookings = cursor.fetchall()
        conn.close()
        
        if not bookings:
            await update.message.reply_text(
                f'📝 На <b>{formatted_date}</b> нет активных записей для отмены.\n\n'
                f'💡 Все записи на эту дату уже отменены или их нет.',
                parse_mode='HTML',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return
        
        # Сообщение с общей информацией
        info_text = f"""❌ <b>ОТМЕНА ЗАПИСЕЙ</b>

📅 <b>Дата:</b> {formatted_date}
📋 <b>Активных записей:</b> {len(bookings)}

👇 <b>Выберите запись для отмены:</b>"""
        
        await update.message.reply_text(
            info_text,
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        
        # Показываем каждую запись отдельным сообщением с кнопкой отмены
        for booking in bookings:
            booking_id, user_id, user_name, time, duration, status, username, client_contact = booking
            
            status_icon = "✅" if status == 'confirmed' else "⏳"
            status_text = "Подтверждена" if status == 'confirmed' else "Ожидает подтверждения"
            
            # Формируем ссылку на пользователя
            user_link = f"<a href=\"tg://user?id={user_id}\">{user_name}</a>" if user_id else user_name
            username_display = f"@{username}" if username else "без username"
            
            # Добавляем контакт если есть
            contact_display = f"\n📞 <b>Контакт:</b> {client_contact}" if client_contact else ""
            
            booking_text = f"""{status_icon} <b>ЗАПИСЬ #{booking_id}</b>

👤 <b>Клиент:</b> {user_link}
📱 <b>Telegram:</b> {username_display}{contact_display}
🕐 <b>Время:</b> {time}
⏱ <b>Продолжительность:</b> {duration} час(а)
📊 <b>Статус:</b> {status_text}
🆔 <b>ID клиента:</b> {user_id if user_id else 'N/A'}"""
            
            # Создаем кнопку отмены для каждой записи
            keyboard = [
                [InlineKeyboardButton("❌ Отменить эту запись", callback_data=f"admin_cancel_{booking_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                booking_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        
        # Финальное сообщение с инструкцией
        await update.message.reply_text(
            "💡 <b>Как отменить запись:</b>\n\n"
            "1. Найдите нужную запись выше\n"
            "2. Нажмите кнопку <b>❌ Отменить эту запись</b>\n"
            "3. Подтвердите отмену\n"
            "4. Клиент получит уведомление об отмене\n\n"
            "🔄 Время записи станет доступным для бронирования другим клиентам.",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Error in show_bookings_for_cancellation: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при загрузке записей.",
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )

# Обработка отмены записи администратором - ИСПРАВЛЕНА ДЛЯ ЗАДАЧИ 1
async def handle_admin_cancellation(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    booking_id = int(query.data.split('_')[2])
    
    conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Получаем полную информацию о бронировании
    cursor.execute('''
        SELECT b.user_id, b.user_name, b.day, b.time, b.duration, b.status, u.username, b.client_contact
        FROM bookings b
        LEFT JOIN users u ON b.user_id = u.user_id
        WHERE b.id = ?
    ''', (booking_id,))
    
    booking = cursor.fetchone()
    
    if not booking:
        await query.edit_message_text("❌ Запись не найдена")
        conn.close()
        return
    
    user_id, user_name, day, time, duration, status, username, client_contact = booking
    
    # Обновляем статус брони на "отменено администратором"
    cursor.execute('UPDATE bookings SET status = ? WHERE id = ?', ('cancelled_by_admin', booking_id))
    conn.commit()
    conn.close()
    
    # Обновляем статистику бронирований пользователя
    if user_id:
        update_user_booking_stats(user_id)
    
    # Сообщение администратору об успешной отмене
    admin_success_text = f"""✅ <b>ЗАПИСЬ ОТМЕНЕНА</b>

📅 <b>Дата:</b> {day}
🕐 <b>Время:</b> {time}
⏱ <b>Продолжительность:</b> {duration} час(а)
👤 <b>Клиент:</b> {user_name}
📱 <b>Telegram:</b> @{username or 'без username'}
📞 <b>Контакт:</b> {client_contact or 'Не указан'}
🆔 <b>ID брони:</b> {booking_id}

✅ <i>Запись отменена. Время стало доступным для бронирования.</i>
📞 <i>Клиент уведомлен об отмене.</i>"""
    
    await query.edit_message_text(
        admin_success_text,
        parse_mode='HTML'
    )
    
    # ИСПРАВЛЕНИЕ ЗАДАЧИ 1: Убираем кнопку "В главное меню" из сообщения клиенту
    if user_id:
        try:
            client_message = f"""😔 <b>ВАША ЗАПИСЬ ОТМЕНЕНА АДМИНИСТРАТОРОМ</b>

📅 <b>Дата:</b> {day}
🕐 <b>Время:</b> {time}
⏱ <b>Продолжительность:</b> {duration} час(а)

💡 <b>Что делать дальше?</b>

🎵 <b>Забронируйте новое время</b> - выберите удобное время для записи
📞 <b>Свяжитесь с администратором</b> - для уточнения деталей

📱 <b>Контакты:</b>
Телефон: +7 (918) 880-52-92
Telegram: @Solnyshkin_Mikhail

🙏 <i>Приносим извинения за доставленные неудобства!</i>
🎶 <i>Надеемся увидеть вас в нашей студии в другое время!</i>"""
            
            await context.bot.send_message(
                chat_id=user_id,
                text=client_message,
                parse_mode='HTML'
            )
            print(f"✅ Уведомление об отмене отправлено клиенту {user_id}")
            
        except Exception as e:
            logger.error(f"Не удалось уведомить клиента об отмене: {e}")
            # Сообщаем администратору, что не удалось уведомить клиента
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ <b>Не удалось уведомить клиента об отмене</b>\n\n"
                     f"👤 Клиент: {user_name}\n"
                     f"📅 Дата: {day}\n"
                     f"🕐 Время: {time}\n\n"
                     f"💡 <i>Рекомендуется связаться с клиентом самостоятельно</i>",
                parse_mode='HTML'
            )
    
    # ИСПРАВЛЕНИЕ ЗАДАЧИ 2: Заменяем кнопку "В админ-панель" на корректную обработку
    action_keyboard = [
        ['❌ Отменить еще запись', '🗓️ Админ расписание'],
        ['🔙 Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(action_keyboard, resize_keyboard=True)
    
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text="💡 <b>Что дальше?</b>\n\n"
             "❌ <b>Отменить еще запись</b> - продолжить отмену записей\n"
             "🗓️ <b>Админ расписание</b> - просмотреть расписание\n"
             "🔙 <b>Назад</b> - вернуться к админ-панели",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# Обработка кнопки "В главное меню" после отмены администратором
async def handle_to_main_menu_from_cancel(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    # Получаем данные пользователя
    user_id = query.from_user.id
    username = query.from_user.username or 'без username'
    first_name = query.from_user.first_name
    last_name = query.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    # Удаляем кнопку после нажатия
    await query.edit_message_reply_markup(reply_markup=None)
    
    # Обновляем текст сообщения
    await query.edit_message_text(
        "🔙 Возвращаемся в главное меню...",
        parse_mode='HTML'
    )
    
    # Показываем главное меню
    await start(update, context)

# Обработка кнопки "Забронировать новое время" после отмены администратором
async def handle_start_booking_from_cancel(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    # Получаем данные пользователя
    user_id = query.from_user.id
    username = query.from_user.username or 'без username'
    first_name = query.from_user.first_name
    last_name = query.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    # Удаляем кнопку после нажатия
    await query.edit_message_reply_markup(reply_markup=None)
    
    # Обновляем текст сообщения
    await query.edit_message_text(
        "🎵 Отлично! Давайте подберем для вас новое время для записи!",
        parse_mode='HTML'
    )
    
    # Показываем меню бронирования (точно такое же как из главного меню)
    booking_keyboard = [
        ['📅 Забронировать на ближайшую дату', '🗓️ Забронировать на другую дату'],
        ['📋❌ Мои брони/Отменить запись', '🔙 Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(booking_keyboard, resize_keyboard=True)
    
    await context.bot.send_message(
        chat_id=user_id,
        text='🎵 <b>ВЫБЕРИТЕ ТИП БРОНИРОВАНИЯ</b>\n\n'
             '📅 <b>Забронировать на ближайшую дату</b> - выбор из ближайших 7 дней (включая сегодня)\n'
             '🗓️ <b>Забронировать на другую дату</b> - ввод любой даты вручную\n'
             '📋❌ <b>Мои брони/Отменить запись</b> - просмотр и управление вашими записями',
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# Обработка действий в админ-панели - ОБНОВЛЕНА
async def handle_admin_actions_panel(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Проверяем, является ли пользователь администратором
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    if text == '📊 Статистика пользователей':
        await show_user_statistics(update, context)
    elif text == '📈 Аналитика':
        await show_analytics_menu(update, context)
    elif text == '📢 Рассылка':
        await show_broadcast_menu(update, context)
    elif text == '🗓️ Админ расписание':
        await show_admin_schedule_menu(update, context)
    elif text == '❌ Отменить запись':
        await show_cancel_booking_menu(update, context)
    elif text == '📝 Добавить запись':
        await show_add_booking_menu(update, context)
    elif text == '🔙 В главное меню' or text == '🔙 Главное меню':
        await start(update, context)
    elif text == '📈 Новая аналитика':
        await show_analytics_menu(update, context)
    elif text == '📊 Экспорт данных':
        await export_analytics_data(update, context)
    elif text == '🔙 В админ-панель':
        await show_admin_panel(update, context)
    elif text == '📅 Выбрать другую дату':
        # Очищаем состояние перед новым вводом даты
        if 'admin_schedule_date' in context.user_data:
            context.user_data.pop('admin_schedule_date')
        
        await update.message.reply_text(
            "📅 <b>ВВЕДИТЕ ДАТУ ДЛЯ ПРОСМОТРА РАСПИСАНИЯ</b>\n\n"
            "Формат: <b>ДД.ММ.ГГГГ</b>\n"
            "Например: 25.12.2024\n\n"
            "📝 Введите дату в указанном формате:",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
    elif text == '❌ Отменить еще запись':
        await show_cancel_booking_menu(update, context)
    elif text == '🔙 Назад':
        await show_admin_panel(update, context)

# Меню бронирования
async def show_booking_menu(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    booking_keyboard = [
        ['📅 Забронировать на ближайшую дату', '🗓️ Забронировать на другую дату'],
        ['📋❌ Мои брони/Отменить запись', '🔙 Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(booking_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '🎵 <b>ВЫБЕРИТЕ ТИП БРОНИРОВАНИЯ</b>\n\n'
        '📅 <b>Забронировать на ближайшую дату</b> - выбор из ближайших 7 дней (включая сегодня)\n'
        '🗓️ <b>Забронировать на другую дату</b> - ввод любой даты вручную\n'
        '📋❌ <b>Мои брони/Отменить запись</b> - просмотр и управление вашими записями',
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    return SELECT_BOOKING_TYPE

# Обработка выбора типа бронирования
async def handle_booking_type(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    choice = update.message.text
    
    if choice == '🔙 Назад':
        await start(update, context)
        return ConversationHandler.END
    
    elif choice == '📅 Забронировать на ближайшую дату':
        context.user_data['booking_type'] = 'nearest'
        await show_nearest_dates(update, context)
        return SELECT_DAY
    
    elif choice == '🗓️ Забронировать на другую дату':
        context.user_data['booking_type'] = 'manual'
        await ask_for_specific_date(update, context)
        return SELECT_DAY
    
    elif choice == '📋❌ Мои брони/Отменить запись':
        await show_user_bookings_with_buttons(update, context, user_id)
        return ConversationHandler.END
    
    else:
        await update.message.reply_text(
            'Пожалуйста, выберите один из предложенных вариантов:',
            reply_markup=ReplyKeyboardMarkup([
                ['📅 Забронировать на ближайшую дату', '🗓️ Забронировать на другую дату'],
                ['📋❌ Мои брони/Отменить запись', '🔙 Назад']
            ], resize_keyboard=True)
        )
        return SELECT_BOOKING_TYPE

# Показ ближайших дат (7 дней ВКЛЮЧАЯ СЕГОДНЯ)
async def show_nearest_dates(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    dates = generate_dates()
    
    dates_keyboard = []
    for i in range(0, len(dates), 2):
        row = dates[i:i+2]
        dates_keyboard.append(row)
    
    dates_keyboard.append(['🔙 Назад'])
    
    reply_markup = ReplyKeyboardMarkup(dates_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '📅 <b>ВЫБЕРИТЕ ДАТУ ДЛЯ ЗАПИСИ</b>\n\n'
        '🎯 Доступные даты на 7 дней вперед (включая сегодня):\n'
        '💡 <i>На сегодня доступно время только со следующего часа</i>',
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# Запрос конкретной даты (ручной ввод)
async def ask_for_specific_date(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    await update.message.reply_text(
        '📅 <b>ВВЕДИТЕ ДАТУ ДЛЯ ЗАПИСИ</b>\n\n'
        'Формат: <b>ДД.ММ.ГГГГ</b>\n'
        'Например: 25.12.2024\n\n'
        '📝 Введите дату в указанном формате:',
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
    )

# Обработка выбора даты (общая функция для обоих типов)
async def handle_date_selection(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    user_input = update.message.text
    
    if user_input == '🔙 Назад':
        # Возвращаемся к меню выбора типа бронирования
        await show_booking_menu(update, context)
        return SELECT_BOOKING_TYPE
    
    # Определяем тип бронирования
    booking_type = context.user_data.get('booking_type', 'nearest')
    
    if booking_type == 'nearest':
        # Обработка выбора из ближайших дат
        dates = generate_dates()
        if user_input not in dates:
            await update.message.reply_text(
                '❌ Пожалуйста, выберите дату из предложенного списка:',
                reply_markup=ReplyKeyboardMarkup([dates[i:i+2] for i in range(0, len(dates), 2)] + [['🔙 Назад']], resize_keyboard=True)
            )
            return SELECT_DAY
        
        context.user_data['booking_day'] = user_input
        
    else:  # manual
        # Обработка ручного ввода даты
        try:
            selected_date = datetime.strptime(user_input, "%d.%m.%Y")
            today = datetime.now()
            
            # Проверяем что дата не в прошлом
            if selected_date.date() < today.date():
                await update.message.reply_text(
                    '❌ Нельзя выбрать прошедшую дату.\n'
                    'Пожалуйста, введите сегодняшнюю или будущую дату:',
                    reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
                )
                return SELECT_DAY
            
            # Проверяем что дата не слишком далеко (максимум 3 месяца)
            max_date = today + timedelta(days=90)
            if selected_date > max_date:
                await update.message.reply_text(
                    '❌ Бронирование доступно только на 3 месяца вперед.\n'
                    'Пожалуйста, введите более близкую дату:',
                    reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
                )
                return SELECT_DAY
            
            # Форматируем дату для отображения
            date_str = selected_date.strftime("%d.%m.%Y")
            day_name = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][selected_date.weekday()]
            
            # Добавляем пометку "Сегодня" если это сегодняшняя дата
            if selected_date.date() == today.date():
                formatted_date = f"{date_str} ({day_name}) - Сегодня"
            else:
                formatted_date = f"{date_str} ({day_name})"
            
            context.user_data['booking_day'] = formatted_date
            
        except ValueError:
            await update.message.reply_text(
                '❌ Неправильный формат даты.\n'
                'Пожалуйста, введите дату в формате <b>ДД.ММ.ГГГГ</b>\n'
                'Например: 25.12.2024',
                parse_mode='HTML',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
            return SELECT_DAY
    
    # Проверяем доступность даты
    selected_date = context.user_data['booking_day']
    available_times = get_available_times(selected_date)
    
    if not available_times:
        if booking_type == 'nearest':
            dates = generate_dates()
            await update.message.reply_text(
                f'❌ На {selected_date} нет свободного времени.\n'
                f'Пожалуйста, выберите другую дату:',
                reply_markup=ReplyKeyboardMarkup([dates[i:i+2] for i in range(0, len(dates), 2)] + [['🔙 Назад']], resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                f'❌ На {selected_date} нет свободного времени.\n'
                f'Пожалуйста, выберите другую дату:',
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
            )
        return SELECT_DAY
    
    # Показываем выбор времени
    await show_time_selection(update, context, selected_date)
    return SELECT_TIME

# Показ выбора времени
async def show_time_selection(update: Update, context: CallbackContext, selected_date: str) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    available_times = get_available_times(selected_date)
    
    if not available_times:
        await update.message.reply_text(
            f'❌ На {selected_date} нет свободного времени.\n'
            f'Пожалуйста, выберите другую дату:',
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True)
        )
        return
    
    time_keyboard = []
    row = []
    for i, time_slot in enumerate(available_times):
        row.append(time_slot)
        if len(row) == 2 or i == len(available_times) - 1:
            time_keyboard.append(row)
            row = []
    
    time_keyboard.append(['🔙 Назад'])
    
    reply_markup = ReplyKeyboardMarkup(time_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f'📅 Выбрана дата: <b>{selected_date}</b>\n'
        f'🕐 <b>ВЫБЕРИТЕ ВРЕМЯ НАЧАЛА СЕССИИ</b>\n\n'
        f'🎯 Доступное время:',
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# Обработка выбора времени
async def handle_time_selection(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    selected_time = update.message.text
    
    if selected_time == '🔙 Назад':
        # Возвращаемся к выбору даты
        selected_date = context.user_data['booking_day']
        booking_type = context.user_data.get('booking_type', 'nearest')
        
        if booking_type == 'nearest':
            await show_nearest_dates(update, context)
        else:
            await ask_for_specific_date(update, context)
        return SELECT_DAY
    
    selected_date = context.user_data['booking_day']
    available_times = get_available_times(selected_date)
    
    if selected_time not in available_times:
        await update.message.reply_text(
            f'❌ Время {selected_time} недоступно.\n'
            f'Пожалуйста, выберите другое время:',
            reply_markup=ReplyKeyboardMarkup([available_times[i:i+2] for i in range(0, len(available_times), 2)] + [['🔙 Назад']], resize_keyboard=True)
        )
        return SELECT_TIME
    
    context.user_data['booking_time'] = selected_time
    await show_duration_selection(update, context, selected_date, selected_time)
    return SELECT_DURATION

# Показ выбора продолжительности
async def show_duration_selection(update: Update, context: CallbackContext, selected_date: str, selected_time: str) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    duration_keyboard = [
        ['1 час', '2 часа'],
        ['3 часа', '4 часа'],
        ['🔙 Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(duration_keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f'📅 Дата: <b>{selected_date}</b>\n'
        f'🕐 Время: <b>{selected_time}</b>\n\n'
        f'⏱ <b>ВЫБЕРИТЕ ПРОДОЛЖИТЕЛЬНОСТЬ СЕССИИ</b>:',
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# Обработка выбора продолжительности и завершение бронирования
async def handle_duration_selection(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    duration_text = update.message.text
    
    if duration_text == '🔙 Назад':
        selected_date = context.user_data['booking_day']
        await show_time_selection(update, context, selected_date)
        return SELECT_TIME
    
    duration_map = {
        '1 час': 1, 
        '2 часа': 2, 
        '3 часа': 3, 
        '4 часа': 4
    }
    
    if duration_text not in duration_map:
        await update.message.reply_text(
            '❌ Пожалуйста, выберите продолжительность из списка:',
            reply_markup=ReplyKeyboardMarkup([
                ['1 час', '2 часа'],
                ['3 часа', '4 часа'],
                ['🔙 Назад']
            ], resize_keyboard=True)
        )
        return SELECT_DURATION
    
    duration = duration_map[duration_text]
    selected_date = context.user_data['booking_day']
    selected_time = context.user_data['booking_time']
    
    if not is_time_available(selected_date, selected_time, duration):
        await update.message.reply_text(
            f'❌ Время {selected_time} продолжительностью {duration} час(а) недоступно.\n'
            f'Пожалуйста, выберите другое время или продолжительность.',
            reply_markup=ReplyKeyboardMarkup([
                ['1 час', '2 часа'],
                ['3 часа', '4 часа'],
                ['🔙 Назад']
            ], resize_keyboard=True)
        )
        return SELECT_DURATION
    
    # Сохраняем бронирование
    user_name = update.message.from_user.first_name
    
    # Сохраняем дату без пометки " - Сегодня"
    clean_date = selected_date.replace(" - Сегодня", "")
        # Убираем день недели в скобках, оставляем только дату
    clean_date = clean_date.split(' (')[0]
    
    conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO bookings (user_id, user_name, day, time, duration, status, created_at, added_by_admin, client_contact)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
    ''', (user_id, user_name, clean_date, selected_time, duration, get_current_time(), False, None))
    booking_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Обновляем статистику бронирований пользователя
    update_user_booking_stats(user_id)
    
    # Сообщение пользователю
    await update.message.reply_text(
        f'✅ <b>ЗАЯВКА НА БРОНИРОВАНИЕ ОТПРАВЛЕНА!</b>\n\n'
        f'📅 Дата: <b>{selected_date}</b>\n'
        f'🕐 Время: <b>{selected_time}</b>\n'
        f'⏱ Продолжительность: <b>{duration} час(а)</b>\n\n'
        f'⏳ Ожидайте подтверждения от администратора в течение 24 часов.\n'
        f'📞 Для срочных вопросов свяжитесь с администратором.',
        parse_mode='HTML',
        reply_markup=get_main_keyboard(user_id)
    )
    
    # Отправляем уведомление администратору
    await send_admin_notification(context, booking_id, user_name, selected_date, selected_time, duration, user_id, username)
    
    # Настраиваем повторяющееся напоминание администратору каждые 30 минут
    if context.job_queue:
        context.job_queue.run_repeating(
            send_reminder_to_admin,
            interval=1800,  # 30 минут в секундах
            first=1800,     # Первое напоминание через 30 минут
            data={
                'booking_id': booking_id,
                'user_name': user_name,
                'selected_date': selected_date,
                'selected_time': selected_time,
                'duration': duration
            },
            name=f"admin_reminder_{booking_id}"
        )
        print(f"✅ Повторяющееся напоминание настроено для бронирования {booking_id} (каждые 30 минут)")
    
    return ConversationHandler.END

# Показ бронирований пользователя с кнопками отмены
async def show_user_bookings_with_buttons(update: Update, context: CallbackContext, user_id: int) -> None:
    try:
        user_id = update.message.from_user.id
        username = update.message.from_user.username or 'без username'
        first_name = update.message.from_user.first_name
        last_name = update.message.from_user.last_name or ''
        
        # Обновляем статистику пользователя
        update_user_stats(user_id, username, first_name, last_name)
        
        conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, day, time, duration, status 
            FROM bookings 
            WHERE user_id = ? AND status IN ('pending', 'confirmed')
            ORDER BY day, time
        ''', (user_id,))
        
        bookings = cursor.fetchall()
        conn.close()
        
        if not bookings:
            await update.message.reply_text(
                '📝 У вас нет активных бронирований.',
                reply_markup=get_main_keyboard(user_id)
            )
            return
        
        for booking in bookings:
            booking_id, day, time, duration, status = booking
            status_icon = "✅" if status == 'confirmed' else "⏳"
            status_text = "Подтверждено" if status == 'confirmed' else "Ожидание подтверждения"
            
            booking_text = f"""{status_icon} <b>ВАША БРОНЬ</b>

📅 <b>Дата</b>: {day}
🕐 <b>Время</b>: {time}
⏱ <b>Продолжительность</b>: {duration} час(а)
📊 <b>Статус</b>: {status_text}
🆔 <b>ID брони</b>: {booking_id}"""
            
            # Создаем кнопку отмены для каждой брони
            keyboard = [
                [InlineKeyboardButton("❌ Отменить запись", callback_data=f"user_cancel_{booking_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                booking_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        
        # Добавляем основное меню после списка бронирований
        await update.message.reply_text(
            "💡 Вы можете отменить любую из ваших записей, нажав кнопку '❌ Отменить запись' под соответствующей бронью.",
            reply_markup=get_main_keyboard(user_id)
        )
        
    except Exception as e:
        logger.error(f"Error in show_user_bookings_with_buttons: {e}")
        await update.message.reply_text(
            '❌ Произошла ошибка при загрузке ваших бронирований.',
            reply_markup=get_main_keyboard(user_id)
        )

# Обработка отмены брони пользователем - ИСПРАВЛЕНА ДЛЯ ЗАДАЧИ 1
async def handle_user_cancellation(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    booking_id = int(query.data.split('_')[2])
    user_id = query.from_user.id
    
    conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('SELECT user_id, user_name, day, time, duration, status FROM bookings WHERE id = ?', (booking_id,))
    booking = cursor.fetchone()
    
    if not booking:
        await query.edit_message_text("❌ Заявка не найдена")
        conn.close()
        return
    
    booking_user_id, user_name, day, time, duration, status = booking
    
    # Проверяем, что отменяет именно владелец брони
    if booking_user_id != user_id:
        await query.edit_message_text("❌ Вы не можете отменить чужую бронь")
        conn.close()
        return
    
    # Обновляем статус брони
    cursor.execute('UPDATE bookings SET status = ? WHERE id = ?', ('cancelled', booking_id))
    conn.commit()
    conn.close()
    
    # Обновляем статистику бронирований пользователя
    update_user_booking_stats(user_id)
    
    # ИСПРАВЛЕНИЕ ЗАДАЧИ 1: Убираем кнопку "Забронировать новую сессию" из сообщения
    cancellation_text = f"""😔 <b>ВАША ЗАПИСЬ ОТМЕНЕНА</b>

📅 <b>Дата</b>: {day}
🕐 <b>Время</b>: {time}
⏱ <b>Продолжительность</b>: {duration} час(а)

Мы сожалеем, что вы отменили запись 😔

🎵 Не расстраивайтесь! Вы всегда можете записаться на другое время.
Наша студия всегда рада помочь вам в создании качественного звука!

💫 <b>Если передумаете - мы будем ждать вас снова!</b>"""
    
    # Убираем кнопку "Забронировать новую сессию"
    await query.edit_message_text(
        cancellation_text,
        parse_mode='HTML'
    )
    
    # Уведомление администратору об отмене
    admin_cancel_message = f"""🚫 <b>ОТМЕНА БРОНИ КЛИЕНТОМ</b>

👤 <b>Клиент</b>: {user_name}
📱 <b>Telegram</b>: @{query.from_user.username or 'без username'}
📅 <b>Дата</b>: {day}
🕐 <b>Время</b>: {time}
⏱ <b>Продолжительность</b>: {duration} час(а)
🆔 <b>ID брони</b>: {booking_id}
📋 <b>ID клиента</b>: {user_id}

❌ <i>Бронь отменена самим клиентом</i>"""

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_cancel_message,
            parse_mode='HTML'
        )
        print(f"✅ Уведомление об отмене отправлено администратору {ADMIN_ID} о брони {booking_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление админу об отмене: {e}")

# Обработка кнопки "Забронировать новую сессию" после отмены
async def handle_new_booking_after_cancel(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    # Получаем данные пользователя
    user_id = query.from_user.id
    username = query.from_user.username or 'без username'
    first_name = query.from_user.first_name
    last_name = query.from_user.last_name or ''
    
    # Обновляем статистику пользователя
    update_user_stats(user_id, username, first_name, last_name)
    
    # Удаляем кнопку после нажатия
    await query.edit_message_reply_markup(reply_markup=None)
    
    # Обновляем текст сообщения
    await query.edit_message_text(
        "🎵 Отлично! Давайте подберем для вас новое время для записи!",
        parse_mode='HTML'
    )
    
    # Показываем меню бронирования
    booking_keyboard = [
        ['📅 Забронировать на ближайшую дату', '🗓️ Забронировать на другую дату'],
        ['📋❌ Мои брони/Отменить запись', '🔙 Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(booking_keyboard, resize_keyboard=True)
    
    # Отправляем новое сообщение с меню бронирования
    await context.bot.send_message(
        chat_id=user_id,
        text='🎵 <b>ВЫБЕРИТЕ ТИП БРОНИРОВАНИЯ</b>\n\n'
             '📅 <b>Забронировать на ближайшую дату</b> - выбор из ближайших 7 дней (включая сегодня)\n'
             '🗓️ <b>Забронировать на другую дату</b> - ввод любой даты вручную\n'
             '📋❌ <b>Мои брони/Отменить запись</b> - просмотр и управление вашими записями',
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# Обработка действий администратора
async def handle_admin_actions(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    data = query.data
    booking_id = int(data.split('_')[1])
    action = data.split('_')[0]
    
    conn = sqlite3.connect('studio_schedule.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('SELECT user_id, user_name, day, time, duration FROM bookings WHERE id = ?', (booking_id,))
    booking = cursor.fetchone()
    
    if not booking:
        await query.edit_message_text("❌ Заявка не найдена")
        conn.close()
        return
    
    user_id, user_name, day, time, duration = booking
    
    if action == 'confirm':
        cursor.execute('UPDATE bookings SET status = ? WHERE id = ?', ('confirmed', booking_id))
        conn.commit()
        
        # Обновляем статистику бронирований пользователя
        update_user_booking_stats(user_id)
        
        # ОТМЕНЯЕМ напоминание администратору
        if context.job_queue:
            job_name = f"admin_reminder_{booking_id}"
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal()
                print(f"🔕 Напоминание администратору отменено для брони {booking_id}")
        
        await query.edit_message_text(
            f"✅ <b>БРОНЬ ПОДТВЕРЖДЕНА!</b>\n\n"
            f"👤 <b>Клиент</b>: {user_name}\n"
            f"📅 <b>Дата</b>: {day}\n"
            f"🕐 <b>Время</b>: {time}\n"
            f"⏱ <b>Продолжительность</b>: {duration} час(а)\n\n"
            f"✅ <i>Клиент уведомлен о подтверждении.</i>",
            parse_mode='HTML'
        )
        
        try:
            # Отправляем подтверждение клиенту
            confirmation_text = f"""🎉 <b>ВАША БРОНЬ ПОДТВЕРЖДЕНА!</b>

📅 <b>Дата</b>: {day}
🕐 <b>Время</b>: {time}
⏱ <b>Продолжительность</b>: {duration} час(а)

🏢 <b>MS Studio</b>
📍 <b>Адрес</b>: г. Ставрополь, ул. Спартака 8, 2-ой этаж

✅ <i>Ждем вас в студии!</i>
📞 <b>По всем вопросам:</b> +7 (918) 880-52-92

💡 <i>Вы получите напоминания за 24 часа и за 2 часа до сессии.</i>"""
            
            await context.bot.send_message(
                chat_id=user_id,
                text=confirmation_text,
                parse_mode='HTML'
            )
            print(f"✅ Уведомление о подтверждении отправлено клиенту {user_id}")
            
            # НАСТРАИВАЕМ НАПОМИНАНИЯ ДЛЯ КЛИЕНТА
            if context.job_queue:
                # Рассчитываем время напоминаний
                delay_24h, delay_2h = calculate_reminder_times(day, time)
                
                if delay_24h and delay_24h > 0:
                    # Напоминание за 24 часа
                    context.job_queue.run_once(
                        send_24h_reminder_to_client,
                        when=delay_24h,
                        data={
                            'user_id': user_id,
                            'selected_date': day,
                            'selected_time': time,
                            'duration': duration
                        }
                    )
                    print(f"✅ Напоминание за 24 часа настроено для клиента {user_id} (через {delay_24h} сек)")
                else:
                    print(f"⚠️ Напоминание за 24 часа не настроено (недостаточно времени)")
                
                if delay_2h and delay_2h > 0:
                    # Напоминание за 2 часа
                    context.job_queue.run_once(
                        send_2h_reminder_to_client,
                        when=delay_2h,
                        data={
                            'user_id': user_id,
                            'selected_date': day,
                            'selected_time': time,
                            'duration': duration
                        }
                    )
                    print(f"✅ Напоминание за 2 часа настроено для клиента {user_id} (через {delay_2h} сек)")
                else:
                    print(f"⚠️ Напоминание за 2 часа не настроено (недостаточно времени)")
            
        except Exception as e:
            logger.error(f"Не удалось уведомить клиента о подтверждении: {e}")
            
    elif action == 'cancel':
        cursor.execute('UPDATE bookings SET status = ? WHERE id = ?', ('cancelled', booking_id))
        conn.commit()
        
        # Обновляем статистику бронирований пользователя
        update_user_booking_stats(user_id)
        
        # ОТМЕНЯЕМ напоминание администратору
        if context.job_queue:
            job_name = f"admin_reminder_{booking_id}"
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal()
                print(f"🔕 Напоминание администратору отменено для брони {booking_id}")
        
        await query.edit_message_text(
            f"❌ <b>БРОНЬ ОТКЛОНЕНА</b>\n\n"
            f"👤 <b>Клиент</b>: {user_name}\n"
            f"📅 <b>Дата</b>: {day}\n"
            f"🕐 <b>Время</b>: {time}\n"
            f"⏱ <b>Продолжительность</b>: {duration} час(а)\n\n"
            f"❌ <i>Клиент уведомлен об отмене.</i>",
            parse_mode='HTML'
        )
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"😔 <b>ИЗВИНИТЕ, ВАША БРОНЬ НЕ ПОДТВЕРЖДЕНА</b>\n\n"
                     f"📅 <b>Дата</b>: {day}\n"
                     f"🕐 <b>Время</b>: {time}\n\n"
                     f"💡 <b>Возможные причины:</b>\n"
                     f"• Время уже занято\n"
                     f"• Технические работы\n"
                     f"• Изменение графика\n\n"
                     f"🔄 Пожалуйста, выберите другое время\n"
                     f"📞 Или ожидайте - с вами свяжется администратор\n\n"
                     f"📞 <b>Контакты</b>: +7 (918) 880-52-92",
                parse_mode='HTML'
            )
            print(f"✅ Уведомление об отмене отправлено клиенту {user_id}")
        except Exception as e:
            logger.error(f"Не удалось уведомить клиента об отмене: {e}")
    
    conn.close()

# Обработка обычных сообщений
async def handle_message(update: Update, context: CallbackContext) -> None:
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Обновляем статистику пользователя для любого сообщения
    username = update.message.from_user.username or 'без username'
    first_name = update.message.from_user.first_name
    last_name = update.message.from_user.last_name or ''
    update_user_stats(user_id, username, first_name, last_name)
    
    if text == '📅 Расписание':
        await show_schedule(update, context)
    elif text == '🎵 Забронировать':
        await show_booking_menu(update, context)
    elif text == '💰 Цены':
        await show_prices(update, context)
    elif text == '👨‍💻 Связь':
        await contact_admin(update, context)
    elif text == '👑 Админ панель':
        await show_admin_panel(update, context)
    else:
        # Проверяем, находится ли пользователь в админ-панели
        if user_id == ADMIN_ID and text in ['📊 Статистика пользователей', '📈 Аналитика', '📢 Рассылка', '🗓️ Админ расписание', '❌ Отменить запись', '🔙 В главное меню', '🔙 Главное меню', '📈 Новая аналитика', '📊 Экспорт данных', '🔙 В админ-панель', '📅 Выбрать другую дату', '📝 Добавить запись', '❌ Отменить еще запись', '🔙 Назад']:
            await handle_admin_actions_panel(update, context)
        else:
            await update.message.reply_text(
                "Используйте кнопки меню для навигации:",
                reply_markup=get_main_keyboard(user_id)
            )

def main():
    # Инициализация базы данных (полная пересоздание)
    init_db()
    
    # Создание приложения
    application = Application.builder().token(TOKEN).build()

    # ConversationHandler для бронирования
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^🎵 Забронировать$'), show_booking_menu),
            CallbackQueryHandler(handle_new_booking_after_cancel, pattern='^new_booking_after_cancel$'),
            CallbackQueryHandler(handle_start_booking_from_cancel, pattern='^start_booking_from_cancel$'),
            CallbackQueryHandler(handle_to_main_menu_from_cancel, pattern='^to_main_menu_from_cancel$')
        ],
        states={
            SELECT_BOOKING_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_booking_type)],
            SELECT_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date_selection)],
            SELECT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time_selection)],
            SELECT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_duration_selection)],
        },
        fallbacks=[CommandHandler('cancel', start)]
    )

    # ConversationHandler для рассылки
    broadcast_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📢 Рассылка$'), show_broadcast_menu)],
        states={
            BROADCAST_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^🔙 Назад$'), handle_broadcast_message),
                MessageHandler(filters.PHOTO | filters.VIDEO, handle_broadcast_media)
            ],
            BROADCAST_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_confirmation)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex('^🔙 Назад$'), cancel_broadcast),
            CommandHandler('cancel', cancel_broadcast)
        ]
    )

    # ConversationHandler для аналитики
    analytics_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📈 Аналитика$'), show_analytics_menu)],
        states={
            ANALYTICS_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analytics_period)],
            ANALYTICS_PERIOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_period)],
        },
        fallbacks=[CommandHandler('cancel', show_admin_panel)]
    )

    # ConversationHandler для админ расписания
    admin_schedule_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🗓️ Админ расписание$'), show_admin_schedule_menu)],
        states={
            ADMIN_SCHEDULE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_schedule_choice)],
            ADMIN_SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_schedule_date)],
        },
        fallbacks=[
            MessageHandler(filters.Regex('^🔙 Назад$'), show_admin_panel),
            CommandHandler('cancel', show_admin_panel)
        ]
    )

    # ConversationHandler для добавления записи администратором (ОБНОВЛЕН)
    add_booking_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📝 Добавить запись$'), show_add_booking_menu)],
        states={
            ADMIN_ADD_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_add_date)],
            ADMIN_ADD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_add_time)],
            ADMIN_ADD_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_add_duration)],
            ADMIN_ADD_CLIENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_add_client_name)],
            ADMIN_ADD_CLIENT_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_add_client_contact)],
        },
        fallbacks=[CommandHandler('cancel', show_admin_panel)]
    )

    # ConversationHandler для отмены записей администратором
    admin_cancel_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^❌ Отменить запись$'), show_cancel_booking_menu)],
        states={
            ADMIN_CANCEL_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_cancel_date)],
            ADMIN_CANCEL_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_cancel_date)],  # Для возврата к выбору даты
        },
        fallbacks=[
            MessageHandler(filters.Regex('^🔙 Назад$'), show_admin_panel),
            CommandHandler('cancel', show_admin_panel)
        ]
    )

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(broadcast_handler)
    application.add_handler(analytics_handler)
    application.add_handler(admin_schedule_handler)
    application.add_handler(add_booking_handler)
    application.add_handler(admin_cancel_handler)
    application.add_handler(CallbackQueryHandler(handle_admin_actions, pattern='^(confirm|cancel)_'))
    application.add_handler(CallbackQueryHandler(handle_user_cancellation, pattern='^user_cancel_'))
    application.add_handler(CallbackQueryHandler(handle_new_booking_after_cancel, pattern='^new_booking_after_cancel$'))
    application.add_handler(CallbackQueryHandler(handle_start_booking_from_cancel, pattern='^start_booking_from_cancel$'))
    application.add_handler(CallbackQueryHandler(handle_to_main_menu_from_cancel, pattern='^to_main_menu_from_cancel$'))
    application.add_handler(CallbackQueryHandler(handle_admin_cancellation, pattern='^admin_cancel_'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота
    print("🎵 Бот студии звукозаписи запущен!")
    print(f"🆔 ID администратора: {ADMIN_ID}")
    print("✅ База данных полностью пересоздана")
    print("✅ Добавлена новая функция: 'Добавить запись' в админ-панели")
    print("✅ Изменена расстановка кнопок в админ-панели")
    print("✅ Кнопка 'Расширенная аналитика' переименована в 'Аналитика'")
    print("✅ Бронирование доступно с сегодняшнего дня")
    print("✅ На сегодня доступно время только со следующего часа")
    print("✅ Добавлены напоминания клиентам за 24 часа и за 2 часа до сессии")
    print("✅ Добавлена функция отмены брони клиентом")
    print("✅ Напоминания администратору каждые 30 минут о неподтвержденных заявках")
    print("✅ Заявки дублируются администратору с кнопками подтверждения/отмены")
    print("✅ Добавлена новая услуга 'Сведение вместе с артистом' в прайс-лист")
    print("✅ Добавлена админ-панель с кнопками управления")
    print("✅ Реализована подробная статистика пользователей")
    print("✅ Исправлена проблема с часовым поясом - используется локальное время")
    print("✅ Исправлена проблема с отображением админ-панели")
    print("✅ Реализована функция массовой рассылки сообщений")
    print("✅ Реализована аналитика для администратора")
    print("✅ Реализована функция экспорта данных в CSV формате")
    print("✅ Исправлены кнопки в рассылке - теперь везде '🔙 Назад'")
    print("✅ Добавлена функция 'Админ расписание' для просмотра бронирований")
    print("✅ ИСПРАВЛЕНА аналитика - теперь корректно отображаются все дни недели")
    print("✅ ДОБАВЛЕН сайт студии в контакты: https://msstudio-stav.ru/")
    print("✅ ИЗМЕНЕН текст приветствия в главном меню")
    print("✅ ИСПРАВЛЕНА функция бронирования после отмены - теперь корректно запускается процесс бронирования")
    print("✅ ДОБАВЛЕНЫ ссылки на VK и Telegram канал в контакты")
    print("✅ ИСПРАВЛЕНА кнопка 'Назад' в ручном вводе даты - теперь возвращает в меню бронирования")
    print("✅ ИСПРАВЛЕНЫ кнопки в меню бронирования после отмены - теперь работают корректно")
    print("✅ ДОБАВЛЕНА функция добавления записи администратором с пошаговым процессом")
    print("✅ Записи от администратора отображаются в расписании с пометкой '(админ)'")
    print("✅ В аналитике вместо ссылок на Telegram отображаются имена клиентов, введенные администратором")
    print("✅ ИСПРАВЛЕНЫ функции проверки доступности времени - теперь корректно учитываются все бронирования")
    print("✅ ДОБАВЛЕНА функция отмены записей администратором с пошаговым процессом")
    print("✅ При отмене администратором клиент получает уведомление с предложением забронировать новое время")
    print("✅ Время отмененных записей становится доступным для бронирования другими клиентами")
    print("✅ ИСПРАВЛЕНА проблема: в админ расписании теперь отображаются ссылки на пользователей Telegram")
    print("✅ ИСПРАВЛЕНА проблема: кнопка 'Забронировать новое время' после отмены администратором теперь ведет в главное меню бронирования")
    print("✅ ДОБАВЛЕН ввод контакта клиента при добавлении записи администратором")
    print("✅ ИСПРАВЛЕНА навигация в админ расписании - убрана кнопка 'Новая дата'")
    print("✅ ИСПРАВЛЕНА ошибка при просмотре пустого расписания")
    print("✅ РЕШЕНА ЗАДАЧА 1: При отмене брони клиентом убрана кнопка 'Забронировать новую сессию'")
    print("✅ РЕШЕНА ЗАДАЧА 2: Кнопка 'В главное меню' заменена на 'Назад' и ведет на уровень выше")
    application.run_polling()

if __name__ == '__main__':

    main()
