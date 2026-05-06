import logging
import asyncio
import aiohttp
import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

from telegram import Bot, Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler
from telegram.error import TelegramError

# Завантажуємо змінні середовища
load_dotenv()

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('whale_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class PolymarketWhaleBot:
    def __init__(self, telegram_token: str):
        self.telegram_token = telegram_token
        self.bot = Bot(token=telegram_token)
        self.checked_bets = set()
        
        # НАЛАШТУВАННЯ ФІЛЬТРІВ
        self.min_turnover = 10000
        self.min_trader_amount = 5000
        self.max_notifications_per_hour = 50
        self.notifications_count = 0
        self.last_hour = datetime.now()
        
        self.chat_id = None
        self.monitoring = False
        
        self._init_database()
    
    def _init_database(self):
        """Ініціалізує БД"""
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
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("✅ База даних ініціалізована")
        except Exception as e:
            logger.error(f"❌ Помилка БД: {e}")
    
    def _save_user(self, chat_id: str, username: str, first_name: str, last_name: str = ""):
        """Зберігає інформацію про користувача"""
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO bot_users 
                (chat_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            ''', (chat_id, username, first_name, last_name))
            
            conn.commit()
            conn.close()
            logger.info(f"✅ Користувач: @{username} (ID: {chat_id})")
        except Exception as e:
            logger.error(f"❌ Помилка збереження користувача: {e}")
    
    def _load_user(self):
        """Завантажує chat_id з БД"""
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            cursor.execute('SELECT chat_id FROM bot_users LIMIT 1')
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"❌ Помилка завантаження користувача: {e}")
            return None
    
    async def handle_start(self, update: Update, context):
        """Команда /start"""
        try:
            if update.effective_user and update.effective_chat:
                self.chat_id = str(update.effective_chat.id)
                self._save_user(
                    self.chat_id,
                    update.effective_user.username or "unknown",
                    update.effective_user.first_name or "",
                    update.effective_user.last_name or ""
                )
                
                msg = f"""
🐋 <b>Polymarket Whale Bot</b>

✅ Бот активований!

<b>📊 Налаштування:</b>
• Мін. оборот: <code>${self.min_turnover:,}</code>
• Мін. від трейдера: <code>${self.min_trader_amount:,}</code>
• Макс алертів/год: <code>{self.max_notifications_per_hour}</code>

🚀 Моніторинг розпочато!

Команди:
/status - статус
/stop - зупинити
"""
                await update.message.reply_html(msg)
                
                if not self.monitoring:
                    self.monitoring = True
                    asyncio.create_task(self.monitor_markets())
        
        except Exception as e:
            logger.error(f"❌ Помилка /start: {e}")
    
    async def handle_status(self, update: Update, context):
        """Команда /status"""
        try:
            msg = f"""
🟢 <b>БОТ АКТИВНИЙ</b>

📊 Сповіщень цю годину: {self.notifications_count}/{self.max_notifications_per_hour}
⏰ Статус: МОНІТОРИНГ

Параметри:
• Мін. оборот: ${self.min_turnover:,}
• Інтервал: 20 сек
"""
            await update.message.reply_html(msg)
        except Exception as e:
            logger.error(f"❌ Помилка /status: {e}")
    
    async def handle_message(self, update: Update, context):
        """Обробник звичайних повідомлень"""
        try:
            if update.effective_user and update.effective_chat:
                self.chat_id = str(update.effective_chat.id)
                self._save_user(
                    self.chat_id,
                    update.effective_user.username or "unknown",
                    update.effective_user.first_name or "",
                    update.effective_user.last_name or ""
                )
        except Exception as e:
            logger.error(f"❌ Помилка обробки повідомлення: {e}")
    
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
        """Отримує ринки з Polymarket"""
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://clob.polymarket.com/markets"
                params = {"limit": 100, "order": "volume24h"}
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        markets = await response.json()
                        return [m for m in markets if 'gamblingIsAllYouNeed' in m.get('tags', [])]
                    return []
        except Exception as e:
            logger.error(f"❌ Помилка отримання ринків: {e}")
            return []
    
    async def get_market_trades(self, market_id: str):
        """Отримує угоди для ринку"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://clob.polymarket.com/trades"
                params = {"market": market_id, "limit": 100}
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
            
            if trade_value < self.min_turnover or trade_value < self.min_trader_amount:
                return (False, trade_value)
            
            if not self.check_rate_limit():
                return (False, trade_value)
            
            return (True, trade_value)
        except (ValueError, TypeError):
            return (False, 0)
    
    def get_side_emoji(self, side: str) -> str:
        """Emoji для сторони ставки"""
        side_lower = side.lower()
        return '🟢' if side_lower == 'yes' else ('🔴' if side_lower == 'no' else '🟡')
    
    def format_address(self, address: str) -> str:
        """Скорочує адресу"""
        if not address or len(address) < 8:
            return address
        return f"{address[:6]}...{address[-4:]}"
    
    async def save_whale_trade(self, market: dict, trade: dict, trade_value: float):
        """Зберігає угоду в БД"""
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
                trade_id, market_id, market.get('question', 'Unknown'),
                trade_value, float(trade.get('price', 0)), trade.get('side', 'unknown'),
                trade.get('trader', ''), trade.get('tx_hash', ''),
                datetime.fromisoformat(trade.get('timestamp', datetime.now().isoformat()))
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Помилка збереження: {e}")
    
    def format_whale_message(self, market: dict, trade: dict, trade_value: float) -> str:
        """Форматує повідомлення про китову угоду"""
        try:
            side = trade.get('side', 'unknown').upper()
            side_emoji = self.get_side_emoji(side)
            market_question = market.get('question', 'Unknown')[:80]
            price = float(trade.get('price', 0))
            size = float(trade.get('size', 0))
            short_address = self.format_address(trade.get('trader', ''))
            
            tx_hash = trade.get('tx_hash', '')
            short_tx = tx_hash[:10] + '…' if tx_hash else 'pending'
            
            try:
                dt = datetime.fromisoformat(trade.get('timestamp', datetime.now().isoformat()))
                time_str = dt.strftime('%H:%M:%S UTC')
            except:
                time_str = datetime.now().strftime('%H:%M:%S UTC')
            
            return f"""
{side_emoji} [PAPER] {side} • #{size:.0f}
{market_question}

📊 Трейдер: ${trade_value:,.0f} → n: ${price:.2f} ● ${size:.3f}
👤 GamblingIsAllYouNeed {short_address}

{time_str} • tx {short_tx}
"""
        except Exception as e:
            logger.error(f"❌ Помилка форматування: {e}")
            return "🐋 Виявлена китова угода"
    
    async def send_telegram_message(self, message: str) -> bool:
        """Надсилає повідомлення в Telegram"""
        if not self.chat_id:
            return False
        
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=message, parse_mode='HTML')
            return True
        except TelegramError as e:
            logger.error(f"❌ Помилка Telegram: {e}")
            return False
    
    async def monitor_markets(self, interval: int = 20):
        """Моніторинг китів"""
        while not self.chat_id:
            await asyncio.sleep(2)
        
        logger.info("🚀 Запуск моніторингу китів на Polymarket...")
        check_count = 0
        
        while self.monitoring:
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
                            await self.send_telegram_message(message)
                            logger.info(f"✅ Китова угода: ${trade_value:,.0f}")
                        
                        if len(self.checked_bets) > 10000:
                            self.checked_bets.clear()
                
                check_count += 1
                logger.info(f"✓ Цикл #{check_count}: {len(markets)} ринків, {detected_whales} китів")
                
                await asyncio.sleep(interval)
            
            except Exception as e:
                logger.error(f"❌ Помилка в циклі: {e}")
                await asyncio.sleep(interval)


async def main():
    # НАЛАШТУВАННЯ
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не встановлений!")
        return
    
    # Запускаємо бота
    whale_bot = PolymarketWhaleBot(TELEGRAM_TOKEN)
    
    # Завантажуємо збережений chat_id
    saved_chat_id = whale_bot._load_user()
    if saved_chat_id:
        whale_bot.chat_id = saved_chat_id
        logger.info(f"📨 Знайдено збережений chat_id: {whale_bot.chat_id}")
        whale_bot.monitoring = True
    
    logger.info("=" * 70)
    logger.info("🐋 POLYMARKET WHALE BOT - ПРИВАТНИЙ ЧАТ")
    logger.info("=" * 70)
    logger.info(f"✅ Бот готовий!")
    logger.info(f"📱 Telegram ID: 8645033199")
    logger.info(f"📬 Команда: /start")
    logger.info("=" * 70)
    
    # Запускаємо Application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Обробники
    application.add_handler(CommandHandler("start", whale_bot.handle_start))
    application.add_handler(CommandHandler("status", whale_bot.handle_status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, whale_bot.handle_message))
    
    # Запускаємо
    async with application:
        await application.initialize()
        await application.start()
        
        # Запускаємо моніторинг у фоні
        monitor_task = asyncio.create_task(whale_bot.monitor_markets())
        
        # Чекаємо сигналу зупинки
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("⛔ Зупинка...")
            whale_bot.monitoring = False
            await monitor_task
        finally:
            await application.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ Критична помилка: {e}")
