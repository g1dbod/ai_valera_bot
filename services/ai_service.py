import asyncio
import logging
import html
import base64
import io
from typing import Dict, Tuple, Optional, List, Set

from aiogram import types
from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
from config import bot, logger
from database import get_config, save_config
from utils.formatter import format_to_html, split_text, wrap_answer

# --- КЭШИ ---
_CLIENT_CACHE: Dict[Tuple[str, str], AsyncOpenAI] = {}
_MODEL_TYPE_CACHE: Dict[Tuple[int, str, str], str] = {}
_MODEL_SELECT_CACHE: Dict[int, List[Tuple[str, str]]] = {}
_API_DELETE_CACHE: Dict[int, List[str]] = {}
_MODEL_DELETE_CACHE: Dict[int, List[Tuple[str, str]]] = {}
_REFERENCE_CACHE: Dict[int, List[str]] = {}
_NEEDS_REFS: Set[Tuple[str, str]] = set()

def get_client(key: str, url: str) -> AsyncOpenAI:
    cache_key = (key, url)
    client = _CLIENT_CACHE.get(cache_key)
    if client is None:
        client = AsyncOpenAI(api_key=key, base_url=url, timeout=120.0)
        _CLIENT_CACHE[cache_key] = client
    return client

async def safe_edit(chat_id: int, message_id: int, text: str):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML")
    except Exception:
        pass

async def _send_image(message: types.Message, thinking_msg: types.Message, image_data: str, caption: str):
    try:
        if image_data.startswith("data:image"):
            header, encoded = image_data.split(",", 1)
            img_bytes = base64.b64decode(encoded)
            photo = types.BufferedInputFile(img_bytes, filename="generated.png")
        else:
            photo = types.URLInputFile(image_data)
        
        await message.answer_photo(photo=photo, caption=caption, parse_mode="HTML")
        await bot.delete_message(chat_id=message.chat.id, message_id=thinking_msg.message_id)
    except Exception as e:
        logger.error(f"Image send error: {e}")
        await safe_edit(message.chat.id, thinking_msg.message_id, f"❌ Ошибка при отправке изображения: {html.escape(str(e))}")

def _extract_image_from_chat_message(msg_obj) -> Optional[str]:
    if not msg_obj: return None
    if hasattr(msg_obj, 'content') and msg_obj.content:
        if "base64" in msg_obj.content.lower():
            import re
            match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', msg_obj.content)
            if match: return match.group(0)
    return None
async def _get_image_base64(message: types.Message) -> Optional[str]:
    try:
        photo = None
        if message.photo:
            photo = message.photo[-1]
        elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
            file_id = message.document.file_id
            file = await bot.get_file(file_id)
            file_bytes = await bot.download_file(file.file_path)
            return f"data:{message.document.mime_type};base64,{base64.b64encode(file_bytes.read()).decode('utf-8')}"
        
        if photo:
            file = await bot.get_file(photo.file_id)
            file_bytes = await bot.download_file(file.file_path)
            return f"data:image/jpeg;base64,{base64.b64encode(file_bytes.read()).decode('utf-8')}"
    except Exception as e:
        logger.error(f"Error getting image base64: {e}")
    return None



# Декоратор для повторных попыток при ошибках сети или лимитах
ai_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(min=1, max=10),
    retry=retry_if_exception_type((APIConnectionError, RateLimitError)),
    reraise=True
)

