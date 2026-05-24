# production_ready_telegram_shop_bot_complete_v3.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import base64
import logging
import asyncio
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
    InputMediaPhoto,
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
from telegram.error import TelegramError, BadRequest

# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

if not GROUP_CHAT_ID:
    raise RuntimeError("GROUP_CHAT_ID not set")

try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID)
except ValueError:
    raise RuntimeError("GROUP_CHAT_ID must be integer")

DATA_DIR = "data"
PHOTOS_DIR = "photos"

storage_lock = asyncio.Lock()

(
    ADD_TO_CART_QTY,
    ASK_NAME,
    ASK_PHONE,
    ASK_COMMENT,
    ADD_PRODUCT_NAME,
    ADD_PRODUCT_DESC,
    ADD_PRODUCT_PRICE,
    ADD_PRODUCT_STOCK,
    ADD_PRODUCT_CATEGORY,
    ADD_PRODUCT_PHOTO,
    NEW_CATEGORY_NAME,
    RENAME_CATEGORY_NAME,
    EDIT_CART_ITEM,
    ADD_ADMIN_ID,
    DELETE_PRODUCT_ID,
) = range(15)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger(__name__)

ADMIN_BUTTONS = [
    "➕ Добавить товар",
    "📦 Управление товарами",
    "📂 Управление категориями",
    "➕ Добавить категорию",
    "👤 Добавить менеджера",
    "📋 Заказы",
    "❌ Удалить товар",
    "🔙 Выйти",
]

CLIENT_BUTTONS = [
    "📦 Каталог",
    "🛒 Корзина",
]

ALL_MENU_BUTTONS = ADMIN_BUTTONS + CLIENT_BUTTONS + [
    "Отмена",
    "Пропустить",
]

CANCEL_BUTTONS = ["Отмена", "🔙 Выйти"]

# =========================================================
# STORAGE
# =========================================================


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
            decoded = base64.b64decode(content).decode("utf-8")
            return json.loads(decoded)

    except Exception as e:
        log.warning("Failed load %s: %s", filename, e)
        return default



