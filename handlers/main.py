from aiogram import Router, F, types
from services.ai_service import handle_ai_request

router = Router()

@router.message(F.text)
async def text_handler(message: types.Message):
    if not message.text.startswith("/") and "|" not in message.text:
        await handle_ai_request(message)

@router.message(F.photo | F.document | F.voice | F.video)
async def media_handler(message: types.Message):
    await handle_ai_request(message)

