from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Tuple
from cardinal import Cardinal
if TYPE_CHECKING:
    from cardinal import Cardinal
import re
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
import logging
from logging.handlers import RotatingFileHandler
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.types import Message
import os
import json
import time
import random
import asyncio
from pyrogram import Client
from pyrogram.errors.exceptions.bad_request_400 import StargiftUsageLimited
from pyrogram.enums import ChatType
from datetime import datetime, timedelta

logger = logging.getLogger("FPC.auto_gifts")
logger.setLevel(logging.DEBUG)

log_file = os.path.join("storage", "logs", "auto_gifts.log")
os.makedirs(os.path.dirname(log_file), exist_ok=True)

file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

LOGGER_PREFIX = "[AUTOGIFTS]"
SESSION_STATS_PATH = os.path.join("storage", "cache", "session_stats.json")
NAME = "Auto Gifts"
VERSION = "3.0.8"
DESCRIPTION = "Плагин для автоматической выдачи Telegram подарков с поддержкой нескольких сессий"
CREDITS = "@hexlynch"
UUID = "a3d3f3c9-2da0-4f87-b51c-066038520c49"
SETTINGS_PAGE = False

RUNNING = True

config = {}
lot_mapping = {}
waiting_for_lots_upload = set()
auto_refunds = ""

CONFIG_PATH = os.path.join("storage", "cache", "gift_lots.json")
ORDERS_PATH = os.path.join("storage", "cache", "auto_gift_orders.json")
SESSIONS_PATH = "/bot2/sessions"
os.makedirs(os.path.dirname(ORDERS_PATH), exist_ok=True)
os.makedirs(SESSIONS_PATH, exist_ok=True)

EMOJI_SETS = [
    "🎉",  # Праздничный хлопок
    "🎁",  # Подарок
    "🌟",  # Звезда
    "😊",  # Улыбка
    "💖",  # Сердце
    "🎈",  # Воздушный шар
    "🥳",  # Празднующее лицо
    "🚀",  # Ракета
    "🍀",  # Четырёхлистный клевер
    "🌸",  # Цветок сакуры
    "🔥",  # Огонь
    "🍓",  # Клубника
    "🧁",  # Кекс
    "🎶",  # Музыкальные ноты
    "🎯",  # Мишень
    "💎",  # Бриллиант
    "🍿",  # Попкорн
    "🎨",  # Палитра
    "🧸",  # Плюшевый мишка
    "💡",  # Лампочка
    "🥂",  # Бокалы
    "🌈",  # Радуга
    "🍾",  # Шампанское
    "🎂",  # Торт
    "🌼",  # Цветок
]

SESSION_STATS_PATH = os.path.join("storage", "cache", "session_stats.json")

class SessionManager:
    def __init__(self):
        self.sessions = []
        self.current_session_index = 0
        self.load_sessions()
        self.load_session_stats()

    def load_sessions(self):
        session_files = [f for f in os.listdir(SESSIONS_PATH) if f.startswith("stars_") and f.endswith(".session")]
        self.sessions = []
        for idx, session_file in enumerate(session_files):
            session_name = f"stars_{idx+1}"
            self.sessions.append({
                "name": session_name,
                "path": os.path.join(SESSIONS_PATH, session_file),
                "active": True,
                "last_used": datetime.min,  # Инициализация last_used
                "balance": None,
                "gifts_sent": 0,
                "total_cost": 0.0
            })
        logger.info(f"{LOGGER_PREFIX} Loaded {len(self.sessions)} sessions")

    def load_session_stats(self):
        """Load session statistics (gifts sent and total cost)"""
        if os.path.exists(SESSION_STATS_PATH):
            with open(SESSION_STATS_PATH, 'r', encoding='utf-8') as f:
                stats = json.load(f)
            for session in self.sessions:
                session_name = session["name"]
                if session_name in stats:
                    session["gifts_sent"] = stats[session_name].get("gifts_sent", 0)
                    session["total_cost"] = stats[session_name].get("total_cost", 0.0)
            logger.info(f"{LOGGER_PREFIX} Loaded session stats from {SESSION_STATS_PATH}")
        else:
            logger.info(f"{LOGGER_PREFIX} No session stats found, initializing empty stats")
            self.save_session_stats()

    def save_session_stats(self):
        """Save session statistics to file"""
        stats = {}
        for session in self.sessions:
            stats[session["name"]] = {
                "gifts_sent": session["gifts_sent"],
                "total_cost": session["total_cost"]
            }
        os.makedirs(os.path.dirname(SESSION_STATS_PATH), exist_ok=True)
        with open(SESSION_STATS_PATH, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=4)
        logger.info(f"{LOGGER_PREFIX} Saved session stats to {SESSION_STATS_PATH}")

    async def get_active_session(self, order_id=None):
        if not self.sessions:
            logger.error(f"{LOGGER_PREFIX} No sessions available")
            return None
        await self.check_all_sessions(None, None)
        active_sessions = [s for s in self.sessions if s['active'] and s['balance'] > 0]
        
        if not active_sessions:
            logger.error(f"{LOGGER_PREFIX} No active sessions with sufficient balance")
            return None

        logger.info(f"{LOGGER_PREFIX} Available active sessions: {len(active_sessions)}")
        start_index = self.current_session_index % len(active_sessions)
        
        for i in range(len(active_sessions)):
            session_index = (start_index + i) % len(active_sessions)
            session = active_sessions[session_index]
            logger.info(f"{LOGGER_PREFIX} Checking session {session['name']} at index {session_index}")
            
            try:
                async with Client(session["name"], workdir=SESSIONS_PATH) as app:
                    balance = await app.get_stars_balance()
                    session["balance"] = balance
                    logger.info(f"{LOGGER_PREFIX} Session {session['name']} balance: {balance}")
                    
                    if balance > 0:
                        self.current_session_index = (session_index + 1) % len(active_sessions)
                        session["last_used"] = datetime.now()
                        logger.info(f"{LOGGER_PREFIX} Selected session {session['name']} with balance {balance} for order #{order_id}")
                        return session
                    else:
                        logger.warning(f"{LOGGER_PREFIX} Session {session['name']} has zero balance")
                        session["active"] = False
                        await self.notify_low_balance(session)
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Error checking session {session['name']}: {str(e)}")
                session["active"] = False
                await self.notify_low_balance(session)
        
        logger.error(f"{LOGGER_PREFIX} No active sessions with sufficient balance after full cycle")
        return None
    async def notify_low_balance(self, session, bot=None, authorized_users=None):
        """Notify authorized users about low balance"""
        if bot and authorized_users:
            for user_id in authorized_users:
                await bot.send_message(
                    user_id,
                    f"⚠️ Сессия {session['name']} имеет нулевой баланс звёзд! Пожалуйста, пополните баланс.",
                    parse_mode='HTML'
                )
        logger.warning(f"{LOGGER_PREFIX} Session {session['name']} marked as inactive due to low balance")

    async def check_all_sessions(self, bot, authorized_users):
        """Check all sessions and notify about their status"""
        for session in self.sessions:
            async with Client(session["name"], workdir=SESSIONS_PATH) as app:
                try:
                    balance = await app.get_stars_balance()
                    session["balance"] = balance
                    if balance == 0 and session["active"]:
                        session["active"] = False
                        await self.notify_low_balance(session, bot, authorized_users)
                    elif balance > 0 and not session["active"]:
                        session["active"] = True
                        for user_id in authorized_users:
                            await bot.send_message(
                                user_id,
                                f"✅ Сессия {session['name']} восстановлена с балансом {balance} звёзд",
                                parse_mode='HTML'
                            )
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Error checking session {session['name']}: {str(e)}")
                    session["active"] = False
                    await self.notify_low_balance(session, bot, authorized_users)
session_manager = SessionManager()

