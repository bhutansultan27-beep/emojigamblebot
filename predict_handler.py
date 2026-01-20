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

    if data.startswith("setup_mode_predict_edit_"):
        parts = data.split("_")
        try:
            wager = float(parts[4])
        except (ValueError, IndexError):
            wager = 10.0
        game_mode = parts[5] if len(parts) > 5 else "dice"
        await bot_instance._setup_predict_interface(update, context, wager, game_mode, force_new=False)
        return

    if data.startswith("setup_mode_predict_"):
        # Remove buttons from the result message
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.error(f"Error removing markup in setup_mode_predict: {e}")
            
        parts = data.split("_")
        try:
            wager = float(parts[3])
        except (ValueError, IndexError):
            wager = 10.0
        game_mode = parts[4] if len(parts) > 4 else "dice"
        # Force a new message by setting update.callback_query to None for the interface call
        await bot_instance._setup_predict_interface(update, context, wager, game_mode, force_new=True)
        return

    if data.startswith("setup_predict_select_"):
        # We don't check for clicked_buttons here because we want selections to be togglable
        parts = data.split("_")
        wager = float(parts[3])
        prediction = parts[4]
        game_mode = parts[5]
        
        if not hasattr(bot_instance, "_predict_selections"):
            bot_instance._predict_selections = {}
        
        if user_id not in bot_instance._predict_selections:
            bot_instance._predict_selections[user_id] = {}
            
        if game_mode not in bot_instance._predict_selections[user_id]:
            bot_instance._predict_selections[user_id][game_mode] = set()

        if prediction in bot_instance._predict_selections[user_id][game_mode]:
            bot_instance._predict_selections[user_id][game_mode].remove(prediction)
            await query.answer("Removed selection")
        else:
            # Prevent picking all options in basketball and soccer
            if game_mode in ["basketball", "soccer"]:
                current_len = len(bot_instance._predict_selections[user_id][game_mode])
                if current_len >= 2:
                    await query.answer("❌ Can't pick all options!", show_alert=True)
                    return
            
            if len(bot_instance._predict_selections[user_id][game_mode]) < 5:
                bot_instance._predict_selections[user_id][game_mode].add(prediction)
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
        
        user_selections = bot_instance._predict_selections.get(user_id, {})
        selections = user_selections.get(game_mode, set())
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

        # Make buttons unclickable - DISABLED as per user request to remove "game in progress" feel
        # if query.message and query.message.reply_markup:
        #     try:
        #         new_keyboard = []
        #         for row in query.message.reply_markup.inline_keyboard:
        #             new_row = []
        #             for button in row:
        #                 # Create a new button with same text but no callback data (or dummy)
        #                 new_row.append(InlineKeyboardButton(button.text, callback_data="dummy"))
        #             new_keyboard.append(new_row)
        #         await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
        #     except Exception as e:
        #         logger.error(f"Error making markup unclickable in predict_start: {e}")

        # await query.answer("Game started!")
        
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
            # Values 1-5. 1-2: miss, 3: stuck, 4-5: score.
            # miss: 2/5 (40%), stuck: 1/5 (20%), score: 2/5 (40%)
            outcome = "miss" if result_val <= 2 else "stuck" if result_val == 3 else "score"
            result_label = outcome
            if outcome in selections:
                win = True
        elif game_mode == "soccer":
            # Values 1-5. 1-2: miss, 3: bar, 4-5: goal.
            # miss: 2/5 (40%), bar: 1/5 (20%), goal: 2/5 (40%)
            outcome = "miss" if result_val <= 2 else "bar" if result_val == 3 else "goal"
            result_label = outcome
            if outcome in selections:
                win = True
        elif game_mode == "coinflip":
            # 1: heads, 2: tails (assuming standard dice mapping for the custom bot logic)
            outcome = "heads" if result_val % 2 == 1 else "tails"
            result_label = outcome
            if outcome in selections:
                win = True

        # Calculate multiplier with 0.5% house edge
        # Multiplier = (Total Outcomes / Selected Outcomes) * (1 - House Edge)
        house_edge = 0.005
        
        import math
        
        if game_mode in ["dice", "darts", "bowling"]:
            multipliers = {
                1: 5.85,
                2: 2.93,
                3: 1.95,
                4: 1.46,
                5: 1.17
            }
            multiplier = multipliers.get(len(selections), 0.0)
        elif game_mode == "basketball":
            # Probability based on values 1-5: miss(2), stuck(1), score(2)
            # score: (5/2)*0.995 = 2.4875x
            # miss: (5/2)*0.995 = 2.4875x
            # stuck: (5/1)*0.995 = 4.975x
            outcomes_map = {"miss": 2, "stuck": 1, "score": 2}
            total_slots = 5
            selected_outcome_slots = sum(outcomes_map[s] for s in selections if s in outcomes_map)
            multiplier = (total_slots / selected_outcome_slots) * (1 - house_edge) if selected_outcome_slots > 0 else 0
        elif game_mode == "soccer":
            # Probability based on values 1-5: miss(2), bar(1), goal(2)
            outcomes_map = {"miss": 2, "bar": 1, "goal": 2}
            total_slots = 5
            selected_outcome_slots = sum(outcomes_map[s] for s in selections if s in outcomes_map)
            multiplier = (total_slots / selected_outcome_slots) * (1 - house_edge) if selected_outcome_slots > 0 else 0
        elif game_mode == "coinflip":
            # heads: 3/6, tails: 3/6 (mapped from 1-6 dice)
            total_outcomes = 2
            multiplier = (total_outcomes / len(selections)) * (1 - house_edge)
        else:
            multiplier = 0.0

        if not math.isfinite(multiplier) or multiplier < 0:
            multiplier = 0.0

        await asyncio.sleep(4) # Wait for animation

        if win:
            payout = wager * multiplier
            user_data = bot_instance.db.get_user(user_id)
            user_data['balance'] += payout
            bot_instance.db.update_user(user_id, user_data)
            bot_instance.db.add_transaction(user_id, "predict_win", payout, f"Prediction win on {game_mode}")
            bot_instance.db.update_house_balance(-(payout - wager))
            
            # Record game for history
            bot_instance.db.record_game({
                "type": f"predict_{game_mode}",
                "player_id": user_id,
                "user_id": user_id,
                "wager": wager,
                "payout": payout,
                "result": "win"
            })
            
            user_username = user_data.get('username', f'User{user_id}')
            win_text = (
                f"🏆 <b>Game over!</b>\n\n"
                f"{user_username} won <b>${payout:,.2f}</b>!"
            )
            
            # Replay buttons
            kb = [[
                InlineKeyboardButton("🔄 Play Again", callback_data=f"setup_mode_predict_{wager:.2f}_{game_mode}"),
                InlineKeyboardButton("🔄 Double", callback_data=f"setup_mode_predict_{wager*2:.2f}_{game_mode}")
            ]]
            
            sent_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=win_text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML",
                reply_to_message_id=sent_dice.message_id
            )
            bot_instance.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id
        else:
            bot_instance.db.update_house_balance(wager)
            
            # Record game for history
            bot_instance.db.record_game({
                "type": f"predict_{game_mode}",
                "player_id": user_id,
                "user_id": user_id,
                "wager": wager,
                "payout": 0,
                "result": "loss"
            })
            
            loss_text = (
                f"🏆 <b>Game over!</b>\n\n"
                f"Bot won <b>${wager:,.2f}</b>!"
            )
            
            # Replay buttons
            kb = [[
                InlineKeyboardButton("🔄 Play Again", callback_data=f"setup_mode_predict_{wager:.2f}_{game_mode}"),
                InlineKeyboardButton("🔄 Double", callback_data=f"setup_mode_predict_{wager*2:.2f}_{game_mode}")
            ]]
            
            sent_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=loss_text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML",
                reply_to_message_id=sent_dice.message_id
            )
            bot_instance.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id
        
        # Clear selections for next game - DISABLED to persist selections for replay
        # bot_instance._predict_selections[user_id] = set()
        return
