from __future__ import annotations

import base64
import io
import logging
from typing import Optional

import aiohttp
from PIL import Image

log = logging.getLogger(__name__)


async def telegram_file_to_base64(bot, file_id: str) -> str:
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    buf.seek(0)
    # resize if too large
    img = Image.open(buf).convert("RGB")
    img.thumbnail((1024, 1024))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode()


async def url_to_bytes(url: str) -> Optional[bytes]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return await r.read()
    except Exception as exc:
        log.warning("url_to_bytes failed: %s", exc)
    return None


async def base64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)


def svg_to_png_bytes(svg_text: str) -> bytes:
    import cairosvg  # lazy import — only needed for SVG
    return cairosvg.svg2png(bytestring=svg_text.encode())