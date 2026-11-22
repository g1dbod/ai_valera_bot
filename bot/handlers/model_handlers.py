from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder


from bot.database import (
    AsyncSessionLocal, add_model, get_models, get_apis,
    delete_model, set_active_model, get_model_by_name,
)

log = logging.getLogger(__name__)
router = Router()


class AddModelStates(StatesGroup):
    waiting_api = State()
    waiting_name = State()
    waiting_model_id = State()
    waiting_type = State()
    waiting_style_image = State()


async def build_models_text(user_id: int) -> str:
    async with AsyncSessionLocal() as session:
        models = await get_models(session, user_id)
    if not models:
        return "Нет добавленных моделей. Используйте /add_model."

    lines = ["<b>Ваши модели:</b>"]
    for m in models:
        active = " ✅" if m.is_active else ""
        api_name = m.api.name if m.api else "?"
        lines.append(
            f"• <b>{m.name}</b> — <code>{m.model_id}</code> "
            f"[{m.model_type}] · API: {api_name}{active}"
        )
    return "\n".join(lines)


async def start_add_model(
    user_id: int, state: FSMContext
) -> tuple[str, InlineKeyboardMarkup | None]:
    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, user_id)

    if not apis:
        return "Сначала добавьте API через /add_api.", None

    await state.set_state(AddModelStates.waiting_api)

    builder = InlineKeyboardBuilder()
    for a in apis:
        builder.button(text=a.name, callback_data=f"addm_api:{a.id}")
    builder.adjust(2)

    return (
        "Выберите <b>API</b>, к которому будет привязана новая модель:",
        builder.as_markup(),
    )


async def build_select_model(
    user_id: int,
) -> tuple[str, InlineKeyboardMarkup | None]:
    async with AsyncSessionLocal() as session:
        models = await get_models(session, user_id)

    if not models:
        return "Нет моделей для выбора. Используйте /add_model.", None

    builder = InlineKeyboardBuilder()
    for m in models:
        label = f"{'✅ ' if m.is_active else ''}{m.name} [{m.model_type}]"
        builder.button(text=label, callback_data=f"activate:{m.id}")
    builder.adjust(1)

    return "Выберите активную модель:", builder.as_markup()

# ── /add_model ───────────────────────────────────────────────────────────────

@router.message(Command("add_model"))
async def cmd_add_model(message: Message, state: FSMContext) -> None:
    text, kb = await start_add_model(message.from_user.id, state)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("addm_api:"), AddModelStates.waiting_api)
async def add_model_choose_api(callback: CallbackQuery, state: FSMContext) -> None:
    api_id = int(callback.data.split(":", 1)[1])

    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, callback.from_user.id)
    api = next((a for a in apis if a.id == api_id), None)

    if api is None:
        await callback.answer("API не найден.", show_alert=True)
        return

    await state.update_data(api_id=api.id, api_name=api.name)
    await state.set_state(AddModelStates.waiting_name)

    await callback.message.edit_text(
        f"API: <b>{api.name}</b>\n\n"
        "Введите <b>название модели</b> — как вы будете её видеть в боте "
        "(например, <code>GPT-4o</code>):",
        parse_mode="HTML",
    )


@router.message(AddModelStates.waiting_name)
async def add_model_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 128:
        await message.answer("Название должно быть от 1 до 128 символов. Попробуйте ещё раз:")
        return

    async with AsyncSessionLocal() as session:
        existing = await get_model_by_name(session, message.from_user.id, name)
    if existing:
        await message.answer(f"❌ Модель «{name}» уже существует. Введите другое название:")
        return

    await state.update_data(name=name)
    await state.set_state(AddModelStates.waiting_model_id)
    await message.answer(
        "Введите <b>ID модели</b> у провайдера (например, <code>gpt-4o</code>, "
        "<code>dall-e-3</code>, <code>llama-3.1-70b</code>):",
        parse_mode="HTML",
    )


