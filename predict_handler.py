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
        parts = data.split("_")
        wager = float(parts[2])
        game_mode = parts[3]
        
        selections = bot_instance._predict_selections.get(user_id, set())
        if not selections:
            await query.answer("❌ Please make a selection first!", show_alert=True)
            return

        user_data = bot_instance.db.get_user(user_id)
        if user_data['balance'] < wager:
            await query.answer("❌ Insufficient balance!", show_alert=True)
            return

        # Deduct wager
        user_data['balance'] -= wager
        bot_instance.db.update_user(user_id, user_data)
        bot_instance.db.add_transaction(user_id, f"predict_{game_mode}", -wager, f"Prediction bet on {game_mode}")

        await query.answer("Game started!")
        
        # Mapping for emoji values
        # Dice: 1-6
        # Darts: 1-6
        # Bowling: 1-6
        # Basketball: 1 (miss), 2 (stuck), 3-5 (score)
        # Soccer: 1-2 (miss/bar), 3-5 (goal)
        # Coinflip (slots): 1 (heads), 2 (tails) - though coinflip emoji is actually 🎲/🎰 etc sometimes. 
        # For simplicity, we use the value returned by the dice emoji.

        sent_dice = await context.bot.send_dice(chat_id=chat_id, emoji=bot_instance.emoji_map.get(game_mode, "🎲"))
        result_val = sent_dice.dice.value
        
        # Determine win/loss based on game mode and value
        win = False
        result_label = str(result_val)
        
        if game_mode in ["dice", "darts", "bowling"]:
            if str(result_val) in selections:
                win = True
        elif game_mode == "basketball":
            # 1: miss, 2: stuck, 3-5: score
            outcome = "miss" if result_val == 1 else "stuck" if result_val == 2 else "score"
            result_label = outcome
            if outcome in selections:
                win = True
        elif game_mode == "soccer":
            # 1-2: miss/bar, 3-5: goal
            outcome = "miss" if result_val == 1 else "bar" if result_val == 2 else "goal"
            result_label = outcome
            if outcome in selections:
                win = True
        elif game_mode == "coinflip":
            # 1: heads, 2: tails (assuming standard dice mapping for the custom bot logic)
            outcome = "heads" if result_val % 2 == 1 else "tails"
            result_label = outcome
            if outcome in selections:
                win = True

        # Calculate multiplier
        if len(selections) == 3 and game_mode == "dice":
            multiplier = 1.95
        else:
            multiplier = 6.0 / len(selections)

        await asyncio.sleep(4) # Wait for animation

        if win:
            payout = wager * multiplier
            user_data = bot_instance.db.get_user(user_id)
            user_data['balance'] += payout
            bot_instance.db.update_user(user_id, user_data)
            bot_instance.db.add_transaction(user_id, "predict_win", payout, f"Prediction win on {game_mode}")
            bot_instance.db.update_house_balance(-(payout - wager))
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎉 <b>Winner!</b>\n\nThe result was <b>{result_label.capitalize()}</b>.\nYou won <b>${payout:.2f}</b>!",
                parse_mode="HTML",
                reply_to_message_id=sent_dice.message_id
            )
        else:
            bot_instance.db.update_house_balance(wager)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ <b>You Lost!</b>\n\nThe result was <b>{result_label.capitalize()}</b>.\nBetter luck next time!",
                parse_mode="HTML",
                reply_to_message_id=sent_dice.message_id
            )
        
        # Clear selections for next game
        bot_instance._predict_selections[user_id] = set()
        return