def safe_save_json(filename: str, data):
    path = data_path(filename)

    raw = json.dumps(data, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")

    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        f.write(encoded)

    os.replace(tmp, path)



def init_storage():
    ensure_dirs()

    defaults = {
        "products.json": [],
        "orders.json": [],
        "admins.json": [],
        "categories.json": [],
    }

    for fn, default in defaults.items():
        if not os.path.exists(data_path(fn)):
            safe_save_json(fn, default)



def load_products():
    return safe_load_json("products.json", [])



def save_products(products):
    safe_save_json("products.json", products)



def load_orders():
    return safe_load_json("orders.json", [])



def save_orders(orders):
    safe_save_json("orders.json", orders)



def load_categories():
    return safe_load_json("categories.json", [])



def save_categories(categories):
    categories = sorted(list(dict.fromkeys(categories)))
    safe_save_json("categories.json", categories)



def load_admins():
    raw = safe_load_json("admins.json", [])

    result = []

    for item in raw:
        try:
            result.append(int(item))
        except Exception:
            pass

    return result



def save_admins(admins):
    safe_save_json("admins.json", sorted(list(set(admins))))



def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


# =========================================================
# HELPERS
# =========================================================


def sanitize_string(text: str, max_length: int = 300) -> str:
    return escape(str(text))[:max_length]



def is_menu_button(text: Optional[str]) -> bool:
    return bool(text and text in ALL_MENU_BUTTONS)



def is_cancel_button(text: Optional[str]) -> bool:
    return bool(text and text in CANCEL_BUTTONS)



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



def get_reply_markup_for_user(user_id: int):
    return admin_menu() if is_admin(user_id) else main_keyboard()



def get_categories():
    return load_categories()



def get_products_by_category(category: str):
    return [p for p in load_products() if p.get("category") == category]



def get_product_by_id(product_id) -> Optional[dict]:
    try:
        pid = int(product_id)
    except Exception:
        return None

    for p in load_products():
        if p.get("id") == pid:
            return p

    return None



def next_product_id():
    products = load_products()
    return max((p.get("id", 0) for p in products), default=0) + 1



def next_order_id():
    orders = load_orders()
    return max((o.get("id", 0) for o in orders), default=0) + 1



def format_product_card(product: dict):
    name = sanitize_string(product.get("name", "—"), 100)
    desc = sanitize_string(product.get("description", "—"), 700)

    if len(desc) > 850:
        desc = desc[:850]

    stock = int(product.get("stock", 0))
    price = float(product.get("price", 0))

    stock_text = (
        f"📦 В наличии: {stock} шт."
        if stock > 0
        else "❌ Нет в наличии"
    )

    return (
        f"🏷 <b>{name}</b>\n\n"
        f"{desc}\n\n"
        f"💰 Цена: {price:,.0f}₽\n"
        f"{stock_text}"
    )



def format_order_message(order: dict):
    lines = [
        f"🛒 <b>Новый заказ №{order['id']}</b>\n",
        f"👤 Имя: {escape(order['client_name'])}",
        f"📞 Телефон: {escape(order['phone'])}",
        f"💬 Комментарий: {escape(order.get('comment') or '—')}\n",
        "📋 <b>Состав заказа:</b>",
    ]

    for item in order["items"]:
        total = item["price"] * item["quantity"]

        lines.append(
            f"— {sanitize_string(item['name'])} × {item['quantity']} = {total:,.0f}₽"
        )

    lines.append(f"\n💰 <b>Итого: {order['total']:,.0f}₽</b>")

    return "\n".join(lines)


async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    try:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        log.warning("edit_message_text failed: %s", e)


async def safe_delete_message(message):
    try:
        await message.delete()
    except Exception:
        pass


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await update.message.reply_text(
        "❌ Действие отменено.",
        reply_markup=get_reply_markup_for_user(user_id),
    )

    return ConversationHandler.END


# =========================================================
# START
# =========================================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", [])

    user_id = update.effective_user.id

    if is_admin(user_id):
        text = "👋 Добро пожаловать, менеджер!"
    else:
        text = "👋 Выберите действие:"

    await update.message.reply_text(
        text,
        reply_markup=get_reply_markup_for_user(user_id),
    )


# =========================================================
# CATALOG
# =========================================================


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = get_categories()

    query = update.callback_query
    msg = query.message if query else update.message

    if not categories:
        if query:
            await safe_edit_message(query, "📂 Каталог пока пуст.")
        else:
            await msg.reply_text("📂 Каталог пока пуст.")
        return

    kb = [
        [InlineKeyboardButton(cat, callback_data=f"cat|{cat}")]
        for cat in categories
    ]

    if query:
        await safe_edit_message(
            query,
            "📂 Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await msg.reply_text(
            "📂 Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(kb),
        )


async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category = query.data.split("|", 1)[1]

    products = get_products_by_category(category)

    context.user_data["cat_products"] = products
    context.user_data["current_index"] = 0

    if not products:
        await safe_edit_message(
            query,
            "📂 В категории нет товаров.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_cats")]
            ])
        )
        return

    await show_product_card(update, context)


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    products = context.user_data.get("cat_products", [])
    index = context.user_data.get("current_index", 0)

    if not products:
        return

    if index < 0:
        index = 0

    if index >= len(products):
        index = len(products) - 1

    context.user_data["current_index"] = index

    product = products[index]

    text = format_product_card(product)

    rows = [
        [
            InlineKeyboardButton("⬅️", callback_data="nav_prev"),
            InlineKeyboardButton(f"{index + 1}/{len(products)}", callback_data="nav_none"),
            InlineKeyboardButton("➡️", callback_data="nav_next"),
        ]
    ]

    if int(product.get("stock", 0)) > 0:
        rows.append([
            InlineKeyboardButton(
                "🛒 Добавить в корзину",
                callback_data=f"add|{product['id']}"
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 К категориям", callback_data="back_to_cats")
    ])

    markup = InlineKeyboardMarkup(rows)

    photo_path = None

    if product.get("photo"):
        photo_path = os.path.join(PHOTOS_DIR, product["photo"])

    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as ph:
                media = InputMediaPhoto(
                    media=ph,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                )

                try:
                    await query.message.edit_media(media=media, reply_markup=markup)
                except Exception:
                    with open(photo_path, "rb") as ph2:
                        await query.message.reply_photo(
                            photo=ph2,
                            caption=text,
                            reply_markup=markup,
                            parse_mode=ParseMode.HTML,
                        )
                        await safe_delete_message(query.message)
        else:
            await safe_edit_message(
                query,
                text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )

    except TelegramError as e:
        log.error("show_product_card error: %s", e)


async def nav_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data

    if action == "nav_none":
        return

    if action == "back_to_cats":
        await show_categories(update, context)
        return

    if action == "nav_prev":
        context.user_data["current_index"] -= 1
        await show_product_card(update, context)
        return

    if action == "nav_next":
        context.user_data["current_index"] += 1
        await show_product_card(update, context)
        return

    if action.startswith("add|"):
        pid = int(action.split("|", 1)[1])

        product = get_product_by_id(pid)

        if not product:
            await query.message.reply_text("❌ Товар не найден.")
            return ConversationHandler.END

        context.user_data["adding_product_id"] = pid

        await query.message.reply_text(
            f"Введите количество (доступно: {product['stock']})",
            reply_markup=ReplyKeyboardMarkup(
                [["Отмена"]],
                resize_keyboard=True,
            )
        )

        return ADD_TO_CART_QTY


# =========================================================
# CART
# =========================================================


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if is_cancel_button(text) or is_menu_button(text):
        return await cancel_conv(update, context)

    try:
        qty = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите целое число.")
        return ADD_TO_CART_QTY

    if qty <= 0:
        await update.message.reply_text("❌ Количество должно быть больше 0.")
        return ADD_TO_CART_QTY

    pid = context.user_data.get("adding_product_id")

    product = get_product_by_id(pid)

    if not product:
        await update.message.reply_text("❌ Товар не найден.")
        return ConversationHandler.END

    stock = int(product.get("stock", 0))

    cart = context.user_data.get("cart", [])

    existing = next((i for i in cart if i["id"] == pid), None)

    already = existing["quantity"] if existing else 0

    if qty + already > stock:
        available = stock - already

        await update.message.reply_text(
            f"❌ Недостаточно товара. Доступно: {available}"
        )

        return ADD_TO_CART_QTY

    if existing:
        existing["quantity"] += qty
    else:
        cart.append({
            "id": pid,
            "name": product["name"],
            "price": product["price"],
            "quantity": qty,
        })

    context.user_data["cart"] = cart

    context.user_data.pop("adding_product_id", None)

    await update.message.reply_text(
        "✅ Товар добавлен в корзину.",
        reply_markup=get_reply_markup_for_user(update.effective_user.id),
    )

    return ConversationHandler.END


async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", [])

    query = update.callback_query

    if not cart:
        if query:
            await safe_edit_message(query, "🛒 Корзина пуста.")
        else:
            await update.message.reply_text("🛒 Корзина пуста.")
        return

    total = sum(i["price"] * i["quantity"] for i in cart)

    lines = ["🛒 <b>Ваша корзина:</b>\n"]

    for idx, item in enumerate(cart, 1):
        item_total = item["price"] * item["quantity"]

        lines.append(
            f"{idx}. {sanitize_string(item['name'])} × {item['quantity']} = {item_total:,.0f}₽"
        )

    lines.append(f"\n💰 <b>Итого: {total:,.0f}₽</b>")

    kb = [
        [InlineKeyboardButton("✅ Оформить", callback_data="checkout")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_cart")],
        [InlineKeyboardButton("🗑 Очистить", callback_data="clear_cart")],
    ]

    if query:
        await safe_edit_message(
            query,
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML,
        )


async def cart_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "clear_cart":
        context.user_data["cart"] = []

        await safe_edit_message(query, "🗑 Корзина очищена.")

        return ConversationHandler.END

    if data == "edit_cart":
        cart = context.user_data.get("cart", [])

        if not cart:
            await safe_edit_message(query, "🛒 Корзина пуста.")
            return ConversationHandler.END

        kb = []

        for idx, item in enumerate(cart):
            kb.append([
                InlineKeyboardButton(
                    f"❌ {sanitize_string(item['name'], 30)} × {item['quantity']}",
                    callback_data=f"editcart|{idx}"
                )
            ])

        kb.append([
            InlineKeyboardButton("🔙 Назад", callback_data="back_to_cart")
        ])

        await safe_edit_message(
            query,
            "✏️ Выберите товар для удаления:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

        return EDIT_CART_ITEM

    if data == "checkout":
        cart = context.user_data.get("cart", [])

        if not cart:
            await safe_edit_message(query, "🛒 Корзина пуста.")
            return ConversationHandler.END

        await query.message.reply_text(
            "👤 Введите ваше имя:",
            reply_markup=ReplyKeyboardMarkup(
                [["Отмена"]],
                resize_keyboard=True,
            )
        )

        return ASK_NAME


async def edit_cart_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_cart":
        await view_cart(update, context)
        return EDIT_CART_ITEM

    try:
        idx = int(query.data.split("|")[1])
    except Exception:
        await safe_edit_message(query, "❌ Ошибка данных.")
        return ConversationHandler.END

    cart = context.user_data.get("cart", [])

    if idx < 0 or idx >= len(cart):
        await safe_edit_message(query, "❌ Товар не найден.")
        return ConversationHandler.END

    removed = cart.pop(idx)

    context.user_data["cart"] = cart

    if not cart:
        await safe_edit_message(query, "🛒 Корзина пуста.")
        return ConversationHandler.END

    await view_cart(update, context)

    log.info("Removed from cart: %s", removed.get("name"))

    return EDIT_CART_ITEM


# =========================================================
# CHECKOUT
# =========================================================


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if is_cancel_button(text) or is_menu_button(text):
        return await cancel_conv(update, context)

    context.user_data["client_name"] = sanitize_string(text, 100)

    kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 Поделиться номером", request_contact=True)],
            ["Отмена"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        "📞 Отправьте номер телефона:",
        reply_markup=kb,
    )

    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message.text else ""

    if is_cancel_button(text) or is_menu_button(text):
        return await cancel_conv(update, context)

    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = text

    phone = (
        phone
        .replace("+", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .replace(" ", "")
    )

    if phone.startswith("7"):
        phone = "8" + phone[1:]

    if not phone.startswith("8"):
        phone = "8" + phone

    if not phone.isdigit() or len(phone) != 11:
        await update.message.reply_text(
            "❌ Номер должен быть в формате 89991234567"
        )
        return ASK_PHONE

    context.user_data["phone"] = phone

    await update.message.reply_text(
        "💬 Комментарий к заказу:",
        reply_markup=ReplyKeyboardMarkup(
            [["Пропустить"], ["Отмена"]],
            resize_keyboard=True,
        )
    )

    return ASK_COMMENT


async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if is_cancel_button(text) or is_menu_button(text):
        return await cancel_conv(update, context)

    comment = "" if text == "Пропустить" else sanitize_string(text, 300)

    cart = context.user_data.get("cart", [])

    if not cart:
        await update.message.reply_text("❌ Корзина пуста.")
        return ConversationHandler.END

    async with storage_lock:
        products = load_products()

        for item in cart:
            product = next((p for p in products if p["id"] == item["id"]), None)

            if not product:
                await update.message.reply_text(
                    f"❌ Товар {item['name']} больше недоступен."
                )
                return ConversationHandler.END

            if product["stock"] < item["quantity"]:
                await update.message.reply_text(
                    f"❌ Недостаточно товара: {item['name']}"
                )
                return ConversationHandler.END

        for item in cart:
            for p in products:
                if p["id"] == item["id"]:
                    p["stock"] -= item["quantity"]

        save_products(products)

        orders = load_orders()

        total = sum(i["price"] * i["quantity"] for i in cart)

        order = {
            "id": next_order_id(),
            "client_name": context.user_data["client_name"],
            "phone": context.user_data["phone"],
            "comment": comment,
            "items": cart,
            "total": total,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

        orders.append(order)
        save_orders(orders)

    try:
        await context.bot.send_message(
            GROUP_CHAT_ID,
            format_order_message(order),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        log.error("send order failed: %s", e)

    await update.message.reply_text(
        f"✅ Заказ №{order['id']} оформлен!",
        reply_markup=get_reply_markup_for_user(update.effective_user.id),
    )

    context.user_data["cart"] = []

    for key in [
        "client_name",
        "phone",
        "adding_product_id",
    ]:
        context.user_data.pop(key, None)

    return ConversationHandler.END


# =========================================================
# ADMIN MANAGERS
# =========================================================


async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите Telegram ID менеджера:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        )
    )

    return ADD_ADMIN_ID


async def add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if is_cancel_button(text):
        return await cancel_conv(update, context)

    try:
        new_id = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите числовой ID")
        return ADD_ADMIN_ID

    admins = load_admins()

    if new_id in admins:
        await update.message.reply_text(
            "⚠️ Уже является менеджером.",
            reply_markup=admin_menu(),
        )
        return ConversationHandler.END

    admins.append(new_id)
    save_admins(admins)

    await update.message.reply_text(
        "✅ Менеджер добавлен.",
        reply_markup=admin_menu(),
    )

    return ConversationHandler.END


# =========================================================
# ADMIN CATEGORIES
# =========================================================


async def new_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите название категории:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        )
    )

    return NEW_CATEGORY_NAME


async def new_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_cancel_button(text):
        return await cancel_conv(update, context)

    if not text:
        await update.message.reply_text("❌ Введите название.")
        return NEW_CATEGORY_NAME

    if len(text) > 50:
        await update.message.reply_text("❌ Слишком длинное название.")
        return NEW_CATEGORY_NAME

    cats = load_categories()

    if text in cats:
        await update.message.reply_text("❌ Такая категория уже существует.")
        return ConversationHandler.END

    cats.append(text)
    save_categories(cats)

    await update.message.reply_text(
        "✅ Категория создана.",
        reply_markup=admin_menu(),
    )

    return ConversationHandler.END


async def show_manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    categories = get_categories()

    kb = [
        [InlineKeyboardButton(cat, callback_data=f"cat_manage|{cat}")]
        for cat in categories
    ]

    if query:
        await safe_edit_message(
            query,
            "📂 Управление категориями:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await update.message.reply_text(
            "📂 Управление категориями:",
            reply_markup=InlineKeyboardMarkup(kb),
        )


async def category_manage_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat = query.data.split("|", 1)[1]

    kb = [
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"del_cat|{cat}")],
        [InlineKeyboardButton("✏️ Переименовать", callback_data=f"rename_cat|{cat}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_cat_list")],
    ]

    await safe_edit_message(
        query,
        f"Категория: {escape(cat)}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML,
    )


