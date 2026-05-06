import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler
from telegram.error import TelegramError
import sqlite3
import json
from pathlib import Path

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

class PolymarketWhaleBotPrivate:
    def __init__(self, telegram_token: str):
        self.telegram_token = telegram_token
        self.bot = Bot(token=telegram_token)
        self.checked_bets = set()
        
        # 🔧 НАЛАШТУВАННЯ ФІЛЬТРІВ
        self.min_turnover = 10000          # Мінімальний оборот кита для сповіщення ($)
        self.min_trader_amount = 5000       # Мінімальна сума трейдера ($)
        self.max_notifications_per_hour = 50  # Максимум сповіщень на годину
        self.notifications_count = 0
        self.last_hour = datetime.now()
        
        # Chat ID буде отриманий від користувача
        self.chat_id = None
        self.user_info = None
        
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
            
            # Таблиця для зберігання user ID
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
            logger.info(f"✅ Користувач зареєстрований: @{username} (ID: {chat_id})")
        except Exception as e:
            logger.error(f"❌ Помилка при збереженні користувача: {e}")
    
    def _load_user(self):
        """Завантажує інформацію про користувача з БД"""
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            
            cursor.execute('SELECT chat_id, username FROM bot_users LIMIT 1')
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return result[0]
            return None
        except Exception as e:
            logger.error(f"❌ Помилка при завантаженні користувача: {e}")
            return None
    
    async def handle_user_message(self, update: Update, context):
        """Обробник повідомлень від користувача"""
        try:
            if update.effective_user and update.effective_chat:
                # Зберігаємо chat_id від першого повідомлення
                self.chat_id = str(update.effective_chat.id)
                
                # Зберігаємо інформацію про користувача
                self._save_user(
                    self.chat_id,
                    update.effective_user.username or "unknown",
                    update.effective_user.first_name or "",
                    update.effective_user.last_name or ""
                )
                
                # Привіт повідомлення
                welcome_message = f"""
🤖 <b>Привіт, {update.effective_user.first_name}!</b>

✅ Я готовий слідкувати за китами на Polymarket!

<b>Налаштування:</b>
💰 Мінімальний оборот: ${self.min_turnover:,}
👤 Мінімальна сума від трейдера: ${self.min_trader_amount:,}
⏱️  Максимум сповіщень/годину: {self.max_notifications_per_hour}

🚀 Почнемо моніторинг китів на Polymarket...

📊 Всі сповіщення про великі ставки будуть приходити сюди!

#whale #polymarket #gamblingIsAllYouNeed
"""
                
                await update.message.reply_html(welcome_message)
                logger.info(f"✅ Користувач підключився: {update.effective_user.first_name} (ID: {self.chat_id})")
        
        except Exception as e:
            logger.error(f"❌ Помилка обробки повідомлення: {e}")
    
    async def handle_start_command(self, update: Update, context):
        """Обробник команди /start"""
        try:
            if update.effective_user and update.effective_chat:
                self.chat_id = str(update.effective_chat.id)
                self._save_user(
                    self.chat_id,
                    update.effective_user.username or "unknown",
                    update.effective_user.first_name or "",
                    update.effective_user.last_name or ""
                )
                
                start_message = f"""
🐋 <b>Polymarket Whale Bot</b>

✅ Бот активований і готовий до роботи!

<b>📊 Поточні налаштування:</b>
• Мінімальний оборот: <code>${self.min_turnover:,}</code>
• Мінімальна сума трейдера: <code>${self.min_trader_amount:,}</code>
• Максимум алертів/годину: <code>{self.max_notifications_per_hour}</code>

🔍 Будуть моніторитися ринки з тегом <code>gamblingIsAllYouNeed</code>

💬 Команди:
/status - статус бота
/stats - статистика
/settings - налаштування

🚀 Моніторинг розпочато! Очікуємо китів...

#whale #polymarket #gamblingIsAllYouNeed
"""
                
                await update.message.reply_html(start_message)
                logger.info(f"✅ /start команда виконана для {update.effective_user.first_name}")
        
        except Exception as e:
            logger.error(f"❌ Помилка команди /start: {e}")
    
    async def handle_status_command(self, update: Update, context):
        """Обробник команди /status"""
        try:
            status_message = f"""
🟢 <b>БОТ АКТИВНИЙ</b>

⏰ Статус: <code>МОНІТОРИНГ</code>
📊 Сповіщень цю годину: <code>{self.notifications_count}/{self.max_notifications_per_hour}</code>

<b>Параметри:</b>
• Мінімум оборот: <code>${self.min_turnover:,}</code>
• Мінімум від трейдера: <code>${self.min_trader_amount:,}</code>
• Інтервал перевірки: <code>20 сек</code>

🔍 Моніторяться: <code>gamblingIsAllYouNeed</code>

Часова зона: <code>UTC</code>
"""
            await update.message.reply_html(status_message)
        except Exception as e:
            logger.error(f"❌ Помилка команди /status: {e}")
    
    def check_rate_limit(self) -> bool:
        """Перевіряє ліміт на сповіщення"""
        now = datetime.now()
        
        if (now.hour != self.last_hour.hour):
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
                params = {
                    "limit": 100,
                    "order": "volume24h"
                }
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        markets = await response.json()
                        filtered = [m for m in markets 
                                   if 'gamblingIsAllYouNeed' in m.get('tags', [])]
                        return filtered
                    return []
        except Exception as e:
            logger.error(f"❌ Помилка отримання ринків: {e}")
            return []
    
    async def get_market_trades(self, market_id: str):
        """Отримує угоди для ринку"""
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
            
            # ФІЛЬТР 1: Мінімальний оборот
            if trade_value < self.min_turnover:
                return (False, trade_value)
            
            # ФІЛЬТР 2: Мінімальна сума трейдера
            if trade_value < self.min_trader_amount:
                return (False, trade_value)
            
            # ФІЛЬТР 3: Rate limiting
            if not self.check_rate_limit():
                logger.warning(f"⚠️  Досягнуто ліміт сповіщень на годину")
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
        """Форматує адресу"""
        if not address or len(address) < 8:
            return address
        return f"{address[:6]}...{address[-4:]}"
    
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
            logger.error(f"❌ Помилка збереження в БД: {e}")
    
    def format_whale_message(self, market: dict, trade: dict, trade_value: float) -> str:
        """Форматує повідомлення про китову угоду"""
        try:
            side = trade.get('side', 'unknown').upper()
            side_emoji = self.get_side_emoji(side)
            
            market_question = market.get('question', 'Unknown')[:80]
            price = float(trade.get('price', 0))
            size = float(trade.get('size', 0))
            
            trader_address = trade.get('trader', '')
            short_address = self.format_address(trader_address)
            
            tx_hash = trade.get('tx_hash', '')
            short_tx = tx_hash[:10] + '…' if tx_hash else 'pending'
            
            timestamp = trade.get('timestamp', datetime.now().isoformat())
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime('%H:%M:%S UTC')
            except:
                time_str = datetime.now().strftime('%H:%M:%S UTC')
            
            bet_type = "PAPER"
            
            message = f"""
{side_emoji} [{bet_type}] {side} • #{size:.0f}
{market_question}

📊 Трейдер: ${trade_value:,.0f} → n: ${price:.2f} ● ${size:.3f}
👤 GamblingIsAllYouNeed {short_address}

{time_str} • tx {short_tx}
"""
            
            return message.strip()
        
        except Exception as e:
            logger.error(f"❌ Помилка форматування: {e}")
            return "🐋 Виявлена китова угода на Polymarket"
    
    async def send_telegram_message(self, message: str) -> bool:
        """Надсилає повідомлення в Telegram"""
        if not self.chat_id:
            logger.warning("⚠️  Chat ID не встановлений")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML'
            )
            return True
        except TelegramError as e:
            logger.error(f"❌ Помилка Telegram: {e}")
            return False
    
    async def monitor_markets(self, interval: int = 20):
        """Головна функція моніторингу"""
        # Чекаємо поки користувач напише боту
        while not self.chat_id:
            logger.info("⏳ Чекаємо повідомлення від користувача...")
            await asyncio.sleep(2)
        
        logger.info("🚀 Запуск моніторингу китів на Polymarket...")
        logger.info(f"📊 Фільтри: Мін. оборот=${self.min_turnover}, Мін. сума=${self.min_trader_amount}")
        logger.info(f"⏱️  Інтервал перевірки: {interval}сек, Макс сповіщень/год: {self.max_notifications_per_hour}")
        
        check_count = 0
        
        while True:
            try:
                markets = await self.get_polymarket_markets()
                
                if not markets:
                    logger.warning("⚠️  Ринки не знайдені")
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
                                logger.info(f"✅ [{detected_whales}] Китова угода: ${trade_value:,.0f}")
                        
                        if len(self.checked_bets) > 10000:
                            self.checked_bets.clear()
                
                check_count += 1
                logger.info(f"✓ Цикл #{check_count}: {len(markets)} ринків, {detected_whales} китів виявлено")
                
                await asyncio.sleep(interval)
            
            except Exception as e:
                logger.error(f"❌ Помилка в циклі: {e}")
                await asyncio.sleep(interval)
    
    async def start(self):
        """Запускає бота"""
        try:
            # Отримуємо інформацію про бота
            bot_info = await self.bot.get_me()
            logger.info(f"✅ Бот активований: @{bot_info.username}")
            
            # Спробуємо завантажити збережений chat_id
            saved_chat_id = self._load_user()
            if saved_chat_id:
                self.chat_id = saved_chat_id
                logger.info(f"📨 Знайдено збережений chat_id: {self.chat_id}")
            
            # Запускаємо додаток для обробки повідомлень
            application = Application.builder().token(self.telegram_token).build()
            
            # Додаємо обробники
            application.add_handler(CommandHandler("start", self.handle_start_command))
            application.add_handler(CommandHandler("status", self.handle_status_command))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_user_message))
            
            # Запускаємо бота в фоні
            async with application:
                await application.initialize()
                await application.start()
                
                # Запускаємо моніторинг
                await self.monitor_markets()
        
        except Exception as e:
            logger.error(f"❌ Критична помилка: {e}")


