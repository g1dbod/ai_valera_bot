from __future__ import annotations

import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database import AsyncSessionLocal, get_context, set_context, clear_context

from bot.handlers.common_handlers import cancel_kb

log = logging.getLogger(__name__)
router = Router()


class AddContextStates(StatesGroup):
    waiting_text = State()

ADD_CONTEXT_PROMPT = (
    "Введите <b>системный контекст / инструкции</b> для AI.\n"
    "Этот текст будет передаваться как system-prompt в каждом запросе."
)


async def build_context_text(user_id: int) -> str:
    async with AsyncSessionLocal() as session:
        ctx = await get_context(session, user_id)
    if not ctx:
        return "Контекст не задан."
    return f"<b>Текущий контекст:</b>\n<pre>{ctx[:3000]}</pre>"


async def start_add_context(user_id: int, state: FSMContext, send) -> None:
    await state.set_state(AddContextStates.waiting_text)
    await send(ADD_CONTEXT_PROMPT, parse_mode="HTML", reply_markup=cancel_kb())

@router.message(Command("add_context"))
async def cmd_add_context(message: Message, state: FSMContext) -> None:
    await start_add_context(message.from_user.id, state, message.answer)


@router.message(AddContextStates.waiting_text, ~F.text.startswith("/"))
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
    text = await build_context_text(message.from_user.id)
    await message.answer(text, parse_mode="HTML")