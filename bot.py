import asyncio
from config import bot, dp, logger
from database import init_db
from handlers import settings, main as main_handlers, help as help_handlers
from handlers.settings import set_main_menu

async def start_bot():
    # Инициализация БД
    await init_db()
    
    # Регистрация роутеров
    dp.include_router(settings.router)
    dp.include_router(help_handlers.router)
    dp.include_router(main_handlers.router)
    
    try:
        await set_main_menu()
    except Exception as e:
        logger.error(f"Menu error: {e}")
        
    logger.info("Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")

import asyncio
import logging
import os
import re
import html
import base64
import io
import time
from typing import Dict, Tuple, Optional, List, Set

from aiogram import Bot, Dispatcher, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, BotCommand
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI

from database import init_db, get_config, save_config

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class BotStates(StatesGroup):
    waiting_for_api = State()
    waiting_for_model = State()
    waiting_for_context = State()


# ---------------------------------------------------------------------------
# КЭШИ (живут в памяти процесса, в БД ничего не добавляем)
# ---------------------------------------------------------------------------

# Переиспользуем AsyncOpenAI клиентов (переиспользование TCP/TLS соединений)
_CLIENT_CACHE: Dict[Tuple[str, str], AsyncOpenAI] = {}

# Запоминаем тип модели: "chat_text" / "chat_image" / "image_api"
_MODEL_TYPE_CACHE: Dict[Tuple[int, str, str], str] = {}

# Списки для инлайн-кнопок (обход лимита callback_data и символа "|" в именах)
_MODEL_SELECT_CACHE: Dict[int, List[Tuple[str, str]]] = {}
_API_DELETE_CACHE: Dict[int, List[str]] = {}
_MODEL_DELETE_CACHE: Dict[int, List[Tuple[str, str]]] = {}

# Референсы стиля для моделей вида Recraft Styles (user_id -> [data-URI, ...])
_REFERENCE_CACHE: Dict[int, List[str]] = {}
# (api, model), для которых провайдер потребовал input_references
_NEEDS_REFS: Set[Tuple[str, str]] = set()


def get_client(key: str, url: str) -> AsyncOpenAI:
    cache_key = (key, url)
    client = _CLIENT_CACHE.get(cache_key)
    if client is None:
        client = AsyncOpenAI(api_key=key, base_url=url, timeout=120.0)
        _CLIENT_CACHE[cache_key] = client
    return client


# ---------------------------------------------------------------------------
# ФОРМАТИРОВАНИЕ ТЕКСТА
# ---------------------------------------------------------------------------

