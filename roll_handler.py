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
    if data.startswith("v2_bot_"):
        parts = data.split("_")
        # Format: v2_bot_{game}_{wager}_{rolls}_{mode}_{pts}
        if len(parts) >= 7:
            game = parts[2]
            wager = float(parts[3])
            rolls = int(parts[4])
            mode = parts[5]
            pts = int(parts[6])
            
            logger.info(f"PLAY AGAIN DETECTED: Game:{game} Wager:{wager} Rolls:{rolls} Mode:{mode} Pts:{pts}")
            # Call the bot start function
            await bot_instance.start_generic_v2_bot(update, context, game, wager, rolls, mode, pts)
            return

    if data.startswith("v2_send_emoji_"):
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
        
        # Use secondary bot (helper) ONLY if it exists, otherwise the game would break
        if not bot_instance.secondary_bot:
            logger.error("Secondary bot (helper) not initialized! Bot cannot roll.")
            await context.bot.send_message(chat_id=chat_id, text="❌ Bot error: Helper bot not connected.")
            return

        bot_to_use = bot_instance.secondary_bot
        for _ in range(challenge['rolls']):
            try:
                # Secondary bot (helper) sends dice
                d = await bot_to_use.send_dice(chat_id=chat_id, emoji=emoji)
                val = d.dice.value
                score = (1 if val >= 4 else 0) if emoji in ["⚽", "🏀"] else val
                b_tot += score
                challenge['b_rolls'].append(score)
            except Exception as e:
                logger.error(f"Error sending bot dice via helper: {e}")
        
        await asyncio.sleep(4)
        
        # Re-load challenge for safety
        bot_instance.pending_pvp = bot_instance.db.data.get('pending_pvp', {})
        challenge = bot_instance.pending_pvp.get(cid)
        if not challenge:
            logger.error(f"Challenge {cid} not found after rolls")
            return
        
        # NORMAL mode: Highest wins
        # CRAZY mode: Lowest wins
        
        p_tot = sum(challenge['p_rolls'])
        b_tot = sum(challenge.get('b_rolls', [b_tot]))
        
        # Consistent mode check: allow both 'crazy' and 'inverted'
        game_mode_type = str(challenge.get('mode', 'normal')).strip().lower()
        is_crazy = game_mode_type in ["crazy", "inverted"]
        
        logger.info(f"RESOLVING ROUND: PlayerTotal={p_tot}, BotTotal={b_tot}, Mode='{game_mode_type}', IsCrazy={is_crazy}")
        
        if is_crazy: # Crazy mode: Lowest wins (or inverted side for coinflip)
            if p_tot < b_tot: 
                round_win = "p"
                logger.info(f"WINNER: Player (Crazy Mode: {p_tot} < {b_tot})")
            elif b_tot < p_tot: 
                round_win = "b"
                logger.info(f"WINNER: Bot (Crazy Mode: {b_tot} < {p_tot})")
            else: 
                round_win = "draw"
                logger.info("WINNER: Draw (Crazy Mode)")
        else: # Normal mode: Highest wins
            if p_tot > b_tot: 
                round_win = "p"
                logger.info(f"WINNER: Player (Normal Mode: {p_tot} > {b_tot})")
            elif b_tot > p_tot: 
                round_win = "b"
                logger.info(f"WINNER: Bot (Normal Mode: {b_tot} > {p_tot})")
            else: 
                round_win = "draw"
                logger.info("WINNER: Draw (Normal Mode)")

        logger.info(f"DEBUG RESOLVE: P:{p_tot} B:{b_tot} Mode:{game_mode_type} IsCrazy:{is_crazy} -> Win:{round_win}")
        
        if round_win == "p":
            challenge['p_pts'] += 1
        elif round_win == "b":
            challenge['b_pts'] += 1
        
        if round_win == "draw":
            u = bot_instance.db.get_user(user_id)
            p1_name = u.get('username', f'User{user_id}')
            challenge['p_rolls'] = []
            bot_instance.db.update_pending_pvp(bot_instance.pending_pvp)
            return
        
        target_pts = challenge.get('pts', 1)
        if challenge['p_pts'] >= target_pts or challenge['b_pts'] >= target_pts:
            # Series End
            w = challenge['wager']
            if challenge['p_pts'] >= target_pts:
                payout = w * 1.95
                u_data = bot_instance.db.get_user(user_id)
                new_bal = u_data['balance'] + payout
                bot_instance.db.update_user(user_id, {'balance': new_bal})
                bot_instance.db.update_house_balance(-(payout - w))
                
                # Update stats and record game
                bot_instance._update_user_stats(user_id, w, payout - w, "win")
                bot_instance.db.record_game({
                    'type': f"{challenge['game']}_bot",
                    'player_id': user_id,
                    'user_id': user_id,
                    'wager': w,
                    'payout': payout,
                    'result': 'win',
                    'timestamp': datetime.now().isoformat()
                })
                
                p1_name = u_data.get('username', f'User{user_id}')
                win_text = (
                    f"🏆 <b>Game over!</b>\n\n"
                    f"<b>{p1_name}</b> • {challenge['p_pts']}\n"
                    f"<b>Bot</b> • {challenge['b_pts']}\n\n"
                    f"✅ <b>{p1_name}</b> won <b>${payout:,.2f}</b>!"
                )
                # Use "inverted" if game_mode_type is "crazy" for callback data consistency
                mode_for_cb = "inverted" if game_mode_type == "crazy" else "normal"
                kb = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{challenge['game']}_{w:.2f}_{challenge['rolls']}_{mode_for_cb}_{target_pts}"),
                       InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{challenge['game']}_{w*2:.2f}_{challenge['rolls']}_{mode_for_cb}_{target_pts}")]]
                sent_msg = await context.bot.send_message(chat_id=chat_id, text=win_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                bot_instance.button_ownership[(chat_id, sent_msg.message_id)] = user_id
            else:
                bot_instance.db.update_house_balance(w)
                u_data = bot_instance.db.get_user(user_id)
                
                # Update stats and record game
                bot_instance._update_user_stats(user_id, w, -w, "loss")
                bot_instance.db.record_game({
                    'type': f"{challenge['game']}_bot",
                    'player_id': user_id,
                    'user_id': user_id,
                    'wager': w,
                    'payout': 0,
                    'result': 'loss',
                    'timestamp': datetime.now().isoformat()
                })
                
                p1_name = u_data.get('username', f'User{user_id}')
                
                loss_text = (
                    f"🏆 <b>Game over!</b>\n\n"
                    f"<b>{p1_name}</b> • {challenge['p_pts']}\n"
                    f"<b>Bot</b> • {challenge['b_pts']}\n\n"
                    f"❌ <b>Bot</b> won <b>${w * 1.95:,.2f}</b>!"
                )
                
                # Use "inverted" if game_mode_type is "crazy" for callback data consistency
                mode_for_cb = "inverted" if game_mode_type == "crazy" else "normal"
                kb = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{challenge['game']}_{w:.2f}_{challenge['rolls']}_{mode_for_cb}_{target_pts}"),
                       InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{challenge['game']}_{w*2:.2f}_{challenge['rolls']}_{mode_for_cb}_{target_pts}")]]
                
                reply_id = challenge.get('message_id')
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
                f"Bot: {challenge['b_pts']}\n\n"
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
