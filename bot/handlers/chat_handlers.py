from __future__ import annotations

import io
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command, StateFilter

from bot.database import (
    AsyncSessionLocal, get_active_model, get_context,
    append_history, get_history, clear_history,
)
from bot.services.ai_service import stream_chat, generate_image, generate_svg
from bot.services.image_service import (
    telegram_file_to_base64,
    url_to_bytes,
    base64_to_bytes,
    svg_to_png_bytes,
)

import asyncio
import time

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from bot.utils.formatting import format_to_html, split_text

log = logging.getLogger(__name__)
router = Router()

# Minimum chars before we update the streaming message
STREAM_UPDATE_THRESHOLD = 60


async def _get_active(user_id: int):
    async with AsyncSessionLocal() as session:
        model = await get_active_model(session, user_id)
        if model is None:
            return None, None, None
        api = model.api
        if api is None:
            return model, None, None      # модель есть, но API потерян
        context = await get_context(session, user_id)
        return model, api, context


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Я универсальный шлюз к AI-моделям.\n\n"
        "Добавьте API (/add_api), зарегистрируйте модель (/add_model), "
        "выберите активную (/select_model) — и просто пишите мне.\n\n"
        "/help — подробная справка.",
    )


@router.message(Command("clear"))
async def cmd_clear_history(message: Message) -> None:
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        model = await get_active_model(session, user_id)
        if model is None:
            await message.answer("Нет активной модели — история и так пуста.")
            return
        await clear_history(session, user_id, model.id)
    log.info("User %d cleared chat history for model id=%d", user_id, model.id)
    await message.answer("🗑 История чата очищена. Начинаем с чистого листа.")

@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    model, api, context = await _get_active(user_id)
    if model is None:
        await message.answer("Выберите активную модель через /select_model.")
        return
    if api is None:
        await message.answer("У активной модели потеряно API-подключение. Удалите её через /delete_model и добавьте заново.")
        return

    text = message.text or ""
    log.info("User %d → model '%s' type=%s", user_id, model.name, model.model_type)

    if model.model_type == "image":
        await _handle_image_gen(message, bot, api, model, text, None)
    elif model.model_type == "svg":
        await _handle_svg_gen(message, bot, api, model, context, text)
    elif model.model_type == "styled":
        await _handle_styled_gen(message, bot, api, model, text)
    else:
        await _handle_chat(message, api, model, context, text, None)



