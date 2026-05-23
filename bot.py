#!/usr/bin/env python3
# coding: utf-8

import os
import json
import base64
import logging
from datetime import datetime
from html import escape
from uuid import uuid4

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
    raise RuntimeError("BOT_TOKEN is not set in environment")
if not GROUP_CHAT_ID:
    raise RuntimeError("GROUP_CHAT_ID is not set in environment")
GROUP_CHAT_ID = int(GROUP_CHAT_ID)

DATA_DIR = "data"
PHOTOS_DIR = "photos"

CATEGORIES_FILE = "categories.json"
PRODUCTS_FILE = "products.json"
ORDERS_FILE = "orders.json"
ADMINS_FILE = "admins.json"

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
    ADD_CATEGORY_NAME,
    ADD_ADMIN_ID,
    DELETE_PRODUCT_ID,
) = range(13)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


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
        text = json.dumps(data, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
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
        CATEGORIES_FILE: [],
        PRODUCTS_FILE: [],
        ORDERS_FILE: [],
        ADMINS_FILE: [707877919],
    }
    for fn, default in defaults.items():
        path = data_path(fn)
        if not os.path.exists(path):
            safe_save_json(fn, default)
            log.info("Created %s with default", fn)


def load_categories():
    return safe_load_json(CATEGORIES_FILE, [])


def save_categories(categories):
    safe_save_json(CATEGORIES_FILE, categories)


def load_products():
    return safe_load_json(PRODUCTS_FILE, [])


def save_products(products):
    safe_save_json(PRODUCTS_FILE, products)


def load_orders():
    return safe_load_json(ORDERS_FILE, [])


def save_orders(orders):
    safe_save_json(ORDERS_FILE, orders)


def load_admins():
    admins = safe_load_json(ADMINS_FILE, [707877919])
    return admins if isinstance(admins, list) else [707877919]


def save_admins(admins):
    safe_save_json(ADMINS_FILE, admins)


def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


def get_categories():
    return sorted(set(load_categories()))


def get_products_by_category(category: str):
    return [p for p in load_products() if p.get("category") == category]


def get_product_by_id(product_id):
    try:
        pid = int(product_id)
    except Exception:
        return None
    return next((p for p in load_products() if p.get("id") == pid), None)


def next_product_id():
    products = load_products()
    if not products:
        return 1
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


def main_keyboard():
    return ReplyKeyboardMarkup([["📦 Каталог", "🛒 Корзина"]], resize_keyboard=True)


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["➕ Добавить товар", "➕ Добавить категорию"],
            ["📋 Список товаров", "❌ Удалить товар"],
            ["👤 Добавить админа", "🔙 Выйти"],
        ],
        resize_keyboard=True,
    )


def fmt_product_card(product: dict):
    name = escape(str(product.get("name", "—")))
    desc = escape(str(product.get("description", "—")))
    price = escape(str(product.get("price", "—")))
    stock = product.get("stock", 0)
    stock_text = f"📦 В наличии: {stock} шт." if stock > 0 else "❌ Нет в наличии"
    return f"🏷 <b>{name}</b>\n\n{desc}\n\n💰 Цена: {price}₽\n{stock_text}"


