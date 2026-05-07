#!/usr/bin/env python3
"""
🐋 Polymarket Whale Bot
Telegram бот для моніторингу великих ставок на Polymarket
"""

import logging
import asyncio
import aiohttp
import os
import sqlite3
import json
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

class PolymarketWhaleBot:
    """Основний клас бота для моніторингу китів на Polymarket"""
    
    def __init__(self, telegram_token: str, chat_id: str):
        """Ініціалізація бота"""
        self.token = telegram_token
        self.bot = Bot(token=telegram_token)
        self.chat_id = chat_id  # ЧИТАЄМО З ЗМІННОЇ
        self.checked_bets = set()
        self.running = True
        
        # Налаштування фільтрів
        self.min_turnover = 10000
        self.min_trader_amount = 5000
        self.max_notifications_per_hour = 50
        self.notifications_count = 0
        self.last_hour = datetime.now()
        
        # Ініціалізуємо БД
        self._init_database()
        
        logger.info("✅ Бот ініціалізований успішно")
    
    def _init_database(self):
        """Створює таблиці в БД"""
        try:
            conn = sqlite3.connect("whale_bets.db")
            cursor = conn.cursor()
            
            # Таблиця для угод
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
            
            # Таблиця для користувачів
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_users (
                    chat_id TEXT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблиця для статистики
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
    
    async def send_telegram_message(self, message: str) -> bool:
        """Надсилає повідомлення в Telegram"""
        if not self.chat_id:
            logger.warning("⚠️ Chat ID не встановлений")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
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
        
        # Скидаємо лічильник на новій годині
        if now.hour != self.last_hour.hour:
            self.notifications_count = 0
            self.last_hour = now
        
        # Перевіряємо ліміт
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
                        # Фільтруємо за тегом
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
            logger.error(f"❌ Помилка отримання угод для {market_id}: {e}")
            return []
    
    def check_whale_filters(self, trade: dict) -> tuple[bool, float]:
        """
        Перевіряє чи угода відповідає критеріям для китового сповіщення
        
        Фільтри:
        - Мінімальний оборот ($10,000)
        - Мінімальна сума від трейдера ($5,000)
        - Rate limiting (не більше 50 на годину)
        """
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
            
            # Фільтр 3: Rate limiting
            if not self.check_rate_limit():
                logger.warning(f"⚠️ Досягнуто ліміт сповіщень на годину ({self.max_notifications_per_hour})")
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
        """Форматує адресу (скорочена версія)"""
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
            logger.error(f"❌ Помилка збереження угоди: {e}")
    
    def format_whale_message(self, market: dict, trade: dict, trade_value: float) -> str:
        """
        Форматує красивого повідомлення про китову угоду
        
        Формат:
        🟢 [PAPER] BUY Yes • #14
        Will Real Betis Balompié win on 2026-05-03?
        📊 Трейдер: $7,413 → n: $10.00 ● $0.610
        👤 GamblingIsAllYouNeed 0x507e_beaa
        16:24:20 UTC • tx 0xd9f2acfc…
        """
        try:
            # Отримуємо дані
            side = trade.get('side', 'unknown').upper()
            side_emoji = self.get_side_emoji(side)
            
            market_question = market.get('question', 'Unknown')[:80]
            price = float(trade.get('price', 0))
            size = float(trade.get('size', 0))
            
            trader_address = trade.get('trader', '')
            short_address = self.format_address(trader_address)
            
            tx_hash = trade.get('tx_hash', '')
            short_tx = tx_hash[:10] + '…' if tx_hash else 'pending'
            
            # Форматуємо час
            timestamp = trade.get('timestamp', datetime.now().isoformat())
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime('%H:%M:%S UTC')
            except:
                time_str = datetime.now().strftime('%H:%M:%S UTC')
            
            bet_type = "PAPER"
            
            # Формуємо повідомлення
            message = f"""
{side_emoji} [{bet_type}] {side} • #{size:.0f}
{market_question}

📊 Трейдер: ${trade_value:,.0f} → n: ${price:.2f} ● ${size:.3f}
👤 GamblingIsAllYouNeed {short_address}

{time_str} • tx {short_tx}
"""
            
            return message.strip()
        
        except Exception as e:
            logger.error(f"❌ Помилка форматування повідомлення: {e}")
            return "🐋 Виявлена китова угода на Polymarket"
    
    async def monitor_markets(self, interval: int = 20):
        """
        Головна функція моніторингу ринків
        Перевіряє ринки кожні 20 секунд і надсилає сповіщення про китів
        """
        logger.info("🚀 Запуск моніторингу китів на Polymarket...")
        logger.info(f"📊 Фільтри: Мін. оборот=${self.min_turnover}, Мін. сума=${self.min_trader_amount}")
        logger.info(f"⏱️  Інтервал перевірки: {interval}сек, Макс сповіщень/год: {self.max_notifications_per_hour}")
        
        # НАДСИЛАЄМО ПЕРШЕ ПОВІДОМЛЕННЯ
        await self.send_telegram_message("✅ Бот активований! Моніторинг розпочато! 🚀🐋")
        
        check_count = 0
        
        while self.running:
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
                        
                        # Пропускаємо вже перевірені угоди
                        if trade_id in self.checked_bets:
                            continue
                        
                        # Перевіряємо фільтри
                        is_whale, trade_value = self.check_whale_filters(trade)
                        
                        if is_whale:
                            self.checked_bets.add(trade_id)
                            detected_whales += 1
                            
                            # Зберігаємо в БД
                            await self.save_whale_trade(market, trade, trade_value)
                            
                            # Форматуємо та надсилаємо повідомлення
                            message = self.format_whale_message(market, trade, trade_value)
                            success = await self.send_telegram_message(message)
                            
                            if success:
                                logger.info(f"✅ [{detected_whales}] Китова угода: ${trade_value:,.0f}")
                        
                        # Очищаємо старі записи щоб не забивати пам'ять
                        if len(self.checked_bets) > 10000:
                            self.checked_bets.clear()
                
                check_count += 1
                logger.info(f"✓ Цикл #{check_count}: {len(markets)} ринків, {detected_whales} китів виявлено")
                
                await asyncio.sleep(interval)
            
            except asyncio.CancelledError:
                logger.info("⛔ Моніторинг скасований")
                break
            except Exception as e:
                logger.error(f"❌ Помилка в циклі моніторингу: {e}")
                await asyncio.sleep(interval)
    
    async def start(self):
        """Запускає бота"""
        try:
            # Перевіряємо з'єднання з Telegram
            bot_info = await self.bot.get_me()
            logger.info(f"✅ Бот активований: @{bot_info.username}")
            
            logger.info("=" * 70)
            logger.info("🐋 POLYMARKET WHALE BOT")
            logger.info("=" * 70)
            logger.info(f"✅ Бот готовий!")
            logger.info(f"📱 Chat ID: {self.chat_id}")
            logger.info(f"🚀 Моніторинг запущено!")
            logger.info("=" * 70)
            
            # Запускаємо моніторинг
            await self.monitor_markets()
        
        except Exception as e:
            logger.error(f"❌ Критична помилка: {e}")
            self.running = False


async def main():
    """Головна функція"""
    
    # ЧИТАЄМО ОБИДВА З ЗМІННИХ СЕРЕДОВИЩА
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    CHAT_ID = os.getenv('CHAT_ID')
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ ПОМИЛКА: TELEGRAM_TOKEN не встановлений!")
        logger.error("Встав TELEGRAM_TOKEN у змінні середовища Railway")
        return
    
    if not CHAT_ID:
        logger.error("❌ ПОМИЛКА: CHAT_ID не встановлений!")
        logger.error("Встав CHAT_ID у змінні середовища Railway")
        return
    
    # ПЕРЕДАЄМО CHAT_ID ПРИ СТВОРЕННІ
    bot = PolymarketWhaleBot(TELEGRAM_TOKEN, CHAT_ID)
    
    # Обробник сигналу для грамотного завершення
    def signal_handler(sig, frame):
        logger.info("⛔ Отримано сигнал завершення")
        bot.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Запускаємо бота
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Бот зупинений користувачем")
    except Exception as e:
        logger.error(f"❌ Непередбачена помилка: {e}")
        sys.exit(1)
