import logging
import hashlib
import sqlite3
from datetime import datetime
import requests
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Загрузка переменных окружения
from dotenv import load_dotenv
load_dotenv()

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
CHECK_INTERVAL = 15  # минут

# API endpoints
NEWS_API_URL = "https://newsapi.org/v2/everything"
TRANSLATE_API_URL = "https://api.mymemory.translated.net/get"

# Настройка базы данных
conn = sqlite3.connect('news.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS news
             (id TEXT PRIMARY KEY, 
              title TEXT, 
              title_ru TEXT,
              url TEXT, 
              source TEXT,
              published_at TIMESTAMP)''')
conn.commit()

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def translate_text(text: str) -> str:
    """Перевод текста через MyMemory Translation API"""
    if not text.strip():
        return text

    try:
        params = {
            'q': text,
            'langpair': 'en|ru',
            'de': 'example@example.com'
        }

        response = requests.get(
            TRANSLATE_API_URL,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        
        if result['responseStatus'] == 200:
            return result['responseData']['translatedText']
        return text

    except Exception as e:
        logger.error(f"Ошибка перевода: {str(e)}")
        return text

def generate_news_id(title: str, url: str) -> str:
    """Генерация уникального ID для новости"""
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()

def fetch_bitcoin_news() -> list:
    """Получение новостей о Bitcoin"""
    params = {
        "q": "Bitcoin OR BTC",
        "sortBy": "publishedAt",
        "apiKey": NEWS_API_KEY,
        "language": "en",
        "pageSize": 20
    }
    
    try:
        response = requests.get(NEWS_API_URL, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get('articles', [])
    except Exception as e:
        logger.error(f"Ошибка News API: {e}")
        return []

async def check_and_send_news(context: ContextTypes.DEFAULT_TYPE):
    """Проверка и отправка новых новостей"""
    try:
        articles = fetch_bitcoin_news()
        if not articles:
            logger.info("Новых новостей не найдено")
            return

        new_articles = []
        for article in articles:
            try:
                title = article['title']
                url = article['url']
                source = article['source']['name']
                published_at = datetime.strptime(
                    article['publishedAt'], 
                    '%Y-%m-%dT%H:%M:%SZ'
                )
                
                news_id = generate_news_id(title, url)
                
                # Проверка на существование в базе
                c.execute('SELECT id FROM news WHERE id=?', (news_id,))
                if not c.fetchone():
                    new_articles.append({
                        'id': news_id,
                        'title': title,
                        'url': url,
                        'source': source,
                        'published_at': published_at
                    })

            except Exception as e:
                logger.error(f"Ошибка обработки статьи: {e}")

        # Отправляем только новые статьи
        for article in new_articles:
            translated_title = translate_text(article['title'])
            
            message = (
                f"📰 *{translated_title}*\n"
                f"🔗 _Источник: {article['source']}_\n"
                f"📆 _Дата: {article['published_at'].strftime('%d.%m.%Y %H:%M')}_\n"
                f"[Читать оригинал]({article['url']})"
            )
            
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=message,
                parse_mode="Markdown"
            )
            
            # Сохраняем в базу
            c.execute('''
                INSERT INTO news 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (article['id'], article['title'], translated_title, 
                  article['url'], article['source'], article['published_at']))
            conn.commit()

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    chat_id = update.effective_chat.id
    context.job_queue.run_repeating(
        check_and_send_news,
        interval=CHECK_INTERVAL * 60,
        first=10,
        chat_id=chat_id
    )
    await update.message.reply_text(
        f"🔔 Бот активирован! Новости будут приходить каждые {CHECK_INTERVAL} минут.\n"
        "Используйте /latest для мгновенного получения новостей."
    )

async def latest_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /latest"""
    try:
        articles = fetch_bitcoin_news()[:5]
        if not articles:
            await update.message.reply_text("😔 Новости не найдены")
            return

        sent_articles = []
        for article in articles:
            title = article['title']
            url = article['url']
            news_id = generate_news_id(title, url)
            
            # Проверяем наличие в базе
            c.execute('SELECT id FROM news WHERE id=?', (news_id,))
            if not c.fetchone():
                translated_title = translate_text(title)
                message = (
                    f"📰 *{translated_title}*\n"
                    f"[Читать статью]({url})"
                )
                await update.message.reply_text(message, parse_mode="Markdown")
                
                # Сохраняем в базу
                c.execute('''
                    INSERT INTO news 
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (news_id, title, translated_title, 
                      url, article['source']['name'], 
                      datetime.strptime(article['publishedAt'], '%Y-%m-%dT%H:%M:%SZ')))
                sent_articles.append(news_id)
        
        if not sent_articles:
            await update.message.reply_text("🔄 Новых новостей нет")

        conn.commit()

    except Exception as e:
        logger.error(f"Ошибка в /latest: {e}")
        await update.message.reply_text("⚠️ Ошибка при получении новостей")

if __name__ == "__main__":
    # Проверка обязательных переменных
    required_vars = ["TELEGRAM_TOKEN", "NEWS_API_KEY"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.critical(f"Отсутствуют переменные: {', '.join(missing_vars)}")
        exit(1)

    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("latest", latest_news))
        
        logger.info("Запуск бота...")
        app.run_polling()
        
    except Exception as e:
        logger.critical(f"Фатальная ошибка: {e}")
    finally:
        conn.close()
        logger.info("Соединение с БД закрыто")