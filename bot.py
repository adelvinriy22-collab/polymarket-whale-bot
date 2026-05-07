#!/usr/bin/env python3
"""
🐋 PolyMrktCopy - Professional Whale Tracking Bot
З інтерактивними фільтрами
"""

import logging
import asyncio
import aiohttp
import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError
import json

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('whale_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

WELCOME_MESSAGE = """✨ <b>Ласкаво просимо до PolyMrktCopy!</b> ✨

🐋 Вы отримали доступ до професійної системи моніторингу.

<b>Доступні команди:</b>

🔧 <b>/filter</b> - Показати поточні фільтри
📊 <b>/set_min MIN_SUM</b> - Мінімум угоди (в $)
   Приклад: /set_min 50000

👤 <b>/set_trader MIN_SUM</b> - Мінімум від трейдера (в $)
   Приклад: /set_trader 10000

📈 <b>/set_size MIN_KOL</b> - Мінімальна кількість
   Приклад: /set_size 5

📋 <b>/status</b> - Статус бота

🚀 Моніторинг розпочато!
"""

class PolyMrktCopyBot:
    def __init__(self, token: str, chat_id: str = None):
        self.token = token
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self.checked_bets = set()
        self.running = True
        
        # Фільтри за замовчуванням
        self.min_turnover = 10000
        self.min_trader_amount = 5000
        self.min_order_size = 1
        self.max_notifications_per_hour = 50
        self.notifications_count = 0
        self.last_hour = datetime.now()
        
        # Offset для getUpdates
        self.update_offset = 0
        
        self._init_database()
        logger.info("✅ PolyMrktCopy ініціалізована")
    
    def _init_database(self):
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
                    min_turnover INTEGER DEFAULT 10000,
                    min_trader INTEGER DEFAULT 5000,
                    min_order INTEGER DEFAULT 1,
                    access_granted BOOLEAN DEFAULT 1,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Помилка БД: {e}")
    
    def _load_filters(self):
        """Завантажує фільтри користувача"""
        if not self.chat_id:
            return
        
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            cursor.execute('SELECT min_turnover, min_trader, min_order FROM bot_users WHERE chat_id = ?', (self.chat_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                self.min_turnover = result[0]
                self.min_trader_amount = result[1]
                self.min_order_size = result[2]
                logger.info(f"📊 Фільтри: ${self.min_turnover}, ${self.min_trader_amount}, {self.min_order_size}")
        except Exception as e:
            logger.error(f"❌ Помилка завантаження: {e}")
    
    def _save_filters(self):
        """Зберігає фільтри користувача"""
        if not self.chat_id:
            return
        
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO bot_users 
                (chat_id, min_turnover, min_trader, min_order, access_granted)
                VALUES (?, ?, ?, ?, 1)
            ''', (self.chat_id, self.min_turnover, self.min_trader_amount, self.min_order_size))
            conn.commit()
            conn.close()
            logger.info(f"✅ Фільтри збережені")
        except Exception as e:
            logger.error(f"❌ Помилка: {e}")
    
    async def send_message(self, text: str, chat_id: str = None) -> bool:
        target_id = chat_id or self.chat_id
        if not target_id:
            return False
        
        try:
            await self.bot.send_message(chat_id=target_id, text=text, parse_mode='HTML')
            return True
        except TelegramError as e:
            logger.error(f"❌ Помилка: {e}")
            return False
    
    async def handle_command(self, message: str):
        """Обробляє команди від користувача"""
        text = message.strip()
        
        if text == '/filter':
            filter_msg = f"""
📊 <b>Поточні фільтри:</b>

💰 Мінімум угоди: <code>${self.min_turnover:,}</code>
👤 Мінімум від трейдера: <code>${self.min_trader_amount:,}</code>
📈 Мінімальна кількість: <code>{self.min_order_size}</code>

⏱️ Макс сповіщень/год: <code>{self.max_notifications_per_hour}</code>

<b>Команди для зміни:</b>
/set_min 50000
/set_trader 10000
/set_size 5
"""
            await self.send_message(filter_msg)
        
        elif text.startswith('/set_min '):
            try:
                new_val = int(text.split()[1])
                self.min_turnover = new_val
                self._save_filters()
                await self.send_message(f"✅ Мінімум угоди змінено на <code>${new_val:,}</code>")
                logger.info(f"📊 Мінімум угоди: ${new_val}")
            except:
                await self.send_message("❌ Помилка! Використовуй: /set_min 50000")
        
        elif text.startswith('/set_trader '):
            try:
                new_val = int(text.split()[1])
                self.min_trader_amount = new_val
                self._save_filters()
                await self.send_message(f"✅ Мінімум від трейдера змінено на <code>${new_val:,}</code>")
                logger.info(f"👤 Мінімум трейдера: ${new_val}")
            except:
                await self.send_message("❌ Помилка! Використовуй: /set_trader 10000")
        
        elif text.startswith('/set_size '):
            try:
                new_val = int(text.split()[1])
                self.min_order_size = new_val
                self._save_filters()
                await self.send_message(f"✅ Мінімальна кількість змінена на <code>{new_val}</code>")
                logger.info(f"📈 Мінімальна кількість: {new_val}")
            except:
                await self.send_message("❌ Помилка! Використовуй: /set_size 5")
        
        elif text == '/status':
            status = f"""
🟢 <b>БОТ АКТИВНИЙ</b>

⏰ Статус: МОНІТОРИНГ
📊 Сповіщень цю годину: {self.notifications_count}/{self.max_notifications_per_hour}

<b>Фільтри:</b>
💰 ${self.min_turnover:,}
👤 ${self.min_trader_amount:,}
📈 {self.min_order_size}

Напиши /filter для більше інформації
"""
            await self.send_message(status)
        
        elif text == '/help':
            await self.send_message(WELCOME_MESSAGE)
    
    async def check_messages(self):
        """Перевіряє вхідні повідомлення від користувача"""
        try:
            updates = await self.bot.get_updates(offset=self.update_offset, timeout=10)
            
            for update in updates:
                self.update_offset = update.update_id + 1
                
                if update.message and update.message.text:
                    chat_id = str(update.message.chat_id)
                    
                    # Реєструємо користувача
                    if chat_id == self.chat_id:
                        try:
                            conn = sqlite3.connect("whale_bets.db")
                            cursor = conn.cursor()
                            cursor.execute('INSERT OR REPLACE INTO bot_users (chat_id, access_granted) VALUES (?, 1)', (chat_id,))
                            conn.commit()
                            conn.close()
                        except:
                            pass
                        
                        # Обробляємо команду
                        await self.handle_command(update.message.text)
        
        except Exception as e:
            logger.error(f"❌ Помилка перевірки: {e}")
    
    def check_rate_limit(self) -> bool:
        now = datetime.now()
        if now.hour != self.last_hour.hour:
            self.notifications_count = 0
            self.last_hour = now
        
        if self.notifications_count >= self.max_notifications_per_hour:
            return False
        
        self.notifications_count += 1
        return True
    
    async def get_polymarket_markets(self):
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://clob.polymarket.com/markets"
                params = {"limit": 100, "order": "volume24h"}
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if isinstance(data, str):
                            data = json.loads(data)
                        if isinstance(data, list):
                            return [m for m in data if 'gamblingIsAllYouNeed' in m.get('tags', [])]
                    return []
        except Exception as e:
            logger.error(f"❌ Помилка ринків: {e}")
            return []
    
    async def get_market_trades(self, market_id: str):
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://clob.polymarket.com/trades"
                params = {"market": market_id, "limit": 100}
                
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if isinstance(data, str):
                            data = json.loads(data)
                        return data if isinstance(data, list) else []
                    return []
        except Exception as e:
            logger.error(f"❌ Помилка угод: {e}")
            return []
    
    def check_whale_filters(self, trade: dict) -> tuple[bool, float]:
        try:
            size = float(trade.get('size', 0))
            price = float(trade.get('price', 0))
            value = size * price
            
            if value < self.min_turnover:
                return (False, value)
            if value < self.min_trader_amount:
                return (False, value)
            if size < self.min_order_size:
                return (False, value)
            if not self.check_rate_limit():
                return (False, value)
            
            return (True, value)
        except:
            return (False, 0)
    
    def format_whale_message(self, market: dict, trade: dict, value: float) -> str:
        try:
            side = trade.get('side', '?').upper()
            emoji = '🟢' if side == 'YES' else ('🔴' if side == 'NO' else '🟡')
            
            question = market.get('question', 'Unknown')[:70]
            price = float(trade.get('price', 0))
            size = float(trade.get('size', 0))
            
            addr = trade.get('trader', '')
            addr_short = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 8 else addr
            
            tx = trade.get('tx_hash', '')
            tx_short = f"{tx[:8]}...{tx[-4:]}" if len(tx) > 10 else 'pending'
            
            try:
                dt = datetime.fromisoformat(trade.get('timestamp', datetime.now().isoformat()))
                time_str = dt.strftime('%H:%M:%S UTC')
            except:
                time_str = datetime.now().strftime('%H:%M:%S UTC')
            
            message = f"""
{emoji} <b>[PAPER] {side}</b> • #{size:.0f}
<i>{question}</i>

💰 <b>Трейдер:</b> ${value:,.0f}
📊 <b>Ціна:</b> ${price:.2f} | <b>Кількість:</b> {size:.3f}

<b>👤 Гаманець:</b> <code>{addr_short}</code>
<b>🔗 TX:</b> <code>{tx_short}</code>

⏰ {time_str}
"""
            return message.strip()
        except:
            return "🐋 Китова угода"
    
    async def save_trade(self, market: dict, trade: dict, value: float):
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            market_id = market.get('id')
            trade_id = f"{market_id}_{trade.get('id', '')}"
            
            cursor.execute('''
                INSERT OR IGNORE INTO whale_trades 
                (id, market_id, market_question, trader_amount, outcome_price, side, trader_address, tx_hash, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (trade_id, market_id, market.get('question', ''), value, 
                  float(trade.get('price', 0)), trade.get('side', ''), 
                  trade.get('trader', ''), trade.get('tx_hash', ''), 
                  datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except:
            pass
    
    async def monitor(self):
        """Моніторинг китів"""
        logger.info("🚀 Запуск PolyMrktCopy...")
        
        await self.send_message(WELCOME_MESSAGE)
        
        self._load_filters()
        
        check_count = 0
        
        while self.running:
            try:
                # Перевіряємо повідомлення від користувача
                await self.check_messages()
                
                # Моніторим китів
                markets = await self.get_polymarket_markets()
                if not markets:
                    await asyncio.sleep(10)
                    continue
                
                whales = 0
                for market in markets:
                    market_id = market.get('id')
                    if not market_id:
                        continue
                    
                    trades = await self.get_market_trades(market_id)
                    for trade in trades:
                        trade_id = f"{market_id}_{trade.get('id', '')}"
                        
                        if trade_id in self.checked_bets:
                            continue
                        
                        is_whale, value = self.check_whale_filters(trade)
                        if is_whale:
                            self.checked_bets.add(trade_id)
                            whales += 1
                            
                            await self.save_trade(market, trade, value)
                            msg = self.format_whale_message(market, trade, value)
                            await self.send_message(msg)
                            logger.info(f"✅ Китова угода: ${value:,.0f}")
                        
                        if len(self.checked_bets) > 5000:
                            self.checked_bets.clear()
                
                check_count += 1
                logger.info(f"✓ Цикл #{check_count}: {len(markets)} ринків, {whales} китів")
                await asyncio.sleep(15)
            
            except Exception as e:
                logger.error(f"❌ Помилка: {e}")
                await asyncio.sleep(15)
    
    async def start(self):
        try:
            info = await self.bot.get_me()
            logger.info(f"✅ PolyMrktCopy: @{info.username}")
            logger.info("=" * 70)
            logger.info("🐋 PolyMrktCopy - Professional Whale Tracking")
            logger.info("=" * 70)
            
            await self.monitor()
        except Exception as e:
            logger.error(f"❌ Помилка: {e}")
            self.running = False


async def main():
    TOKEN = os.getenv('TELEGRAM_TOKEN')
    CHAT_ID = os.getenv('CHAT_ID')
    
    if not TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не встановлений!")
        return
    
    bot = PolyMrktCopyBot(TOKEN, CHAT_ID)
    
    if CHAT_ID:
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO bot_users (chat_id, access_granted) VALUES (?, 1)', (CHAT_ID,))
            conn.commit()
            conn.close()
            logger.info(f"✅ Користувач {CHAT_ID} авторизований")
        except:
            pass
    
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Зупинено")
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")
