import json
import os
import base64
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ConversationHandler

# Загружаем переменные из .env файла
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))

# Состояния для диалогов
CHOOSE_CATEGORY, VIEW_PRODUCT, ADD_TO_CART_QTY = range(3)
ASK_NAME, ASK_PHONE, ASK_COMMENT = range(3, 6)
ADMIN_MENU, ADD_PRODUCT_NAME, ADD_PRODUCT_DESC, ADD_PRODUCT_PRICE = range(6, 10)
ADD_PRODUCT_STOCK, ADD_PRODUCT_PHOTO, ADD_PRODUCT_CATEGORY, ADD_CATEGORY_NAME = range(10, 14)

# -------------------- Работа с JSON --------------------
def load_json(filename):
    """Загружает данные из JSON-файла, автоматически раскодируя Base64."""
    path = f"data/{filename}"
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
    # Пробуем раскодировать Base64
    try:
        decoded = base64.b64decode(content).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        # Если не Base64 — читаем как обычный JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return []

def save_json(filename, data):
    """Сохраняет данные в JSON-файл."""
    path = f"data/{filename}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    """Проверяет, есть ли пользователь в списке админов."""
    admins = load_json("admins.json")
    return user_id in admins

def get_categories():
    """Возвращает список всех уникальных категорий товаров."""
    products = load_json("products.json")
    cats = set()
    for p in products:
        if p.get("category"):
            cats.add(p["category"])
    return sorted(list(cats))

def get_products_by_category(category):
    """Возвращает товары из определённой категории."""
    products = load_json("products.json")
    return [p for p in products if p.get("category") == category]

def add_order(client_name, phone, comment, cart_items, total):
    """Добавляет заказ в orders.json и возвращает его."""
    orders = load_json("orders.json")
    order = {
        "id": len(orders) + 1,
        "client_name": client_name,
        "phone": phone,
        "comment": comment or "",
        "items": cart_items,
        "total": total,
        "created_at": datetime.now().isoformat()
    }
    orders.append(order)
    save_json("orders.json", orders)
    return order

# -------------------- Клавиатуры --------------------
def main_keyboard():
    """Главное меню клиента."""
    return ReplyKeyboardMarkup(
        [["📦 Каталог", "🛒 Корзина"]],
        resize_keyboard=True
    )

def admin_keyboard():
    """Меню администратора."""
    return ReplyKeyboardMarkup(
        [
            ["➕ Добавить товар", "➕ Добавить категорию"],
            ["📋 Список товаров", "❌ Удалить товар"],
            ["👤 Добавить админа", "🔙 Выйти"]
        ],
        resize_keyboard=True
    )

# -------------------- Команды --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    context.user_data["cart"] = []
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Добро пожаловать, администратор!",
            reply_markup=admin_keyboard()
        )
    else:
        await update.message.reply_text(
            "Добро пожаловать! Выберите действие:",
            reply_markup=main_keyboard()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений (кнопки главного меню)."""
    text = update.message.text

    if text == "📦 Каталог":
        await show_categories(update, context)
    elif text == "🛒 Корзина":
        await view_cart(update, context)
    elif text == "🔙 Выйти":
        await start(update, context)
    elif text == "➕ Добавить товар" and is_admin(update.effective_user.id):
        await update.message.reply_text("Введите название товара:")
        return ADD_PRODUCT_NAME
    elif text == "➕ Добавить категорию" and is_admin(update.effective_user.id):
        await update.message.reply_text("Введите название новой категории:")
        return ADD_CATEGORY_NAME
    elif text == "👤 Добавить админа" and is_admin(update.effective_user.id):
        await update.message.reply_text("Введите Telegram ID нового администратора:")
        return 20
    elif text == "📋 Список товаров" and is_admin(update.effective_user.id):
        await list_products_admin(update, context)
    elif text == "❌ Удалить товар" and is_admin(update.effective_user.id):
        await update.message.reply_text("Введите ID товара для удаления:")
        return 21

# -------------------- Каталог --------------------
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список категорий."""
    categories = get_categories()
    if not categories:
        await update.message.reply_text("Каталог пока пуст. Загляните позже!")
        return ConversationHandler.END

    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(cat, callback_data=f"cat_{cat}")])

    await update.message.reply_text(
        "📂 Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора категории."""
    query = update.callback_query
    await query.answer()

    category = query.data.replace("cat_", "")
    products = get_products_by_category(category)

    context.user_data["cat_products"] = products
    context.user_data["current_index"] = 0

    if not products:
        await query.edit_message_text("В этой категории пока нет товаров.")
        return ConversationHandler.END

    await show_product_card(update, context)