async def rename_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    old_name = query.data.split("|", 1)[1]

    context.user_data["rename_old_cat"] = old_name

    await safe_edit_message(
        query,
        f"Введите новое имя для '{escape(old_name)}'",
        parse_mode=ParseMode.HTML,
    )

    return RENAME_CATEGORY_NAME


async def rename_category_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()

    if is_cancel_button(new_name):
        return await cancel_conv(update, context)

    old_name = context.user_data.get("rename_old_cat")

    if not old_name:
        await update.message.reply_text("❌ Ошибка состояния.")
        return ConversationHandler.END

    cats = load_categories()

    if new_name in cats:
        await update.message.reply_text("❌ Такое имя уже существует.")
        return ConversationHandler.END

    if old_name in cats:
        cats.remove(old_name)
        cats.append(new_name)
        save_categories(cats)

    products = load_products()

    for p in products:
        if p.get("category") == old_name:
            p["category"] = new_name

    save_products(products)

    context.user_data.pop("rename_old_cat", None)

    await update.message.reply_text(
        "✅ Категория переименована.",
        reply_markup=admin_menu(),
    )

    return ConversationHandler.END


async def delete_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat = query.data.split("|", 1)[1]

    cats = load_categories()

    if cat in cats:
        cats.remove(cat)
        save_categories(cats)

    products = load_products()

    for p in products:
        if p.get("category") == cat:
            p["category"] = ""

    save_products(products)

    await safe_edit_message(query, "✅ Категория удалена.")


