from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database import AsyncSessionLocal, add_api, get_apis, delete_api, get_api_by_name

log = logging.getLogger(__name__)
router = Router()


class AddAPIStates(StatesGroup):
    waiting_name = State()
    waiting_key = State()
    waiting_url = State()


class DeleteAPIStates(StatesGroup):
    waiting_name = State()


@router.message(Command("add_api"))
async def cmd_add_api(message: Message, state: FSMContext) -> None:
    await state.set_state(AddAPIStates.waiting_name)
    await message.answer(
        "Введите <b>название</b> для этого API-подключения (любое короткое имя):",
        parse_mode="HTML",
    )


@router.message(AddAPIStates.waiting_name)
async def add_api_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Попробуйте ещё раз:")
        return
    async with AsyncSessionLocal() as session:
        existing = await get_api_by_name(session, message.from_user.id, name)
    if existing:
        await message.answer(f"API с именем «{name}» уже существует. Введите другое имя:")
        return
    await state.update_data(name=name)
    await state.set_state(AddAPIStates.waiting_key)
    await message.answer("Введите <b>API-ключ</b>:", parse_mode="HTML")


@router.message(AddAPIStates.waiting_key)
async def add_api_key(message: Message, state: FSMContext) -> None:
    key = (message.text or "").strip()
    if not key:
        await message.answer("Ключ не может быть пустым:")
        return
    await state.update_data(api_key=key)
    await state.set_state(AddAPIStates.waiting_url)
    await message.answer(
        "Введите <b>Base URL</b> API (например, <code>https://api.openai.com/v1</code>):",
        parse_mode="HTML",
    )


@router.message(AddAPIStates.waiting_url)
async def add_api_url(message: Message, state: FSMContext) -> None:
    url = (message.text or "").strip().rstrip("/")
    if not url.startswith("http"):
        await message.answer("URL должен начинаться с http:// или https://. Попробуйте снова:")
        return
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        api = await add_api(session, message.from_user.id, data["name"], data["api_key"], url)
    await state.clear()
    log.info("User %d added API '%s'", message.from_user.id, api.name)
    await message.answer(f"✅ API <b>{api.name}</b> добавлен.", parse_mode="HTML")


@router.message(Command("api"))
async def cmd_list_apis(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, message.from_user.id)
    if not apis:
        await message.answer("У вас нет добавленных API. Используйте /add_api.")
        return
    lines = [f"<b>Ваши API-подключения:</b>"]
    for a in apis:
        masked = a.api_key[:6] + "..." + a.api_key[-4:] if len(a.api_key) > 12 else "***"
        lines.append(f"• <b>{a.name}</b> — {a.base_url} (ключ: <code>{masked}</code>)")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("delete_api"))
async def cmd_delete_api(message: Message, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, message.from_user.id)
    if not apis:
        await message.answer("Нет API для удаления.")
        return
    builder = InlineKeyboardBuilder()
    for a in apis:
        builder.button(text=a.name, callback_data=f"del_api:{a.name}")
    builder.adjust(2)
    await message.answer("Выберите API для удаления:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("del_api:"))
async def cb_delete_api(callback: CallbackQuery) -> None:
    name = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        removed = await delete_api(session, callback.from_user.id, name)
    if removed:
        log.info("User %d deleted API '%s'", callback.from_user.id, name)
        await callback.message.edit_text(f"🗑 API <b>{name}</b> удалён.", parse_mode="HTML")
    else:
        await callback.answer("API не найден.", show_alert=True)