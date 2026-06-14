import json
import os
import re
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import requests
import telebot
from dotenv import load_dotenv
from telebot import types


BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "catalog.json"
IMAGES_DIR = BASE_DIR / "images" / "admin"

user_states: dict[int, dict] = {}


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


def keyboard(rows: list[list[tuple[str, str]]]) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.row(*(types.InlineKeyboardButton(text, callback_data=data) for text, data in row))
    return markup


class CatalogStore:
    def load_catalog(self) -> dict:
        raise NotImplementedError

    def add_part(self, title: str) -> None:
        raise NotImplementedError

    def add_piercing(self, part_id: str, title: str, image: str) -> None:
        raise NotImplementedError

    def add_product(self, piercing_id: str, product: dict) -> None:
        raise NotImplementedError

    def update_product(self, product_id: str, field: str, value: str) -> None:
        raise NotImplementedError

    def delete_product(self, product_id: str) -> None:
        raise NotImplementedError


class LocalJsonStore(CatalogStore):
    def load_catalog(self) -> dict:
        with CATALOG_PATH.open("r", encoding="utf-8") as file:
            return json.load(file)

    def save_catalog(self, catalog: dict) -> None:
        with CATALOG_PATH.open("w", encoding="utf-8") as file:
            json.dump(catalog, file, ensure_ascii=False, indent=2)
            file.write("\n")

    def add_part(self, title: str) -> None:
        catalog = self.load_catalog()
        existing = {part["id"] for part in catalog["body_parts"]}
        catalog["body_parts"].append({"id": slugify(title, existing), "title": title, "piercings": []})
        self.save_catalog(catalog)

    def add_piercing(self, part_id: str, title: str, image: str) -> None:
        catalog = self.load_catalog()
        part = find_part(catalog, part_id)
        if not part:
            raise RuntimeError("Частину тіла не знайдено.")
        existing = {piercing["id"] for piercing in part["piercings"]}
        part["piercings"].append({"id": slugify(title, existing), "title": title, "image": image, "products": []})
        self.save_catalog(catalog)

    def add_product(self, piercing_id: str, product: dict) -> None:
        catalog = self.load_catalog()
        for part in catalog["body_parts"]:
            piercing = find_piercing(part, piercing_id)
            if piercing:
                product["id"] = uuid4().hex
                piercing.setdefault("products", []).append(product)
                self.save_catalog(catalog)
                return
        raise RuntimeError("Вид проколу не знайдено.")

    def update_product(self, product_id: str, field: str, value: str) -> None:
        catalog = self.load_catalog()
        for part in catalog["body_parts"]:
            for piercing in part["piercings"]:
                for product in piercing.get("products", []):
                    if product.get("id") == product_id:
                        product[field] = value
                        self.save_catalog(catalog)
                        return
        raise RuntimeError("Прикрасу не знайдено.")

    def delete_product(self, product_id: str) -> None:
        catalog = self.load_catalog()
        for part in catalog["body_parts"]:
            for piercing in part["piercings"]:
                products = piercing.get("products", [])
                before = len(products)
                piercing["products"] = [product for product in products if product.get("id") != product_id]
                if len(piercing["products"]) != before:
                    self.save_catalog(catalog)
                    return


