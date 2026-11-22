from __future__ import annotations

import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database import AsyncSessionLocal, get_context, set_context, clear_context

log = logging.getLogger(__name__)
router = Router()


class AddContextStates(StatesGroup):
    waiting_text = State()


@router.message(Command("add_context"))
async def cmd_add_context(message: Message, state: FSMContext) -> None:
    await state.set_state(AddContextStates.waiting_text)
    await message.answer(
        "Введите <b>системный контекст / инструкции</b> для AI.\n"
        "Этот текст будет передаваться как system-prompt в каждом запросе.",
        parse_mode="HTML",
    )


@router.message(AddContextStates.waiting_text)
async def save_context(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Контекст не может быть пустым. Попробуйте снова:")
        return
    async with AsyncSessionLocal() as session:
        await set_context(session, message.from_user.id, text)
    await state.clear()
    log.info("User %d set context (%d chars)", message.from_user.id, len(text))
    await message.answer("✅ Контекст сохранён.")


@router.message(Command("delete_context"))
async def cmd_delete_context(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        await clear_context(session, message.from_user.id)
    log.info("User %d cleared context", message.from_user.id)
    await message.answer("🗑 Контекст очищен.")


@router.message(Command("context"))
async def cmd_show_context(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        ctx = await get_context(session, message.from_user.id)
    if not ctx:
        await message.answer("Контекст не задан.")
    else:
        await message.answer(
            f"<b>Текущий контекст:</b>\n<pre>{ctx[:3000]}</pre>", parse_mode="HTML"
        )