def format_to_html(text: str) -> str:
    """Безопасно и аккуратно конвертирует Markdown-подобный текст модели в Telegram HTML."""
    if not text:
        return ""

    placeholders: List[str] = []

    def stash(chunk: str) -> str:
        token = f"\x00PH{len(placeholders)}\x00"
        placeholders.append(chunk)
        return token

    # 1. Блоки кода ```lang\n code ```
    def _code_block(m: re.Match) -> str:
        code = html.escape(m.group(2))
        return stash(f"<pre><code>{code}</code></pre>")

    text = re.sub(r'```(\w*)\n?(.*?)```', _code_block, text, flags=re.DOTALL)

    # 2. Инлайн-код `code`
    def _inline_code(m: re.Match) -> str:
        return stash(f"<code>{html.escape(m.group(1))}</code>")

    text = re.sub(r'`([^`\n]+?)`', _inline_code, text)

    # 3. Экранируем всё остальное
    text = html.escape(text)

    # 4. Заголовки "# Заголовок"
    text = re.sub(r'^#{1,6}\s*(.+?)\s*$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # 5. Жирный **text** / __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # 6. Списки "- item" / "* item" -> "• item" (до курсива, иначе конфликт с *)
    text = re.sub(r'^[ \t]*[-*]\s+', '• ', text, flags=re.MULTILINE)

    # 7. Курсив *text* / _text_
    text = re.sub(r'(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)', r'<i>\1</i>', text)

    # 8. Ссылки [text](url)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', text)

    # 9. Возвращаем код/инлайн-код обратно
    for i, chunk in enumerate(placeholders):
        text = text.replace(f"\x00PH{i}\x00", chunk)

    return text.strip()


def split_text(text: str, max_length: int = 4000) -> List[str]:
    return [text[i:i + max_length] for i in range(0, len(text), max_length)] or [""]


def wrap_answer(answer: str, model_name: str) -> str:
    body = format_to_html(answer) if answer else "<i>(пустой ответ)</i>"
    return f"{body}\n\n<i>— {html.escape(model_name)}</i>"


async def safe_edit(chat_id: int, message_id: int, text: str, parse_mode: Optional[str] = "HTML"):
    """Редактирование сообщения с игнорированием 'message is not modified'."""
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Edit failed: {e}")


def _looks_like_svg(data: bytes) -> bool:
    head = data[:2048].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


async def _get_reference_data_uri(message: types.Message) -> Optional[str]:
    """Скачивает фото/картинку-документ из сообщения и возвращает data-URI base64."""
    file_id, mime = None, "image/jpeg"
    if message.photo:
        file_id = message.photo[-1].file_id  # самое большое разрешение
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id, mime = message.document.file_id, message.document.mime_type
    if not file_id:
        return None
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# НАСТРОЙКА МЕНЮ
# ---------------------------------------------------------------------------

async def set_main_menu():
    main_menu_commands = [
        BotCommand(command="/start", description="Запуск бота"),
        BotCommand(command="/help", description="Помощь"),
        BotCommand(command="/select_model", description="Выбрать модель AI"),
        BotCommand(command="/add_api", description="Добавить API ключ"),
        BotCommand(command="/delete_api", description="Удалить API ключ"),
        BotCommand(command="/add_model", description="Добавить модель к API"),
        BotCommand(command="/delete_model", description="Удалить модель"),
        BotCommand(command="/api", description="Список моих API"),
        BotCommand(command="/models", description="Список моих моделей"),
        BotCommand(command="/add_context", description="Установить контекст"),
        BotCommand(command="/context", description="Посмотреть контекст"),
        BotCommand(command="/delete_context", description="Очистить контекст"),
        BotCommand(command="/clear", description="Очистить историю чата"),
    ]
    await bot.set_my_commands(main_menu_commands)


# ---------------------------------------------------------------------------
# ОБРАБОТЧИКИ КОМАНД
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "🤖 <b>AI Bot Ready!</b>\n\n"
        "Добро пожаловать! Я универсальный интерфейс для AI моделей.\n"
        "Используйте /help или кнопку <b>Меню</b>."
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📚 <b>Справка по командам</b>\n\n"
        "⚙️ <b>Настройка:</b>\n"
        "/add_api — <code>Название:Ключ:URL</code>\n"
        "/delete_api — удалить API\n"
        "/add_model — <code>НазваниеAPI:Модель</code>\n"
        "/delete_model — удалить модель\n"
        "/select_model — выбор модели\n"
        "/add_context — установить системную роль\n"
        "/context — текущий контекст\n"
        "/delete_context — удалить контекст\n\n"
        "🔍 <b>Информация:</b>\n"
        "/api — список API\n"
        "/models — список моделей\n\n"
        "🧹 <b>Управление:</b>\n"
        "/clear — очистить историю"
    )
    await message.answer(text, parse_mode="HTML")


# --- API: добавление ---

@dp.message(Command("add_api"))
async def add_api_start(message: types.Message, state: FSMContext):
    args = message.text.replace("/add_api", "").strip()
    if args:
        parts = args.split(":", 2)
        if len(parts) == 3:
            name, key, url = parts
            config = await get_config(message.from_user.id)
            current_apis = dict(config.apis) if config.apis else {}
            current_apis[name.strip()] = {"key": key.strip(), "url": url.strip()}
            config.apis = current_apis
            await save_config(config)
            return await message.answer(f"✅ API <b>{html.escape(name.strip())}</b> успешно добавлено!", parse_mode="HTML")
    await message.answer("Введите данные в формате: <code>Название:Ключ:URL</code>", parse_mode="HTML")
    await state.set_state(BotStates.waiting_for_api)


