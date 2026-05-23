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
) = range(17)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


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


def save_products(products):
    safe_save_json("products.json", products)


def save_orders(orders):
    safe_save_json("orders.json", orders)


def save_admins(admins):
    safe_save_json("admins.json", list(set(admins)))


def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


# =========================
# Домен
# =========================

def get_categories():
    products = load_products()
    cats = set()
    for p in products:
        if (cat := p.get("category")):
            cats.add(cat)
    return sorted(list(cats))


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
            ["📂 Управление подгруппами", "➕ Добавить подгруппу"],
            ["👤 Добавить менеджера", "📋 Заказы"],
            ["❌ Удалить товар", "🔙 Выйти"],
        ],
        resize_keyboard=True,
    )


def format_product_card(prod: dict, index: int, total: int):
    name = escape(str(prod.get("name", "—")))
    desc = escape(str(prod.get("description", "—")))
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
        lines.append(f"— {escape(str(item.get('name','')))} × {item.get('quantity',0)} = {item.get('price',0)*item.get('quantity',0):,.0f}₽")
    lines.append(f"\n💰 <b>Итого: {order['total']:,.0f}₽</b>")
    return "\n".join(lines)


# =========================
# Клиент
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", [])
    text = "Добро пожаловать, менеджер!" if is_admin(update.effective_user.id) else "Выберите действие:"
    reply_markup = admin_menu() if is_admin(update.effective_user.id) else main_keyboard()
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик всех текстовых сообщений."""
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
        await update.message.reply_text("Введите название товара:")
        return ADD_PRODUCT_NAME
    elif text == "➕ Добавить подгруппу" and admin:
        await update.message.reply_text("Введите название новой подгруппы:")
        return NEW_CATEGORY_NAME
    elif text == "👤 Добавить менеджера" and admin:
        await update.message.reply_text("Введите Telegram ID нового менеджера:")
        return ADD_ADMIN_ID
    elif text == "📦 Управление товарами" and admin:
        await list_products_admin(update, context)
    elif text == "📂 Управление подгруппами" and admin:
        await show_manage_categories(update, context)
    elif text == "📋 Заказы" and admin:
        await show_orders_list(update, context)
    elif text == "❌ Удалить товар" and admin:
        await update.message.reply_text("Введите ID товара для удаления:")
        return DELETE_PRODUCT_ID
    else:
        await update.message.reply_text("Используйте кнопки меню.")


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = get_categories()
    if not cats:
        await update.message.reply_text("Каталог пока пуст.", reply_markup=main_keyboard())
        return
    kb = [[InlineKeyboardButton(cat, callback_data=f"cat|{cat}")] for cat in cats]
    await update.message.reply_text("📂 Выберите подгруппу:", reply_markup=InlineKeyboardMarkup(kb))


async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split("|", 1)[1]
    products = get_products_by_category(cat)
    context.user_data["cat_products"] = products
    context.user_data["current_index"] = 0
    if not products:
        await query.edit_message_text("В этой подгруппе пока нет товаров.")
        return
    await show_product_card(update, context)


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    rows.append([InlineKeyboardButton("🔙 К подгруппам", callback_data="back_to_cats")])

    photo_path = os.path.join(PHOTOS_DIR, p.get("photo", "")) if p.get("photo") else None

    if photo_path and os.path.exists(photo_path):
        with open(photo_path, "rb") as ph:
            if update.callback_query:
                await update.callback_query.message.reply_photo(
                    photo=ph, caption=text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML
                )
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


async def nav_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await query.message.reply_text("Товар не найден.")
            return ConversationHandler.END
        context.user_data["adding_product_id"] = int(pid)
        await query.message.reply_text(
            f"Сколько штук добавить? (доступно: {product.get('stock',0)})",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ADD_TO_CART_QTY


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Отмена":
        await update.message.reply_text("Добавление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END

    try:
        qty = int(text)
    except ValueError:
        await update.message.reply_text("Введите целое число:")
        return ADD_TO_CART_QTY
    if qty <= 0:
        await update.message.reply_text("Количество должно быть больше 0:")
        return ADD_TO_CART_QTY

    pid = context.user_data.get("adding_product_id")
    product = get_product_by_id(pid)
    if not product:
        await update.message.reply_text("Товар не найден.", reply_markup=main_keyboard())
        return ConversationHandler.END
    if qty > product.get("stock", 0):
        await update.message.reply_text(f"Максимум: {product['stock']} шт.")
        return ADD_TO_CART_QTY

    cart = context.user_data.get("cart", [])
    item = next((i for i in cart if i["id"] == pid), None)
    if item:
        item["quantity"] += qty
    else:
        cart.append({"id": pid, "name": product["name"], "price": product["price"], "quantity": qty})
    context.user_data["cart"] = cart
    await update.message.reply_text(
        f"✅ {product['name']} × {qty} добавлено в корзину!",
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


# =========================
# Корзина и заказ
# =========================

async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", [])
    if not cart:
        await update.message.reply_text("🛒 Корзина пуста.", reply_markup=main_keyboard())
        return
    total = sum(i["price"] * i["quantity"] for i in cart)
    lines = ["🛒 <b>Ваша корзина:</b>\n"]
    for i, item in enumerate(cart, 1):
        lines.append(f"{i}. {escape(item['name'])} × {item['quantity']} = {item['price']*item['quantity']:,.0f}₽")
    lines.append(f"\n💰 Итого: {total:,.0f}₽")

    kb = [
        [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_cart")],
    ]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)


async def cart_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "clear_cart":
        context.user_data["cart"] = []
        await query.edit_message_text("🗑 Корзина очищена.")
        return ConversationHandler.END

    if query.data == "checkout":
        await query.edit_message_text("Оформляем заказ! 📝")
        await query.message.reply_text("Введите ваше имя:", reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True))
        return ASK_NAME

    if query.data == "edit_cart":
        cart = context.user_data.get("cart", [])
        if not cart:
            await query.edit_message_text("Корзина пуста.")
            return ConversationHandler.END
        kb = []
        for i, item in enumerate(cart, 1):
            kb.append([InlineKeyboardButton(
                f"❌ {escape(item['name'])} × {item['quantity']}",
                callback_data=f"editcart|{i-1}"
            )])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_cart_view")])
        await query.edit_message_text("Выберите товар для удаления:", reply_markup=InlineKeyboardMarkup(kb))
        return EDIT_CART_ITEM


async def edit_cart_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_cart_view":
        await view_cart(update, context)
        return ConversationHandler.END

    idx = int(query.data.split("|")[1])
    cart = context.user_data.get("cart", [])
    if 0 <= idx < len(cart):
        removed = cart.pop(idx)
        context.user_data["cart"] = cart
        await query.edit_message_text(f"🗑 {removed['name']} удалён из корзины.")
        await query.message.reply_text("Корзина обновлена.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END
    context.user_data["client_name"] = update.message.text.strip()
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)], ["Отмена"]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await update.message.reply_text("Отправьте ваш номер телефона:", reply_markup=kb)
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END

    phone = None
    if update.message.contact:
        phone = update.message.contact.phone_number.replace("+", "")
        if phone.startswith("7"):
            phone = "8" + phone[1:]
    else:
        phone = update.message.text.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if phone.startswith("+7"):
            phone = "8" + phone[2:]

    if not phone or not phone.isdigit() or not phone.startswith("8") or len(phone) != 11:
        await update.message.reply_text("❌ Номер должен начинаться с 8 и содержать 11 цифр.")
        return ASK_PHONE

    context.user_data["phone"] = phone
    kb = ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True)
    await update.message.reply_text("Комментарий к заказу (необязательно):", reply_markup=kb)
    return ASK_COMMENT


async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END

    comment = "" if update.message.text == "Пропустить" else update.message.text.strip()
    context.user_data["comment"] = comment

    cart = context.user_data.get("cart", [])
    total = sum(i["price"] * i["quantity"] for i in cart)
    order = add_order(context.user_data["client_name"], context.user_data["phone"], comment, cart, total)

    await context.bot.send_message(GROUP_CHAT_ID, format_order_message(order), parse_mode=ParseMode.HTML)
    await update.message.reply_text("✅ Заказ оформлен! Мы свяжемся с вами.", reply_markup=main_keyboard())
    context.user_data["cart"] = []
    return ConversationHandler.END


# =========================
# Админка: менеджеры
# =========================

async def add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        new_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Введите числовой ID:")
        return ADD_ADMIN_ID

    admins = load_admins()
    if new_id in admins:
        await update.message.reply_text("Этот пользователь уже менеджер.")
    else:
        admins.append(new_id)
        save_admins(admins)
        await update.message.reply_text(f"✅ Менеджер с ID {new_id} добавлен.")
    await update.message.reply_text("Меню менеджера:", reply_markup=admin_menu())
    return ConversationHandler.END


# =========================
# Админка: подгруппы
# =========================

async def new_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    name = update.message.text.strip()
    cats = get_categories()
    if name in cats:
        await update.message.reply_text("Такая подгруппа уже существует!", reply_markup=admin_menu())
        return ConversationHandler.END
    # Категория появляется, когда к ней привязан товар. Создадим фиктивный товар-заглушку.
    products = load_products()
    products.append({
        "id": next_product_id(),
        "name": f"__placeholder_{name}",
        "description": "Системная заглушка",
        "price": 0,
        "stock": 0,
        "category": name,
        "photo": ""
    })
    save_products(products)
    await update.message.reply_text(f"✅ Подгруппа '{name}' создана!", reply_markup=admin_menu())
    return ConversationHandler.END


async def show_manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    cats = get_categories()
    if not cats:
        await update.message.reply_text("Подгрупп пока нет.", reply_markup=admin_menu())
        return
    kb = []
    for cat in cats:
        kb.append([InlineKeyboardButton(cat, callback_data=f"cat_manage|{cat}")])
    kb.append([InlineKeyboardButton("🗑 Удалить пустые заглушки", callback_data="clean_placeholders")])
    await update.message.reply_text("📂 Управление подгруппами:", reply_markup=InlineKeyboardMarkup(kb))


async def category_manage_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "clean_placeholders":
        products = [p for p in load_products() if not str(p.get("name","")).startswith("__placeholder_")]
        save_products(products)
        await query.edit_message_text("✅ Заглушки удалены.")
        return

    cat = query.data.split("|", 1)[1]
    kb = [
        [InlineKeyboardButton("🗑 Удалить подгруппу", callback_data=f"del_cat|{cat}")],
        [InlineKeyboardButton("✏️ Переименовать", callback_data=f"rename_cat|{cat}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_cat_list")],
    ]
    await query.edit_message_text(f"Подгруппа: {escape(cat)}", reply_markup=InlineKeyboardMarkup(kb))


async def rename_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    old_name = query.data.split("|", 1)[1]
    context.user_data["rename_old_cat"] = old_name
    await query.edit_message_text(f"Введите новое название для '{old_name}':")
    return RENAME_CATEGORY_NAME


async def rename_category_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    old_name = context.user_data.get("rename_old_cat")
    new_name = update.message.text.strip()
    if not old_name or not new_name:
        return ConversationHandler.END

    products = load_products()
    for p in products:
        if p.get("category") == old_name:
            p["category"] = new_name
    save_products(products)
    await update.message.reply_text(f"✅ Подгруппа '{old_name}' переименована в '{new_name}'.", reply_markup=admin_menu())
    return ConversationHandler.END


async def delete_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    cat = query.data.split("|", 1)[1]
    products = [p for p in load_products() if p.get("category") != cat]
    save_products(products)
    await query.edit_message_text(f"✅ Подгруппа '{cat}' и все товары в ней удалены.")


# =========================
# Админка: товары
# =========================

async def add_product_name_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["new_product"] = {"name": update.message.text.strip()}
    await update.message.reply_text("Введите описание товара:")
    return ADD_PRODUCT_DESC


async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["new_product"]["description"] = update.message.text.strip()
    await update.message.reply_text("Введите цену (только число):")
    return ADD_PRODUCT_PRICE


async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        price = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Введите число:")
        return ADD_PRODUCT_PRICE
    context.user_data["new_product"]["price"] = price
    await update.message.reply_text("Введите количество в наличии (целое число):")
    return ADD_PRODUCT_STOCK


async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        stock = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Введите целое число:")
        return ADD_PRODUCT_STOCK
    context.user_data["new_product"]["stock"] = stock
    cats = get_categories()
    if not cats:
        await update.message.reply_text("Сначала создайте подгруппу.", reply_markup=admin_menu())
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(c, callback_data=f"cat_prod|{c}")] for c in cats]
    await update.message.reply_text("Выберите подгруппу:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_PRODUCT_CATEGORY


async def add_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    cat = query.data.split("|", 1)[1]
    context.user_data["new_product"]["category"] = cat
    await query.edit_message_text("Отправьте фото товара или нажмите «Пропустить»:")
    return ADD_PRODUCT_PHOTO


async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
    if not is_admin(update.effective_user.id):
        return
    products = load_products()
    if not products:
        await update.message.reply_text("Товаров пока нет.", reply_markup=admin_menu())
        return
    lines = ["📋 <b>Товары по подгруппам:</b>\n"]
    cats = get_categories()
    for cat in cats:
        lines.append(f"<b>{escape(cat)}</b>")
        for p in products:
            if p.get("category") == cat and not str(p.get("name","")).startswith("__placeholder_"):
                stock = p.get("stock", 0)
                color = "🟢" if stock > 0 else "🔴"
                lines.append(f"  {color} {escape(p['name'])} (ID: {p['id']}) — {p['price']:,.0f}₽, остаток: {stock}")
        lines.append("")
    await update.message.reply_text("\n".join(lines), reply_markup=admin_menu(), parse_mode=ParseMode.HTML)


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    try:
        pid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Введите числовой ID товара:")
        return DELETE_PRODUCT_ID

    product = get_product_by_id(pid)
    if not product:
        await update.message.reply_text("Товар не найден.", reply_markup=admin_menu())
        return ConversationHandler.END

    if product.get("photo"):
        path = os.path.join(PHOTOS_DIR, product["photo"])
        try:
            os.remove(path)
        except Exception as e:
            log.warning("Failed to remove photo %s: %s", path, e)

    products = [p for p in load_products() if p.get("id") != pid]
    save_products(products)
    await update.message.reply_text(f"✅ Товар '{product['name']}' удалён.", reply_markup=admin_menu())
    return ConversationHandler.END


# =========================
# Админка: заказы
# =========================

async def show_orders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    orders = load_orders()
    if not orders:
        await update.message.reply_text("Заказов пока нет.", reply_markup=admin_menu())
        return
    lines = ["📋 <b>Последние заказы:</b>\n"]
    for o in orders[-10:]:
        lines.append(f"Заказ #{o['id']}: {escape(o['client_name'])} | {o['total']:,.0f}₽ | {o['created_at']}")
    await update.message.reply_text("\n".join(lines), reply_markup=admin_menu(), parse_mode=ParseMode.HTML)


# =========================
# Запуск
# =========================

def main():
    init_storage()
    app = Application.builder().token(BOT_TOKEN).build()

    # Диалог: добавление товара
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

    # Диалог: оформление заказа
    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cart_action, pattern="^(checkout|clear_cart|edit_cart)$")],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), ask_phone)],
            ASK_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_comment)],
            EDIT_CART_ITEM: [CallbackQueryHandler(edit_cart_item, pattern="^(editcart\\||back_to_cart_view)")],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    # Диалог: добавление в корзину
    cart_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(nav_product, pattern="^add\\|")],
        states={
            ADD_TO_CART_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_to_cart)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    # Диалог: добавление менеджера
    add_admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 Добавить менеджера$"), add_admin_id)],
        states={
            ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    # Диалог: удаление товара
    delete_product_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❌ Удалить товар$"), delete_product)],
        states={
            DELETE_PRODUCT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_product)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    # Диалог: создание подгруппы
    new_cat_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить подгруппу$"), new_category_name)],
        states={
            NEW_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_category_name)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

    # Диалог: переименование подгруппы
    rename_cat_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(rename_category_prompt, pattern="^rename_cat\\|")],
        states={
            RENAME_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_category_execute)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )

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
    app.add_handler(CallbackQueryHandler(category_manage_action, pattern="^(cat_manage\\||clean_placeholders)"))
    app.add_handler(CallbackQueryHandler(delete_category, pattern="^del_cat\\|"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))

    log.info("✅ Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