# =========================================================
# ADMIN PRODUCTS
# =========================================================


async def add_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите название товара:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        )
    )

    return ADD_PRODUCT_NAME


async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_cancel_button(text):
        return await cancel_conv(update, context)

    if not text:
        await update.message.reply_text("❌ Введите название.")
        return ADD_PRODUCT_NAME

    context.user_data["new_product"] = {
        "name": sanitize_string(text, 100)
    }

    await update.message.reply_text("Введите описание:")

    return ADD_PRODUCT_DESC


async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_cancel_button(text):
        return await cancel_conv(update, context)

    context.user_data["new_product"]["description"] = sanitize_string(text, 800)

    await update.message.reply_text("Введите цену:")

    return ADD_PRODUCT_PRICE


async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_cancel_button(text):
        return await cancel_conv(update, context)

    try:
        price = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введите число.")
        return ADD_PRODUCT_PRICE

    if price < 0:
        await update.message.reply_text("❌ Цена не может быть отрицательной.")
        return ADD_PRODUCT_PRICE

    context.user_data["new_product"]["price"] = price

    await update.message.reply_text("Введите остаток:")

    return ADD_PRODUCT_STOCK


async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_cancel_button(text):
        return await cancel_conv(update, context)

    try:
        stock = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите целое число.")
        return ADD_PRODUCT_STOCK

    if stock < 0:
        await update.message.reply_text("❌ Остаток не может быть отрицательным.")
        return ADD_PRODUCT_STOCK

    context.user_data["new_product"]["stock"] = stock

    categories = get_categories()

    if not categories:
        await update.message.reply_text(
            "❌ Сначала создайте категорию.",
            reply_markup=admin_menu(),
        )
        return ConversationHandler.END

    kb = [
        [InlineKeyboardButton(cat, callback_data=f"cat_prod|{cat}")]
        for cat in categories
    ]

    await update.message.reply_text(
        "Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

    return ADD_PRODUCT_CATEGORY


async def add_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category = query.data.split("|", 1)[1]

    context.user_data["new_product"]["category"] = category

    await query.message.reply_text(
        "Отправьте фото или нажмите Пропустить",
        reply_markup=ReplyKeyboardMarkup(
            [["Пропустить"], ["Отмена"]],
            resize_keyboard=True,
        )
    )

    return ADD_PRODUCT_PHOTO


async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        if is_cancel_button(update.message.text):
            return await cancel_conv(update, context)

        if is_menu_button(update.message.text) and update.message.text != "Пропустить":
            return await cancel_conv(update, context)

    product = context.user_data.get("new_product")

    if not product:
        await update.message.reply_text("❌ Ошибка состояния.")
        return ConversationHandler.END

    product["id"] = next_product_id()

    if update.message.photo:
        ph = update.message.photo[-1]
        file = await ph.get_file()

        filename = f"product_{product['id']}.jpg"

        await file.download_to_drive(
            os.path.join(PHOTOS_DIR, filename)
        )

        product["photo"] = filename
    else:
        product["photo"] = ""

    products = load_products()
    products.append(product)
    save_products(products)

    context.user_data.pop("new_product", None)

    await update.message.reply_text(
        f"✅ Товар добавлен. ID: {product['id']}",
        reply_markup=admin_menu(),
    )

    return ConversationHandler.END


async def list_products_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = load_products()

    if not products:
        await update.message.reply_text(
            "📦 Товаров нет.",
            reply_markup=admin_menu(),
        )
        return

    lines = ["📋 <b>Товары:</b>\n"]

    for p in products:
        lines.append(
            f"ID {p['id']} | {sanitize_string(p['name'], 40)} | "
            f"{p['price']:,.0f}₽ | Остаток: {p['stock']}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu(),
    )


async def delete_product_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите ID товара:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        )
    )

    return DELETE_PRODUCT_ID


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_cancel_button(text):
        return await cancel_conv(update, context)

    try:
        pid = int(text)
    except ValueError:
        await update.message.reply_text("❌ Введите ID числом.")
        return DELETE_PRODUCT_ID

    product = get_product_by_id(pid)

    if not product:
        await update.message.reply_text("❌ Товар не найден.")
        return ConversationHandler.END

    if product.get("photo"):
        path = os.path.join(PHOTOS_DIR, product["photo"])

        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    products = [p for p in load_products() if p["id"] != pid]

    save_products(products)

    await update.message.reply_text(
        "✅ Товар удален.",
        reply_markup=admin_menu(),
    )

    return ConversationHandler.END


