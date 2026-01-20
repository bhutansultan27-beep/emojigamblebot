import logging
import os
from telegram_bot import AntariaCasinoBot
from app import app

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    try:
        # The token is now pulled from environment variables inside the class
        bot = AntariaCasinoBot(None)
        logger.info("Bot initialized, starting polling...")
        bot.app.run_polling()
    except Exception as e:
        logger.error(f"Critical error starting bot: {e}")