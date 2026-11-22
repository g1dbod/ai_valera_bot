from __future__ import annotations

import html
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

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


# ── клавиатура и текст панели ────────────────────────────────────────────────

def context_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Задать контекст", callback_data="ctx:set")
    builder.button(text="🗑 Удалить контекст", callback_data="ctx:del")
    builder.adjust(1)
    return builder.as_markup()


async def build_context_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionLocal() as session:
        ctx = await get_context(session, user_id)

    if not ctx:
        text = (
            "📝 <b>Контекст</b>\n\n"
            "Контекст не задан — системный промпт в запросы не передаётся."
        )
    else:
        text = (
            f"📝 <b>Текущий контекст</b> · {len(ctx)} симв.\n\n"
            f"<pre>{html.escape(ctx[:3000])}</pre>"
        )
        if len(ctx) > 3000:
            text += f"\n<i>…показаны первые 3000 из {len(ctx)} символов</i>"

    return text, context_kb()


# ── ввод контекста ───────────────────────────────────────────────────────────

async def start_add_context(user_id: int, state: FSMContext, send) -> None:
    await state.set_state(AddContextStates.waiting_text)
    await send(ADD_CONTEXT_PROMPT, parse_mode="HTML", reply_markup=cancel_kb())


@router.message(AddContextStates.waiting_text, ~F.text.startswith("/"))
async def save_context(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(
            "Контекст не может быть пустым. Введите текст или нажмите «Отмена»:",
            reply_markup=cancel_kb(),
        )
        return

    async with AsyncSessionLocal() as session:
        await set_context(session, message.from_user.id, text)

    await state.clear()
    log.info("User %d set context (%d chars)", message.from_user.id, len(text))

    panel, kb = await build_context_panel(message.from_user.id)
    await message.answer(
        "✅ Контекст сохранён.\n\n" + panel,
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── команды и кнопки ─────────────────────────────────────────────────────────

@router.message(Command("add_context"))
async def cmd_add_context(message: Message, state: FSMContext) -> None:
    await start_add_context(message.from_user.id, state, message.answer)


@router.message(Command("context"))
async def cmd_show_context(message: Message) -> None:
    text, kb = await build_context_panel(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("delete_context"))
async def cmd_delete_context(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        await clear_context(session, message.from_user.id)

    log.info("User %d cleared context", message.from_user.id)

    text, kb = await build_context_panel(message.from_user.id)
    await message.answer(
        "🗑 Контекст очищен.\n\n" + text,
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "ctx:set")
async def cb_ctx_set(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_add_context(callback.from_user.id, state, callback.message.answer)


@router.callback_query(F.data == "ctx:del")
async def cb_ctx_del(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()

    async with AsyncSessionLocal() as session:
        await clear_context(session, callback.from_user.id)

    log.info("User %d cleared context via button", callback.from_user.id)
    await callback.answer("Контекст очищен")

    text, kb = await build_context_panel(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)