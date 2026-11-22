from __future__ import annotations

import logging
import re
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
    waiting_input = State()

ADD_API_PROMPT = (
    "Введите данные API <b>одной строкой</b> в формате:\n\n"
    "<code>Название|ссылка|ключ</code>\n\n"
    "Пример:\n"
    "<code>OpenAI|https://api.openai.com/v1|sk-proj-xxxx</code>\n\n"
    "• <b>Название</b> — как вы будете видеть этот API при выборе модели\n"
    "• <b>ссылка</b> — base URL, заканчивается на <code>/v1</code>\n"
    "• <b>ключ</b> — секретный ключ API"
)


def _mask(key: str) -> str:
    return key[:6] + "..." + key[-4:] if len(key) > 12 else "***"


async def build_apis_text(user_id: int) -> str:
    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, user_id)
    if not apis:
        return "У вас нет добавленных API. Используйте /add_api."
    lines = ["<b>Ваши API-подключения:</b>"]
    for a in apis:
        lines.append(
            f"• <b>{a.name}</b> — {a.base_url} (ключ: <code>{_mask(a.api_key)}</code>)"
        )
    return "\n".join(lines)


async def start_add_api(user_id: int, state: FSMContext, send) -> None:
    await state.set_state(AddAPIStates.waiting_input)
    await send(ADD_API_PROMPT, parse_mode="HTML")


@router.message(Command("add_api"))
async def cmd_add_api(message: Message, state: FSMContext) -> None:
    await start_add_api(message.from_user.id, state, message.answer)


@router.message(AddAPIStates.waiting_input)
async def add_api_input(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()

    # разделители — "|" и переводы строк, пустые куски выбрасываем
    parts = [p.strip() for p in re.split(r"[|\n]+", raw) if p.strip()]

    if len(parts) < 3:
        await message.answer(
            "❌ Нужно три значения: <b>название</b>, <b>ссылка</b> и <b>ключ</b>.\n\n"
            "Можно одной строкой:\n"
            "<code>Название|ссылка|ключ</code>\n\n"
            "Или в три строки:\n"
            "<code>Название\nссылка\nключ</code>",
            parse_mode="HTML",
        )
        return

    name, url = parts[0], parts[1]
    key = "|".join(parts[2:])          # если в ключе случайно есть "|"

    if not name or len(name) > 64:
        await message.answer("❌ Название должно быть от 1 до 64 символов. Попробуйте ещё раз:")
        return

    if not url.startswith(("http://", "https://")):
        await message.answer(
            "❌ Ссылка должна начинаться с <code>http://</code> или <code>https://</code>.",
            parse_mode="HTML",
        )
        return

    url = url.rstrip("/")

    async with AsyncSessionLocal() as session:
        existing = await get_api_by_name(session, message.from_user.id, name)
    if existing:
        await message.answer(
            f"❌ API с названием «{name}» уже существует. Введите другое название:"
        )
        return

    async with AsyncSessionLocal() as session:
        api = await add_api(session, message.from_user.id, name, key, url)

    await state.clear()
    log.info("User %d added API '%s' (%s)", message.from_user.id, api.name, api.base_url)

    masked = api.api_key[:6] + "..." + api.api_key[-4:] if len(api.api_key) > 12 else "***"
    await message.answer(
        f"✅ API добавлен: <b>{api.name}</b>\n"
        f"URL: <code>{api.base_url}</code>\n"
        f"Ключ: <code>{masked}</code>\n\n"
        "Теперь добавьте модель через /add_model.",
        parse_mode="HTML",
    )


@router.message(Command("api"))
async def cmd_list_apis(message: Message) -> None:
    text = await build_apis_text(message.from_user.id)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("delete_api"))
async def cmd_delete_api(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, message.from_user.id)
    if not apis:
        await message.answer("Нет API для удаления.")
        return
    builder = InlineKeyboardBuilder()
    for a in apis:
        builder.button(text=a.name, callback_data=f"del_api:{a.id}")
    builder.adjust(2)
    await message.answer(
        "Выберите API для удаления "
        "(вместе с ним удалятся все модели, привязанные к нему):",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("del_api:"))
async def cb_delete_api(callback: CallbackQuery) -> None:
    api_id = int(callback.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, callback.from_user.id)
        api = next((a for a in apis if a.id == api_id), None)
        if api is None:
            await callback.answer("API не найден.", show_alert=True)
            return
        name = api.name
        await delete_api(session, callback.from_user.id, name)

    log.info("User %d deleted API '%s'", callback.from_user.id, name)
    await callback.message.edit_text(f"🗑 API <b>{name}</b> удалён.", parse_mode="HTML")