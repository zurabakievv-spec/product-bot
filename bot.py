#!/usr/bin/env python3
# coding: utf-8

import os
import json
import base64
import logging
from datetime import datetime
from html import escape
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Bot,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.error import TelegramError

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")
if not GROUP_CHAT_ID:
    raise RuntimeError("GROUP_CHAT_ID not set in .env")
try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID)
except ValueError:
    raise RuntimeError("GROUP_CHAT_ID must be integer")

DATA_DIR = "data"
PHOTOS_DIR = "photos"

(
    ADD_TO_CART_QTY, ASK_NAME, ASK_PHONE, ASK_COMMENT,
    ADD_PRODUCT_NAME, ADD_PRODUCT_DESC, ADD_PRODUCT_PRICE,
    ADD_PRODUCT_STOCK, ADD_PRODUCT_PHOTO, ADD_PRODUCT_CATEGORY,
    NEW_CATEGORY_NAME, RENAME_CATEGORY_NAME,
    EDIT_CART_ITEM, EDIT_CART_QTY,
    ADD_ADMIN_ID, DELETE_PRODUCT_ID,
) = range(16)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ADMIN_BUTTONS = [
    "➕ Добавить товар", "📦 Управление товарами",
    "📂 Управление категориями", "➕ Добавить категорию",
    "👤 Добавить менеджера", "📋 Заказы",
    "❌ Удалить товар", "🔙 Выйти",
]

CLIENT_BUTTONS = ["📦 Каталог", "🛒 Корзина"]

ALL_MENU_BUTTONS = ADMIN_BUTTONS + CLIENT_BUTTONS + ["Отмена", "Пропустить"]

CANCEL_BUTTONS = ["Отмена", "🔙 Выйти"]


# =========================
# Хранение
# =========================

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PHOTOS_DIR, exist_ok=True)


def data_path(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)


def safe_load_json(filename: str, default):
    path = data_path(filename)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return default
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        decoded = base64.b64decode(content).decode("utf-8")
        return json.loads(decoded)
    except Exception as e:
        log.warning("Failed to load %s: %s — returning default", filename, e)
        return default


def safe_save_json(filename: str, data):
    path = data_path(filename)
    try:
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(encoded)
        os.replace(tmp, path)
    except Exception as e:
        log.error("Failed to save %s: %s", filename, e)
        raise


def init_storage():
    ensure_dirs()
    defaults = {
        "products.json": [],
        "orders.json": [],
        "admins.json": [707877919],
        "categories.json": [],
    }
    for fn, default in defaults.items():
        path = data_path(fn)
        if not os.path.exists(path):
            safe_save_json(fn, default)
            log.info("Created %s with default", fn)


def load_products():
    return safe_load_json("products.json", [])


def load_orders():
    return safe_load_json("orders.json", [])


def load_admins():
    raw = safe_load_json("admins.json", [])
    if isinstance(raw, list):
        return [int(x) for x in raw if isinstance(x, (int, str)) and str(x).strip().isdigit()]
    return []


def load_categories():
    return safe_load_json("categories.json", [])


def save_products(products):
    safe_save_json("products.json", products)


def save_orders(orders):
    safe_save_json("orders.json", orders)


def save_admins(admins):
    safe_save_json("admins.json", list(set(admins)))


def save_categories(cats):
    cats = sorted(list(set(cats)))
    safe_save_json("categories.json", cats)


def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


# =========================
# Домен
# =========================

def get_categories():
    return load_categories()


def get_products_by_category(category: str):
    return [p for p in load_products() if p.get("category") == category]


def get_product_by_id(product_id) -> Optional[dict]:
    try:
        pid = int(product_id)
    except (ValueError, TypeError):
        return None
    for p in load_products():
        if p.get("id") == pid:
            return p
    return None


def next_product_id() -> int:
    products = load_products()
    return max((p.get("id", 0) for p in products), default=0) + 1


def add_order(client_name, phone, comment, cart_items, total):
    orders = load_orders()
    order = {
        "id": len(orders) + 1,
        "client_name": client_name,
        "phone": phone,
        "comment": comment or "",
        "items": cart_items,
        "total": total,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    orders.append(order)
    save_orders(orders)
    return order


def sanitize_string(text: str, max_length: int = 200) -> str:
    """Очистка строки от HTML и обрезка длины"""
    return escape(str(text))[:max_length]


# =========================
# UI
# =========================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [["📦 Каталог", "🛒 Корзина"]],
        resize_keyboard=True,
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["➕ Добавить товар", "📦 Управление товарами"],
            ["📂 Управление категориями", "➕ Добавить категорию"],
            ["👤 Добавить менеджера", "📋 Заказы"],
            ["❌ Удалить товар", "🔙 Выйти"],
        ],
        resize_keyboard=True,
    )


