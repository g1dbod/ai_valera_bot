from aiogram import Router, F, types
from aiogram.filters import Command
from config import bot

router = Router()

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "<b>👋 Привет! Я AI-помощник.</b>\n\n"
        "Я могу общаться с вами, используя разные модели ИИ, которые вы настроите.\n\n"
        "<b>Основные команды:</b>\n"
        "🔹 /start — запуск бота\n"
        "🔹 /settings — настройка API, моделей и системного контекста\n"
        "🔹 /help — эта справка\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Перейдите в /settings.\n"
        "2. Добавьте API (например, OpenAI или прокси).\n"
        "3. Добавьте список моделей для этого API.\n"
        "4. Выберите активную модель.\n"
        "5. Просто пишите мне сообщения, и я буду отвечать!"
    )
    await message.answer(help_text, parse_mode="HTML")