def fmt_order_message(order: dict):
    items_text = "\n".join(
        f"— {escape(str(i.get('name', '')))} × {i.get('quantity', 0)} = {i.get('price', 0) * i.get('quantity', 0)}₽"
        for i in order.get("items", [])
    ) or "—"
    return (
        f"🛒 <b>Новый заказ №{order['id']}</b>\n\n"
        f"👤 Имя: {escape(str(order.get('client_name', '')))}\n"
        f"📞 Телефон: {escape(str(order.get('phone', '')))}\n"
        f"💬 Комментарий: {escape(str(order.get('comment', '') or '—'))}\n\n"
        f"<b>Состав заказа:</b>\n{items_text}\n\n"
        f"💰 <b>Итого: {order.get('total', 0)}₽</b>"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", [])
    uid = update.effective_user.id
    if is_admin(uid):
        await update.message.reply_text("Добро пожаловать, администратор!", reply_markup=admin_keyboard())
    else:
        await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=main_keyboard())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_product", None)
    context.user_data.pop("adding_product_id", None)
    await update.message.reply_text("Действие отменено.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📦 Каталог":
        await show_categories(update, context)
    elif text == "🛒 Корзина":
        await view_cart(update, context)
    elif text == "🔙 Выйти":
        await start(update, context)
    else:
        # Если юзер не админ и нажал, например, случайно на кнопку админа — спокойно игнорируем
        if is_admin(update.effective_user.id) and text not in [
            "➕ Добавить товар",
            "➕ Добавить категорию",
            "👤 Добавить админа",
            "❌ Удалить товар",
        ]:
            await update.message.reply_text("Для дальнейших действий используйте команды из меню.")
        elif not is_admin(update.effective_user.id) and text not in ["📦 Каталог", "🛒 Корзина", "🔙 Выйти"]:
            await update.message.reply_text("Для дальнейших действий используйте команды из меню.")


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = get_categories()
    if not cats:
        await update.message.reply_text("Каталог пока пуст. Загляните позже!", reply_markup=main_keyboard())
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(c, callback_data=f"cat|{c}")] for c in cats]
    await update.message.reply_text("📂 Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, cat = query.data.split("|", 1)
    products = get_products_by_category(cat)
    context.user_data["cat_products"] = products
    context.user_data["current_index"] = 0
    if not products:
        await query.edit_message_text("В этой категории пока нет товаров.")
        return ConversationHandler.END
    return await show_product_card(update, context)


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    index = context.user_data.get("current_index", 0)
    products = context.user_data.get("cat_products", [])
    if not products or index < 0 or index >= len(products):
        await (update.callback_query.message if update.callback_query else update.message).reply_text(
            "Ошибка: товар не найден.", reply_markup=main_keyboard()
        )
        return ConversationHandler.END
    product = products[index]
    text = fmt_product_card(product)

    keyboard = []
    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data="nav_prev"))
    nav.append(InlineKeyboardButton(f"{index + 1}/{len(products)}", callback_data="nav_none"))
    if index < len(products) - 1:
        nav.append(InlineKeyboardButton("Вперёд ➡️", callback_data="nav_next"))
    keyboard.append(nav)
    if product.get("stock", 0) > 0:
        keyboard.append([InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"add|{product['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 К категориям", callback_data="back_to_cats")])

    photo_file = product.get("photo", "")
    photo_path = os.path.join(PHOTOS_DIR, photo_file) if photo_file else None

    if photo_path and os.path.exists(photo_path):
        with open(photo_path, "rb") as ph:
            if update.callback_query:
                await update.callback_query.message.reply_photo(
                    photo=ph,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_photo(
                    photo=ph,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML,
                )
    else:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )


