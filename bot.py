#!/usr/bin/env python3
"""
🐋 PolyMrktCopy - Professional Whale Tracking Bot
Telegram бот для моніторингу великих ставок на Polymarket
"""

import logging
import asyncio
import aiohttp
import os
import sqlite3
import hashlib
from datetime import datetime
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError
import signal
import sys

# Завантажуємо змінні середовища
load_dotenv()

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('whale_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Текст для незареєстрованих користувачів
RESTRICTED_MESSAGE = """🔒 <b>Доступ до PolyMrktCopy обмежено</b> 🔒

На жаль, на вашому акаунті наразі немає активного доступу до системи.

<b>PolyMrktCopy</b> — це професійний інструмент для моніторингу великого капіталу на Polymarket. Ми свідомо тримаємо проект у закритому режимі, щоб забезпечити максимальну швидкість доставки даних та зберегти цінність інформації для тих, хто вже з нами. 📈

<b>Чому так?</b>

✨ <b>Стабільність:</b> Кожне сповіщення про ставку кита обробляється миттєво, що потребує величезних ресурсів. ⚡️

🐋 <b>Ексклюзивність:</b> Чим менше людей володіє інформацією про дії китів, тим ефективніше вона працює.

📊 <b>Якість:</b> Ми не прагнемо масовості, ми прагнемо результату для обмеженого кола користувачів.

Ваш запит на вхід залишається в системі, але на даний момент реєстрація нових учасників не проводиться. 

Слідкуйте за оновленнями та чекайте на відкриття нових слотів. 👋
"""

# Текст для зареєстрованих користувачів
WELCOME_MESSAGE = """✨ <b>Ласкаво просимо до PolyMrktCopy!</b> ✨

🐋 Вы отримали доступ до професійної системи моніторингу великого капіталу на Polymarket.

<b>Ваш профіль активний!</b> Система тепер буде надсилати вам сповіщення про значні ставки китів у реальному часі. 🚀

<b>Параметри моніторингу:</b>
💰 Мінімальний оборот: $10,000
👤 Мінімум від трейдера: $5,000
📊 Макс сповіщень: 50/год

Дякуємо за те, що ви з нами! 🙏
"""

class PolyMrktCopyBot:
    """PolyMrktCopy - Professional Whale Tracking Bot"""
    
    def __init__(self, telegram_token: str, chat_id: str = None):
        """Ініціалізація бота"""
        self.token = telegram_token
        self.bot = Bot(token=telegram_token)
        self.chat_id = chat_id
        self.checked_bets = set()
        self.running = True
        
        # Налаштування фільтрів
        self.min_turnover = 10000
        self.min_trader_amount = 5000
        self.min_order_size = 1  # Мінімальна кількість ставки
        self.max_notifications_per_hour = 50
        self.notifications_count = 0
        self.last_hour = datetime.now()
        
        # Ініціалізуємо БД
        self._init_database()
        
        logger.info("✅ PolyMrktCopy ініціалізована успішно")
    
    def _init_database(self):
        """Створює таблиці в БД"""
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS whale_trades (
                    id TEXT PRIMARY KEY,
                    market_id TEXT,
                    market_question TEXT,
                    trader_amount REAL,
                    outcome_price REAL,
                    side TEXT,
                    trader_address TEXT,
                    tx_hash TEXT,
                    timestamp DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_users (
                    chat_id TEXT PRIMARY KEY,
                    access_granted BOOLEAN DEFAULT 1,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS whale_stats (
                    date TEXT PRIMARY KEY,
                    trades_count INTEGER DEFAULT 0,
                    total_volume REAL DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("✅ База даних ініціалізована")
        except Exception as e:
            logger.error(f"❌ Помилка ініціалізації БД: {e}")
    
    def _check_access(self, chat_id: str) -> bool:
        """Перевіряє чи користувач має доступ"""
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            cursor.execute('SELECT access_granted FROM bot_users WHERE chat_id = ?', (chat_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result and result[0]:
                return True
            return False
        except:
            return False
    
    async def send_telegram_message(self, message: str, chat_id: str = None) -> bool:
        """Надсилає повідомлення в Telegram"""
        target_chat_id = chat_id or self.chat_id
        
        if not target_chat_id:
            logger.warning("⚠️ Chat ID не встановлений")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=message,
                parse_mode='HTML'
            )
            return True
        except TelegramError as e:
            logger.error(f"❌ Помилка надсилання Telegram: {e}")
            return False
    
    def check_rate_limit(self) -> bool:
        """Перевіряє ліміт на сповіщення"""
        now = datetime.now()
        
        if now.hour != self.last_hour.hour:
            self.notifications_count = 0
            self.last_hour = now
        
        if self.notifications_count >= self.max_notifications_per_hour:
            return False
        
        self.notifications_count += 1
        return True
    
    async def get_polymarket_markets(self):
        """Отримує ринки з Polymarket API"""
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://clob.polymarket.com/markets"
                params = {
                    "limit": 100,
                    "order": "volume24h"
                }
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        markets = await response.json()
                        filtered = [
                            m for m in markets 
                            if 'gamblingIsAllYouNeed' in m.get('tags', [])
                        ]
                        return filtered
                    return []
        except asyncio.TimeoutError:
            logger.warning("⚠️ Timeout при отриманні ринків")
            return []
        except Exception as e:
            logger.error(f"❌ Помилка отримання ринків: {e}")
            return []
    
    async def get_market_trades(self, market_id: str):
        """Отримує угоди для конкретного ринку"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://clob.polymarket.com/trades"
                params = {
                    "market": market_id,
                    "limit": 100
                }
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        trades = await response.json()
                        return trades if isinstance(trades, list) else []
                    return []
        except Exception as e:
            logger.error(f"❌ Помилка отримання угод: {e}")
            return []
    
    def check_whale_filters(self, trade: dict) -> tuple[bool, float]:
        """Перевіряє фільтри для китової угоди"""
        try:
            size = float(trade.get('size', 0))
            price = float(trade.get('price', 0))
            trade_value = size * price
            
            # Фільтр 1: Мінімальний оборот
            if trade_value < self.min_turnover:
                return (False, trade_value)
            
            # Фільтр 2: Мінімальна сума трейдера
            if trade_value < self.min_trader_amount:
                return (False, trade_value)
            
            # ФІЛЬТР 3: Мінімальна кількість ставки
            if size < self.min_order_size:
                return (False, trade_value)
            
            # Фільтр 4: Rate limiting
            if not self.check_rate_limit():
                return (False, trade_value)
            
            return (True, trade_value)
        except (ValueError, TypeError):
            return (False, 0)
    
    def get_side_emoji(self, side: str) -> str:
        """Повертає emoji для сторони ставки"""
        side_lower = side.lower()
        if side_lower == 'yes':
            return '🟢'
        elif side_lower == 'no':
            return '🔴'
        else:
            return '🟡'
    
    def format_address(self, address: str) -> str:
        """Форматує адресу гаманця"""
        if not address or len(address) < 8:
            return address
        return f"{address[:6]}...{address[-4:]}"
    
    def format_tx_hash(self, tx_hash: str) -> str:
        """Форматує хеш транзакції"""
        if not tx_hash or len(tx_hash) < 10:
            return tx_hash or "pending"
        return f"{tx_hash[:8]}...{tx_hash[-4:]}"
    
    async def save_whale_trade(self, market: dict, trade: dict, trade_value: float):
        """Зберігає китову угоду в БД"""
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            
            market_id = market.get('id')
            trade_id = f"{market_id}_{trade.get('id', '')}"
            
            cursor.execute('''
                INSERT OR IGNORE INTO whale_trades 
                (id, market_id, market_question, trader_amount, outcome_price, side, trader_address, tx_hash, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_id,
                market_id,
                market.get('question', 'Unknown'),
                trade_value,
                float(trade.get('price', 0)),
                trade.get('side', 'unknown'),
                trade.get('trader', ''),
                trade.get('tx_hash', ''),
                datetime.fromisoformat(trade.get('timestamp', datetime.now().isoformat()))
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Помилка збереження угоди: {e}")
    
    def format_whale_message(self, market: dict, trade: dict, trade_value: float) -> str:
        """Форматує повідомлення про китову угоду з хешем та адресою"""
        try:
            side = trade.get('side', 'unknown').upper()
            side_emoji = self.get_side_emoji(side)
            
            market_question = market.get('question', 'Unknown')[:75]
            price = float(trade.get('price', 0))
            size = float(trade.get('size', 0))
            
            # АДРЕСА ГАМАНЦЯ
            trader_address = trade.get('trader', '')
            short_address = self.format_address(trader_address)
            
            # ХЕШ ТРАНЗАКЦІЇ
            tx_hash = trade.get('tx_hash', '')
            short_tx = self.format_tx_hash(tx_hash)
            
            # ЧАС
            timestamp = trade.get('timestamp', datetime.now().isoformat())
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime('%H:%M:%S UTC')
            except:
                time_str = datetime.now().strftime('%H:%M:%S UTC')
            
            bet_type = "PAPER"
            
            # ФОРМАТУЄМО ПОВІДОМЛЕННЯ З АДРЕСОЮ ТА ХЕШЕМ
            message = f"""
{side_emoji} <b>[{bet_type}] {side}</b> • #{size:.0f}
<i>{market_question}</i>

💰 <b>Трейдер:</b> ${trade_value:,.0f}
📊 <b>Ціна:</b> ${price:.2f} | <b>Розмір:</b> {size:.3f}

<b>👤 Гаманець:</b> <code>{short_address}</code>
<b>🔗 TX:</b> <code>{short_tx}</code>

⏰ {time_str}
<b>🏷️ GamblingIsAllYouNeed</b>
"""
            
            return message.strip()
        
        except Exception as e:
            logger.error(f"❌ Помилка форматування: {e}")
            return "🐋 Виявлена китова угода на Polymarket"
    
    async def monitor_markets(self, interval: int = 20):
        """Головна функція моніторингу ринків"""
        logger.info("🚀 Запуск PolyMrktCopy...")
        logger.info(f"📊 Фільтри: Мін. оборот=${self.min_turnover}, Мін. сума=${self.min_trader_amount}, Мін. кількість={self.min_order_size}")
        
        # Надсилаємо привіт
        if self.chat_id:
            await self.send_telegram_message(WELCOME_MESSAGE)
        
        check_count = 0
        
        while self.running:
            try:
                markets = await self.get_polymarket_markets()
                
                if not markets:
                    await asyncio.sleep(interval)
                    continue
                
                detected_whales = 0
                
                for market in markets:
                    market_id = market.get('id')
                    if not market_id:
                        continue
                    
                    trades = await self.get_market_trades(market_id)
                    
                    for trade in trades:
                        trade_id = f"{market_id}_{trade.get('id', '')}"
                        
                        if trade_id in self.checked_bets:
                            continue
                        
                        is_whale, trade_value = self.check_whale_filters(trade)
                        
                        if is_whale:
                            self.checked_bets.add(trade_id)
                            detected_whales += 1
                            
                            await self.save_whale_trade(market, trade, trade_value)
                            
                            message = self.format_whale_message(market, trade, trade_value)
                            success = await self.send_telegram_message(message)
                            
                            if success:
                                logger.info(f"✅ Китова угода: ${trade_value:,.0f}")
                        
                        if len(self.checked_bets) > 10000:
                            self.checked_bets.clear()
                
                check_count += 1
                logger.info(f"✓ Цикл #{check_count}: {len(markets)} ринків, {detected_whales} китів")
                
                await asyncio.sleep(interval)
            
            except asyncio.CancelledError:
                logger.info("⛔ Моніторинг скасований")
                break
            except Exception as e:
                logger.error(f"❌ Помилка: {e}")
                await asyncio.sleep(interval)
    
    async def start(self):
        """Запускає бота"""
        try:
            bot_info = await self.bot.get_me()
            logger.info(f"✅ PolyMrktCopy активована: @{bot_info.username}")
            
            logger.info("=" * 70)
            logger.info("🐋 PolyMrktCopy - Professional Whale Tracking")
            logger.info("=" * 70)
            logger.info(f"✅ Бот активний!")
            if self.chat_id:
                logger.info(f"📱 Chat ID: {self.chat_id} ✅ Доступ надано")
            else:
                logger.info(f"📱 Chat ID: Не встановлений ⚠️")
            logger.info("=" * 70)
            
            await self.monitor_markets()
        
        except Exception as e:
            logger.error(f"❌ Критична помилка: {e}")
            self.running = False


async def main():
    """Головна функція"""
    
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    CHAT_ID = os.getenv('CHAT_ID')
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не встановлений!")
        return
    
    # Якщо CHAT_ID не встановлений - режим без доступу
    bot = PolyMrktCopyBot(TELEGRAM_TOKEN, CHAT_ID)
    
    # Якщо CHAT_ID встановлений - регіструємо користувача
    if CHAT_ID:
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO bot_users (chat_id, access_granted)
                VALUES (?, 1)
            ''', (CHAT_ID,))
            conn.commit()
            conn.close()
            logger.info(f"✅ Користувач {CHAT_ID} зареєстрований з доступом")
        except:
            pass
    else:
        logger.warning("⚠️ CHAT_ID не встановлений - бот в режимі очікування")
    
    def signal_handler(sig, frame):
        logger.info("⛔ Сигнал завершення")
        bot.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Зупинено")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")
        sys.exit(1)