async def inform():
    for session in session_manager.sessions:
        async with Client(session["name"], workdir=SESSIONS_PATH) as app:
            try:
                me = await app.get_me()
                stars = await app.get_stars_balance()
                session["balance"] = stars
                logger.info(f"{LOGGER_PREFIX} Session {session['name']} initialized: ID={me.id}, Balance={stars}")
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Error initializing session {session['name']}: {str(e)}")
                session["active"] = False

loop = asyncio.new_event_loop()
try:
    loop.run_until_complete(inform())
finally:
    loop.close()

def save_config(cfg: Dict):
    logger.info(f"{LOGGER_PREFIX} Saving configuration (gift_lots.json)...")
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    logger.info(f"{LOGGER_PREFIX} Configuration saved")

def load_config() -> Dict:
    logger.info(f"{LOGGER_PREFIX} Loading configuration (gift_lots.json)...")
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if "auto_refunds" not in cfg:
            cfg["auto_refunds"] = True
        if "active_lots" not in cfg:
            cfg['active_lots'] = True
        save_config(cfg)
        logger.info(f"{LOGGER_PREFIX} Configuration loaded successfully")
        return cfg
    else:
        logger.info(f"{LOGGER_PREFIX} Configuration file not found, creating default")
        default_config = {
            "lot_mapping": {
                "lot_1": {
                    "name": "Тестовый лот",
                    "gift_id": 5170690322832818290,
                    "gift_name": "Кольцо 💍"
                }
            },
            "auto_refunds": True,
            "active_lots": True
        }
        save_config(default_config)
        return default_config

queue: Dict[str, Dict] = {}

def get_authorized_users() -> List[int]:
    path_ = os.path.join("storage", "cache", "tg_authorized_users.json")
    if not os.path.exists(path_):
        return []
    try:
        with open(path_, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [int(k) for k in data.keys()]
    except:
        return []

async def check_username(c: Cardinal, msg_chat_id, username, order_id):
    msg_author_id = c.account.id
    data = queue.get(msg_author_id, {})
    session_name = data.get("session_name")
    session = next((s for s in session_manager.sessions if s["name"] == session_name), None)
    
    if not session or not session["active"]:
        session = await session_manager.get_active_session(order_id)
        if not session:
            logger.error(f"{LOGGER_PREFIX} No active sessions for username check, order #{order_id}")
            c.send_message(msg_chat_id, "❌ Нет доступных сессий для обработки заказа. Свяжитесь с продавцом.")
            return None
        data["session_name"] = session["name"]
    
    async with Client(session["name"], workdir=SESSIONS_PATH) as app:
        try:
            user = await app.get_chat(username)
            if user.type in (ChatType.PRIVATE, ChatType.CHANNEL):
                name = clean_display_name(user.first_name)  # Очищаем имя
                logger.debug(f"{LOGGER_PREFIX} Got name: {name} for order #{order_id}")
                return name
            else:
                logger.debug(f"{LOGGER_PREFIX} Got {user.type} for order #{order_id}")
                c.send_message(msg_chat_id, "🐒 Юзернейм не распознан!\nВспоминаем: должен быть знак @ и ник.\nВот так правильно: @example\nПопробуй ещё раз 👇")
                return None
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error processing username {username} for order #{order_id}: {str(e)}")
            c.send_message(msg_chat_id, "🐒 Юзернейм не распознан!\nВспоминаем: должен быть знак @ и ник.\nВот так правильно: @example\nПопробуй ещё раз 👇")
            return None
        
async def clean_comment(comment: str | None) -> str:
    """Очищает комментарий для отправки через Telegram API."""
    if not comment:
        return f"Ваш подарок! {random.choice(EMOJI_SETS)}"
    comment = re.sub(r'[|[\]<>]', '', comment)
    comment = comment.replace('*', '\\*').replace('_', '\\_').replace('`', '\\`')
    return comment[:200]

async def buy_gifts(c: Cardinal, msg_chat_id, username, gift_id, order_amount, order_id, bot, comment=None, is_anonymous=True):
    gift_price = await get_amount(gift_id)
    if gift_price is None:
        logger.error(f"{LOGGER_PREFIX} Failed to get gift price for gift_id {gift_id}, order #{order_id}")
        await bot.send_message(msg_chat_id, f"❌ Ошибка: не удалось определить стоимость подарка для заказа #{order_id}.", parse_mode='HTML')
        return False

    for attempt in range(len(session_manager.sessions)):
        session = await session_manager.get_active_session(order_id)
        if not session:
            logger.error(f"{LOGGER_PREFIX} No active sessions for sending gifts, order #{order_id}")
            await bot.send_message(msg_chat_id, "❌ Нет активных сессий для отправки подарков. Свяжитесь с продавцом.", parse_mode='HTML')
            for user_id in get_authorized_users():
                await bot.send_message(user_id, f"⚠️ Нет активных сессий для обработки заказа #{order_id}", parse_mode='HTML')
            return False

        logger.debug(f"{LOGGER_PREFIX} Selected session {session['name']} for order #{order_id}, balance: {session['balance']}")

        if session["balance"] < gift_price * order_amount:
            logger.warning(f"{LOGGER_PREFIX} Insufficient balance in session {session['name']} for order #{order_id}. Required: {gift_price * order_amount}, Available: {session['balance']}")
            session["active"] = False
            await session_manager.notify_low_balance(session, bot, get_authorized_users())
            continue

        async with Client(session["name"], workdir=SESSIONS_PATH) as app:
            logger.debug(f"{LOGGER_PREFIX} Starting gift sending for order #{order_id}, username: {username}, gift_id: {gift_id}, session: {session['name']}, comment: {comment}, anonymous: {is_anonymous}")
            gift_text = await clean_comment(comment)

            for gift_num in range(order_amount):
                logger.debug(f"{LOGGER_PREFIX} Attempt {gift_num+1}/{order_amount} for order #{order_id}")
                try:
                    result = await app.send_gift(chat_id=username, gift_id=gift_id, is_private=is_anonymous, text=gift_text)
                    logger.info(f"{LOGGER_PREFIX} Successfully sent gift #{gift_num+1}/{order_amount} for order #{order_id} using session {session['name']}")
                    session["gifts_sent"] += 1
                    session["total_cost"] += gift_price
                    session_manager.save_session_stats()
                    await asyncio.sleep(1)
                except StargiftUsageLimited as e:
                    logger.error(f"{LOGGER_PREFIX} Error: Gift sold out for order #{order_id}. Details: {str(e)}")
                    await bot.send_message(msg_chat_id, "❌ Этот подарок распродан! Напишите #help для связи с продавцом.", parse_mode='HTML')
                    for user_id in get_authorized_users():
                        await bot.send_message(user_id, f"❌ Подарок распродан для заказа #{order_id}: {str(e)}", parse_mode='HTML')
                    return False
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Error sending gift #{gift_num+1} for order #{order_id} using session {session['name']}: {type(e).__name__}: {str(e)}")
                    await bot.send_message(msg_chat_id, f"❌ Произошла ошибка при обработке заказа #{order_id}. Свяжитесь с продавцом.\nПодробности: {str(e)}", parse_mode='HTML')
                    for user_id in get_authorized_users():
                        await bot.send_message(user_id, f"❌ Ошибка при обработке заказа #{order_id} с сессией {session['name']}: {type(e).__name__}: {str(e)}", parse_mode='HTML')
                    session["active"] = False
                    await session_manager.notify_low_balance(session, bot, get_authorized_users())
                    return False

            logger.info(f"{LOGGER_PREFIX} All {order_amount} gifts sent successfully for order #{order_id} using session {session['name']}")
            return True

    logger.error(f"{LOGGER_PREFIX} Exhausted all sessions for order #{order_id}")
    return False

async def get_balance():
    session = await session_manager.get_active_session()
    if not session:
        return 0
    async with Client(session["name"], workdir=SESSIONS_PATH) as app:
        try:
            stars = await app.get_stars_balance()
            session["balance"] = stars
            return stars
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error getting balance for session {session['name']}: {str(e)}")
            session["active"] = False
            return 0

async def get_amount(gift_id):
    session = await session_manager.get_active_session()
    if not session:
        return None
    async with Client(session["name"], workdir=SESSIONS_PATH) as app:
        try:
            gifts = await app.get_available_gifts()
            for gift in gifts:
                if gift.id == gift_id:
                    return gift.price
            return None
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error getting gift amount for gift_id {gift_id}: {str(e)}")
            return None

def get_tg_id_by_description(description: str) -> Tuple[int | None, str | None]:
    for lot_key, lot_data in lot_mapping.items():
        lot_name = lot_data["name"]
        if "ПОДАРОК НА АККАУНТ" not in description or "ПО USERNAME" not in description:
            continue
        key_part = re.search(r'🔮([^\s]+)[^\|]*\|', lot_name)
        if key_part:
            key_part = key_part.group(1)
            if key_part in description:
                gift_id = lot_data["gift_id"]
                gift_name = lot_data["gift_name"]
                logger.debug(f"{LOGGER_PREFIX} Lot found: {lot_name} (key: {key_part}) -> gift_id: {gift_id}, gift_name: {gift_name}")
                return gift_id, gift_name
    logger.warning(f"{LOGGER_PREFIX} Lot not found for description: {description}")
    return None, None

def generate_lots_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    cfg = load_config()
    lot_map = cfg.get("lot_mapping", {})
    items = list(lot_map.items())

    per_page = 10
    start_ = page * per_page
    end_ = start_ + per_page
    chunk = items[start_:end_]

    kb = InlineKeyboardMarkup(row_width=1)
    for lot_key, lot_data in chunk:
        name_ = lot_data["name"]
        gift_id = lot_data["gift_id"]
        gift_name = lot_data["gift_name"]
        btn_text = f"{name_} [ID={gift_id}, Name={gift_name}]"
        cd = f"ed_lot_{lot_key}"
        kb.add(InlineKeyboardButton(btn_text, callback_data=cd))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"pr_page_{page-1}"))
    if end_ < len(items):
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"ne_page_{page+1}"))
    if nav_buttons:
        kb.row(*nav_buttons)

    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="to_setting"))
    return kb

