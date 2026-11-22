from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database import (
    AsyncSessionLocal, add_model, get_models, get_apis,
    delete_model, set_active_model, get_model_by_name, get_api_by_name,
)
from bot.services.image_service import telegram_file_to_base64

log = logging.getLogger(__name__)
router = Router()

MODEL_TYPES = ["chat", "image", "svg", "styled"]


class AddModelStates(StatesGroup):
    waiting_name = State()
    waiting_api = State()
    waiting_model_id = State()
    waiting_type = State()
    waiting_style_image = State()


class SelectModelStates(StatesGroup):
    waiting_choice = State()


class DeleteModelStates(StatesGroup):
    waiting_choice = State()


@router.message(Command("add_model"))
async def cmd_add_model(message: Message, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, message.from_user.id)
    if not apis:
        await message.answer("Сначала добавьте API через /add_api.")
        return
    await state.set_state(AddModelStates.waiting_name)
    await message.answer("Введите <b>название</b> для модели:", parse_mode="HTML")


@router.message(AddModelStates.waiting_name)
async def add_model_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым:")
        return
    async with AsyncSessionLocal() as session:
        existing = await get_model_by_name(session, message.from_user.id, name)
    if existing:
        await message.answer(f"Модель «{name}» уже существует. Введите другое имя:")
        return
    await state.update_data(name=name)
    await state.set_state(AddModelStates.waiting_api)

    async with AsyncSessionLocal() as session:
        apis = await get_apis(session, message.from_user.id)
    builder = InlineKeyboardBuilder()
    for a in apis:
        builder.button(text=a.name, callback_data=f"sel_api_m:{a.name}")
    builder.adjust(2)
    await message.answer("Выберите API для этой модели:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("sel_api_m:"), AddModelStates.waiting_api)
async def add_model_api(callback: CallbackQuery, state: FSMContext) -> None:
    api_name = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        api = await get_api_by_name(session, callback.from_user.id, api_name)
    if not api:
        await callback.answer("API не найден.", show_alert=True)
        return
    await state.update_data(api_id=api.id)
    await state.set_state(AddModelStates.waiting_model_id)
    await callback.message.edit_text(
        "Введите <b>ID модели</b> (например, <code>gpt-4o</code>):", parse_mode="HTML"
    )


@router.message(AddModelStates.waiting_model_id)
async def add_model_id(message: Message, state: FSMContext) -> None:
    model_id = (message.text or "").strip()
    if not model_id:
        await message.answer("ID модели не может быть пустым:")
        return
    await state.update_data(model_id=model_id)
    await state.set_state(AddModelStates.waiting_type)

    builder = InlineKeyboardBuilder()
    labels = {
        "chat": "💬 Чат",
        "image": "🖼 Генерация изображений",
        "svg": "📐 SVG-генерация",
        "styled": "🎨 Стилизованная генерация",
    }
    for t, label in labels.items():
        builder.button(text=label, callback_data=f"sel_type:{t}")
    builder.adjust(2)
    await message.answer("Выберите <b>тип модели</b>:", reply_markup=builder.as_markup(), parse_mode="HTML")


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
    file_id = message.photo[-1].file_id
    await state.update_data(style_image_file_id=file_id)
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
    log.info("User %d added model '%s' (type=%s)", user_id, model.name, model.model_type)
    await message.answer(
        f"✅ Модель <b>{model.name}</b> добавлена (тип: {model.model_type}).\n"
        "Выберите её активной через /select_model.",
        parse_mode="HTML",
    )


@router.message(Command("models"))
async def cmd_list_models(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        models = await get_models(session, message.from_user.id)
    if not models:
        await message.answer("Нет добавленных моделей. Используйте /add_model.")
        return
    lines = ["<b>Ваши модели:</b>"]
    for m in models:
        active = " ✅" if m.is_active else ""
        lines.append(f"• <b>{m.name}</b> — <code>{m.model_id}</code> [{m.model_type}]{active}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("select_model"))
async def cmd_select_model(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        models = await get_models(session, message.from_user.id)
    if not models:
        await message.answer("Нет моделей для выбора. Используйте /add_model.")
        return
    builder = InlineKeyboardBuilder()
    for m in models:
        label = f"{'✅ ' if m.is_active else ''}{m.name} [{m.model_type}]"
        builder.button(text=label, callback_data=f"activate:{m.name}")
    builder.adjust(1)
    await message.answer("Выберите активную модель:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("activate:"))
async def cb_activate_model(callback: CallbackQuery) -> None:
    name = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        model = await set_active_model(session, callback.from_user.id, name)
    if model:
        log.info("User %d activated model '%s'", callback.from_user.id, name)
        await callback.message.edit_text(
            f"✅ Активная модель: <b>{model.name}</b> (<code>{model.model_id}</code>)",
            parse_mode="HTML",
        )
    else:
        await callback.answer("Модель не найдена.", show_alert=True)


@router.message(Command("delete_model"))
async def cmd_delete_model(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        models = await get_models(session, message.from_user.id)
    if not models:
        await message.answer("Нет моделей для удаления.")
        return
    builder = InlineKeyboardBuilder()
    for m in models:
        builder.button(text=m.name, callback_data=f"del_model:{m.name}")
    builder.adjust(2)
    await message.answer("Выберите модель для удаления:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("del_model:"))
async def cb_delete_model(callback: CallbackQuery) -> None:
    name = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        removed = await delete_model(session, callback.from_user.id, name)
    if removed:
        log.info("User %d deleted model '%s'", callback.from_user.id, name)
        await callback.message.edit_text(f"🗑 Модель <b>{name}</b> удалена.", parse_mode="HTML")
    else:
        await callback.answer("Модель не найдена.", show_alert=True)