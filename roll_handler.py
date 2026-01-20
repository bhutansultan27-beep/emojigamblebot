import asyncio
import logging
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def handle_roll(bot_instance, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all roll game interactions (setup and start)"""
    query = update.callback_query
    if not query:
        return
        
    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    # Handle setup navigation/steps
    if data.startswith("emoji_setup_"):
        parts = data.split("_")
        # Callback format: emoji_setup_{game_mode}_{wager}_{step}_{pts}_{rolls}_{mode}
        if len(parts) < 5:
            return
            
        game_mode = parts[2]
        try:
            wager = float(parts[3])
        except ValueError:
            return
            
        step = parts[4]
        
        # Extract existing params if any
        params = {}
        if step == "rolls":
            if len(parts) > 5: params["mode"] = parts[5]
        elif step == "points":
            if len(parts) > 5: params["rolls"] = int(parts[5])
            if len(parts) > 6: params["mode"] = parts[6]
        elif step == "final":
            if len(parts) > 5: params["pts"] = int(parts[5])
            if len(parts) > 6: params["rolls"] = int(parts[6])
            if len(parts) > 7: params["mode"] = parts[7]
            if len(parts) > 8: params["opponent"] = parts[8]
        elif step == "start":
            # For emoji_setup_dice_1.00_start_1_1_normal
            if len(parts) >= 8:
                try:
                    pts = int(parts[5])
                    rolls = int(parts[6])
                    mode = parts[7]
                except (ValueError, IndexError):
                    # Fallback if indices are shifted
                    num_parts = [p for p in parts if p.isdigit()]
                    if len(num_parts) >= 2:
                        pts = int(num_parts[0])
                        rolls = int(num_parts[1])
                        mode = "normal"
                    else:
                        pts, rolls, mode = 1, 1, "normal"
            else:
                pts, rolls, mode = 1, 1, "normal"
            
            await bot_instance.start_generic_v2_bot(update, context, game_mode, wager, rolls, mode, pts)
            return
            
        await bot_instance._show_emoji_game_setup(update, context, wager, game_mode, step, params)
        return

    # Handle bot game start/roll
    if data.startswith("v2_send_emoji_"):
        parts = data.split("_")
        # Format: v2_send_emoji_bot_{g_mode}_{wager}_{rolls}_{mode}_{pts}
        # OR v2_send_emoji_{cid}
        if len(parts) > 3 and parts[2] == "bot":
            g_mode = parts[3]
            wager = float(parts[4])
            rolls = int(parts[5])
            mode = parts[6]
            pts = int(parts[7])
            
            # Call the bot start function which handles the actual game logic
            await bot_instance.start_generic_v2_bot(update, context, g_mode, wager, rolls, mode, pts)
            return

        cid = data.replace("v2_send_emoji_", "")
        challenge = bot_instance.pending_pvp.get(cid)
        if not challenge or challenge.get('player') != user_id:
            await query.answer("❌ Game no longer valid.", show_alert=True)
            return
        
        await query.answer()
        # Make buttons unclickable by removing their callback data
        if query.message and query.message.reply_markup:
            try:
                new_keyboard = []
                for row in query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        new_row.append(InlineKeyboardButton(button.text, callback_data="dummy"))
                    new_keyboard.append(new_row)
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
            except Exception as e:
                logger.error(f"Error making markup unclickable: {e}")
        
        emoji = challenge['emoji']
        # Send emojis for user based on number of rolls
        num_rolls = challenge.get('rolls', 1)
        
        for _ in range(num_rolls):
            try:
                msg = await context.bot.send_dice(chat_id=chat_id, emoji=emoji)
                val = msg.dice.value
                score = (1 if val >= 4 else 0) if emoji in ["⚽", "🏀"] else val
                challenge['p_rolls'].append(score)
            except Exception as e:
                logger.error(f"Error sending player dice: {e}")
                await context.bot.send_message(chat_id=chat_id, text="❌ Error sending dice. Please try again.")
                return
        
        await asyncio.sleep(4)
        
        # Check if challenge still exists after sleep
        bot_instance.pending_pvp = bot_instance.db.data.get('pending_pvp', {})
        challenge = bot_instance.pending_pvp.get(cid)
        if not challenge: 
            logger.error(f"Challenge {cid} not found after player rolls")
            return

        p_tot = sum(challenge['p_rolls'])

        # Remove button from old cashout message before bot speaks
        old_msg_id = challenge.get('cashout_msg_id')
        if old_msg_id:
            try:
                await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                challenge['cashout_msg_id'] = None
                bot_instance.db.update_pending_pvp(bot_instance.pending_pvp)
            except Exception as e:
                pass

        # await context.bot.send_message(chat_id=chat_id, text=f"<b>Rukia</b>, your turn!", parse_mode="HTML")
        
        # Bot rolls
        b_tot = 0
        challenge['b_rolls'] = [] # Track bot rolls
        bot_to_use = bot_instance.secondary_bot if bot_instance.secondary_bot else context.bot
        for _ in range(challenge['rolls']):
            try:
                d = await bot_to_use.send_dice(chat_id=chat_id, emoji=emoji)
                val = d.dice.value
                score = (1 if val >= 4 else 0) if emoji in ["⚽", "🏀"] else val
                b_tot += score
                challenge['b_rolls'].append(score)
            except Exception as e:
                logger.error(f"Error sending bot dice: {e}")
        
        await asyncio.sleep(4)
        
        # Re-load challenge for safety
        bot_instance.pending_pvp = bot_instance.db.data.get('pending_pvp', {})
        challenge = bot_instance.pending_pvp.get(cid)
        if not challenge:
            logger.error(f"Challenge {cid} not found after rolls")
            return
        
        # NORMAL mode: Highest wins
        # INVERTED mode: Lowest wins
        
        p_tot = sum(challenge['p_rolls'])
        b_tot = sum(challenge.get('b_rolls', [b_tot]))
        
        game_mode_type = challenge.get('mode', 'normal') # Define it before use
        if game_mode_type == "crazy": # Crazy mode: Lowest wins
            if p_tot < b_tot: 
                round_win = "p"
            elif b_tot < p_tot: 
                round_win = "b"
            else: 
                round_win = "draw"
        else: # Normal mode: Highest wins
            if p_tot > b_tot: 
                round_win = "p"
            elif b_tot > p_tot: 
                round_win = "b"
            else: 
                round_win = "draw"

        logger.info(f"DEBUG RESOLVE: P:{p_tot} B:{b_tot} Mode:{game_mode_type} -> Win:{round_win}")
        
        if round_win == "p":
            challenge['p_pts'] += 1
        elif round_win == "b":
            challenge['b_pts'] += 1
        
        if round_win == "draw":
            u = bot_instance.db.get_user(user_id)
            p1_name = u.get('username', f'User{user_id}')
            # await context.bot.send_message(chat_id=chat_id, text=f"🤝 Draw! Refunded", parse_mode="HTML")
            challenge['p_rolls'] = []
            # Re-show roll button
            kb = [[InlineKeyboardButton("✅ Roll again", callback_data=f"v2_send_emoji_{cid}")]]
            # sent_msg = await context.bot.send_message(chat_id=chat_id, text=f"<b>{p1_name}</b>, your turn! {emoji}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            # bot_instance.button_ownership[(chat_id, sent_msg.message_id)] = user_id
            bot_instance.db.update_pending_pvp(bot_instance.pending_pvp)
            return
        
        target_pts = challenge.get('pts', 1)
        if challenge['p_pts'] >= target_pts or challenge['b_pts'] >= target_pts:
            # Remove button from final cashout message if it exists
            old_msg_id = challenge.get('cashout_msg_id')
            if old_msg_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                except Exception as e:
                    pass

            # Series End
            w = challenge['wager']
            if challenge['p_pts'] >= target_pts:
                payout = w * 1.95
                u = bot_instance.db.get_user(user_id)
                u['balance'] += payout
                bot_instance.db.update_user(user_id, {'balance': u['balance']})
                bot_instance.db.update_house_balance(-(payout - w))
                
                p1_name = u.get('username', f'User{user_id}')
                p1_mention = f'<a href="tg://user?id={user_id}">{p1_name}</a>'
                win_text = (
                    f"🏆 <b>Game over!</b>\n\n"
                    f"<b>{p1_name}</b> • {challenge['p_pts']}\n"
                    f"<b>Bot</b> • {challenge['b_pts']}\n\n"
                    f"<b>{p1_name}</b> won <b>${payout:,.2f}</b>!"
                )
                kb = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{challenge['game']}_{w:.2f}_{challenge['rolls']}_{challenge['mode']}_{target_pts}"),
                       InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{challenge['game']}_{w*2:.2f}_{challenge['rolls']}_{challenge['mode']}_{target_pts}")]]
                sent_msg = await context.bot.send_message(chat_id=chat_id, text=win_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                bot_instance.button_ownership[(chat_id, sent_msg.message_id)] = user_id
            else:
                bot_instance.db.update_house_balance(w)
                u = bot_instance.db.get_user(user_id)
                p1_name = u.get('username', f'User{user_id}')
                
                loss_text = (
                    f"🏆 <b>Game over!</b>\n\n"
                    f"<b>{p1_name}</b> • {challenge['p_pts']}\n"
                    f"<b>Bot</b> • {challenge['b_pts']}\n\n"
                    f"❌ <b>Bot</b> won <b>${w * 1.95:,.2f}</b>!"
                )
                
                kb = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{challenge['game']}_{w:.2f}_{challenge['rolls']}_{challenge['mode']}_{target_pts}"),
                       InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{challenge['game']}_{w*2:.2f}_{challenge['rolls']}_{challenge['mode']}_{target_pts}")]]
                
                # Check for cashout message or game details message to reply to
                reply_id = challenge.get('message_id') # Original game details msg
                # If we have a more recent 'msg_id' (like the cashout prompt), use that
                if challenge.get('msg_id'):
                    reply_id = challenge['msg_id']

                sent_msg = await context.bot.send_message(
                    chat_id=chat_id, 
                    text=loss_text, 
                    reply_markup=InlineKeyboardMarkup(kb), 
                    parse_mode="HTML",
                    reply_to_message_id=reply_id
                )
                bot_instance.button_ownership[(chat_id, sent_msg.message_id)] = user_id
            
            del bot_instance.pending_pvp[cid]
        else:
            # Next Round
            challenge['p_rolls'] = []
            u = bot_instance.db.get_user(user_id)
            p1_name = u.get('username', f'User{user_id}')
            text = (
                f"<b>Score</b>\n\n"
                f"{p1_name}: {challenge['p_pts']}\n"
                f"Rukia: {challenge['b_pts']}\n\n"
                f"<b>{p1_name}</b>, your turn! {emoji}"
            )
            cashout_val = bot_instance.calculate_cashout(challenge['p_pts'], challenge['b_pts'], challenge['pts'], challenge['wager'])
            cashout_multiplier = round(cashout_val / challenge['wager'], 2) if challenge['wager'] > 0 else 0
            kb = [
                [InlineKeyboardButton(f"💰 Cashout ${cashout_val:.2f} ({cashout_multiplier}x)", callback_data=f"v2_cashout_{cid}")]
            ]
            
            # Remove button from old cashout message
            old_msg_id = challenge.get('cashout_msg_id')
            if old_msg_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                except Exception as e:
                    # logger not imported here, but we can use print or skip
                    pass

            sent_msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            challenge['cashout_msg_id'] = sent_msg.message_id
            bot_instance.button_ownership[(chat_id, sent_msg.message_id)] = user_id
        
        bot_instance.db.update_pending_pvp(bot_instance.pending_pvp)
        return