def save_order_info(order_id: int, order_summa: float, lot_name: str, order_profit: float):
    data_ = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "order_id": order_id,
        "summa": order_summa,
        "lot_name": lot_name,
        "profit": order_profit
    }
    if not os.path.exists(ORDERS_PATH):
        with open(ORDERS_PATH, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=4, ensure_ascii=False)

    with open(ORDERS_PATH, 'r', encoding='utf-8') as f:
        orders = json.load(f)
    orders.append(data_)
    with open(ORDERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(orders, f, indent=4, ensure_ascii=False)

def fast_get_lot_fields(cardinal: Cardinal, lot_id: int):
    return cardinal.account.get_lot_fields(lot_id)

def fast_save_lot(cardinal: Cardinal, lot_fields):
    cardinal.account.save_lot(lot_fields)

def force_set_lot_active(cardinal: Cardinal, lot_id: int, make_active: bool) -> bool:
    try:
        lf = fast_get_lot_fields(cardinal, lot_id)
    except Exception as e:
        logger.warning(f"{LOGGER_PREFIX} get_lot_fields(lot_id={lot_id}) error: {e}")
        return False
    time.sleep(0.3)
    lf.active = make_active
    lf.renew_fields()
    try:
        fast_save_lot(cardinal, lf)
    except Exception as e:
        logger.warning(f"{LOGGER_PREFIX} save_lot(lot_id={lot_id}) error: {e}")
        return False
    return True

def get_my_subcategory_lots_fast(account, subcat_id: int):
    return account.get_my_subcategory_lots(subcat_id)
def clean_display_name(name: str) -> str:
    """Очищает имя от специальных символов, которые могут сломать Telegram API."""
    if not name:
        return "не указано"
    name = re.sub(r'[|[\]<>]', '', name)
    name = name.replace('*', '\\*').replace('_', '\\_').replace('`', '\\`')
    return name.strip()[:100] 
def toggle_subcat_status(cardinal: Cardinal, subcat_id: str) -> bool:
    old_st = is_subcat_active(cardinal, subcat_id)
    new_st = not old_st
    try:
        sc_id = int(subcat_id)
    except:
        return new_st

    changed = 0
    try:
        sub_lots = get_my_subcategory_lots_fast(cardinal.account, sc_id)
    except Exception as e:
        logger.warning(f"{LOGGER_PREFIX} get_my_subcategory_lots({subcat_id}) error: {e}")
        return new_st

    for lt in sub_lots:
        if force_set_lot_active(cardinal, lt.id, new_st):
            changed += 1

    logger.info(f"{LOGGER_PREFIX} subcat={subcat_id} => {new_st}, changed={changed}.")
    return new_st

def is_subcat_active(cardinal: Cardinal, subcat_id: str) -> bool:
    try:
        sc_id = int(subcat_id)
    except:
        return False
    try:
        lots = get_my_subcategory_lots_fast(cardinal.account, sc_id)
        if not lots:
            return False
        return any(l.active for l in lots)
    except:
        logger.warning(f"{LOGGER_PREFIX} is_subcat_active({subcat_id}): error => returning False")
        return False

def get_statistics():
    if not os.path.exists(ORDERS_PATH):
        return None
    with open(ORDERS_PATH, 'r', encoding='utf-8') as f:
        orders = json.load(f)
    now = datetime.now()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    day_orders = [o for o in orders if datetime.strptime(o["date"], "%Y-%m-%d %H:%M:%S") >= day_ago]
    week_orders = [o for o in orders if datetime.strptime(o["date"], "%Y-%m-%d %H:%M:%S") >= week_ago]
    month_orders = [o for o in orders if datetime.strptime(o["date"], "%Y-%m-%d %H:%M:%S") >= month_ago]
    all_orders = orders

    day_total = round(sum(o["summa"] for o in day_orders), 2)
    week_total = round(sum(o["summa"] for o in week_orders), 2)
    month_total = round(sum(o["summa"] for o in month_orders), 2)
    all_total = round(sum(o["summa"] for o in all_orders), 2)

    day_profit = round(sum(o.get("profit", 0) for o in day_orders), 2)
    week_profit = round(sum(o.get("profit", 0) for o in week_orders), 2)
    month_profit = round(sum(o.get("profit", 0) for o in month_orders), 2)
    all_profit = round(sum(o.get("profit", 0) for o in all_orders), 2)

    def find_best_service(os_):
        if not os_:
            return "Нет"
        freq = {}
        for _o in os_:
            srv = _o.get("lot_name", "Неизвестно")
            freq[srv] = freq.get(srv, 0) + 1
        return max(freq, key=freq.get, default="Нет")

    return {
        "day_orders": len(day_orders),
        "day_total": day_total,
        "day_profit": day_profit,
        "week_orders": len(week_orders),
        "week_total": week_total,
        "week_profit": week_profit,
        "month_orders": len(month_orders),
        "month_total": month_total,
        "month_profit": month_profit,
        "all_time_orders": len(all_orders),
        "all_time_total": all_total,
        "all_time_profit": all_profit,
        "best_day_service": find_best_service(day_orders),
        "best_week_service": find_best_service(week_orders),
        "best_month_service": find_best_service(month_orders),
        "best_all_time_service": find_best_service(all_orders),
    }

def reindex_lots(cfg: Dict):
    lot_map = cfg.get("lot_mapping", {})
    sorted_lots = sorted(
        lot_map.items(),
        key=lambda x: int(x[0].split('_')[1]) if x[0].startswith('lot_') and x[0].split('_')[1].isdigit() else 0
    )
    new_lot_map = {}
    for idx, (lot_key, lot_data) in enumerate(sorted_lots, start=1):
        new_key = f"lot_{idx}"
        new_lot_map[new_key] = lot_data
    cfg["lot_mapping"] = new_lot_map
    save_config(cfg)
    logger.info(f"{LOGGER_PREFIX} Lots reindexed after deletion")

def init_commands(c: Cardinal):
    global config, lot_mapping
    logger.info(f"{LOGGER_PREFIX} === init_commands() from auto_gifts ===")
    if not c.telegram:
        return
    bot = c.telegram.bot
    global RUNNING
    RUNNING = True
    logger.info(f"{LOGGER_PREFIX} Auto Gifts plugin automatically activated")

    @bot.message_handler(content_types=['document'])
    def handle_document_upload(message: types.Message):
        user_id = message.from_user.id
        logger.info(f"{LOGGER_PREFIX} Received document from {user_id}. Checking waitlist...")
        if user_id not in waiting_for_lots_upload:
            logger.info(f"{LOGGER_PREFIX} User {user_id} not waiting for JSON upload")
            bot.send_message(message.chat.id, "❌ Вы не активировали загрузку JSON. Используйте меню настроек.")
            return
        waiting_for_lots_upload.remove(user_id)
        logger.info(f"{LOGGER_PREFIX} User {user_id} removed from waitlist. Processing file...")
        file_id = message.document.file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        try:
            data = json.loads(downloaded_file.decode('utf-8'))
            if "lot_mapping" not in data:
                bot.send_message(message.chat.id, "❌ Ошибка: в файле нет ключа 'lot_mapping'.")
                logger.error(f"{LOGGER_PREFIX} JSON does not contain 'lot_mapping'")
                return
            save_config(data)
            kb_ = InlineKeyboardMarkup()
            kb_.add(InlineKeyboardButton("🔙 Назад", callback_data="to_setting"))
            bot.send_message(message.chat.id, "✅ Новый gift_lots.json успешно загружен и сохранён!", reply_markup=kb_)
            logger.info(f"{LOGGER_PREFIX} JSON successfully uploaded and saved")
        except json.JSONDecodeError as e:
            bot.send_message(message.chat.id, f"❌ Ошибка: Не удалось считать JSON. Проверьте синтаксис. ({e})")
            logger.error(f"{LOGGER_PREFIX} JSON decode error: {e}")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Произошла ошибка при загрузке файла: {e}")
            logger.error(f"{LOGGER_PREFIX} Unknown error during upload: {e}")

    def start(m: Message):
        global RUNNING
        if RUNNING is False:
            bot.send_message(m.chat.id, "🚀 Auto Gifts активирован!\nТеперь всё будет работать на автомате 🎁")
            RUNNING = True
            return
        bot.send_message(m.chat.id, "⚠️ Auto Gifts уже работает!\nРасслабься и наблюдай за магией ✨")

    def stop(m: Message):
        global RUNNING
        if RUNNING is False:
            bot.send_message(m.chat.id, "🛑 Auto Gifts и так отдыхает.\nНичего останавливать не нужно 😉")
            return
        bot.send_message(m.chat.id, "⛔ Auto Gifts выключен.\nЕсли что — включи обратно, я всегда на готове 🤖")
        RUNNING = False
    cfg = load_config()
    config.update(cfg)
    lot_mapping.clear()
    lot_mapping.update(cfg.get("lot_mapping", {}))

    def edit_lot(call: types.CallbackQuery, lot_key: str):
        cfg = load_config()
        lot_map = cfg.get("lot_mapping", {})
        if lot_key not in lot_map:
            bot.edit_message_text(f"❌ Лот {lot_key} не найден.", call.message.chat.id, call.message.message_id)
            return

        ld = lot_map[lot_key]
        txt = f"""
<b>{lot_key}</b>
Название: <code>{ld['name']}</code>
GIFT ID:  <code>{ld['gift_id']}</code>
GIFT NAME: <code>{ld['gift_name']}</code>
""".strip()

        kb_ = InlineKeyboardMarkup(row_width=1)
        kb_.add(
            InlineKeyboardButton("✏️ Переименовать лот", callback_data=f"changing_lot_{lot_key}"),
            InlineKeyboardButton("🆔 Изменить GIFT ID", callback_data=f"changing_id_{lot_key}"),
            InlineKeyboardButton("🏷 Изменить GIFT NAME", callback_data=f"changing_nam_{lot_key}"),
        )
        kb_.add(InlineKeyboardButton("🗑 Удалить лот", callback_data=f"deletin_one_lot_{lot_key}"))
        kb_.add(InlineKeyboardButton("🔙 Назад к списку", callback_data="return_t_lot"))
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=kb_)

    def process_lot_change(message: types.Message, lot_key: str):
        new_name = message.text.strip()
        cfg = load_config()
        lot_map = cfg.get("lot_mapping", {})
        if lot_key not in lot_map:
            bot.send_message(message.chat.id, f"❌ Лот <b>{lot_key}</b> не найден. Проверь ещё раз!", parse_mode="HTML")
            return

        lot_map[lot_key]["name"] = new_name
        cfg["lot_mapping"] = lot_map
        save_config(cfg)
        kb_ = InlineKeyboardMarkup()
        kb_.add(InlineKeyboardButton("🔙 Вернуться к лотам", callback_data="return_t_lot"))

        bot.send_message(
            message.chat.id,
            f"✅ Название лота <b>{lot_key}</b> успешно обновлено на <b>{new_name}</b> 🏷",
            parse_mode="HTML",
            reply_markup=kb_
        )

    def process_new_lot(message: types.Message):
        try:
            lot_id = int(message.text.strip())
        except ValueError:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data="to_setting"))
            bot.send_message(
                message.chat.id,
                "🚫 Oшибочка! ID лота должен быть числом 🔢\nПопробуй ещё раз 👇",
                reply_markup=kb
            )
            return

        try:
            lot_fields = c.account.get_lot_fields(lot_id)
            fields = lot_fields.fields
            name = fields.get("fields[summary][ru]", "Без названия")
        except Exception as e:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data="to_setting"))
            bot.send_message(
                message.chat.id,
                f"❌ Упс! Не удалось получить данные лота 😔\nОшибка: <code>{e}</code>",
                parse_mode="HTML",
                reply_markup=kb
            )
            return

        cfg = load_config()
        lot_map = cfg.get("lot_mapping", {})

        new_lot_key = f"lot_{len(lot_map) + 1}"

        lot_map[new_lot_key] = {
            "name": name,
            "gift_id": 1,
            "gift_name": ""
        }

        cfg["lot_mapping"] = lot_map
        save_config(cfg)

        def process_new_lot2(message: types.Message):
            try:
                gift_id = int(message.text.strip())
            except ValueError:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("🔙 Назад", callback_data="to_setting"))
                bot.send_message(
                    message.chat.id,
                    "🚫 GIFT ID должен быть числом! 🔢\nПопробуй ещё раз — всё получится 💪",
                    reply_markup=kb
                )
                return

            lot_map[new_lot_key]["gift_id"] = gift_id
            cfg["lot_mapping"] = lot_map
            save_config(cfg)

        msg = bot.send_message(
            message.chat.id,
            "📦 Введи GIFT ID для добавления 🎁\n(только цифры, пожалуйста!)"
        )
        bot.register_next_step_handler(msg, process_new_lot2)

        while True:
            cfg = load_config()
            lot_map = cfg.get("lot_mapping", {})
            if lot_map[new_lot_key]['gift_id'] == 1:
                time.sleep(2)
            else:
                break

        def process_new_lot3(message: types.Message):
            gift_name = message.text.strip()
            lot_map[new_lot_key]["gift_name"] = gift_name
            cfg["lot_mapping"] = lot_map
            save_config(cfg)

        msg = bot.send_message(
            message.chat.id,
            "🏷 Введи GIFT Name для добавления 🎁\nКак назовём этот подарок?"
        )
        bot.register_next_step_handler(msg, process_new_lot3)

        while True:
            cfg = load_config()
            lot_map = cfg.get("lot_mapping", {})
            if lot_map[new_lot_key]['gift_name'] == "":
                time.sleep(2)
            else:
                break

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⚙️ К настройкам", callback_data="to_setting"))
        bot.send_message(
            message.chat.id,
            f"✅ Лот <b>{new_lot_key}</b> успешно добавлен! 🎉\nНазвание: <b>{name}</b>",
            parse_mode="HTML",
            reply_markup=kb
        )

    def process_id_change(message: types.Message, lot_key: str):
        try:
            new_id = int(message.text.strip())
        except ValueError:
            bot.send_message(
                message.chat.id,
                "🚫 GIFT ID должен быть числом! 🔢\nПопробуй ещё раз — у тебя получится 💪"
            )
            return

        cfg = load_config()
        lot_map = cfg.get("lot_mapping", {})
        if lot_key not in lot_map:
            bot.send_message(
                message.chat.id,
                f"❌ Лот <b>{lot_key}</b> не найден. Проверь ключ и попробуй снова.",
                parse_mode="HTML"
            )
            return

        lot_map[lot_key]["gift_id"] = new_id
        cfg["lot_mapping"] = lot_map
        save_config(cfg)

        kb_ = InlineKeyboardMarkup()
        kb_.add(InlineKeyboardButton("🔙 К лотам", callback_data="return_t_lot"))

        bot.send_message(
            message.chat.id,
            f"✅ GIFT ID для лота <b>{lot_key}</b> успешно обновлён на <b>{new_id}</b> 🎯",
            parse_mode="HTML",
            reply_markup=kb_
        )

    def process_name_change(message: types.Message, lot_key: str):
        new_name = message.text.strip()
        cfg = load_config()
        lot_map = cfg.get("lot_mapping", {})

        if lot_key not in lot_map:
            bot.send_message(
                message.chat.id,
                f"❌ Лот <b>{lot_key}</b> не найден. Проверь ещё раз 👀",
                parse_mode="HTML"
            )
            return

        lot_map[lot_key]["gift_name"] = new_name
        cfg["lot_mapping"] = lot_map
        save_config(cfg)

        kb_ = InlineKeyboardMarkup()
        kb_.add(InlineKeyboardButton("🔙 К лотам", callback_data="return_t_lot"))

        bot.send_message(
            message.chat.id,
            f"✅ Название подарка для лота <b>{lot_key}</b> обновлено на <b>{new_name}</b> ✨",
            parse_mode="HTML",
            reply_markup=kb_
        )

    def delete_one_lot(call: types.CallbackQuery, lot_key: str):
        cfg = load_config()
        lot_map = cfg.get("lot_mapping", {})

        if lot_key in lot_map:
            del lot_map[lot_key]
            cfg["lot_mapping"] = lot_map
            reindex_lots(cfg)

            bot.edit_message_text(
                f"🗑 Лот <b>{lot_key}</b> успешно удалён.\n🔄 Все лоты переиндексированы — порядок наведен! ✅",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=generate_lots_keyboard(0)
            )
        else:
            bot.edit_message_text(
                f"❌ Лот <b>{lot_key}</b> не найден. Возможно, он уже был удалён 🤷‍♂️",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )

    def auto_gifts_settings(message: types.Message):
        cfg = load_config()
        lmap = cfg.get("lot_mapping", {})
        auto_refunds = cfg.get("auto_refunds", True)
        active_lots = cfg.get("active_lots", True)

        loop = asyncio.new_event_loop()
        try:
            stars = loop.run_until_complete(get_balance())
        finally:
            loop.close()

        txt = f"""
    <b>⚙️ Auto Gifts v{VERSION} — панель управления</b>
👨‍💻 Разработчик: {CREDITS}

📦 <b>Всего лотов:</b> {len(lmap)}
🌟 <b>Баланс звёзд (активная сессия):</b> {stars}
📝 <b>Описание:</b> {DESCRIPTION}
📡 <b>Активных сессий:</b> {sum(1 for s in session_manager.sessions if s['active'])}/{len(session_manager.sessions)}
    """.strip()

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🛠️ Управление лотами", callback_data="lot_se"),
            InlineKeyboardButton("📥 Загрузить лоты", callback_data="upload_lots"),
            InlineKeyboardButton(
                f"{'🟢' if auto_refunds else '🔴'} Автовозвраты", callback_data="auto_refund"
            ),
            InlineKeyboardButton(
                f"{'🟢' if active_lots else '🔴'} Лоты активны", callback_data="active_lot"
            ),
            InlineKeyboardButton("➕ Новый лот", callback_data="add_lot"),
            InlineKeyboardButton("📊 Статистика", callback_data="show_stat"),
            InlineKeyboardButton("📡 Статус сессий", callback_data="show_sessions")  # New button
        )

        bot.send_message(message.chat.id, txt, parse_mode='HTML', reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data == "show_sessions")
    def show_sessions(call: types.CallbackQuery):
        loop = asyncio.new_event_loop()
        try:
            # Update balances for all sessions
            for session in session_manager.sessions:
                async def update_balance(s):
                    async with Client(s["name"], workdir=SESSIONS_PATH) as app:
                        try:
                            balance = await app.get_stars_balance()
                            s["balance"] = balance
                            if balance == 0 and s["active"]:
                                s["active"] = False
                                await session_manager.notify_low_balance(s, bot, get_authorized_users())
                            elif balance > 0 and not s["active"]:
                                s["active"] = True
                        except Exception as e:
                            logger.error(f"{LOGGER_PREFIX} Error updating balance for session {s['name']}: {str(e)}")
                            s["active"] = False
                loop.run_until_complete(update_balance(session))
            active_session = loop.run_until_complete(session_manager.get_active_session())
            active_session_name = active_session["name"] if active_session else "Нет активной сессии"

            # Format the session status message
            text = "<b>📡 Статус сессий</b>\n\n"
            for session in session_manager.sessions:
                status = "🟢 Активна" if session["active"] else "🔴 Неактивна"
                is_current = " (Текущая)" if session["name"] == active_session_name else ""
                balance = session.get("balance", 0)
                gifts_sent = session.get("gifts_sent", 0)
                total_cost = session.get("total_cost", 0.0)
                text += (
                    f"📌 <b>{session['name']}{is_current}</b>\n"
                    f"   {status}\n"
                    f"   🌟 Баланс: {balance} звёзд\n"
                    f"   🎁 Подарков отправлено: {gifts_sent}\n"
                    f"   💸 Общая стоимость: {total_cost} звёзд\n\n"
                )

            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🔙 Вернуться в настройки", callback_data="to_setting"))
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode='HTML',
                reply_markup=kb
            )
        finally:
            loop.close()

    @bot.callback_query_handler(func=lambda call: call.data == "add_lot")
    def add_new_lot(call: types.CallbackQuery):
        bot.delete_message(call.message.chat.id, call.message.message_id)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Вернуться в настройки", callback_data="to_setting"))

        msg = bot.send_message(
            call.message.chat.id,
            "📦 Давай добавим новый лот!\n🔢 Введи <b>ID лота</b>, который хочешь добавить:",
            parse_mode="HTML",
            reply_markup=kb
        )

        bot.register_next_step_handler(msg, process_new_lot)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("ed_lot_"))
    def edit_lot_callback(call: types.CallbackQuery):
        lot_key = call.data.split("_", 2)[2]
        edit_lot(call, lot_key)

    @bot.callback_query_handler(func=lambda call: call.data == "return_t_lot")
    def return_to_lots(call: types.CallbackQuery):
        bot.edit_message_text(
            "📦 Выбери лот, с которым хочешь поработать 👇",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=generate_lots_keyboard(0)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("deletin_one_lot_"))
    def delete_one_lot_callback(call: types.CallbackQuery):
        lot_key = call.data.split("_", 3)[3]
        delete_one_lot(call, lot_key)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("changing_lot_"))
    def change_name(call: types.CallbackQuery):
        lot_key = call.data.split("_", 2)[2]
        msg_ = bot.edit_message_text(
            f"✏️ Введи новое название для лота <b>{lot_key}</b>:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML"
        )

        bot.register_next_step_handler(msg_, process_lot_change, lot_key)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("changing_id_"))
    def change_id(call: types.CallbackQuery):
        lot_key = call.data.split("_", 2)[2]
        msg_ = bot.edit_message_text(
            f"🔢 Введи новый <b>GIFT ID</b> для лота <b>{lot_key}</b>:\n(только число, без лишнего ✂️)",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML"
        )

        bot.register_next_step_handler(msg_, process_id_change, lot_key)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("changing_nam_"))
    def change_id(call: types.CallbackQuery):
        lot_key = call.data.split("_", 2)[2]
        msg_ = bot.edit_message_text(
            f"🏷 Придумай новое <b>GIFT NAME</b> для лота <b>{lot_key}</b>:\nКак назовём этот подарочек? 🎁",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML"
        )

        bot.register_next_step_handler(msg_, process_name_change, lot_key)

    @bot.callback_query_handler(func=lambda call: call.data == "active_lot")
    def lot_active(call: types.CallbackQuery):
        state = toggle_subcat_status(c, 3064)
        cfg = load_config()
        if state is False:
            stat = "деактивированы"
            cfg['active_lots'] = False
        else:
            stat = "активированы"
            cfg['active_lots'] = True
        save_config(cfg)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Вернуться в настройки", callback_data="to_setting"))
        bot.edit_message_text(
            f"✅ Лоты успешно <b>{stat}</b>! 🎉",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "lot_se")
    def lot_set(call: types.CallbackQuery):
        bot.edit_message_text("📂 Выбери лот:", call.message.chat.id, call.message.message_id, reply_markup=generate_lots_keyboard(0))

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pr_page_") or call.data.startswith("ne_page_"))
    def page_navigation(call: types.CallbackQuery):
        try:
            page_ = int(call.data.split("_")[-1])
        except ValueError:
            page_ = 0
        bot.edit_message_text("📂 Выбери лот:", call.message.chat.id, call.message.message_id, reply_markup=generate_lots_keyboard(page_))

    @bot.callback_query_handler(func=lambda call: call.data == "to_setting")
    def to_settings(call: types.CallbackQuery):
        cfg = load_config()
        lmap = cfg.get("lot_mapping", {})
        auto_refunds = cfg.get("auto_refunds", True)
        active_lots = cfg.get("active_lots", True)

        loop = asyncio.new_event_loop()
        try:
            stars = loop.run_until_complete(get_balance())
        finally:
            loop.close()

        txt = f"""
    <b>⚙️ Панель управления Auto Gifts v{VERSION}</b>
👨‍💻 <b>Разработчик:</b> {CREDITS}

📦 <b>Лотов в системе:</b> {len(lmap)}
🌟 <b>Баланс звёзд (активная сессия):</b> {stars}
📝 <b>Описание:</b> {DESCRIPTION}
📡 <b>Активных сессий:</b> {sum(1 for s in session_manager.sessions if s['active'])}/{len(session_manager.sessions)}
    """.strip()

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🛠️ Управление лотами", callback_data="lot_se"),
            InlineKeyboardButton("📥 Загрузить лоты", callback_data="upload_lots"),
            InlineKeyboardButton(
                f"{'🟢' if auto_refunds else '🔴'} Автовозвраты", callback_data="auto_refund"
            ),
            InlineKeyboardButton(
                f"{'🟢' if active_lots else '🔴'} Лоты активны", callback_data="active_lot"
            ),
            InlineKeyboardButton("➕ Добавить лот", callback_data="add_lot"),
            InlineKeyboardButton("📊 Статистика", callback_data="show_stat"),
            InlineKeyboardButton("📡 Статус сессий", callback_data="show_sessions")  # New button
        )

        bot.edit_message_text(
            txt,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "show_stat")
    def show_orders(call: types.CallbackQuery):
        stats = get_statistics()
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Вернуться в настройки", callback_data="to_setting"))

        if not stats:
            bot.edit_message_text(
                "📭 Статистика пуста!\nПока нет данных по заказам.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=kb
            )
            return

        text = f"""
    <b>📊 Статистика заказов</b>

    📅 <b>За 24 часа:</b>
    • 🔥 Заказов: <b>{stats['day_orders']}</b>
    • 💸 Сумма: {stats['day_total']} ₽
    • 💰 Прибыль: <b>{stats['day_profit']} ₽</b>
    • 🌟 Топ товар: <code>{stats['best_day_service']}</code>

    📆 <b>За неделю:</b>
    • 🔥 Заказов: <b>{stats['week_orders']}</b>
    • 💸 Сумма: {stats['week_total']} ₽
    • 💰 Прибыль: <b>{stats['week_profit']} ₽</b>
    • 🌟 Топ товар: <code>{stats['best_week_service']}</code>

    🗓 <b>За месяц:</b>
    • 🔥 Заказов: <b>{stats['month_orders']}</b>
    • 💸 Сумма: {stats['month_total']} ₽
    • 💰 Прибыль: <b>{stats['month_profit']} ₽</b>
    • 🌟 Топ товар: <code>{stats['best_month_service']}</code>

    📦 <b>За всё время:</b>
    • 🔥 Заказов: <b>{stats['all_time_orders']}</b>
    • 💸 Сумма: {stats['all_time_total']} ₽
    • 💰 Прибыль: <b>{stats['all_time_profit']} ₽</b>
    • 🌟 Топ товар: <code>{stats['best_all_time_service']}</code>
        """.strip()

        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "upload_lots")
    def upload_lots_json(call: types.CallbackQuery):
        user_id = call.from_user.id
        waiting_for_lots_upload.add(user_id)
        logger.info(f"{LOGGER_PREFIX} Added user {user_id} to waiting_for_lots_upload: {waiting_for_lots_upload}")

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Вернуться в настройки", callback_data="to_setting"))

        bot.edit_message_text(
            "📤 Пришли файл с лотами в формате <b>JSON</b> 🧾\nНазвание файла может быть любым.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "auto_refund")
    def auto_refund(call: types.CallbackQuery):
        cfg = load_config()
        if cfg['auto_refunds'] is True:
            cfg['auto_refunds'] = False
            auto_refunds = "выключены"
        else:
            cfg['auto_refunds'] = True
            auto_refunds = "включены"
        save_config(cfg)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="to_setting"))
        bot.edit_message_text(f"Автовозвраты успешно {auto_refunds}", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)

    c.telegram.msg_handler(start, commands=["start_gifts"])
    c.telegram.msg_handler(stop, commands=["stop_gifts"])
    c.telegram.msg_handler(auto_gifts_settings, commands=["auto_gifts_settings"])
    c.add_telegram_commands(UUID, [
        ("start_gifts", "🚀 Старт автопродажи", True),
        ("stop_gifts", "🛑 Стоп автопродажи", True),
        ("auto_gifts_settings", "⚙️ Настройки автораздачи — управляем лотами и режимами", True),
    ])

