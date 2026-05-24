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
    ConversationHandler,
    filters,
)
from telegram.error import TelegramError

# =========================================================
# ENV
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found")

if not GROUP_CHAT_ID:
    raise RuntimeError("GROUP_CHAT_ID not found")

GROUP_CHAT_ID = int(GROUP_CHAT_ID)

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger(__name__)

# =========================================================
# DIRS
# =========================================================

DATA_DIR = "data"
PHOTOS_DIR = "photos"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)

# =========================================================
# STATES
# =========================================================

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

    ADD_ADMIN_ID,

    DELETE_PRODUCT_ID,

    EDIT_CART_ITEM,
) = range(14)

# =========================================================
# BUTTONS
# =========================================================

ADMIN_BUTTONS = [
    ["➕ Добавить товар", "📦 Управление товарами"],
    ["➕ Добавить категорию", "📂 Управление категориями"],
    ["👤 Добавить менеджера", "📋 Заказы"],
    ["🔙 Выйти"],
]

CLIENT_BUTTONS = [
    ["📦 Каталог", "🛒 Корзина"]
]

# =========================================================
# HELPERS
# =========================================================

def data_path(name: str):
    return os.path.join(DATA_DIR, name)

def safe_load_json(filename, default):
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
        except:
            decoded = base64.b64decode(content).decode("utf-8")
            return json.loads(decoded)

    except Exception as e:
        log.error("load json error %s", e)
        return default