class SupabaseStore(CatalogStore):
    def __init__(self, url: str, key: str, bucket: str):
        self.url = url.rstrip("/")
        self.key = key
        self.bucket = bucket

    @property
    def headers(self) -> dict:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = requests.request(method, f"{self.url}{path}", headers=self.headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase error {response.status_code}: {response.text}")
        return response

    def get_rows(self, table: str, params: dict | None = None) -> list[dict]:
        response = self.request("GET", f"/rest/v1/{table}", params=params or {})
        return response.json()

    def insert_row(self, table: str, data: dict) -> dict:
        headers = {**self.headers, "Prefer": "return=representation"}
        response = requests.post(f"{self.url}/rest/v1/{table}", headers=headers, json=data, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase insert error {response.status_code}: {response.text}")
        return response.json()[0]

    def patch_row(self, table: str, row_id: str, data: dict) -> None:
        self.request("PATCH", f"/rest/v1/{table}", params={"id": f"eq.{row_id}"}, json=data)

    def delete_row(self, table: str, row_id: str) -> None:
        self.request("DELETE", f"/rest/v1/{table}", params={"id": f"eq.{row_id}"})

    def load_catalog(self) -> dict:
        parts = self.get_rows("body_parts", {"select": "id,title", "order": "sort_order.asc,title.asc"})
        piercings = self.get_rows("piercings", {"select": "id,body_part_id,title,image_url", "order": "sort_order.asc,title.asc"})
        products = self.get_rows(
            "products",
            {"select": "id,piercing_id,title,image_url,material,size,price,description", "order": "sort_order.asc,title.asc"},
        )

        piercings_by_part: dict[str, list[dict]] = {}
        for piercing in piercings:
            piercings_by_part.setdefault(piercing["body_part_id"], []).append(
                {
                    "id": piercing["id"],
                    "title": piercing["title"],
                    "image": piercing.get("image_url") or "",
                    "products": [],
                }
            )

        piercings_by_id = {
            piercing["id"]: piercing
            for part_piercings in piercings_by_part.values()
            for piercing in part_piercings
        }

        for product in products:
            piercing = piercings_by_id.get(product["piercing_id"])
            if not piercing:
                continue
            piercing["products"].append(
                {
                    "id": product["id"],
                    "title": product["title"],
                    "image": product.get("image_url") or "",
                    "material": product.get("material") or "",
                    "size": product.get("size") or "",
                    "price": product.get("price") or "",
                    "description": product.get("description") or "",
                }
            )

        return {
            "body_parts": [
                {
                    "id": part["id"],
                    "title": part["title"],
                    "piercings": piercings_by_part.get(part["id"], []),
                }
                for part in parts
            ]
        }

    def add_part(self, title: str) -> None:
        rows = self.get_rows("body_parts", {"select": "id"})
        self.insert_row(
            "body_parts",
            {"id": slugify(title, {row["id"] for row in rows}), "title": title, "sort_order": len(rows)},
        )

    def add_piercing(self, part_id: str, title: str, image: str) -> None:
        rows = self.get_rows("piercings", {"select": "id", "body_part_id": f"eq.{part_id}"})
        self.insert_row(
            "piercings",
            {
                "id": slugify(title, {row["id"] for row in rows}),
                "body_part_id": part_id,
                "title": title,
                "image_url": image,
                "sort_order": len(rows),
            },
        )

    def add_product(self, piercing_id: str, product: dict) -> None:
        rows = self.get_rows("products", {"select": "id", "piercing_id": f"eq.{piercing_id}"})
        self.insert_row(
            "products",
            {
                "id": uuid4().hex,
                "piercing_id": piercing_id,
                "title": product["title"],
                "image_url": product.get("image", ""),
                "material": product.get("material", ""),
                "size": product.get("size", ""),
                "price": product.get("price", ""),
                "description": product.get("description", ""),
                "sort_order": len(rows),
            },
        )

    def update_product(self, product_id: str, field: str, value: str) -> None:
        column = "image_url" if field == "image" else field
        self.patch_row("products", product_id, {column: value})

    def delete_product(self, product_id: str) -> None:
        self.delete_row("products", product_id)

    def upload_photo(self, folder: str, file_bytes: bytes, unique_id: str) -> str:
        safe_folder = re.sub(r"[^a-zA-Z0-9_-]+", "-", folder).strip("-") or "images"
        path = f"{safe_folder}/{unique_id}-{uuid4().hex[:8]}.jpg"
        encoded_path = quote(path, safe="/")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "image/jpeg",
            "x-upsert": "true",
        }
        response = requests.post(
            f"{self.url}/storage/v1/object/{self.bucket}/{encoded_path}",
            headers=headers,
            data=file_bytes,
            timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase storage error {response.status_code}: {response.text}")
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{encoded_path}"


def create_store() -> CatalogStore:
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_KEY", "").strip()
    supabase_bucket = os.getenv("SUPABASE_BUCKET", "piercing-images").strip()
    if supabase_url and supabase_key:
        return SupabaseStore(supabase_url, supabase_key, supabase_bucket)
    return LocalJsonStore()


def find_part(catalog: dict, part_id: str) -> dict | None:
    return next((part for part in catalog["body_parts"] if part["id"] == part_id), None)


def find_piercing(part: dict, piercing_id: str) -> dict | None:
    return next((piercing for piercing in part["piercings"] if piercing["id"] == piercing_id), None)


def image_path(path_value: str) -> Path | None:
    if not path_value or path_value.startswith(("http://", "https://")):
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path if path.exists() and path.is_file() else None


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


def send_product(bot: telebot.TeleBot, store: CatalogStore, chat_id: int, part_id: str, piercing_id: str, index: int) -> None:
    catalog = store.load_catalog()
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
    image = product.get("image", "")
    local_photo = image_path(image)
    markup = product_menu(part_id, piercing_id, index, len(products))
    if image.startswith(("http://", "https://")):
        bot.send_photo(chat_id, image, caption=product_caption(product, index, len(products)), reply_markup=markup)
    elif local_photo:
        with local_photo.open("rb") as photo:
            bot.send_photo(chat_id, photo, caption=product_caption(product, index, len(products)), reply_markup=markup)
    else:
        bot.send_message(chat_id, product_caption(product, index, len(products)), reply_markup=markup)


def save_message_photo(bot: telebot.TeleBot, store: CatalogStore, message: types.Message, folder: str) -> str:
    photo = message.photo[-1]
    file_info = bot.get_file(photo.file_id)
    file_bytes = bot.download_file(file_info.file_path)
    if isinstance(store, SupabaseStore):
        return store.upload_photo(folder, file_bytes, photo.file_unique_id)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    relative_path = Path("images") / "admin" / f"{photo.file_unique_id}.jpg"
    destination = BASE_DIR / relative_path
    destination.write_bytes(file_bytes)
    return relative_path.as_posix()


def set_state(user_id: int, step: str, **data) -> None:
    user_states[user_id] = {"step": step, **data}


def clear_state(user_id: int) -> None:
    user_states.pop(user_id, None)


def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Додайте BOT_TOKEN у файл .env або Render Environment Variables")

    store = create_store()
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
                "Додайте його в ADMIN_IDS."
            )
            return
        bot.send_message(message.chat.id, "Адмін-панель:", reply_markup=admin_menu())

    @bot.callback_query_handler(func=lambda call: True)
    def callbacks(call: types.CallbackQuery) -> None:
        data = call.data
        chat_id = call.message.chat.id
        bot.answer_callback_query(call.id)

        try:
            catalog = store.load_catalog()

            if data == "home":
                clear_state(call.from_user.id)
                bot.send_message(chat_id, "Оберіть дію:", reply_markup=main_menu())
                return

            if data == "catalog":
                bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(catalog))
                return

            if data.startswith("body:"):
                part_id = data.split(":")[1]
                part = find_part(catalog, part_id)
                if not part:
                    bot.send_message(chat_id, "Розділ не знайдено.", reply_markup=main_menu())
                    return
                bot.send_message(chat_id, f"{part['title']}\n\nОберіть вид проколу:", reply_markup=piercings_menu(part))
                return

            if data.startswith("piercing:"):
                _, part_id, piercing_id = data.split(":")
                send_product(bot, store, chat_id, part_id, piercing_id, 0)
                return

            if data.startswith("product:"):
                _, part_id, piercing_id, index = data.split(":")
                send_product(bot, store, chat_id, part_id, piercing_id, int(index))
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
                bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(catalog, "admin_piercing_part"))
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
                bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(catalog, "admin_product_part"))
                return

            if data.startswith("admin_product_part:"):
                if deny_admin(call):
                    return
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
                bot.send_message(chat_id, "Оберіть частину тіла:", reply_markup=body_parts_menu(catalog, "admin_edit_part"))
                return

            if data.startswith("admin_edit_part:"):
                if deny_admin(call):
                    return
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
                part = find_part(catalog, part_id)
                piercing = find_piercing(part, piercing_id) if part else None
                product = piercing["products"][int(index)] if piercing and int(index) < len(piercing["products"]) else None
                if not product:
                    bot.send_message(chat_id, "Прикрасу не знайдено.", reply_markup=admin_menu())
                    return
                set_state(call.from_user.id, "edit_product_field", field=field, product_id=product["id"])
                bot.send_message(chat_id, "Введіть нове значення:")
                return

            if data.startswith("admin_edit_photo:"):
                if deny_admin(call):
                    return
                _, part_id, piercing_id, index = data.split(":")
                part = find_part(catalog, part_id)
                piercing = find_piercing(part, piercing_id) if part else None
                product = piercing["products"][int(index)] if piercing and int(index) < len(piercing["products"]) else None
                if not product:
                    bot.send_message(chat_id, "Прикрасу не знайдено.", reply_markup=admin_menu())
                    return
                set_state(call.from_user.id, "edit_product_photo", product_id=product["id"])
                bot.send_message(chat_id, "Надішліть нове фото прикраси:")
                return

            if data.startswith("admin_delete_product:"):
                if deny_admin(call):
                    return
                _, part_id, piercing_id, index = data.split(":")
                part = find_part(catalog, part_id)
                piercing = find_piercing(part, piercing_id) if part else None
                product = piercing["products"][int(index)] if piercing and int(index) < len(piercing["products"]) else None
                if product:
                    store.delete_product(product["id"])
                bot.send_message(chat_id, "Прикрасу видалено.", reply_markup=admin_menu())
                return
        except Exception as error:
            bot.send_message(chat_id, f"Помилка: {error}", reply_markup=main_menu())

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

        try:
            if step == "add_part_title":
                store.add_part(text)
                clear_state(user_id)
                bot.send_message(message.chat.id, "Частину тіла додано.", reply_markup=admin_menu())
                return

            if step == "add_piercing_title":
                state["title"] = text
                state["step"] = "add_piercing_image"
                bot.send_message(message.chat.id, "Надішліть фото виду проколу або напишіть `-`, якщо фото поки немає.")
                return

            if step == "add_piercing_image":
                image = save_message_photo(bot, store, message, "piercings") if message.photo else ""
                store.add_piercing(state["part_id"], state["title"], image)
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
                state["image"] = save_message_photo(bot, store, message, "products") if message.photo else ""
                state["step"] = "add_product_material"
                bot.send_message(message.chat.id, "Введіть матеріал. Наприклад: Титан")
                return

            if step == "add_product_description":
                store.add_product(
                    state["piercing_id"],
                    {
                        "title": state["title"],
                        "image": state.get("image", ""),
                        "material": state["material"],
                        "size": state["size"],
                        "price": state["price"],
                        "description": text,
                    },
                )
                clear_state(user_id)
                bot.send_message(message.chat.id, "Прикрасу додано.", reply_markup=admin_menu())
                return

            if step == "edit_product_field":
                store.update_product(state["product_id"], state["field"], text)
                clear_state(user_id)
                bot.send_message(message.chat.id, "Зміну збережено.", reply_markup=admin_menu())
                return

            if step == "edit_product_photo":
                if not message.photo:
                    bot.send_message(message.chat.id, "Потрібно надіслати саме фото.")
                    return
                image = save_message_photo(bot, store, message, "products")
                store.update_product(state["product_id"], "image", image)
                clear_state(user_id)
                bot.send_message(message.chat.id, "Фото оновлено.", reply_markup=admin_menu())
        except Exception as error:
            clear_state(user_id)
            bot.send_message(message.chat.id, f"Помилка: {error}", reply_markup=admin_menu())

    print("Bot is running...")
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