@router.message(StateFilter(None), F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    model, api, context = await _get_active(user_id)
    if model is None:
        await message.answer("Выберите активную модель через /select_model.")
        return

    caption = message.caption or ""
    image_b64 = await telegram_file_to_base64(bot, message.photo[-1].file_id)
    log.info("User %d sent photo → model '%s'", user_id, model.name)

    if model.model_type == "image":
        await _handle_image_gen(message, bot, api, model, caption or "describe this image", image_b64)
    elif model.model_type == "svg":
        await _handle_svg_gen(message, bot, api, model, context, caption or "create SVG from this")
    else:
        await _handle_chat(message, api, model, context, caption or "Опиши изображение.", image_b64)


TAIL_LIMIT = 3900
EDIT_EVERY = 1.5      # сек между правками сообщения (флуд-контроль Telegram)


def _tail(text: str) -> str:
    if len(text) <= TAIL_LIMIT:
        return text
    return "…" + text[-TAIL_LIMIT:]


async def _handle_chat(message, api, model, context, text, image_b64):
    placeholder = await message.answer("⏳")
    accumulated = ""
    last_edit = 0.0

    async with AsyncSessionLocal() as session:
        history = await get_history(session, message.from_user.id, model.id)

    try:
        async for chunk in stream_chat(
            api.api_key, api.base_url, model.model_id,
            context, text, image_b64, history=history,
        ):
            accumulated += chunk
            now = time.monotonic()
            if now - last_edit < EDIT_EVERY:
                continue
            try:
                await placeholder.edit_text(_tail(accumulated))
                last_edit = now
            except TelegramRetryAfter as exc:
                last_edit = now + exc.retry_after
            except TelegramBadRequest:
                pass

        if not accumulated.strip():
            await placeholder.edit_text("_(пустой ответ)_")
            return

        # финальный рендер: удаляем «⏳» и отправляем оформленный текст
        try:
            await placeholder.delete()
        except TelegramBadRequest:
            pass

        for i, chunk in enumerate(split_text(accumulated)):
            try:
                await message.answer(format_to_html(chunk), parse_mode="HTML")
            except TelegramBadRequest:
                await message.answer(chunk)          # страховка от битой разметки

        async with AsyncSessionLocal() as session:
            await append_history(session, message.from_user.id, model.id, "user", text)
            await append_history(
                session, message.from_user.id, model.id, "assistant", accumulated
            )

    except Exception as exc:
        log.exception("stream_chat error for user %d", message.from_user.id)
        brief = str(exc).split("\n")[0][:200]
        try:
            await placeholder.edit_text(f"❌ {brief}")
        except TelegramBadRequest:
            await message.answer(f"❌ {brief}")


async def _get_message_text(msg) -> str:
    return msg.text or ""


async def _handle_image_gen(message, bot, api, model, prompt, image_b64):
    placeholder = await message.answer("🎨 Генерирую изображение...")
    try:
        result = await generate_image(
            api.api_key, api.base_url, model.model_id, prompt, image_b64
        )
        if result.startswith("http"):
            img_bytes = await url_to_bytes(result)
        else:
            img_bytes = await base64_to_bytes(result)

        if img_bytes:
            await message.answer_photo(
                BufferedInputFile(img_bytes, filename="image.jpg"),
                caption=f"🖼 {prompt[:200]}",
            )
            await placeholder.delete()
        else:
            await placeholder.edit_text("❌ Не удалось получить изображение.")
    except Exception as exc:
        log.exception("image gen error user %d", message.from_user.id)
        await placeholder.edit_text(f"❌ Ошибка генерации: {exc}")


async def _handle_svg_gen(message, bot, api, model, context, text):
    placeholder = await message.answer("📐 Генерирую SVG...")
    try:
        svg = await generate_svg(api.api_key, api.base_url, model.model_id, context, text)
        if not svg.strip().startswith("<svg"):
            await placeholder.edit_text(f"❌ Модель вернула не SVG:\n<pre>{svg[:500]}</pre>", parse_mode="HTML")
            return
        # Send raw SVG as file
        svg_file = BufferedInputFile(svg.encode(), filename="result.svg")
        # Also render to PNG
        try:
            png_bytes = svg_to_png_bytes(svg)
            await message.answer_photo(
                BufferedInputFile(png_bytes, filename="result.png"),
                caption="SVG (PNG-preview)"
            )
        except Exception:
            pass
        await message.answer_document(svg_file, caption="result.svg")
        await placeholder.delete()
    except Exception as exc:
        log.exception("svg gen error user %d", message.from_user.id)
        await placeholder.edit_text(f"❌ Ошибка: {exc}")


async def _handle_styled_gen(message, bot, api, model, text):
    placeholder = await message.answer("🎨 Стилизованная генерация...")
    style_b64 = None
    if model.style_image_file_id:
        try:
            style_b64 = await telegram_file_to_base64(bot, model.style_image_file_id)
        except Exception as exc:
            log.warning("Failed to fetch style image: %s", exc)
    try:
        result = await generate_image(
            api.api_key, api.base_url, model.model_id, text, style_b64
        )
        if result.startswith("http"):
            img_bytes = await url_to_bytes(result)
        else:
            img_bytes = await base64_to_bytes(result)
        if img_bytes:
            await message.answer_photo(
                BufferedInputFile(img_bytes, filename="styled.jpg"),
                caption=f"🎨 {text[:200]}",
            )
            await placeholder.delete()
        else:
            await placeholder.edit_text(f"Результат (текст): {result[:1000]}")
    except Exception as exc:
        log.exception("styled gen error user %d", message.from_user.id)
        await placeholder.edit_text(f"❌ Ошибка: {exc}")