def message_hook(c: Cardinal, e: NewMessageEvent):
    global queue
    if not RUNNING:
        logger.debug(f"{LOGGER_PREFIX} Plugin not running, ignoring message from {e.message.author}")
        return
    tg = c.telegram
    bot = tg.bot
    my_id = c.account.id

    if e.message.author_id == my_id:
        logger.debug(f"{LOGGER_PREFIX} Ignoring message from self (ID: {my_id})")
        return

    msg_text = e.message.text.strip()
    msg_author_id = e.message.author_id
    msg_chat_id = e.message.chat_id

    logger.debug(f"{LOGGER_PREFIX} Received message from {e.message.author} (ID: {msg_author_id}): {msg_text}")

    if msg_author_id not in queue:
        logger.debug(f"{LOGGER_PREFIX} User {msg_author_id} not found in queue")
        return

    data = queue.get(msg_author_id)
    if not data:
        logger.warning(f"{LOGGER_PREFIX} No data found for user {msg_author_id} in queue")
        return

    if data["step"] == "await_username":
        logger.debug(f"{LOGGER_PREFIX} Processing username for order #{data['order_id']}")
        username_match = re.match(r'^@(\w+)$', msg_text)
        if not username_match:
            logger.warning(f"{LOGGER_PREFIX} Invalid username format: {msg_text} for order #{data['order_id']}")
            c.send_message(
                msg_chat_id,
                "🐒 Юзернейм не распознан!\nДолжен быть в формате @username.\nПопробуй ещё раз 👇"
            )
            return
        username = username_match.group(1)
        order_id = data['order_id']
        logger.debug(f"{LOGGER_PREFIX} Username recognized: {username} for order #{order_id}")

        try:
            session = asyncio.run(session_manager.get_active_session(order_id))
            if not session:
                logger.error(f"{LOGGER_PREFIX} No active sessions for username check, order #{order_id}")
                c.send_message(
                    msg_chat_id,
                    "❌ Нет доступных сессий для обработки заказа. Свяжитесь с продавцом."
                )
                return
            data["session_name"] = session["name"]
            name = asyncio.run(check_username(c, msg_chat_id, username, order_id))
            if name is None:
                logger.debug(f"{LOGGER_PREFIX} Failed to get username for {username}, order #{data['order_id']}")
                return
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error checking username {username} for order #{order_id}: {type(e).__name__}: {str(e)}")
            c.send_message(
                msg_chat_id,
                f"❌ Ошибка при проверке юзернейма для заказа #{order_id}. Свяжитесь с продавцом.\nПодробности: {str(e)}"
            )
            for user_id in get_authorized_users():
                bot.send_message(
                    user_id,
                    f"❌ Ошибка при проверке юзернейма для заказа #{order_id}: {type(e).__name__}: {str(e)}",
                    parse_mode='HTML'
                )
            return

        order_amount = data["order_amount"]
        amount = data["amount"]
        gift_name = data['gift_name']
        name_display = name if name else "не указано"
        order_text = (
            f"🔎 Проверим заказ перед отправкой:\n\n"
            f"👤 Username: @{username}\n"
            f"🏷️ Отображаемое имя: {name_display}\n"
            f"🎉 Подарков: {order_amount} шт. по {amount} ⭐️ каждый\n"
            f"🎁 Тип подарка: {gift_name}\n"
            f"💬 Комментарии: *комментарии нет, подарок отправляется анонимно*\n\n"
            f"✅ Всё верно? ➡️ Отправьте +\n"
            f"✏️ Нужно изменить данные? Отправь -\n"
            f"💬 Если хотите отправить подарок с комментарием (до 200 символов) и не анонимно, напишите его."
        )
        c.send_message(msg_chat_id, order_text)
        data['name'] = name
        data['username'] = username
        data["step"] = "await_confirm"
        logger.info(f"{LOGGER_PREFIX} Username processed: {username}, moving to confirmation for order #{order_id}")
        return

    elif data["step"] == "await_confirm":
        logger.debug(f"{LOGGER_PREFIX} Processing confirmation for order #{data['order_id']}: {msg_text}")
        order_id = data["order_id"]
        order_amount = data["order_amount"]
        amount = data["amount"]
        username = data['username']
        name = data['name']
        order_time = data['order_time']
        gift_id = data['gift_id']
        gift_name = data['gift_name']
        order_price = data['order_price']
        order_profit = data['order_profit']
        session_name = data.get("session_name")

        if msg_text == "-":
            logger.debug(f"{LOGGER_PREFIX} User declined order #{order_id}, returning to username input")
            c.send_message(
                msg_chat_id,
                "📍 Отправьте ещё раз ваш @username"
            )
            data["step"] = "await_username"
            data['comment'] = None
            data['is_anonymous'] = True
            return
        elif msg_text == "+":
            logger.debug(f"{LOGGER_PREFIX} User confirmed order #{order_id}, proceeding to send gifts")
        else:
            if len(msg_text) > 200:
                c.send_message(
                    msg_chat_id,
                    "❌ Комментарий слишком длинный (максимум 200 символов). Попробуйте снова или отправьте '+' для отправки."
                )
                logger.warning(f"{LOGGER_PREFIX} Comment too long for order #{order_id}: {len(msg_text)} characters")
                return
            data['comment'] = msg_text
            data['is_anonymous'] = False
            name_display = name if name else "не указано"
            order_text = (
                f"🔎 Подарок уже готов к выдаче - осталось проверить и подтвердить:\n\n"
                f"👤 Username: @{username}\n"
                f"🏷️ Отображаемое имя: {name_display}\n"
                f"🎉 Подарков: {order_amount} шт. по {amount} ⭐️ каждый\n"
                f"🎁 Тип подарка: {gift_name}\n"
                f"💬 Комментарий: {msg_text}\n\n"
                f"✅ Всё правильно? ➡️ Отправьте +\n"
                f"✏️ Нужно изменить данные? Отправь - \n"
                f"💬 Если хотите изменить комментарии - просто напишите его еще раз"
            )
            c.send_message(msg_chat_id, order_text)
            logger.info(f"{LOGGER_PREFIX} Comment updated: {msg_text}, waiting for final confirmation for order #{order_id}")
            return
        try:
            session = next((s for s in session_manager.sessions if s["name"] == session_name), None)
            if not session or not session["active"] or session["balance"] < order_amount * amount:
                logger.warning(f"{LOGGER_PREFIX} Session {session_name} is invalid or insufficient for order #{order_id}")
                session = asyncio.run(session_manager.get_active_session(order_id))
                if not session:
                    logger.error(f"{LOGGER_PREFIX} No active sessions for order #{order_id}")
                    c.send_message(
                        msg_chat_id,
                        "❌ Нет доступных сессий для обработки заказа. Свяжитесь с продавцом."
                    )
                    return
                session_name = session["name"]
                data["session_name"] = session_name
            stars = session["balance"]
            logger.debug(f"{LOGGER_PREFIX} Star balance: {stars}, required: {order_amount * amount} for order #{order_id}")
            if order_amount * amount > stars:
                logger.warning(f"{LOGGER_PREFIX} Insufficient stars for order #{order_id}. Required: {order_amount * amount}, available: {stars}")
                cfg = load_config()
                auto_refunds = cfg.get("auto_refunds", True)
                if auto_refunds:
                    c.account.refund(order_id)
                    c.send_message(
                        msg_chat_id,
                        "❌ Баланса не хватило для оплаты, поэтому был осуществлён возврат средств. Приносим свои искренние извинения."
                    )
                    logger.info(f"{LOGGER_PREFIX} Automatic refund for order #{order_id}")
                else:
                    c.send_message(
                        msg_chat_id,
                        "❌ Баланса не хватило для оплаты, возврат средств требует ручного подтверждения. Напишите #help чтобы позвать продавца."
                    )
                    order_url = f"https://funpay.com/orders/{order_id}/"
                    for user_id in get_authorized_users():
                        bot.send_message(
                            user_id,
                            f"⚠️ Требуется ручной возврат средств для заказа #{order_id}\n🔗 Перейдите по ссылке, чтобы вернуть деньги: {order_url}",
                            parse_mode='HTML'
                        )
                queue.pop(msg_author_id, None)
                state = is_subcat_active(c, 3064)
                if state is False:
                    logger.debug(f"{LOGGER_PREFIX} Lots already deactivated for order #{order_id}")
                    return
                status = toggle_subcat_status(c, 3064)
                cfg['active_lots'] = status
                save_config(cfg)
                for user_id in get_authorized_users():
                    bot.send_message(
                        user_id,
                        "✅ Звёзды закончились, лоты успешно деактивированы",
                        parse_mode='HTML'
                    )
                logger.info(f"{LOGGER_PREFIX} Lots deactivated for order #{order_id}")
                return
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error getting balance for order #{order_id}: {type(e).__name__}: {str(e)}")
            c.send_message(
                msg_chat_id,
                f"❌ Ошибка при проверке баланса для заказа #{order_id}. Свяжитесь с продавцом.\nПодробности: {str(e)}"
            )
            for user_id in get_authorized_users():
                bot.send_message(
                    user_id,
                    f"❌ Ошибка при проверке баланса для заказа #{order_id}: {type(e).__name__}: {str(e)}",
                    parse_mode='HTML'
                )
            data["step"] = "await_username"
            c.send_message(
                msg_chat_id,
                "📍 Отправьте ещё раз ваш @username"
            )
            return

        try:
            result = asyncio.run(buy_gifts(c, msg_chat_id, username, gift_id, order_amount, order_id, bot, data['comment'], data['is_anonymous']))
            logger.debug(f"{LOGGER_PREFIX} Gift sending result for order #{order_id}: {result}")
            if not result:
                logger.warning(f"{LOGGER_PREFIX} Gift sending failed for order #{order_id}, returning to username input")
                data["step"] = "await_username"
                c.send_message(
                    msg_chat_id,
                    "📍 Отправьте ещё раз ваш @username"
                )
                return
            order_url = f"https://funpay.com/orders/{order_id}/"
            success_text = (
                f"✅ Готово! Подарки успешно отправлены {'в приватном режиме' if data['is_anonymous'] else 'открыто'} 🎉\n"
                f"💬 Не забудь подтвердить заказ и оставить отзыв — это важно!\n\n"
                f"🔗 Ссылка для подтверждения:\n{order_url}"
            )
            c.send_message(msg_chat_id, success_text)
            logger.info(f"{LOGGER_PREFIX} Order #{order_id} successfully completed")
            current_time = datetime.now().strftime("%H:%M:%S")
            text = (
                f"🎉 Заказ <a href='https://funpay.com/orders/{order_id}/'>{order_id}</a> выполнен!\n\n"
                f"👤 <b>Username:</b> @{username}\n"
                f"📝 <b>Ник в системе:</b> {name if name else 'не указано'}\n"
                f"🎁 <b>Подарков:</b> {order_amount} × {amount} ⭐️ ({gift_name})\n"
                f"💬 <b>Комментарий:</b> {data['comment'] if data['comment'] else 'Рандомный (анонимно)'}\n"
                f"💸 <b>Оплачено:</b> {order_price} ₽\n"
                f"💰 <b>Чистый профит:</b> {order_profit} ₽\n\n"
                f"⏳ <b>Добавлен в очередь:</b> <code>{order_time}</code>\n"
                f"✅ <b>Завершён:</b> <code>{current_time}</code>\n"
                f"📡 <b>Сессия:</b> {session_name}"
            )
            for user_id in get_authorized_users():
                bot.send_message(
                    user_id,
                    text,
                    parse_mode='HTML'
                )
            queue.pop(msg_author_id, None)
            logger.debug(f"{LOGGER_PREFIX} Order #{order_id} removed from queue")
            return
        except StargiftUsageLimited as e:
            logger.error(f"{LOGGER_PREFIX} Error: Gift sold out for order #{order_id}: {str(e)}")
            for user_id in get_authorized_users():
                bot.send_message(
                    user_id,
                    f"❌ Подарок распродан для заказа #{order_id}: {str(e)}",
                    parse_mode='HTML'
                )
            c.send_message(
                msg_chat_id,
                "❌ Этот подарок распродан! Напишите #help для связи с продавцом."
            )
            queue.pop(msg_author_id, None)
            return
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error processing order #{order_id}: {type(e).__name__}: {str(e)}")
            for user_id in get_authorized_users():
                bot.send_message(
                    user_id,
                    f"❌ Ошибка при обработке заказа #{order_id}: {type(e).__name__}: {str(e)}",
                    parse_mode='HTML'
                )
            c.send_message(
                msg_chat_id,
                f"❌ Произошла ошибка при обработке заказа #{order_id}. Свяжитесь с продавцом.\nПодробности: {str(e)}"
            )
            data["step"] = "await_username"
            c.send_message(
                msg_chat_id,
                "📍 Отправьте ещё раз ваш @username"
            )
            return