# =========================================================
# ADMIN ORDERS
# =========================================================


async def show_orders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = load_orders()

    if not orders:
        await update.message.reply_text(
            "📋 Заказов пока нет.",
            reply_markup=admin_menu(),
        )
        return

    lines = ["📋 <b>Последние заказы:</b>\n"]

    for order in orders[-10:]:
        lines.append(
            f"#{order['id']} | {sanitize_string(order['client_name'], 30)} | "
            f"{order['total']:,.0f}₽ | {order['created_at']}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu(),
    )


# =========================================================
# MAIN MESSAGE HANDLER
# =========================================================


async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    user_id = update.effective_user.id
    admin = is_admin(user_id)

    if text == "📦 Каталог":
        await show_categories(update, context)
        return

    if text == "🛒 Корзина":
        await view_cart(update, context)
        return

    if text == "🔙 Выйти":
        await start(update, context)
        return

    if admin:
        if text == "📦 Управление товарами":
            await list_products_admin(update, context)
            return

        if text == "📂 Управление категориями":
            await show_manage_categories(update, context)
            return

        if text == "📋 Заказы":
            await show_orders_list(update, context)
            return

    await update.message.reply_text(
        "Используйте кнопки меню.",
        reply_markup=get_reply_markup_for_user(user_id),
    )