@router.message(AddModelStates.waiting_model_id)
async def add_model_id(message: Message, state: FSMContext) -> None:
    model_id = (message.text or "").strip()
    if not model_id or len(model_id) > 256:
        await message.answer("ID модели должен быть от 1 до 256 символов. Попробуйте ещё раз:")
        return

    await state.update_data(model_id=model_id)
    await state.set_state(AddModelStates.waiting_type)

    labels = {
        "chat": "💬 Чат",
        "image": "🖼 Генерация изображений",
        "svg": "📐 SVG-генерация",
        "styled": "🎨 Стилизованная генерация",
    }
    builder = InlineKeyboardBuilder()
    for t, label in labels.items():
        builder.button(text=label, callback_data=f"sel_type:{t}")
    builder.adjust(2)

    await message.answer(
        "Выберите <b>тип модели</b>:", parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("sel_type:"), AddModelStates.waiting_type)
async def add_model_type(callback: CallbackQuery, state: FSMContext) -> None:
    mtype = callback.data.split(":", 1)[1]
    await state.update_data(model_type=mtype)

    if mtype == "styled":
        await state.set_state(AddModelStates.waiting_style_image)
        await callback.message.edit_text(
            "Отправьте <b>референсное изображение</b> (стиль) для этой модели:",
            parse_mode="HTML",
        )
    else:
        await _finish_add_model(callback.message, state, callback.from_user.id)


@router.message(AddModelStates.waiting_style_image, F.photo)
async def add_model_style_image(message: Message, state: FSMContext) -> None:
    await state.update_data(style_image_file_id=message.photo[-1].file_id)
    await _finish_add_model(message, state, message.from_user.id)


@router.message(AddModelStates.waiting_style_image)
async def add_model_style_image_bad(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте изображение (фото).")


async def _finish_add_model(message: Message, state: FSMContext, user_id: int) -> None:
    data = await state.get_data()

    async with AsyncSessionLocal() as session:
        model = await add_model(
            session,
            user_id=user_id,
            name=data["name"],
            model_id=data["model_id"],
            api_id=data["api_id"],
            model_type=data.get("model_type", "chat"),
            style_image_file_id=data.get("style_image_file_id"),
        )

    await state.clear()
    log.info(
        "User %d added model '%s' (id=%s, type=%s, api=%s)",
        user_id, model.name, model.model_id, model.model_type, data.get("api_name"),
    )

    await message.answer(
        f"✅ Модель <b>{model.name}</b> добавлена\n"
        f"API: <b>{data.get('api_name')}</b>\n"
        f"ID: <code>{model.model_id}</code>\n"
        f"Тип: {model.model_type}\n\n"
        "Сделайте её активной через /select_model.",
        parse_mode="HTML",
    )


# ── /models, /select_model, /delete_model ────────────────────────────────────

@router.message(Command("models"))
async def cmd_list_models(message: Message) -> None:
    text = await build_models_text(message.from_user.id)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("select_model"))
async def cmd_select_model(message: Message) -> None:
    text, kb = await build_select_model(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("activate:"))
async def cb_activate_model(callback: CallbackQuery) -> None:
    model_db_id = int(callback.data.split(":", 1)[1])

    async with AsyncSessionLocal() as session:
        models = await get_models(session, callback.from_user.id)
        target = next((m for m in models if m.id == model_db_id), None)
        if target is None:
            await callback.answer("Модель не найдена.", show_alert=True)
            return
        model = await set_active_model(session, callback.from_user.id, target.name)

    log.info("User %d activated model '%s'", callback.from_user.id, target.name)
    await callback.message.edit_text(
        f"✅ Активная модель: <b>{model.name}</b> (<code>{model.model_id}</code>, {model.model_type})",
        parse_mode="HTML",
    )


@router.message(Command("delete_model"))
async def cmd_delete_model(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        models = await get_models(session, message.from_user.id)
    if not models:
        await message.answer("Нет моделей для удаления.")
        return

    builder = InlineKeyboardBuilder()
    for m in models:
        builder.button(text=m.name, callback_data=f"del_model:{m.id}")
    builder.adjust(2)

    await message.answer("Выберите модель для удаления:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("del_model:"))
async def cb_delete_model(callback: CallbackQuery) -> None:
    model_db_id = int(callback.data.split(":", 1)[1])

    async with AsyncSessionLocal() as session:
        models = await get_models(session, callback.from_user.id)
        target = next((m for m in models if m.id == model_db_id), None)
        if target is None:
            await callback.answer("Модель не найдена.", show_alert=True)
            return
        name = target.name
        await delete_model(session, callback.from_user.id, name)

    log.info("User %d deleted model '%s'", callback.from_user.id, name)
    await callback.message.edit_text(f"🗑 Модель <b>{name}</b> удалена.", parse_mode="HTML")