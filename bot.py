#!/usr/bin/env python3
# coding: utf-8

import os
import json
import base64
import logging
from datetime import datetime, timezone, timedelta
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
    ConversationHandler,
    filters,
)
from telegram.error import TelegramError

# =========================================================
# ENV
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found")

# =========================================================
# TIMEZONE
# =========================================================

MOSCOW_TZ = timezone(timedelta(hours=3))

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
    EDIT_CART_QTY,
    ADD_PRODUCT_NEW_CATEGORY,
) = range(14)
# =========================================================
# BUTTONS
# =========================================================

ADMIN_BUTTONS = [
    ["➕ Добавить товар", "📦 Управление товарами"],
    ["➕ Добавить категорию", "📂 Управление категориями"],
    ["👤 Добавить менеджера", "📋 Заказы"],
]

CLIENT_BUTTONS = [
    ["📦 Каталог", "🛒 Корзина"],
    ["📋 Мои заказы", "ℹ️ Инфо"],
    ["🛑 Стоп"],
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
    raw = safe_load_json("admins.json", [])
    if isinstance(raw, list):
        out = []
        for x in raw:
            try:
                out.append(int(x))
            except (ValueError, TypeError):
                pass
        return out
    return []


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


def sanitize(text: str, limit=1500):
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
    stock_text = f"📦 В наличии: {stock} шт." if stock > 0 else "❌ Нет в наличии"
    description = product.get('description', '') or '—'
    return (
        f"🏷 <b>{sanitize(product['name'], 100)}</b>\n\n"
        f"{escape(str(description))[:1500]}\n\n"
        f"💰 {product['price']:,.0f}₽\n"
        f"{stock_text}"
    )


def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)


def get_product_photo_bytes(product: dict) -> Optional[bytes]:
    if product.get("photo_base64"):
        try:
            return base64.b64decode(product["photo_base64"])
        except:
            pass
    
    if product.get("photo"):
        photo_path = os.path.join(PHOTOS_DIR, product["photo"])
        if os.path.exists(photo_path):
            with open(photo_path, "rb") as f:
                return f.read()
    
    return None


async def safe_edit_query_message(query, text, kb=None, parse_mode=None, photo_bytes=None):
    markup = InlineKeyboardMarkup(kb) if kb else None
    try:
        if query.message.photo or query.message.caption:
            if photo_bytes is None and query.message.photo:
                photo_bytes = await query.message.photo[-1].get_file()
                photo_bytes = await photo_bytes.download_as_bytearray()
            if query.message.photo:
                await query.edit_message_caption(
                    caption=text,
                    reply_markup=markup,
                    parse_mode=parse_mode,
                )
            else:
                await query.edit_message_text(
                    text,
                    reply_markup=markup,
                    parse_mode=parse_mode,
                )
        else:
            await query.edit_message_text(
                text,
                reply_markup=markup,
                parse_mode=parse_mode,
            )
    except TelegramError:
        try:
            await query.message.delete()
        except:
            pass

        if photo_bytes is not None:
            await query.message.reply_photo(
                photo=photo_bytes,
                caption=text,
                reply_markup=markup,
                parse_mode=parse_mode,
            )
        else:
            await query.message.reply_text(
                text,
                reply_markup=markup,
                parse_mode=parse_mode,
            )
            

def get_tech_category_name():
    return "📦 Без категории"


def is_hidden_category(category_name: str) -> bool:
    return category_name == get_tech_category_name()


def clear_waiting_states(context: ContextTypes.DEFAULT_TYPE):
    keys_to_clear = [
        "awaiting_rename", "awaiting_photo", "edit_field",
        "rename_old_cat", "edit_product", "new_product",
        "adding_product_id", "editing_cart_item"
    ]
    for key in keys_to_clear:
        context.user_data.pop(key, None)