@dp.message(BotStates.waiting_for_api)
async def add_api_finish(message: types.Message, state: FSMContext):
    parts = message.text.split(":", 2)
    if len(parts) < 3:
        await message.answer("❌ Ошибка! Формат: <code>Название:Ключ:URL</code>", parse_mode="HTML")
        await state.clear()
        return
    name, key, url = parts
    config = await get_config(message.from_user.id)
    current_apis = dict(config.apis) if config.apis else {}
    current_apis[name.strip()] = {"key": key.strip(), "url": url.strip()}
    config.apis = current_apis
    await save_config(config)
    await message.answer(f"✅ API <b>{html.escape(name.strip())}</b> успешно добавлено!", parse_mode="HTML")
    await state.clear()


# --- API: удаление ---

@dp.message(Command("delete_api"))
async def delete_api_start(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.apis:
        return await message.answer("ℹ️ API ещё не добавлены.")

    names = list(config.apis.keys())
    _API_DELETE_CACHE[message.from_user.id] = names

    builder = InlineKeyboardBuilder()
    for idx, name in enumerate(names):
        builder.row(InlineKeyboardButton(text=f"🗑 {name}", callback_data=f"del_api|{idx}"))
    await message.answer("Выберите API для удаления:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("del_api|"))
async def callback_delete_api(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.split("|", 1)[1])
        name = _API_DELETE_CACHE[callback.from_user.id][idx]
    except (ValueError, IndexError, KeyError):
        await callback.answer("⚠️ Список устарел, вызовите /delete_api заново", show_alert=True)
        return

    config = await get_config(callback.from_user.id)
    apis = dict(config.apis) if config.apis else {}
    apis.pop(name, None)
    config.apis = apis

    # Каскадно удаляем все модели, привязанные к этому API
    models = dict(config.models) if config.models else {}
    models.pop(name, None)
    config.models = models

    # Если это API было выбрано - сбрасываем выбор
    if config.selected_api == name:
        config.selected_api = None
        config.selected_model = None

    await save_config(config)
    await callback.answer(f"API {name} удалено")
    await callback.message.edit_text(f"🗑 API <b>{html.escape(name)}</b> и его модели удалены.", parse_mode="HTML")


# --- Модели: добавление ---

@dp.message(Command("add_model"))
async def add_model_start(message: types.Message, state: FSMContext):
    await message.answer("Введите данные в формате: <code>НазваниеAPI:НазваниеМодели</code>", parse_mode="HTML")
    await state.set_state(BotStates.waiting_for_model)


@dp.message(BotStates.waiting_for_model)
async def add_model_finish(message: types.Message, state: FSMContext):
    parts = message.text.split(":", 1)
    if len(parts) < 2:
        await message.answer("❌ Ошибка! Формат: <code>НазваниеAPI:НазваниеМодели</code>", parse_mode="HTML")
        await state.clear()
        return
    api_name, model_name = parts[0].strip(), parts[1].strip()
    config = await get_config(message.from_user.id)
    if not config.apis or api_name not in config.apis:
        await message.answer("❌ API не найдено. Сначала добавьте его через /add_api.")
        await state.clear()
        return
    current_models = dict(config.models) if config.models else {}
    current_models.setdefault(api_name, [])
    if model_name not in current_models[api_name]:
        current_models[api_name].append(model_name)
    config.models = current_models
    await save_config(config)
    await message.answer(f"✅ Модель <b>{html.escape(model_name)}</b> добавлена!", parse_mode="HTML")
    await state.clear()


# --- Модели: удаление ---

@dp.message(Command("delete_model"))
async def delete_model_start(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.models:
        return await message.answer("ℹ️ Модели ещё не добавлены.")

    flat: List[Tuple[str, str]] = []
    for api, models in config.models.items():
        for m in models:
            flat.append((api, m))

    if not flat:
        return await message.answer("ℹ️ Модели ещё не добавлены.")

    _MODEL_DELETE_CACHE[message.from_user.id] = flat

    builder = InlineKeyboardBuilder()
    for idx, (api, m) in enumerate(flat):
        builder.row(InlineKeyboardButton(text=f"🗑 {api} | {m}", callback_data=f"del_m|{idx}"))
    await message.answer("Выберите модель для удаления:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("del_m|"))
async def callback_delete_model(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.split("|", 1)[1])
        api_name, model_name = _MODEL_DELETE_CACHE[callback.from_user.id][idx]
    except (ValueError, IndexError, KeyError):
        await callback.answer("⚠️ Список устарел, вызовите /delete_model заново", show_alert=True)
        return

    config = await get_config(callback.from_user.id)
    models = dict(config.models) if config.models else {}
    if api_name in models and model_name in models[api_name]:
        models[api_name].remove(model_name)
        if not models[api_name]:
            models.pop(api_name)
    config.models = models

    # Если эта модель была выбрана - сбрасываем выбор
    if config.selected_api == api_name and config.selected_model == model_name:
        config.selected_api = None
        config.selected_model = None

    await save_config(config)
    await callback.answer(f"Модель {model_name} удалена")
    await callback.message.edit_text(
        f"🗑 Модель <b>{html.escape(api_name)} | {html.escape(model_name)}</b> удалена.", parse_mode="HTML"
    )


# --- Выбор модели ---

@dp.message(Command("select_model"))
async def select_model(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.models:
        return await message.answer("❌ Модели не добавлены.")

    flat: List[Tuple[str, str]] = []
    for api, models in config.models.items():
        for m in models:
            flat.append((api, m))

    _MODEL_SELECT_CACHE[message.from_user.id] = flat

    builder = InlineKeyboardBuilder()
    for idx, (api, m) in enumerate(flat):
        builder.row(InlineKeyboardButton(text=f"{api} | {m}", callback_data=f"set_m|{idx}"))
    await message.answer("Выберите модель:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("set_m|"))
async def callback_select_model(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.split("|", 1)[1])
        api_name, model_name = _MODEL_SELECT_CACHE[callback.from_user.id][idx]
    except (ValueError, IndexError, KeyError):
        await callback.answer("⚠️ Список устарел, вызовите /select_model заново", show_alert=True)
        return

    config = await get_config(callback.from_user.id)
    config.selected_api = api_name
    config.selected_model = model_name
    await save_config(config)
    await callback.answer(f"Модель {model_name} выбрана!")
    await callback.message.edit_text(f"✅ Выбрана модель: <b>{html.escape(model_name)}</b>", parse_mode="HTML")


# --- Информационные команды ---

@dp.message(Command("api"))
async def list_apis(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.apis:
        return await message.answer("ℹ️ API ещё не добавлены.")
    lines = "\n".join(f"• {html.escape(name)}" for name in config.apis.keys())
    await message.answer(f"🔑 <b>Ваши API:</b>\n{lines}", parse_mode="HTML")


@dp.message(Command("models"))
async def list_models(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.models:
        return await message.answer("ℹ️ Модели ещё не добавлены.")
    res = ""
    for api, models in config.models.items():
        model_lines = "\n".join(f"  • {html.escape(m)}" for m in models)
        res += f"<b>{html.escape(api)}</b>:\n{model_lines}\n\n"
    for part in split_text(res.strip(), 3500):
        await message.answer(part, parse_mode="HTML")


# --- Контекст ---

@dp.message(Command("add_context"))
async def add_context_start(message: types.Message, state: FSMContext):
    await message.answer("Введите новый системный промпт (контекст):")
    await state.set_state(BotStates.waiting_for_context)


@dp.message(BotStates.waiting_for_context)
async def add_context_finish(message: types.Message, state: FSMContext):
    config = await get_config(message.from_user.id)
    config.context = message.text
    await save_config(config)
    await message.answer("✅ Контекст обновлен!")
    await state.clear()


@dp.message(Command("context"))
async def show_context(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.context:
        return await message.answer("ℹ️ Контекст не установлен.")
    await message.answer(f"📝 <b>Текущий контекст:</b>\n\n{format_to_html(config.context)}", parse_mode="HTML")


@dp.message(Command("delete_context"))
async def delete_context(message: types.Message):
    config = await get_config(message.from_user.id)
    config.context = ""
    await save_config(config)
    await message.answer("🗑 Контекст удален!")


@dp.message(Command("clear"))
async def clear_history(message: types.Message):
    config = await get_config(message.from_user.id)
    config.history = []
    await save_config(config)
    _REFERENCE_CACHE.pop(message.from_user.id, None)
    await message.answer("🗑 История и образец стиля очищены!")


# ---------------------------------------------------------------------------
# ОСНОВНАЯ ЛОГИКА AI
# ---------------------------------------------------------------------------

async def _typing_loop(chat_id: int):
    """Периодически шлёт chat_action 'typing', пока идёт обработка запроса."""
    try:
        while True:
            await bot.send_chat_action(chat_id, "typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


def _extract_image_from_chat_message(msg_obj) -> Optional[bytes]:
    """Достаёт картинку из нестандартного поля 'images' chat-ответа (если есть)."""
    images_list = getattr(msg_obj, "images", None)
    if not images_list or not isinstance(images_list, list):
        return None
    try:
        first = images_list[0]
        img_data = first.get("image_url", {}).get("url", "") if isinstance(first, dict) else ""
        if img_data.startswith("data:image"):
            return base64.b64decode(img_data.split(",")[1])
    except Exception as e:
        logger.warning(f"Не удалось извлечь картинку из chat-ответа: {e}")
    return None


async def _extract_image_from_image_response(img_response) -> Optional[object]:
    """Возвращает либо bytes, либо url картинки из /images ответа."""
    if img_response.data:
        img_obj = img_response.data[0]
        if getattr(img_obj, "url", None):
            return img_obj.url
        if getattr(img_obj, "b64_json", None):
            try:
                return base64.b64decode(img_obj.b64_json)
            except Exception as e:
                logger.warning(f"Не удалось декодировать b64_json: {e}")

    raw = str(img_response)
    urls = re.findall(r'(https?://\S+?\.(?:jpg|jpeg|png|webp|gif))', raw, re.IGNORECASE)
    if urls:
        return urls[0]
    b64_matches = re.findall(r'(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)', raw)
    if b64_matches:
        try:
            return base64.b64decode(b64_matches[0].split(",")[1])
        except Exception as e:
            logger.warning(f"Не удалось декодировать data:image base64: {e}")
    return None


async def _send_svg(message: types.Message, svg_bytes: bytes, caption_html: str):
    """SVG нельзя отправить как фото: шлём PNG-превью (если доступен cairosvg) + SVG-документ."""
    preview = None
    try:
        import cairosvg  # pip install cairosvg (в Docker нужен ещё libcairo2)
        loop = asyncio.get_running_loop()
        preview = await loop.run_in_executor(
            None, lambda: cairosvg.svg2png(bytestring=svg_bytes, output_width=1024)
        )
    except Exception as e:
        logger.info(f"SVG preview unavailable: {e}")

    svg_file = types.BufferedInputFile(svg_bytes, filename="gen.svg")
    if preview:
        await message.answer_photo(
            photo=types.BufferedInputFile(preview, filename="preview.png"),
            caption=caption_html, parse_mode="HTML",
        )
        await message.answer_document(document=svg_file, caption="📐 Векторный оригинал (SVG)")
    else:
        await message.answer_document(document=svg_file, caption=caption_html, parse_mode="HTML")


async def _send_image(message: types.Message, thinking_msg: types.Message, image, caption_html: str):
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=thinking_msg.message_id)
    except TelegramBadRequest:
        pass
    if isinstance(image, bytes):
        if _looks_like_svg(image):
            await _send_svg(message, image, caption_html)
            return
        photo_file = types.BufferedInputFile(image, filename="gen.webp")
        await message.answer_photo(photo=photo_file, caption=caption_html, parse_mode="HTML")
    else:
        await message.answer_photo(photo=image, caption=caption_html, parse_mode="HTML")


async def _run_streaming_chat(client, model, messages, message: types.Message, thinking_msg: types.Message, model_name: str):
    """Стриминг ответа с троттлингом редактирования (быстрее воспринимается пользователем)."""
    full_text = ""
    last_edit = 0.0
    last_sent_len = 0

    stream = await client.chat.completions.create(model=model, messages=messages, stream=True)
    async for chunk in stream:
        delta = ""
        if chunk.choices:
            delta = chunk.choices[0].delta.content or ""
        if delta:
            full_text += delta
            now = time.monotonic()
            if now - last_edit >= 1.2 and len(full_text) - last_sent_len > 20:
                preview = format_to_html(full_text) + " ▌"
                if len(preview) <= 4096:
                    await safe_edit(message.chat.id, thinking_msg.message_id, preview)
                    last_edit = now
                    last_sent_len = len(full_text)

    final_html = wrap_answer(full_text, model_name)
    parts = split_text(final_html)
    await safe_edit(message.chat.id, thinking_msg.message_id, parts[0])
    for part in parts[1:]:
        await message.answer(part, parse_mode="HTML")
    return full_text


async def _run_image_api(client, config, message: types.Message, thinking_msg: types.Message, user_text: str, remember):
    model = config.selected_model
    model_key = (config.selected_api, model)
    user_id = message.from_user.id

    # Новый референс из сообщения (если прислали фото) запоминаем
    new_ref = await _get_reference_data_uri(message)
    if new_ref:
        _REFERENCE_CACHE[user_id] = [new_ref]
    refs = _REFERENCE_CACHE.get(user_id, [])

    needs_refs = model_key in _NEEDS_REFS or "styles" in model.lower()

    if needs_refs and not refs:
        await safe_edit(
            message.chat.id, thinking_msg.message_id,
            "🖼 <b>Этой модели нужен образец стиля.</b>\n\n"
            "Отправьте <b>картинку с подписью-промптом</b> (PNG/JPG/WEBP, от 256 px "
            "по короткой стороне). Картинка запомнится, и дальше можно слать обычные "
            "текстовые промпты.\n"
            "<i>Сбросить образец: /clear</i>",
        )
        return

    logger.info(f"Image API: model={model}, refs={len(refs)}, needs_refs={needs_refs}")

    kwargs = dict(model=model, prompt=user_text, n=1)
    if needs_refs:
        extra = {
            "aspect_ratio": "1:1",
            "input_references": [
                {"type": "image_url", "image_url": {"url": uri}} for uri in refs[:10]
            ],
        }
        if "vector" in model.lower():
            extra["output_format"] = "svg"
        kwargs["extra_body"] = extra
    else:
        kwargs["size"] = "1024x1024"

    img_response = await client.images.generate(**kwargs)

    # Роутер может вернуть ошибку в теле ответа (data=None, error={...}), а не исключением
    api_error = getattr(img_response, "error", None)
    if api_error:
        err_text = str(api_error)
        if "input_references" in err_text.lower() and not needs_refs:
            _NEEDS_REFS.add(model_key)
            return await _run_image_api(client, config, message, thinking_msg, user_text, remember)
        msg = api_error.get("message") if isinstance(api_error, dict) else err_text
        raise RuntimeError(msg or err_text)

    image_to_send = await _extract_image_from_image_response(img_response)

    if image_to_send:
        await _send_image(message, thinking_msg, image_to_send, "🎨 <b>Изображение создано!</b>")
        await remember(user_text, "[Image Generated]")
    else:
        raw_dump = str(img_response)
        await safe_edit(
            message.chat.id,
            thinking_msg.message_id,
            f"⚠️ <b>Image API ответил, но картинка не найдена.</b>\n\n<pre>{html.escape(raw_dump[:1000])}</pre>",
        )


async def handle_ai_request(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.selected_api or not config.selected_model:
        return await message.answer("❌ Сначала выберите модель через /select_model")

    api_info = config.apis.get(config.selected_api)
    if not api_info:
        return await message.answer("❌ Выбранный API не найден, выберите модель заново через /select_model")

    client = get_client(api_info["key"], api_info["url"])
    cache_key = (message.from_user.id, config.selected_api, config.selected_model)
    known_type = _MODEL_TYPE_CACHE.get(cache_key)

    thinking_msg = await message.answer("🤔 <i>Думаю над ответом...</i>", parse_mode="HTML")
    typing_task = asyncio.create_task(_typing_loop(message.chat.id))

    user_text = message.text or message.caption or "Generate an image"
    system_content = config.context or ""
    messages = [{"role": "system", "content": system_content}] + (config.history or []) + [
        {"role": "user", "content": user_text}
    ]

    async def remember(user_msg: str, assistant_msg: str):
        history = list(config.history) if config.history else []
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": assistant_msg})
        config.history = history[-20:]
        await save_config(config)

    try:
        # --- Путь 1: уже известно, что это чистый генератор картинок ---
        if known_type == "image_api":
            await _run_image_api(client, config, message, thinking_msg, user_text, remember)
            return

        # --- Путь 2: уже известно, что это обычная текстовая модель -> стримим ---
        if known_type == "chat_text":
            answer = await _run_streaming_chat(
                client, config.selected_model, messages, message, thinking_msg, config.selected_model
            )
            await remember(user_text, answer or "[пусто]")
            return

        # --- Путь 3: неизвестный/гибридный тип -> обычный (не стриминговый) запрос ---
        try:
            response = await client.chat.completions.create(model=config.selected_model, messages=messages)
        except Exception as e:
            err_str = str(e).lower()
            if "/api/v1/images" in err_str or "image generation model" in err_str:
                _MODEL_TYPE_CACHE[cache_key] = "image_api"
                await _run_image_api(client, config, message, thinking_msg, user_text, remember)
                return
            raise

        # Провайдер может отдать 200 OK, но с пустым choices — трактуем как
        # "эта модель не умеет в chat-формат, нужен /images" и уходим в fallback
        if not response.choices:
            try:
                raw_dump = response.model_dump_json()
            except Exception:
                raw_dump = str(response)
            logger.warning(
                f"Chat API вернул 200 OK без choices для модели {config.selected_model}. "
                f"Переключаюсь на Image API. Raw: {raw_dump[:1500]}"
            )
            _MODEL_TYPE_CACHE[cache_key] = "image_api"
            await _run_image_api(client, config, message, thinking_msg, user_text, remember)
            return

        choice = response.choices[0]
        msg_obj = choice.message
        answer = msg_obj.content if (msg_obj and msg_obj.content) else None
        image_to_send = _extract_image_from_chat_message(msg_obj)

        if image_to_send or answer:
            if image_to_send:
                _MODEL_TYPE_CACHE[cache_key] = "chat_image"
                caption = wrap_answer(answer, config.selected_model) if answer else "🎨 Изображение создано!"
                await _send_image(message, thinking_msg, image_to_send, caption)
            else:
                _MODEL_TYPE_CACHE[cache_key] = "chat_text"
                final_html = wrap_answer(answer, config.selected_model)
                parts = split_text(final_html)
                await safe_edit(message.chat.id, thinking_msg.message_id, parts[0])
                for part in parts[1:]:
                    await message.answer(part, parse_mode="HTML")
            await remember(user_text, answer if answer else "[Image]")
            return

        # Ни текста, ни картинки в ответе - пробуем image API как последний шанс
        await _run_image_api(client, config, message, thinking_msg, user_text, remember)

    except Exception as e:
        logger.error(f"AI Error: {e}", exc_info=True)
        error_text = f"❌ <b>Ошибка API:</b>\n<pre>{html.escape(str(e))}</pre>"
        try:
            await safe_edit(message.chat.id, thinking_msg.message_id, error_text)
        except Exception:
            await message.answer(f"❌ Ошибка API: {html.escape(str(e))}")
    finally:
        typing_task.cancel()


@dp.message(F.text)
async def text_handler(message: types.Message):
    if not message.text.startswith("/") and "|" not in message.text:
        await handle_ai_request(message)


@dp.message(F.photo | F.document | F.voice | F.video)
async def media_handler(message: types.Message):
    await handle_ai_request(message)


async def main():
    await init_db()
    try:
        await set_main_menu()
    except Exception as e:
        logger.error(f"Menu error: {e}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
