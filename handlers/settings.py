from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, BotCommand
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import bot, logger
from database import get_config, save_config
from services.ai_service import get_client

class BotStates(StatesGroup):
    waiting_for_api = State()
    waiting_for_model = State()
    waiting_for_context = State()

router = Router()

async def set_main_menu():
    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="settings", description="Настройки API и моделей"),
        BotCommand(command="help", description="Помощь"),
    ])

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я AI-бот. Настрой меня через /settings и просто пиши сообщения.")

@router.message(Command("settings"))
async def cmd_settings(message: types.Message, state: FSMContext):
    config = await get_config(message.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔑 Добавить API", callback_data="set_api"))
    kb.row(InlineKeyboardButton(text="🤖 Выбрать модель", callback_data="set_model"))
    kb.row(InlineKeyboardButton(text="📝 Контекст", callback_data="set_context"))
    kb.row(InlineKeyboardButton(text="🗑 Удалить API", callback_data="del_api"))
    kb.row(InlineKeyboardButton(text="🗑 Удалить модель", callback_data="del_model"))
    
    text = (
        f"<b>Настройки:</b>\n\n"
        f"API: {config.selected_api or 'Не выбран'}\n"
        f"Модель: {config.selected_model or 'Не выбрана'}\n"
        f"Контекст: {config.context[:50]}..."
    )
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "set_api")
async def set_api_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Пришлите данные API в формате:\n`Имя|Ключ|URL`\n\nПример:\n`OpenAI|sk-proj-xxx|https://api.openai.com/v1`", parse_mode="Markdown")
    await state.set_state(BotStates.waiting_for_api)
    await callback.answer()

@router.message(BotStates.waiting_for_api)
async def set_api_finish(message: types.Message, state: FSMContext):
    try:
        name, key, url = message.text.split("|")
        config = await get_config(message.from_user.id)
        config.apis[name.strip()] = {"key": key.strip(), "url": url.strip()}
        await save_config(config)
        await message.answer(f"✅ API {name} успешно добавлен!")
        await state.clear()
    except Exception:
        await message.answer("❌ Ошибка. Используйте формат: `Имя|Ключ|URL`", parse_mode="Markdown")

@router.callback_query(F.data == "set_model")
async def set_model_start(callback: types.CallbackQuery, state: FSMContext):
    config = await get_config(callback.from_user.id)
    if not config.apis:
        await callback.message.answer("❌ Сначала добавьте хотя бы один API.")
        return await callback.answer()
    
    kb = InlineKeyboardBuilder()
    for api_name in config.apis.keys():
        kb.add(InlineKeyboardButton(text=api_name, callback_data=f"api_sel:{api_name}"))
    
    await callback.message.answer("Выберите API для настройки моделей:", reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("api_sel:"))
async def api_selected(callback: types.CallbackQuery, state: FSMContext):
    api_name = callback.data.split(":")[1]
    config = await get_config(callback.from_user.id)
    
    kb = InlineKeyboardBuilder()
    models = config.models.get(api_name, [])
    if not models:
        await callback.message.answer(f"Для API {api_name} моделей не добавлено. Пришлите список через запятую.")
        await state.set_state(BotStates.waiting_for_model)
        await state.update_data(current_api=api_name)
        return await callback.answer()
    
    for model in models:
        kb.add(InlineKeyboardButton(text=model, callback_data=f"mod_sel:{model}"))
    
    await callback.message.edit_text(f"Выберите модель для {api_name}:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("mod_sel:"))
async def model_selected(callback: types.CallbackQuery):
    model_name = callback.data.split(":")[1]
    config = await get_config(callback.from_user.id)
    
    # Находим, к какому API принадлежит эта модель
    selected_api = None
    for api_name, models in config.models.items():
        if model_name in models:
            selected_api = api_name
            break
    
    if selected_api:
        config.selected_api = selected_api
        config.selected_model = model_name
        await save_config(config)
        await callback.message.edit_text(f"✅ Модель {model_name} (API: {selected_api}) выбрана!")
    else:
        await callback.message.edit_text(f"❌ Ошибка: модель {model_name} не найдена ни в одном API.")
    
    await callback.answer()

@router.message(BotStates.waiting_for_model)
async def set_model_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    api_name = data.get("current_api")
    if not api_name:
        await message.answer("Ошибка состояния.")
        return await state.clear()
    
    try:
        models = [m.strip() for m in message.text.split(",")]
        config = await get_config(message.from_user.id)
        config.models[api_name] = models
        await save_config(config)
        await message.answer(f"✅ Модели для {api_name} добавлены!")
        await state.clear()
    except Exception:
        await message.answer("❌ Ошибка. Пришлите список моделей через запятую.")

@router.callback_query(F.data == "set_context")
async def set_context_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Пришлите новый системный контекст (инструкцию для бота):")
    await state.set_state(BotStates.waiting_for_context)
    await callback.answer()

@router.message(BotStates.waiting_for_context)
async def set_context_finish(message: types.Message, state: FSMContext):
    config = await get_config(message.from_user.id)
    config.context = message.text
    await save_config(config)
    await message.answer("✅ Контекст обновлен!")
    await state.clear()

@router.callback_query(F.data == "del_api")
async def del_api_start(callback: types.CallbackQuery):
    config = await get_config(callback.from_user.id)
    if not config.apis:
        await callback.message.answer("Список API пуст.")
        return await callback.answer()
    
    kb = InlineKeyboardBuilder()
    for api_name in config.apis.keys():
        kb.add(InlineKeyboardButton(text=api_name, callback_data=f"api_del:{api_name}"))
    
    await callback.message.answer("Выберите API для удаления:", reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("api_del:"))
async def api_deleted(callback: types.CallbackQuery):
    api_name = callback.data.split(":")[1]
    config = await get_config(callback.from_user.id)
    if api_name in config.apis:
        del config.apis[api_name]
        await save_config(config)
    await callback.message.edit_text(f"✅ API {api_name} удален!")
    await callback.answer()

@router.callback_query(F.data == "del_model")
async def del_model_start(callback: types.CallbackQuery):
    config = await get_config(callback.from_user.id)
    if not config.apis:
        await callback.message.answer("Список API пуст.")
        return await callback.answer()
    
    kb = InlineKeyboardBuilder()
    for api_name in config.apis.keys():
        kb.add(InlineKeyboardButton(text=api_name, callback_data=f"api_mod_del:{api_name}"))
    
    await callback.message.answer("Выберите API, в котором нужно удалить модели:", reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("api_mod_del:"))
async def api_mod_del_selected(callback: types.CallbackQuery):
    api_name = callback.data.split(":")[1]
    config = await get_config(callback.from_user.id)
    models = config.models.get(api_name, [])
    
    if not models:
        await callback.message.answer("Список моделей пуст.")
        return await callback.answer()
    
    kb = InlineKeyboardBuilder()
    for model in models:
        kb.add(InlineKeyboardButton(text=model, callback_data=f"mod_del:{model}"))
    
    await callback.message.edit_text(f"Выберите модель для удаления:", reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("mod_del:"))
async def model_deleted(callback: types.CallbackQuery):
    model_name = callback.data.split(":")[1]
    config = await get_config(callback.from_user.id)
    for api in config.models:
        if model_name in config.models[api]:
            config.models[api].remove(model_name)
    await save_config(config)
    await callback.message.edit_text(f"✅ Модель {model_name} удалена!")
    await callback.answer()
