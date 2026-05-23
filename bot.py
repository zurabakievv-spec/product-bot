import os
import json
import logging
from html import escape
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))

DATA_DIR = "data"
PHOTOS_DIR = "photos"

PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
CATEGORIES_FILE = os.path.join(DATA_DIR, "categories.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
ADMINS_FILE = os.path.join(DATA_DIR, "admins.json")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

(
    ADD_TO_CART_QTY,
    ASK_NAME,
    ASK_PHONE,
    ASK_COMMENT,
    CONFIRM_ORDER,
    ADD_PRODUCT_NAME,
    ADD_PRODUCT_DESC,
    ADD_PRODUCT_PRICE,
    ADD_PRODUCT_STOCK,
    ADD_PRODUCT_CATEGORY,
    ADD_PRODUCT_PHOTO,
    ADD_CATEGORY,
    DELETE_PRODUCT,
) = range(13)


# =========================================================
# STORAGE
# =========================================================


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PHOTOS_DIR, exist_ok=True)



def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default



def save_json(path, data):
    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp, path)



def init_storage():
    ensure_dirs()

    defaults = {
        PRODUCTS_FILE: [],
        CATEGORIES_FILE: [],
        ORDERS_FILE: [],
        ADMINS_FILE: [707877919],
    }

    for path, default in defaults.items():
        if not os.path.exists(path):
            save_json(path, default)


# =========================================================
# LOADERS
# =========================================================


def load_products():
    return load_json(PRODUCTS_FILE, [])



def save_products(data):
    save_json(PRODUCTS_FILE, data)



def load_categories():
    return load_json(CATEGORIES_FILE, [])



def save_categories(data):
    save_json(CATEGORIES_FILE, data)



def load_orders():
    return load_json(ORDERS_FILE, [])



def save_orders(data):
    save_json(ORDERS_FILE, data)



def load_admins():
    return load_json(ADMINS_FILE, [])


# =========================================================
# HELPERS
# =========================================================


def is_admin(user_id: int):
    return user_id in load_admins()



def next_product_id():
    products = load_products()

    if not products:
        return 1

    return max(p["id"] for p in products) + 1



def get_product(product_id):
    for p in load_products():
        if p["id"] == product_id:
            return p

    return None



def get_products_by_category(category):
    return [
        p for p in load_products()
        if p["category"] == category
    ]



def main_keyboard(user_id):
    if is_admin(user_id):
        return ReplyKeyboardMarkup(
            [
                ["📦 Каталог", "🛒 Корзина"],
                ["➕ Добавить товар", "➕ Добавить категорию"],
                ["📋 Заказы", "❌ Удалить товар"],
            ],
            resize_keyboard=True,
        )

    return ReplyKeyboardMarkup(
        [["📦 Каталог", "🛒 Корзина"]],
        resize_keyboard=True,
    )


# =========================================================
# START
# =========================================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", [])

    await update.message.reply_text(
        "Добро пожаловать!",
        reply_markup=main_keyboard(update.effective_user.id),
    )