def format_product_card(prod: dict, index: int, total: int):
    name = sanitize_string(prod.get("name", "—"), 100)
    desc = sanitize_string(prod.get("description", "—"), 500)
    price = float(prod.get("price", 0))
    stock = int(prod.get("stock", 0))
    stock_text = f"📦 В наличии: {stock} шт." if stock > 0 else "❌ Нет в наличии"

    return (
        f"🏷 <b>{name}</b>\n\n"
        f"{desc}\n\n"
        f"💰 Цена: {price:,.0f}₽\n"
        f"{stock_text}"
    )


def format_order_message(order: dict):
    lines = [
        f"🛒 <b>Новый заказ №{order['id']}</b>\n",
        f"👤 Имя: {escape(order.get('client_name',''))}\n",
        f"📞 Телефон: {escape(order.get('phone',''))}\n",
        f"💬 Комментарий: {escape(order.get('comment','') or '—')}\n",
        "\n📋 <b>Состав заказа:</b>\n",
    ]
    for item in order.get("items", []):
        lines.append(
            f"— {sanitize_string(item.get('name',''))} × {item.get('quantity',0)} "
            f"= {item.get('price',0)*item.get('quantity',0):,.0f}₽"
        )
    lines.append(f"\n💰 <b>Итого: {order['total']:,.0f}₽</b>")
    return "\n".join(lines)


def is_menu_button(text: str) -> bool:
    return text in ALL_MENU_BUTTONS


def is_cancel_button(text: str) -> bool:
    return text in CANCEL_BUTTONS


def get_reply_markup_for_user(user_id: int):
    """Получить клавиатуру в зависимости от роли"""
    return admin_menu() if is_admin(user_id) else main_keyboard()


# =========================
# Клиент
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    context.user_data.setdefault("cart", [])
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        text = "👋 Добро пожаловать, менеджер!"
        reply_markup = admin_menu()
    else:
        text = "👋 Выберите действие:"
        reply_markup = main_keyboard()
    
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех текстовых сообщений"""
    text = update.message.text
    user_id = update.effective_user.id
    admin = is_admin(user_id)

    if text == "📦 Каталог":
        await show_categories(update, context)
    elif text == "🛒 Корзина":
        await view_cart(update, context)
    elif text == "🔙 Выйти":
        await start(update, context)
    elif text == "➕ Добавить товар" and admin:
        await update.message.reply_text(
            "📝 Введите название товара:", 
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ADD_PRODUCT_NAME
    elif text == "➕ Добавить категорию" and admin:
        await update.message.reply_text(
            "📂 Введите название новой категории:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return NEW_CATEGORY_NAME
    elif text == "👤 Добавить менеджера" and admin:
        await update.message.reply_text(
            "👤 Введите Telegram ID нового менеджера:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ADD_ADMIN_ID
    elif text == "📦 Управление товарами" and admin:
        await list_products_admin(update, context)
    elif text == "📂 Управление категориями" and admin:
        await show_manage_categories(update, context)
    elif text == "📋 Заказы" and admin:
        await show_orders_list(update, context)
    elif text == "❌ Удалить товар" and admin:
        await update.message.reply_text(
            "🗑 Введите ID товара для удаления:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return DELETE_PRODUCT_ID
    else:
        await update.message.reply_text(
            "❓ Используйте кнопки меню.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список категорий"""
    cats = get_categories()
    message = None
    is_callback = False
    
    if hasattr(update, 'callback_query') and update.callback_query:
        message = update.callback_query.message
        is_callback = True
    else:
        message = update.message
    
    if not cats:
        text = "📂 Каталог пока пуст."
        if is_callback:
            await update.callback_query.edit_message_text(text)
        else:
            await message.reply_text(text, reply_markup=get_reply_markup_for_user(update.effective_user.id))
        return
    
    kb = [[InlineKeyboardButton(cat, callback_data=f"cat|{cat}")] for cat in cats]
    
    if is_callback:
        await update.callback_query.edit_message_text(
            "📂 Выберите категорию:", 
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await message.reply_text(
            "📂 Выберите категорию:", 
            reply_markup=InlineKeyboardMarkup(kb)
        )


async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать товары категории"""
    query = update.callback_query
    await query.answer()
    
    cat = query.data.split("|", 1)[1]
    products = get_products_by_category(cat)
    context.user_data["cat_products"] = products
    context.user_data["current_index"] = 0
    context.user_data["current_category"] = cat
    
    if not products:
        await query.edit_message_text(
            f"📂 В категории '{cat}' пока нет товаров.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К категориям", callback_data="back_to_cats")
            ]])
        )
        return
    
    await show_product_card(update, context)


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку товара"""
    index = context.user_data.get("current_index", 0)
    products = context.user_data.get("cat_products", [])
    
    if not products or index >= len(products):
        return
    
    p = products[index]
    text = format_product_card(p, index, len(products))

    nav = [
        InlineKeyboardButton("⬅️ Назад", callback_data="nav_prev"),
        InlineKeyboardButton(f"{index+1}/{len(products)}", callback_data="nav_none"),
        InlineKeyboardButton("Вперёд ➡️", callback_data="nav_next"),
    ]
    rows = [nav]
    
    if p.get("stock", 0) > 0:
        rows.append([InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"add|{p['id']}")])
    
    rows.append([InlineKeyboardButton("🔙 К категориям", callback_data="back_to_cats")])

    photo_path = os.path.join(PHOTOS_DIR, p.get("photo", "")) if p.get("photo") else None

    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as ph:
                if update.callback_query:
                    await update.callback_query.message.reply_photo(
                        photo=ph, caption=text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML
                    )
                    try:
                        await update.callback_query.message.delete()
                    except Exception:
                        pass
                else:
                    await update.message.reply_photo(
                        photo=ph, caption=text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML
                    )
        else:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML
                )
    except TelegramError as e:
        log.error(f"Failed to show product card: {e}")