def order_hook(c: Cardinal, e: NewOrderEvent):
    if not RUNNING:
        logger.debug(f"{LOGGER_PREFIX} Plugin not running, skipping order #{e.order.id}")
        return
    order = e.order
    order_description = order.description
    logger.debug(f"{LOGGER_PREFIX} Processing order #{order.id} with description: {order_description}")
    gift_id, gift_name = get_tg_id_by_description(order_description)
    if gift_id is None or gift_name is None:
        logger.info(f"{LOGGER_PREFIX} Lot not found for description: {order_description}. Skipping order #{order.id}.")
        return
    loop = asyncio.new_event_loop()
    try:
        amount = loop.run_until_complete(get_amount(gift_id))
        if amount is None:
            logger.error(f"{LOGGER_PREFIX} Failed to get gift price for gift_id: {gift_id}, order #{order.id}")
            c.send_message(order.chat_id, f"❌ Ошибка при обработке заказа #{order.id}: не удалось определить стоимость подарка. Свяжитесь с продавцом.")
            for user_id in get_authorized_users():
                c.telegram.bot.send_message(
                    user_id,
                    f"❌ Ошибка при получении стоимости подарка для заказа #{order.id}: gift_id {gift_id}",
                    parse_mode='HTML'
                )
            return
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Error getting gift price for order #{order.id}: {type(e).__name__}: {str(e)}")
        c.send_message(order.chat_id, f"❌ Ошибка при обработке заказа #{order.id}. Свяжитесь с продавцом.\nПодробности: {str(e)}")
        for user_id in get_authorized_users():
            c.telegram.bot.send_message(
                user_id,
                f"❌ Ошибка при получении стоимости подарка для заказа #{order.id}: {type(e).__name__}: {str(e)}",
                parse_mode='HTML'
            )
        return
    finally:
        loop.close()
    order_id = order.id
    order_price = order.price
    buyer_id = int(order.buyer_id)
    order_amount = int(order.amount)
    order_fulldata = c.account.get_order(order_id)
    chat_id = order_fulldata.chat_id
    star_cost = amount * 1.16 * 1.06  # 1.16 руб. за звезду + 6% комиссии
    order_profit = round(order_price - order_amount * star_cost, 1)
    save_order_info(order_id, order_price, order_description, order_profit)

    logger.info(f"{LOGGER_PREFIX} 🛍 Order #{order_id} accepted and paid — {order_amount} gifts ready to send! ({gift_name})")
    start_text = (
        f"🎉 Заказ #{order_id} принят!\n"
        f"{order_amount} подарков ({gift_name}) готово к выдаче.\n\n"
        f"Отправьте свой Telegram юзернейм (например: @username), чтобы я знал, куда всё отправить 🍀\n"
    )

    logger.debug(f"{LOGGER_PREFIX} #{order_id} | gift_id: {gift_id}, gift_name: {gift_name}, amount: {amount}")
    c.send_message(chat_id, start_text)
    order_time = datetime.now().strftime("%H:%M:%S")
    queue[buyer_id] = {
        "order_id": order_id,
        "chat_id": chat_id,
        "step": "await_username",
        "amount": amount,
        "order_amount": order_amount,
        "order_time": order_time,
        "gift_id": gift_id,
        "gift_name": gift_name,
        "order_price": order_price,
        "order_profit": order_profit,
        "comment": None,
        "is_anonymous": True
    }
    logger.debug(f"{LOGGER_PREFIX} Order #{order_id} added to queue: {queue}")

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_NEW_MESSAGE = [message_hook]
BIND_TO_NEW_ORDER = [order_hook]
BIND_TO_DELETE = None