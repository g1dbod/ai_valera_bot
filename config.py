import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

# БД (Внешняя)
DB_USER = os.getenv("POSTGRES_USER", "db-1006665_default")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "TAhOqDaSR2jLB2jT")
DB_HOST = os.getenv("POSTGRES_HOST", "db.spb.sprinthost.ru")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "db-1006665_default")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