async def main():
    # ════════════════════════════════════════════════════════════
    # 🔧 НАЛАШТУВАННЯ БОТА
    # ════════════════════════════════════════════════════════════
    
    # 🔑 API Telegram Бота
    TELEGRAM_TOKEN = "8645033199:AAHymz1RJUvbT8duZWkz1qr8-2nR0DsMsn4"
    
    # 🎯 Фільтри для китів
    MIN_TURNOVER = 10000          # 💰 Мінімальна сума угоди ($)
    MIN_TRADER_AMOUNT = 5000       # 👤 Мінімальна сума від трейдера ($)
    MAX_NOTIFICATIONS_PER_HOUR = 50  # ⏱️  Максимум алертів на годину
    
    # ════════════════════════════════════════════════════════════
    
    if TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("❌ ПОМИЛКА: Встав свій Telegram токен!")
        return
    
    # Запускаємо бота
    bot = PolymarketWhaleBotPrivate(TELEGRAM_TOKEN)
    
    # Застосовуємо налаштування
    bot.min_turnover = MIN_TURNOVER
    bot.min_trader_amount = MIN_TRADER_AMOUNT
    bot.max_notifications_per_hour = MAX_NOTIFICATIONS_PER_HOUR
    
    logger.info("=" * 70)
    logger.info("🐋 POLYMARKET WHALE BOT - ПРИВАТНИЙ ЧАТ")
    logger.info("=" * 70)
    logger.info(f"✅ Бот запущено!")
    logger.info(f"📱 Напиши боту @{bot.telegram_token.split(':')[0]} в Telegram")
    logger.info(f"📬 Або видай команду /start")
    logger.info(f"💬 Чекаємо твого першого повідомлення...")
    logger.info("=" * 70)
    
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Бот зупинений користувачем")
