import json
import os
import re
from pathlib import Path
from uuid import uuid4

import telebot
from dotenv import load_dotenv
from telebot import types


BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "catalog.json"
IMAGES_DIR = BASE_DIR / "images" / "admin"

user_states: dict[int, dict] = {}


def load_catalog() -> dict:
    with CATALOG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_catalog(catalog: dict) -> None:
    with CATALOG_PATH.open("w", encoding="utf-8") as file:
        json.dump(catalog, file, ensure_ascii=False, indent=2)
        file.write("\n")


def admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return set()
    return {int(value.strip()) for value in raw.split(",") if value.strip().isdigit()}


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids()


def slugify(value: str, existing: set[str]) -> str:
    replacements = {
        "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
        "є": "ye", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "y",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "shch", "ь": "", "ю": "yu", "я": "ya",
    }
    text = "".join(replacements.get(char, char) for char in value.lower())
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    base = text or f"item_{uuid4().hex[:8]}"
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate


def find_part(catalog: dict, part_id: str) -> dict | None:
    return next((part for part in catalog["body_parts"] if part["id"] == part_id), None)


def find_piercing(part: dict, piercing_id: str) -> dict | None:
    return next((piercing for piercing in part["piercings"] if piercing["id"] == piercing_id), None)


def image_path(path_value: str) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path if path.exists() and path.is_file() else None


def keyboard(rows: list[list[tuple[str, str]]]) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.row(*(types.InlineKeyboardButton(text, callback_data=data) for text, data in row))
    return markup


def url_keyboard(text: str, url: str, back_data: str = "home") -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton(text, url=url))
    markup.row(types.InlineKeyboardButton("Назад", callback_data=back_data))
    return markup


def main_menu() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("Каталог", callback_data="catalog"))
    manager_url = os.getenv("MANAGER_URL", "").strip()
    if manager_url:
        markup.row(types.InlineKeyboardButton("Зв'язатись з майстром", url=manager_url))
    return markup


def body_parts_menu(catalog: dict, prefix: str = "body") -> types.InlineKeyboardMarkup:
    rows = [[(part["title"], f"{prefix}:{part['id']}")] for part in catalog["body_parts"]]
    rows.append([("Назад", "home" if prefix == "body" else "admin")])
    return keyboard(rows)


def piercings_menu(part: dict, prefix: str = "piercing") -> types.InlineKeyboardMarkup:
    rows = [[(piercing["title"], f"{prefix}:{part['id']}:{piercing['id']}")] for piercing in part["piercings"]]
    rows.append([("Назад", "catalog" if prefix == "piercing" else "admin")])
    return keyboard(rows)


