from __future__ import annotations

import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

from bot.database import AsyncSessionLocal, get_active_model, get_context, get_apis, get_models

log = logging.getLogger(__name__)
router = Router()

HELP_TEXT = """
<b>🤖 Telegram AI Gateway</b>

<b>Управление API:</b>
/add_api — добавить API одной строкой: <code>Название|ссылка|ключ</code>
/api — список подключений
/delete_api — удалить подключение

<b>Управление моделями:</b>
/add_model — добавить модель (сначала выбор API)
/models — список моделей
/select_model — выбрать активную модель
/delete_model — удалить модель

<b>Контекст (system prompt):</b>
/add_context — задать инструкции
/context — посмотреть контекст
/delete_context — очистить контекст

<b>Диалог:</b>
/clear — очистить историю чата

<b>Прочее:</b>
/settings — текущие настройки и быстрые кнопки
/help — эта справка
/cancel — прервать текущий ввод

<b>Типы моделей:</b>
• <b>chat</b> — текстовый диалог (со стримингом), поддерживает изображения
• <b>image</b> — генерация изображений
• <b>svg</b> — генерация SVG (файл + PNG-превью)
• <b>styled</b> — генерация с референсным изображением-стилем
"""

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("↩️ Ввод отменён. Можете начать заново.")

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        active = await get_active_model(session, user_id)
        ctx = await get_context(session, user_id)
        api_count = len(await get_apis(session, user_id))
        model_count = len(await get_models(session, user_id))

    active_str = (
        f"<b>{active.name}</b> (<code>{active.model_id}</code>, {active.model_type})"
        if active
        else "не выбрана"
    )
    ctx_str = f"{len(ctx)} символов" if ctx else "не задан"

    text = (
        f"<b>⚙️ Текущие настройки</b>\n\n"
        f"Активная модель: {active_str}\n"
        f"API-подключений: {api_count}\n"
        f"Моделей: {model_count}\n"
        f"Контекст: {ctx_str}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить API", callback_data="cmd:add_api")
    builder.button(text="➕ Добавить модель", callback_data="cmd:add_model")
    builder.button(text="🔄 Выбрать модель", callback_data="cmd:select_model")
    builder.button(text="📋 Мои API", callback_data="cmd:api")
    builder.button(text="📋 Мои модели", callback_data="cmd:models")
    builder.button(text="📝 Контекст", callback_data="cmd:context")
    builder.adjust(2)

    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


# Route settings-panel buttons back to their commands
from aiogram import F
from aiogram.types import CallbackQuery


@router.callback_query(F.data.startswith("cmd:"))
async def cb_settings_cmd(callback: CallbackQuery) -> None:
    cmd = callback.data.split(":", 1)[1]
    fake = callback.message
    # Simply re-send as if user typed the command
    await callback.answer()
    dispatch = {
        "add_api": "Используйте команду /add_api",
        "add_model": "Используйте команду /add_model",
        "select_model": "Используйте команду /select_model",
        "api": None,
        "models": None,
        "context": None,
    }
    # For list commands we can inline-call their logic
    if cmd == "api":
        from bot.handlers.api_handlers import cmd_list_apis
        await cmd_list_apis(callback.message)
    elif cmd == "models":
        from bot.handlers.model_handlers import cmd_list_models
        await cmd_list_models(callback.message)
    elif cmd == "context":
        from bot.handlers.context_handlers import cmd_show_context
        await cmd_show_context(callback.message)
    else:
        await callback.message.answer(dispatch.get(cmd, ""))