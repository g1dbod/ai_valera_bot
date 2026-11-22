from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI, APIStatusError, APITimeoutError

log = logging.getLogger(__name__)


def _client(api_key: str, base_url: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=300.0,
        max_retries=1,
    )


def _build_messages(
    system_prompt: str,
    user_message: str,
    image_base64: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> list[dict]:
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        messages.extend(history)

    if image_base64:
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                },
                {"type": "text", "text": user_message},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_message})

    return messages


def _pick_text(msg) -> str:
    """content, а если пусто — reasoning_content (reasoning-модели)."""
    if msg is None:
        return ""
    text = (getattr(msg, "content", None) or "").strip()
    if not text:
        text = (getattr(msg, "reasoning_content", None) or "").strip()
    return text

def _friendly_error(exc: Exception) -> RuntimeError:
    """Технические ошибки SDK → понятный текст для пользователя."""
    if isinstance(exc, APITimeoutError):
        return RuntimeError("модель не ответила за отведённое время — попробуйте другую")
    if isinstance(exc, APIStatusError):
        return RuntimeError(f"провайдер вернул ошибку {exc.status_code} — модель недоступна")
    return RuntimeError(str(exc).split("\n")[0][:200])


async def stream_chat(
    api_key: str,
    base_url: str,
    model_id: str,
    system_prompt: str,
    user_message: str,
    image_base64: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> AsyncIterator[str]:
    client = _client(api_key, base_url)
    messages = _build_messages(system_prompt, user_message, image_base64, history)

    log.debug(
        "stream_chat model=%s base_url=%s history_len=%d vision=%s",
        model_id, base_url, len(history or []), bool(image_base64),
    )

    produced = 0
    reasoning: list[str] = []

    try:
        try:
            stream = await client.chat.completions.create(
                model=model_id, messages=messages, stream=True
            )
            try:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        reasoning.append(rc)

                    text = getattr(delta, "content", None)
                    if text:
                        produced += 1
                        yield text
            finally:
                await stream.close()

        except Exception as exc:
            if produced:
                log.warning(
                    "stream aborted after %d chunks (%s: %s)",
                    produced, type(exc).__name__, exc,
                )
                return

            log.warning(
                "streaming unsupported by provider (%s: %s) — retrying without stream",
                type(exc).__name__, exc,
            )

            try:
                resp = await client.chat.completions.create(
                    model=model_id, messages=messages, stream=False
                )
            except Exception as exc2:
                log.error("non-stream fallback failed: %s", exc2)
                raise _friendly_error(exc2) from exc2

            text = _pick_text(resp.choices[0].message if resp.choices else None)
            if text:
                produced += 1
                yield text

        if produced == 0 and reasoning:
            yield "".join(reasoning)

    finally:
        log.debug("stream_chat finished model=%s chunks=%d", model_id, produced)
        await client.close()


async def generate_image(
    api_key: str,
    base_url: str,
    model_id: str,
    prompt: str,
    style_image_base64: Optional[str] = None,
) -> str:
    client = _client(api_key, base_url)
    try:
        if style_image_base64:
            resp = await client.chat.completions.create(
                model=model_id,
                messages=_build_messages("", prompt, style_image_base64),
            )
            return _pick_text(resp.choices[0].message if resp.choices else None)

        resp = await client.images.generate(model=model_id, prompt=prompt, n=1)
        item = resp.data[0] if resp.data else None
        if item is None:
            return ""
        return item.url or item.b64_json or ""
    finally:
        await client.close()


async def generate_svg(
    api_key: str,
    base_url: str,
    model_id: str,
    system_prompt: str,
    user_message: str,
) -> str:
    client = _client(api_key, base_url)
    prompt = (
        f"{user_message}\n\nОтветь ТОЛЬКО валидным SVG-кодом, "
        "начиная с <svg и заканчивая </svg>. Без пояснений."
    )
    try:
        resp = await client.chat.completions.create(
            model=model_id,
            messages=_build_messages(system_prompt, prompt),
        )
        raw = _pick_text(resp.choices[0].message if resp.choices else None)
        start, end = raw.find("<svg"), raw.rfind("</svg>")
        if start != -1 and end != -1:
            return raw[start:end + 6]
        return raw
    finally:
        await client.close()