def product_menu(part_id: str, piercing_id: str, index: int, total: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    nav = []
    if index > 0:
        nav.append(types.InlineKeyboardButton("← Назад", callback_data=f"product:{part_id}:{piercing_id}:{index - 1}"))
    if index < total - 1:
        nav.append(types.InlineKeyboardButton("Вперед →", callback_data=f"product:{part_id}:{piercing_id}:{index + 1}"))
    if nav:
        markup.row(*nav)

    manager_url = os.getenv("MANAGER_URL", "").strip()
    if manager_url:
        markup.row(types.InlineKeyboardButton("Зв'язатись з майстром", url=manager_url))
    markup.row(types.InlineKeyboardButton("До видів проколів", callback_data=f"body:{part_id}"))
    markup.row(types.InlineKeyboardButton("У каталог", callback_data="catalog"))
    return markup


def admin_menu() -> types.InlineKeyboardMarkup:
    return keyboard([
        [("Додати частину тіла", "admin_add_part")],
        [("Додати вид проколу", "admin_add_piercing")],
        [("Додати прикрасу", "admin_add_product")],
        [("Редагувати прикрасу", "admin_edit_product")],
        [("У головне меню", "home")],
    ])


def edit_fields_menu(part_id: str, piercing_id: str, index: int) -> types.InlineKeyboardMarkup:
    target = f"{part_id}:{piercing_id}:{index}"
    return keyboard([
        [("Назва", f"admin_edit_field:title:{target}")],
        [("Фото", f"admin_edit_photo:{target}")],
        [("Матеріал", f"admin_edit_field:material:{target}")],
        [("Розмір", f"admin_edit_field:size:{target}")],
        [("Ціна", f"admin_edit_field:price:{target}")],
        [("Опис", f"admin_edit_field:description:{target}")],
        [("Видалити прикрасу", f"admin_delete_product:{target}")],
        [("В адмінку", "admin")],
    ])


def product_caption(product: dict, index: int | None = None, total: int | None = None) -> str:
    lines = [
        product.get("title", "Без назви"),
        "",
        f"Матеріал: {product.get('material', '-')}",
        f"Розмір: {product.get('size', '-')}",
        f"Ціна: {product.get('price', '-')}",
        "",
        product.get("description", ""),
    ]
    if index is not None and total is not None:
        lines.extend(["", f"{index + 1} / {total}"])
    return "\n".join(line for line in lines if line is not None)


def send_product(bot: telebot.TeleBot, chat_id: int, part_id: str, piercing_id: str, index: int) -> None:
    catalog = load_catalog()
    part = find_part(catalog, part_id)
    piercing = find_piercing(part, piercing_id) if part else None
    if not part or not piercing:
        bot.send_message(chat_id, "Розділ не знайдено.", reply_markup=main_menu())
        return

    products = piercing.get("products", [])
    if not products:
        bot.send_message(chat_id, f"{piercing['title']}\n\nПрикраси для цього проколу скоро з'являться.", reply_markup=piercings_menu(part))
        return

    index = max(0, min(index, len(products) - 1))
    product = products[index]
    photo_path = image_path(product.get("image", ""))
    markup = product_menu(part_id, piercing_id, index, len(products))
    if photo_path:
        with photo_path.open("rb") as photo:
            bot.send_photo(chat_id, photo, caption=product_caption(product, index, len(products)), reply_markup=markup)
    else:
        bot.send_message(chat_id, product_caption(product, index, len(products)), reply_markup=markup)


def save_message_photo(bot: telebot.TeleBot, message: types.Message) -> str:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    photo = message.photo[-1]
    file_info = bot.get_file(photo.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    relative_path = Path("images") / "admin" / f"{photo.file_unique_id}.jpg"
    destination = BASE_DIR / relative_path
    destination.write_bytes(downloaded_file)
    return relative_path.as_posix()


def set_state(user_id: int, step: str, **data) -> None:
    user_states[user_id] = {"step": step, **data}


def clear_state(user_id: int) -> None:
    user_states.pop(user_id, None)


def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Додайте BOT_TOKEN у файл .env")

    bot = telebot.TeleBot(token, parse_mode=None)

    def deny_admin(call: types.CallbackQuery) -> bool:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Немає доступу. Напишіть /whoami і додайте ID в ADMIN_IDS.", show_alert=True)
            return True
        return False

    @bot.message_handler(commands=["start"])
    def start(message: types.Message) -> None:
        bot.send_message(message.chat.id, "Оберіть дію:", reply_markup=main_menu())

    @bot.message_handler(commands=["whoami"])
    def whoami(message: types.Message) -> None:
        bot.send_message(message.chat.id, f"Ваш Telegram ID: {message.from_user.id}")

    @bot.message_handler(commands=["cancel"])
    def cancel(message: types.Message) -> None:
        clear_state(message.from_user.id)
        bot.send_message(message.chat.id, "Дію скасовано.", reply_markup=main_menu())

    @bot.message_handler(commands=["admin"])
    def admin_command(message: types.Message) -> None:
        if not is_admin(message.from_user.id):
            bot.send_message(
                message.chat.id,
                "Адмін-доступ не увімкнено для вашого акаунта.\n"
                f"Ваш Telegram ID: {message.from_user.id}\n"
                "Додайте його в ADMIN_IDS у файлі .env."
            )
            return
        bot.send_message(message.chat.id, "Адмін-панель:", reply_markup=admin_menu())

    @bot.callback_query_handler(func=lambda call: True)
    def callbacks(call: types.CallbackQuery) -> None:
        data = call.data
        chat_id = call.message.chat.id
        bot.answer_callback_query(call.id)

        if data == "home":
            clear_state(call.from_user.id)
            bot.send_message(chat_id, "Оберіть дію:", reply_markup=main_menu())
            return

        if data == "catalog":
            bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(load_catalog()))
            return

        if data.startswith("body:"):
            part_id = data.split(":")[1]
            part = find_part(load_catalog(), part_id)
            if not part:
                bot.send_message(chat_id, "Розділ не знайдено.", reply_markup=main_menu())
                return
            bot.send_message(chat_id, f"{part['title']}\n\nОберіть вид проколу:", reply_markup=piercings_menu(part))
            return

        if data.startswith("piercing:"):
            _, part_id, piercing_id = data.split(":")
            send_product(bot, chat_id, part_id, piercing_id, 0)
            return

        if data.startswith("product:"):
            _, part_id, piercing_id, index = data.split(":")
            send_product(bot, chat_id, part_id, piercing_id, int(index))
            return

        if data == "contact":
            manager_url = os.getenv("MANAGER_URL", "").strip()
            if manager_url:
                bot.send_message(chat_id, "Для замовлення або консультації натисніть кнопку нижче:", reply_markup=url_keyboard("Написати менеджеру", manager_url))
            else:
                bot.send_message(chat_id, "Контакт менеджера поки не вказано.", reply_markup=main_menu())
            return

        if data == "admin":
            if deny_admin(call):
                return
            clear_state(call.from_user.id)
            bot.send_message(chat_id, "Адмін-панель:", reply_markup=admin_menu())
            return

        if data == "admin_add_part":
            if deny_admin(call):
                return
            set_state(call.from_user.id, "add_part_title")
            bot.send_message(chat_id, "Введіть назву частини тіла. Наприклад: Вухо")
            return

        if data == "admin_add_piercing":
            if deny_admin(call):
                return
            set_state(call.from_user.id, "add_piercing_part")
            bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(load_catalog(), "admin_piercing_part"))
            return

        if data.startswith("admin_piercing_part:"):
            if deny_admin(call):
                return
            part_id = data.split(":")[1]
            set_state(call.from_user.id, "add_piercing_title", part_id=part_id)
            bot.send_message(chat_id, "Введіть назву виду проколу. Наприклад: Хелікс")
            return

        if data == "admin_add_product":
            if deny_admin(call):
                return
            set_state(call.from_user.id, "add_product_part")
            bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(load_catalog(), "admin_product_part"))
            return

        if data.startswith("admin_product_part:"):
            if deny_admin(call):
                return
            catalog = load_catalog()
            part_id = data.split(":")[1]
            part = find_part(catalog, part_id)
            if not part:
                bot.send_message(chat_id, "Розділ не знайдено.", reply_markup=admin_menu())
                return
            set_state(call.from_user.id, "add_product_piercing", part_id=part_id)
            bot.send_message(chat_id, "Оберіть вид проколу:", reply_markup=piercings_menu(part, "admin_product_piercing"))
            return

        if data.startswith("admin_product_piercing:"):
            if deny_admin(call):
                return
            _, part_id, piercing_id = data.split(":")
            set_state(call.from_user.id, "add_product_title", part_id=part_id, piercing_id=piercing_id)
            bot.send_message(chat_id, "Введіть назву прикраси:")
            return

        if data == "admin_edit_product":
            if deny_admin(call):
                return
            bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(load_catalog(), "admin_edit_part"))
            return

        if data.startswith("admin_edit_part:"):
            if deny_admin(call):
                return
            catalog = load_catalog()
            part_id = data.split(":")[1]
            part = find_part(catalog, part_id)
            if not part:
                bot.send_message(chat_id, "Розділ не знайдено.", reply_markup=admin_menu())
                return
            bot.send_message(chat_id, "Оберіть вид проколу:", reply_markup=piercings_menu(part, "admin_edit_piercing"))
            return

        if data.startswith("admin_edit_piercing:"):
            if deny_admin(call):
                return
            _, part_id, piercing_id = data.split(":")
            catalog = load_catalog()
            part = find_part(catalog, part_id)
            piercing = find_piercing(part, piercing_id) if part else None
            if not piercing:
                bot.send_message(chat_id, "Прокол не знайдено.", reply_markup=admin_menu())
                return
            rows = [
                [(product.get("title", "Без назви"), f"admin_edit_target:{part_id}:{piercing_id}:{index}")]
                for index, product in enumerate(piercing.get("products", []))
            ]
            rows.append([("В адмінку", "admin")])
            bot.send_message(chat_id, "Оберіть прикрасу:", reply_markup=keyboard(rows))
            return

        if data.startswith("admin_edit_target:"):
            if deny_admin(call):
                return
            _, part_id, piercing_id, index = data.split(":")
            bot.send_message(chat_id, "Що змінити?", reply_markup=edit_fields_menu(part_id, piercing_id, int(index)))
            return

        if data.startswith("admin_edit_field:"):
            if deny_admin(call):
                return
            _, field, part_id, piercing_id, index = data.split(":")
            set_state(call.from_user.id, "edit_product_field", field=field, part_id=part_id, piercing_id=piercing_id, index=int(index))
            bot.send_message(chat_id, "Введіть нове значення:")
            return

        if data.startswith("admin_edit_photo:"):
            if deny_admin(call):
                return
            _, part_id, piercing_id, index = data.split(":")
            set_state(call.from_user.id, "edit_product_photo", part_id=part_id, piercing_id=piercing_id, index=int(index))
            bot.send_message(chat_id, "Надішліть нове фото прикраси:")
            return

        if data.startswith("admin_delete_product:"):
            if deny_admin(call):
                return
            _, part_id, piercing_id, index = data.split(":")
            catalog = load_catalog()
            part = find_part(catalog, part_id)
            piercing = find_piercing(part, piercing_id) if part else None
            if piercing and 0 <= int(index) < len(piercing.get("products", [])):
                piercing["products"].pop(int(index))
                save_catalog(catalog)
            bot.send_message(chat_id, "Прикрасу видалено.", reply_markup=admin_menu())
            return

    @bot.message_handler(content_types=["text", "photo"])
    def state_messages(message: types.Message) -> None:
        user_id = message.from_user.id
        state = user_states.get(user_id)
        if not state:
            bot.send_message(message.chat.id, "Оберіть дію:", reply_markup=main_menu())
            return
        if not is_admin(user_id):
            clear_state(user_id)
            return

        step = state["step"]
        text = (message.text or "").strip()

        if step == "add_part_title":
            catalog = load_catalog()
            existing = {part["id"] for part in catalog["body_parts"]}
            catalog["body_parts"].append({"id": slugify(text, existing), "title": text, "piercings": []})
            save_catalog(catalog)
            clear_state(user_id)
            bot.send_message(message.chat.id, "Частину тіла додано.", reply_markup=admin_menu())
            return

        if step == "add_piercing_title":
            state["title"] = text
            state["step"] = "add_piercing_image"
            bot.send_message(message.chat.id, "Надішліть фото виду проколу або напишіть `-`, якщо фото поки немає.")
            return

        if step == "add_piercing_image":
            catalog = load_catalog()
            part = find_part(catalog, state["part_id"])
            if not part:
                clear_state(user_id)
                bot.send_message(message.chat.id, "Частину тіла не знайдено.", reply_markup=admin_menu())
                return
            image = save_message_photo(bot, message) if message.photo else ""
            existing = {piercing["id"] for piercing in part["piercings"]}
            part["piercings"].append({"id": slugify(state["title"], existing), "title": state["title"], "image": image, "products": []})
            save_catalog(catalog)
            clear_state(user_id)
            bot.send_message(message.chat.id, "Вид проколу додано.", reply_markup=admin_menu())
            return

        product_steps = {
            "add_product_title": ("title", "add_product_image", "Надішліть фото прикраси або напишіть `-`, якщо фото поки немає."),
            "add_product_material": ("material", "add_product_size", "Введіть розмір. Наприклад: 1.2 x 8 мм"),
            "add_product_size": ("size", "add_product_price", "Введіть ціну. Наприклад: 1200 грн"),
            "add_product_price": ("price", "add_product_description", "Введіть опис прикраси."),
        }
        if step in product_steps:
            field, next_step, prompt = product_steps[step]
            state[field] = text
            state["step"] = next_step
            bot.send_message(message.chat.id, prompt)
            return

        if step == "add_product_image":
            state["image"] = save_message_photo(bot, message) if message.photo else ""
            state["step"] = "add_product_material"
            bot.send_message(message.chat.id, "Введіть матеріал. Наприклад: Титан")
            return

        if step == "add_product_description":
            catalog = load_catalog()
            part = find_part(catalog, state["part_id"])
            piercing = find_piercing(part, state["piercing_id"]) if part else None
            if not piercing:
                clear_state(user_id)
                bot.send_message(message.chat.id, "Вид проколу не знайдено.", reply_markup=admin_menu())
                return
            piercing.setdefault("products", []).append(
                {
                    "title": state["title"],
                    "image": state.get("image", ""),
                    "material": state["material"],
                    "size": state["size"],
                    "price": state["price"],
                    "description": text,
                }
            )
            save_catalog(catalog)
            clear_state(user_id)
            bot.send_message(message.chat.id, "Прикрасу додано.", reply_markup=admin_menu())
            return

        if step == "edit_product_field":
            catalog = load_catalog()
            part = find_part(catalog, state["part_id"])
            piercing = find_piercing(part, state["piercing_id"]) if part else None
            if piercing:
                piercing["products"][state["index"]][state["field"]] = text
                save_catalog(catalog)
            clear_state(user_id)
            bot.send_message(message.chat.id, "Зміну збережено.", reply_markup=admin_menu())
            return

        if step == "edit_product_photo":
            if not message.photo:
                bot.send_message(message.chat.id, "Потрібно надіслати саме фото.")
                return
            catalog = load_catalog()
            part = find_part(catalog, state["part_id"])
            piercing = find_piercing(part, state["piercing_id"]) if part else None
            if piercing:
                piercing["products"][state["index"]]["image"] = save_message_photo(bot, message)
                save_catalog(catalog)
            clear_state(user_id)
            bot.send_message(message.chat.id, "Фото оновлено.", reply_markup=admin_menu())

    print("Bot is running...")
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
