import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

async def handle_predict(bot_instance, update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if data.startswith("setup_predict_select_"):
        # We don't check for clicked_buttons here because we want selections to be togglable
        parts = data.split("_")
        wager = float(parts[3])
        prediction = parts[4]
        game_mode = parts[5]
        
        if not hasattr(bot_instance, "_predict_selections"):
            bot_instance._predict_selections = {}
        
        if user_id not in bot_instance._predict_selections:
            bot_instance._predict_selections[user_id] = set()
        elif not isinstance(bot_instance._predict_selections[user_id], set):
            bot_instance._predict_selections[user_id] = {str(bot_instance._predict_selections[user_id])}

        if prediction in bot_instance._predict_selections[user_id]:
            bot_instance._predict_selections[user_id].remove(prediction)
            await query.answer("Removed selection")
        else:
            if len(bot_instance._predict_selections[user_id]) < 5:
                bot_instance._predict_selections[user_id].add(prediction)
                await query.answer("Added selection")
            else:
                await query.answer("❌ Max 5 selections!", show_alert=True)
                return
                
        await bot_instance._setup_predict_interface(update, context, wager, game_mode)
        return

    if data.startswith("predict_start_"):
        # The user requested that the bot does nothing when "Start" is clicked
        await query.answer()
        return