# =========================================================
# CATALOG
# =========================================================


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = load_categories()

    if not categories:
        await update.message.reply_text("Категорий пока нет")
        return

    kb = []

    for cat in categories:
        kb.append([
            InlineKeyboardButton(
                cat,
                callback_data=f"cat|{cat}"
            )
        ])

    await update.message.reply_text(
        "Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def open_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category = query.data.split("|", 1)[1]

    products = get_products_by_category(category)

    if not products:
        await query.edit_message_text("В категории нет товаров")
        return

    context.user_data["products"] = products
    context.user_data["index"] = 0

    await render_product(query, context)


async def render_product(query, context):
    products = context.user_data["products"]
    index = context.user_data["index"]

    product = products[index]

    text = (
        f"<b>{escape(product['name'])}</b>\n\n"
        f"{escape(product['description'])}\n\n"
        f"💰 {product['price']:,.0f}₽\n"
        f"📦 Остаток: {product['stock']}"
    )

    rows = [
        [
            InlineKeyboardButton("⬅️", callback_data="prev"),
            InlineKeyboardButton(f"{index+1}/{len(products)}", callback_data="none"),
            InlineKeyboardButton("➡️", callback_data="next"),
        ]
    ]

    if product["stock"] > 0:
        rows.append([
            InlineKeyboardButton(
                "🛒 В корзину",
                callback_data=f"add|{product['id']}"
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 Категории", callback_data="back_categories")
    ])

    markup = InlineKeyboardMarkup(rows)

    photo_path = None

    if product.get("photo"):
        photo_path = os.path.join(PHOTOS_DIR, product["photo"])

    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as ph:
                await query.edit_message_media(
                    media=InputMediaPhoto(
                        media=ph,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                    ),
                    reply_markup=markup,
                )
        else:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )

    except Exception:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as ph:
                await query.message.reply_photo(
                    photo=ph,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
        else:
            await query.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )


async def navigate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    products = context.user_data.get("products", [])

    if not products:
        return

    if query.data == "next":
        context.user_data["index"] = min(
            len(products) - 1,
            context.user_data["index"] + 1,
        )

    elif query.data == "prev":
        context.user_data["index"] = max(
            0,
            context.user_data["index"] - 1,
        )

    elif query.data == "back_categories":
        categories = load_categories()

        kb = [
            [InlineKeyboardButton(c, callback_data=f"cat|{c}")]
            for c in categories
        ]

        await query.edit_message_text(
            "Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

        return

    await render_product(query, context)


# =========================================================
# CART
# =========================================================


async def add_to_cart_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split("|", 1)[1])

    product = get_product(product_id)

    if not product:
        await query.message.reply_text("Товар не найден")
        return ConversationHandler.END

    context.user_data["adding_product"] = product_id

    await query.message.reply_text(
        f"Введите количество (макс. {product['stock']}):",
        reply_markup=ReplyKeyboardMarkup(
            [["Отмена"]],
            resize_keyboard=True,
        )
    )

    return ADD_TO_CART_QTY


async def add_to_cart_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "Отмена":
        await update.message.reply_text(
            "Отменено",
            reply_markup=main_keyboard(update.effective_user.id),
        )
        return ConversationHandler.END

    try:
        qty = int(text)
    except Exception:
        await update.message.reply_text("Введите число")
        return ADD_TO_CART_QTY

    if qty <= 0:
        await update.message.reply_text("Количество должно быть больше 0")
        return ADD_TO_CART_QTY

    product_id = context.user_data["adding_product"]

    product = get_product(product_id)

    if not product:
        await update.message.reply_text("Товар не найден")
        return ConversationHandler.END

    cart = context.user_data.get("cart", [])

    existing = next(
        (i for i in cart if i["id"] == product_id),
        None,
    )

    existing_qty = existing["quantity"] if existing else 0

    if existing_qty + qty > product["stock"]:
        await update.message.reply_text(
            f"Доступно максимум {product['stock']}"
        )
        return ADD_TO_CART_QTY

    if existing:
        existing["quantity"] += qty
    else:
        cart.append({
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "quantity": qty,
        })

    context.user_data["cart"] = cart

    await update.message.reply_text(
        "✅ Добавлено в корзину",
        reply_markup=main_keyboard(update.effective_user.id),
    )

    return ConversationHandler.END


async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", [])

    if not cart:
        await update.message.reply_text("Корзина пуста")
        return

    lines = ["🛒 <b>Корзина</b>\n"]

    total = 0

    for item in cart:
        subtotal = item["price"] * item["quantity"]
        total += subtotal

        lines.append(
            f"• {escape(item['name'])} × {item['quantity']} = {subtotal:,.0f}₽"
        )

    lines.append(f"\n💰 Итого: {total:,.0f}₽")

    kb = [
        [InlineKeyboardButton("✅ Оформить", callback_data="checkout")],
        [InlineKeyboardButton("🗑 Очистить", callback_data="clear_cart")],
    ]

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cart_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "clear_cart":
        context.user_data["cart"] = []

        await query.edit_message_text("Корзина очищена")

        return ConversationHandler.END

    elif query.data == "checkout":
        await query.message.reply_text(
            "Введите имя:",
            reply_markup=ReplyKeyboardMarkup(
                [["Отмена"]],
                resize_keyboard=True,
            )
        )

        return ASK_NAME


# =========================================================
# ORDER
# =========================================================


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        return ConversationHandler.END

    context.user_data["client_name"] = update.message.text.strip()

    kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 Отправить номер", request_contact=True)],
            ["Отмена"],
        ],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "Введите номер:",
        reply_markup=kb,
    )

    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Отмена":
        return ConversationHandler.END

    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text

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

    if not phone.isdigit() or len(phone) != 11:
        await update.message.reply_text("Введите корректный номер")
        return ASK_PHONE

    context.user_data["phone"] = phone

    await update.message.reply_text(
        "Комментарий или 'Пропустить'",
        reply_markup=ReplyKeyboardMarkup(
            [["Пропустить"]],
            resize_keyboard=True,
        )
    )

    return ASK_COMMENT


