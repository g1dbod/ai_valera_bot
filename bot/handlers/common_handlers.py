from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

log = logging.getLogger(__name__)
router = Router(name="common")


def cancel_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")


def cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(cancel_button())
    return b.as_markup()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("↩️ Действие отменено.")


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    had_state = await state.get_state() is not None
    await state.clear()

    text = "↩️ Действие отменено." if had_state else "Нечего отменять."
    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)