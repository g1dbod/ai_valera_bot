import re
import html
from typing import List

def format_to_html(text: str) -> str:
    """Безопасно и аккуратно конвертирует Markdown-подобный текст модели в Telegram HTML."""
    if not text:
        return ""

    placeholders: List[str] = []

    def stash(chunk: str) -> str:
        token = f"\\x00PH{len(placeholders)}\\x00"
        placeholders.append(chunk)
        return token

    # 1. Блоки кода ```lang\\n code ```
    def _code_block(m: re.Match) -> str:
        code = html.escape(m.group(2))
        return stash(f"<pre><code>{code}</code></pre>")

    text = re.sub(r'```(\\w*)\\n?(.*?)```', _code_block, text, flags=re.DOTALL)

    # 2. Инлайн-код `code`
    def _inline_code(m: re.Match) -> str:
        return stash(f"<code>{html.escape(m.group(1))}</code>")

    text = re.sub(r'`([^`\\n]+?)`', _inline_code, text)

    # 3. Экранируем всё остальное
    text = html.escape(text)

    # 4. Заголовки "# Заголовок"
    text = re.sub(r'^#{1,6}\\s*(.+?)\\s*$', r'<b>\\1</b>', text, flags=re.MULTILINE)

    # 5. Жирный **text** / __
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\\1</b>', text)

    # 6. Курсив *text* / _
    text = re.sub(r'\*(.*?)\*', r'<i>\\1</i>', text)
    text = re.sub(r'_(.*?)_', r'<i>\\1</i>', text)

    # 7. Зачеркнутый ~~text~~
    text = re.sub(r'~~(.*?)~~', r'<s>\\1</s>', text)

    # 8. Ссылки [text](url)
    def _link(m: re.Match) -> str:
        t = html.escape(m.group(1))
        u = m.group(2)
        return stash(f'<a href="{u}">{t}</a>')

    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _link, text)

    # Возвращаем всё на место
    for token, chunk in reversed(list(enumerate(placeholders))):
        text = text.replace(f"\\x00PH{token}\\x00", chunk)

    return text

def split_text(text: str, limit: int = 4096) -> List[str]:
    """Разбивает текст на части, чтобы не превысить лимит Telegram."""
    if len(text) <= limit:
        return [text]
    
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        
        split_idx = text.rfind('\n', 0, limit)
        if split_idx == -1:
            split_idx = limit
            
        parts.append(text[:split_idx])
        text = text[split_idx:].lstrip()
        
    return parts

def wrap_answer(text: str, model_name: str) -> str:
    """Добавляет подпись с названием модели к ответу."""
    return f"{text}\n\n🤖 Модель: {html.escape(model_name)}"