async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    comment = ""

    if text != "Пропустить":
        comment = text

    context.user_data["comment"] = comment

    cart = context.user_data["cart"]

    total = sum(i["price"] * i["quantity"] for i in cart)

    lines = ["🛒 Проверьте заказ:\n"]

    for item in cart:
        lines.append(
            f"• {item['name']} × {item['quantity']}"
        )

    lines.append(f"\n💰 Итого: {total:,.0f}₽")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_order")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")],
    ])

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=kb,
    )

    return CONFIRM_ORDER


async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_order":
        await query.edit_message_text("Заказ отменён")
        return ConversationHandler.END

    cart = context.user_data.get("cart", [])

    products = load_products()

    for item in cart:
        product = next(
            (p for p in products if p["id"] == item["id"]),
            None,
        )

        if not product:
            await query.message.reply_text(
                f"Товар {item['name']} не найден"
            )
            return ConversationHandler.END

        if product["stock"] < item["quantity"]:
            await query.message.reply_text(
                f"Недостаточно товара: {item['name']}"
            )
            return ConversationHandler.END

    for item in cart:
        for product in products:
            if product["id"] == item["id"]:
                product["stock"] -= item["quantity"]

    save_products(products)

    orders = load_orders()

    order_id = len(orders) + 1

    total = sum(i["price"] * i["quantity"] for i in cart)

    order = {
        "id": order_id,
        "client_name": context.user_data["client_name"],
        "phone": context.user_data["phone"],
        "comment": context.user_data["comment"],
        "items": cart,
        "total": total,
        "created_at": datetime.now().isoformat(),
    }

    orders.append(order)
    save_orders(orders)

    text = [f"🛒 Новый заказ №{order_id}\n"]

    text.append(f"👤 {escape(order['client_name'])}")
    text.append(f"📱 {escape(order['phone'])}\n")

    for item in cart:
        text.append(
            f"• {escape(item['name'])} × {item['quantity']}"
        )

    text.append(f"\n💰 {total:,.0f}₽")

    await context.bot.send_message(
        GROUP_CHAT_ID,
        "\n".join(text),
        parse_mode=ParseMode.HTML,
    )

    context.user_data["cart"] = []

    await query.edit_message_text(
        f"✅ Заказ №{order_id} оформлен"
    )

    return ConversationHandler.END


# =========================================================
# ADMIN
# =========================================================


async def add_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название категории")
    return ADD_CATEGORY


async def add_category_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()

    categories = load_categories()

    if name in categories:
        await update.message.reply_text("Категория уже существует")
        return ConversationHandler.END

    categories.append(name)
    save_categories(categories)

    await update.message.reply_text(
        "✅ Категория создана",
        reply_markup=main_keyboard(update.effective_user.id),
    )

    return ConversationHandler.END


async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название товара")
    return ADD_PRODUCT_NAME


async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_product"] = {
        "name": update.message.text.strip()
    }

    await update.message.reply_text("Введите описание")

    return ADD_PRODUCT_DESC


async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_product"]["description"] = update.message.text.strip()

    await update.message.reply_text("Введите цену")

    return ADD_PRODUCT_PRICE


async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.replace(",", "."))
    except Exception:
        await update.message.reply_text("Введите число")
        return ADD_PRODUCT_PRICE

    context.user_data["new_product"]["price"] = price

    await update.message.reply_text("Введите остаток")

    return ADD_PRODUCT_STOCK


async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stock = int(update.message.text)
    except Exception:
        await update.message.reply_text("Введите число")
        return ADD_PRODUCT_STOCK

    context.user_data["new_product"]["stock"] = stock

    categories = load_categories()

    kb = [
        [InlineKeyboardButton(c, callback_data=f"choose_cat|{c}")]
        for c in categories
    ]

    await update.message.reply_text(
        "Выберите категорию",
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
            [["Пропустить"]],
            resize_keyboard=True,
        )
    )

    return ADD_PRODUCT_PHOTO


async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = context.user_data["new_product"]

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
        "✅ Товар добавлен",
        reply_markup=main_keyboard(update.effective_user.id),
    )

    return ConversationHandler.END


