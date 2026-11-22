from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database import (
    AsyncSessionLocal, get_active_model, get_context, get_apis, get_models,
)
from bot.handlers.api_handlers import build_apis_text, start_add_api
from bot.handlers.model_handlers import (
    build_models_text, build_select_model, start_add_model,
)
from bot.handlers.context_handlers import build_context_text, start_add_context

log = logging.getLogger(__name__)
router = Router()

HELP_TEXT = """
<b>🤖 Telegram AI Gateway</b>

<b>Управление API:</b>
/add_api — добавить API: <code>Название|ссылка|ключ</code>
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
/cancel — прервать текущий ввод
/settings — текущие настройки и быстрые кнопки
/help — эта справка
"""


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("↩️ Ввод отменён.")


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
        if active else "не выбрана"
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


@router.callback_query(F.data.startswith("cmd:"))
async def cb_settings_cmd(callback: CallbackQuery, state: FSMContext) -> None:
    cmd = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id          # ← человек, а не бот
    msg = callback.message

    await callback.answer()

    if cmd == "api":
        await msg.answer(await build_apis_text(user_id), parse_mode="HTML")

    elif cmd == "models":
        await msg.answer(await build_models_text(user_id), parse_mode="HTML")

    elif cmd == "context":
        await msg.answer(await build_context_text(user_id), parse_mode="HTML")

    elif cmd == "add_api":
        await start_add_api(user_id, state, msg.answer)

    elif cmd == "add_model":
        text, kb = await start_add_model(user_id, state)
        await msg.answer(text, parse_mode="HTML", reply_markup=kb)

    elif cmd == "select_model":
        text, kb = await build_select_model(user_id)
        await msg.answer(text, parse_mode="HTML", reply_markup=kb)

    else:
        await msg.answer("Неизвестная команда.")