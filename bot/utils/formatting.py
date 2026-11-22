from __future__ import annotations

import html
import re

_FENCE = re.compile(r"```[ \t]*[\w+-]*[ \t]*\n(.*?)```", re.DOTALL)
_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.DOTALL)
_HEAD = re.compile(r"^#{1,6}[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def format_to_html(text: str) -> str:
    """Markdown → безопасный Telegram-HTML."""
    if not text:
        return ""

    out = html.escape(text)

    fences: list[str] = []

    def _stash(m: re.Match) -> str:
        fences.append(m.group(1))
        return f"\x00{len(fences) - 1}\x00"

    out = _FENCE.sub(_stash, out)
    out = _CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    out = _HEAD.sub(r"<b>\1</b>", out)

    for i, block in enumerate(fences):
        out = out.replace(f"\x00{i}\x00", f"<pre>{block}</pre>")

    return out


def split_text(text: str, limit: int = 3800) -> list[str]:
    """Режем по строкам, чтобы не разорвать слово и уложиться в лимит Telegram."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    cur = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts or [text]