async def show_product_card(update, context):
    """Показывает карточку товара."""
    index = context.user_data["current_index"]
    products = context.user_data["cat_products"]
    product = products[index]

    if product['stock'] > 0:
        stock_text = f"📦 В наличии: {product['stock']} шт."
    else:
        stock_text = "❌ Нет в наличии"

    text = (
        f"🏷 *{product['name']}*\n\n"
        f"_{product['description']}_\n\n"
        f"💰 Цена: {product['price']}₽\n"
        f"{stock_text}"
    )

    keyboard = []
    nav_row = []

    if index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data="nav_prev"))
    nav_row.append(InlineKeyboardButton(f"{index + 1}/{len(products)}", callback_data="nav_none"))
    if index < len(products) - 1:
        nav_row.append(InlineKeyboardButton("Вперёд ➡️", callback_data="nav_next"))

    keyboard.append(nav_row)

    if product['stock'] > 0:
        keyboard.append([InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"add_{product['id']}")])

    keyboard.append([InlineKeyboardButton("🔙 К категориям", callback_data="back_to_cats")])

    photo_file = product.get('photo', '')
    photo_path = f"photos/{photo_file}" if photo_file else ""

    if photo_file and os.path.exists(photo_path):
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.delete_message()
            await update.callback_query.message.reply_photo(
                photo=open(photo_path, "rb"),
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_photo(
                photo=open(photo_path, "rb"),
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
    else:
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

async def nav_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик навигации по товарам."""
    query = update.callback_query
    await query.answer()

    action = query.data

    if action == "nav_prev":
        context.user_data["current_index"] -= 1
    elif action == "nav_next":
        context.user_data["current_index"] += 1
    elif action == "back_to_cats":
        await show_categories(update, context)
        return ConversationHandler.END
    elif action == "nav_none":
        return
    elif action.startswith("add_"):
        product_id = int(action.replace("add_", ""))
        context.user_data["adding_product_id"] = product_id

        products = load_json("products.json")
        product = next((p for p in products if p["id"] == product_id), None)

        await query.message.reply_text(
            f"Сколько штук добавить? (доступно: {product['stock']})",
            reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True)
        )
        return ADD_TO_CART_QTY

    await show_product_card(update, context)
    return ConversationHandler.END

# -------------------- Корзина --------------------
async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление товара в корзину."""
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

    product_id = context.user_data.get("adding_product_id")
    products = load_json("products.json")
    product = next((p for p in products if p["id"] == product_id), None)

    if not product:
        await update.message.reply_text("Товар не найден.", reply_markup=main_keyboard())
        return ConversationHandler.END

    if qty > product["stock"]:
        await update.message.reply_text(f"Недостаточно товара. В наличии: {product['stock']} шт.")
        return ADD_TO_CART_QTY

    cart = context.user_data.get("cart", [])
    existing = next((item for item in cart if item["id"] == product_id), None)

    if existing:
        existing["quantity"] += qty
    else:
        cart.append({
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "quantity": qty
        })

    context.user_data["cart"] = cart
    await update.message.reply_text(
        f"✅ {product['name']} × {qty} добавлено в корзину!",
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END

async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр корзины."""
    cart = context.user_data.get("cart", [])

    if not cart:
        await update.message.reply_text("🛒 Корзина пуста.", reply_markup=main_keyboard())
        return ConversationHandler.END

    total = 0
    text = "🛒 *Ваша корзина:*\n\n"

    for i, item in enumerate(cart, 1):
        subtotal = item["price"] * item["quantity"]
        total += subtotal
        text += f"{i}. {item['name']} × {item['quantity']} = {subtotal}₽\n"

    text += f"\n💰 *Итого: {total}₽*"

    keyboard = [
        [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")]
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def cart_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик действий с корзиной."""
    query = update.callback_query
    await query.answer()

    if query.data == "clear_cart":
        context.user_data["cart"] = []
        await query.edit_message_text("🗑 Корзина очищена.")
        return ConversationHandler.END

    elif query.data == "checkout":
        await query.edit_message_text("Оформляем заказ! 📝")
        await query.message.reply_text("Введите ваше имя:", reply_markup=ReplyKeyboardMarkup([["Отмена"]], resize_keyboard=True))
        return ASK_NAME

# -------------------- Оформление заказа --------------------
async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: запрос имени."""
    if update.message.text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END

    context.user_data["client_name"] = update.message.text

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться номером", request_contact=True)], ["Отмена"]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await update.message.reply_text("Отправьте ваш номер телефона:", reply_markup=keyboard)
    return ASK_PHONE

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2: запрос телефона."""
    if update.message.text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END

    if update.message.contact:
        phone = update.message.contact.phone_number
        if phone.startswith("+7"):
            phone = "8" + phone[2:]
        elif phone.startswith("7"):
            phone = "8" + phone[1:]
    else:
        phone = update.message.text.strip()
        phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    if not phone.startswith("8") or len(phone) != 11 or not phone.isdigit():
        await update.message.reply_text(
            "❌ Номер должен начинаться с 8 и содержать 11 цифр. Попробуйте ещё раз:"
        )
        return ASK_PHONE

    context.user_data["phone"] = phone

    keyboard = ReplyKeyboardMarkup([["Пропустить"], ["Отмена"]], resize_keyboard=True)
    await update.message.reply_text("Комментарий к заказу (необязательно):", reply_markup=keyboard)
    return ASK_COMMENT

async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3: комментарий и завершение заказа."""
    text = update.message.text

    if text == "Отмена":
        await update.message.reply_text("Оформление отменено.", reply_markup=main_keyboard())
        return ConversationHandler.END

    comment = "" if text == "Пропустить" else text
    context.user_data["comment"] = comment

    cart = context.user_data.get("cart", [])
    total = sum(item["price"] * item["quantity"] for item in cart)

    order = add_order(
        context.user_data["client_name"],
        context.user_data["phone"],
        context.user_data["comment"],
        cart,
        total
    )

    items_text = "\n".join([
        f"— {item['name']} × {item['quantity']} = {item['price'] * item['quantity']}₽"
        for item in cart
    ])

    group_msg = (
        f"🛒 *Новый заказ №{order['id']}*\n\n"
        f"👤 Имя: {order['client_name']}\n"
        f"📞 Телефон: {order['phone']}\n"
        f"💬 Комментарий: {order['comment'] or '—'}\n\n"
        f"*Состав заказа:*\n{items_text}\n\n"
        f"💰 *Итого: {total}₽*"
    )

    await context.bot.send_message(GROUP_CHAT_ID, group_msg, parse_mode="Markdown")

    await update.message.reply_text(
        "✅ Заказ оформлен! Мы свяжемся с вами в ближайшее время.",
        reply_markup=main_keyboard()
    )

    context.user_data["cart"] = []
    return ConversationHandler.END

# -------------------- Админка --------------------
async def list_products_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список всех товаров для админа."""
    products = load_json("products.json")
    if not products:
        await update.message.reply_text("Товаров пока нет.")
        return

    text = "📋 *Список товаров:*\n\n"
    for p in products:
        text += f"ID: {p['id']} | {p['name']} | {p['price']}₽ | Остаток: {p['stock']}\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1 добавления товара: название."""
    context.user_data["new_product"] = {"name": update.message.text}
    await update.message.reply_text("Введите описание товара:")
    return ADD_PRODUCT_DESC

async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2: описание."""
    context.user_data["new_product"]["description"] = update.message.text
    await update.message.reply_text("Введите цену товара (только число):")
    return ADD_PRODUCT_PRICE

async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 3: цена."""
    try:
        price = float(update.message.text)
        context.user_data["new_product"]["price"] = price
    except ValueError:
        await update.message.reply_text("Введите число!")
        return ADD_PRODUCT_PRICE

    await update.message.reply_text("Введите количество на складе:")
    return ADD_PRODUCT_STOCK

async def add_product_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 4: остаток."""
    try:
        stock = int(update.message.text)
        context.user_data["new_product"]["stock"] = stock
    except ValueError:
        await update.message.reply_text("Введите целое число!")
        return ADD_PRODUCT_STOCK

    categories = get_categories()
    if not categories:
        await update.message.reply_text("Сначала создайте категорию через меню!")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(cat, callback_data=f"newcat_{cat}")] for cat in categories]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_PRODUCT_CATEGORY

async def add_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 5: выбор категории."""
    query = update.callback_query
    await query.answer()

    category = query.data.replace("newcat_", "")
    context.user_data["new_product"]["category"] = category

    await query.edit_message_text("Отправьте фото товара (или нажмите 'Пропустить'):")
    return ADD_PRODUCT_PHOTO

async def add_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 6: фото и сохранение товара."""
    new_product = context.user_data["new_product"]

    products = load_json("products.json")
    new_id = max([p["id"] for p in products], default=0) + 1
    new_product["id"] = new_id

    if update.message.photo:
        photo_file = update.message.photo[-1]
        file = await photo_file.get_file()
        filename = f"product_{new_id}.jpg"
        await file.download_to_drive(f"photos/{filename}")
        new_product["photo"] = filename
    else:
        new_product["photo"] = ""

    products.append(new_product)
    save_json("products.json", products)

    await update.message.reply_text(
        f"✅ Товар '{new_product['name']}' добавлен! ID: {new_id}",
        reply_markup=admin_keyboard()
    )
    return ConversationHandler.END

async def add_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление новой категории."""
    category_name = update.message.text.strip()

    categories = get_categories()
    if category_name in categories:
        await update.message.reply_text("Такая категория уже существует!", reply_markup=admin_keyboard())
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ Категория '{category_name}' создана! Добавляйте в неё товары.",
        reply_markup=admin_keyboard()
    )
    return ConversationHandler.END

async def add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление нового админа по Telegram ID."""
    try:
        new_id = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Введите числовой ID!")
        return 20

    admins = load_json("admins.json")
    if new_id in admins:
        await update.message.reply_text("Этот пользователь уже администратор.", reply_markup=admin_keyboard())
    else:
        admins.append(new_id)
        save_json("admins.json", admins)
        await update.message.reply_text(f"✅ Админ с ID {new_id} добавлен!", reply_markup=admin_keyboard())

    return ConversationHandler.END

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление товара по ID."""
    try:
        product_id = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Введите числовой ID товара!")
        return 21

    products = load_json("products.json")
    product = next((p for p in products if p["id"] == product_id), None)

    if not product:
        await update.message.reply_text("Товар с таким ID не найден.", reply_markup=admin_keyboard())
        return ConversationHandler.END

    if product.get("photo"):
        photo_path = f"photos/{product['photo']}"
        if os.path.exists(photo_path):
            os.remove(photo_path)

    products = [p for p in products if p["id"] != product_id]
    save_json("products.json", products)

    await update.message.reply_text(f"✅ Товар '{product['name']}' удалён.", reply_markup=admin_keyboard())
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена любого диалога."""
    await update.message.reply_text("Действие отменено.", reply_markup=main_keyboard())
    return ConversationHandler.END

# -------------------- Запуск --------------------
def main():
    """Точка входа."""
    os.makedirs("data", exist_ok=True)
    os.makedirs("photos", exist_ok=True)

    for filename in ["products.json", "orders.json", "admins.json"]:
        path = f"data/{filename}"
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                if filename == "admins.json":
                    f.write("[707877919]")
                else:
                    f.write("[]")

    app = Application.builder().token(BOT_TOKEN).build()

    add_product_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить товар$"), add_product_name)],
        states={
            ADD_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_PRODUCT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)],
            ADD_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)],
            ADD_PRODUCT_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_stock)],
            ADD_PRODUCT_CATEGORY: [CallbackQueryHandler(add_product_category, pattern="^newcat_")],
            ADD_PRODUCT_PHOTO: [
                MessageHandler(filters.PHOTO, add_product_photo),
                MessageHandler(filters.Regex("^Пропустить$"), add_product_photo)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cart_action, pattern="^(checkout|clear_cart)$")],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE: [
                MessageHandler(filters.CONTACT, ask_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone),
            ],
            ASK_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    cart_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(nav_product, pattern="^add_")],
        states={
            ADD_TO_CART_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_to_cart)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    cat_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить категорию$"), add_category_name)],
        states={
            ADD_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 Добавить админа$"), add_admin_id)],
        states={
            20: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    delete_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❌ Удалить товар$"), delete_product)],
        states={
            21: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_product)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))

    app.add_handler(add_product_conv)
    app.add_handler(order_conv)
    app.add_handler(cart_conv)
    app.add_handler(cat_conv)
    app.add_handler(admin_conv)
    app.add_handler(delete_conv)

    app.add_handler(CallbackQueryHandler(show_category_products, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(nav_product, pattern="^nav_"))
    app.add_handler(CallbackQueryHandler(nav_product, pattern="^back_to_cats$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