async def nav_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация по товарам"""
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "nav_prev":
        context.user_data["current_index"] = max(0, context.user_data.get("current_index", 0) - 1)
        return await show_product_card(update, context)
    
    if action == "nav_next":
        products = context.user_data.get("cat_products", [])
        context.user_data["current_index"] = min(len(products)-1, context.user_data.get("current_index", 0) + 1)
        return await show_product_card(update, context)
    
    if action == "back_to_cats":
        await show_categories(update, context)
        return ConversationHandler.END
    
    if action.startswith("add|"):
        pid = action.split("|", 1)[1]
        product = get_product_by_id(pid)
        
        if not product:
            await query.message.reply_text("❌ Товар не найден.")
            return ConversationHandler.END
        
        context.user_data["adding_product_id"] = int(pid)
        await query.message.reply_text(
            f"📝 Сколько штук добавить? (доступно: {product.get('stock',0)})\n"
            "Или нажмите «Отмена»",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ADD_TO_CART_QTY


async def cancel_add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена добавления в корзину"""
    user_id = update.effective_user.id
    await update.message.reply_text(
        "❌ Добавление отменено.", 
        reply_markup=get_reply_markup_for_user(user_id)
    )
    return ConversationHandler.END


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление товара в корзину"""
    text = update.message.text
    user_id = update.effective_user.id
    
    if is_cancel_button(text):
        return await cancel_add_to_cart(update, context)
    
    if is_menu_button(text):
        await update.message.reply_text(
            "❌ Добавление отменено.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END

    try:
        qty = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите целое число:")
        return ADD_TO_CART_QTY
    
    if qty <= 0:
        await update.message.reply_text("❌ Количество должно быть больше 0:")
        return ADD_TO_CART_QTY

    pid = context.user_data.get("adding_product_id")
    product = get_product_by_id(pid)
    
    if not product:
        await update.message.reply_text(
            "❌ Товар не найден.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END
    
    cart = context.user_data.get("cart", [])
    item = next((i for i in cart if i["id"] == pid), None)
    
    if item:
        new_qty = item["quantity"] + qty
        if new_qty > product.get("stock", 0):
            await update.message.reply_text(
                f"❌ Всего в корзине будет {new_qty} шт., а в наличии только {product['stock']} шт.\n"
                f"Максимально можно добавить: {product['stock'] - item['quantity']} шт."
            )
            return ADD_TO_CART_QTY
        item["quantity"] = new_qty
    else:
        if qty > product.get("stock", 0):
            await update.message.reply_text(f"❌ Максимум: {product['stock']} шт.")
            return ADD_TO_CART_QTY
        cart.append({
            "id": pid, 
            "name": product["name"], 
            "price": product["price"], 
            "quantity": qty
        })
    
    context.user_data["cart"] = cart
    await update.message.reply_text(
        f"✅ {sanitize_string(product['name'])} × {qty} добавлено в корзину!",
        reply_markup=get_reply_markup_for_user(user_id),
    )
    return ConversationHandler.END


# =========================
# Корзина и заказ
# =========================

async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр корзины"""
    cart = context.user_data.get("cart", [])
    user_id = update.effective_user.id
    
    if not cart:
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text("🛒 Корзина пуста.")
        else:
            await update.message.reply_text(
                "🛒 Корзина пуста.", 
                reply_markup=get_reply_markup_for_user(user_id)
            )
        return
    
    total = sum(i["price"] * i["quantity"] for i in cart)
    lines = ["🛒 <b>Ваша корзина:</b>\n"]
    
    for i, item in enumerate(cart, 1):
        item_total = item['price'] * item['quantity']
        lines.append(
            f"{i}. {sanitize_string(item['name'])} × {item['quantity']} = {item_total:,.0f}₽"
        )
    lines.append(f"\n💰 <b>Итого: {total:,.0f}₽</b>")

    kb = [
        [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_cart")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")],
    ]
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(
            "\n".join(lines), 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "\n".join(lines), 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode=ParseMode.HTML
        )