async def nav_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "nav_prev":
        context.user_data["current_index"] = max(0, context.user_data.get("current_index", 0) - 1)
        return await show_product_card(update, context)
    if action == "nav_next":
        context.user_data["current_index"] = min(
            len(context.user_data.get("cat_products", [])) - 1, context.user_data.get("current_index", 0) + 1
        )
        return await show_product_card(update, context)
    if action == "back_to_cats":
        await show_categories(update, context)
        return ConversationHandler.END
    if action.startswith("add|"):
        _, pid = action.split("|", 1)
        product = get_product_by_id(pid)
        if not product:
            await query.message.reply_text("Товар не найден.", reply_markup=main_keyboard())
            return ConversationHandler.END
        context.user_data["adding_product_id"] = int(pid)
        await query.message.reply_text(
            f"Сколько штук добавить? (доступно: {product.get('stock', 0)})",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
        )
        return ADD_TO_CART_QTY
    return ConversationHandler.END


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Отмена":
        await update.message.reply_text("Добавление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END
    try:
        qty = int(text)
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число.")
        return ADD_TO_CART_QTY
    if qty <= 0:
        await update.message.reply_text("Количество должно быть больше нуля.")
        return ADD_TO_CART_QTY

    pid = context.user_data.get("adding_product_id")
    product = get_product_by_id(pid)
    if not product:
        await update.message.reply_text("Товар не найден.", reply_markup=main_keyboard())
        return ConversationHandler.END
    if qty > product.get("stock", 0):
        await update.message.reply_text(f"Недостаточно товара. В наличии: {product.get('stock', 0)} шт.")
        return ADD_TO_CART_QTY

    cart = context.user_data.get("cart", [])
    existing = next((i for i in cart if i["id"] == pid), None)
    if existing:
        existing["quantity"] += qty
    else:
        cart.append(
            {"id": pid, "name": product.get("name"), "price": product.get("price"), "quantity": qty}
        )
    context.user_data["cart"] = cart
    await update.message.reply_text(
        f"✅ {product.get('name')} × {qty} добавлено в корзину!", reply_markup=main_keyboard()
    )
    return ConversationHandler.END


async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", [])
    if not cart:
        await update.message.reply_text("🛒 Корзина пуста.", reply_markup=main_keyboard())
        return ConversationHandler.END
    total = 0
    lines = ["🛒 <b>Ваша корзина:</b>", ""]
    for i, it in enumerate(cart, 1):
        subtotal = it["price"] * it["quantity"]
        total += subtotal
        lines.append(f"{i}. {escape(str(it['name']))} × {it['quantity']} = {subtotal}₽")
    lines.append("")
    lines.append(f"💰 <b>Итого: {total}₽</b>")
    keyboard = [
        [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")],
    ]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def cart_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "clear_cart":
        context.user_data["cart"] = []
        await query.edit_message_text("🗑 Корзина очищена.")
        return ConversationHandler.END
    if query.data == "checkout":
        await query.edit_message_text("Оформляем заказ! 📝")
        await query.message.reply_text(
            "Введите ваше имя:", reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ASK_NAME
    return ConversationHandler.END


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END
    context.user_data["client_name"] = update.message.text.strip()
    kb = ReplyKeyboardMarkup(
        [["📱 Поделиться номером", KeyboardButton(request_contact=True)], ["Отмена"]],
        resize_keyboard=True,
        one_time_keyboard=True,
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
        await update.message.reply_text("❌ Номер должен начинаться с 8 и содержать 11 цифр. Попробуйте ещё раз:")
        return ASK_PHONE

    context.user_data["phone"] = phone
    await update.message.reply_text(
        "Комментарий к заказу (необязательно):",
        reply_markup=ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True),
    )
    return ASK_COMMENT


async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END
    comment = "" if update.message.text == "Пропустить" else update.message.text.strip()
    context.user_data["comment"] = comment
    cart = context.user_data.get("cart", [])
    total = sum(it["price"] * it["quantity"] for it in cart)
    order = add_order(
        context.user_data.get("client_name"),
        context.user_data.get("phone"),
        comment,
        cart,
        total,
    )
    await context.bot.send_message(GROUP_CHAT_ID, fmt_order_message(order), parse_mode=ParseMode.HTML)
    await update.message.reply_text(
        "✅ Заказ оформлен! Мы свяжемся с вами в ближайшее время.", reply_markup=main_keyboard()
    )
    context.user_data["cart"] = []
    return ConversationHandler.END


async def list_products_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = load_products()
    if not products:
        await update.message.reply_text("Товаров пока нет.", reply_markup=admin_keyboard())
        return
    lines = ["📋 <b>Список товаров:</b>", ""]
    for p in products:
        lines.append(
            f"ID: {p.get('id')} | {escape(str(p.get('name')))} | {p.get('price')}₽ | Остаток: {p.get('stock')}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())


async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    context.user_data["new_product"] = {"name": update.message.text.strip()}
    await update.message.reply_text("Введите описание товара:")
    return ADD_PRODUCT_DESC


async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    context.user_data["new_product"]["description"] = update.message.text.strip()
    await update.message.reply_text("Введите цену товара (только число):")
    return ADD_PRODUCT_PRICE


async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    try:
        price = float(update.message.text.replace(",", "."))
    except Exception:
        await update.message.reply_text("Введите корректную цену (число).")
        return ADD_PRODUCT_PRICE
    context.user_data["new_product"]["price"] = price
    await update.message.reply_text("Введите количество на складе (целое число):")
    return ADD_PRODUCT_STOCK


async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    try:
        stock = int(update.message.text)
    except Exception:
        await update.message.reply_text("Введите целое число!")
        return ADD_PRODUCT_STOCK
    context.user_data["new_product"]["stock"] = stock
    cats = get_categories()
    if not cats:
        await update.message.reply_text("Сначала создайте категорию через меню!", reply_markup=admin_keyboard())
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(c, callback_data=f"newcat|{c}")] for c in cats]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_PRODUCT_CATEGORY


async def add_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    _, cat = query.data.split("|", 1)
    context.user_data["new_product"]["category"] = cat
    await query.edit_message_text("Отправьте фото товара или нажмите 'Пропустить':")
    return ADD_PRODUCT_PHOTO


async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    new_p = context.user_data.get("new_product", {})
    products = load_products()
    nid = next_product_id()
    new_p["id"] = nid
    if update.message and update.message.photo:
        ph = update.message.photo[-1]
        file = await ph.get_file()
        filename = f"product_{nid}.jpg"
        target = os.path.join(PHOTOS_DIR, filename)
        await file.download_to_drive(target)
        new_p["photo"] = filename
    else:
        new_p["photo"] = ""
    products.append(new_p)
    save_products(products)
    await update.message.reply_text(f"✅ Товар '{new_p.get('name')}' добавлен! ID: {nid}", reply_markup=admin_keyboard())
    context.user_data.pop("new_product", None)
    return ConversationHandler.END


async def add_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    cat = update.message.text.strip()
    cats = load_categories()
    if cat in cats:
        await update.message.reply_text("Такая категория уже существует!", reply_markup=admin_keyboard())
        return ConversationHandler.END
    cats.append(cat)
    save_categories(sorted(set(cats)))
    await update.message.reply_text(f"✅ Категория '{cat}' создана!", reply_markup=admin_keyboard())
    return ConversationHandler.END


async def add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    try:
        new_id = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("Введите числовой ID!")
        return ADD_ADMIN_ID
    admins = load_admins()
    if new_id in admins:
        await update.message.reply_text("Этот пользователь уже администратор.", reply_markup=admin_keyboard())
    else:
        admins.append(new_id)
        save_admins(sorted(set(admins)))
        await update.message.reply_text(f"✅ Админ с ID {new_id} добавлен!", reply_markup=admin_keyboard())
    return ConversationHandler.END


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_keyboard())
        return ConversationHandler.END
    try:
        pid = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("Введите числовой ID товара!")
        return DELETE_PRODUCT_ID
    products = load_products()
    prod = next((p for p in products if p.get("id") == pid), None)
    if not prod:
        await update.message.reply_text("Товар с таким ID не найден.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    if prod.get("photo"):
        photo_path = os.path.join(PHOTOS_DIR, prod["photo"])
        try:
            if os.path.exists(photo_path):
                os.remove(photo_path)
        except Exception as e:
            log.warning("Failed to remove photo %s: %s", photo_path, e)
    products = [p for p in products if p.get("id") != pid]
    save_products(products)
    await update.message.reply_text(f"✅ Товар '{prod.get('name')}' удалён.", reply_markup=admin_keyboard())
    return ConversationHandler.END


def main():
    init_storage()
    app = Application.builder().token(BOT_TOKEN).build()

    add_product_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^➕ Добавить товар$") & filters.COMMAND | filters.TEXT & ~filters.COMMAND,
                           add_product_name)
        ],
        states={
            ADD_PRODUCT_DESC: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, add_product_desc
                )
            ],
            ADD_PRODUCT_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, add_product_price
                )
            ],
            ADD_PRODUCT_STOCK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, add_product_stock
                )
            ],
            ADD_PRODUCT_CATEGORY: [
                CallbackQueryHandler(add_product_category, pattern=r"^newcat\|")
            ],
            ADD_PRODUCT_PHOTO: [
                MessageHandler(filters.PHOTO, add_product_photo),
                MessageHandler(filters.Regex(r"^Пропустить$"), add_product_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cart_action, pattern="^(checkout|clear_cart)$")],
        states={
            ASK_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, ask_name
                )
            ],
            ASK_PHONE: [
                MessageHandler(
                    filters.CONTACT | (filters.TEXT & ~filters.COMMAND), ask_phone
                )
            ],
            ASK_COMMENT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, ask_comment
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    cart_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(nav_product, pattern="^add\\|")],
        states={
            ADD_TO_CART_QTY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, add_to_cart
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    add_category_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить категорию$"), add_category_name)],
        states={
            ADD_CATEGORY_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, add_category_name
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    add_admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 Добавить админа$"), add_admin_id)],
        states={
            ADD_ADMIN_ID: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, add_admin_id
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    delete_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❌ Удалить товар$"), delete_product)],
        states={
            DELETE_PRODUCT_ID: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, delete_product
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_product_conv)
    app.add_handler(order_conv)
    app.add_handler(cart_conv)
    app.add_handler(add_category_conv)
    app.add_handler(add_admin_conv)
    app.add_handler(delete_conv)

    app.add_handler(CallbackQueryHandler(show_category_products, pattern="^cat\\|"))
    app.add_handler(
        CallbackQueryHandler(
            nav_product, pattern="^(nav_prev|nav_next|nav_none|back_to_cats|add\\|)"
        )
    )
    app.add_handler(CallbackQueryHandler(cart_action, pattern="^(checkout|clear_cart)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