# =========================================================
# MAIN
# =========================================================


def main():
    init_storage()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    add_product_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^➕ Добавить товар$"),
                add_product_prompt,
            )
        ],
        states={
            ADD_PRODUCT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)
            ],
            ADD_PRODUCT_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)
            ],
            ADD_PRODUCT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)
            ],
            ADD_PRODUCT_STOCK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_stock)
            ],
            ADD_PRODUCT_CATEGORY: [
                CallbackQueryHandler(add_product_category, pattern="^cat_prod\\|")
            ],
            ADD_PRODUCT_PHOTO: [
                MessageHandler(filters.PHOTO, add_product_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_photo),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^Отмена$"), cancel_conv)
        ],
        allow_reentry=True,
    )

    new_cat_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^➕ Добавить категорию$"),
                new_category_prompt,
            )
        ],
        states={
            NEW_CATEGORY_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_category_name)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^Отмена$"), cancel_conv)
        ],
        allow_reentry=True,
    )

    cart_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(nav_product, pattern="^add\\|")
        ],
        states={
            ADD_TO_CART_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_to_cart)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^Отмена$"), cancel_conv)
        ],
        allow_reentry=True,
    )

    order_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                cart_action,
                pattern="^(checkout|clear_cart|edit_cart)$",
            )
        ],
        states={
            ASK_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)
            ],
            ASK_PHONE: [
                MessageHandler(
                    filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
                    ask_phone,
                )
            ],
            ASK_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_comment)
            ],
            EDIT_CART_ITEM: [
                CallbackQueryHandler(
                    edit_cart_item,
                    pattern="^(editcart\\||back_to_cart)$",
                )
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^Отмена$"), cancel_conv)
        ],
        allow_reentry=True,
    )

    add_admin_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^👤 Добавить менеджера$"),
                start_add_admin,
            )
        ],
        states={
            ADD_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^Отмена$"), cancel_conv)
        ],
        allow_reentry=True,
    )

    delete_product_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^❌ Удалить товар$"),
                delete_product_entry,
            )
        ],
        states={
            DELETE_PRODUCT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_product)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^Отмена$"), cancel_conv)
        ],
        allow_reentry=True,
    )

    rename_cat_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                rename_category_prompt,
                pattern="^rename_cat\\|",
            )
        ],
        states={
            RENAME_CATEGORY_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    rename_category_execute,
                )
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^Отмена$"), cancel_conv)
        ],
        allow_reentry=True,
    )

    app.add_handler(add_product_conv)
    app.add_handler(new_cat_conv)
    app.add_handler(cart_conv)
    app.add_handler(order_conv)
    app.add_handler(add_admin_conv)
    app.add_handler(delete_product_conv)
    app.add_handler(rename_cat_conv)

    app.add_handler(
        CallbackQueryHandler(show_category_products, pattern="^cat\\|")
    )

    app.add_handler(
        CallbackQueryHandler(
            nav_product,
            pattern="^(nav_prev|nav_next|back_to_cats|nav_none)$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            category_manage_action,
            pattern="^cat_manage\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(delete_category, pattern="^del_cat\\|")
    )

    app.add_handler(
        CallbackQueryHandler(
            show_manage_categories,
            pattern="^back_to_cat_list$",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_all_messages,
            block=False,
        )
    )

    log.info("✅ Bot started")

    app.run_polling()


if __name__ == "__main__":
    main()