async def cart_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Действия с корзиной"""
    query = update.callback_query
    await query.answer()

    if query.data == "clear_cart":
        context.user_data["cart"] = []
        await query.edit_message_text("🗑 Корзина очищена.")
        return ConversationHandler.END

    if query.data == "checkout":
        cart = context.user_data.get("cart", [])
        if not cart:
            await query.edit_message_text("🛒 Корзина пуста. Нечего оформлять.")
            return ConversationHandler.END
        
        # Проверяем актуальность товаров
        for item in cart:
            product = get_product_by_id(item["id"])
            if not product:
                await query.edit_message_text(f"❌ Товар '{item['name']}' больше недоступен.")
                return ConversationHandler.END
            if item["quantity"] > product.get("stock", 0):
                await query.edit_message_text(
                    f"❌ Товара '{item['name']}' недостаточно на складе. "
                    f"Доступно: {product['stock']} шт."
                )
                return ConversationHandler.END
        
        await query.edit_message_text("📝 Оформляем заказ!")
        await query.message.reply_text(
            "👤 Введите ваше имя:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ASK_NAME

    if query.data == "edit_cart":
        cart = context.user_data.get("cart", [])
        if not cart:
            await query.edit_message_text("🛒 Корзина пуста.")
            return ConversationHandler.END
        
        kb = []
        for i, item in enumerate(cart, 1):
            kb.append([InlineKeyboardButton(
                f"❌ {sanitize_string(item['name'], 30)} × {item['quantity']}",
                callback_data=f"editcart|{i-1}"
            )])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_cart_view")])
        
        await query.edit_message_text(
            "✏️ Выберите товар для удаления:", 
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return EDIT_CART_ITEM

    if query.data == "back_to_main":
        await query.edit_message_text("👋 Главное меню")
        await query.message.reply_text(
            "Выберите действие:", 
            reply_markup=get_reply_markup_for_user(query.from_user.id)
        )
        return ConversationHandler.END


async def edit_cart_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование товара в корзине"""
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_cart_view":
        await view_cart(update, context)
        return ConversationHandler.END

    try:
        idx = int(query.data.split("|")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка данных.")
        return ConversationHandler.END
    
    cart = context.user_data.get("cart", [])
    if 0 <= idx < len(cart):
        removed = cart.pop(idx)
        context.user_data["cart"] = cart
        await query.edit_message_text(f"🗑 {sanitize_string(removed['name'])} удалён из корзины.")
        
        # Показываем обновленную корзину
        if cart:
            await view_cart(update, context)
        else:
            await query.message.reply_text(
                "🛒 Корзина пуста.", 
                reply_markup=get_reply_markup_for_user(query.from_user.id)
            )
    else:
        await query.edit_message_text("❌ Товар не найден в корзине.")
    
    return ConversationHandler.END


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос имени клиента"""
    text = update.message.text
    user_id = update.effective_user.id
    
    if is_cancel_button(text):
        await update.message.reply_text(
            "❌ Оформление отменено.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END
    
    if is_menu_button(text):
        await update.message.reply_text(
            "❌ Оформление отменено.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END

    context.user_data["client_name"] = sanitize_string(text.strip(), 100)
    
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)], ["Отмена"]],
        resize_keyboard=True, 
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "📞 Отправьте ваш номер телефона (начиная с 8):",
        reply_markup=kb
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос телефона клиента"""
    user_id = update.effective_user.id
    
    if update.message.text and is_cancel_button(update.message.text):
        await update.message.reply_text(
            "❌ Оформление отменено.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END
    
    if update.message.text and is_menu_button(update.message.text):
        await update.message.reply_text(
            "❌ Оформление отменено.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END

    phone = None
    if update.message.contact:
        phone = update.message.contact.phone_number.replace("+", "").replace("-", "").replace(" ", "")
    else:
        phone = update.message.text.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    
    # Нормализация номера
    if phone.startswith("+7"):
        phone = "8" + phone[2:]
    elif phone.startswith("7"):
        phone = "8" + phone[1:]
    elif not phone.startswith("8"):
        phone = "8" + phone

    if not phone.isdigit() or len(phone) != 11:
        await update.message.reply_text(
            "❌ Номер должен содержать 11 цифр и начинаться с 8.\n"
            "Пример: 89991234567"
        )
        return ASK_PHONE

    context.user_data["phone"] = phone
    
    kb = ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True)
    await update.message.reply_text(
        "💬 Комментарий к заказу (необязательно):",
        reply_markup=kb
    )
    return ASK_COMMENT


async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос комментария и оформление заказа"""
    text = update.message.text
    user_id = update.effective_user.id
    
    if is_cancel_button(text):
        await update.message.reply_text(
            "❌ Оформление отменено.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END
    
    if is_menu_button(text):
        await update.message.reply_text(
            "❌ Оформление отменено.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END

    comment = "" if text == "Пропустить" else sanitize_string(text.strip(), 500)
    context.user_data["comment"] = comment

    cart = context.user_data.get("cart", [])
    if not cart:
        await update.message.reply_text(
            "❌ Корзина пуста. Нечего оформлять.", 
            reply_markup=get_reply_markup_for_user(user_id)
        )
        return ConversationHandler.END
    
    total = sum(i["price"] * i["quantity"] for i in cart)
    
    try:
        order = add_order(
            context.user_data["client_name"], 
            context.user_data["phone"], 
            comment, 
            cart, 
            total
        )
        
        # Отправляем заказ в группу
        await context.bot.send_message(
            GROUP_CHAT_ID, 
            format_order_message(order), 
            parse_mode=ParseMode.HTML
        )
        
        # Уведомляем клиента
        await update.message.reply_text(
            f"✅ Заказ №{order['id']} оформлен! Мы свяжемся с вами в ближайшее время.",
            reply_markup=get_reply_markup_for_user(user_id)
        )
        
        # Очищаем корзину
        context.user_data["cart"] = []
        
    except TelegramError as e:
        log.error(f"Failed to send order to group: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при оформлении заказа. Попробуйте позже.",
            reply_markup=get_reply_markup_for_user(user_id)
        )
    
    return ConversationHandler.END


# =========================
# Админка: менеджеры
# =========================

async def add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление нового менеджера"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ У вас нет прав для этого действия.", 
            reply_markup=get_reply_markup_for_user(update.effective_user.id)
        )
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    if is_cancel_button(text):
        await update.message.reply_text("❌ Добавление менеджера отменено.", reply_markup=admin_menu())
        return ConversationHandler.END
    
    if is_menu_button(text):
        await update.message.reply_text("❌ Добавление менеджера отменено.", reply_markup=admin_menu())
        return ConversationHandler.END
    
    try:
        new_id = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите числовой ID:")
        return ADD_ADMIN_ID

    # Проверяем существование пользователя
    try:
        user = await context.bot.get_chat(new_id)
        user_name = user.full_name or user.username or str(new_id)
    except TelegramError:
        await update.message.reply_text(
            "⚠️ Не удалось проверить пользователя. Проверьте правильность ID.\n"
            "Пользователь должен хотя бы раз запустить бота."
        )
        return ADD_ADMIN_ID

    admins = load_admins()
    if new_id in admins:
        await update.message.reply_text(
            f"ℹ️ Пользователь {user_name} уже является менеджером.",
            reply_markup=admin_menu()
        )
    else:
        admins.append(new_id)
        save_admins(admins)
        await update.message.reply_text(
            f"✅ Менеджер {user_name} (ID: {new_id}) добавлен.",
            reply_markup=admin_menu()
        )
    
    return ConversationHandler.END


# =========================
# Админка: категории
# =========================

async def new_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание новой категории"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ У вас нет прав для этого действия.", 
            reply_markup=get_reply_markup_for_user(update.effective_user.id)
        )
        return ConversationHandler.END
    
    name = update.message.text.strip()

    if is_cancel_button(name):
        await update.message.reply_text("❌ Создание категории отменено.", reply_markup=admin_menu())
        return ConversationHandler.END
    
    if is_menu_button(name):
        await update.message.reply_text("❌ Создание категории отменено.", reply_markup=admin_menu())
        return ConversationHandler.END
    
    if not name:
        await update.message.reply_text("❌ Введите название категории:")
        return NEW_CATEGORY_NAME
    
    if len(name) > 50:
        await update.message.reply_text("❌ Название категории слишком длинное (макс. 50 символов).")
        return NEW_CATEGORY_NAME

    cats = load_categories()
    if name in cats:
        await update.message.reply_text(
            "❌ Такая категория уже существует!", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END

    cats.append(name)
    save_categories(cats)
    await update.message.reply_text(
        f"✅ Категория '{name}' создана!", 
        reply_markup=admin_menu()
    )
    return ConversationHandler.END


async def show_manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление категориями"""
    if not is_admin(update.effective_user.id):
        return
    
    cats = get_categories()
    if not cats:
        await update.message.reply_text("📂 Категорий пока нет.", reply_markup=admin_menu())
        return
    
    kb = []
    for cat in cats:
        kb.append([InlineKeyboardButton(cat, callback_data=f"cat_manage|{cat}")])
    
    await update.message.reply_text(
        "📂 Управление категориями:", 
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def category_manage_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Действия с категорией"""
    query = update.callback_query
    await query.answer()

    cat = query.data.split("|", 1)[1]
    kb = [
        [InlineKeyboardButton("🗑 Удалить категорию", callback_data=f"del_cat|{cat}")],
        [InlineKeyboardButton("✏️ Переименовать", callback_data=f"rename_cat|{cat}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_cat_list")],
    ]
    await query.edit_message_text(
        f"📂 Категория: {escape(cat)}", 
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def rename_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос нового имени категории"""
    query = update.callback_query
    await query.answer()
    
    old_name = query.data.split("|", 1)[1]
    context.user_data["rename_old_cat"] = old_name
    
    await query.edit_message_text(
        f"✏️ Введите новое название для '{old_name}':"
    )
    return RENAME_CATEGORY_NAME


async def rename_category_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переименование категории"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    new_name = update.message.text.strip()
    
    if is_cancel_button(new_name) or is_menu_button(new_name):
        await update.message.reply_text(
            "❌ Переименование отменено.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END
    
    if not new_name:
        await update.message.reply_text("❌ Введите новое название:")
        return RENAME_CATEGORY_NAME
    
    old_name = context.user_data.get("rename_old_cat")
    if not old_name or new_name == old_name:
        await update.message.reply_text(
            "❌ Новое имя совпадает со старым.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END

    cats = load_categories()
    if old_name in cats:
        cats.remove(old_name)
        if new_name not in cats:
            cats.append(new_name)
        save_categories(cats)

    products = load_products()
    for p in products:
        if p.get("category") == old_name:
            p["category"] = new_name
    save_products(products)

    await update.message.reply_text(
        f"✅ Категория '{old_name}' переименована в '{new_name}'.", 
        reply_markup=admin_menu()
    )
    return ConversationHandler.END


async def delete_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление категории"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        return
    
    cat = query.data.split("|", 1)[1]

    # Проверяем, есть ли товары в категории
    products_in_cat = [p for p in load_products() if p.get("category") == cat]
    if products_in_cat:
        await query.answer(
            f"⚠️ В категории {len(products_in_cat)} товаров. Они останутся без категории.", 
            show_alert=True
        )

    cats = load_categories()
    if cat in cats:
        cats.remove(cat)
        save_categories(cats)

    products = load_products()
    for p in products:
        if p.get("category") == cat:
            p["category"] = ""
    save_products(products)

    await query.edit_message_text(f"✅ Категория '{cat}' удалена.")


# =========================
# Админка: товары
# =========================

async def add_product_name_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления товара"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    name = update.message.text.strip()
    
    if is_cancel_button(name) or is_menu_button(name):
        await update.message.reply_text(
            "❌ Добавление товара отменено.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END
    
    context.user_data["new_product"] = {"name": name}
    await update.message.reply_text("📝 Введите описание товара:")
    return ADD_PRODUCT_DESC


async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление описания товара"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    if is_cancel_button(text) or is_menu_button(text):
        await update.message.reply_text(
            "❌ Добавление товара отменено.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END
    
    context.user_data["new_product"]["description"] = text
    await update.message.reply_text("💰 Введите цену (только число):")
    return ADD_PRODUCT_PRICE


async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление цены товара"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    if is_cancel_button(text) or is_menu_button(text):
        await update.message.reply_text(
            "❌ Добавление товара отменено.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END
    
    try:
        price = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введите число:")
        return ADD_PRODUCT_PRICE
    
    context.user_data["new_product"]["price"] = price
    await update.message.reply_text("📦 Введите количество в наличии (целое число):")
    return ADD_PRODUCT_STOCK


async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление количества товара"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    if is_cancel_button(text) or is_menu_button(text):
        await update.message.reply_text(
            "❌ Добавление товара отменено.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END
    
    try:
        stock = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите целое число:")
        return ADD_PRODUCT_STOCK
    
    context.user_data["new_product"]["stock"] = stock
    
    cats = get_categories()
    if not cats:
        await update.message.reply_text(
            "❌ Сначала создайте категорию.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END
    
    kb = [[InlineKeyboardButton(c, callback_data=f"cat_prod|{c}")] for c in cats]
    await update.message.reply_text(
        "📂 Выберите категорию:", 
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ADD_PRODUCT_CATEGORY


async def add_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор категории товара"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    cat = query.data.split("|", 1)[1]
    context.user_data["new_product"]["category"] = cat
    
    await query.edit_message_text(
        "📸 Отправьте фото товара или нажмите «Пропустить»:"
    )
    return ADD_PRODUCT_PHOTO


async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление фото и сохранение товара"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    if update.message and update.message.text and is_menu_button(update.message.text):
        await update.message.reply_text(
            "❌ Добавление товара отменено.", 
            reply_markup=admin_menu()
        )
        return ConversationHandler.END

    product = context.user_data["new_product"]
    product["id"] = next_product_id()

    if update.message and update.message.photo:
        ph = update.message.photo[-1]
        file = await ph.get_file()
        filename = f"product_{product['id']}.jpg"
        await file.download_to_drive(os.path.join(PHOTOS_DIR, filename))
        product["photo"] = filename
    else:
        product["photo"] = ""

    products = load_products()
    products.append(product)
    save_products(products)

    await update.message.reply_text(
        f"✅ Товар '{product['name']}' добавлен! ID: {product['id']}",
        reply_markup=admin_menu(),
    )
    return ConversationHandler.END


async def list_products_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список товаров для админа"""
    if not is_admin(update.effective_user.id):
        return
    
    products = load_products()
    if not products:
        await update.message.reply_text("📦 Товаров пока нет.", reply_markup=admin_menu())
        return
    
    lines = ["📋 <b>Товары по категориям:</b>\n"]
    cats = get_categories()
    
    for cat in cats:
        lines.append(f"<b>{escape(cat)}</b>")
        for p in products:
            if p.get("category") == cat:
                stock = p.get("stock", 0)
                color = "🟢" if stock > 0 else "🔴"
                lines.append(
                    f"  {color} {sanitize_string(p['name'], 50)} "
                    f"(ID: {p['id']}) — {p['price']:,.0f}₽, остаток: {stock}"
                )
        lines.append("")
    
    await update.message.reply_text(
        "\n".join(lines), 
        reply_markup=admin_menu(), 
        parse_mode=ParseMode.HTML
    )


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление товара"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    if is_cancel_button(text) or is_menu_button(text):
        await update.message.reply_text("❌ Удаление отменено.", reply_markup=admin_menu())
        return ConversationHandler.END
    
    try:
        pid = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите числовой ID товара:")
        return DELETE_PRODUCT_ID

    product = get_product_by_id(pid)
    if not product:
        await update.message.reply_text("❌ Товар не найден.", reply_markup=admin_menu())
        return ConversationHandler.END

    if product.get("photo"):
        path = os.path.join(PHOTOS_DIR, product["photo"])
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                log.warning("Failed to remove photo %s: %s", path, e)

    products = [p for p in load_products() if p.get("id") != pid]
    save_products(products)
    
    await update.message.reply_text(
        f"✅ Товар '{product['name']}' удалён.", 
        reply_markup=admin_menu()
    )
    return ConversationHandler.END


# =========================
# Админка: заказы
# =========================

async def show_orders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список заказов"""
    if not is_admin(update.effective_user.id):
        return
    
    orders = load_orders()
    if not orders:
        await update.message.reply_text("📋 Заказов пока нет.", reply_markup=admin_menu())
        return
    
    lines = ["📋 <b>Последние заказы:</b>\n"]
    for o in orders[-10:]:
        lines.append(
            f"Заказ #{o['id']}: {sanitize_string(o['client_name'], 30)} | "
            f"{o['total']:,.0f}₽ | {o['created_at']}"
        )
    
    await update.message.reply_text(
        "\n".join(lines), 
        reply_markup=admin_menu(), 
        parse_mode=ParseMode.HTML
    )


# =========================
# Запуск
# =========================

def main():
    """Точка входа"""
    init_storage()
    
    # Проверяем доступ к группе
    try:
        import asyncio
        async def check_group():
            bot = Bot(token=BOT_TOKEN)
            await bot.get_chat(GROUP_CHAT_ID)
            log.info(f"✅ Доступ к группе {GROUP_CHAT_ID} подтвержден")
        asyncio.get_event_loop().run_until_complete(check_group())
    except Exception as e:
        log.error(f"❌ Нет доступа к группе {GROUP_CHAT_ID}: {e}")
        log.error("Убедитесь, что бот добавлен в группу и имеет права администратора")
    
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handlers
    add_product_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить товар$"), add_product_name_new)],
        states={
            ADD_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name_new)],
            ADD_PRODUCT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)],
            ADD_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)],
            ADD_PRODUCT_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_stock)],
            ADD_PRODUCT_CATEGORY: [CallbackQueryHandler(add_product_category, pattern="^cat_prod\\|")],
            ADD_PRODUCT_PHOTO: [
                MessageHandler(filters.PHOTO, add_product_photo),
                MessageHandler(filters.Regex("^Пропустить$"), add_product_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cart_action, pattern="^(checkout|clear_cart|edit_cart|back_to_main)$")],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), ask_phone)],
            ASK_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_comment)],
            EDIT_CART_ITEM: [CallbackQueryHandler(edit_cart_item, pattern="^(editcart\\||back_to_cart_view)")],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    cart_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(nav_product, pattern="^add\\|")],
        states={
            ADD_TO_CART_QTY: [
                MessageHandler(filters.Regex("^Отмена$"), cancel_add_to_cart),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_to_cart),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    add_admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 Добавить менеджера$"), add_admin_id)],
        states={
            ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    delete_product_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❌ Удалить товар$"), delete_product)],
        states={
            DELETE_PRODUCT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_product)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    new_cat_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить категорию$"), new_category_name)],
        states={
            NEW_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_category_name)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    rename_cat_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(rename_category_prompt, pattern="^rename_cat\\|")],
        states={
            RENAME_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_category_execute)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_product_conv)
    app.add_handler(order_conv)
    app.add_handler(cart_conv)
    app.add_handler(add_admin_conv)
    app.add_handler(delete_product_conv)
    app.add_handler(new_cat_conv)
    app.add_handler(rename_cat_conv)

    app.add_handler(CallbackQueryHandler(show_category_products, pattern="^cat\\|"))
    app.add_handler(CallbackQueryHandler(nav_product, pattern="^(nav_prev|nav_next|back_to_cats)"))
    app.add_handler(CallbackQueryHandler(category_manage_action, pattern="^cat_manage\\|"))
    app.add_handler(CallbackQueryHandler(delete_category, pattern="^del_cat\\|"))
    app.add_handler(CallbackQueryHandler(show_manage_categories, pattern="^back_to_cat_list$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))

    log.info("✅ Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