async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = load_orders()

    if not orders:
        await update.message.reply_text("Заказов пока нет")
        return

    lines = ["📋 Последние заказы\n"]

    for order in orders[-10:]:
        lines.append(
            f"№{order['id']} | {escape(order['client_name'])} | {order['total']:,.0f}₽"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def delete_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ID товара")
    return DELETE_PRODUCT


async def delete_product_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        product_id = int(update.message.text)
    except Exception:
        await update.message.reply_text("Введите число")
        return DELETE_PRODUCT

    product = get_product(product_id)

    if not product:
        await update.message.reply_text("Товар не найден")
        return ConversationHandler.END

    products = [
        p for p in load_products()
        if p["id"] != product_id
    ]

    if product.get("photo"):
        path = os.path.join(PHOTOS_DIR, product["photo"])

        if os.path.exists(path):
            os.remove(path)

    save_products(products)

    await update.message.reply_text(
        "✅ Товар удалён",
        reply_markup=main_keyboard(update.effective_user.id),
    )

    return ConversationHandler.END


# =========================================================
# MAIN MENU
# =========================================================


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📦 Каталог":
        await show_categories(update, context)

    elif text == "🛒 Корзина":
        await view_cart(update, context)

    elif text == "➕ Добавить категорию":
        if is_admin(update.effective_user.id):
            return await add_category_start(update, context)

    elif text == "➕ Добавить товар":
        if is_admin(update.effective_user.id):
            return await add_product_start(update, context)

    elif text == "📋 Заказы":
        if is_admin(update.effective_user.id):
            await show_orders(update, context)

    elif text == "❌ Удалить товар":
        if is_admin(update.effective_user.id):
            return await delete_product_start(update, context)


# =========================================================
# MAIN
# =========================================================


def main():
    init_storage()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    add_cart_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_to_cart_start, pattern="^add\\|")
        ],
        states={
            ADD_TO_CART_QTY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_to_cart_qty,
                )
            ]
        },
        fallbacks=[],
    )

    order_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cart_actions, pattern="^(checkout|clear_cart)$")
        ],
        states={
            ASK_NAME: [
                MessageHandler(filters.TEXT, ask_name)
            ],
            ASK_PHONE: [
                MessageHandler(filters.CONTACT | filters.TEXT, ask_phone)
            ],
            ASK_COMMENT: [
                MessageHandler(filters.TEXT, ask_comment)
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(
                    confirm_order,
                    pattern="^(confirm_order|cancel_order)$"
                )
            ],
        },
        fallbacks=[],
    )

    add_product_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^➕ Добавить товар$"),
                add_product_start,
            )
        ],
        states={
            ADD_PRODUCT_NAME: [
                MessageHandler(filters.TEXT, add_product_name)
            ],
            ADD_PRODUCT_DESC: [
                MessageHandler(filters.TEXT, add_product_desc)
            ],
            ADD_PRODUCT_PRICE: [
                MessageHandler(filters.TEXT, add_product_price)
            ],
            ADD_PRODUCT_STOCK: [
                MessageHandler(filters.TEXT, add_product_stock)
            ],
            ADD_PRODUCT_CATEGORY: [
                CallbackQueryHandler(
                    add_product_category,
                    pattern="^choose_cat\\|"
                )
            ],
            ADD_PRODUCT_PHOTO: [
                MessageHandler(
                    filters.PHOTO | filters.TEXT,
                    add_product_photo,
                )
            ],
        },
        fallbacks=[],
    )

    add_category_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^➕ Добавить категорию$"),
                add_category_start,
            )
        ],
        states={
            ADD_CATEGORY: [
                MessageHandler(filters.TEXT, add_category_save)
            ]
        },
        fallbacks=[],
    )

    delete_product_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^❌ Удалить товар$"),
                delete_product_start,
            )
        ],
        states={
            DELETE_PRODUCT: [
                MessageHandler(filters.TEXT, delete_product_confirm)
            ]
        },
        fallbacks=[],
    )

    app.add_handler(add_cart_conv)
    app.add_handler(order_conv)
    app.add_handler(add_product_conv)
    app.add_handler(add_category_conv)
    app.add_handler(delete_product_conv)

    app.add_handler(
        CallbackQueryHandler(open_category, pattern="^cat\\|")
    )

    app.add_handler(
        CallbackQueryHandler(
            navigate,
            pattern="^(next|prev|back_categories)$"
        )
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu)
    )

    app.run_polling()


if __name__ == "__main__":
    main()
