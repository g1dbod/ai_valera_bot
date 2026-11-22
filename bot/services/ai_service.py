from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

log = logging.getLogger(__name__)


def _client(api_key: str, base_url: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


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

    content: list = []
    if image_base64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
        })
    content.append({"type": "text", "text": user_message})

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        messages.extend(history)
    messages.append({
        "role": "user",
        "content": content if image_base64 else user_message,
    })

    log.debug(
        "stream_chat model=%s base_url=%s history_len=%d",
        model_id, base_url, len(history or []),
    )

    stream = await client.chat.completions.create(
        model=model_id,
        messages=messages,
        stream=True,
    )

    try:
        async for chunk in stream:
            if not chunk.choices:
                continue                    # некоторые провайдеры шлют пустые чанки
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield text
    finally:
        await stream.close()
        await client.close()


async def generate_image(
    api_key: str,
    base_url: str,
    model_id: str,
    prompt: str,
    style_image_base64: Optional[str] = None,
) -> str:
    """Returns URL or base64 of the generated image."""
    client = _client(api_key, base_url)

    if style_image_base64:
        # styled generation: include reference image in messages via chat
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{style_image_base64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        resp = await client.chat.completions.create(model=model_id, messages=messages)
        return resp.choices[0].message.content or ""

    resp = await client.images.generate(model=model_id, prompt=prompt, n=1)
    return resp.data[0].url or resp.data[0].b64_json or ""


async def generate_svg(
    api_key: str,
    base_url: str,
    model_id: str,
    system_prompt: str,
    user_message: str,
) -> str:
    """Returns raw SVG text."""
    client = _client(api_key, base_url)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "user",
            "content": (
                f"{user_message}\n\nОтветь ТОЛЬКО валидным SVG-кодом, "
                "начиная с <svg и заканчивая </svg>. Без пояснений."
            ),
        }
    )

    resp = await client.chat.completions.create(model=model_id, messages=messages)
    raw = resp.choices[0].message.content or ""
    # extract SVG block
    start = raw.find("<svg")
    end = raw.rfind("</svg>")
    if start != -1 and end != -1:
        return raw[start : end + 6]
    return raw