@ai_retry
async def _run_image_api(client: AsyncOpenAI, config, message: types.Message, thinking_msg: types.Message, user_text: str, remember_func):
    try:
        response = await client.images.generate(
            model=config.selected_model,
            prompt=user_text,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url
        caption = wrap_answer("🎨 Изображение создано!", config.selected_model)
        await _send_image(message, thinking_msg, image_url, caption)
        await remember_func(user_text, "[Image]")
    except Exception as e:
        logger.error(f"Image API error: {e}")
        await safe_edit(message.chat.id, thinking_msg.message_id, f"❌ Ошибка Image API: {html.escape(str(e))}")

async def handle_ai_request(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.selected_api or not config.selected_model:
        await message.answer("❌ Сначала настройте API и выберите модель в меню /settings")
        return

    api_data = config.apis.get(config.selected_api)
    if not api_data:
        await message.answer("❌ Выбранный API не найден. Пожалуйста, настройте его заново.")
        return

    client = get_client(api_data['key'], api_data['url'])
    
    thinking_msg = await message.answer("🤔 Думаю...")
    
    async def typing_loop():
        while True:
            try:
                await bot.send_chat_action(chat_id=message.chat.id, action="typing")
                await asyncio.sleep(5)
            except Exception:
                await asyncio.sleep(5)

    typing_task = asyncio.create_task(typing_loop())

    async def remember(text, answer):
        config.history.append({"role": "user", "content": text})
        config.history.append({"role": "assistant", "content": answer})
        if len(config.history) > 20:
            config.history = config.history[-20:]
        await save_config(config)

    try:
        user_text = message.text or message.caption or "Изображение"
        
        cache_key = (message.from_user.id, config.selected_api, config.selected_model)
        
        if _MODEL_TYPE_CACHE.get(cache_key) == "chat_text":
            try:
                stream = await client.chat.completions.create(model=config.selected_model, messages=messages, stream=True)
                full_answer = ""
                last_update = 0
                
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_answer += content
                        
                        now = asyncio.get_event_loop().time()
                        if now - last_update > 1.5:
                            await safe_edit(message.chat.id, thinking_msg.message_id, format_to_html(full_answer))
                            last_update = now
                            
                await safe_edit(message.chat.id, thinking_msg.message_id, format_to_html(wrap_answer(full_answer, config.selected_model)))
                await remember(user_text, full_answer)
                return
            except Exception as e:
                logger.warning(f"Streaming failed: {e}, falling back to non-streaming")

        try:
            @ai_retry
            async def _make_request():
                return await client.chat.completions.create(model=config.selected_model, messages=messages)
            
            response = await _make_request()
            answer = response.choices[0].message.content if response.choices else None
            
            if answer:
                _MODEL_TYPE_CACHE[cache_key] = "chat_text"
                final_html = format_to_html(wrap_answer(answer, config.selected_model))
                parts = split_text(final_html)
                await safe_edit(message.chat.id, thinking_msg.message_id, parts[0])
                for part in parts[1:]:
                    await message.answer(part, parse_mode="HTML")
                await remember(user_text, answer)
                return
            
        except Exception as e:
            err_str = str(e).lower()
            if "image" in err_str or "/api/v1/images" in err_str:
                _MODEL_TYPE_CACHE[cache_key] = "image_api"
                await _run_image_api(client, config, message, thinking_msg, user_text, remember)
                return
            raise e

    except Exception as e:
        logger.error(f"AI Error: {e}", exc_info=True)
        await safe_edit(message.chat.id, thinking_msg.message_id, f"❌ Ошибка API: {html.escape(str(e))}")
    finally:
        typing_task.cancel()

@ai_retry
async def _run_image_api(client: AsyncOpenAI, config, message: types.Message, thinking_msg: types.Message, user_text: str, remember_func):
    try:
        response = await client.images.generate(
            model=config.selected_model,
            prompt=user_text,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url
        caption = wrap_answer("🎨 Изображение создано!", config.selected_model)
        await _send_image(message, thinking_msg, image_url, caption)
        await remember_func(user_text, "[Image]")
    except Exception as e:
        logger.error(f"Image API error: {e}")
        await safe_edit(message.chat.id, thinking_msg.message_id, f"❌ Ошибка Image API: {html.escape(str(e))}")


async def handle_ai_request(message: types.Message):
    config = await get_config(message.from_user.id)
    if not config.selected_api or not config.selected_model:
        await message.answer("❌ Сначала настройте API и выберите модель в меню /settings")
        return

    api_data = config.apis.get(config.selected_api)
    if not api_data:
        await message.answer("❌ Выбранный API не найден. Пожалуйста, настройте его заново.")
        return

    client = get_client(api_data['key'], api_data['url'])
    
    thinking_msg = await message.answer("🤔 Думаю...")
    
    async def typing_loop():
        while True:
            try:
                await bot.send_chat_action(chat_id=message.chat.id, action="typing")
                await asyncio.sleep(5)
            except Exception:
                await asyncio.sleep(5)

    typing_task = asyncio.create_task(typing_loop())

    async def remember(text, answer):
        config.history.append({"role": "user", "content": text})
        config.history.append({"role": "assistant", "content": answer})
        if len(config.history) > 20:
            config.history = config.history[-20:]
        await save_config(config)

    try:
        user_text = message.text or message.caption or "Изображение"
        # Обработка изображения
        image_base64 = await _get_image_base64(message)
        
        if image_base64:
            # Формируем контент для мультимодальной модели (текст + картинка)
            user_content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_base64}}
            ]
        else:
            user_content = user_text

        messages = [{"role": "system", "content": config.context}] + config.history + [{"role": "user", "content": user_content}]
        
        cache_key = (message.from_user.id, config.selected_api, config.selected_model)
        
        if _MODEL_TYPE_CACHE.get(cache_key) == "chat_text":
            try:
                stream = await client.chat.completions.create(model=config.selected_model, messages=messages, stream=True)
                full_answer = ""
                last_update = 0
                
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_answer += content
                        
                        now = asyncio.get_event_loop().time()
                        if now - last_update > 1.5:
                            await safe_edit(message.chat.id, thinking_msg.message_id, format_to_html(full_answer))
                            last_update = now
                            
                await safe_edit(message.chat.id, thinking_msg.message_id, format_to_html(wrap_answer(full_answer, config.selected_model)))
                await remember(user_text, full_answer)
                return
            except Exception as e:
                logger.warning(f"Streaming failed: {e}, falling back to non-streaming")

        try:
            response = await client.chat.completions.create(model=config.selected_model, messages=messages)
            answer = response.choices[0].message.content if response.choices else None
            
            if answer:
                _MODEL_TYPE_CACHE[cache_key] = "chat_text"
                final_html = format_to_html(wrap_answer(answer, config.selected_model))
                parts = split_text(final_html)
                await safe_edit(message.chat.id, thinking_msg.message_id, parts[0])
                for part in parts[1:]:
                    await message.answer(part, parse_mode="HTML")
                await remember(user_text, answer)
                return
            
        except Exception as e:
            err_str = str(e).lower()
            if "image" in err_str or "/api/v1/images" in err_str:
                _MODEL_TYPE_CACHE[cache_key] = "image_api"
                await _run_image_api(client, config, message, thinking_msg, user_text, remember)
                return
            raise e

    except Exception as e:
        logger.error(f"AI Error: {e}", exc_info=True)
        await safe_edit(message.chat.id, thinking_msg.message_id, f"❌ Ошибка API: {html.escape(str(e))}")
    finally:
        typing_task.cancel()