def is_private_chat(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.type == "private"


# =========================================================
# COMMANDS
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    context.user_data.setdefault("cart", [])
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        text = "👋 Добро пожаловать, менеджер!"
    else:
        contact_info = (
            "📍 Пункт самовывоза: Пупкина залупкина д1\n"
            "🌐 Сайт: https://oduvan-farm.com\n"
            "📞 Телефон: +7(495)6453872"
        )
        text = f"👋 Добро пожаловать в магазин!\n\n{contact_info}"
    
    await update.message.reply_text(
        text,
        reply_markup=get_reply_markup(user_id),
        parse_mode=ParseMode.HTML,
    )


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    info_text = (
        "🤖 <b>О боте-магазине</b>\n\n"
        "Этот бот поможет вам быстро и удобно покупать товары!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🛍️ Что умеет бот:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📦 <b>Каталог товаров</b>\n"
        "• Товары разбиты по категориям\n"
        "• У каждого товара есть фото, описание и цена\n"
        "• Показывается наличие на складе\n\n"
        "🛒 <b>Корзина</b>\n"
        "• Добавляйте товары в корзину\n"
        "• Меняйте количество прямо в корзине\n"
        "• Удаляйте ненужные позиции\n"
        "• Автоматический подсчёт итоговой суммы\n\n"
        "✅ <b>Оформление заказа</b>\n"
        "• Укажите своё имя\n"
        "• Отправьте номер телефона (кнопкой или вручную)\n"
        "• Добавьте комментарий к заказу (опционально)\n\n"
        "📋 <b>Мои заказы</b>\n"
        "• Просматривайте историю своих заказов\n"
        "• Смотрите детали каждого заказа\n\n"
        "📱 <b>Удобство</b>\n"
        "• Все данные сохраняются в боте\n"
        "• Кнопки меню всегда под рукой\n"
        "• Можно вернуться к оформлению в любой момент\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📝 Как сделать заказ:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Нажмите «📦 Каталог»\n"
        "2️⃣ Выберите категорию\n"
        "3️⃣ Листайте товары стрелками ← и →\n"
        "4️⃣ Нажмите «🛒 Добавить в корзину»\n"
        "5️⃣ Укажите количество товара\n"
        "6️⃣ Перейдите в «🛒 Корзина»\n"
        "7️⃣ Проверьте заказ и нажмите «✅ Оформить»\n"
        "8️⃣ Введите имя и номер телефона\n"
        "9️⃣ Получите подтверждение заказа\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📍 Наши контакты:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏢 Самовывоз: Пупкина залупкина д1\n"
        "📞 Телефон: +7(495)6453872\n"
        "🌐 Сайт: https://oduvan-farm.com\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💚 Спасибо, что выбираете нас!"
    )
    
    await update.message.reply_text(
        info_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_reply_markup(update.effective_user.id),
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    user_id = update.effective_user.id
    context.user_data.clear()
    
    await update.message.reply_text(
        "🛑 Бот остановлен. Все ваши данные (корзина, временные данные) очищены.\n\n"
        "Чтобы начать заново, нажмите /start",
        reply_markup=get_reply_markup(user_id),
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keys_to_clear = [
        "new_product", "edit_product", "edit_field",
        "awaiting_rename", "awaiting_photo", "rename_old_cat",
        "editing_cart_item"
    ]
    for key in keys_to_clear:
        context.user_data.pop(key, None)
    
    await update.message.reply_text(
        "❌ Действие отменено.",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return ConversationHandler.END


# =========================================================
# CATEGORY
# =========================================================

async def new_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    clear_waiting_states(context)
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text(
        "📂 Введите название категории:",
        reply_markup=get_cancel_keyboard(),
    )
    return NEW_CATEGORY_NAME


async def new_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        return await cancel_action(update, context)
    if not text:
        await update.message.reply_text("❌ Введите название:")
        return NEW_CATEGORY_NAME
    if len(text) > 50:
        await update.message.reply_text("❌ Слишком длинное название (макс. 50 символов)")
        return NEW_CATEGORY_NAME
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
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    if not is_admin(update.effective_user.id):
        return
    categories = load_categories()
    if not categories:
        await update.message.reply_text("📂 Категорий пока нет")
        return
    
    kb = []
    for cat in categories:
        if is_hidden_category(cat):
            kb.append([InlineKeyboardButton(f"🔧 {cat} (техническая)", callback_data=f"managecat|{cat}")])
        else:
            kb.append([InlineKeyboardButton(cat, callback_data=f"managecat|{cat}")])
    
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
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    cat = query.data.split("|", 1)[1]
    
    if is_hidden_category(cat):
        kb = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_categories")]]
        await query.edit_message_text(
            f"🔧 Категория '{cat}' является технической и не может быть изменена или удалена.\n\n"
            f"Она нужна для товаров, у которых нет категории.",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return
    
    kb = [
        [InlineKeyboardButton("✏️ Переименовать", callback_data=f"renamecat|{cat}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"deletecat|{cat}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_categories")],
    ]
    await query.edit_message_text(
        f"📂 Управление категорией: {sanitize(cat, 50)}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML,
    )


async def rename_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        return
    
    cat = query.data.split("|", 1)[1]
    
    if is_hidden_category(cat):
        kb = [[InlineKeyboardButton("🔙 Назад", callback_data=f"managecat|{cat}")]]
        await query.edit_message_text(
            f"🔧 Техническая категория '{cat}' не может быть переименована.",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return
    
    context.user_data["rename_old_cat"] = cat
    context.user_data["awaiting_rename"] = True
    
    await query.message.reply_text(
        f"✏️ Введите новое название для категории '{cat}'\n\nИли нажмите кнопку «Отмена»:",
        reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def handle_rename_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return False
    
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
        await update.message.reply_text("❌ Ошибка: категория не найдена")
        return True
    
    if not text:
        await update.message.reply_text(
            "❌ Введите название:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
        )
        return True
    
    if len(text) > 50:
        await update.message.reply_text(
            "❌ Слишком длинное название (макс. 50 символов)\n\nВведите другое название или нажмите «Отмена»:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
        )
        return True
    
    cats = load_categories()
    if text in cats and text != old_name:
        await update.message.reply_text(
            "❌ Категория с таким названием уже существует\n\nВведите другое название или нажмите «Отмена»:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
        )
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
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    cat = query.data.split("|", 1)[1]
    
    if is_hidden_category(cat):
        kb = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_categories")]]
        await query.edit_message_text(
            f"🔧 Техническая категория '{cat}' не может быть удалена.",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return
    
    products_in_cat = len([p for p in load_products() if p.get("category") == cat])
    warning = ""
    if products_in_cat > 0:
        warning = f"\n⚠️ В категории {products_in_cat} товаров. Они будут перемещены в «{get_tech_category_name()}»."
    kb = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirmdel|{cat}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"managecat|{cat}")],
    ]
    await query.edit_message_text(
        f"🗑 Удалить категорию '{cat}'?{warning}",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def delete_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
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
    tech_category = get_tech_category_name()
    
    cats_after = load_categories()
    if tech_category not in cats_after:
        cats_after.append(tech_category)
        save_categories(cats_after)
    
    for p in products:
        if p.get("category") == cat:
            p["category"] = tech_category
    save_products(products)
    
    await query.edit_message_text(
        f"✅ Категория '{cat}' удалена. Товары перемещены в '{tech_category}'"
    )


# =========================================================
# ADD PRODUCT
# =========================================================

async def add_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    clear_waiting_states(context)
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["new_product"] = {}
    await update.message.reply_text(
        "📝 Введите название товара:",
        reply_markup=get_cancel_keyboard(),
    )
    return ADD_PRODUCT_NAME


async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        return await cancel_action(update, context)
    if not text:
        await update.message.reply_text("❌ Введите название:")
        return ADD_PRODUCT_NAME
    if len(text) > 100:
        await update.message.reply_text("❌ Слишком длинное название (макс. 100 символов)")
        return ADD_PRODUCT_NAME
    context.user_data["new_product"]["name"] = text
    await update.message.reply_text(
        "📝 Введите описание товара или нажмите «Пропустить»:",
        reply_markup=ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True),
    )
    return ADD_PRODUCT_DESC


async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        return await cancel_action(update, context)
    if text == "Пропустить":
        text = ""
    
    if len(text) > 1500:
        await update.message.reply_text(
            f"❌ Описание слишком длинное (макс. 1500 символов).\n"
            f"Сейчас: {len(text)} символов.\n\n"
            f"Пожалуйста, сократите описание или нажмите «Отмена»:",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True),
        )
        return ADD_PRODUCT_DESC
    
    context.user_data["new_product"]["description"] = text
    await update.message.reply_text(
        "💰 Введите цену товара:",
        reply_markup=get_cancel_keyboard(),
    )
    return ADD_PRODUCT_PRICE


async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        return await cancel_action(update, context)
    try:
        price = float(text.replace(",", "."))
    except:
        await update.message.reply_text("❌ Введите число")
        return ADD_PRODUCT_PRICE
    if price < 0:
        await update.message.reply_text("❌ Цена не может быть отрицательной")
        return ADD_PRODUCT_PRICE
    context.user_data["new_product"]["price"] = price
    await update.message.reply_text(
        "📦 Введите количество на складе:",
        reply_markup=get_cancel_keyboard(),
    )
    return ADD_PRODUCT_STOCK


async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END

    text = update.message.text.strip()
    if text == "Отмена":
        return await cancel_action(update, context)

    try:
        stock = int(text)
    except:
        await update.message.reply_text("❌ Введите целое число")
        return ADD_PRODUCT_STOCK

    if stock < 0:
        await update.message.reply_text("❌ Остаток не может быть отрицательным")
        return ADD_PRODUCT_STOCK

    context.user_data["new_product"]["stock"] = stock
    categories = load_categories()
    available_categories = [cat for cat in categories if not is_hidden_category(cat)]

    keyboard = []
    for cat in available_categories:
        keyboard.append([InlineKeyboardButton(cat, callback_data=f"cat|{cat}")])

    keyboard.append([InlineKeyboardButton("➕ Создать новую категорию", callback_data="create_category_from_product")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_product_creation")])

    await update.message.reply_text(
        "📂 Выберите категорию для товара:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADD_PRODUCT_CATEGORY


async def add_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()

    if query.data == "cancel_product_creation":
        try:
            await query.message.delete()
        except:
            pass
        await query.message.reply_text(
            "❌ Создание товара отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        context.user_data.pop("new_product", None)
        return ConversationHandler.END

    if query.data == "create_category_from_product":
        try:
            await query.message.delete()
        except:
            pass
        await query.message.reply_text(
            "📂 Введите название новой категории:\n\nИли нажмите «Отмена»:",
            reply_markup=get_cancel_keyboard(),
        )
        return ADD_PRODUCT_NEW_CATEGORY

    category = query.data.split("|", 1)[1]
    context.user_data["new_product"]["category"] = category

    try:
        await query.message.delete()
    except:
        pass

    await query.message.reply_text(
        "📸 Отправьте фото товара или нажмите «Пропустить»:",
        reply_markup=ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True),
    )
    return ADD_PRODUCT_PHOTO


async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    product = context.user_data.get("new_product")
    if not product:
        return ConversationHandler.END
    
    product["id"] = next_product_id()
    
    if update.message.photo:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        photo_data = await file.download_as_bytearray()
        product["photo_base64"] = base64.b64encode(photo_data).decode('utf-8')
        product["photo"] = ""
        log.info(f"✅ Photo saved as base64 for product #{product['id']}")
    else:
        product["photo_base64"] = ""
        product["photo"] = ""
    
    products = load_products()
    products.append(product)
    save_products(products)
    
    await update.message.reply_text(
        f"✅ Товар '{product['name']}' добавлен (ID: {product['id']})",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    context.user_data.pop("new_product", None)
    return ConversationHandler.END


async def save_new_category_and_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END

    text = update.message.text.strip()

    if text == "Отмена":
        categories = load_categories()
        available_categories = [cat for cat in categories if not is_hidden_category(cat)]

        keyboard = []
        for cat in available_categories:
            keyboard.append([InlineKeyboardButton(cat, callback_data=f"cat|{cat}")])
        keyboard.append([InlineKeyboardButton("➕ Создать новую категорию", callback_data="create_category_from_product")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_product_creation")])

        await update.message.reply_text(
            "📂 Выберите категорию для товара:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ADD_PRODUCT_CATEGORY

    if not text:
        await update.message.reply_text("❌ Введите название категории:")
        return ADD_PRODUCT_NEW_CATEGORY

    if len(text) > 50:
        await update.message.reply_text("❌ Слишком длинное название (макс. 50 символов)\n\nВведите другое название или нажмите «Отмена»:")
        return ADD_PRODUCT_NEW_CATEGORY

    cats = load_categories()
    if text in cats:
        await update.message.reply_text(
            f"❌ Категория '{text}' уже существует\n\nИспользуйте существующую категорию или введите другое название:",
            reply_markup=get_cancel_keyboard(),
        )
        return ADD_PRODUCT_NEW_CATEGORY

    cats.append(text)
    save_categories(cats)

    await update.message.reply_text(f"✅ Категория '{text}' создана!")
    context.user_data["new_product"]["category"] = text

    await update.message.reply_text(
        "📸 Отправьте фото товара или нажмите «Пропустить»:",
        reply_markup=ReplyKeyboardMarkup([[\"Пропустить\"], [\"Отмена\"]], resize_keyboard=True),
    )
    return ADD_PRODUCT_PHOTO


# =========================================================
# PRODUCTS ADMIN
# =========================================================

async def list_products_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    if not is_admin(update.effective_user.id):
        return
    
    categories = load_categories()
    if not categories:
        await update.message.reply_text("📂 Сначала создайте категории")
        return
    
    kb = []
    for cat in categories:
        if is_hidden_category(cat):
            kb.append([InlineKeyboardButton(f"🔧 {cat}", callback_data=f"admincat|{cat}")])
        else:
            kb.append([InlineKeyboardButton(cat, callback_data=f"admincat|{cat}")])
    
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.edit_message_text(
            "📂 Выберите категорию для управления товарами:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await update.message.reply_text(
            "📂 Выберите категорию для управления товарами:",
            reply_markup=InlineKeyboardMarkup(kb),
        )


async def show_admin_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        return
    
    cat = query.data.split("|", 1)[1]
    products = [p for p in load_products() if p["category"] == cat]
    
    # Удаляем сообщение с карточкой товара
    try:
        await query.message.delete()
    except:
        pass
    
    if not products:
        await query.message.reply_text(
            f"📦 В категории '{cat}' нет товаров",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К категориям", callback_data="back_to_admin_cats")
            ]])
        )
        return
    
    kb = []
    for p in products:
        stock = int(p.get("stock", 0))
        status = "🟢" if stock > 0 else "🔴"
        kb.append([
            InlineKeyboardButton(
                f"{status} {sanitize(p['name'], 30)} | {p['price']:,.0f}₽ | ост: {stock}",
                callback_data=f"editprod|{p['id']}"
            )
        ])
    kb.append([InlineKeyboardButton("🔙 К категориям", callback_data="back_to_admin_cats")])
    
    await query.message.reply_text(
        f"📦 Товары в категории '{cat}':",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def edit_product_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

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

    product_text = format_product(product)
    admin_info = (
        f"\n\n📋 <b>Информация для менеджера:</b>\n"
        f"ID: {product['id']}\n"
        f"Категория: {sanitize(product.get('category', '—'), 50)}"
    )
    full_text = product_text + admin_info

    current_category = product.get("category", "") or get_tech_category_name()

    kb = [
        [InlineKeyboardButton("✏️ Изменить название", callback_data="editfield|name")],
        [InlineKeyboardButton("📝 Изменить описание", callback_data="editfield|description")],
        [InlineKeyboardButton("💰 Изменить цену", callback_data="editfield|price")],
        [InlineKeyboardButton("📦 Изменить остаток", callback_data="editfield|stock")],
        [InlineKeyboardButton("📂 Изменить категорию", callback_data="editfield|category")],
        [InlineKeyboardButton("📸 Изменить фото", callback_data="editfield|photo")],
        [InlineKeyboardButton("🗑 Удалить товар", callback_data=f"deleteprod|{pid}")],
        [InlineKeyboardButton("🔙 К товарам категории", callback_data=f"admincat|{current_category}")],
    ]

    photo_bytes = get_product_photo_bytes(product)

    try:
        await query.message.delete()
    except:
        pass

    if photo_bytes:
        await query.message.reply_photo(
            photo=photo_bytes,
            caption=full_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await query.message.reply_text(
            full_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )


async def delete_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    pid = int(query.data.split("|")[1])
    product = get_product(pid)
    if not product:
        await query.edit_message_text("❌ Товар не найден")
        return

    kb = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete|{pid}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_delete|{pid}")],
    ]

    text = (
        f"🗑 Вы уверены, что хотите удалить товар «{sanitize(product['name'], 50)}»?\n\n"
        f"Это действие нельзя отменить."
    )

    if query.message.photo:
        await query.edit_message_caption(
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
        )

async def confirm_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    pid = int(query.data.split("|")[1])
    product = get_product(pid)
    if not product:
        await query.edit_message_text("❌ Товар не найден")
        return

    products = load_products()
    products = [p for p in products if p["id"] != pid]
    save_products(products)

    text = f"✅ Товар «{sanitize(product['name'], 50)}» удалён"

    if query.message.photo:
        await query.edit_message_caption(caption=text)
    else:
        await query.edit_message_text(text)


async def cancel_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    pid = int(query.data.split("|")[1])
    product = get_product(pid)
    name = product["name"] if product else f"#{pid}"

    text = f"❌ Удаление товара «{sanitize(name, 50)}» отменено"

    if query.message.photo:
        await query.edit_message_caption(caption=text)
    else:
        await query.edit_message_text(text)


async def edit_product_field_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    field = query.data.split("|")[1]
    context.user_data["edit_field"] = field
    if field in ["name", "description", "price", "stock"]:
        prompts = {
            "name": ("📝 Введите новое название:", get_cancel_keyboard()),
            "description": ("📝 Введите новое описание (можно оставить пустым):\n\nИли нажмите «Отмена»:", get_cancel_keyboard()),
            "price": ("💰 Введите новую цену:", get_cancel_keyboard()),
            "stock": ("📦 Введите новый остаток:", get_cancel_keyboard()),
        }
        text, kb = prompts[field]
        await query.message.reply_text(text, reply_markup=kb)
    elif field == "category":
        categories = load_categories()
        available_categories = [cat for cat in categories if not is_hidden_category(cat)]
        kb = [[InlineKeyboardButton(cat, callback_data=f"setcat|{cat}")] for cat in available_categories]
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data=f"editprod|{context.user_data['edit_product']['id']}")])
        await query.message.reply_text("📂 Выберите новую категорию:", reply_markup=InlineKeyboardMarkup(kb))
    elif field == "photo":
        await query.message.reply_text(
            "📸 Отправьте новое фото для товара или нажмите «Отмена»:",
            reply_markup=get_cancel_keyboard(),
        )
        context.user_data["awaiting_photo"] = True


async def handle_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return False
    
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
        if len(text) > 100:
            await update.message.reply_text("❌ Название слишком длинное (макс. 100 символов)")
            return True
        product["name"] = text
    elif field == "description":
        if len(text) > 1500:
            await update.message.reply_text(
                f"❌ Описание слишком длинное (макс. 1500 символов).\n"
                f"Сейчас: {len(text)} символов.\n\n"
                f"Пожалуйста, сократите описание или нажмите «Отмена»:",
                reply_markup=get_cancel_keyboard(),
            )
            return True
        
        product["description"] = text
    elif field == "price":
        try:
            price = float(text.replace(",", "."))
        except:
            await update.message.reply_text("❌ Введите число")
            return True
        if price < 0:
            await update.message.reply_text("❌ Цена не может быть отрицательной")
            return True
        product["price"] = price
    elif field == "stock":
        try:
            stock = int(text)
        except:
            await update.message.reply_text("❌ Введите целое число")
            return True
        if stock < 0:
            await update.message.reply_text("❌ Остаток не может быть отрицательным")
            return True
        product["stock"] = stock
    
    products = load_products()
    for i, p in enumerate(products):
        if p["id"] == product["id"]:
            if not product.get("photo_base64") and p.get("photo_base64"):
                product["photo_base64"] = p["photo_base64"]
            products[i] = product
            break
    save_products(products)
    
    context.user_data.pop("edit_field", None)
    
    product_text = format_product(product)
    admin_info = (
        f"\n\n📋 <b>Информация:</b>\n"
        f"ID: {product['id']}\n"
        f"Категория: {sanitize(product.get('category', '—'), 50)}"
    )
    
    await update.message.reply_text(
        f"✅ Товар обновлён\n\n{product_text}{admin_info}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return True


async def set_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
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
    
    product_text = format_product(product)
    admin_info = (
        f"\n\n📋 <b>Информация:</b>\n"
        f"ID: {product['id']}\n"
        f"Категория: {sanitize(product.get('category', '—'), 50)}"
    )
    
    await query.message.reply_text(
        f"✅ Категория обновлена\n\n{product_text}{admin_info}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_reply_markup(update.effective_user.id),
    )


async def handle_photo_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return False
    
    if not context.user_data.get("awaiting_photo"):
        return False
    
    product = context.user_data.get("edit_product")
    if not product:
        context.user_data.pop("awaiting_photo", None)
        return False
    
    if update.message and update.message.text and update.message.text == "Отмена":
        context.user_data.pop("awaiting_photo", None)
        context.user_data.pop("edit_field", None)
        await update.message.reply_text(
            "❌ Изменение отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return True
    
    if update.message and update.message.photo:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        photo_data = await file.download_as_bytearray()
        product["photo_base64"] = base64.b64encode(photo_data).decode('utf-8')
        product["photo"] = ""
        
        products = load_products()
        found = False
        for i, p in enumerate(products):
            if p["id"] == product["id"]:
                products[i] = product.copy()
                found = True
                break
        if not found:
            products.append(product)
        save_products(products)
        
        context.user_data.pop("awaiting_photo", None)
        context.user_data.pop("edit_field", None)
        context.user_data.pop("edit_product", None)
        
        product_text = format_product(product)
        admin_info = (
            f"\n\n📋 <b>Информация:</b>\n"
            f"ID: {product['id']}\n"
            f"Категория: {sanitize(product.get('category', '—'), 50)}"
        )
        
        photo_bytes = base64.b64decode(product["photo_base64"])
        await update.message.reply_photo(
            photo=photo_bytes,
            caption=f"✅ Фото обновлено\n\n{product_text}{admin_info}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return True
    
    await update.message.reply_text(
        "❌ Пожалуйста, отправьте фото или нажмите «Отмена»",
        reply_markup=get_cancel_keyboard(),
    )
    return True


# =========================================================
# CATALOG
# =========================================================

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    categories = load_categories()
    if not categories:
        await update.message.reply_text("📂 Категорий пока нет")
        return
    
    if not is_admin(update.effective_user.id):
        categories = [cat for cat in categories if not is_hidden_category(cat)]
    
    if not categories:
        await update.message.reply_text("📂 Категорий пока нет")
        return
    
    kb = [[InlineKeyboardButton(cat, callback_data=f"showcat|{cat}")] for cat in categories]
    await update.message.reply_text(
        "📂 Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

    query = update.callback_query
    await query.answer()
    category = query.data.split("|", 1)[1]
    products = [p for p in load_products() if p["category"] == category]

    if not products:
        await query.edit_message_text(
            "📦 В категории нет товаров",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К категориям", callback_data="back_to_cats")
            ]]),
        )
        return

    context.user_data["cat_products"] = products
    context.user_data["current_index"] = 0
    await show_product_card(update, context)


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

    index = context.user_data.get("current_index", 0)
    products = context.user_data.get("cat_products", [])
    if not products or index >= len(products):
        return

    p = products[index]
    text = format_product(p)

    rows = []

    if len(products) > 1:
        nav_buttons = []
        if index > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data="nav_prev"))
        else:
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data="nav_none"))

        nav_buttons.append(InlineKeyboardButton(f"{index + 1}/{len(products)}", callback_data="nav_none"))

        if index < len(products) - 1:
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data="nav_next"))
        else:
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data="nav_none"))

        rows.append(nav_buttons)

    if int(p["stock"]) > 0:
        rows.append([InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"addcart|{p['id']}")])

    rows.append([InlineKeyboardButton("🔙 К категориям", callback_data="back_to_cats")])

    photo_bytes = get_product_photo_bytes(p)
    query = update.callback_query

    try:
        # Если есть query, удаляем старое сообщение
        if query:
            try:
                await query.message.delete()
            except:
                pass

        # Отправляем новое сообщение
        if photo_bytes:
            if query:
                await query.message.reply_photo(
                    photo=photo_bytes,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(rows),
                )
            else:
                await update.message.reply_photo(
                    photo=photo_bytes,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(rows),
                )
        else:
            if query:
                await query.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(rows),
                )
            else:
                await update.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(rows),
                )
    except TelegramError as e:
        log.error("Failed to show product card: %s", e)


async def nav_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "nav_none":
        return

    if action == "nav_prev":
        context.user_data["current_index"] = max(0, context.user_data.get("current_index", 0) - 1)
        return await show_product_card(update, context)

    if action == "nav_next":
        products = context.user_data.get("cat_products", [])
        context.user_data["current_index"] = min(len(products) - 1, context.user_data.get("current_index", 0) + 1)
        return await show_product_card(update, context)

    if action == "back_to_cats":
        context.user_data.pop("cat_products", None)
        context.user_data.pop("current_index", None)

        categories = load_categories()
        if not categories:
            # Удаляем старое сообщение и отправляем новое
            try:
                await query.message.delete()
            except:
                pass
            await query.message.reply_text("📂 Категорий пока нет")
            return

        if not is_admin(update.effective_user.id):
            categories = [cat for cat in categories if not is_hidden_category(cat)]

        if not categories:
            try:
                await query.message.delete()
            except:
                pass
            await query.message.reply_text("📂 Категорий пока нет")
            return

        kb = [[InlineKeyboardButton(cat, callback_data=f"showcat|{cat}")] for cat in categories]
        
        # Удаляем старое сообщение и отправляем новое вместо редактирования
        try:
            await query.message.delete()
        except:
            pass
        
        await query.message.reply_text(
            "📂 Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if action.startswith("addcart|"):
        pid = int(action.split("|")[1])
        product = get_product(pid)
        if not product:
            await query.message.reply_text("❌ Товар не найден.")
            return

        context.user_data["adding_product_id"] = pid
        await query.message.reply_text(
            f"📝 Введите количество для '{sanitize(product['name'], 50)}'\n"
            f"Доступно: {product['stock']} шт.\n"
            f"Или нажмите «Отмена»:",
            reply_markup=get_cancel_keyboard(),
        )
        return ADD_TO_CART_QTY


# =========================================================
# CART
# =========================================================

async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    cart = context.user_data.get("cart", [])
    
    if not cart:
        if hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.edit_message_text("🛒 Корзина пуста")
        else:
            await update.message.reply_text("🛒 Корзина пуста")
        return
    
    total = 0
    lines = ["🛒 <b>Корзина:</b>\n"]
    warnings = []
    has_unavailable = False
    updated_cart = []
    
    for item in cart:
        product = get_product(item["id"])
        
        if not product:
            warnings.append(f"❌ Товар больше не продаётся")
            has_unavailable = True
            lines.append(f"❌ <s>Товар</s> × {item['quantity']} - ТОВАР УДАЛЁН")
            continue
        
        current_price = product["price"]
        current_name = product["name"]
        stock = int(product.get("stock", 0))
        qty = item["quantity"]
        
        if stock == 0:
            warnings.append(f"❌ {sanitize(current_name, 50)}: товар закончился")
            has_unavailable = True
            lines.append(f"❌ {sanitize(current_name, 50)} × {qty} = {current_price * qty:,.0f}₽ - <b>НЕТ В НАЛИЧИИ</b>")
            total += current_price * qty
            updated_cart.append(item)
            continue
        
        if qty > stock:
            warnings.append(f"⚠️ {sanitize(current_name, 50)}: доступно только {stock} шт. (в корзине {qty})")
            has_unavailable = True
        
        item_total = current_price * qty
        total += item_total
        lines.append(f"{sanitize(current_name, 50)} × {qty} = {item_total:,.0f}₽")
        updated_cart.append(item)
    
    context.user_data["cart"] = updated_cart
    
    lines.append(f"\n💰 Итого: {total:,.0f}₽")
    
    if warnings:
        lines.append("\n⚠️ <b>Внимание:</b>")
        lines.extend(warnings)
        lines.append("\n❌ Оформление заказа недоступно, пока есть проблемы с товарами.")
    
    kb = []
    if not has_unavailable and updated_cart:
        kb.append([InlineKeyboardButton("✅ Оформить", callback_data="checkout")])
    kb.append([InlineKeyboardButton("✏️ Редактировать", callback_data="edit_cart")])
    kb.append([InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")])
    
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )


async def add_to_cart_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        await update.message.reply_text(
            "❌ Добавление отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return ConversationHandler.END
    try:
        qty = int(text)
    except:
        await update.message.reply_text(
            "❌ Введите целое число.\nИли нажмите «Отмена»:",
            reply_markup=get_cancel_keyboard(),
        )
        return ADD_TO_CART_QTY
    pid = context.user_data["adding_product_id"]
    product = get_product(pid)
    if not product:
        await update.message.reply_text(
            "❌ Товар не найден",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return ConversationHandler.END
    if qty <= 0:
        await update.message.reply_text(
            "❌ Количество должно быть больше 0.\nВведите другое количество или нажмите «Отмена»:",
            reply_markup=get_cancel_keyboard(),
        )
        return ADD_TO_CART_QTY
    stock = int(product["stock"])
    if qty > stock:
        await update.message.reply_text(
            f"❌ Недостаточно товара на складе.\n"
            f"Доступно: {stock} шт.\n"
            f"Введите другое количество или нажмите «Отмена»:",
            reply_markup=get_cancel_keyboard(),
        )
        return ADD_TO_CART_QTY
    cart = context.user_data.setdefault("cart", [])
    existing = None
    for item in cart:
        if item["id"] == pid:
            existing = item
            break
    if existing:
        new_qty = existing["quantity"] + qty
        if new_qty > stock:
            await update.message.reply_text(
                f"❌ Недостаточно товара на складе.\n"
                f"В корзине уже {existing['quantity']} шт., доступно всего {stock} шт.\n"
                f"Можно добавить ещё {stock - existing['quantity']} шт.\n"
                f"Введите другое количество или нажмите «Отмена»:",
                reply_markup=get_cancel_keyboard(),
            )
            return ADD_TO_CART_QTY
        existing["quantity"] = new_qty
    else:
        cart.append({
            "id": pid,
            "quantity": qty,
        })
    await update.message.reply_text(
        f"✅ {sanitize(product['name'], 50)} × {qty} добавлено в корзину",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return ConversationHandler.END


# =========================================================
# CHECKOUT
# =========================================================

async def cart_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    if query.data == "clear_cart":
        context.user_data["cart"] = []
        await query.edit_message_text("🗑 Корзина очищена")
        return
    
    if query.data == "edit_cart":
        cart = context.user_data.get("cart", [])
        if not cart:
            await query.edit_message_text("🛒 Корзина пуста")
            return
        kb = []
        for i, item in enumerate(cart):
            product = get_product(item["id"])
            name = product["name"] if product else f"Товар #{item['id']}"
            kb.append([
                InlineKeyboardButton(
                    f"✏️ {sanitize(name, 25)} × {item['quantity']}",
                    callback_data=f"editcartitem|{i}"
                )
            ])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_cart")])
        await query.edit_message_text(
            "✏️ Выберите товар для редактирования:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return
    
    if query.data == "back_to_cart":
        await view_cart(update, context)
        return
    
    if query.data == "checkout":
        cart = context.user_data.get("cart", [])
        if not cart:
            await query.edit_message_text("🛒 Корзина пуста")
            return
        
        problems = []
        for item in cart:
            product = get_product(item["id"])
            if not product:
                problems.append(f"❌ Товар больше не продаётся")
            elif int(product.get("stock", 0)) == 0:
                problems.append(f"❌ Товар «{product['name']}» закончился")
            elif item["quantity"] > int(product.get("stock", 0)):
                problems.append(f"⚠️ Товара «{product['name']}» недостаточно. Доступно: {product['stock']} шт., в корзине: {item['quantity']}")
        
        if problems:
            await query.edit_message_text(
                "❌ <b>Оформление невозможно</b>\n\n" + "\n".join(problems) + "\n\n"
                "✏️ Отредактируйте корзину и попробуйте снова.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✏️ Редактировать корзину", callback_data="edit_cart")
                ]]),
            )
            return
        
        await query.edit_message_text("📝 Оформляем заказ...")
        await query.message.reply_text(
            "👤 Введите ваше имя:",
            reply_markup=get_cancel_keyboard(),
        )
        return ASK_NAME


async def edit_cart_item_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    idx = int(query.data.split("|")[1])
    cart = context.user_data.get("cart", [])
    
    if idx >= len(cart):
        await query.edit_message_text("❌ Товар не найден в корзине")
        return
    
    item = cart[idx]
    product = get_product(item["id"])
    name = product["name"] if product else f"Товар #{item['id']}"
    price = product["price"] if product else 0
    
    context.user_data["editing_cart_item"] = idx
    
    kb = [
        [InlineKeyboardButton("🔢 Изменить количество", callback_data=f"changeqty|{idx}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"removecart|{idx}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="edit_cart")],
    ]
    
    await query.edit_message_text(
        f"✏️ Редактирование:\n\n"
        f"🏷 {sanitize(name, 50)}\n"
        f"💰 {price:,.0f}₽ × {item['quantity']} = {price * item['quantity']:,.0f}₽",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML,
    )


async def remove_cart_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    idx = int(query.data.split("|")[1])
    cart = context.user_data.get("cart", [])
    
    if idx < len(cart):
        cart.pop(idx)
        context.user_data["cart"] = cart
        await query.edit_message_text(f"🗑 Товар удалён из корзины")
    else:
        await query.edit_message_text("❌ Товар не найден")


async def change_cart_qty_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    idx = int(query.data.split("|")[1])
    cart = context.user_data.get("cart", [])
    
    if idx >= len(cart):
        await query.edit_message_text("❌ Товар не найден")
        return
    
    item = cart[idx]
    product = get_product(item["id"])
    stock = int(product["stock"]) if product else 0
    name = product["name"] if product else f"Товар #{item['id']}"
    
    context.user_data["editing_cart_item"] = idx
    
    await query.edit_message_text(
        f"🔢 Введите новое количество для '{sanitize(name, 50)}'\n"
        f"Текущее: {item['quantity']} шт.\n"
        f"Доступно на складе: {stock} шт.\n"
        f"Или нажмите «Отмена»:"
    )
    return EDIT_CART_QTY


async def change_cart_qty_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    if text == "Отмена":
        context.user_data.pop("editing_cart_item", None)
        await update.message.reply_text(
            "❌ Изменение отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return ConversationHandler.END
    
    try:
        qty = int(text)
    except:
        await update.message.reply_text("❌ Введите целое число:")
        return EDIT_CART_QTY
    
    if qty <= 0:
        await update.message.reply_text("❌ Количество должно быть больше 0:")
        return EDIT_CART_QTY
    
    idx = context.user_data.get("editing_cart_item")
    cart = context.user_data.get("cart", [])
    
    if idx is None or idx >= len(cart):
        await update.message.reply_text(
            "❌ Товар не найден в корзине",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return ConversationHandler.END
    
    item = cart[idx]
    product = get_product(item["id"])
    
    if product and qty > int(product.get("stock", 0)):
        await update.message.reply_text(
            f"❌ Недостаточно товара на складе.\n"
            f"Доступно: {product['stock']} шт.\n"
            f"Введите другое количество:"
        )
        return EDIT_CART_QTY
    
    item["quantity"] = qty
    context.user_data["cart"] = cart
    context.user_data.pop("editing_cart_item", None)
    
    await update.message.reply_text(
        f"✅ Количество обновлено",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return ConversationHandler.END


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        await update.message.reply_text(
            "❌ Оформление отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return ConversationHandler.END
    if not text:
        await update.message.reply_text("❌ Введите имя:")
        return ASK_NAME
    context.user_data["client_name"] = text
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)], ["Отмена"]],
        resize_keyboard=True,
    )
    await update.message.reply_text("📞 Отправьте номер:", reply_markup=kb)
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    if update.message.text == "Отмена":
        await update.message.reply_text(
            "❌ Оформление отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return ConversationHandler.END
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
    phone = phone.replace("+", "").replace("-", "").replace("(", "").replace(")", "").replace(" ", "")
    if phone.startswith("7"):
        phone = "8" + phone[1:]
    if not phone.startswith("8"):
        phone = "8" + phone
    if not phone.isdigit() or len(phone) != 11:
        await update.message.reply_text("❌ Неверный номер")
        return ASK_PHONE
    context.user_data["phone"] = phone
    await update.message.reply_text(
        "💬 Комментарий или «Пропустить»:",
        reply_markup=ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True),
    )
    return ASK_COMMENT


async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        await update.message.reply_text(
            "❌ Оформление отменено",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return ConversationHandler.END
    if text == "Пропустить":
        text = ""
    cart = context.user_data.get("cart", [])
    total = 0
    items_copy = []
    for item in cart:
        product = get_product(item["id"])
        if product:
            total += product["price"] * item["quantity"]
            items_copy.append({
                "id": item["id"],
                "name": product["name"],
                "price": product["price"],
                "quantity": item["quantity"],
            })
    
    orders = load_orders()
    order = {
        "id": len(orders) + 1,
        "user_id": update.effective_user.id,
        "client_name": context.user_data["client_name"],
        "phone": context.user_data["phone"],
        "comment": text,
        "items": items_copy,
        "total": total,
        "created_at": datetime.now(MOSCOW_TZ).isoformat(timespec="seconds"),
    }
    
    orders.append(order)
    save_orders(orders)
    
    products = load_products()
    for cart_item in cart:
        for product in products:
            if product["id"] == cart_item["id"]:
                product["stock"] = int(product["stock"]) - cart_item["quantity"]
                break
    save_products(products)
    
    group_id = os.getenv("GROUP_CHAT_ID")
    
    if group_id:
        try:
            group_id_int = int(group_id)
            
            msg = (
                f"🛒 <b>Новый заказ #{order['id']}</b>\n\n"
                f"👤 Имя: {sanitize(order['client_name'], 50)}\n"
                f"📞 Телефон: {sanitize(order['phone'])}"
            )
            if order['comment']:
                msg += f"\n💬 Комментарий: {sanitize(order['comment'], 200)}"
            msg += "\n\n📋 <b>Состав заказа:</b>"
            for item in items_copy:
                item_total = item['price'] * item['quantity']
                msg += f"\n— {sanitize(item['name'], 50)} × {item['quantity']} = {item_total:,.0f}₽"
            msg += f"\n\n💰 <b>Итого: {total:,.0f}₽</b>"
            
            await context.bot.send_message(
                chat_id=group_id_int,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
            log.info(f"Order #{order['id']} sent to group")
            
        except Exception as e:
            log.error(f"Failed to send order to group: {e}")
    else:
        log.error("GROUP_CHAT_ID is not set!")
    
    context.user_data["cart"] = []
    await update.message.reply_text(
        f"✅ Заказ №{order['id']} оформлен! Мы свяжемся с вами.",
        reply_markup=get_reply_markup(update.effective_user.id),
    )
    return ConversationHandler.END


# =========================================================
# MY ORDERS (FOR CUSTOMERS)
# =========================================================

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    user_id = update.effective_user.id
    
    orders = load_orders()
    user_orders = [o for o in orders if o.get("user_id") == user_id]
    
    if not user_orders:
        await update.message.reply_text(
            "📋 У вас пока нет заказов.\n\n"
            "Сделайте первый заказ через «📦 Каталог»!",
            reply_markup=get_reply_markup(user_id),
        )
        return
    
    user_orders_sorted = sorted(user_orders, key=lambda x: x['id'], reverse=True)
    recent_orders = user_orders_sorted[:5]
    
    text = "📋 <b>Ваши последние заказы:</b>\n\n"
    
    for o in recent_orders:
        try:
            order_date = datetime.fromisoformat(o['created_at'])
            date_str = order_date.strftime("%d.%m.%Y %H:%M")
        except:
            date_str = o.get('created_at', '—')
        
        text += f"┌ <b>Заказ #{o['id']}</b> | {date_str}\n"
        text += f"├ 💰 Итого: {o['total']:,.0f}₽\n"
        text += f"└ 📦 Товаров: {len(o.get('items', []))} шт.\n\n"
    
    text += "🔍 Чтобы увидеть детали заказа, нажмите на него ниже:"
    
    kb = []
    for o in recent_orders:
        try:
            order_date = datetime.fromisoformat(o['created_at'])
            date_short = order_date.strftime('%d.%m.%Y')
        except:
            date_short = 'дата неизвестна'
        kb.append([InlineKeyboardButton(
            f"📦 Заказ #{o['id']} от {date_short}",
            callback_data=f"myorder|{o['id']}"
        )])
    
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def my_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    order_id = int(query.data.split("|")[1])
    
    orders = load_orders()
    
    order = None
    for o in orders:
        if o['id'] == order_id and o.get('user_id') == user_id:
            order = o
            break
    
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    try:
        order_date = datetime.fromisoformat(order['created_at'])
        date_str = order_date.strftime("%d.%m.%Y в %H:%M")
    except:
        date_str = order.get('created_at', '—')
    
    lines = [
        f"🛒 <b>Заказ #{order['id']}</b>",
        "",
        f"📅 Дата: {date_str}",
        f"👤 Имя: {sanitize(order['client_name'], 50)}",
        f"📞 Телефон: {sanitize(order['phone'])}",
    ]
    
    if order.get('comment'):
        lines.append(f"💬 Комментарий: {sanitize(order['comment'], 200)}")
    
    lines.append("")
    lines.append("📋 <b>Состав заказа:</b>")
    
    for item in order.get('items', []):
        item_total = item['price'] * item['quantity']
        lines.append(f"— {sanitize(item['name'], 50)} × {item['quantity']} = {item_total:,.0f}₽")
    
    lines.append("")
    lines.append(f"💰 <b>Итого: {order['total']:,.0f}₽</b>")
    
    # Убираем кнопку полностью, передавая None или пустую клавиатуру
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=None,  # Изменено: убираем кнопку
    )

async def back_to_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    await my_orders(update, context)


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    await query.edit_message_text(
        "👋 Вы вернулись в главное меню.",
        reply_markup=get_reply_markup(user_id),
    )

# =========================================================
# ORDERS (ADMIN)
# =========================================================

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    clear_waiting_states(context)
    if not is_admin(update.effective_user.id):
        return
    
    orders = load_orders()
    if not orders:
        if hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.edit_message_text("📋 Заказов пока нет")
        else:
            await update.message.reply_text("📋 Заказов пока нет")
        return
    
    orders_sorted = sorted(orders, key=lambda x: x['id'], reverse=True)
    
    if hasattr(update, "callback_query") and update.callback_query:
        if "|" in update.callback_query.data and update.callback_query.data.startswith("orders_page|"):
            page = int(update.callback_query.data.split("|")[1])
        else:
            page = context.user_data.get("orders_page", 0)
    else:
        page = context.user_data.get("orders_page", 0)
    
    total_pages = (len(orders_sorted) + 9) // 10
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(orders_sorted))
    current_orders = orders_sorted[start_idx:end_idx]
    
    context.user_data["orders_page"] = page
    context.user_data["orders_total_pages"] = total_pages
    
    kb = []
    for o in current_orders:
        try:
            order_date = datetime.fromisoformat(o['created_at'])
            date_str = order_date.strftime("%d.%m.%Y %H:%M")
        except:
            date_str = o.get('created_at', '—')
        
        kb.append([
            InlineKeyboardButton(
                f"#{o['id']} | {sanitize(o['client_name'], 20)} | {o['total']:,.0f}₽ | {date_str}",
                callback_data=f"orderdetail|{o['id']}"
            )
        ])
    
    nav_buttons = []
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Предыдущие", callback_data=f"orders_page|{page - 1}"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Следующие ▶️", callback_data=f"orders_page|{page + 1}"))
    
    if nav_buttons:
        kb.append(nav_buttons)
    
    page_info = f"\n📄 Страница {page + 1} из {total_pages} | Всего заказов: {len(orders_sorted)}"
    
    message_text = f"📋 <b>История заказов:</b>{page_info}\n\n"
    if not current_orders:
        message_text += "❌ Заказов не найдено"
    
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.edit_message_text(
            message_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
        )
    else:
        await update.message.reply_text(
            message_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
        )


async def orders_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        return
    
    page = int(query.data.split("|")[1])
    context.user_data["orders_page"] = page
    
    await show_orders(update, context)


async def show_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return
    
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        return
    
    order_id = int(query.data.split("|")[1])
    orders = load_orders()
    
    order = None
    for o in orders:
        if o['id'] == order_id:
            order = o
            break
    
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    try:
        order_date = datetime.fromisoformat(order['created_at'])
        date_str = order_date.strftime("%d.%m.%Y в %H:%M")
    except:
        date_str = order.get('created_at', '—')
    
    lines = [
        f"🛒 <b>Заказ #{order['id']}</b>",
        "",
        f"📅 Дата: {date_str}",
        f"👤 Имя: {sanitize(order['client_name'], 50)}",
        f"📞 Телефон: {sanitize(order['phone'])}",
    ]
    
    if order.get('comment'):
        lines.append(f"💬 Комментарий: {sanitize(order['comment'], 200)}")
    
    lines.append("")
    lines.append("📋 <b>Состав заказа:</b>")
    
    for item in order.get('items', []):
        item_total = item['price'] * item['quantity']
        lines.append(f"— {sanitize(item['name'], 50)} × {item['quantity']} = {item_total:,.0f}₽")
    
    lines.append("")
    lines.append(f"💰 <b>Итого: {order['total']:,.0f}₽</b>")
    
    kb = [[InlineKeyboardButton("🔙 К списку заказов", callback_data="back_to_orders")]]
    
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


# =========================================================
# ADMINS
# =========================================================

async def add_admin_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    clear_waiting_states(context)
    
    await update.message.reply_text(
        "👤 Введите Telegram ID пользователя.\n\n"
        "❓ Не знаете ID? Узнайте у @userinfobot\n\n"
        "Или нажмите «Отмена»:",
        reply_markup=get_cancel_keyboard(),
    )
    return ADD_ADMIN_ID


async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return ConversationHandler.END
    
    text = update.message.text.strip()
    if text == "Отмена":
        return await cancel_action(update, context)
    try:
        uid = int(text)
    except:
        await update.message.reply_text(
            "❌ ID должен быть числом.\n\nПопробуйте снова или нажмите «Отмена»:",
            reply_markup=get_cancel_keyboard(),
        )
        return ADD_ADMIN_ID
    admins = load_admins()
    if uid not in admins:
        admins.append(uid)
        save_admins(admins)
        await update.message.reply_text(
            f"✅ Менеджер с ID {uid} добавлен!",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
    else:
        await update.message.reply_text(
            f"⚠️ Менеджер с ID {uid} уже существует.",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
    return ConversationHandler.END


# =========================================================
# MENU ROUTER
# =========================================================

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        return

    text = update.message.text if update.message else None

    menu_buttons = [
        "📦 Каталог", "🛒 Корзина", "📋 Мои заказы",
        "📦 Управление товарами", "📂 Управление категориями", "📋 Заказы",
        "➕ Добавить категорию", "➕ Добавить товар", "👤 Добавить менеджера",
        "ℹ️ Инфо", "🛑 Стоп"
    ]

    is_waiting = (
        context.user_data.get("awaiting_rename")
        or context.user_data.get("awaiting_photo")
        or context.user_data.get("edit_field")
        or context.user_data.get("new_product")
    )

    if is_waiting and text and text in menu_buttons:
        clear_waiting_states(context)
        await update.message.reply_text(
            "⚠️ Действие прервано. Начинаем новый процесс...",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return

    if is_waiting:
        if context.user_data.get("awaiting_rename"):
            result = await handle_rename_input(update, context)
            if result:
                return
        elif context.user_data.get("awaiting_photo"):
            result = await handle_photo_edit(update, context)
            if result:
                return
        elif context.user_data.get("edit_field"):
            result = await handle_edit_field(update, context)
            if result:
                return
        return

    if text and text.startswith('/'):
        if text == "/start":
            clear_waiting_states(context)
            return await start_command(update, context)
        if text == "/info":
            clear_waiting_states(context)
            return await info_command(update, context)
        if text == "/stop":
            clear_waiting_states(context)
            return await stop_command(update, context)
        await update.message.reply_text(
            "❌ Неизвестная команда. Используйте /start, /info или /stop",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return

    if text == "📦 Каталог":
        return await show_categories(update, context)
    if text == "🛒 Корзина":
        return await view_cart(update, context)
    if text == "📋 Мои заказы":
        return await my_orders(update, context)
    if text == "📦 Управление товарами":
        return await list_products_admin(update, context)
    if text == "📂 Управление категориями":
        return await manage_categories(update, context)
    if text == "📋 Заказы":
        return await show_orders(update, context)
    if text == "➕ Добавить категорию":
        return await new_category_prompt(update, context)
    if text == "➕ Добавить товар":
        return await add_product_prompt(update, context)
    if text == "👤 Добавить менеджера":
        return await add_admin_prompt(update, context)
    if text == "ℹ️ Инфо":
        return await info_command(update, context)
    if text == "🛑 Стоп":
        return await stop_command(update, context)

    if update.message and update.message.photo:
        await update.message.reply_text(
            "❓ Чтобы добавить фото товара, используйте меню редактирования товара",
            reply_markup=get_reply_markup(update.effective_user.id),
        )
        return

    if update.message and text:
        await update.message.reply_text(
            "Используйте кнопки меню",
            reply_markup=get_reply_markup(update.effective_user.id),
        )


# =========================================================
# MAIN
# =========================================================

def main():
    init_storage()

    import asyncio

    app = Application.builder().token(BOT_TOKEN).build()

    async def cleanup():
        await app.bot.delete_webhook(drop_pending_updates=True)
        log.info("Cleaned up webhook and pending updates")

    asyncio.get_event_loop().run_until_complete(cleanup())

    # Команды бота
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("stop", stop_command))

    # Добавить категорию
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить категорию$"), new_category_prompt)],
        states={
            NEW_CATEGORY_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_category_name)
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), cancel_action)],
        allow_reentry=True,
    ))

    # Добавить товар
    app.add_handler(ConversationHandler(
    entry_points=[
        MessageHandler(
            filters.Regex("^➕ Добавить товар$"),
            add_product_prompt
        )
    ],
    states={
        ADD_PRODUCT_NAME: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                add_product_name
            )
        ],

        ADD_PRODUCT_DESC: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                add_product_desc
            )
        ],

        ADD_PRODUCT_PRICE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                add_product_price
            )
        ],

        ADD_PRODUCT_STOCK: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                add_product_stock
            )
        ],

        ADD_PRODUCT_CATEGORY: [
            CallbackQueryHandler(
                add_product_category,
                pattern=r"^(cat\|.*|create_category_from_product|cancel_product_creation)$"
            )
        ],

        ADD_PRODUCT_NEW_CATEGORY: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                save_new_category_and_continue
            )
        ],

        ADD_PRODUCT_PHOTO: [
            MessageHandler(filters.PHOTO, add_product_photo),
            MessageHandler(
                filters.Regex("^Пропустить$"),
                add_product_photo
            ),
            MessageHandler(
                filters.Regex("^Отмена$"),
                cancel_action
            ),
        ],
    },
    fallbacks=[
        MessageHandler(
            filters.Regex("^Отмена$"),
            cancel_action
        )
    ],
    allow_reentry=True,
))

    # Корзина / оформление
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(nav_product, pattern="^addcart\\|")],
        states={
            ADD_TO_CART_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_to_cart_qty)
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), cancel_action)],
        allow_reentry=True,
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(change_cart_qty_prompt, pattern="^changeqty\\|")],
        states={
            EDIT_CART_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, change_cart_qty_execute)
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), cancel_action)],
        allow_reentry=True,
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cart_actions, pattern="^(checkout|clear_cart|edit_cart|back_to_cart)$")],
        states={
            ASK_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)
            ],
            ASK_PHONE: [
                MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), ask_phone)
            ],
            ASK_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_comment)
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), cancel_action)],
        allow_reentry=True,
    ))

    # Добавить менеджера
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 Добавить менеджера$"), add_admin_prompt)],
        states={
            ADD_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin)
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), cancel_action)],
        allow_reentry=True,
    ))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(show_products, pattern="^showcat\\|"))
    app.add_handler(CallbackQueryHandler(show_admin_category_products, pattern="^admincat\\|"))
    app.add_handler(CallbackQueryHandler(manage_category_action, pattern="^managecat\\|"))
    app.add_handler(CallbackQueryHandler(rename_category_prompt, pattern="^renamecat\\|"))
    app.add_handler(CallbackQueryHandler(delete_category_prompt, pattern="^deletecat\\|"))
    app.add_handler(CallbackQueryHandler(delete_category_confirm, pattern="^confirmdel\\|"))
    app.add_handler(CallbackQueryHandler(manage_categories, pattern="^back_to_categories$"))
    app.add_handler(CallbackQueryHandler(list_products_admin, pattern="^back_to_admin_cats$"))
    app.add_handler(CallbackQueryHandler(edit_product_menu, pattern="^editprod\\|"))
    app.add_handler(CallbackQueryHandler(edit_product_field_prompt, pattern="^editfield\\|"))
    app.add_handler(CallbackQueryHandler(set_product_category, pattern="^setcat\\|"))
    app.add_handler(CallbackQueryHandler(nav_product, pattern="^(nav_prev|nav_next|nav_none|back_to_cats)$"))
    app.add_handler(CallbackQueryHandler(edit_cart_item_menu, pattern="^editcartitem\\|"))
    app.add_handler(CallbackQueryHandler(remove_cart_item, pattern="^removecart\\|"))
    app.add_handler(CallbackQueryHandler(cart_actions, pattern="^(checkout|clear_cart|edit_cart|back_to_cart)$"))
    app.add_handler(CallbackQueryHandler(change_cart_qty_prompt, pattern="^changeqty\\|"))
    app.add_handler(CallbackQueryHandler(my_order_detail, pattern="^myorder\\|"))
    app.add_handler(CallbackQueryHandler(show_orders, pattern="^back_to_orders$"))
    app.add_handler(CallbackQueryHandler(orders_pagination, pattern="^orders_page\\|"))
    app.add_handler(CallbackQueryHandler(delete_product_prompt, pattern=r"^deleteprod\|"))
    app.add_handler(CallbackQueryHandler(confirm_delete_product, pattern=r"^confirm_delete\|"))
    app.add_handler(CallbackQueryHandler(cancel_delete_product, pattern=r"^cancel_delete\|"))
    app.add_handler(CallbackQueryHandler(back_to_my_orders, pattern="^my_orders_back$"))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"))

    # Основной роутер
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    app.add_handler(MessageHandler(filters.PHOTO, menu_router))

    log.info("BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
