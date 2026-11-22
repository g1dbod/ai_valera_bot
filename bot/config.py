import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL: str = os.environ["DATABASE_URL"]
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG")
LOG_FILE: str = "/app/logs/bot.log"