def safe_save_json(filename, data):
    path = data_path(filename)

    raw = json.dumps(data, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(raw.encode()).decode()

    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        f.write(encoded)

    os.replace(tmp, path)

def init_storage():
    defaults = {
        "products.json": [],
        "orders.json": [],
        "admins.json": [],
        "categories.json": [],
    }

    for filename, default in defaults.items():
        path = data_path(filename)

        if not os.path.exists(path):
            safe_save_json(filename, default)

# =========================================================
# STORAGE
# =========================================================

def load_products():
    return safe_load_json("products.json", [])

def save_products(data):
    safe_save_json("products.json", data)

def load_orders():
    return safe_load_json("orders.json", [])

def save_orders(data):
    safe_save_json("orders.json", data)

def load_admins():
    return safe_load_json("admins.json", [])

def save_admins(data):
    safe_save_json("admins.json", list(set(data)))

def load_categories():
    return safe_load_json("categories.json", [])

def save_categories(data):
    safe_save_json("categories.json", list(set(data)))

# =========================================================
# DOMAIN
# =========================================================

def is_admin(user_id: int):
    return user_id in load_admins()

def sanitize(text: str, limit=300):
    return escape(str(text))[:limit]

def get_reply_markup(user_id: int):
    if is_admin(user_id):
        return ReplyKeyboardMarkup(ADMIN_BUTTONS, resize_keyboard=True)

    return ReplyKeyboardMarkup(CLIENT_BUTTONS, resize_keyboard=True)

def get_product(product_id) -> Optional[dict]:
    try:
        product_id = int(product_id)
    except:
        return None

    for p in load_products():
        if p["id"] == product_id:
            return p

    return None

def next_product_id():
    products = load_products()

    if not products:
        return 1

    return max(p["id"] for p in products) + 1

def format_product(product):
    stock = int(product["stock"])

    stock_text = (
        f"📦 В наличии: {stock}"
        if stock > 0
        else "❌ Нет в наличии"
    )

    return (
        f"🏷 <b>{sanitize(product['name'])}</b>\n\n"
        f"{sanitize(product.get('description', '') or '—')}\n\n"
        f"💰 {product['price']:,.0f}₽\n"
        f"{stock_text}"
    )

# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", [])

    await update.message.reply_text(
        "👋 Добро пожаловать!",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

# =========================================================
# CANCEL
# =========================================================

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_product", None)
    context.user_data.pop("edit_product", None)

    await update.message.reply_text(
        "❌ Действие отменено.",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

    return ConversationHandler.END

# =========================================================
# CATEGORY
# =========================================================

async def new_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    await update.message.reply_text(
        "📂 Введите название категории:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return NEW_CATEGORY_NAME

async def new_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    cats = load_categories()

    if text in cats:
        await update.message.reply_text("❌ Категория уже существует")
        return NEW_CATEGORY_NAME

    cats.append(text)

    save_categories(cats)

    await update.message.reply_text(
        f"✅ Категория '{text}' создана",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

    return ConversationHandler.END

# =========================================================
# MANAGE CATEGORIES
# =========================================================

async def manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    categories = load_categories()

    if not categories:
        await update.message.reply_text("📂 Категорий пока нет")
        return

    kb = [
        [InlineKeyboardButton(cat, callback_data=f"managecat|{cat}")]
        for cat in categories
    ]

    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.edit_message_text(
            "📂 Выберите категорию для управления:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await update.message.reply_text(
            "📂 Выберите категорию для управления:",
            reply_markup=InlineKeyboardMarkup(kb),
        )


async def manage_category_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    cat = query.data.split("|", 1)[1]

    kb = [
        [InlineKeyboardButton("✏️ Переименовать", callback_data=f"renamecat|{cat}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"deletecat|{cat}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_categories")],
    ]

    await query.edit_message_text(
        f"📂 Управление категорией: {sanitize(cat)}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML,
    )


async def rename_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    cat = query.data.split("|", 1)[1]
    
    context.user_data["rename_old_cat"] = cat
    context.user_data["awaiting_rename"] = True

    await query.edit_message_text(
        f"✏️ Введите новое название для категории '{cat}'\n"
        f"Или нажмите «Отмена»:"
    )


async def handle_rename_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_rename"):
        return False
    
    if not is_admin(update.effective_user.id):
        context.user_data.pop("awaiting_rename", None)
        context.user_data.pop("rename_old_cat", None)
        return False

    text = update.message.text.strip()

    if text == "Отмена":
        context.user_data.pop("awaiting_rename", None)
        context.user_data.pop("rename_old_cat", None)
        await update.message.reply_text(
            "❌ Переименование отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return True

    old_name = context.user_data.get("rename_old_cat")

    if not old_name:
        context.user_data.pop("awaiting_rename", None)
        return False

    if not text:
        await update.message.reply_text("❌ Введите название:")
        return True

    if len(text) > 50:
        await update.message.reply_text("❌ Слишком длинное название (макс. 50 символов)")
        return True

    cats = load_categories()

    if text in cats and text != old_name:
        await update.message.reply_text("❌ Категория с таким названием уже существует")
        return True

    if old_name in cats:
        cats.remove(old_name)
        cats.append(text)
        save_categories(cats)

    products = load_products()
    for p in products:
        if p.get("category") == old_name:
            p["category"] = text
    save_products(products)

    context.user_data.pop("awaiting_rename", None)
    context.user_data.pop("rename_old_cat", None)

    await update.message.reply_text(
        f"✅ Категория '{old_name}' переименована в '{text}'",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return True


async def delete_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    cat = query.data.split("|", 1)[1]

    products_in_cat = len([
        p for p in load_products()
        if p.get("category") == cat
    ])

    warning = ""
    if products_in_cat > 0:
        warning = f"\n⚠️ В категории {products_in_cat} товаров. Они останутся без категории."

    kb = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirmdel|{cat}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"managecat|{cat}")],
    ]

    await query.edit_message_text(
        f"🗑 Удалить категорию '{cat}'?{warning}",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def delete_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

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

    await query.edit_message_text(f"✅ Категория '{cat}' удалена")

# =========================================================
# ADD PRODUCT
# =========================================================

async def add_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    context.user_data["new_product"] = {}

    await update.message.reply_text(
        "📝 Введите название товара:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ADD_PRODUCT_NAME

async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    context.user_data["new_product"]["name"] = text

    await update.message.reply_text(
        "📝 Введите описание товара или нажмите «Пропустить»:",
        reply_markup=ReplyKeyboardMarkup(
            [["Пропустить"], ["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ADD_PRODUCT_DESC

async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    if text == "Пропустить":
        text = ""

    context.user_data["new_product"]["description"] = text

    await update.message.reply_text(
        "💰 Введите цену товара:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ADD_PRODUCT_PRICE

async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    try:
        price = float(text.replace(",", "."))
    except:
        await update.message.reply_text("❌ Введите число")
        return ADD_PRODUCT_PRICE

    context.user_data["new_product"]["price"] = price

    await update.message.reply_text(
        "📦 Введите количество на складе:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ADD_PRODUCT_STOCK

async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    try:
        stock = int(text)
    except:
        await update.message.reply_text("❌ Введите целое число")
        return ADD_PRODUCT_STOCK

    context.user_data["new_product"]["stock"] = stock

    categories = load_categories()

    if not categories:
        await update.message.reply_text(
            "❌ Нет категорий",
            reply_markup=get_reply_markup(update.effective_user.id),
        )

        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(cat, callback_data=f"cat|{cat}")]
        for cat in categories
    ]

    await update.message.reply_text(
        "📂 Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_PRODUCT_CATEGORY

async def add_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category = query.data.split("|", 1)[1]

    context.user_data["new_product"]["category"] = category

    await query.message.reply_text(
        "📸 Отправьте фото товара или нажмите «Пропустить»:",
        reply_markup=ReplyKeyboardMarkup(
            [["Пропустить"], ["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ADD_PRODUCT_PHOTO

async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = context.user_data.get("new_product")

    if not product:
        return ConversationHandler.END

    if update.message.text == "Отмена":
        return await cancel_conversation(update, context)

    product["id"] = next_product_id()

    if update.message.photo:
        photo = update.message.photo[-1]

        file = await photo.get_file()

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

    await update.message.reply_text(
        f"✅ Товар '{product['name']}' добавлен",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

    context.user_data.pop("new_product", None)

    return ConversationHandler.END

# =========================================================
# PRODUCTS ADMIN
# =========================================================

async def list_products_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    products = load_products()

    if not products:
        await update.message.reply_text("📦 Товаров нет.")
        return

    kb = []
    for p in products:
        kb.append([
            InlineKeyboardButton(
                f"ID {p['id']} | {sanitize(p['name'], 30)} | {p['price']:,.0f}₽",
                callback_data=f"editprod|{p['id']}"
            )
        ])

    await update.message.reply_text(
        "📦 Выберите товар для управления:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def edit_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    pid = int(query.data.split("|")[1])
    product = get_product(pid)

    if not product:
        await query.edit_message_text("❌ Товар не найден")
        return

    context.user_data["edit_product"] = product

    kb = [
        [InlineKeyboardButton("✏️ Изменить название", callback_data=f"editfield|name")],
        [InlineKeyboardButton("📝 Изменить описание", callback_data=f"editfield|description")],
        [InlineKeyboardButton("💰 Изменить цену", callback_data=f"editfield|price")],
        [InlineKeyboardButton("📦 Изменить остаток", callback_data=f"editfield|stock")],
        [InlineKeyboardButton("📂 Изменить категорию", callback_data=f"editfield|category")],
        [InlineKeyboardButton("📸 Изменить фото", callback_data=f"editfield|photo")],
        [InlineKeyboardButton("🗑 Удалить товар", callback_data=f"deleteprod|{pid}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_products")],
    ]

    await query.edit_message_text(
        f"📦 Управление товаром: {sanitize(product['name'])}\n\n"
        f"Цена: {product['price']:,.0f}₽\n"
        f"Остаток: {product['stock']}\n"
        f"Категория: {product.get('category', '—')}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML,
    )


async def edit_product_field_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    field = query.data.split("|")[1]
    context.user_data["edit_field"] = field

    prompts = {
        "name": ("📝 Введите новое название:", [["Отмена"]]),
        "description": ("📝 Введите новое описание или «Пропустить»:", [["Пропустить"], ["Отмена"]]),
        "price": ("💰 Введите новую цену:", [["Отмена"]]),
        "stock": ("📦 Введите новый остаток:", [["Отмена"]]),
    }

    if field in prompts:
        text, buttons = prompts[field]
        await query.message.reply_text(
            text,
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        )
        return
    elif field == "category":
        categories = load_categories()
        kb = [[InlineKeyboardButton(cat, callback_data=f"setcat|{cat}")] for cat in categories]
        await query.message.reply_text(
            "📂 Выберите новую категорию:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return
    elif field == "photo":
        await query.message.reply_text(
            "📸 Отправьте новое фото или «Пропустить» для удаления:",
            reply_markup=ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True),
        )
        context.user_data["awaiting_photo"] = True


async def handle_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get("edit_field")
    product = context.user_data.get("edit_product")

    if not field or not product:
        return False

    if update.message.text == "Отмена":
        context.user_data.pop("edit_field", None)
        await update.message.reply_text(
            "❌ Изменение отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return True

    text = update.message.text.strip()

    if field == "name":
        if not text:
            await update.message.reply_text("❌ Введите название:")
            return True
        product["name"] = text

    elif field == "description":
        if text == "Пропустить":
            text = ""
        product["description"] = text

    elif field == "price":
        try:
            price = float(text.replace(",", "."))
        except:
            await update.message.reply_text("❌ Введите число")
            return True
        product["price"] = price

    elif field == "stock":
        try:
            stock = int(text)
        except:
            await update.message.reply_text("❌ Введите целое число")
            return True
        product["stock"] = stock

    # Сохраняем изменения
    products = load_products()
    for i, p in enumerate(products):
        if p["id"] == product["id"]:
            products[i] = product
            break
    save_products(products)

    context.user_data.pop("edit_field", None)

    await update.message.reply_text(
        f"✅ Товар обновлён",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return True


async def set_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product = context.user_data.get("edit_product")
    if not product:
        return

    cat = query.data.split("|")[1]
    product["category"] = cat

    products = load_products()
    for i, p in enumerate(products):
        if p["id"] == product["id"]:
            products[i] = product
            break
    save_products(products)

    context.user_data.pop("edit_field", None)
    context.user_data.pop("edit_product", None)

    await query.message.reply_text(
        "✅ Категория обновлена",
        reply_markup=get_reply_markup(update.effective_user.id),
    )


async def handle_photo_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_photo"):
        return False

    product = context.user_data.get("edit_product")
    if not product:
        return False

    if update.message.text == "Отмена":
        context.user_data.pop("awaiting_photo", None)
        context.user_data.pop("edit_field", None)
        await update.message.reply_text(
            "❌ Изменение отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return True

    if update.message.photo:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        filename = f"product_{product['id']}.jpg"
        await file.download_to_drive(os.path.join(PHOTOS_DIR, filename))
        product["photo"] = filename
    elif update.message.text == "Пропустить":
        product["photo"] = ""

    products = load_products()
    for i, p in enumerate(products):
        if p["id"] == product["id"]:
            products[i] = product
            break
    save_products(products)

    context.user_data.pop("awaiting_photo", None)
    context.user_data.pop("edit_field", None)

    await update.message.reply_text(
        "✅ Фото обновлено",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return True


async def delete_product_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    pid = int(query.data.split("|")[1])
    product = get_product(pid)

    if not product:
        await query.edit_message_text("❌ Товар не найден")
        return

    if product.get("photo"):
        path = os.path.join(PHOTOS_DIR, product["photo"])
        if os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass

    products = load_products()
    products = [p for p in products if p["id"] != pid]
    save_products(products)

    await query.edit_message_text(f"✅ Товар '{product['name']}' удалён")


# =========================================================
# CATALOG
# =========================================================

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = load_categories()

    if not categories:
        await update.message.reply_text("📂 Категорий пока нет")
        return

    kb = [
        [InlineKeyboardButton(cat, callback_data=f"showcat|{cat}")]
        for cat in categories
    ]

    await update.message.reply_text(
        "📂 Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    category = query.data.split("|", 1)[1]

    products = [
        p for p in load_products()
        if p["category"] == category
    ]

    if not products:
        await query.message.reply_text("📦 В категории нет товаров")
        return

    for p in products:
        kb = []

        if int(p["stock"]) > 0:
            kb.append([
                InlineKeyboardButton(
                    "🛒 Добавить в корзину",
                    callback_data=f"addcart|{p['id']}",
                )
            ])

        photo_path = (
            os.path.join(PHOTOS_DIR, p["photo"])
            if p.get("photo")
            else None
        )

        text = format_product(p)

        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as ph:
                await query.message.reply_photo(
                    photo=ph,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb) if kb else None,
                )
        else:
            await query.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(kb) if kb else None,
            )

# =========================================================
# CART
# =========================================================

async def add_to_cart_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pid = int(query.data.split("|")[1])

    product = get_product(pid)

    if not product:
        return ConversationHandler.END

    context.user_data["adding_product_id"] = pid

    await query.message.reply_text(
        f"Введите количество (доступно {product['stock']}):",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ADD_TO_CART_QTY

async def add_to_cart_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    try:
        qty = int(text)
    except:
        await update.message.reply_text("❌ Введите число")
        return ADD_TO_CART_QTY

    pid = context.user_data["adding_product_id"]

    product = get_product(pid)

    if not product:
        return ConversationHandler.END

    if qty <= 0:
        await update.message.reply_text("❌ Количество > 0")
        return ADD_TO_CART_QTY

    if qty > int(product["stock"]):
        await update.message.reply_text("❌ Недостаточно товара")
        return ADD_TO_CART_QTY

    cart = context.user_data.setdefault("cart", [])

    cart.append({
        "id": pid,
        "name": product["name"],
        "price": product["price"],
        "quantity": qty,
    })

    await update.message.reply_text(
        "✅ Товар добавлен в корзину",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

    return ConversationHandler.END

# =========================================================
# VIEW CART
# =========================================================

async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", [])

    if not cart:
        await update.message.reply_text("🛒 Корзина пуста")
        return

    total = 0

    lines = ["🛒 <b>Корзина:</b>\n"]

    for item in cart:
        item_total = item["price"] * item["quantity"]

        total += item_total

        lines.append(
            f"{sanitize(item['name'])} × {item['quantity']} = {item_total:,.0f}₽"
        )

    lines.append(f"\n💰 Итого: {total:,.0f}₽")

    kb = [
        [InlineKeyboardButton("✅ Оформить", callback_data="checkout")]
    ]

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )

# =========================================================
# CHECKOUT
# =========================================================

async def checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get("cart", [])

    if not cart:
        return ConversationHandler.END

    await query.message.reply_text(
        "👤 Введите имя:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    context.user_data["client_name"] = text

    kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 Поделиться номером", request_contact=True)],
            ["Отмена"],
        ],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "📞 Отправьте номер:",
        reply_markup=kb,
    )

    return ASK_PHONE

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        return await cancel_conversation(update, context)

    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

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
            "❌ Неверный номер"
        )
        return ASK_PHONE

    context.user_data["phone"] = phone

    await update.message.reply_text(
        "💬 Комментарий или «Пропустить»:",
        reply_markup=ReplyKeyboardMarkup(
            [["Пропустить"], ["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ASK_COMMENT

async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    if text == "Пропустить":
        text = ""

    cart = context.user_data.get("cart", [])

    total = sum(i["price"] * i["quantity"] for i in cart)

    orders = load_orders()

    order = {
        "id": len(orders) + 1,
        "client_name": context.user_data["client_name"],
        "phone": context.user_data["phone"],
        "comment": text,
        "items": cart,
        "total": total,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    orders.append(order)

    save_orders(orders)

    msg = [
        f"🛒 <b>Заказ #{order['id']}</b>",
        f"👤 {sanitize(order['client_name'])}",
        f"📞 {sanitize(order['phone'])}",
        "",
    ]

    for item in cart:
        msg.append(
            f"{sanitize(item['name'])} × {item['quantity']}"
        )

    msg.append("")
    msg.append(f"💰 {total:,.0f}₽")

    try:
        await context.bot.send_message(
            GROUP_CHAT_ID,
            "\n".join(msg),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        log.error(e)

    context.user_data["cart"] = []

    await update.message.reply_text(
        "✅ Заказ оформлен",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

    return ConversationHandler.END

# =========================================================
# ORDERS
# =========================================================

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = load_orders()

    if not orders:
        await update.message.reply_text("📋 Заказов нет")
        return

    lines = ["📋 Заказы:\n"]

    for o in orders[-10:]:
        lines.append(
            f"#{o['id']} | "
            f"{sanitize(o['client_name'])} | "
            f"{o['total']:,.0f}₽"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )

# =========================================================
# ADMINS
# =========================================================

async def add_admin_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите Telegram ID:",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        ),
    )

    return ADD_ADMIN_ID

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Отмена":
        return await cancel_conversation(update, context)

    try:
        uid = int(text)
    except:
        await update.message.reply_text("❌ ID должен быть числом")
        return ADD_ADMIN_ID

    admins = load_admins()

    if uid not in admins:
        admins.append(uid)
        save_admins(admins)

    await update.message.reply_text(
        "✅ Менеджер добавлен",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

    return ConversationHandler.END

# =========================================================
# MENU
# =========================================================

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем режим переименования категории
    if context.user_data.get("awaiting_rename"):
        return await handle_rename_input(update, context)

    # Проверяем режим редактирования товара
    if context.user_data.get("edit_field"):
        return await handle_edit_field(update, context)

    # Проверяем режим редактирования фото
    if context.user_data.get("awaiting_photo"):
        if update.message.photo or (update.message.text and update.message.text in ["Пропустить", "Отмена"]):
            return await handle_photo_edit(update, context)

    text = update.message.text

    if text == "📦 Каталог":
        return await show_categories(update, context)

    if text == "🛒 Корзина":
        return await view_cart(update, context)

    if text == "📦 Управление товарами":
        return await list_products_admin(update, context)

    if text == "📂 Управление категориями":
        return await manage_categories(update, context)

    if text == "📋 Заказы":
        return await show_orders(update, context)

    if text == "🔙 Выйти":
        return await start(update, context)

    await update.message.reply_text(
        "Используйте кнопки меню",
        reply_markup=get_reply_markup(update.effective_user.id),
    )

# =========================================================
# MAIN
# =========================================================

def main():
    init_storage()

    app = Application.builder().token(BOT_TOKEN).build()

    # START
    app.add_handler(CommandHandler("start", start))

    # CATEGORY
    app.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex("^➕ Добавить категорию$"),
                    new_category_prompt,
                )
            ],
            states={
                NEW_CATEGORY_NAME: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        new_category_name,
                    )
                ]
            },
            fallbacks=[
                MessageHandler(
                    filters.Regex("^Отмена$"),
                    cancel_conversation,
                )
            ],
            allow_reentry=True,
        )
    )

    # ADD PRODUCT
    app.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex("^➕ Добавить товар$"),
                    add_product_prompt,
                )
            ],
            states={
                ADD_PRODUCT_NAME: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        add_product_name,
                    )
                ],

                ADD_PRODUCT_DESC: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        add_product_desc,
                    )
                ],

                ADD_PRODUCT_PRICE: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        add_product_price,
                    )
                ],

                ADD_PRODUCT_STOCK: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        add_product_stock,
                    )
                ],

                ADD_PRODUCT_CATEGORY: [
                    CallbackQueryHandler(
                        add_product_category,
                        pattern="^cat\\|",
                    )
                ],

                ADD_PRODUCT_PHOTO: [
                    MessageHandler(
                        filters.PHOTO,
                        add_product_photo,
                    ),

                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        add_product_photo,
                    ),
                ],
            },
            fallbacks=[
                MessageHandler(
                    filters.Regex("^Отмена$"),
                    cancel_conversation,
                )
            ],
            allow_reentry=True,
        )
    )

    # ADD TO CART
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    add_to_cart_start,
                    pattern="^addcart\\|",
                )
            ],
            states={
                ADD_TO_CART_QTY: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        add_to_cart_qty,
                    )
                ]
            },
            fallbacks=[
                MessageHandler(
                    filters.Regex("^Отмена$"),
                    cancel_conversation,
                )
            ],
            allow_reentry=True,
        )
    )

    # CHECKOUT
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    checkout_start,
                    pattern="^checkout$",
                )
            ],
            states={
                ASK_NAME: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        ask_name,
                    )
                ],

                ASK_PHONE: [
                    MessageHandler(
                        filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
                        ask_phone,
                    )
                ],

                ASK_COMMENT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        ask_comment,
                    )
                ],
            },
            fallbacks=[
                MessageHandler(
                    filters.Regex("^Отмена$"),
                    cancel_conversation,
                )
            ],
            allow_reentry=True,
        )
    )

    # ADD ADMIN
    app.add_handler(
        ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex("^👤 Добавить менеджера$"),
                    add_admin_prompt,
                )
            ],
            states={
                ADD_ADMIN_ID: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        add_admin,
                    )
                ]
            },
            fallbacks=[
                MessageHandler(
                    filters.Regex("^Отмена$"),
                    cancel_conversation,
                )
            ],
            allow_reentry=True,
        )
    )

    # CALLBACKS
    app.add_handler(
        CallbackQueryHandler(
            show_products,
            pattern="^showcat\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            manage_category_action,
            pattern="^managecat\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            rename_category_prompt,
            pattern="^renamecat\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_category_prompt,
            pattern="^deletecat\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_category_confirm,
            pattern="^confirmdel\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            manage_categories,
            pattern="^back_to_categories$",
        )
    )

    # Product management
    app.add_handler(
        CallbackQueryHandler(
            edit_product_menu,
            pattern="^editprod\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            edit_product_field_prompt,
            pattern="^editfield\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            set_product_category,
            pattern="^setcat\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_product_inline,
            pattern="^deleteprod\\|",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            list_products_admin,
            pattern="^back_to_products$",
        )
    )

    # MENU
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            menu_router,
        )
    )

    log.info("BOT STARTED")

    app.run_polling()

if __name__ == "__main__":
    main()
