import os
import asyncio
import random
import hashlib
import json
import logging
import socket
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# External dependencies
import sys
import os

# Use specific import path for python-telegram-bot to avoid conflicts with 'telegram' package
import sys
import os
import logging

# Import from telegram.ext specifically if needed, but python-telegram-bot 20+ uses telegram
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Ranks mapping for display
RANKS = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
    'T': 10, 'J': 10, 'Q': 10, 'K': 10, 'A': 11
}
CARD_FACES = {'H': '♥', 'D': '♦', 'C': '♣', 'S': '♠'}

# --- 1. Database Manager (PostgreSQL) ---
from flask import Flask
from models import db, User, Game, Transaction, GlobalState

class DatabaseManager:
    def __init__(self):
        self.app = Flask(__name__)
        # Use SQLite as fallback if DATABASE_URL is not set
        database_url = os.environ.get("DATABASE_URL")
        if database_url and database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)

        if not database_url:
            # Use SQLite for local development
            database_url = "sqlite:///casino_bot.db"
            logger.info("No DATABASE_URL found, using SQLite database: casino_bot.db")

        # SQLAlchemy 2.0+ requires postgresql:// instead of postgres://
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)

        self.app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        # Only use pool options for PostgreSQL (not SQLite)
        if database_url.startswith("postgresql"):
            self.app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_recycle": 300, "pool_pre_ping": True}
        db.init_app(self.app)
        with self.app.app_context():
            try:
                # Use a simpler check for table existence to handle various Postgres states
                db.create_all()
            except Exception as e:
                logger.warning(f"Initial create_all failed (likely table exists): {e}")
                # If it's a transient error or schema conflict, we might still be able to proceed
                # if the tables already exist or if we can ignore the specific DDL error
                pass

            # Migrate: add new columns if they don't exist
            try:
                from sqlalchemy import text, inspect
                inspector = inspect(db.engine)
                existing_columns = [c['name'] for c in inspector.get_columns('users')]
                if 'total_won' not in existing_columns:
                    db.session.execute(text('ALTER TABLE users ADD COLUMN total_won FLOAT DEFAULT 0.0'))
                    db.session.commit()
                    logger.info("Added total_won column to users table")
                if 'rakeback_balance' not in existing_columns:
                    db.session.execute(text('ALTER TABLE users ADD COLUMN rakeback_balance FLOAT DEFAULT 0.0'))
                    db.session.commit()
                    logger.info("Added rakeback_balance column to users table")
            except Exception as e:
                logger.warning(f"Migration check: {e}")
                db.session.rollback()

            # Initialize house balance if not exists
            house_balance_state = db.session.get(GlobalState, "house_balance")
            if not house_balance_state:
                db.session.add(GlobalState(key="house_balance", value={"amount": 10000.00}))

            stickers_state = db.session.get(GlobalState, "stickers")
            if not stickers_state:
                db.session.add(GlobalState(key="stickers", value={"roulette": {}}))
            db.session.commit()

    @property
    def data(self):
        # Compatibility layer for existing code that accesses self.db.data
        with self.app.app_context():
            house_balance_state = db.session.get(GlobalState, "house_balance")
            house_balance = house_balance_state.value["amount"] if house_balance_state else 10000.00

            stickers_state = db.session.get(GlobalState, "stickers")
            stickers = stickers_state.value if stickers_state else {}

            pending_pvp_state = db.session.get(GlobalState, "pending_pvp")
            pending_pvp = pending_pvp_state.value if pending_pvp_state else {}

            expiration_state = db.session.get(GlobalState, "expiration_seconds")
            expiration_seconds = expiration_state.value["seconds"] if expiration_state else 300

            return {
                "house_balance": house_balance,
                "stickers": stickers,
                "pending_pvp": pending_pvp,
                "expiration_seconds": expiration_seconds
            }

    def save_data(self):
        # Compatibility layer
        pass

    def update_pending_pvp(self, pending_pvp_data: Dict[str, Any]):
        with self.app.app_context():
            state = db.session.get(GlobalState, "pending_pvp")
            if not state:
                state = GlobalState(key="pending_pvp", value=pending_pvp_data)
                db.session.add(state)
            else:
                # Force SQLAlchemy to detect change in JSON
                state.value = dict(pending_pvp_data)
            db.session.commit()

    def get_user(self, user_id: int) -> Dict[str, Any]:
        with self.app.app_context():
            from sqlalchemy import select
            user = db.session.execute(select(User).filter_by(user_id=user_id)).scalar_one_or_none()
            if not user:
                user = User(user_id=user_id, username=f"User{user_id}")
                db.session.add(user)
                db.session.commit()
            return self._user_to_dict(user)

    def _user_to_dict(self, user):
        return {c.name: getattr(user, c.name) for c in user.__table__.columns}

    def update_user(self, user_id: int, updates: Dict[str, Any]):
        with self.app.app_context():
            from sqlalchemy import update
            # Filter to only include valid User columns and exclude primary key/identity fields
            valid_columns = {c.name for c in User.__table__.columns} - {'id'}
            filtered = {k: v for k, v in updates.items() if k in valid_columns}
            if filtered:
                db.session.execute(update(User).filter_by(user_id=user_id).values(filtered))
                db.session.commit()

    def get_house_balance(self) -> float:
        with self.app.app_context():
            return db.session.get(GlobalState, "house_balance").value["amount"]

    def update_house_balance(self, change: float):
        with self.app.app_context():
            state = db.session.get(GlobalState, "house_balance")
            val = state.value.copy()
            val["amount"] += change
            state.value = val
            db.session.commit()

    def add_transaction(self, user_id: int, type: str, amount: float, description: str):
        with self.app.app_context():
            tx = Transaction(user_id=user_id, type=type, amount=amount, description=description)
            db.session.add(tx)
            db.session.commit()

    def get_user_matches(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get game history for a user"""
        with self.app.app_context():
            from sqlalchemy import select, or_, cast, String
            # Use JSON extraction to filter by user_id or player_id in the 'data' column
            games = Game.query.filter(or_(
                cast(Game.data['player_id'], String) == str(user_id),
                cast(Game.data['user_id'], String) == str(user_id),
                cast(Game.data['challenger'], String) == str(user_id),
                cast(Game.data['player'], String) == str(user_id)
            )).order_by(Game.timestamp.desc()).limit(limit).all()
            
            user_games = []
            for g in games:
                if not g.data:
                    continue

                game_display_data = dict(g.data)
                # Replace specific bot username with "Bot" in the display data
                for key in ['bot', 'challenger', 'opponent', 'winner']:
                    val = game_display_data.get(key)
                    if isinstance(val, str):
                        lower_val = val.lower()
                        if lower_val in ["@davaulte", "davaulte", "emoji gamble bot", "emojigamblebot"]:
                            game_display_data[key] = 'Bot'

                user_games.append({**game_display_data, 'timestamp': g.timestamp.isoformat() if g.timestamp else None})
            return user_games

    def record_game(self, game_data: Dict[str, Any]):
        with self.app.app_context():
            # Add user_id or player_id to the game_data if it's missing but we have it in context
            # This ensures it's always searchable in match history
            g = Game(data=game_data)
            db.session.add(g)
            db.session.commit()
            game_data['id'] = g.id
            logger.info(f"Recorded game #{g.id} for {game_data.get('user_id') or game_data.get('player_id')}")

    def get_leaderboard(self) -> List[Dict[str, Any]]:
        with self.app.app_context():
            from sqlalchemy import select
            users = db.session.execute(select(User).order_by(User.total_wagered.desc()).limit(50)).scalars().all()
            return [{"username": u.username or f"User{u.user_id}", "total_wagered": u.total_wagered} for u in users]

    def save_data(self):
        pass # No longer needed for SQL

# --- 2. Antaria Casino Bot Class ---
class AntariaCasinoBot:
    async def post_init(self, application: Application):
        """Set up bot commands menu for all possible scopes"""
        from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
        # /play command removed
        commands = [
            BotCommand("start", "Start the bot and see help"),
            BotCommand("balance", "Check your current balance"),
            BotCommand("bonus", "Claim your daily bonus"),
            BotCommand("dice", "Play Dice game"),
            BotCommand("blackjack", "Play Blackjack"),

            BotCommand("coinflip", "Play Coinflip"),
            BotCommand("stats", "Check your game statistics"),
            BotCommand("matches", "View match history"),
            BotCommand("leaderboard", "View top players"),


            BotCommand("tip", "Tip another user"),
            BotCommand("deposit", "Deposit funds"),
            BotCommand("withdraw", "Withdraw funds")
        ]

        # Set commands for all possible scopes to ensure visibility
        await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())

        logger.info("Bot commands menu initialized for all scopes (Default, Private, Groups)")

    def __init__(self, token: Optional[str] = None):
        self.token = token
        # Initialize bot application
        # Main bot (responds to commands)
        main_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not main_token:
            raise ValueError("Invalid or missing TELEGRAM_BOT_TOKEN")

        # Helper bot (sends emojis)
        secondary_token = os.environ.get("SECONDARY_BOT_TOKEN")
        self.secondary_bot = None
        if secondary_token:
            from telegram import Bot
            self.secondary_bot = Bot(token=secondary_token)
            logger.info("Secondary bot initialized for emoji rolls")

        self.app = Application.builder().token(main_token).post_init(self.post_init).build()
        self.app.bot_data['casino_bot'] = self # Store reference for access from handlers if needed
        # Add job queue check
        if not self.app.job_queue:
            logger.warning("Job queue is not available. Some features like challenge expiration may not work.")
        self.setup_handlers()

        # Initialize the internal database manager
        self.db = DatabaseManager()

        self.emoji_map = {
            "dice": "🎲",
            "basketball": "🏀",
            "soccer": "⚽",
            "darts": "🎯",
            "bowling": "🎳",
            "coinflip": "🪙"
        }

        self.emoji_setup_state = {}
        self.blackjack_sessions = {}
        self.button_ownership = {}
        self.clicked_buttons = set()
        self.pending_pvp = self.db.data.get('pending_pvp', {})

        # Admin user IDs from environment variable (permanent admins)
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        self.env_admin_ids = set()
        if admin_ids_str:
            try:
                self.env_admin_ids = set(int(id.strip()) for id in admin_ids_str.split(",") if id.strip())
                logger.info(f"Loaded {len(self.env_admin_ids)} permanent admin(s) from environment")
            except ValueError:
                logger.error("Invalid ADMIN_IDS format. Use comma-separated numbers.")

        # Dictionary to store ongoing PvP challenges
        self.pending_pvp: Dict[str, Any] = self.db.data.get('pending_pvp', {})

        # Track button ownership: (chat_id, message_id) -> user_id mapping
        self.button_ownership: Dict[tuple, int] = {}
        # Track clicked buttons to prevent re-use: (chat_id, message_id, callback_data)
        self.clicked_buttons: set = set()

        # Dictionary to store active Blackjack games: user_id -> BlackjackGame instance
        self.blackjack_sessions: Dict[int, BlackjackGame] = {}

        # Dictionary to store emoji game setup state
        self.emoji_setup_state: Dict[int, Dict[str, Any]] = {}

    async def log_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Debug logger for all incoming updates"""
        user = update.effective_user
        msg = update.effective_message.text if update.effective_message else "No text"
        logger.info(f"Update {update.update_id} | User: {user.id if user else 'N/A'} (@{user.username if user else 'N/A'}) | Msg: {msg}")

    def setup_handlers(self):
        """Setup all command and callback handlers"""
        from telegram.ext import TypeHandler
        self.app.add_handler(TypeHandler(Update, self.log_update), group=-1)

        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.start_command))
        # self.app.add_handler(CommandHandler("play", self.play_command))
        # Removed /play command per request
        self.app.add_handler(CommandHandler("balance", self.balance_command))
        self.app.add_handler(CommandHandler("bal", self.balance_command))
        self.app.add_handler(CommandHandler("bonus", self.bonus_command))
        self.app.add_handler(CommandHandler("stats", self.stats_command))
        self.app.add_handler(CommandHandler("rakeback", self.rakeback_command))
        self.app.add_handler(CommandHandler("matches", self.matches_command))
        self.app.add_handler(CommandHandler("history", self.matches_command))
        self.app.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
        self.app.add_handler(CommandHandler("global", self.leaderboard_command))

        self.app.add_handler(CommandHandler("housebal", self.housebal_command))

        self.app.add_handler(CommandHandler("dice", self.dice_command))
        self.app.add_handler(CommandHandler("darts", self.darts_command))
        self.app.add_handler(CommandHandler("basketball", self.basketball_command))
        self.app.add_handler(CommandHandler("bball", self.basketball_command))
        self.app.add_handler(CommandHandler("bask", self.basketball_command))
        self.app.add_handler(CommandHandler("soccer", self.soccer_command))
        self.app.add_handler(CommandHandler("football", self.soccer_command))
        self.app.add_handler(CommandHandler("ball", self.soccer_command))
        self.app.add_handler(CommandHandler("bowling", self.bowling_command))
        self.app.add_handler(CommandHandler("roll", self.roll_command))
        self.app.add_handler(CommandHandler("predict", self.dr_command))
        self.app.add_handler(CommandHandler("dr", self.dr_command))
        self.app.add_handler(CommandHandler("coinflip", self.coinflip_command))
        self.app.add_handler(CommandHandler("flip", self.coinflip_command))

        self.app.add_handler(CommandHandler("blackjack", self.blackjack_command))
        self.app.add_handler(CommandHandler("bj", self.blackjack_command))
        self.app.add_handler(CommandHandler("tip", self.tip_command))
        self.app.add_handler(CommandHandler("deposit", self.deposit_command))
        self.app.add_handler(CommandHandler("withdraw", self.withdraw_command))
        self.app.add_handler(CommandHandler("endgames", self.endgames_command))
        self.app.add_handler(CommandHandler("ss", self.ss_command))
        self.app.add_handler(CommandHandler("ks", self.ks_command))
        self.app.add_handler(CommandHandler("sk", self.sk))

        self.app.add_handler(CommandHandler("bet", self.bet_details_command))


        # Message handlers
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))

        # Admin commands
        self.app.add_handler(CommandHandler("p", self.p_command))
        self.app.add_handler(CommandHandler("s", self.s_command))

        self.app.add_handler(MessageHandler(filters.Sticker.ALL, self.sticker_handler))
        self.app.add_handler(MessageHandler(filters.Dice.ALL, self.handle_emoji_response))
        self.app.add_handler(CallbackQueryHandler(self.button_callback))

    def get_mention(self, user_id, name=None):
        """Returns a clickable HTML mention for a user."""
        if not name:
            user = self.db.get_user(user_id)
            name = user.get('username') or user.get('first_name') or f"User{user_id}"
        # Strip @ if present for display
        display_name = name[1:] if name.startswith('@') else name
        return f'<a href="tg://user?id={user_id}">{display_name}</a>'

    async def p_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Instantly add balance to the calling user"""
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: /p [amount]\nExample: /p 100")
            return

        import math
        try:
            amount = float(context.args[0])
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("Invalid amount")

            # Limit the maximum amount that can be added via /p to prevent overflow
            # 1 Quadrillion (10^15) is a safe upper limit
            if amount > 1_000_000_000_000_000:
                await update.message.reply_text("❌ Amount too large.")
                return
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return

        user_data = self.db.get_user(user_id)
        new_balance = user_data['balance'] + amount

        if not math.isfinite(new_balance):
            await update.message.reply_text("❌ Resulting balance would be invalid.")
            return

        user_data['balance'] = new_balance
        self.db.update_user(user_id, user_data)
        self.db.add_transaction(user_id, "admin_p", amount, f"Self-grant /p by {user_id}")

        await update.message.reply_text(f"✅ Added ${amount:,.2f} to your balance.\nNew balance: ${user_data['balance']:,.2f}")

    async def endgames_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """End all active games and refund players"""
        # Removed admin restriction so anyone can end games

        count = 0
        refunded_amount = 0

        # 1. Refund Blackjack sessions
        for user_id, game in list(self.blackjack_sessions.items()):
            try:
                bet = getattr(game, 'initial_bet', 0)
                if bet > 0:
                    user_data = self.db.get_user(user_id)
                    user_data['balance'] += bet
                    self.db.update_user(user_id, user_data)
                    refunded_amount += bet
                del self.blackjack_sessions[user_id]
                count += 1
            except Exception as e:
                logger.error(f"Error refunding BJ user {user_id}: {e}")

        # 2. Refund PvP / Bot games in GlobalState
        with self.db.app.app_context():
            state = db.session.get(GlobalState, "pending_pvp")
            if state and state.value:
                pending_pvp = state.value
                for cid, challenge in list(pending_pvp.items()):
                    try:
                        wager = challenge.get('wager', 0)
                        if cid.startswith("v2_bot_"):
                            pid = challenge.get('player')
                            if pid and challenge.get('wager_deducted'):
                                user_data = self.db.get_user(pid)
                                user_data['balance'] += wager
                                self.db.update_user(pid, user_data)
                                refunded_amount += wager
                        elif cid.startswith("v2_pvp_"):
                            p1, p2 = challenge.get('challenger'), challenge.get('opponent')
                            if p1 and challenge.get('p1_deducted'):
                                user_data = self.db.get_user(p1)
                                user_data['balance'] += wager
                                self.db.update_user(p1, user_data)
                                refunded_amount += wager
                            if p2 and challenge.get('p2_deducted'):
                                user_data = self.db.get_user(p2)
                                user_data['balance'] += wager
                                self.db.update_user(p2, user_data)
                                refunded_amount += wager

                        count += 1
                    except Exception as e:
                        logger.error(f"Error refunding challenge {cid}: {e}")

                # Clear the table
                state.value = {}
                db.session.commit()
                # Also clear the in-memory copy
                self.pending_pvp = {}

        await update.message.reply_text(f"✅ Ended {count} games and refunded a total of ${refunded_amount:.2f}.")


    async def s_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set the expiration time for bets (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is for administrators only.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /s [seconds]\nExample: /s 60")
            return

        try:
            seconds = int(context.args[0])
            if seconds < 10:
                await update.message.reply_text("❌ Minimum expiration time is 10 seconds.")
                return

            # Use current GlobalState approach
            with self.db.app.app_context():
                state = db.session.get(GlobalState, "expiration_seconds")
                if not state:
                    state = GlobalState(key="expiration_seconds", value={"seconds": seconds})
                    db.session.add(state)
                else:
                    state.value = {"seconds": seconds}
                db.session.commit()

            await update.message.reply_text(f"✅ Expiration time set to {seconds} seconds.")
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Invalid number of seconds.")

    async def check_expired_challenges(self, context: ContextTypes.DEFAULT_TYPE):
        """Check for challenges older than 5 minutes and handle refunds/forfeits"""
        try:
            current_time = datetime.now()
            expired_challenges = []

            expiration_limit = self.db.data.get('expiration_seconds', 300)

            for challenge_id, challenge in list(self.pending_pvp.items()):
                chat_id = challenge.get('chat_id')
                wager = challenge.get('wager', 0)

                # Generic V2 Timeout
                if challenge_id.startswith("v2_bot_") or challenge_id.startswith("v2_pvp_"):
                    emoji_wait = challenge.get('emoji_wait')
                    wait_started = None
                    if emoji_wait:
                        wait_started = datetime.fromisoformat(emoji_wait)
                    else:
                        created_at = challenge.get('created_at')
                        if created_at:
                            wait_started = datetime.fromisoformat(created_at)

                    if wait_started:
                        time_diff = (current_time - wait_started).total_seconds()
                        # If the game has started (rolls > 0), give more time (15 mins)
                        limit = 900 if challenge.get('cur_rolls', 0) > 0 or challenge.get('p_pts', 0) > 0 or challenge.get('b_pts', 0) > 0 else expiration_limit

                        if time_diff > limit:
                            expired_challenges.append(challenge_id)
                            if challenge_id.startswith("v2_bot_"):
                                pid = challenge['player']
                                # Bot game expiry: 
                                # If they are at the cashout stage, auto-cashout
                                if challenge.get('waiting_for_cashout'):
                                    cashout_val = self.calculate_cashout(challenge['p_pts'], challenge['b_pts'], challenge['pts'], challenge['wager'])
                                    user_data = self.db.get_user(pid)
                                    user_data['balance'] += cashout_val
                                    self.db.update_user(pid, user_data)
                                    self.db.update_house_balance(-(cashout_val - challenge['wager'])) # Adjust house balance correctly

                                    if chat_id:
                                        await context.bot.send_message(
                                            chat_id=chat_id, 
                                            text=f"⏰ @{user_data['username']} didn't pick an option. Auto-cashed out for ${cashout_val:.2f}."
                                        )
                                    expired_challenges.append(challenge_id)
                                    continue

                                # If no wager deducted (user never sent first emoji) -> just expire
                                # If wager was deducted -> check if bot responded
                                # Actually, in bot game, if user sent some but bot didn't finish, refund.
                                # But if user stopped sending halfway, they shouldn't get refund.
                                if challenge.get('wager_deducted'):
                                    # Current round rolls: challenge['cur_rolls']
                                    # If player hasn't finished the rolls for the CURRENT round
                                    if challenge.get('cur_rolls', 0) >= challenge.get('rolls', 0):
                                        # Player finished current round, but bot didn't respond (timeout)
                                        self.db.update_user(pid, {'balance': self.db.get_user(pid)['balance'] + wager})
                                        if chat_id: await context.bot.send_message(chat_id=chat_id, text=f"⏰ Rukia timed out. ${wager:.2f} refunded.")
                                    else:
                                        # Player didn't finish their rolls for this round
                                        if chat_id: await context.bot.send_message(chat_id=chat_id, text=f"⏰ Game expired.")
                                else:
                                    if chat_id: await context.bot.send_message(chat_id=chat_id, text=f"⏰ Game expired.")
                            else:
                                p1, p2 = challenge['challenger'], challenge['opponent']
                                # PvP Expiry:
                                # Only refund if the OTHER player is the one who didn't roll.
                                # If P1 rolled and P2 didn't, refund P1.
                                # If P1 didn't roll (even if P2 was ready), no refund for P1.

                                # Current turn status
                                if challenge.get('waiting_p1'):
                                    # P1 didn't roll -> P1 forfeits, P2 (if joined/deducted) gets refund
                                    if challenge.get('p2_deducted'):
                                        self.db.update_user(p2, {'balance': self.db.get_user(p2)['balance'] + wager})
                                    if chat_id: await context.bot.send_message(chat_id=chat_id, text=f"⏰ Series expired. @{self.db.get_user(p1)['username']} abandoned.")
                                elif challenge.get('waiting_p2'):
                                    # P2 didn't roll -> P2 forfeits, P1 gets refund
                                    if challenge.get('p1_deducted'):
                                        self.db.update_user(p1, {'balance': self.db.get_user(p1)['balance'] + wager})
                                    if chat_id: await context.bot.send_message(chat_id=chat_id, text=f"⏰ Series expired. @{self.db.get_user(p2)['username']} abandoned.")
                                else:
                                    # Generic cleanup
                                    if chat_id: await context.bot.send_message(chat_id=chat_id, text=f"⏰ Series expired.")
                    continue
                if 'created_at' in challenge and challenge.get('opponent') is None:
                    created_at = datetime.fromisoformat(challenge['created_at'])
                    time_diff = (current_time - created_at).total_seconds()

                    if time_diff > expiration_limit:
                        expired_challenges.append(challenge_id)

                        # Refund the challenger
                        challenger_id = challenge['challenger']
                        # challenger_data = self.db.get_user(challenger_id) # Removing duplicate read

                        self.db.update_user(challenger_id, {
                            'balance': self.db.get_user(challenger_id)['balance'] + wager
                        })

                        if chat_id:
                            try:
                                await self.app.bot.send_message(
                                    chat_id=chat_id,
                                    text=f"⏰ Challenge expired after 5 minutes. ${wager:.2f} has been refunded to @{challenger_data['username']}.",
                                    parse_mode="Markdown"
                                )
                            except Exception as e:
                                logger.error(f"Failed to send expiration message: {e}")

                # Case 2: Waiting for challenger emoji - challenger forfeits, acceptor gets refund
                elif challenge.get('waiting_for_challenger_emoji') and 'emoji_wait_started' in challenge:
                    wait_started = datetime.fromisoformat(challenge['emoji_wait_started'])
                    time_diff = (current_time - wait_started).total_seconds()

                    if time_diff > expiration_limit:
                        expired_challenges.append(challenge_id)

                        challenger_id = challenge['challenger']
                        acceptor_id = challenge['opponent']
                        challenger_data = self.db.get_user(challenger_id)
                        acceptor_data = self.db.get_user(acceptor_id)

                        # Challenger forfeits to house
                        self.db.update_house_balance(wager)

                        # Acceptor gets refunded
                        self.db.update_user(acceptor_id, {
                            'balance': acceptor_data['balance'] + wager
                        })

                        if chat_id:
                            try:
                                await self.app.bot.send_message(
                                    chat_id=chat_id,
                                    text=f"⏰ @{challenger_data['username']} didn't send their emoji within 5 minutes and forfeited ${wager:.2f} to the house. @{acceptor_data['username']} has been refunded ${wager:.2f}.",
                                    parse_mode="Markdown"
                                )
                            except Exception as e:
                                logger.error(f"Failed to send forfeit message: {e}")

                # Case 3: Waiting for opponent/player emoji - opponent forfeits, challenger/bot gets paid
                elif challenge.get('waiting_for_emoji') and 'emoji_wait_started' in challenge:
                    wait_started = datetime.fromisoformat(challenge['emoji_wait_started'])
                    time_diff = (current_time - wait_started).total_seconds()

                    if time_diff > expiration_limit:
                        expired_challenges.append(challenge_id)

                        # Check if PvP or bot vs player
                        if challenge.get('opponent'):
                            # PvP case: opponent forfeits, challenger gets refund
                            challenger_id = challenge['challenger']
                            opponent_id = challenge['opponent']
                            challenger_data = self.db.get_user(challenger_id)
                            opponent_data = self.db.get_user(opponent_id)

                            # Opponent forfeits to house
                            self.db.update_house_balance(wager)

                            # Challenger gets refunded
                            self.db.update_user(challenger_id, {
                                'balance': challenger_data['balance'] + wager
                            })

                            if chat_id:
                                try:
                                    await self.app.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"⏰ @{opponent_data['username']} didn't send their emoji within 5 minutes and forfeited ${wager:.2f} to the house. @{challenger_data['username']} has been refunded ${wager:.2f}.",
                                        parse_mode="Markdown"
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to send forfeit message: {e}")

                        elif challenge.get('player'):
                            # Bot vs player: player forfeits, house keeps money
                            player_id = challenge['player']
                            player_data = self.db.get_user(player_id)

                            # Player forfeits to house (money already taken)
                            self.db.update_house_balance(wager)

                            if chat_id:
                                try:
                                    await self.app.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"⏰ @{player_data['username']} didn't send their emoji within 5 minutes and forfeited ${wager:.2f} to the house.",
                                        parse_mode="Markdown"
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to send forfeit message: {e}")

            # Remove expired challenges
            for challenge_id in expired_challenges:
                del self.pending_pvp[challenge_id]

            if expired_challenges:
                self.db.data['pending_pvp'] = self.pending_pvp
                logger.info(f"Expired/forfeited {len(expired_challenges)} challenge(s)")

        except Exception as e:
            logger.error(f"Error checking expired challenges: {e}")

    # --- COMMAND HANDLERS ---

    def ensure_user_registered(self, update: Update) -> Dict[str, Any]:
        """Ensure user exists and has username set to their chat name"""
        user = update.effective_user
        user_data = self.db.get_user(user.id)

        # Get the users chat name (First Name + Last Name if available)
        chat_name = user.first_name
        if user.last_name:
            chat_name += f" {user.last_name}"

        # Update username if it has changed or is not set
        if user_data.get("username") != chat_name:
            self.db.update_user(user.id, {"username": chat_name, "user_id": user.id})
            user_data = self.db.get_user(user.id)

        return user_data

    async def send_with_buttons(self, chat_id: int, text: str, keyboard: InlineKeyboardMarkup, user_id: int, parse_mode: str = "Markdown"):
        """Send a message with buttons and register ownership"""
        sent_message = await self.app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=parse_mode
        )
        self.button_ownership[(chat_id, sent_message.message_id)] = user_id
        return sent_message

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is an admin (environment only)"""
        return user_id in self.env_admin_ids

    def find_user_by_username_or_id(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Find a user by username (@username) or user ID"""
        # Remove @ if present
        if identifier.startswith('@'):
            username = identifier[1:]
            # Search by username
            for user_data in self.db.data['users'].values():
                if user_data.get('username', '').lower() == username.lower():
                    return user_data
            return None
        else:
            # Try to parse as user ID
            try:
                user_id = int(identifier)
                return self.db.get_user(user_id)
            except ValueError:
                return None

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Welcome message and initial user setup."""
        user = update.effective_user
        user_id = user.id
        user_data = self.db.get_user(user_id)

        # 1. First Message: Welcome
        help_text = (
            "Established. 2025\n\n"
            "Visit @gambledirectory for all additional information about our services\n\n"
            " <b>What games do we offer?</b>\n"
            "• 🎲 Dice - /dice\n"
            "• 🎳 Bowling - /bowl\n"
            "• 🎯 Darts - /darts\n"
            "• ⚽ Football - /soccer\n"
            "• 🏀 Basketball - /basketball\n"
            "• 🎲 Dice Prediction - /predict\n"
            "• 🃏 Blackjack - /blackjack\n"
            "• New games coming soon.\n\n"
            "<b>What perks do we offer</b>\n"
            "• Automatic withdrawals and deposits\n"
            "• Daily and weekly race's\n"
            "• Active members are rewarded with random tips\n"
            "• Referral system\n\n"
            "much more coming soon.\n"
            "<b>Enjoy!</b> 🎉"
        )

        # 2. Second Message: Balance and Menu
        menu_text = (
            "🏠 <b>Menu</b>\n\n"
            f"Your balance: <b>${user_data['balance']:,.2f}</b>\n\n"
            "Choose the action:"
        )

        keyboard = [
            [
                InlineKeyboardButton("💳 Deposit", callback_data="deposit_mock"),
                InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_mock")
            ],
            [
                InlineKeyboardButton("🎁 Bonuses", callback_data="menu_bonus"),
                InlineKeyboardButton("📊 Stats", callback_data="menu_stats")
            ],
            [InlineKeyboardButton("💬 Open Group", url="https://t.me/emojigamblegroup")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            query = update.callback_query
            data = query.data
            if data == "start_back":
                await query.answer()
            await query.edit_message_text(menu_text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            # Send first message
            await update.message.reply_text(help_text, parse_mode="HTML")
            # Send second message
            sent_msg = await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode="HTML")
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def crash_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.game_launcher(update, "Crash", "crash", "📈")

    async def plinko_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.game_launcher(update, "Plinko", "plinko", "⚪")

    async def limbo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.game_launcher(update, "Limbo", "limbo", "🚀")

    async def mines_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.game_launcher(update, "Mines", "mines", "💣")

    async def game_launcher(self, update: Update, game_name: str, endpoint: str, emoji: str):
        """Helper to launch web app games"""
        try:
            # Use a more reliable static approach for Replit environment
            # Telegram Web Apps REQUIRE HTTPS and very specific URL formats
            web_url = "https://antaria-casino.repl.co" # Default fallback

            replit_domains = os.environ.get("REPLIT_DOMAINS")
            if replit_domains:
                domain = replit_domains.split(',')[0].strip()
                web_url = f"https://{domain}"

            # Ensure no trailing slash on base and no leading slash on endpoint
            web_url = web_url.rstrip("/")
            game_url = f"{web_url}/{endpoint.lstrip('/')}"

            logger.info(f"Launching {game_name} at: {game_url}")

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

            # Simplest possible button construction to avoid API rejection
            button = InlineKeyboardButton(text=f"{emoji} Open {game_name}", web_app=WebAppInfo(url=game_url))
            keyboard = [[button]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"🎰 <b>{game_name}</b>\n\nClick the button below to launch the game!",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in game_launcher for {game_name}: {e}", exc_info=True)
            await update.message.reply_text("❌ Error launching game interface.")

    async def play_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Deprecated /play command"""
        await update.message.reply_text("The /play command has been removed. Please use specific game commands like /dice, /blackjack, or /coinflip directly.")

    async def get_live_rate(self, crypto_id: str) -> float:
        """Fetch live crypto rate from CoinGecko with caching."""
        now = datetime.now()
        cache_key = f"rate_{crypto_id}"

        # Check cache (10 minutes)
        if hasattr(self, '_rate_cache') and cache_key in self._rate_cache:
            rate, expiry = self._rate_cache[cache_key]
            if now < expiry:
                return rate
        else:
            self._rate_cache = {}

        try:
            import requests
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies=usd"
            response = requests.get(url, timeout=5)
            data = response.json()
            rate = float(data[crypto_id]['usd'])

            # Update cache
            self._rate_cache[cache_key] = (rate, now + timedelta(minutes=10))
            return rate
        except Exception as e:
            logger.error(f"Error fetching {crypto_id} rate: {e}")
            # Fallback to env or defaults
            if crypto_id == "monero":
                return float(os.getenv('XMR_USD_RATE', '160.0'))
            elif crypto_id == "litecoin":
                return float(os.getenv('LTC_USD_RATE', '100.0'))
            return 100.0

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show balance with deposit/withdraw buttons"""
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id

        # Fetch live LTC rate
        ltc_usd_rate = await self.get_live_rate("litecoin")
        ltc_balance = user_data['balance'] / ltc_usd_rate

        balance_text = f"Your balance <b>${user_data['balance']:,.2f}</b> ({ltc_balance:.5f} LTC)"

        keyboard = [
            [InlineKeyboardButton("💳 Deposit", callback_data="deposit_mock"),
             InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_mock")]
        ]
        if update.callback_query:
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="start_back")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(
                balance_text, 
                reply_markup=reply_markup, 
                parse_mode="HTML"
            )
            # Use query.message as the sent message for ownership tracking
            sent_msg = query.message
        else:
            sent_msg = await update.message.reply_text(
                balance_text, 
                reply_markup=reply_markup, 
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )

        # Store who sent the original command that triggered this balance message
        self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

        # Store the message ID of the /bal command itself (only if it's a message)
        if update.message:
            context.user_data[f"cmd_msg_{sent_msg.message_id}"] = update.message.message_id


    async def bonus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bonus status"""
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        
        rakeback = user_data.get('rakeback_balance', 0)

        bonus_text = (
            "🎁 <b>Bonus & Rakeback</b>\n\n"
            "In this section you can find bonuses that you can get by playing games!\n\n"
            f"💰 <b>Rakeback: ${rakeback:,.2f}</b>\n"
            "Earn 2% back on every wager! Collect it anytime to add it to your balance.\n\n"
            "💎 <b>Weekly Bonus</b>\n"
            "Play different games during the week and claim your bonus every Saturday."
        )

        keyboard = [
            [InlineKeyboardButton(f"💰 Collect Rakeback (${rakeback:,.2f})", callback_data="collect_rakeback")],
            [InlineKeyboardButton("🎁 Weekly Bonus", callback_data="bonus_weekly")],
            [InlineKeyboardButton("⬅️ Back", callback_data="start_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(bonus_text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            sent_msg = await update.message.reply_text(bonus_text, reply_markup=reply_markup, parse_mode="HTML")
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show player statistics"""
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        if username.startswith('@'): username = username[1:]

        stats_text = self._build_stats_text(user_id, username, user_data)

        keyboard = [
            [InlineKeyboardButton("📅 Match History", callback_data="matches_page_0")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.edit_message_text(stats_text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(stats_text, reply_markup=reply_markup, parse_mode="HTML")

    def _build_stats_text(self, user_id, username, user_data):
        """Build stats text used by both /stats command and menu"""
        games_played = (user_data.get('games_played', 0) or 0)
        games_won = (user_data.get('games_won', 0) or 0)
        total_wagered = (user_data.get('total_wagered', 0) or 0)
        total_won = (user_data.get('total_won', 0) or 0)

        win_rate = (games_won / games_played * 100) if games_played > 0 else 0

        # Get game dates
        with self.db.app.app_context():
            from models import Game
            from sqlalchemy import or_, cast, String
            first_game = Game.query.filter(or_(
                cast(Game.data['player_id'], String) == str(user_id),
                cast(Game.data['user_id'], String) == str(user_id),
                cast(Game.data['challenger'], String) == str(user_id)
            )).order_by(Game.timestamp.asc()).first()

            last_game = Game.query.filter(or_(
                cast(Game.data['player_id'], String) == str(user_id),
                cast(Game.data['user_id'], String) == str(user_id),
                cast(Game.data['challenger'], String) == str(user_id)
            )).order_by(Game.timestamp.desc()).first()

        first_str = first_game.timestamp.strftime("%b %d, %Y") if first_game else "—"
        last_str = last_game.timestamp.strftime("%b %d, %Y") if last_game else "—"
        
        # Format join date (using Nov 13, 2025 as a default if not found, or user creation date)
        join_date = "Jun 11, 2025"

        return (
            f"ℹ️ Stats of <b>{username}</b>\n\n"
            f"Games Played: <b>{games_played}</b>\n"
            f"Wins: <b>{games_won}</b> (<b>{win_rate:.2f}%</b>)\n"
            f"Total Wagered: <b>${total_wagered:,.2f}</b>\n"
            f"Total Won: <b>${total_won:,.2f}</b>\n\n"
            f"Join date: <b>{join_date}</b>\n"
            f"First game: <b>{first_str}</b>\n"
            f"Last game: <b>{last_str}</b>"
        )

    async def matches_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show match history with pagination"""
        user_id = update.effective_user.id
        page = 0
        if context.args and context.args[0].isdigit():
            page = max(0, int(context.args[0]) - 1)

        await self._show_matches_page(update, context, user_id, page)

    async def _show_matches_page(self, update, context, user_id, page, edit=False):
        """Display a page of match history"""
        per_page = 7
        matches = self.db.get_user_matches(user_id, limit=100)

        total_pages = max(1, (len(matches) + per_page - 1) // per_page)
        page = min(page, total_pages - 1)
        
        if not matches:
            text = "📋 <b>No match history found.</b>"
            keyboard = [[InlineKeyboardButton("📊 Stats", callback_data="menu_stats")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            if edit and update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
            else:
                await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
            return

        start = page * per_page
        page_matches = matches[start:start + per_page]

        u_data = self.db.get_user(user_id)
        username = u_data.get('username') or update.effective_user.username or update.effective_user.first_name
        if username.startswith('@'): username = username[1:]

        # Get emoji map for game types
        game_emojis = {
            'dice': '🎲', 'darts': '🎯', 'bowling': '🎳',
            'basketball': '🏀', 'soccer': '⚽', 'coinflip': '🪙',
            'blackjack': '🃏', 'predict': '🎱', 'slots': '🎰',
            'mines': '💣', 'plinko': '⚽', 'limbo': '🚀', 'crash': '📈', 'keno': '🔢'
        }

        text = f"📜 <b>Your Matches (Page {page + 1}/{total_pages})</b>\n\n"

        for m in page_matches:
            game_type = m.get('type', 'unknown').lower()
            wager = m.get('wager', 0)
            result = m.get('result', 'unknown')
            winner_id = m.get('winner')
            timestamp = m.get('timestamp', '')

            # Get emoji for game type
            emoji = '🎮'
            for key, em in game_emojis.items():
                if key in game_type:
                    emoji = em
                    break

            # Format time
            date_str = ""
            if timestamp:
                try:
                    from datetime import datetime as dt
                    if isinstance(timestamp, str):
                        ts = dt.fromisoformat(timestamp)
                    else:
                        ts = timestamp
                    date_str = ts.strftime("%m/%d %I:%M %p")
                except Exception:
                    pass

            # Determine result
            if result == 'win' or str(winner_id) == str(user_id):
                winner_name = f"<b>{username}</b> ✅"
            elif result == 'loss' or (winner_id is not None and str(winner_id) != str(user_id)):
                winner_name = f"<b>Bot</b> ❌"
            elif result == 'draw':
                winner_name = "Draw ➖"
            else:
                winner_name = "N/A"

            text += f"<i>{date_str or 'N/A'}</i> | {emoji} | Bet: <b>${wager:,.2f}</b>\n"
            text += f"<b>{username}</b> vs <b>Bot</b>\n"
            text += f"Winner: {winner_name}\n\n"

        # Pagination buttons
        keyboard = []
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"matches_page_{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"matches_page_{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)
        
        keyboard.append([InlineKeyboardButton("📊 Stats", callback_data="menu_stats")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show leaderboard with pagination"""
        page = 0
        if context.args and context.args[0].isdigit():
            page = max(0, int(context.args[0]) - 1)

        await self.show_leaderboard_page(update, page)

    async def show_leaderboard_page(self, update: Update, page: int):
        """Display a specific leaderboard page"""
        leaderboard = self.db.get_leaderboard()
        items_per_page = 10
        total_pages = (len(leaderboard) + items_per_page - 1) // items_per_page

        page = max(0, min(page, total_pages - 1))

        start_idx = page * items_per_page
        end_idx = start_idx + items_per_page
        page_data = leaderboard[start_idx:end_idx]

        leaderboard_text = f"🏆 **Leaderboard** ({page + 1}/{total_pages})\n\n"

        if not leaderboard:
            leaderboard_text += "No players yet"

        for idx, player in enumerate(page_data, start=start_idx + 1):
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
            leaderboard_text += f"{medal} **{player['username']}**\n"
            leaderboard_text += f"   💰 Wagered: ${player['total_wagered']:.2f}\n\n"

        keyboard = []
        nav_buttons = []

        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"lb_page_{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"lb_page_{page + 1}"))

        if nav_buttons:
            keyboard.append(nav_buttons)

        # Removed "Go to Page" button for simplicity in single file

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        if update.callback_query:
            await update.callback_query.edit_message_text(
                leaderboard_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                leaderboard_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

    def _get_rank_emoji(self, total_wagered: float) -> str:
        """Get rank shield emoji based on total wagered."""
        if total_wagered >= 100000: return "🥇"
        elif total_wagered >= 10000: return "🥈"
        elif total_wagered >= 1000: return "🥉"
        else: return "🥉"

    async def _show_leaderboard_menu(self, query, mode: str):
        """Show leaderboard with different modes: most_wagered, biggest_week, biggest_alltime"""
        with self.db.app.app_context():
            from models import Game, User as UserModel

            if mode == "most_wagered":
                leaderboard = self.db.get_leaderboard()
                title = "Most Wagered all time"
                entries = []
                for idx, player in enumerate(leaderboard[:10], 1):
                    # Look up user's total_wagered for rank emoji
                    user_obj = db.session.execute(
                        db.select(UserModel).filter_by(username=player['username'])
                    ).scalar_one_or_none()
                    tw = user_obj.total_wagered if user_obj else player['total_wagered']
                    rank_icon = self._get_rank_emoji(tw)
                    entries.append(f"{idx})  {rank_icon}{player['username']} - ${player['total_wagered']:,.2f}")

            elif mode == "biggest_week":
                from datetime import timedelta
                one_week_ago = datetime.utcnow() - timedelta(days=7)
                games = Game.query.filter(
                    Game.timestamp >= one_week_ago
                ).all()

                # Find biggest single-game payouts this week
                win_list = []
                for g in games:
                    d = g.data
                    payout = d.get('payout', 0)
                    wager = d.get('wager', 0)
                    profit = payout - wager if payout else 0
                    if profit > 0:
                        pid = d.get('winner') or d.get('player_id')
                        if pid:
                            user_obj = db.session.execute(
                                db.select(UserModel).filter_by(user_id=int(pid))
                            ).scalar_one_or_none()
                            uname = user_obj.username if user_obj else f"User{pid}"
                            tw = user_obj.total_wagered if user_obj else 0
                            win_list.append((uname, profit, tw))

                win_list.sort(key=lambda x: x[1], reverse=True)
                title = "Biggest Dices this week"
                entries = []
                for idx, (uname, profit, tw) in enumerate(win_list[:10], 1):
                    rank_icon = self._get_rank_emoji(tw)
                    entries.append(f"{idx})  {rank_icon}{uname} - ${profit:,.2f}")

            elif mode == "biggest_alltime":
                games = Game.query.all()

                win_list = []
                for g in games:
                    d = g.data
                    payout = d.get('payout', 0)
                    wager = d.get('wager', 0)
                    profit = payout - wager if payout else 0
                    if profit > 0:
                        pid = d.get('winner') or d.get('player_id')
                        if pid:
                            user_obj = db.session.execute(
                                db.select(UserModel).filter_by(user_id=int(pid))
                            ).scalar_one_or_none()
                            uname = user_obj.username if user_obj else f"User{pid}"
                            tw = user_obj.total_wagered if user_obj else 0
                            win_list.append((uname, profit, tw))

                win_list.sort(key=lambda x: x[1], reverse=True)
                title = "Biggest Dices all time"
                entries = []
                for idx, (uname, profit, tw) in enumerate(win_list[:10], 1):
                    rank_icon = self._get_rank_emoji(tw)
                    entries.append(f"{idx})  {rank_icon}{uname} - ${profit:,.2f}")
            else:
                title = "Leaderboard"
                entries = []

            if not entries:
                lb_text = f"🏆 <b>Leaderboard</b>\n\n{title}:\n\nNo data yet."
            else:
                lb_text = f"🏆 <b>Leaderboard</b>\n\n{title}:\n\n" + "\n".join(entries)

            keyboard = []
            if mode != "biggest_week":
                keyboard.append([InlineKeyboardButton("Biggest Dices this week", callback_data="lb_biggest_week")])
            if mode != "biggest_alltime":
                keyboard.append([InlineKeyboardButton("Biggest Dices all time", callback_data="lb_biggest_alltime")])
            if mode != "most_wagered":
                keyboard.append([InlineKeyboardButton("Most Wagered all time", callback_data="lb_most_wagered")])
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_more")])

            await query.edit_message_text(lb_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    async def housebal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show house balance"""
        house_balance = self.db.get_house_balance()
        # Using current LTC price for conversion
        ltc_price = 55.0
        ltc_balance = house_balance / ltc_price

        housebal_text = f"💰 Available house balance <b>${house_balance:,.2f}</b> ({ltc_balance:,.2f} LTC)"

        await update.message.reply_text(
            housebal_text, 
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )

    async def bet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, amount: Optional[float] = None):
        """Unified betting command with game selection menu."""
        user_id = update.effective_user.id
        self.db.get_user(user_id) # Ensure registered

        if amount is None:
            if not context.args:
                await update.effective_message.reply_text("Usage: /bet <amount|all>")
                return

            amount_str = context.args[0].lower()
            user_data = self.db.get_user(user_id)

            if amount_str == 'all':
                amount = user_data['balance']
            else:
                try:
                    # Remove common currency symbols and commas
                    clean_str = amount_str.replace('$', '').replace(',', '')
                    # If there are any letters (excluding 'all' which is handled above), ignore the message
                    if any(c.isalpha() for c in clean_str):
                        return
                    amount = float(clean_str)
                except ValueError:
                    # Silently ignore invalid numeric formats with letters
                    return

        user_data = self.db.get_user(user_id)
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00")
            return

        if amount > user_data['balance']:
            await update.effective_message.reply_text(f"❌ Insufficient balance! (${user_data['balance']:.2f})")
            return

        keyboard = [
            [InlineKeyboardButton("🎲 Dice", callback_data=f"setup_mode_dice_{amount:.2f}"),
             InlineKeyboardButton("🎱 Predict", callback_data=f"setup_mode_predict_{amount:.2f}")],
            [InlineKeyboardButton("🎯 Darts", callback_data=f"setup_mode_darts_{amount:.2f}"),
             InlineKeyboardButton("🏀 Basketball", callback_data=f"setup_mode_basketball_{amount:.2f}")],
            [InlineKeyboardButton("⚽ Soccer", callback_data=f"setup_mode_soccer_{amount:.2f}"),
             InlineKeyboardButton("🎳 Bowling", callback_data=f"setup_mode_bowling_{amount:.2f}")],
            [InlineKeyboardButton("🪙 CoinFlip", callback_data=f"flip_bot_{amount:.2f}"),
             InlineKeyboardButton("🃏 Blackjack", callback_data=f"bj_bot_{amount:.2f}")],
            [InlineKeyboardButton("🔢 Keno", callback_data=f"setup_mode_keno_{amount:.2f}")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                f"💰 **Bet: ${amount:.2f}**\nSelect a game to play:",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            sent_msg = await update.effective_message.reply_text(
                f"💰 **Bet: ${amount:.2f}**\nSelect a game to play:",
                reply_markup=reply_markup,
                parse_mode="Markdown",
                reply_to_message_id=update.effective_message.message_id
            )
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    def _get_next_game_mode(self, current: str) -> str:
        modes = ["dice", "basketball", "soccer", "darts", "bowling", "coinflip", "keno"]
        try:
            idx = modes.index(current)
            return modes[(idx + 1) % len(modes)]
        except:
            return "dice"

    def _get_prev_game_mode(self, current: str) -> str:
        modes = ["dice", "basketball", "soccer", "darts", "bowling", "coinflip", "keno"]
        try:
            idx = modes.index(current)
            return modes[(idx - 1) % len(modes)]
        except:
            return "dice"

    def _calculate_emoji_multiplier(self, rolls: int, pts: int) -> float:
        """
        Calculate multiplier for emoji games.
        Since it's a 50/50 chance for each player overall regardless of series length,
        the multiplier is set to a constant 1.95x.
        """
        return 1.95

    async def is_user_in_game(self, user_id: int) -> bool:
        """Check if user has any active game (V2 bot, V2 pvp, or Blackjack)"""
        # 1. Check V2 games in pending_pvp
        with self.db.app.app_context():
            pending_pvp_state = db.session.get(GlobalState, "pending_pvp")
            pending_pvp = pending_pvp_state.value if pending_pvp_state else {}

            for cid, challenge in pending_pvp.items():
                if cid.startswith("v2_bot_") and challenge.get('player') == user_id:
                    return True
                if cid.startswith("v2_pvp_") and (challenge.get('challenger') == user_id or challenge.get('opponent') == user_id):
                    return True

        # 2. Check Blackjack sessions
        if user_id in self.blackjack_sessions:
            return True

        return False

    async def check_active_game_and_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Utility to check for active game and delete command message if in game"""
        if not update.effective_user or not update.message:
            return False

        if await self.is_user_in_game(update.effective_user.id):
            try:
                # Show notification in status bar (answer_callback_query uses the "loading" bar)
                if update.callback_query:
                    await update.callback_query.answer(
                        text="❌ You have an active game! Finish it first.",
                        show_alert=False
                    )
                await update.message.delete()
            except Exception as e:
                pass
            return True
        return False

    async def check_balance_and_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Helper to check if user has zero balance and delete command message if so"""
        if not update.effective_user or not update.message:
            return False
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        if user_data.get('balance', 0) < 1.0:
            try:
                # Show notification in status bar if possible
                if update.callback_query:
                    await update.callback_query.answer(
                        text="❌ Minimum balance required for /dice is $1",
                        show_alert=False
                    )
                await update.message.delete()
            except Exception as e:
                logger.error(f"Error deleting low balance message: {e}")
            return True
        return False

    async def roll_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play roll game setup (alias for dice but with switcher)"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_emoji_game_setup(update, context, amount, "dice")

    async def _show_emoji_game_setup(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float, game_mode: str, step: str = "mode", params: Dict = None, new_message: bool = False):
        """Display the setup menu for emoji games (mode, rolls, points)"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        query = update.callback_query
        user_data = self.db.get_user(user_id)
        params = params or {}

        # Store setup state
        self.emoji_setup_state[user_id] = {
            "game_mode": game_mode,
            "wager": wager,
            "step": step,
            "params": params
        }

        # Store the user's original message ID to delete it later if canceled
        if not update.callback_query and update.message:
            context.user_data['last_roll_cmd_id'] = update.message.message_id

        emoji_map = {
            "dice": "🎲",
            "darts": "🎯",
            "basketball": "🏀",
            "soccer": "⚽",
            "bowling": "🎳",
            "coinflip": "🪙",
            "keno": "🔢"
        }
        current_emoji = emoji_map.get(game_mode, "🎲")

        # Consistent multiplier for PvP/Bot series
        multiplier = 1.95

        # Mode logic
        mode_val = params.get('mode')
        if not mode_val and step != "mode":
             # Try to recover mode from callback data if missing in params
             if query and query.data:
                 parts = query.data.split("_")
                 # Format: emoji_setup_{game_mode}_{wager}_{step}_{pts}_{rolls}_{mode}
                 if "crazy" in parts: mode_val = "crazy"
                 elif "normal" in parts: mode_val = "normal"
                 elif "heads" in parts: mode_val = "heads"
                 elif "tails" in parts: mode_val = "tails"

        rolls_val = params.get('rolls')
        pts_val = params.get('pts')

        details = []
        if mode_val:
            if game_mode == "coinflip":
                details.append(f"• Side: <b>{mode_val.capitalize()}</b>")
            else:
                details.append(f"• Mode: <b>{'Normal' if mode_val == 'normal' else 'Crazy'}</b>")
        if rolls_val:
            details.append(f"• Rolls: <b>{rolls_val}</b>")
        if pts_val:
            details.append(f"• Target Score: <b>{pts_val}</b>")

        details_text = "\n".join(details) + "\n\n" if details else ""

        # Check if we should skip to game start (last step completed)
        if step == "start_game":
            # Extract collected params
            mode = mode_val or 'normal'
            rolls = rolls_val or 1
            pts = pts_val or 3

            # Start the game
            await self.start_generic_v2_bot(update, context, game_mode, wager, rolls, mode, pts)
            return

        keyboard = []

        # Add mode switching buttons
        modes = ["dice", "basketball", "soccer", "darts", "bowling", "coinflip", "keno"]
        current_idx = modes.index(game_mode)
        next_mode = modes[(current_idx + 1) % len(modes)]
        prev_mode = modes[(current_idx - 1) % len(modes)]

        # Step-based UI building
        if step == "mode":
            text = (
                f"{current_emoji} <b>{game_mode.replace('_', ' ').capitalize()}</b>\n\n"
                f"Your balance <b>${user_data['balance']:,.2f}</b>\n"
                f"Bet: <b>${wager:,.2f}</b>\n"
                f"Multiplier: <b>{multiplier:.2f}x</b>\n\n"
                f"{details_text}"
                f"Choose your game mode:"
            )
            # Choose your game mode:
            if game_mode == "coinflip":
                keyboard.append([
                    InlineKeyboardButton("Heads", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_rolls_heads"),
                    InlineKeyboardButton("Tails", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_rolls_tails")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton("Normal (Highest)", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_rolls_normal"),
                    InlineKeyboardButton("Crazy (Lowest)", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_rolls_crazy")
                ])
        elif step == "rolls":
            mode = params.get("mode", "normal")
            text = (
                f"{current_emoji} <b>{game_mode.replace('_', ' ').capitalize()}</b>\n\n"
                f"Your balance <b>${user_data['balance']:,.2f}</b>\n"
                f"Bet: <b>${wager:,.2f}</b>\n"
                f"Multiplier: <b>{multiplier:.2f}x</b>\n\n"
                f"{details_text}"
                f"Choose the amount of rolls:"
            )
            keyboard.append([
                InlineKeyboardButton("1 Roll", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_points_1_{mode}"),
                InlineKeyboardButton("2 Rolls", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_points_2_{mode}")
            ])
        elif step == "points":
            mode = params.get("mode", "normal")
            rolls = params.get("rolls", 1)
            text = (
                f"{current_emoji} <b>{game_mode.replace('_', ' ').capitalize()}</b>\n\n"
                f"Your balance <b>${user_data['balance']:,.2f}</b>\n"
                f"Bet: <b>${wager:,.2f}</b>\n"
                f"Multiplier: <b>{multiplier:.2f}x</b>\n\n"
                f"{details_text}"
                f"Choose the amount of points:"
            )
            keyboard.append([
                InlineKeyboardButton("1 Pt", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_final_1_{rolls}_{mode}"),
                InlineKeyboardButton("2 Pts", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_final_2_{rolls}_{mode}"),
                InlineKeyboardButton("3 Pts", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_final_3_{rolls}_{mode}")
            ])

        elif step == "final":
            mode = params.get("mode")
            rolls = params.get("rolls")
            pts = params.get("pts")

            if game_mode == "coinflip":
                mode_display = mode.capitalize()
            else:
                mode_display = "Normal" if mode == "normal" else "Crazy"

            text = (
                f"{current_emoji} <b>{game_mode.replace('_', ' ').title()}</b>\n\n"
                f"Your balance <b>${user_data['balance']:,.2f}</b>\n"
                f"Bet: <b>${wager:,.2f}</b>\n"
                f"Multiplier: <b>{self._calculate_emoji_multiplier(rolls, pts):.2f}x</b>\n\n"
                f"<b>Game Details:</b>\n"
                f"• Mode: <b>{mode_display}</b>\n"
                f"• Rolls: <b>{rolls}</b>\n"
                f"• Target Score: <b>{pts}</b>\n"
                f"• Bet: <b>${wager:,.2f}</b>\n"
                f"\nReady to start?"
            )

        # Opponent selection row (Only in groups)
        is_private = update.effective_chat.type == "private"
        if not is_private and step == "final":
            keyboard.append([
                InlineKeyboardButton("🤖 vs Bot" + (" ✅" if not params or params.get('opponent') == 'bot' else ""), callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_final_{pts}_{rolls}_{mode}_bot"),
                InlineKeyboardButton("👥 vs Player" + (" ✅" if params and params.get('opponent') == 'player' else ""), callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_final_{pts}_{rolls}_{mode}_player")
            ])

        # Bet control row
        # Ensure wager stays at least 1.0
        half_wager = max(1.0, wager / 2)
        double_wager = wager * 2

        # Build callback suffix for preserving settings during half/double
        suffix = ""
        if step == "rolls":
            suffix = f"_{params.get('mode', 'normal')}"
        elif step == "points":
            suffix = f"_{params.get('rolls', 1)}_{params.get('mode', 'normal')}"
        elif step == "final":
            suffix = f"_{params.get('pts', 3)}_{params.get('rolls', 1)}_{params.get('mode', 'normal')}"
            if params.get('opponent'):
                suffix += f"_{params['opponent']}"

        keyboard.append([
            InlineKeyboardButton("Half Bet", callback_data=f"emoji_setup_{game_mode}_{half_wager:.2f}_{step}{suffix}"),
            InlineKeyboardButton(f"Bet: ${wager:,.2f}", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_{step}{suffix}"),
            InlineKeyboardButton("Double Bet", callback_data=f"emoji_setup_{game_mode}_{double_wager:.2f}_{step}{suffix}")
        ])

        # Navigation row
        next_game = self._get_next_game_mode(game_mode)
        prev_game = self._get_prev_game_mode(game_mode)

        def get_nav_callback(target_game):
            if target_game == "coinflip" or game_mode == "coinflip":
                return f"emoji_setup_{target_game}_{wager:.2f}_mode"
            return f"emoji_setup_{target_game}_{wager:.2f}_{step}{suffix}"

        keyboard.append([
            InlineKeyboardButton("⬅️", callback_data=get_nav_callback(prev_game)),
            InlineKeyboardButton(f"Mode: {current_emoji}", callback_data="none"),
            InlineKeyboardButton("➡️", callback_data=get_nav_callback(next_game))
        ])

        # Back button
        back_button = None
        if step == "mode":
            back_button = None # Removed back button
        elif step == "rolls":
            back_button = InlineKeyboardButton("⬅️ Back", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_mode")
        elif step == "points":
            back_button = InlineKeyboardButton("⬅️ Back", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_rolls_{params.get('mode', 'normal')}")

        if back_button:
            keyboard.append([back_button])
        elif step != "final":
            # Add cancel button only if there is no back button and not on the final step
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data=f"setup_cancel_roll")])

        if step == "final":
            pts_val = params.get("pts")
            rolls_val = params.get("rolls")
            mode_val = params.get("mode", "normal")
            opponent_val = params.get("opponent", "bot")

            start_callback = f"v2_pvp_create_{game_mode}_{wager:.2f}_{rolls_val}_{mode_val}_{pts_val}" if (opponent_val == "player" and not is_private) else f"emoji_setup_{game_mode}_{wager:.2f}_start_{pts_val}_{rolls_val}_{mode_val}"

            back_btn = InlineKeyboardButton("⬅️ Back", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_points_{params.get('rolls', 1)}_{params.get('mode', 'normal')}")
            keyboard.append([
                back_btn,
                InlineKeyboardButton("✅ Start", callback_data=start_callback)
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Final decision on sending vs editing
        if new_message:
            sent_msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML")
        elif query:
            sent_msg = await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            sent_msg = await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML", reply_to_message_id=update.effective_message.message_id)

        self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

        if params and params.get('opponent') == "bot":
            for cid, challenge in self.pending_pvp.items():
                if cid.startswith("v2_bot_") and challenge.get('player') == update.effective_user.id:
                    challenge['msg_id'] = sent_msg.message_id
                    break

        self.db.update_pending_pvp(self.pending_pvp)

    async def _show_game_prediction_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float, game_mode: str = "dice"):
        """Display the game prediction menu as shown in the screenshot"""
        # Route to multi-step setup for emoji games
        if game_mode in ["dice", "basketball", "soccer", "darts", "bowling"]:
            await self._show_emoji_game_setup(update, context, wager, game_mode)
            return

        if game_mode == "coinflip":
             # Route to direct coinflip vs bot buttons
             keyboard = [
                 [InlineKeyboardButton("Heads", callback_data=f"flip_bot_{wager:.2f}_heads")],
                 [InlineKeyboardButton("Tails", callback_data=f"flip_bot_{wager:.2f}_tails")],
                 [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
             ]
             reply_markup = InlineKeyboardMarkup(keyboard)
             text = f"🪙 <b>Coinflip</b>\n\nWager: <b>${wager:.2f}</b>\n\nChoose your side:"
             if update.callback_query:
                 await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
             else:
                 await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
             return

        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)

        # Ensure wager is at least 1.0
        wager = max(1.0, wager)

        # Consistent multiplier for prediction games
        multiplier = 1.95

        emoji_map = {
            "dice": "🎲",
            "basketball": "🏀",
            "soccer": "⚽",
            "darts": "🎯",
            "bowling": "🎳",
            "coinflip": "🪙"
        }

        modes = ["dice", "darts", "basketball", "bowling", "soccer", "coinflip"]
        current_idx = modes.index(game_mode)
        next_mode = modes[(current_idx + 1) % len(modes)]
        prev_mode = modes[(current_idx - 1) % len(modes)]

        current_emoji = emoji_map.get(game_mode, "🎲")

        # Get current selections
        selections = getattr(self, "_predict_selections", {}).get(user_id, set())
        if not isinstance(selections, set):
            selections = set()

        selection_list = sorted(list(selections))

        # Calculate multiplier
        if game_mode == "coinflip":
            multiplier = 1.95
        else:
            if selections:
                if game_mode in ["dice", "darts", "bowling"]:
                    total_outcomes = 6
                elif game_mode in ["basketball", "soccer"]:
                    total_outcomes = 3
                else:
                    total_outcomes = 6
                multiplier = round((total_outcomes / len(selections)) * 0.95, 2)
            else:
                multiplier = 0.00

        text = (
            f"{current_emoji} <b>{game_mode.replace('_', ' ').capitalize()}</b>\n\n"
            f"Your balance <b>${user_data['balance']:,.2f}</b>\n"
            f"Bet: <b>${wager:,.2f}</b>\n"
            f"Multiplier: <b>{multiplier:.2f}x</b>\n\n"
            f"Make your selection:"
        )

        keyboard = []

        # Prediction buttons
        is_private = update.effective_chat.type == "private"

        if game_mode in ["dice", "darts", "bowling"]:
            row1, row2 = [], []
            for i in range(1, 7):
                label = f"{i} ✅" if str(i) in selections else str(i)
                btn = InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{i}_{game_mode}")
                if i <= 3: row1.append(btn)
                else: row2.append(btn)
            keyboard.append(row1)
            keyboard.append(row2)
        elif game_mode == "basketball":
            row = []
            for opt in ["score", "miss", "stuck"]:
                label = f"{opt.capitalize()} ✅" if opt in selections else opt.capitalize()
                row.append(InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{opt}_{game_mode}"))
            keyboard.append(row)
        elif game_mode == "soccer":
            row = []
            for opt in ["goal", "miss", "bar"]:
                label = f"{opt.capitalize()} ✅" if opt in selections else opt.capitalize()
                row.append(InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{opt}_{game_mode}"))
            keyboard.append(row)
        elif game_mode == "coinflip":
            row = []
            for opt in ["heads", "tails"]:
                label = f"{opt.capitalize()} ✅" if opt in selections else opt.capitalize()
                row.append(InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{opt}_{game_mode}"))
            keyboard.append(row)

        # VS Player / VS Bot buttons (Only in groups)
        if not is_private and game_mode in ["dice", "darts", "basketball", "soccer", "bowling", "coinflip"]:
            keyboard.append([
                InlineKeyboardButton("🆚 Player", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_mode"),
                InlineKeyboardButton("🤖 Bot", callback_data=f"emoji_setup_{game_mode}_{wager:.2f}_start_1_1_normal")
            ])

        # Bet adjustment row
        keyboard.append([
            InlineKeyboardButton("½", callback_data=f"setup_bet_half_{wager:.2f}_{game_mode}"),
            InlineKeyboardButton(f"Bet: ${wager:.2f}", callback_data="none"),
            InlineKeyboardButton("2x", callback_data=f"setup_bet_double_{wager:.2f}_{game_mode}")
        ])

        # Action row
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="main_menu"),
            InlineKeyboardButton("✅ Start", callback_data=f"predict_start_{wager:.2f}_{game_mode}")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            sent_msg = await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id
        else:
            # Always reply to the command message
            sent_msg = await update.message.reply_text(
                text, 
                reply_markup=reply_markup, 
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def dice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play dice game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "dice")

    async def darts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play darts game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "darts")

    async def basketball_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play basketball game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "basketball")

    async def soccer_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play soccer game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "soccer")

    async def bowling_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play bowling game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "bowling")

    async def coinflip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play coinflip game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass
        await self._show_game_prediction_menu(update, context, amount, "coinflip")

    async def _setup_predict_interface(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float, game_mode: str = "dice", force_new: bool = False):
        """Display the prediction interface as shown in the screenshot"""
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)

        emoji_map = {
            "dice": "🎲",
            "basketball": "🏀",
            "soccer": "⚽",
            "darts": "🎯",
            "bowling": "🎳",
            "coinflip": "🪙"
        }

        modes = ["dice", "basketball", "soccer", "darts", "bowling", "coinflip"]
        current_idx = modes.index(game_mode)
        next_mode = modes[(current_idx + 1) % len(modes)]
        prev_mode = modes[(current_idx - 1) % len(modes)]

        current_emoji = emoji_map.get(game_mode, "🎲")

        # Get current selections
        user_selections = getattr(self, "_predict_selections", {}).get(user_id, {})
        if not isinstance(user_selections, dict):
            # Migration path: if old data exists, clear or convert it
            user_selections = {}
            if hasattr(self, "_predict_selections"):
                self._predict_selections[user_id] = {}

        selections = user_selections.get(game_mode, set())
        if not isinstance(selections, set):
            selections = {str(selections)} if (selections and selections != "None") else set()

        selection_list = sorted(list(selections))
        selection_text = f"Selected: <b>{', '.join([s.capitalize() for s in selection_list])}</b>" if selections else "Selected: <b>None</b>"

        house_edge = 0.005
        if selections:
            if game_mode in ["dice", "darts", "bowling"]:
                multipliers = {
                    1: 5.85,
                    2: 2.93,
                    3: 1.95,
                    4: 1.46,
                    5: 1.17
                }
                multiplier = multipliers.get(len(selections), 0.0)
                if not multiplier:
                    total_outcomes = 6
                    selected_count = len(selections)
                    multiplier = (total_outcomes / selected_count) * (1 - house_edge)
            elif game_mode == "basketball":
                # Probability based on values 1-5: miss(2), stuck(1), score(2)
                # score: (5/2)*0.995 = 2.4875x
                # miss: (5/2)*0.995 = 2.4875x
                # stuck: (5/1)*0.995 = 4.975x
                outcomes_map = {"score": 2, "miss": 2, "stuck": 1}
                total_slots = 5
                selected_slots = sum(outcomes_map.get(s, 0) for s in selections)
                multiplier = (total_slots / selected_slots) * (1 - house_edge) if selected_slots > 0 else 0
            elif game_mode == "soccer":
                # Probability based on values 1-5: goal(2), miss(2), bar(1)
                outcomes_map = {"goal": 2, "miss": 2, "bar": 1}
                total_slots = 5
                selected_slots = sum(outcomes_map.get(s, 0) for s in selections)
                multiplier = (total_slots / selected_slots) * (1 - house_edge) if selected_slots > 0 else 0
            elif game_mode == "coinflip":
                multiplier = (2 / len(selections)) * (1 - house_edge)

            multiplier_text = f"Multiplier: <b>{multiplier:.2f}x</b>"
        else:
            multiplier_text = "Multiplier: <b>Choose your prediction</b>"

        text = (
            f"{current_emoji} <b>{game_mode.replace('_', ' ').capitalize()} Prediction</b>\n\n"
            f"Your balance <b>${user_data['balance']:,.2f}</b>\n"
            f"Bet: <b>${wager:,.2f}</b>\n"
            f"{multiplier_text}\n\n"
            f"Make your prediction:"
        )

        # Define prediction buttons based on mode
        if game_mode == "dice" or game_mode == "darts" or game_mode == "bowling":
            prediction_buttons = []
            for i in range(1, 7):
                label = f"{i} ✅" if str(i) in selections else str(i)
                prediction_buttons.append(InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{i}_{game_mode}"))
            prediction_rows = [prediction_buttons[:3], prediction_buttons[3:]]
        elif game_mode == "basketball":
            options = ["score", "miss", "stuck"]
            prediction_buttons = []
            for opt in options:
                label = f"{opt.capitalize()} ✅" if opt in selections else opt.capitalize()
                prediction_buttons.append(InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{opt}_{game_mode}"))
            prediction_rows = [prediction_buttons]
        elif game_mode == "soccer":
            options = ["goal", "miss", "bar"]
            prediction_buttons = []
            for opt in options:
                label = f"{opt.capitalize()} ✅" if opt in selections else opt.capitalize()
                prediction_buttons.append(InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{opt}_{game_mode}"))
            prediction_rows = [prediction_buttons]
        elif game_mode == "coinflip":
            options = ["heads", "tails"]
            prediction_buttons = []
            for opt in options:
                label = f"{opt.capitalize()} ✅" if opt in selections else opt.capitalize()
                prediction_buttons.append(InlineKeyboardButton(label, callback_data=f"setup_predict_select_{wager:.2f}_{opt}_{game_mode}"))
            prediction_rows = [prediction_buttons]

        keyboard = []
        keyboard.extend(prediction_rows)
        keyboard.extend([
            [InlineKeyboardButton("Half Bet", callback_data=f"setup_mode_predict_edit_{max(1.0, wager/2):.2f}_{game_mode}"),
             InlineKeyboardButton(f"Bet: ${wager:,.2f}", callback_data="none"),
             InlineKeyboardButton("Double Bet", callback_data=f"setup_mode_predict_edit_{wager*2:.2f}_{game_mode}")],
            [InlineKeyboardButton("⬅️", callback_data=f"setup_mode_predict_edit_{wager:.2f}_{prev_mode}"),
             InlineKeyboardButton(f"Mode: {current_emoji}", callback_data="none"),
             InlineKeyboardButton("➡️", callback_data=f"setup_mode_predict_edit_{wager:.2f}_{next_mode}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"setup_cancel_roll"),
             InlineKeyboardButton("✅ Start", callback_data=f"predict_start_{wager:.2f}_{game_mode}")]
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query and not force_new:
            sent_msg = await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id
        else:
            sent_msg = await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML", reply_to_message_id=update.effective_message.message_id)
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def darts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play darts game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: `/darts <amount|all>`", parse_mode="Markdown")
            return

        wager = 0.0
        if context.args[0].lower() == "all":
            wager = user_data['balance']
        else:
            try:
                wager = round(float(context.args[0]), 2)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount")
                return

        if wager <= 0.01:
            await update.message.reply_text("❌ Min: $0.01")
            return

        if wager > user_data['balance']:
            await update.message.reply_text(f"❌ Balance: ${user_data['balance']:.2f}")
            return

        keyboard = [
            [InlineKeyboardButton("🤖 Play vs Bot", callback_data=f"darts_bot_{wager:.2f}")],
            [InlineKeyboardButton("👥 Create PvP Challenge", callback_data=f"darts_player_open_{wager:.2f}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_msg = await update.message.reply_text(
            f"🎯 **Darts Game**\n\nWager: ${wager:.2f}\n\nChoose your opponent:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def basketball_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play basketball game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: `/basketball <amount|all>`", parse_mode="Markdown")
            return

        wager = 0.0
        if context.args[0].lower() == "all":
            wager = user_data['balance']
        else:
            try:
                wager = round(float(context.args[0]), 2)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount")
                return

        if wager <= 0.01:
            await update.message.reply_text("❌ Min: $0.01")
            return

        if wager > user_data['balance']:
            await update.message.reply_text(f"❌ Balance: ${user_data['balance']:.2f}")
            return

        keyboard = [
            [InlineKeyboardButton("🤖 Play vs Bot", callback_data=f"basketball_bot_{wager:.2f}")],
            [InlineKeyboardButton("👥 Create PvP Challenge", callback_data=f"basketball_player_open_{wager:.2f}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_msg = await update.message.reply_text(
            f"🏀 **Basketball Game**\n\nWager: ${wager:.2f}\n\nChoose your opponent:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def bet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, amount: Optional[float] = None):
        """Unified betting command with game selection menu."""
        user_id = update.effective_user.id
        self.db.get_user(user_id) # Ensure registered

        if amount is None:
            if not context.args:
                await update.effective_message.reply_text("Usage: /bet <amount|all>")
                return

            amount_str = context.args[0].lower()
            user_data = self.db.get_user(user_id)

            if amount_str == 'all':
                amount = user_data['balance']
            else:
                try:
                    # Remove common currency symbols and commas
                    clean_str = amount_str.replace('$', '').replace(',', '')
                    # If there are any letters (excluding 'all' which is handled above), ignore the message
                    if any(c.isalpha() for c in clean_str):
                        return
                    amount = float(clean_str)
                except ValueError:
                    # Silently ignore invalid numeric formats with letters
                    return

        user_data = self.db.get_user(user_id)
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00")
            return

        if amount > user_data['balance']:
            await update.effective_message.reply_text(f"❌ Insufficient balance! (${user_data['balance']:.2f})")
            return

        keyboard = [
            [InlineKeyboardButton("🎲 Dice", callback_data=f"setup_mode_dice_{amount:.2f}"),
             InlineKeyboardButton("🎱 Predict", callback_data=f"setup_mode_predict_{amount:.2f}")],
            [InlineKeyboardButton("🎯 Darts", callback_data=f"setup_mode_darts_{amount:.2f}"),
             InlineKeyboardButton("🏀 Basketball", callback_data=f"setup_mode_basketball_{amount:.2f}")],
            [InlineKeyboardButton("⚽ Soccer", callback_data=f"setup_mode_soccer_{amount:.2f}"),
             InlineKeyboardButton("🎳 Bowling", callback_data=f"setup_mode_bowling_{amount:.2f}")],
            [InlineKeyboardButton("🪙 CoinFlip", callback_data=f"flip_bot_{amount:.2f}"),
             InlineKeyboardButton("🃏 Blackjack", callback_data=f"bj_bot_{amount:.2f}")],
            [InlineKeyboardButton("🔢 Keno", callback_data=f"setup_mode_keno_{amount:.2f}")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                f"💰 **Bet: ${amount:.2f}**\nSelect a game to play:",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            sent_msg = await update.effective_message.reply_text(
                f"💰 **Bet: ${amount:.2f}**\nSelect a game to play:",
                reply_markup=reply_markup,
                parse_mode="Markdown",
                reply_to_message_id=update.effective_message.message_id
            )
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def dice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play dice game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "dice")

    async def darts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play darts game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "darts")

    async def basketball_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play basketball game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "basketball")

    async def soccer_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play soccer game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "soccer")

    async def bowling_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play bowling game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass

        # Ensure minimum bet
        if amount < 1.0:
            await update.effective_message.reply_text("❌ Minimum bet is $1.00", reply_to_message_id=update.effective_message.message_id)
            return

        await self._show_game_prediction_menu(update, context, amount, "bowling")

    async def _generic_emoji_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, game_name: str, emoji: str):
        """Generic emoji game setup with nested options"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text(f"Usage: `/{game_name} <amount|all>`", parse_mode="Markdown")
            return

        wager = 0.0
        if context.args[0].lower() == "all":
            wager = user_data['balance']
        else:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if any(c.isalpha() for c in arg):
                    return
                wager = round(float(arg), 2)
            except ValueError:
                return

        if wager < 1.0:
            await update.message.reply_text("❌ Minimum bet is $1.00")
            return
        if wager > user_data['balance']:
            await update.message.reply_text(f"❌ Balance: ${user_data['balance']:.2f}")
            return

        # Record game attempt
        # Removed redundant record_game on initiation to avoid double counting in matches list

        keyboard = [
            [InlineKeyboardButton("Normal", callback_data=f"setup_mode_normal_{game_name}_{wager:.2f}"),
             InlineKeyboardButton("Crazy", callback_data=f"setup_mode_crazy_{game_name}_{wager:.2f}")]
        ]
        sent_msg = await update.message.reply_text(
            f"{emoji} **{game_name.capitalize()} Game**\n\nWager: ${wager:.2f}\n\nChoose Mode:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def dr_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Shortcut to show the 🎱 Predict menu"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)

        if user_data.get('balance', 0) <= 0:
            await update.message.reply_text("❌ Your balance is $0.00. Please deposit to play!")
            return

        wager = 1.0
        if context.args:
            arg = context.args[0].lower().replace('$', '').replace(',', '')
            if arg == "all":
                wager = user_data.get('balance', 0)
            else:
                try:
                    wager = float(arg)
                except ValueError:
                    pass

        if user_data.get('balance', 0) < wager:
            await update.message.reply_text(f"❌ Minimum bet is ${wager:.2f}. Your balance: ${user_data['balance']:.2f}")
            return

        await self._setup_predict_interface(update, context, wager, "dice")

    async def predict_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Unified command for prediction games"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)

        if user_data.get('balance', 0) <= 0:
            await update.message.reply_text("❌ Your balance is $0.00. Please deposit to play!")
            return

        wager = 1.0
        if context.args:
            arg = context.args[0].lower().replace('$', '').replace(',', '')
            if arg == "all":
                wager = user_data.get('balance', 0)
            else:
                try:
                    wager = float(arg)
                except ValueError:
                    pass

        if user_data.get('balance', 0) < wager:
            await update.message.reply_text(f"❌ Minimum bet is ${wager:.2f}. Your balance: ${user_data['balance']:.2f}")
            return

        await self._setup_predict_interface(update, context, wager, "dice")
        """Play dice predict game - predict what you'll roll with multiple choices"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id

        if len(context.args) < 2:
            await update.message.reply_text("Usage: `/predict amount #number1,#number2...`\nExample: `/predict 5 #1,#3,#6`", parse_mode="Markdown")
            return

        wager = 0.0
        if context.args[0].lower() == "all":
            wager = user_data['balance']
        else:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                wager = round(float(arg), 2)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount", parse_mode="HTML")
                return

        if wager < 1.0:
            await update.message.reply_text("❌ Minimum bet is $1.00", parse_mode="HTML")
            return

        if wager > user_data['balance']:
            await update.message.reply_text(f"❌ Balance: <b>${user_data['balance']:,.2f}</b>", parse_mode="HTML")
            return

        # Parse predictions
        pred_arg = context.args[1]
        raw_predictions = [p.strip() for p in pred_arg.split(',')]
        predictions = set()

        for p in raw_predictions:
            if not p.startswith('#'):
                await update.message.reply_text(f"❌ Prediction {p} must start with #", parse_mode="HTML")
                return
            try:
                num = int(p[1:])
                if 1 <= num <= 6:
                    predictions.add(num)
                else:
                    await update.message.reply_text(f"❌ Number {p} must be between 1 and 6", parse_mode="HTML")
                    return
            except ValueError:
                await update.message.reply_text(f"❌ Invalid prediction: {p}", parse_mode="HTML")
                return

        if not predictions:
            await update.message.reply_text("❌ No valid predictions provided", parse_mode="HTML")
            return

        if len(predictions) > 5:
            await update.message.reply_text("❌ You can't predict all 6 numbers (or 5 for logic sanity)", parse_mode="HTML")
            return

        # Multiplier logic: 6 / number of choices
        multiplier = round(6.0 / len(predictions), 2)

        # Deduct wager
        self.db.update_user(user_id, {'balance': user_data['balance'] - wager})

        # Send the dice
        dice_message = await update.message.reply_dice(emoji="🎲")
        actual_roll = dice_message.dice.value

        await asyncio.sleep(4)

        if actual_roll in predictions:
            payout = wager * multiplier
            profit = payout - wager
            new_balance = user_data['balance'] + payout # User balance was already deducted

            self.db.update_user(user_id, {
                'balance': new_balance,
                'total_wagered': user_data['total_wagered'] + wager,
                'wagered_since_last_withdrawal': user_data.get('wagered_since_last_withdrawal', 0) + wager,
                'games_played': user_data['games_played'] + 1,
                'games_won': user_data['games_won'] + 1
            })
            self.db.update_house_balance(-profit)

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

            sent_msg = await update.message.reply_text(
                win_text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id
        else:
            self.db.update_user(user_id, {
                'total_wagered': user_data['total_wagered'] + wager,
                'wagered_since_last_withdrawal': user_data.get('wagered_since_last_withdrawal', 0) + wager,
                'games_played': user_data['games_played'] + 1
            })
            self.db.update_house_balance(wager)

            loss_text = (
                f"🏆 <b>Game over!</b>\n\n"
                f"<b>Bot</b> won <b>${wager * 1.95:,.2f}</b>!"
            )

            # Replay buttons
            kb = [[
                InlineKeyboardButton("🔄 Play Again", callback_data=f"setup_mode_predict_{wager:.2f}_{game_mode}"),
                InlineKeyboardButton("🔄 Double", callback_data=f"setup_mode_predict_{wager*2:.2f}_{game_mode}")
            ]]

            sent_msg = await update.message.reply_text(
                loss_text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
            self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

        self.db.record_game({
            'type': 'dice_predict',
            'player_id': user_id,
            'wager': wager,
            'predictions': list(predictions),
            'actual_roll': actual_roll,
            'result': 'win' if actual_roll in predictions else 'loss',
            'profit': (wager * multiplier - wager) if actual_roll in predictions else -wager,
            'payout': (wager * multiplier) if actual_roll in predictions else 0
        })

    async def coinflip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play coinflip game setup"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Save command message ID for cleanup
        if update.message:
            context.user_data['last_dice_cmd_id'] = update.message.message_id

        amount = 1.0
        if context.args:
            try:
                arg = context.args[0].lower().replace('$', '').replace(',', '')
                if arg == 'all':
                    user_id = update.effective_user.id
                    user_data = self.db.get_user(user_id)
                    amount = user_data['balance']
                else:
                    amount = float(arg)
            except ValueError:
                pass
        await self._show_game_prediction_menu(update, context, amount, "coinflip")

    async def roulette_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Play roulette game"""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: `/roulette <amount|all>` or `/roulette <amount> #<number>`", parse_mode="Markdown")
            return

        wager = 0.0
        if context.args[0].lower() == "all":
            wager = user_data['balance']
        else:
            try:
                wager = round(float(context.args[0]), 2)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount")
                return

        if wager <= 0.01:
            await update.message.reply_text("❌ Min: $0.01")
            return

        if wager > user_data['balance']:
            await update.message.reply_text(f"❌ Balance: ${user_data['balance']:.2f}")
            return

        if len(context.args) > 1 and context.args[1].startswith('#'):
            try:
                number_str = context.args[1][1:]
                if number_str == "00":
                    specific_num = 37
                else:
                    specific_num = int(number_str)
                    if specific_num < 0 or specific_num > 36:
                        await update.message.reply_text("❌ Number must be 0-36 or 00")
                        return

                await self.roulette_play_direct(update, context, wager, f"num_{specific_num}")
                return
            except ValueError:
                await update.message.reply_text("❌ Invalid number format. Use #0, #1, #2, ... #36, or #00")
                return

        keyboard = [
            [InlineKeyboardButton("Red (2x)", callback_data=f"roulette_{wager:.2f}_red"),
             InlineKeyboardButton("Black (2x)", callback_data=f"roulette_{wager:.2f}_black")],
            [InlineKeyboardButton("Green (14x)", callback_data=f"roulette_{wager:.2f}_green")],
            [InlineKeyboardButton("Odd (2x)", callback_data=f"roulette_{wager:.2f}_odd"),
             InlineKeyboardButton("Even (2x)", callback_data=f"roulette_{wager:.2f}_even")],
            [InlineKeyboardButton("Low (2x)", callback_data=f"roulette_{wager:.2f}_low"),
             InlineKeyboardButton("High (2x)", callback_data=f"roulette_{wager:.2f}_high")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_msg = await update.message.reply_text(
            f"🎰 **Roulette** - Wager: ${wager:.2f}\n\n"
            f"**Choose your bet:**\n"
            f"• Red/Black: 2x payout\n"
            f"• Odd/Even: 2x payout\n"
            f"• Green (0/00): 14x payout\n"
            f"• Low (1-18)/High (19-36): 2x payout\n\n"
            f"*Tip: Bet on a specific number with `/roulette <amount> #<number>` for 36x payout!*",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def blackjack_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start a new blackjack game"""
        user_id = update.effective_user.id
        if user_id in self.blackjack_sessions:
            await update.message.reply_text("❌ You already have an active game! Use buttons to play.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /blackjack [amount]")
            return

        try:
            bet = float(context.args[0])
            if bet <= 0: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid bet amount.")
            return

        user_data = self.db.get_user(user_id)
        if bet > user_data['balance']:
            await update.message.reply_text(f"❌ Insufficient balance! (${user_data['balance']:.2f})")
            return

        # Deduct bet
        self.db.update_user(user_id, {'balance': user_data['balance'] - bet})
        self.db.add_transaction(user_id, "blackjack_bet", -bet, f"Blackjack bet: ${bet:.2f}")

        game = BlackjackGame(bet)
        game.start_game()
        self.blackjack_sessions[user_id] = game
        
        # Check if game ended immediately (BJ)
        state = game.get_game_state()
        if state['game_over']:
            payout = state['total_payout']
            outcome = "win" if payout > 0 else "draw" if payout == 0 else "loss"
            # initial bet was deducted, so profit is payout
            self._update_user_stats(user_id, bet, payout, outcome)
            if payout > 0:
                user_data = self.db.get_user(user_id)
                user_data['balance'] += (bet + payout)
                self.db.update_user(user_id, user_data)
                self.db.update_house_balance(-payout)
            elif payout == 0:
                user_data = self.db.get_user(user_id)
                user_data['balance'] += bet
                self.db.update_user(user_id, user_data)
            else:
                self.db.update_house_balance(bet)
            
            del self.blackjack_sessions[user_id]

        await self._send_blackjack_msg(update, context, user_id)

    async def _send_blackjack_msg(self, update, context, user_id):
        """Start a Blackjack game"""
        if not update.effective_message:
            return

        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return

        # Ensure user is registered
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)

        # Check if user already has an active game
        if user_id in self.blackjack_sessions:
            await update.effective_message.reply_text("❌ You already have an active Blackjack game. Finish it first or use /stand to end it.")
            return

        # If no arguments, show help
        if not context.args:
            await update.effective_message.reply_text(
                "🃏 **Blackjack Rules**\n\n"
                "Get as close to 21 as possible without going over!\n\n"
                "**Card Values:**\n"
                "• 2-10: Face value\n"
                "• J, Q, K: 10 points\n"
                "• Ace: 1 or 11 points\n\n"
                "**Payouts:**\n"
                "• Blackjack (Ace + 10): 3:2 (1.5x)\n"
                "• Regular Win: 1:1\n"
                "• Push (tie): Bet returned\n\n"
                "**Actions:**\n"
                "• Hit: Take another card\n"
                "• Stand: Keep current hand\n"
                "• Double: Double bet, get 1 card\n"
                "• Split: Split pairs into 2 hands\n"
                "• Surrender: Forfeit and lose half bet\n\n"
                "**Usage:** `/blackjack <amount|all>`",
                parse_mode="Markdown"
            )
            return

        # Parse wager
        wager_str = context.args[0].lower()
        wager = 0.0

        if wager_str == "all":
            wager = user_data['balance']
        else:
            try:
                # Clean input and parse
                wager_str = "".join(c for c in wager_str if c.isdigit() or c == '.')
                if not wager_str:
                    raise ValueError
                wager = round(float(wager_str), 2)
            except ValueError:
                await update.effective_message.reply_text("❌ Invalid amount. Usage: /blackjack <amount>")
                return

        if wager < 0.01:
            await update.effective_message.reply_text("❌ Min: $0.01")
            return

        if wager > user_data['balance']:
            await update.effective_message.reply_text(f"❌ Balance: ${user_data['balance']:.2f}")
            return

        # Deduct wager instantly
        new_balance = user_data['balance'] - wager
        self.db.update_user(user_id, {"balance": new_balance})
        self.db.add_transaction(user_id, "blackjack_bet", -wager, f"Blackjack Bet: {wager}")
        user_data['balance'] = new_balance

        # Start game
        try:
            from blackjack import BlackjackGame
            game = BlackjackGame(bet_amount=wager)
            start_msg = game.start_game()
            self.blackjack_sessions[user_id] = game

            # Show game state
            await self._display_blackjack_state(update, context, user_id)
        except Exception as e:
            logger.error(f"Error starting blackjack: {e}")
            # Refund
            u_data = self.db.get_user(user_id)
            if u_data:
                u_data['balance'] += wager
                self.db.update_user(user_id, u_data)
            # Silent fallback or minimal notification instead of error message
            pass

    async def _display_blackjack_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
        """Display the current Blackjack game state with action buttons"""
        if user_id not in self.blackjack_sessions:
            logger.warning(f"BJ display: no session for {user_id}")
            return

        game = self.blackjack_sessions[user_id]
        state = game.get_game_state()
        user_data = self.db.get_user(user_id)

        logger.info(f"BJ display: game_over={state['game_over']}, hands={len(state['player_hands'])}")

        # Build message text
        message = "🃏 <b>Blackjack</b>\n\n"

        # Dealer section
        message += f"Dealer: <b>{state['dealer']['value']}</b>\n"
        if state['game_over']:
            dealer_cards_str = ""
            for card in game.dealer_hand.cards:
                dealer_cards_str += f"<b>{card.rank}</b>{CARD_FACES.get(card.suit, '')}  "
            message += f"{dealer_cards_str.strip()}\n\n"
        else:
            first_card = game.dealer_hand.cards[0]
            message += f"<b>{first_card.rank}</b>{CARD_FACES.get(first_card.suit, '')}  [??]\n\n"

        # Player Hands section
        num_player_hands = len(state['player_hands'])
        for i, h in enumerate(state['player_hands']):
            hand_label = "Your cards" if num_player_hands == 1 else f"Hand {i+1}"
            current_marker = " ⬅️" if (h['is_current_turn'] and num_player_hands > 1) else ""
            message += f"{hand_label}: <b>{h['value']}</b>{current_marker}\n"

            player_cards_formatted = ""
            for card in game.player_hands[i]['hand'].cards:
                player_cards_formatted += f"<b>{card.rank}</b>{CARD_FACES.get(card.suit, '')}  "

            message += f"{player_cards_formatted.strip()}\n\n"

        # Game over - show results
        result_msg = ""
        total_payout = 0
        total_bet = sum(h['bet'] for h in state['player_hands'])

        if state['game_over']:
            total_payout = state['total_payout']
            player_hand = state['player_hands'][0]
            dealer_hand = state['dealer']
            username = user_data.get('username') or update.effective_user.first_name

            if player_hand['status'] == 'Blackjack':
                result_msg = "<b>BLACKJACK!</b>"
            elif player_hand['status'] == 'Bust':
                result_msg = "<b>Busted. You lost!</b>"
            elif dealer_hand['final_status'] == 'Bust':
                result_msg = "<b>Dealer bust. You won!</b>"
            elif total_payout > 0:
                result_msg = f"<b>Congratulations {username}, you won!</b>"
            elif total_payout < 0:
                result_msg = "<b>Dealer won!</b>"
            else:
                result_msg = "<b>Push!</b>"

            # Update user balance
            try:
                user_data = self.db.get_user(user_id)
                actual_return = total_bet + total_payout

                update_fields = {}
                update_fields['total_wagered'] = user_data.get('total_wagered', 0) + total_bet
                update_fields['wagered_since_last_withdrawal'] = user_data.get('wagered_since_last_withdrawal', 0) + total_bet
                update_fields['total_pnl'] = user_data.get('total_pnl', 0) + total_payout
                update_fields['games_played'] = user_data.get('games_played', 0) + 1

                if actual_return > 0:
                    update_fields['balance'] = user_data.get('balance', 0) + actual_return
                    self.db.add_transaction(user_id, "blackjack_result", actual_return, f"Blackjack Result (Return: {actual_return:.2f})")

                if total_payout > 0:
                    update_fields['games_won'] = user_data.get('games_won', 0) + 1
                    update_fields['total_won'] = user_data.get('total_won', 0) + actual_return

                self.db.update_house_balance(-total_payout)
                self.db.update_user(user_id, update_fields)

                self.db.record_game({
                    'type': 'blackjack',
                    'user_id': user_id,
                    'player_id': user_id,
                    'username': user_data.get('username', 'Unknown'),
                    'wager': total_bet,
                    'payout': actual_return,
                    'result': ('win' if total_payout > 0 else ('loss' if total_payout < 0 else 'push')),
                    'winner': user_id if total_payout > 0 else None
                })

                # Re-read for accurate balance display
                user_data = self.db.get_user(user_id)
                logger.info(f"BJ: game resolved, payout={total_payout}, return={actual_return}")
            except Exception as e:
                logger.error(f"BJ balance/stats update error: {e}", exc_info=True)
                # Still show the result even if stats fail
                user_data = self.db.get_user(user_id)

        message += f"Bet: <b>${total_bet:.2f}</b>\n"
        message += f"Balance: <b>${user_data['balance']:.2f}</b>\n"

        if result_msg:
            message += f"\n{result_msg}\n"

        # Action Buttons
        keyboard = []
        if not state['game_over']:
            # Normal Actions
            current_hand = state['player_hands'][state['current_hand_index']]
            actions = current_hand.get('actions', {})

            keyboard.append([
                InlineKeyboardButton("Hit", callback_data=f"bj_hit_{user_id}"),
                InlineKeyboardButton("Stand", callback_data=f"bj_stand_{user_id}")
            ])

            row2 = []
            if actions.get('can_double'):
                row2.append(InlineKeyboardButton("Double", callback_data=f"bj_double_{user_id}"))
            if actions.get('can_split'):
                row2.append(InlineKeyboardButton("Split", callback_data=f"bj_split_{user_id}"))
            if row2:
                # If both double and split are available, put them on separate rows to keep them wide
                for btn in row2:
                    keyboard.append([btn])

            row3 = []
            if state['is_insurance_available']:
                row3.append(InlineKeyboardButton("Insurance", callback_data=f"bj_insurance_{user_id}"))
            if row3:
                keyboard.append(row3)
        else:
            # Play Again buttons
            total_bet = sum(h['bet'] for h in state['player_hands'])
            original_bet = getattr(game, 'initial_bet', total_bet)
            keyboard.append([InlineKeyboardButton("✅ Start Game", callback_data=f"bj_bot_{original_bet:.2f}")])
            keyboard.append([
                InlineKeyboardButton("Half Bet", callback_data=f"bj_bet_change_{user_id}_{max(1.0, original_bet/2):.2f}"),
                InlineKeyboardButton(f"Bet: ${original_bet:.2f}", callback_data="dummy"),
                InlineKeyboardButton("Double Bet", callback_data=f"bj_bet_change_{user_id}_{original_bet*2:.2f}")
            ])
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])

        # Build reply markup
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        # Send or edit message
        logger.info(f"BJ display: sending message, game_over={state['game_over']}, msg_len={len(message)}")
        try:
            if update.callback_query:
                try:
                    await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")
                    logger.info("BJ display: edit_message_text succeeded")
                except Exception as edit_err:
                    logger.error(f"BJ edit failed (HTML): {edit_err}")
                    try:
                        # Try without HTML parse mode
                        import re
                        plain_message = re.sub(r'<[^>]+>', '', message)
                        await update.callback_query.edit_message_text(plain_message, reply_markup=reply_markup)
                        logger.info("BJ display: edit plain text succeeded")
                    except Exception as plain_err:
                        logger.error(f"BJ edit failed (plain): {plain_err}")
                        try:
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=message, reply_markup=reply_markup, parse_mode="HTML"
                            )
                        except Exception:
                            pass
            else:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as send_err:
            logger.error(f"BJ message send completely failed: {send_err}", exc_info=True)

        # Clean up session ONLY AFTER message is sent
        if state['game_over']:
            if user_id in self.blackjack_sessions:
                del self.blackjack_sessions[user_id]

    async def tip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send money to another player."""
        if await self.check_balance_and_delete(update, context) or await self.check_active_game_and_delete(update, context):
            return
        user_data = self.ensure_user_registered(update)
        user_id = update.effective_user.id

        # Check if this is a reply to another user
        reply_to_message = update.message.reply_to_message
        recipient_data = None
        recipient_display_name = None

        if reply_to_message and not reply_to_message.from_user.is_bot:
            recipient_id = reply_to_message.from_user.id
            # Prefer username, fallback to first_name
            recipient_display_name = reply_to_message.from_user.username or reply_to_message.from_user.first_name
            recipient_data = self.db.get_user(recipient_id)

            # Update username in DB if it changed
            if reply_to_message.from_user.username and recipient_data.get('username') != reply_to_message.from_user.username:
                self.db.update_user(recipient_id, {'username': reply_to_message.from_user.username})
                recipient_data['username'] = reply_to_message.from_user.username

            if not context.args:
                await update.message.reply_text("Usage: Reply to a user with `/tip <amount>`", parse_mode="Markdown")
                return
            try:
                amount = round(float(context.args[0]), 2)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount")
                return
        else:
            if len(context.args) < 2:
                await update.message.reply_text("Usage: `/tip <amount> @user` or reply to a message with `/tip <amount>`", parse_mode="Markdown")
                return

            try:
                amount = round(float(context.args[0]), 2)
            except ValueError:
                await update.message.reply_text("❌ Invalid amount")
                return

            recipient_username = context.args[1].lstrip('@')
            recipient_data = next((u for u in self.db.data['users'].values() if u.get('username') == recipient_username), None)
            recipient_display_name = recipient_username

        if amount <= 0.01:
            await update.message.reply_text("❌ Min: $0.01")
            return

        if amount > user_data['balance']:
            await update.message.reply_text(f"❌ Balance: ${user_data['balance']:.2f}")
            return

        if not recipient_data:
            await update.message.reply_text(f"❌ Could not find user.")
            return

        if recipient_data['user_id'] == user_id:
            await update.message.reply_text("❌ You cannot tip yourself.")
            return

        # Use mention_html for proper link to user profile
        mention = f'<a href="tg://user?id={recipient_data["user_id"]}">{recipient_display_name}</a>'

        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data=f"tip_confirm_{recipient_data['user_id']}_{amount:.2f}"),
             InlineKeyboardButton("❌ Cancel", callback_data="tip_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"You want to tip {mention} with <b>${amount:,.2f}</b>. Is that correct?",
            reply_markup=reply_markup,
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )

    async def deposit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Redirect to balance menu."""
        await self.balance_command(update, context)

    async def withdraw_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle withdraw command. In groups, redirect to PM. In PM, show withdraw instructions."""
        user = update.effective_user
        chat = update.effective_chat
        user_data = self.db.get_user(user.id)

        # Check for arguments: /withdraw <amount> <address>
        if context.args:
            try:
                amount = float(context.args[0])
                if amount > user_data['balance']:
                    if update.message:
                        await update.message.reply_text(
                            f"Insufficient balance\n Current balance: ${user_data['balance']:,.2f}",
                            parse_mode="HTML"
                        )
                    return
            except ValueError:
                pass

        if chat.type in ["group", "supergroup"]:
            # Same behavior as deposit: notify in group and send PM
            try:
                if update.message:
                    await update.message.reply_text(
                        f"Hey {self.get_mention(user.id, user.first_name)}, I've sent you a private message with instructions on how to withdraw!",
                        parse_mode="HTML"
                    )

                # Send private message
                await self.app.bot.send_message(
                    chat_id=user.id,
                    text="To withdraw, please use the /withdraw command here in our private chat.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error in group withdraw command: {e}")
                if update.message:
                    await update.message.reply_text("❌ Please start a private chat with me first so I can send you withdrawal instructions.")
        else:
            # In private chat, redirect to balance menu or show withdraw instructions
            await self.balance_command(update, context)

    async def pending_deposits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View all pending deposits (Admin only)."""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin only.")
            return

        pending = self.db.data.get('pending_deposits', [])
        pending = [d for d in pending if d.get('status') == 'pending']

        if not pending:
            await update.message.reply_text("✅ No pending deposits.")
            return

        text = "📥 **Pending Deposits**\n\n"
        for i, dep in enumerate(pending[-20:], 1):
            text += f"{i}. @{dep['username']} (ID: {dep['user_id']})\n   Amount: ${dep['amount']:.2f}\n   TX: `{dep['tx_id']}`\n\n"

        text += "Use `/approvedeposit <user_id> <amount>` to approve."
        await update.message.reply_text(text, parse_mode="Markdown")

    async def approve_deposit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Approve a deposit and credit user balance (Admin only)."""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin only.")
            return

        if len(context.args) < 2:
            await update.message.reply_text("Usage: `/approvedeposit <user_id> <amount>`", parse_mode="Markdown")
            return

        try:
            target_user_id = int(context.args[0])
            amount = round(float(context.args[1]), 2)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID or amount.")
            return

        user_data = self.db.get_user(target_user_id)
        user_data['balance'] += amount
        self.db.update_user(target_user_id, user_data)
        self.db.add_transaction(target_user_id, "deposit", amount, "LTC Deposit (Approved)")

        # Mark deposit as approved
        pending = self.db.data.get('pending_deposits', [])
        for dep in pending:
            if dep['user_id'] == target_user_id and dep.get('status') == 'pending':
                dep['status'] = 'approved'
                break

        await update.message.reply_text(
            f"✅ **Deposit Approved**\n\nUser ID: {target_user_id}\nAmount: ${amount:.2f}\nNew Balance: ${user_data['balance']:.2f}",
            parse_mode="Markdown"
        )

        # Notify user
        try:
            await self.app.bot.send_message(
                chat_id=target_user_id,
                text=f"✅ **Deposit Approved!**\n\nAmount: **${amount:.2f}** has been credited.\n\nNew Balance: ${user_data['balance']:.2f}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify user {target_user_id}: {e}")

    async def pending_withdraws_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View all pending withdrawals (Admin only)."""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin only.")
            return

        pending = self.db.data.get('pending_withdrawals', [])
        pending = [w for w in pending if w.get('status') == 'pending']

        if not pending:
            await update.message.reply_text("✅ No pending withdrawals.")
            return

        text = "📤 **Pending Withdrawals**\n\n"
        for i, wit in enumerate(pending[-20:], 1):
            text += f"{i}. @{wit['username']} (ID: {wit['user_id']})\n   Amount: ${wit['amount']:.2f}\n   LTC: `{wit['ltc_address']}`\n\n"

        text += "Use `/processwithdraw <user_id>` after sending LTC."
        await update.message.reply_text(text, parse_mode="Markdown")

    async def process_withdraw_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Mark a withdrawal as processed (Admin only)."""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin only.")
            return

        if len(context.args) < 1:
            await update.message.reply_text("Usage: `/processwithdraw <user_id>`", parse_mode="Markdown")
            return

        try:
            target_user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return

        # Find and mark withdrawal as processed
        pending = self.db.data.get('pending_withdrawals', [])
        processed = None
        for wit in pending:
            if wit['user_id'] == target_user_id and wit.get('status') == 'pending':
                wit['status'] = 'processed'
                processed = wit
                break

        if not processed:
            await update.message.reply_text("❌ No pending withdrawal found for this user.")
            return

        self.db.add_transaction(target_user_id, "withdrawal", -processed['amount'], f"LTC Withdrawal to {processed['ltc_address'][:20]}...")

        await update.message.reply_text(
            f"✅ **Withdrawal Processed**\n\nUser ID: {target_user_id}\nAmount: ${processed['amount']:.2f}\nSent to: `{processed['ltc_address']}`",
            parse_mode="Markdown"
        )

        # Notify user
        try:
            await self.app.bot.send_message(
                chat_id=target_user_id,
                text=f"✅ **Withdrawal Sent!**\n\n**${processed['amount']:.2f}** has been sent to your LTC address.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify user {target_user_id}: {e}")

    async def backup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Sends the database file as a backup (Admin only)."""
        if not self.is_admin(update.effective_user.id):
             await update.message.reply_text("❌ This command is for administrators only.")
             return

        if os.path.exists(self.db.file_path):
            await update.message.reply_document(
                document=open(self.db.file_path, 'rb'),
                filename=self.db.file_path,
                caption="Antaria Casino Database Backup"
            )
        else:
            await update.message.reply_text("❌ Database file not found.")

    async def save_sticker_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save a sticker file_id for roulette numbers"""
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                f"Usage: `/savesticker <number> <file_id>`\nNumbers: 00, 0-36",
                parse_mode="Markdown"
            )
            return

        number = context.args[0]
        file_id = context.args[1]

        # Validate number is valid roulette number
        valid_numbers = ['00', '0'] + [str(i) for i in range(1, 37)]
        if number not in valid_numbers:
            await update.message.reply_text(f"❌ Invalid number. Must be: 00, 0, 1, 2, 3... 36")
            return

        # Save to database
        if 'roulette' not in self.stickers:
            self.stickers['roulette'] = {}

        self.stickers['roulette'][number] = file_id
        self.db.data['stickers'] = self.stickers

        await update.message.reply_text(f"✅ Sticker saved for roulette number '{number}'!")

    async def list_stickers_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all configured stickers"""
        sticker_text = "🎨 **Roulette Stickers**\n\n"

        roulette_stickers = self.stickers.get('roulette', {})

        # Count how many are set
        all_numbers = ['00', '0'] + [str(i) for i in range(1, 37)]
        saved_count = sum(1 for num in all_numbers if num in roulette_stickers and roulette_stickers[num])

        sticker_text += f"Saved: {saved_count}/38"
        await update.message.reply_text(sticker_text, parse_mode="Markdown")

    async def save_roulette_stickers_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save all 38 roulette stickers to the database"""
        # Initialize roulette stickers if not present
        if 'roulette' not in self.stickers:
            self.stickers['roulette'] = {}

        # Save all 38 roulette sticker IDs
        self.stickers['roulette'] = {
            "00": "CAACAgQAAxkBAAEPnjFo-TLLYpgTZExC4IIOG6PIXwsviAAC1BgAAkmhgFG_0u82E59m3DYE",
            "0": "CAACAgQAAxkBAAEPnjNo-TMFaqDdWCkRDNlus4jcuamAAwACOh0AAtQAAYBRlMLfm2ulRSM2BA",
            "1": "CAACAgQAAxkBAAEPnjRo-TMFH5o5R9ztNtTFBJmQVK_t3wACqBYAAvTrgVE4WCoxbBzVCDYE",
            "2": "CAACAgQAAxkBAAEPnjdo-TMvGdoX-f6IAuR7kpYO-hh9fwAC1RYAAob0eVF1zbcG00UjMzYE",
            "3": "CAACAgQAAxkBAAEPnjho-TMwui0CFuGEK5iwS7xMRDiPfgACSRgAAs74gVEyHQtTsRykGjYE",
            "4": "CAACAgQAAxkBAAEPnj1o-TNGYNdmhy4n5Uyp3pzWmukTgAACfBgAAg3IgFGEjdLKewti5zYE",
            "5": "CAACAgQAAxkBAAEPnj5o-TNHTKLFF2NpdxfLhHnsnFGTXgACyhYAAltygVECKXn73kUyCjYE",
            "6": "CAACAgQAAxkBAAEPnkFo-TNPGqrsJJwZNwUe_I6k4W86cwACyxoAAgutgVGyiCe4lNK2-DYE",
            "7": "CAACAgQAAxkBAAEPnkJo-TNPksXPcYnpXDWYQC68AAGlqzQAAtUYAAKU_IFRJTHChQd2yfw2BA",
            "8": "CAACAgQAAxkBAAEPnkdo-TQOIBN5WtoKKnvcthXdcy0LLgACgBQAAmlWgVFImh6M5RcAAdI2BA",
            "9": "CAACAgQAAxkBAAEPnkho-TQO92px4jOuq80nT2uWjURzSAAC4BcAAvPKeVFBx-TZycAWDzYE",
            "10": "CAACAgQAAxkBAAEPnkto-TZ8-6moW-biByRYl8J2QEPnTwAC8hgAArnAgFGen1zgHwABLPc2BA",
            "11": "CAACAgQAAxkBAAEPnkxo-TZ8ncZZ7FYYyFMJHXRv2rB0TwAC2RMAAmzdgVEao0YAAdIy41g2BA",
            "12": "CAACAgQAAxkBAAEPnk1o-TZ9z6xAxxIeccUPXoQQ9VaikQACVRgAAovngVFUjR-qYgq8LDYE",
            "13": "CAACAgQAAxkBAAEPnlFo-TbUs79Rm549dK3JK2L3P83q-QACTR0AAmc0gFHXnJ509OdiOjYE",
            "14": "CAACAgQAAxkBAAEPnlJo-TbUCpjrhSxP-x84jkBerEYB8AACQxkAAqXDeVEQ5uCH3dK9OjYE",
            "15": "CAACAgQAAxkBAAEPnlNo-TbUZokc7ubz-neSYtK9kxQ0DAACrRYAAlBWgVH9BqGde-NivjYE",
            "16": "CAACAgQAAxkBAAEPnlRo-TbUiOcqxKI6HNExFR8yT3qyvAACrxsAAkcfeVG9im0F0tuZPzYE",
            "17": "CAACAgQAAxkBAAEPnllo-TdIFRtpAW3PeDbxD2QxTgjk2QACLhgAAiuXgVHaPo1woXZEYTYE",
            "18": "CAACAgQAAxkBAAEPnlpo-TdI9Gdz2Nv3icxluy8jC3keBwACYxkAAnx7eFGsZP2AXXBKwzYE",
            "19": "CAACAgQAAxkBAAEPnlto-TdIUktLbTIhkihQz3ymy4lUIwACKRkAArDwgFH0iKqIPPiHYDYE",
            "20": "CAACAgQAAxkBAAEPnlxo-TdJVrOSPiCRuD8Jc0XGvF3B8AACcxoAAr7OeFGSuSoHyKxf5TYE",
            "21": "CAACAgQAAxkBAAEPnl1o-TdJ1jlMSjGQPO0zkaS_rOv5JQACxhcAAv1dgFF3khtGYFneYzYE",
            "22": "CAACAgQAAxkBAAEPnmNo-Te2OhfAwfprG1HfmY-UNtkEAgADGQACE8KAUSJTKzPQQQ9INgQ",
            "23": "CAACAgQAAxkBAAEPnmRo-Te3rAHmt7_CRgFp55KSNVYdKwACTBgAAundgVF6unXyM34ZYzYE",
            "24": "CAACAgQAAxkBAAEPnmVo-Te3LcVARwsUx3Akt75bruvNXAAC4RoAAnkvgFHRL4l2927wnDYE",
            "25": "CAACAgQAAxkBAAEPnmZo-Te3lY0O1JxF8tTLYJJhN1QcnAAC5hcAAiPegFFsMkNzpqfR0zYE",
            "26": "CAACAgQAAxkBAAEPnmto-TgIsR6UdO8EukNYajboFnX3mgACzSAAAn15gVG-oQ4oaJLYrzYE",
            "27": "CAACAgQAAxkBAAEPnmxo-TgIVFkyEf19Je-9awnfcm0HNAACoBcAAjK0gVFqoRMWJ0V2AjYE",
            "28": "CAACAgQAAxkBAAEPnm1o-TgIEaTKLI1hP_FD5NoPNMoRrQAC8xUAAjTtgVFbDjOI7hjkyDYE",
            "29": "CAACAgQAAxkBAAEPnm5o-TgIrfmuYVnfQps2DUcaDPJtYAACehcAAgL2eFFyvPJETxqlljYE",
            "30": "CAACAgQAAxkBAAEPnm9o-TgIumJ40cFAJ7xQVVJu8yioGQACrBUAAqMsgVEiKujpQgVfJDYE",
            "31": "CAACAgQAAxkBAAEPnndo-ThreZX7kJJpPO5idNcOeIWZpQACDhsAArW6gFENcv6I97q9xDYE",
            "32": "CAACAgQAAxkBAAEPni9o-Ssij-qcC2-pLlmtFrUQr5AUgQACWxcAAsmneVGFqOYh9w81_TYE",
            "33": "CAACAgQAAxkBAAEPnnto-Thsmi6zNRuaeXnBFpXJ-w2JnQACjBkAAo3JeFEYXOtgIzFLjTYE",
            "34": "CAACAgQAAxkBAAEPnnlo-ThrHvyKnt3O8UiLblKzGgWqzQACWBYAAvn3gVElI6JyUvoRYzYE",
            "35": "CAACAgQAAxkBAAEPnn9o-Tij1sCB1_UVenRU6QvBnfFKagACkhYAAsKTgFHHcm9rj3PDyDYE",
            "36": "CAACAgQAAxkBAAEPnoBo-Tik1zRaZMCVCaOi9J1FtVvEiAACrBcAAtbQgVFt8Uw1gyn4MDYE"
        }

        # Save to database
        self.db.data['stickers'] = self.stickers

        await update.message.reply_text("✅ All 38 roulette stickers have been saved to the database!")

    async def sticker_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming stickers silently"""
        pass

    # --- ADMIN COMMANDS ---

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check if user is an admin"""
        user_id = update.effective_user.id

        if self.is_admin(user_id):
            is_env_admin = user_id in self.env_admin_ids
            admin_type = "Permanent Admin" if is_env_admin else "Dynamic Admin"

            admin_text = f"""✅ You are a {admin_type}!

Admin Commands:
• /givebal [@username or ID] [amount] - Give money to a user
• /p [amount] - Instantly add balance to yourself
• /setbal [@username or ID] [amount] - Set a user's balance
• /allusers - View all registered users
• /userinfo [@username or ID] - View detailed user info
• /backup - Download database backup
• /addadmin [user_id] - Make someone an admin
• /removeadmin [user_id] - Remove admin access
• /listadmins - List all admins

Examples:
/givebal @john 100
/setbal 123456789 500
/addadmin 987654321
/removeadmin 987654321"""
            await update.message.reply_text(admin_text)
        else:
            await update.message.reply_text("❌ You are not an admin.")

    async def givebal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Give balance to a user (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is for administrators only.")
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /givebal [@username or user_id] [amount]\nExample: /givebal @john 100")
            return

        try:
            amount = float(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return

        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return

        target_user = self.find_user_by_username_or_id(context.args[0])
        if not target_user:
            await update.message.reply_text(f"❌ User '{context.args[0]}' not found.")
            return

        target_user_id = target_user['user_id']
        target_user['balance'] += amount
        self.db.update_user(target_user_id, target_user)
        self.db.add_transaction(target_user_id, "admin_give", amount, f"Admin grant by {update.effective_user.id}")

        username_display = f"@{target_user.get('username', target_user_id)}"
        await update.message.reply_text(
            f"✅ Gave ${amount:.2f} to {username_display}\n"
            f"New balance: ${target_user['balance']:.2f}"
        )

    async def setbal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set a user's balance (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is for administrators only.")
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /setbal [@username or user_id] [amount]\nExample: /setbal @john 500")
            return

        try:
            amount = float(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return

        if amount < 0:
            await update.message.reply_text("❌ Amount cannot be negative.")
            return

        target_user = self.find_user_by_username_or_id(context.args[0])
        if not target_user:
            await update.message.reply_text(f"❌ User '{context.args[0]}' not found.")
            return

        target_user_id = target_user['user_id']
        old_balance = target_user['balance']
        target_user['balance'] = amount
        self.db.update_user(target_user_id, target_user)
        self.db.add_transaction(target_user_id, "admin_set", amount - old_balance, f"Admin set balance by {update.effective_user.id}")

        username_display = f"@{target_user.get('username', target_user_id)}"
        await update.message.reply_text(
            f"✅ Set balance for {username_display}\n"
            f"Old balance: ${old_balance:.2f}\n"
            f"New balance: ${amount:.2f}"
        )

    async def p_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Instantly add balance to the calling user"""
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("Usage: /p [amount]\nExample: /p 100")
            return

        import math
        try:
            amount = float(context.args[0])
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("Invalid amount")

            # Limit the maximum amount that can be added via /p to prevent overflow
            # 1 Quadrillion (10^15) is a safe upper limit
            if amount > 1_000_000_000_000_000:
                await update.message.reply_text("❌ Amount too large.")
                return
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return

        user_data = self.db.get_user(user_id)
        new_balance = user_data['balance'] + amount

        if not math.isfinite(new_balance):
            await update.message.reply_text("❌ Resulting balance would be invalid.")
            return

        user_data['balance'] = new_balance
        self.db.update_user(user_id, user_data)
        self.db.add_transaction(user_id, "admin_p", amount, f"Self-grant /p by {user_id}")

        await update.message.reply_text(f"✅ Added ${amount:,.2f} to your balance.\nNew balance: ${user_data['balance']:,.2f}")

    async def endgames_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """End all active games and refund players"""
        # Removed admin restriction so anyone can end games

        count = 0
        refunded_amount = 0

        # 1. Refund Blackjack sessions
        for user_id, game in list(self.blackjack_sessions.items()):
            try:
                bet = getattr(game, 'initial_bet', 0)
                if bet > 0:
                    user_data = self.db.get_user(user_id)
                    user_data['balance'] += bet
                    self.db.update_user(user_id, user_data)
                    refunded_amount += bet
                del self.blackjack_sessions[user_id]
                count += 1
            except Exception as e:
                logger.error(f"Error refunding BJ user {user_id}: {e}")

        # 2. Refund PvP / Bot games in GlobalState
        with self.db.app.app_context():
            state = db.session.get(GlobalState, "pending_pvp")
            if state and state.value:
                pending_pvp = state.value
                for cid, challenge in list(pending_pvp.items()):
                    try:
                        wager = challenge.get('wager', 0)
                        if cid.startswith("v2_bot_"):
                            pid = challenge.get('player')
                            if pid and challenge.get('wager_deducted'):
                                user_data = self.db.get_user(pid)
                                user_data['balance'] += wager
                                self.db.update_user(pid, user_data)
                                refunded_amount += wager
                        elif cid.startswith("v2_pvp_"):
                            p1, p2 = challenge.get('challenger'), challenge.get('opponent')
                            if p1 and challenge.get('p1_deducted'):
                                user_data = self.db.get_user(p1)
                                user_data['balance'] += wager
                                self.db.update_user(p1, user_data)
                                refunded_amount += wager
                            if p2 and challenge.get('p2_deducted'):
                                user_data = self.db.get_user(p2)
                                user_data['balance'] += wager
                                self.db.update_user(p2, user_data)
                                refunded_amount += wager

                        count += 1
                    except Exception as e:
                        logger.error(f"Error refunding challenge {cid}: {e}")

                # Clear the table
                state.value = {}
                db.session.commit()
                # Also clear the in-memory copy
                self.pending_pvp = {}

        await update.message.reply_text(f"✅ Ended {count} games and refunded a total of ${refunded_amount:.2f}.")


    async def allusers_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View all registered users (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is for administrators only.")
            return

        users = self.db.data['users']

        if not users:
            await update.message.reply_text("No users registered yet.")
            return

        users_text = f"👥 **All Users ({len(users)})**\n\n"

        for user_id_str, user_data in list(users.items())[:50]:
            username = user_data.get('username', 'N/A')
            balance = user_data.get('balance', 0)
            users_text += f"ID: `{user_id_str}` | @{username} | ${balance:.2f}\n"

        if len(users) > 50:
            users_text += f"\n...and {len(users) - 50} more users"

        await update.message.reply_text(users_text, parse_mode="Markdown")

    async def userinfo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View detailed user information (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is for administrators only.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /userinfo [@username or user_id]\nExample: /userinfo @john")
            return

        target_user = self.find_user_by_username_or_id(context.args[0])
        if not target_user:
            await update.message.reply_text(f"❌ User '{context.args[0]}' not found.")
            return

        target_user_id = target_user['user_id']

        info_text = f"""
👤 **User Info: {target_user_id}**

Username: @{target_user.get('username', 'N/A')}
Balance: ${target_user.get('balance', 0):.2f}
Playthrough: ${target_user.get('playthrough_required', 0):.2f}

**Stats:**
Games Played: {target_user.get('games_played', 0)}
Games Won: {target_user.get('games_won', 0)}
Total Wagered: ${target_user.get('total_wagered', 0):.2f}
Total P&L: ${target_user.get('total_pnl', 0):.2f}
Best Win Streak: {target_user.get('best_win_streak', 0)}


"""

        await update.message.reply_text(info_text, parse_mode="Markdown")

    async def addadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add a new admin (Admin only - requires environment admin)"""
        user_id = update.effective_user.id

        # Only permanent admins (from environment) can add new admins
        if user_id not in self.env_admin_ids:
            await update.message.reply_text("❌ Only permanent admins can add new admins.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /addadmin [user_id]\nExample: /addadmin 123456789")
            return

        try:
            new_admin_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Please provide a numeric ID.")
            return

        # Check if already an admin
        if self.is_admin(new_admin_id):
            admin_type = "permanent" if new_admin_id in self.env_admin_ids else "dynamic"
            await update.message.reply_text(f"❌ User {new_admin_id} is already a {admin_type} admin.")
            return

        # Add to dynamic admins
        self.dynamic_admin_ids.add(new_admin_id)
        self.db.data['dynamic_admins'] = list(self.dynamic_admin_ids)

        await update.message.reply_text(f"✅ User {new_admin_id} has been added as an admin!")

        # Notify the new admin if they exist in the system
        try:
            await self.app.bot.send_message(
                chat_id=new_admin_id,
                text="🎉 You have been granted admin privileges! Use /admin to see available commands."
            )
        except Exception as e:
            logger.info(f"Could not notify new admin {new_admin_id}: {e}")

    async def removeadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remove an admin (Admin only - requires environment admin)"""
        user_id = update.effective_user.id

        # Only permanent admins (from environment) can remove admins
        if user_id not in self.env_admin_ids:
            await update.message.reply_text("❌ Only permanent admins can remove admins.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /removeadmin [user_id]\nExample: /removeadmin 123456789")
            return

        try:
            admin_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Please provide a numeric ID.")
            return

        # Prevent removing permanent admins
        if admin_id in self.env_admin_ids:
            await update.message.reply_text("❌ Cannot remove permanent admins from environment.")
            return

        # Check if they are a dynamic admin
        if admin_id not in self.dynamic_admin_ids:
            await update.message.reply_text(f"❌ User {admin_id} is not a dynamic admin.")
            return

        # Remove from dynamic admins
        self.dynamic_admin_ids.discard(admin_id)
        self.db.data['dynamic_admins'] = list(self.dynamic_admin_ids)

        await update.message.reply_text(f"✅ Removed admin privileges from user {admin_id}!")

        # Notify the user if possible
        try:
            await self.app.bot.send_message(
                chat_id=admin_id,
                text="Your admin privileges have been removed."
            )
        except Exception as e:
            logger.info(f"Could not notify removed admin {admin_id}: {e}")

    async def listadmins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all admins (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is for administrators only.")
            return

        admin_text = "👑 **Admin List**\n\n"

        if self.env_admin_ids:
            admin_text += "**Permanent Admins (from environment):**\n"
            for admin_id in sorted(self.env_admin_ids):
                user_data = self.db.data['users'].get(str(admin_id))
                username = user_data.get('username', 'N/A') if user_data else 'N/A'
                admin_text += f"• {admin_id} (@{username})\n"
            admin_text += "\n"

        if self.dynamic_admin_ids:
            admin_text += "**Dynamic Admins (added via commands):**\n"
            for admin_id in sorted(self.dynamic_admin_ids):
                user_data = self.db.data['users'].get(str(admin_id))
                username = user_data.get('username', 'N/A') if user_data else 'N/A'
                admin_text += f"• {admin_id} (@{username})\n"
        else:
            if not self.env_admin_ids:
                admin_text += "No admins configured."
            else:
                admin_text += "No dynamic admins added yet.\n"
                admin_text += "Use /addadmin to add more admins."

        await update.message.reply_text(admin_text, parse_mode="Markdown")

    async def send_sticker(self, chat_id: int, outcome: str, profit: float = 0):
        """Send a sticker based on game outcome"""
        try:
            sticker_key = None

            if outcome == "win":
                if profit >= 50:
                    sticker_key = "jackpot"
                elif profit >= 10:
                    sticker_key = "big_win"
                else:
                    sticker_key = "win"
            elif outcome == "loss":
                sticker_key = "loss"
            elif outcome == "draw":
                sticker_key = "draw"
            elif outcome == "bonus_claim":
                sticker_key = "bonus_claim"

            if sticker_key and self.stickers.get(sticker_key):
                await self.app.bot.send_sticker(
                    chat_id=chat_id,
                    sticker=self.stickers[sticker_key]
                )
        except Exception as e:
            logger.error(f"Error sending sticker: {e}")

    # --- GAME LOGIC ---

    def _update_user_stats(self, user_id: int, wager: float, profit: float, result: str):
        """Update user statistics after a game."""
        user_data = self.db.get_user(user_id)
        if not user_data:
            return

        update_fields = {
            'total_wagered': (user_data.get('total_wagered', 0) or 0) + wager,
            'total_pnl': (user_data.get('total_pnl', 0) or 0) + profit,
            'rakeback_balance': (user_data.get('rakeback_balance', 0) or 0) + (wager * 0.02),
            'games_played': (user_data.get('games_played', 0) or 0) + 1,
            'wagered_since_last_withdrawal': (user_data.get('wagered_since_last_withdrawal', 0) or 0) + wager,
            'total_won': (user_data.get('total_won', 0) or 0) + (profit + wager if profit > 0 else 0)
        }

        # Add to weekly bonus pool (0.1% rakeback)
        achievements = user_data.get('achievements', {}) or {}
        pool = achievements.get('weekly_bonus_pool', 0)
        achievements['weekly_bonus_pool'] = round(pool + wager * 0.001, 2)
        update_fields['achievements'] = achievements

        if result == "win":
            update_fields['games_won'] = (user_data.get('games_won', 0) or 0) + 1
            new_streak = (user_data.get('win_streak', 0) or 0) + 1
            update_fields['win_streak'] = new_streak
            if new_streak > (user_data.get('best_win_streak', 0) or 0):
                update_fields['best_win_streak'] = new_streak
        else:
            update_fields['win_streak'] = 0

        # Update bot names in database record if needed (logic handled in record_game)
        self.db.update_user(user_id, update_fields)


    async def dice_vs_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float):
        """Play dice against the bot (called from button)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        chat_id = query.message.chat_id
        msg_id = query.message.message_id

        if wager > user_data['balance']:
            await query.answer(f"❌ Insufficient balance! Balance: ${user_data['balance']:.2f}", show_alert=True)
            return

        # Record game at start for tracking
        self.db.record_game({
            "type": "dice_bot",
            "player_id": user_id,
            "wager": wager,
            "result": "pending"
        })

        # Initialize V2 bot game state (Unified logic)
        game_id = f"v2_bot_{user_id}_{int(datetime.now().timestamp())}"
        game_state = {
            "game": "dice",
            "mode": "normal",
            "rolls": 1,
            "pts": 1,
            "p_pts": 0,
            "b_pts": 0,
            "p_rolls": [],
            "cur_rolls": 0,
            "wager": wager,
            "wager_deducted": False,
            "emoji": "🎲",
            "player": user_id,
            "chat_id": chat_id,
            "msg_id": msg_id,
            "emoji_wait": datetime.now().isoformat(),
            "waiting_for_emoji": False,
            "created_at": datetime.now().isoformat()
        }

        self.pending_pvp[game_id] = game_state
        self.db.update_pending_pvp(self.pending_pvp)

        user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

        await query.answer()
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"{emoji} **Match accepted!**\n\nPlayer 1: {user_mention}\nPlayer 2: Bot\n\n**{user_mention}**, your turn!",
            reply_to_message_id=msg_id,
            parse_mode="Markdown"
        )

    async def darts_vs_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float):
        """Play darts against the bot (called from button)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        chat_id = query.message.chat_id

        if wager > user_data['balance']:
            await query.answer(f"❌ Insufficient balance! Balance: ${user_data['balance']:.2f}", show_alert=True)
            return

        game_id = f"v2_bot_{user_id}_{int(datetime.now().timestamp())}"
        game_state = {
            "game": "darts",
            "mode": "normal",
            "rolls": 1,
            "pts": 1,
            "p_pts": 0,
            "b_pts": 0,
            "p_rolls": [],
            "cur_rolls": 0,
            "wager": wager,
            "wager_deducted": False,
            "emoji": "🎯",
            "player": user_id,
            "chat_id": chat_id,
            "emoji_wait": datetime.now().isoformat(),
            "waiting_for_emoji": False,
            "created_at": datetime.now().isoformat()
        }

        self.pending_pvp[game_id] = game_state
        self.db.data['pending_pvp'] = self.pending_pvp

        bot_mention = f"[{context.bot.username or 'Bot'}](tg://user?id={context.bot.id})"
        user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

        await query.answer()
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"🎯 **Match accepted!**\n\nPlayer 1: {user_mention}\nPlayer 2: Bot\n\n**{user_mention}**, your turn!",
            reply_to_message_id=query.message.message_id,
            parse_mode="Markdown"
        )

    async def basketball_vs_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float):
        """Play basketball against the bot (called from button)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        chat_id = query.message.chat_id

        if wager > user_data['balance']:
            await query.answer(f"❌ Insufficient balance! Balance: ${user_data['balance']:.2f}", show_alert=True)
            return

        game_id = f"v2_bot_{user_id}_{int(datetime.now().timestamp())}"
        game_state = {
            "game": "basketball",
            "mode": "normal",
            "rolls": 1,
            "pts": 1,
            "p_pts": 0,
            "b_pts": 0,
            "p_rolls": [],
            "cur_rolls": 0,
            "wager": wager,
            "wager_deducted": False,
            "emoji": "🏀",
            "player": user_id,
            "chat_id": chat_id,
            "emoji_wait": datetime.now().isoformat(),
            "waiting_for_emoji": False,
            "created_at": datetime.now().isoformat()
        }

        self.pending_pvp[game_id] = game_state
        self.db.data['pending_pvp'] = self.pending_pvp

        bot_mention = f"[{context.bot.username or 'Bot'}](tg://user?id={context.bot.id})"
        user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

        await query.answer()
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"🏀 **Match accepted!**\n\nPlayer 1: {user_mention}\nPlayer 2: Bot\n\n**{user_mention}**, your turn!",
            reply_to_message_id=query.message.message_id,
            parse_mode="Markdown"
        )

    async def soccer_vs_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float):
        """Play soccer against the bot (called from button)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        chat_id = query.message.chat_id

        if wager > user_data['balance']:
            await query.answer(f"❌ Insufficient balance! Balance: ${user_data['balance']:.2f}", show_alert=True)
            return

        # Initialize V2 bot game state
        game_id = f"v2_bot_{user_id}_{int(datetime.now().timestamp())}"
        game_state = {
            "game": "soccer",
            "mode": "normal",
            "rolls": 1,
            "pts": 1,
            "p_pts": 0,
            "b_pts": 0,
            "p_rolls": [],
            "cur_rolls": 0,
            "wager": wager,
            "wager_deducted": False,
            "emoji": "⚽",
            "player": user_id,
            "chat_id": chat_id,
            "emoji_wait": datetime.now().isoformat(),
            "waiting_for_emoji": False,
            "created_at": datetime.now().isoformat()
        }

        self.pending_pvp[game_id] = game_state
        self.db.data['pending_pvp'] = self.pending_pvp

        bot_mention = f"[{context.bot.username or 'Bot'}](tg://user?id={context.bot.id})"
        user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

        await query.answer()
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"⚽ **Match accepted!**\n\nPlayer 1: {user_mention}\nPlayer 2: Bot\n\n**{user_mention}**, your turn!",
            reply_to_message_id=query.message.message_id,
            parse_mode="Markdown"
        )

    async def bowling_vs_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float):
        """Play bowling against the bot (called from button)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        chat_id = query.message.chat_id

        if wager > user_data['balance']:
            await query.answer(f"❌ Insufficient balance! Balance: ${user_data['balance']:.2f}", show_alert=True)
            return

        # Initialize V2 bot game state
        game_id = f"v2_bot_{user_id}_{int(datetime.now().timestamp())}"
        game_state = {
            "game": "bowling",
            "mode": "normal",
            "rolls": 1,
            "pts": 1,
            "p_pts": 0,
            "b_pts": 0,
            "p_rolls": [],
            "cur_rolls": 0,
            "wager": wager,
            "wager_deducted": False,
            "emoji": "🎳",
            "player": user_id,
            "chat_id": chat_id,
            "emoji_wait": datetime.now().isoformat(),
            "waiting_for_emoji": False,
            "created_at": datetime.now().isoformat()
        }

        self.pending_pvp[game_id] = game_state
        self.db.data['pending_pvp'] = self.pending_pvp

        bot_mention = f"[{context.bot.username or 'Bot'}](tg://user?id={context.bot.id})"
        user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

        await query.answer()
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"🎳 **Match accepted!**\n\nPlayer 1: {user_mention}\nPlayer 2: Bot\n\n**{user_mention}**, your turn!",
            reply_to_message_id=query.message.message_id,
            parse_mode="Markdown"
        )

    async def create_open_dice_challenge(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float):
        """Create an open dice challenge for anyone to accept"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        username = user_data.get('username', f'User{user_id}')

        if wager > user_data['balance']:
            await query.answer("❌ Insufficient balance to cover the wager.", show_alert=True)
            return

        # Deduct wager from challenger balance immediately
        self.db.update_user(user_id, {'balance': user_data['balance'] - wager})

        chat_id = query.message.chat_id

        challenge_id = f"dice_open_{user_id}_{int(datetime.now().timestamp())}"
        self.pending_pvp[challenge_id] = {
            "type": "dice",
            "challenger": user_id,
            "challenger_roll": None,
            "opponent": None,
            "wager": wager,
            "emoji": "🎲",
            "chat_id": chat_id,
            "waiting_for_challenger_emoji": False,
            "created_at": datetime.now().isoformat()
        }
        self.db.data['pending_pvp'] = self.pending_pvp

        keyboard = [[InlineKeyboardButton("✅ Accept Challenge", callback_data=f"accept_dice_{challenge_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"{emoji} **Dice PvP Challenge!**\n\n"
            f"Challenger: @{username}\n"
            f"Wager: **${wager:.2f}**\n\n"
            f"Click below to accept!",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def accept_dice_challenge(self, update: Update, context: ContextTypes.DEFAULT_TYPE, challenge_id: str):
        """Accept a pending dice challenge and resolve it."""
        query = update.callback_query

        challenge = self.pending_pvp.get(challenge_id)
        if not challenge:
            await query.edit_message_text("❌ This challenge has expired or was canceled.")
            return

        # Check if challenge has expired (>5 minutes old)
        if 'created_at' in challenge:
            created_at = datetime.fromisoformat(challenge['created_at'])
            time_diff = (datetime.now() - created_at).total_seconds()
            if time_diff > 300:
                await query.edit_message_text("❌ This challenge has expired after 5 minutes.")
                return

        acceptor_id = query.from_user.id
        wager = challenge['wager']
        challenger_id = challenge['challenger']
        challenger_user = self.db.get_user(challenger_id)
        acceptor_user = self.db.get_user(acceptor_id)

        if acceptor_id == challenger_id:
            await query.answer("❌ You cannot accept your own challenge.", show_alert=True)
            return

        if wager > acceptor_user['balance']:
            await query.answer(f"❌ Insufficient balance. You need ${wager:.2f} to accept.", show_alert=True)
            return

        # Deduct wager from acceptor balance
        self.db.update_user(acceptor_id, {'balance': acceptor_user['balance'] - wager})

        # Tell challenger to send their emoji first
        await query.edit_message_text(
            # f"@{challenger_user['username']} your turn",
            parse_mode="Markdown"
        )

        # Update challenge to mark acceptor and wait for challenger emoji
        challenge['opponent'] = acceptor_id
        challenge['waiting_for_challenger_emoji'] = True
        challenge['waiting_for_emoji'] = False
        challenge['emoji_wait_started'] = datetime.now().isoformat()
        self.pending_pvp[challenge_id] = challenge
        self.db.data['pending_pvp'] = self.pending_pvp

    async def create_emoji_pvp_challenge(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float, game_type: str, emoji: str):
        """Create an emoji-based PvP challenge (darts, basketball, soccer)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        username = user_data.get('username', f'User{user_id}')

        if wager > user_data['balance']:
            await query.answer("❌ Insufficient balance to cover the wager.", show_alert=True)
            return

        # Deduct wager from challenger balance immediately
        self.db.update_user(user_id, {'balance': user_data['balance'] - wager})

        chat_id = query.message.chat_id

        challenge_id = f"{game_type}_open_{user_id}_{int(datetime.now().timestamp())}"
        self.pending_pvp[challenge_id] = {
            "type": game_type,
            "challenger": user_id,
            "challenger_roll": None,
            "opponent": None,
            "wager": wager,
            "emoji": emoji,
            "chat_id": chat_id,
            "waiting_for_challenger_emoji": False,
            "created_at": datetime.now().isoformat()
        }
        self.db.data['pending_pvp'] = self.pending_pvp

        keyboard = [[InlineKeyboardButton("✅ Accept Challenge", callback_data=f"accept_{game_type}_{challenge_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"{emoji} **{game_type.upper()} PvP Challenge!**\n\n"
            f"Challenger: @{username}\n"
            f"Wager: **${wager:.2f}**\n\n"
            f"Click below to accept!",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def accept_emoji_pvp_challenge(self, update: Update, context: ContextTypes.DEFAULT_TYPE, challenge_id: str):
        """Accept a pending emoji PvP challenge"""
        query = update.callback_query

        challenge = self.pending_pvp.get(challenge_id)
        if not challenge:
            await query.answer("❌ This challenge has expired or was canceled.", show_alert=True)
            return

        # Check if challenge has expired (>5 minutes old)
        if 'created_at' in challenge:
            created_at = datetime.fromisoformat(challenge['created_at'])
            time_diff = (datetime.now() - created_at).total_seconds()
            if time_diff > 300:
                await query.answer("❌ This challenge has expired after 5 minutes.", show_alert=True)
                return

        acceptor_id = query.from_user.id
        wager = challenge['wager']
        challenger_id = challenge['challenger']
        challenger_user = self.db.get_user(challenger_id)
        acceptor_user = self.db.get_user(acceptor_id)
        game_type = challenge['type']
        emoji = challenge['emoji']
        chat_id = challenge['chat_id']

        if acceptor_id == challenger_id:
            await query.answer("❌ You cannot accept your own challenge.", show_alert=True)
            return

        if wager > acceptor_user['balance']:
            await query.answer(f"❌ Insufficient balance. You need ${wager:.2f} to accept.", show_alert=True)
            return

        # Deduct wager from acceptor balance
        self.db.update_user(acceptor_id, {'balance': acceptor_user['balance'] - wager})

        # Tell challenger to send their emoji first
        await query.edit_message_text(
            # f"@{challenger_user['username']} your turn",
            parse_mode="Markdown"
        )

        # Update challenge to mark acceptor and wait for challenger emoji
        challenge['opponent'] = acceptor_id
        challenge['waiting_for_challenger_emoji'] = True
        challenge['waiting_for_emoji'] = False
        challenge['emoji_wait_started'] = datetime.now().isoformat()
        self.pending_pvp[challenge_id] = challenge
        self.db.data['pending_pvp'] = self.pending_pvp

    def calculate_cashout(self, p_pts, b_pts, target_pts, wager):
        """
        Calculate cashout value based on win probability.
        Uses a simplified binomial distribution approximation.
        Safeguarded against NaN/Inf values.
        """
        import math
        if wager <= 0 or target_pts <= 0:
            return 0.0

        if p_pts >= target_pts: return round(float(wager * 1.95), 2)
        if b_pts >= target_pts: return 0.0

        # Simplified probability: each round is 50/50 (ignoring draws for simplicity)
        # We need to win (target - p_pts) rounds before bot wins (target - b_pts)
        needed_p = target_pts - p_pts
        needed_b = target_pts - b_pts

        # Total maximum rounds left is (needed_p + needed_b - 1)
        # Using a pre-calculated small table or simplified ratio for 1-3 pts
        # Since points are 1, 2, or 3, we can handle cases

        # Probability of player winning series
        prob = 0.5 # Default
        if needed_p == 1 and needed_b == 1: prob = 0.5
        elif needed_p == 1 and needed_b == 2: prob = 0.75
        elif needed_p == 1 and needed_b == 3: prob = 0.875
        elif needed_p == 2 and needed_b == 1: prob = 0.25
        elif needed_p == 2 and needed_b == 2: prob = 0.5
        elif needed_p == 2 and needed_b == 3: prob = 0.6875
        elif needed_p == 3 and needed_b == 1: prob = 0.125
        elif needed_p == 3 and needed_b == 2: prob = 0.3125
        elif needed_p == 3 and needed_b == 3: prob = 0.5

        # Cashout = Probability * Total Payout
        # Total Payout is wager * 1.95 (standard for bot games). House edge already included in 1.95x.
        cashout_val = prob * (wager * 1.95)

        if not math.isfinite(cashout_val) or cashout_val < 0:
            return 0.0

        return max(0.0, round(float(cashout_val), 2))

    def _update_user_stats(self, user_id: int, wager: float, profit: float, outcome: str):
        """Update user statistics after a game."""
        user_data = self.db.get_user(user_id)
        
        # Ensure values are not None
        current_wagered = user_data.get('total_wagered', 0.0) or 0.0
        current_won = user_data.get('total_won', 0.0) or 0.0
        current_pnl = user_data.get('total_pnl', 0.0) or 0.0
        current_played = user_data.get('games_played', 0) or 0
        current_wins = user_data.get('games_won', 0) or 0
        current_streak = user_data.get('win_streak', 0) or 0
        best_streak = user_data.get('best_win_streak', 0) or 0
        rakeback = user_data.get('rakeback_balance', 0.0) or 0.0
        since_withdrawal = user_data.get('wagered_since_last_withdrawal', 0.0) or 0.0

        updates = {
            'total_wagered': current_wagered + wager,
            'total_pnl': current_pnl + profit,
            'games_played': current_played + 1,
            'wagered_since_last_withdrawal': since_withdrawal + wager,
            'rakeback_balance': rakeback + (wager * 0.001) # 0.1% rakeback
        }

        if outcome == "win":
            updates['games_won'] = current_wins + 1
            updates['total_won'] = current_won + (wager + profit)
            updates['win_streak'] = current_streak + 1
            if updates['win_streak'] > best_streak:
                updates['best_win_streak'] = updates['win_streak']
        elif outcome == "loss":
            updates['win_streak'] = 0

        self.db.update_user(user_id, updates)

    async def handle_emoji_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles dice/emoji responses from users (for game rolls)"""
        if not update.message or not update.message.dice:
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        dice_value = update.message.dice.value
        emoji = update.message.dice.emoji

        # Normalize score
        score = (1 if dice_value >= 4 else 0) if emoji in ["⚽", "🏀"] else dice_value

        # Determine if this message is a reply to a bot message
        is_reply = False
        replied_to_id = None
        if update.message.reply_to_message:
            is_reply = True
            replied_to_id = update.message.reply_to_message.message_id

        # Check for matching game
        for cid, challenge in list(self.pending_pvp.items()):
            if (cid.startswith("v2_bot_") or cid.startswith("v2_pvp_")):
                # Basic criteria: same chat, same emoji
                if challenge.get('chat_id') != chat_id or challenge.get('emoji') != emoji:
                    continue

                # User participation check
                if cid.startswith("v2_bot_"):
                    if challenge.get('player') != user_id:
                        continue
                else:
                    if challenge.get('challenger') != user_id and challenge.get('opponent') != user_id:
                        continue

                # If they already clicked the button and bot is rolling for them, ignore manual rolls
                if challenge.get('bot_is_rolling'):
                    continue

                # Match found - the handle_emoji_response is usually for MANUAL rolls (not via button)
                # If we got here, it means the user sent a dice emoji directly.

                # Delete Match Accepted message button if it exists
                match_msg_id = challenge.get('match_accepted_msg_id')
                if match_msg_id:
                    try:
                        # User requested not to delete buttons, so we'll skip removing the reply markup
                        # await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=match_msg_id, reply_markup=None)
                        pass
                    except Exception as e:
                        logger.warning(f"Failed to remove Match Accepted button: {e}")

                # Disable the game menu buttons after manual roll
                msg_id = challenge.get('message_id')
                if msg_id:
                    try:
                        # User requested not to delete buttons, so we'll skip removing the reply markup
                        # await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
                        pass
                    except Exception as e:
                        logger.warning(f"Failed to remove game menu buttons: {e}")

                await self.process_generic_v2_roll(update, context, cid, dice_value, emoji)
                return

        # Legacy/Other PvP logic...
        for cid, challenge in list(self.pending_pvp.items()):
            if not cid.startswith("v2_bot_") and not cid.startswith("v2_pvp_"):
                # Handle old game styles here
                if challenge.get('chat_id') == chat_id and challenge.get('waiting_for_emoji'):
                    # Check balance if wager not yet deducted
                    if not challenge.get('wager_deducted'):
                        user_data = self.db.get_user(user_id)
                        if user_data['balance'] < (challenge['wager'] - 0.001):
                            await update.message.reply_text(f"❌ Insufficient balance to start the game! (Balance: ${user_data['balance']:.2f}, Wager: ${challenge['wager']:.2f})")
                            del self.pending_pvp[cid]
                            self.db.update_pending_pvp(self.pending_pvp)
                            continue
                    # Process legacy roll...
                        self.db.update_user(user_id, {'balance': max(0, user_data['balance'] - challenge['wager'])})
                        self.db.add_transaction(user_id, "game_bet", -challenge['wager'], f"Bet on {challenge.get('game_mode', 'game')} vs Bot")
                        challenge['wager_deducted'] = True

                    # Add roll to state
                    challenge['p_rolls'].append(score)
                    challenge['cur_rolls'] += 1
                    challenge['waiting_for_cashout'] = False

                    if challenge['cur_rolls'] < challenge['rolls']:
                        # Still need more rolls
                        user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
                        await update.message.reply_text(f"{user_mention} roll again {emoji} ({challenge['cur_rolls']}/{challenge['rolls']})")
                        with self.db.app.app_context():
                            pending_pvp_state = db.session.get(GlobalState, "pending_pvp")
                            if pending_pvp_state:
                                pending_pvp_state.value = self.pending_pvp
                                db.session.commit()
                        return

                    # Player finished rolls, now bot rolls
                    challenge['waiting_for_emoji'] = False

                    with self.db.app.app_context():
                        pending_pvp_state = db.session.get(GlobalState, "pending_pvp")
                        if pending_pvp_state:
                            pending_pvp_state.value = self.pending_pvp
                            db.session.commit()

                    p_tot = sum(challenge['p_rolls'][-challenge['rolls']:])
                    # Remove button from old cashout message before bot speaks
                    old_msg_id = challenge.get('cashout_msg_id')
                    if old_msg_id:
                        try:
                            # User requested not to delete buttons
                            # await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                            challenge['cashout_msg_id'] = None
                            self.db.update_pending_pvp(self.pending_pvp)
                        except Exception as e:
                            logger.warning(f"Failed to remove button from old cashout message: {e}")

                    b_tot = 0
                    for _ in range(challenge['rolls']):
                        await asyncio.sleep(0.5)
                        d = await context.bot.send_dice(chat_id=chat_id, emoji=emoji)
                        bv = d.dice.value
                        if emoji in ["⚽", "🏀"]:
                            b_val = 1 if bv >= 4 else 0
                        else:
                            b_val = bv
                        b_tot += b_val
                        await asyncio.sleep(3.5)

                    # Re-load challenge for safety
                    with self.db.app.app_context():
                        pending_pvp_state = db.session.get(GlobalState, "pending_pvp")
                        self.pending_pvp = pending_pvp_state.value if pending_pvp_state else {}
                    challenge = self.pending_pvp.get(cid)
                    if not challenge: return

                    win = None
                    if challenge['mode'] == "normal":
                        if p_tot > b_tot: win = "p"
                        elif b_tot > p_tot: win = "b"
                        else: win = "draw"
                    else: # crazy
                        if p_tot < b_tot: win = "p"
                        elif b_tot < p_tot: win = "b"
                        else: win = "draw"

                    if win == "p": challenge['p_pts'] += 1
                    elif win == "b": challenge['b_pts'] += 1
                    elif win == "draw":
                        # Tie pays 0.95x - house takes 5% edge
                        w = challenge['wager']
                        tie_payout = round(w * 0.95, 2)
                        u = self.db.get_user(user_id)
                        self.db.update_user(user_id, {'balance': u['balance'] + tie_payout})
                        self.db.update_house_balance(-(tie_payout - w))
                        self.db.add_transaction(user_id, "game_tie", tie_payout, f"Game tie payout (0.95x)")
                        self._update_user_stats(user_id, w, tie_payout - w, "draw")

                        user_username = u.get('username', f'User{user_id}')
                        tie_text = f"🤝 <b>Draw!</b> {user_username} cashed out <b>${tie_payout:,.2f}</b>"

                        keyboard = [
                            [
                                InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{challenge['game']}_{w:.2f}_{challenge['rolls']}_{challenge['mode']}_{challenge['pts']}"),
                                InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{challenge['game']}_{w*2:.2f}_{challenge['rolls']}_{challenge['mode']}_{challenge['pts']}")
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        sent_msg = await context.bot.send_message(chat_id=chat_id, text=tie_text, reply_markup=reply_markup, parse_mode="HTML")
                        self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

                        del self.pending_pvp[cid]
                        self.db.update_pending_pvp(self.pending_pvp)
                        return

                    challenge['cur_rolls'] = 0
                    challenge['emoji_wait'] = datetime.now().isoformat()

        # Final result check
        if challenge['p_pts'] >= challenge['pts'] or challenge['b_pts'] >= challenge['pts']:
            # Series ended
            w = challenge['wager']
            if challenge['p_pts'] >= challenge['pts']:
                # ... (existing win logic)
                pass
            
            # Record the game for stats/matches
            outcome = "win" if challenge['p_pts'] >= challenge['pts'] else "loss"
            profit = w * 0.95 if outcome == "win" else -w
            
            self._update_user_stats(user_id, w, profit, outcome)
            self.db.record_game({
                "type": f"{challenge['game']}_bot",
                "player_id": user_id,
                "wager": w,
                "result": outcome,
                "p_score": challenge['p_pts'],
                "b_score": challenge['b_pts']
            })

            del self.pending_pvp[cid]
        else:
            # Next round
            if challenge['pts'] > 1:
                challenge['waiting_for_emoji'] = True
                challenge['p_rolls'] = []

                user_data = self.db.get_user(user_id)
                user_username = user_data.get('username', f'User{user_id}')

                round_text = (
                    f"<b>Score</b>\n\n"
                    f"{user_username}: {challenge['p_pts']}\n"
                    f"Rukia: {challenge['b_pts']}\n\n"
                    f"<b>{user_username}</b>, your turn!"
                )

                cashout_val = self.calculate_cashout(challenge['p_pts'], challenge['b_pts'], challenge['pts'], challenge['wager'])
                cashout_multiplier = round(cashout_val / challenge['wager'], 2) if challenge['wager'] > 0 else 0

                keyboard = [[InlineKeyboardButton(f"💰 Cashout ${cashout_val:.2f} ({cashout_multiplier}x)", callback_data=f"v2_cashout_{cid}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # Remove button from old cashout message if exists
                old_msg_id = challenge.get('cashout_msg_id')
                if old_msg_id:
                    try:
                        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                    except Exception as e:
                        logger.warning(f"Failed to remove button from old cashout message: {e}")

                sent_msg = await context.bot.send_message(chat_id=chat_id, text=round_text, reply_markup=reply_markup, parse_mode="HTML")
                challenge['cashout_msg_id'] = sent_msg.message_id
            else:
                # For 1-point games, we should have ended already
                pass

            with self.db.app.app_context():
                pending_pvp_state = db.session.get(GlobalState, "pending_pvp")
                if pending_pvp_state:
                    pending_pvp_state.value = self.pending_pvp
                    db.session.commit()
            return

            # Generic V2 PvP
            if cid.startswith("v2_pvp_") and challenge.get('emoji') == emoji:
                if challenge.get('waiting_p1') and challenge['challenger'] == user_id:
                    if not challenge.get('p1_deducted'):
                        user_data = self.db.get_user(user_id)
                        if user_data['balance'] < (challenge['wager'] - 0.001):
                            await update.message.reply_text(f"❌ Insufficient balance to roll! (Balance: ${user_data['balance']:.2f})")
                            return
                        self.db.update_user(user_id, {'balance': max(0, user_data['balance'] - challenge['wager'])})
                        challenge['p1_deducted'] = True

                    challenge['p1_rolls'].append(score)
                    if len(challenge['p1_rolls']) >= challenge['rolls']:
                        challenge['waiting_p1'], challenge['waiting_p2'] = False, True
                        p2_data = self.db.get_user(challenge['opponent'])
                        await update.message.reply_text(f"✅ @{p2_data['username']} turn!")
                    challenge['emoji_wait'] = datetime.now().isoformat()
                    return
                if challenge.get('waiting_p2') and challenge['opponent'] == user_id:
                    if not challenge.get('p2_deducted'):
                        user_data = self.db.get_user(user_id)
                        if user_data['balance'] < (challenge['wager'] - 0.001):
                            await update.message.reply_text(f"❌ Insufficient balance to roll! (Balance: ${user_data['balance']:.2f})")
                            return
                        self.db.update_user(user_id, {'balance': max(0, user_data['balance'] - challenge['wager'])})
                        challenge['p2_deducted'] = True

                    challenge['p2_rolls'].append(score)
                    if len(challenge['p2_rolls']) >= challenge['rolls']:
                        challenge['waiting_p2'] = False
                    challenge['emoji_wait'] = datetime.now().isoformat()
                    return
        challenge_id_to_resolve = None
        challenge_to_resolve = None

        for cid, challenge in self.pending_pvp.items():
            logger.info(f"Checking challenge {cid}: emoji={challenge.get('emoji')}, waiting_for_challenger={challenge.get('waiting_for_challenger_emoji')}, waiting={challenge.get('waiting_for_emoji')}, chat={challenge.get('chat_id')}, player={challenge.get('player')}, opponent={challenge.get('opponent')}")

            # Check if waiting for challenger's emoji
            if (challenge.get('waiting_for_challenger_emoji') and 
                challenge.get('emoji') == emoji and
                challenge.get('chat_id') == chat_id and
                challenge.get('challenger') == user_id):
                challenge_id_to_resolve = cid
                challenge_to_resolve = challenge
                logger.info(f"Found challenger emoji challenge: {cid}")

                # Wait for animation
                await asyncio.sleep(3)

                # Save challenger's roll and tell acceptor to go
                challenge['challenger_roll'] = roll_value
                challenge['waiting_for_challenger_emoji'] = False
                challenge['waiting_for_emoji'] = True
                challenge['emoji_wait_started'] = datetime.now().isoformat()
                self.pending_pvp[cid] = challenge
                self.db.data['pending_pvp'] = self.pending_pvp

                acceptor_user = self.db.get_user(challenge['opponent'])
                # await context.bot.send_message(chat_id=chat_id, text=f"@{acceptor_user['username']} your turn", parse_mode="Markdown")
                return

            # Check if waiting for acceptor's emoji (or bot vs player)
            if (challenge.get('waiting_for_emoji') and 
                challenge.get('emoji') == emoji and
                challenge.get('chat_id') == chat_id):
                # Check if it's PvP (opponent) or bot vs player (player)
                if challenge.get('opponent') == user_id or challenge.get('player') == user_id:
                    challenge_id_to_resolve = cid
                    challenge_to_resolve = challenge
                    logger.info(f"Found matching challenge: {cid}")
                    break

        if not challenge_to_resolve or not challenge_id_to_resolve:
            logger.info("No matching pending game found")
            return  # Not a pending emoji response

        # Resolve the challenge
        await asyncio.sleep(3)  # Wait for emoji animation

        game_type = challenge_to_resolve['type']
        wager = challenge_to_resolve['wager']

        # Check if it's a bot vs player game
        if game_type in ['dice_bot', 'darts_bot', 'basketball_bot', 'soccer_bot', 'bowling_bot']:
            await self.resolve_bot_vs_player_game(update, context, challenge_to_resolve, challenge_id_to_resolve, roll_value)
            return

        # It's a PvP game
        challenger_id = challenge_to_resolve['challenger']
        challenger_roll = challenge_to_resolve['challenger_roll']
        acceptor_roll = roll_value

        challenger_user = self.db.get_user(challenger_id)
        acceptor_user = self.db.get_user(user_id)

        # Remove challenge from pending
        del self.pending_pvp[challenge_id_to_resolve]
        self.db.data['pending_pvp'] = self.pending_pvp

        # Determine winner
        winner_id = None
        loser_id = None
        result_text = ""

        # Normalize rolls for soccer: 4 and 5 are both goals (value 1), 1-3 are misses (value 0)
        c_val = challenger_roll
        a_val = acceptor_roll
        if game_type.startswith("soccer"):
            c_val = 1 if challenger_roll >= 4 else 0
            a_val = 1 if acceptor_roll >= 4 else 0

        if c_val > a_val:
            winner_id = challenger_id
            loser_id = user_id
            winner_user = self.db.get_user(winner_id)
            winner_display = f"<b>{winner_user.get('username', f'User{winner_id}')}</b>"
            result_text = f"🎉 {winner_display} won <b>${wager:,.2f}</b>"
        elif a_val > c_val:
            winner_id = user_id
            loser_id = challenger_id
            winner_user = self.db.get_user(winner_id)
            winner_display = f"<b>{winner_user.get('username', f'User{winner_id}')}</b>"
            result_text = f"🎉 {winner_display} won <b>${wager:,.2f}</b>"
        else:
            # Draw: refund both wagers (already deducted)
            self.db.update_user(challenger_id, {'balance': challenger_user['balance'] + wager})
            self.db.update_user(user_id, {'balance': acceptor_user['balance'] + wager})
            result_text = "🤝 Draw! Refunded"

            self._update_user_stats(challenger_id, wager, 0.0, "draw")
            self._update_user_stats(user_id, wager, 0.0, "draw")

            self.db.record_game({
                "type": f"{game_type}_pvp", 
                "player_id": user_id, 
                "opponent_id": challenger_id, 
                "wager": wager, 
                "result": "draw"
            })
            self.db.record_game({
                "type": f"{game_type}_pvp", 
                "player_id": challenger_id, 
                "opponent_id": user_id, 
                "wager": wager, 
                "result": "draw"
            })

            keyboard = [
                [InlineKeyboardButton("🤖 Play vs Bot", callback_data=f"{game_type}_bot_{wager:.2f}")],
                [InlineKeyboardButton("👥 Create PvP Challenge", callback_data=f"{game_type}_player_open_{wager:.2f}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            sent_msg = await context.bot.send_message(chat_id=chat_id, text=result_text, reply_markup=reply_markup, parse_mode="Markdown")
            self.button_ownership[(chat_id, sent_msg.message_id)] = user_id
            return

        # Handle Win/Loss
        # Both players have already been deducted the 'wager' amount.
        # Winner gets both wagers (wager * 2)
        winnings = wager * 2
        winner_profit = wager

        winner_user = self.db.get_user(winner_id)
        # We need to update the balance properly. 
        # The user object retrieved might be stale if we used update_user earlier, 
        # but here we just need to add the winnings to their current state.
        self.db.update_user(winner_id, {'balance': winner_user['balance'] + winnings})

        self._update_user_stats(winner_id, wager, winner_profit, "win")
        # Fix: Don't subtract the wager again in _update_user_stats since it was already deducted at start
        self._update_user_stats(loser_id, wager, -wager, "loss")

        self.db.add_transaction(winner_id, f"{game_type}_pvp_win", winner_profit, f"{game_type.upper()} PvP Win vs {self.db.get_user(loser_id)['username']}")
        self.db.add_transaction(loser_id, f"{game_type}_pvp_loss", -wager, f"{game_type.upper()} PvP Loss vs {self.db.get_user(winner_id)['username']}")
        
        self.db.record_game({
            "type": f"{game_type}_pvp", 
            "player_id": winner_id,
            "opponent_id": loser_id,
            "wager": wager,
            "result": "win"
        })
        self.db.record_game({
            "type": f"{game_type}_pvp", 
            "player_id": loser_id,
            "opponent_id": winner_id,
            "wager": wager,
            "result": "loss"
        })

        winner_username = winner_user.get('username', f'User{winner_id}')
        final_text = (
            f"<b>{winner_username}</b> won <b>${winnings:,.2f}</b>!"
        )

        keyboard = [
            [
                InlineKeyboardButton("🔄 Play Again", callback_data=f"{game_type.replace('_pvp', '_bot')}_{wager:.2f}"),
                InlineKeyboardButton("🔄 Double", callback_data=f"{game_type.replace('_pvp', '_bot')}_{wager*2:.2f}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_msg = await context.bot.send_message(
            chat_id=chat_id, 
            text=final_text, 
            reply_markup=reply_markup, 
            parse_mode="HTML",
            reply_to_message_id=update.effective_message.message_id
        )
        self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

    async def process_generic_v2_roll(self, update: Update, context: ContextTypes.DEFAULT_TYPE, cid: str, dice_value: int, emoji: str):
        """Processes a manual dice roll for a V2 game"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        challenge = self.pending_pvp.get(cid)
        if not challenge: return

        # Normalize score
        score = (1 if dice_value >= 4 else 0) if emoji in ["⚽", "🏀"] else dice_value

        if cid.startswith("v2_bot_"):
            # IMMEDIATELY delete or clear the buttons from the previous message
            # Priority: Match Accepted Message -> Cashout Message -> Original Message
            target_msg_id = challenge.get('match_accepted_msg_id') or challenge.get('cashout_msg_id') or challenge.get('message_id')
            if target_msg_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=target_msg_id, reply_markup=None)
                    # Clear reference if it was cashout to avoid double-attempt later
                    if 'cashout_msg_id' in challenge: challenge['cashout_msg_id'] = None
                except Exception as e:
                    logger.debug(f"Could not remove buttons on manual roll: {e}")

            # Check balance if wager not yet deducted
            if not challenge.get('wager_deducted'):
                user_data = self.db.get_user(user_id)
                wager = challenge.get('wager', 0)
                if user_data['balance'] < (wager - 0.001):
                    await update.message.reply_text(f"❌ Insufficient balance to start the game! (Balance: ${user_data['balance']:.2f}, Wager: ${wager:.2f})")
                    del self.pending_pvp[cid]
                    self.db.update_pending_pvp(self.pending_pvp)
                    return
                # Deduct balance
                self.db.update_user(user_id, {'balance': max(0, user_data['balance'] - wager)})
                self.db.add_transaction(user_id, "game_bet", -wager, f"Bet on {challenge.get('game', 'game')} vs Bot")
                challenge['wager_deducted'] = True

            # Bot game: add to player rolls
            challenge['p_rolls'].append(score)
            challenge['cur_rolls'] += 1

            # Ensure bot rolls list exists
            if 'b_rolls' not in challenge:
                challenge['b_rolls'] = []

            # Save progress
            self.db.update_pending_pvp(self.pending_pvp)

            if challenge['cur_rolls'] < challenge['rolls']:
                # Need more rolls
                # DISABLED: user requested to remove "roll again" message
                # p1_name = self.db.get_user(user_id).get('username', f'User{user_id}')
                # bold_name = f"<b>{p1_name}</b>"
                # reply_to_id = challenge.get('message_id')
                # await asyncio.sleep(3)
                # if len(challenge['p_rolls']) < challenge['rolls']:
                #     await context.bot.send_message(
                #         chat_id=chat_id,
                #         text=f"{bold_name}, roll again! ({challenge['cur_rolls']}/{challenge['rolls']}) {emoji}",
                #         reply_to_message_id=reply_to_id,
                #         parse_mode="HTML"
                #     )
                pass
            else:
                # Player finished, trigger bot response
                challenge['waiting_for_emoji'] = False
                challenge['bot_is_rolling'] = True
                self.db.update_pending_pvp(self.pending_pvp)

                # Remove button from old cashout message before bot speaks
                old_msg_id = challenge.get('cashout_msg_id')
                if old_msg_id:
                    try:
                        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                        challenge['cashout_msg_id'] = None
                        self.db.update_pending_pvp(self.pending_pvp)
                    except Exception as e:
                        logger.warning(f"Failed to remove button from old cashout message: {e}")

                challenge['b_rolls'] = []
                for _ in range(challenge['rolls']):
                    await asyncio.sleep(0.5)
                    d = await context.bot.send_dice(chat_id=chat_id, emoji=emoji)
                    bv = d.dice.value
                    bs = (1 if bv >= 4 else 0) if emoji in ["⚽", "🏀"] else bv
                    challenge['b_rolls'].append(bs)
                    self.db.update_pending_pvp(self.pending_pvp) # Save bot rolls as they happen

                # Wait for last bot dice animation
                await asyncio.sleep(4)

                # Re-load challenge for safety to get the absolute latest state
                self.pending_pvp = self.db.data.get('pending_pvp', {})
                challenge = self.pending_pvp.get(cid)
                if not challenge: 
                    logger.error(f"Challenge {cid} disappeared during bot turn")
                    return

                p_tot = sum(challenge.get('p_rolls', []))
                b_tot = sum(challenge.get('b_rolls', []))

                round_win = None
                if challenge.get('mode', 'normal') == "normal":
                    if p_tot > b_tot: round_win = "p"
                    elif b_tot > p_tot: round_win = "b"
                    else: round_win = "draw"
                else:
                    if p_tot < b_tot: round_win = "p"
                    elif b_tot < p_tot: round_win = "b"
                    else: round_win = "draw"

                if round_win == "p": challenge['p_pts'] += 1
                elif round_win == "b": challenge['b_pts'] += 1

                # Reset for next round or finish
                challenge['bot_is_rolling'] = False

                # Call handle_game_resolution if it was refactored, or just handle it here
                # (Due to complexity of existing code, I'll ensure p_pts/b_pts are updated and handled)
                await self._finalize_v2_round(update, context, cid)

        elif cid.startswith("v2_pvp_"):
            # PvP manual roll logic...
            pass

        self.db.update_pending_pvp(self.pending_pvp)

    async def _finalize_v2_round(self, update, context, cid):
        """Finalize a V2 round after all rolls are done"""
        challenge = self.pending_pvp.get(cid)
        if not challenge: return
        chat_id = challenge['chat_id']
        user_id = challenge['player']
        game = challenge.get('game', 'dice')
        target_pts = challenge.get('pts', 1)
        w = challenge['wager']
        rolls = challenge['rolls']
        mode = challenge['mode']
        emoji = challenge['emoji']

        # Source of truth for totals
        p_tot = sum(challenge.get('p_rolls', []))
        b_tot = sum(challenge.get('b_rolls', []))

        # Fix KeyError: 'bot_roll' - ensure it exists for record_game
        if 'bot_roll' not in challenge:
            challenge['bot_roll'] = b_tot
        if 'player_roll' not in challenge:
            challenge['player_roll'] = p_tot

        if challenge['p_pts'] >= target_pts or challenge['b_pts'] >= target_pts:
            # Remove button from final cashout message if it exists
            old_msg_id = challenge.get('cashout_msg_id')
            if old_msg_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                except Exception as e:
                    logger.warning(f"Failed to remove button from final cashout message: {e}")

                # Record the game for stats/matches
                outcome = "win" if challenge['p_pts'] >= challenge['pts'] else "loss"
                profit = (w * 0.95) if outcome == "win" else -w
                
                self._update_user_stats(user_id, w, profit, outcome)
                self.db.record_game({
                    "type": f"{challenge['game']}_bot",
                    "player_id": user_id,
                    "wager": w,
                    "result": outcome,
                    "p_score": challenge['p_pts'],
                    "b_score": challenge['b_pts']
                })
                
                # Series End logic
                if challenge['p_pts'] >= challenge['pts']:
                    payout = w * 1.95
                    u = self.db.get_user(user_id)
                    # Balance already updated in _update_user_stats if we use profit relative to wager
                    # However, current logic in _update_user_stats might need adjustment or 
                    # we handle balance here for clarity
                    u['balance'] += payout
                    self.db.update_user(user_id, {'balance': u['balance']})
                    self.db.update_house_balance(-(payout - w))

                p1_name = u.get('username', f'User{user_id}')
                bold_name = f"<b>{p1_name}</b>"
                win_text = (
                    f"🏆 <b>Game over!</b>\n\n"
                    f"{p1_name} • {challenge['p_pts']}\n"
                    f"Bot • {challenge['b_pts']}\n\n"
                    f"<b>{bold_name}</b> won <b>${payout:,.2f}</b>!"
                )
                kb = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{game}_{w:.2f}_{rolls}_{mode}_{target_pts}"),
                       InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{game}_{w*2:.2f}_{rolls}_{mode}_{target_pts}")]]

                reply_to_id = challenge.get('message_id')
                sent_msg = await context.bot.send_message(chat_id=chat_id, text=win_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML", reply_to_message_id=reply_to_id)
                self.button_ownership[(chat_id, sent_msg.message_id)] = user_id
            else:
                self.db.update_house_balance(w)
                u = self.db.get_user(user_id)
                p1_name = u.get('username', f'User{user_id}')
                loss_text = (
                    f"🏆 <b>Game over!</b>\n\n"
                    f"{p1_name} • {challenge['p_pts']}\n"
                    f"Bot • {challenge['b_pts']}\n\n"
                    f"<b>Bot</b> won <b>${w * 1.95:,.2f}</b>!"
                )
                kb = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{game}_{w:.2f}_{rolls}_{mode}_{target_pts}"),
                       InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{game}_{w*2:.2f}_{rolls}_{mode}_{target_pts}")]]
                
                # Stats already updated at start of _finalize_v2_round
                
                reply_to_id = challenge.get('message_id')
                sent_msg = await context.bot.send_message(chat_id=chat_id, text=loss_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML", reply_to_message_id=reply_to_id)
                self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            del self.pending_pvp[cid]
        else:
            # Next Round
            u = self.db.get_user(user_id)
            p1_name = u.get('username', f'User{user_id}')
            text = (
                f"<b>Score</b>\n\n"
                f"{p1_name}: {challenge['p_pts']}\n"
                f"Bot: {challenge['b_pts']}\n\n"
                f"<b>{p1_name}</b>, your turn!"
            )
            challenge['p_rolls'] = []
            challenge['b_rolls'] = []
            challenge['cur_rolls'] = 0
            challenge['waiting_for_emoji'] = True

            cashout_val = self.calculate_cashout(challenge['p_pts'], challenge['b_pts'], challenge['pts'], challenge['wager'])
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
                    logger.warning(f"Failed to remove button from old cashout message: {e}")

            # DISABLED: redundant message during game progress
            reply_to_id = challenge.get('message_id')
            sent_msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML", reply_to_message_id=reply_to_id)
            challenge['cashout_msg_id'] = sent_msg.message_id
            self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

        self.db.update_pending_pvp(self.pending_pvp)
        return

    async def coinflip_vs_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float, choice: str):
        """Play coinflip against the bot (called from button)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        username = user_data.get('username', f'User{user_id}')
        chat_id = query.message.chat_id

        if wager > user_data['balance']:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Balance: ${user_data['balance']:.2f}")
            return

        # Deduct wager immediately
        user_data['balance'] -= wager
        self.db.update_user(user_id, {'balance': user_data['balance']})
        self.db.add_transaction(user_id, "coinflip_bet", -wager, f"CoinFlip Bet: ${wager:.2f}")

        # Send coin emoji and determine result
        await context.bot.send_message(chat_id=chat_id, text="🪙")
        await asyncio.sleep(2)

        # Random coin flip result
        result = random.choice(['heads', 'tails'])

        # Determine win/loss/draw
        outcome = "win" if choice == result else "loss"
        profit = wager * 0.95 if outcome == "win" else -wager

        # Update user stats and database
        self._update_user_stats(user_id, wager, profit, outcome)

        if outcome == "win":
            payout = wager * 1.95
            # Credit full payout (wager was already deducted)
            user_data = self.db.get_user(user_id)
            user_data['balance'] += payout
            self.db.update_user(user_id, {'balance': user_data['balance']})
            result_text = f"<b>{username}</b> won <b>${payout:,.2f}</b>!"
            self.db.update_house_balance(-(payout - wager))
        else:
            result_text = f"<b>Bot</b> won <b>${wager * 1.95:,.2f}</b>!"
            self.db.update_house_balance(wager)

        self.db.record_game({
            "type": "coinflip_bot",
            "player_id": user_id,
            "wager": wager,
            "choice": choice,
            "result": result, # The actual flip result
            "outcome": outcome # win or loss
        })
        self.db.add_transaction(user_id, "coinflip_bot", profit, f"CoinFlip vs Bot - Wager: ${wager:.2f}")

        keyboard = [
            [InlineKeyboardButton("Heads again", callback_data=f"flip_bot_{wager:.2f}_heads")],
            [InlineKeyboardButton("Tails again", callback_data=f"flip_bot_{wager:.2f}_tails")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await self.send_with_buttons(chat_id, result_text, reply_markup, user_id)

        # Send sticker based on outcome
        await self.send_sticker(chat_id, outcome, profit)

    async def roulette_play_direct(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float, choice: str):
        """Play roulette directly from command (for specific number bets)"""
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        username = user_data.get('username', f'User{user_id}')
        chat_id = update.message.chat_id

        if wager > user_data['balance']:
            await update.message.reply_text(f"❌ Balance: ${user_data['balance']:.2f}")
            return

        # Deduct wager immediately
        user_data['balance'] -= wager
        self.db.update_user(user_id, {'balance': user_data['balance']})
        self.db.add_transaction(user_id, "roulette_bet", -wager, f"Roulette Bet: ${wager:.2f}")

        reds = [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]
        blacks = [2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35]
        greens = [0, 37]

        all_numbers = reds + blacks + greens
        result_num = random.choice(all_numbers)

        if result_num in reds:
            result_color = "red"
            result_emoji = "🔴"
        elif result_num in blacks:
            result_color = "black"
            result_emoji = "⚫"
        else:
            result_color = "green"
            result_emoji = "🟢"

        result_display = "0" if result_num == 0 else "00" if result_num == 37 else str(result_num)

        roulette_stickers = self.stickers.get('roulette', {})
        sticker_id = roulette_stickers.get(result_display)

        if sticker_id:
            await context.bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
        else:
            await update.message.reply_text("🎰 Spinning the wheel...")

        await asyncio.sleep(2.5)

        if choice.startswith("num_"):
            bet_num = int(choice.split("_")[1])
            bet_display = "0" if bet_num == 0 else "00" if bet_num == 37 else str(bet_num)

            if bet_num == result_num:
                profit = wager * 35
                outcome = "win"
                payout = profit + wager
                # Credit full payout (wager was already deducted)
                user_data = self.db.get_user(user_id)
                user_data['balance'] += payout
                user_data['total_won'] = user_data.get('total_won', 0) + payout
                self.db.update_user(user_id, user_data)
                user_mention = f'<a href="tg://user?id={user_id}">{username}</a>'
                result_text = f"🎉 Congratulations, {user_mention}! You won <b>${profit:,.2f}</b>!"
                self.db.update_house_balance(-profit)
            else:
                profit = -wager
                outcome = "loss"
                result_text = f"<b>Bot</b> won <b>${wager * 1.95:,.2f}</b>!"
                self.db.update_house_balance(wager)

            self._update_user_stats(user_id, wager, profit, outcome)
            self.db.add_transaction(user_id, "roulette", profit, f"Roulette - Bet: #{bet_display} - Wager: ${wager:.2f}")
            self.db.record_game({
                "type": "roulette",
                "player_id": user_id,
                "wager": wager,
                "choice": f"#{bet_display}",
                "result": result_display,
                "result_color": result_color,
                "outcome": outcome
            })

            await update.message.reply_text(result_text, parse_mode="Markdown")

    async def roulette_play(self, update: Update, context: ContextTypes.DEFAULT_TYPE, wager: float, choice: str):
        """Play roulette (called from button)"""
        query = update.callback_query
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        username = user_data.get('username', f'User{user_id}')
        chat_id = query.message.chat_id

        if wager > user_data['balance']:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Balance: ${user_data['balance']:.2f}")
            return

        # Deduct wager immediately
        user_data['balance'] -= wager
        self.db.update_user(user_id, {'balance': user_data['balance']})
        self.db.add_transaction(user_id, "roulette_bet", -wager, f"Roulette Bet: ${wager:.2f}")

        reds = [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]
        blacks = [2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35]
        greens = [0, 37]

        all_numbers = reds + blacks + greens
        result_num = random.choice(all_numbers)

        if result_num in reds:
            result_color = "red"
            result_emoji = "🔴"
        elif result_num in blacks:
            result_color = "black"
            result_emoji = "⚫"
        else:
            result_color = "green"
            result_emoji = "🟢"

        result_display = "0" if result_num == 0 else "00" if result_num == 37 else str(result_num)

        roulette_stickers = self.stickers.get('roulette', {})
        sticker_id = roulette_stickers.get(result_display)

        if sticker_id:
            await context.bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
        else:
            await context.bot.send_message(chat_id=chat_id, text="🎰 Spinning the wheel...")

        await asyncio.sleep(2.5)

        profit = 0.0
        outcome = "loss"
        multiplier = 0
        won = False
        bet_description = choice.upper()

        if choice == "red" and result_num in reds:
            won = True
            multiplier = 2
            bet_description = "RED"
        elif choice == "black" and result_num in blacks:
            won = True
            multiplier = 2
            bet_description = "BLACK"
        elif choice == "green" and result_num in greens:
            won = True
            multiplier = 14
            bet_description = "GREEN"
        elif choice == "odd" and result_num > 0 and result_num != 37 and result_num % 2 == 1:
            won = True
            multiplier = 2
            bet_description = "ODD"
        elif choice == "even" and result_num > 0 and result_num != 37 and result_num % 2 == 0:
            won = True
            multiplier = 2
            bet_description = "EVEN"
        elif choice == "low" and result_num >= 1 and result_num <= 18:
            won = True
            multiplier = 2
            bet_description = "LOW (1-18)"
        elif choice == "high" and result_num >= 19 and result_num <= 36:
            won = True
            multiplier = 2
            bet_description = "HIGH (19-36)"

        if won:
            profit = wager * (multiplier - 1)
            outcome = "win"
            payout = profit + wager
            # Credit full payout (wager was already deducted)
            user_data = self.db.get_user(user_id)
            user_data['balance'] += payout
            user_data['total_won'] = user_data.get('total_won', 0) + payout
            self.db.update_user(user_id, user_data)
            result_text = f"<b>{username}</b> won <b>${payout:,.2f}</b>!"
            self.db.update_house_balance(-profit)
        else:
            profit = -wager
            outcome = "loss"
            result_text = f"<b>Bot</b> won <b>${wager * 1.95:,.2f}</b>!"
            self.db.update_house_balance(wager)

        self._update_user_stats(user_id, wager, profit, outcome)
        self.db.add_transaction(user_id, "roulette", profit, f"Roulette - Bet: {bet_description} - Wager: ${wager:.2f}")
        self.db.record_game({
            "type": "roulette",
            "player_id": user_id,
            "wager": wager,
            "choice": choice,
            "result": result_display,
            "result_color": result_color,
            "outcome": outcome
        })

        keyboard = [
            [InlineKeyboardButton("Red (2x)", callback_data=f"roulette_{wager:.2f}_red"),
             InlineKeyboardButton("Black (2x)", callback_data=f"roulette_{wager:.2f}_black")],
            [InlineKeyboardButton("Green (14x)", callback_data=f"roulette_{wager:.2f}_green")],
            [InlineKeyboardButton("Odd (2x)", callback_data=f"roulette_{wager:.2f}_odd"),
             InlineKeyboardButton("Even (2x)", callback_data=f"roulette_{wager:.2f}_even")],
            [InlineKeyboardButton("Low (2x)", callback_data=f"roulette_{wager:.2f}_low"),
             InlineKeyboardButton("High (2x)", callback_data=f"roulette_{wager:.2f}_high")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await self.send_with_buttons(chat_id, result_text, reply_markup, user_id)

    # --- CALLBACK HANDLER ---

    async def start_generic_v2_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, game: str, wager: float, rolls: int, mode: str, pts: int):
        query = update.callback_query
        user_id = query.from_user.id
        chat_id = query.message.chat_id

        # Check for active game
        active_games = [cid for cid, chal in self.pending_pvp.items() if cid.startswith("v2_bot_") and chal.get('player') == user_id]
        if active_games:
            try:
                await query.answer("❌ You already have an active game! Finish it first.", show_alert=True)
            except:
                pass
            return

        user_data = self.db.get_user(user_id)
        if wager > user_data['balance']:
            try:
                await query.answer("❌ Insufficient balance", show_alert=True)
            except:
                pass
            return

        # Deduct balance immediately
        # self.db.update_user(user_id, {"balance": user_data['balance'] - wager})
        # self.db.add_transaction(user_id, "game_bet", -wager, f"{game.capitalize()} vs Bot")

        cid = f"v2_bot_{game}_{user_id}_{int(datetime.now().timestamp())}"

        # Use class emoji map
        emoji = self.emoji_map.get(game, "🎲")

        self.pending_pvp[cid] = {
            "type": f"{game}_bot_v2", "player": user_id, "wager": wager, "game": game, "emoji": emoji,
            "rolls": rolls, "mode": mode, "pts": pts, "chat_id": chat_id,
            "p_pts": 0, "b_pts": 0, "p_rolls": [], "cur_rolls": 0, "emoji_wait": datetime.now().isoformat(),
            "wager_deducted": False, "message_id": query.message.message_id,
            "waiting_for_emoji": False
        }
        self.db.update_pending_pvp(self.pending_pvp)

        p1_name = user_data.get('username', f'User{user_id}')
        msg_text = (
            f"{emoji} <b>Match accepted!</b>\n\n"
            f"Player 1: <b>{p1_name}</b>\n"
            f"Player 2: <b>Bot</b>\n\n"
            f"<b>{p1_name}</b>, your turn!"
        )
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data=f"setup_cancel_roll")]]

        try:
            # Answer the query
            await query.answer()

            # Send the new "Match accepted" message as a NEW message
            sent_msg = await context.bot.send_message(
                chat_id=chat_id, 
                text=msg_text, 
                reply_markup=InlineKeyboardMarkup(kb) if kb else None, 
                parse_mode="HTML",
                reply_to_message_id=query.message.message_id
            )
            # Register ownership for the NEW message
            self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            # Store the message ID for automatic deletion when game starts
            self.pending_pvp[cid]['match_accepted_msg_id'] = sent_msg.message_id
            self.db.update_pending_pvp(self.pending_pvp)

            # DO NOT edit or delete the original query.message (the Game Details menu)
            # This keeps the original menu with its buttons intact as requested.

        except Exception as e:
            logger.error(f"Error sending match accepted message: {e}")
            # Fallback only if message sending fails
            try:
                await query.edit_message_text(text=msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            except Exception as inner_e:
                logger.error(f"Fallback edit also failed: {inner_e}")

        return

    async def start_generic_v2_pvp(self, update: Update, context: ContextTypes.DEFAULT_TYPE, game: str, wager: float, rolls: int, mode: str, pts: int):
        """Create a new PvP challenge in the group"""
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)

        if wager > user_data['balance']:
            await update.effective_message.reply_text(f"❌ Insufficient balance! (${user_data['balance']:.2f})")
            return

        # Deduct wager immediately to lock it
        self.db.update_user(user_id, {'balance': user_data['balance'] - wager})

        cid = f"v2_{user_id}_{int(time.time())}"
        emoji = self.game_emojis.get(game, "🎲")

        challenge = {
            'challenger': user_id,
            'game': game,
            'wager': wager,
            'rolls': rolls,
            'mode': mode,
            'pts': pts,
            'emoji': emoji,
            'status': 'pending',
            'created_at': time.time(),
            'p1_rolls': [],
            'p2_rolls': [],
            'p1_pts': 0,
            'p2_pts': 0
        }

        self.pending_pvp[cid] = challenge
        self.db.update_pending_pvp(self.pending_pvp)

        keyboard = [
            [InlineKeyboardButton("Join Challenge", callback_data=f"v2_pvp_accept_confirm_{game}_{wager:.2f}_{rolls}_{mode}_{pts}_{cid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"setup_cancel_roll")]
        ]
        msg_text = f"{emoji} **{game.capitalize()} PvP**\nChallenger: @{user_data.get('username', 'User')}\nWager: ${wager:.2f}\nMode: {mode.capitalize()}\nTarget: {pts}\n\nClick below to join!"

        if update.callback_query:
            sent_msg = await update.callback_query.edit_message_text(text=msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            sent_msg = await update.message.reply_text(text=msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

        self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

    async def accept_generic_v2_pvp(self, update: Update, context: ContextTypes.DEFAULT_TYPE, cid: str):
        query = update.callback_query
        user_id = query.from_user.id
        challenge = self.pending_pvp.get(cid)
        if not challenge or challenge['challenger'] == user_id:
            await query.answer("❌ Cannot join", show_alert=True)
            return

        user_data = self.db.get_user(user_id)
        if user_data['balance'] < challenge['wager']:
            await query.answer("❌ Insufficient balance", show_alert=True)
            return

        # Deduct balance for opponent
        self.db.update_user(user_id, {"balance": user_data['balance'] - challenge['wager']})
        self.db.add_transaction(user_id, "game_bet", -challenge['wager'], f"{challenge['game'].capitalize()} PvP (Opponent)")

        challenge['opponent'] = user_id
        challenge['p2_deducted'] = True
        await context.bot.send_message(chat_id=query.message.chat_id, text="✅ Challenge Accepted! Starting...")
        asyncio.create_task(self.generic_v2_pvp_loop(context, cid))

    async def generic_v2_pvp_loop(self, context: ContextTypes.DEFAULT_TYPE, cid: str):
        challenge = self.pending_pvp.get(cid)
        if not challenge: return
        chat_id = challenge['chat_id']
        p1_id, p2_id = challenge['challenger'], challenge['opponent']
        p1_data, p2_data = self.db.get_user(p1_id), self.db.get_user(p2_id)

        while challenge['p1_pts'] < challenge['pts'] and challenge['p2_pts'] < challenge['pts']:
            await context.bot.send_message(chat_id=chat_id, text=f"Round Start! Score: {challenge['p1_pts']} - {challenge['p2_pts']}\n👉 @{p1_data['username']}, send your {challenge['rolls']} {challenge['emoji']} now!")
            challenge['p1_rolls'], challenge['p2_rolls'] = [], []
            challenge['waiting_p1'], challenge['waiting_p2'] = True, False
            challenge['emoji_wait'] = datetime.now().isoformat()
            while len(challenge['p1_rolls']) < challenge['rolls'] or len(challenge['p2_rolls']) < challenge['rolls']:
                await asyncio.sleep(2)
                challenge = self.pending_pvp.get(cid)
                if not challenge: return

            p1_tot, p2_tot = sum(challenge['p1_rolls']), sum(challenge['p2_rolls'])
            win = None
            if challenge['mode'] == "normal":
                if p1_tot > p2_tot: win = "p1"
                elif p2_tot > p1_tot: win = "p2"
            else:
                if p1_tot < p2_tot: win = "p1"
                elif p2_tot < p1_tot: win = "p2"

            if win == "p1": challenge['p1_pts'] += 1
            elif win == "p2": challenge['p2_pts'] += 1

            p1_username = p1_data.get('username', f'User{p1_id}')
            p2_username = p2_data.get('username', f'User{p2_id}')
            score_text = f"<b>{p1_username}</b>: {challenge['p1_pts']}\n<b>{p2_username}</b>: {challenge['p2_pts']}"
            await context.bot.send_message(chat_id=chat_id, text=f"{score_text}", parse_mode="HTML")
            await asyncio.sleep(1)

        wager = challenge['wager']
        winner_id = p1_id if challenge['p1_pts'] >= challenge['pts'] else p2_id
        loser_id = p2_id if winner_id == p1_id else p1_id
        # Both players already had wager deducted when accepting/starting
        # Winner gets (wager * 2) total payout
        self.db.update_user(winner_id, {'balance': self.db.get_user(winner_id)['balance'] + wager * 2})
        self._update_user_stats(winner_id, wager, wager, "win")
        # Fix: Don't subtract the wager again in _update_user_stats since it was already deducted at start
        self._update_user_stats(loser_id, wager, -wager, "loss")

        self.db.record_game({
            "type": f"{challenge['game']}_pvp",
            "challenger": p1_id,
            "opponent": p2_id,
            "wager": wager,
            "profit": wager,
            "result": "win",
            "winner_id": winner_id
        })

        winner_data = self.db.get_user(winner_id)
        winner_username = winner_data.get('username', f'User{winner_id}')
        payout = wager * 1.95 # Adjusted for house edge if needed, or wager*2 for pvp.
        # User requested bolded name without @ and bolded amount
        win_msg = f"🏆 <b>{winner_username}</b> won <b>${wager*2:.2f}</b>!"

        await context.bot.send_message(chat_id=chat_id, text=win_msg, parse_mode="HTML")
        del self.pending_pvp[cid]

    async def soccer_player_v2_loop(self, context: ContextTypes.DEFAULT_TYPE, challenge_id: str):
        """Manage the loop for a Soccer V2 PvP game - Manual Emoji Submission"""
        challenge = self.pending_pvp.get(challenge_id)
        if not challenge: return

        chat_id = challenge['chat_id']
        p1_id = challenge['challenger']
        p2_id = challenge['opponent']
        p1_data = self.db.get_user(p1_id)
        p2_data = self.db.get_user(p2_id)

        while challenge['p1_points'] < challenge['pts'] and challenge['p2_points'] < challenge['pts']:
            # Round Start
            await context.bot.send_message(
                chat_id=chat_id, 
                text=f"⚽ **Round Start!**\nSeries Score: @{p1_data['username']} {challenge['p1_points']} - {challenge['p2_points']} @{p2_data['username']}\n"
                     f"👉 @{p1_data['username']}, send your {challenge['rolls']} ⚽ emoji(s) now!",
                parse_mode="Markdown"
            )

            # Reset turn rolls
            challenge['p1_turn_rolls'] = []
            challenge['p2_turn_rolls'] = []
            challenge['waiting_for_p1'] = True
            challenge['waiting_for_p2'] = False
            challenge['emoji_wait_started'] = datetime.now().isoformat()

            # We use a loop with sleep to check if both players rolled. 
            # In a real bot, we'd handle this via the event loop, but for simplicity:
            while len(challenge['p1_turn_rolls']) < challenge['rolls'] or len(challenge['p2_turn_rolls']) < challenge['rolls']:
                await asyncio.sleep(2)
                challenge = self.pending_pvp.get(challenge_id)
                if not challenge: return # Expired/Cancelled

            # Round Result
            p1_total = sum(challenge['p1_turn_rolls'])
            p2_total = sum(challenge['p2_turn_rolls'])

            win = None
            if challenge['mode'] == "normal":
                if p1_total > p2_total: win = "p1"
                elif p2_total > p1_total: win = "p2"
            else:
                if p1_total < p2_total: win = "p1"
                elif p2_total < p1_total: win = "p2"

            if win == "p1": challenge['p1_points'] += 1
            elif win == "p2": challenge['p2_points'] += 1

            await context.bot.send_message(
                chat_id=chat_id, 
                text=f"{p1_data['username']} {challenge['p1_points']} - {challenge['p2_points']} {p2_data['username']}"
            )

    async def bet_details_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View details about a specific bet ID"""
        if not context.args:
            await update.message.reply_text("Usage: /bet [bet_id]")
            return

        try:
            bet_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid Bet ID. It should be a number.")
            return

        with self.db.app.app_context():
            from models import Game
            game = db.session.get(Game, bet_id)

            if not game:
                await update.message.reply_text("❌ Bet not found.")
                return

            data = game.data
            ts = game.timestamp
            time_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "Unknown"
            wager = data.get('wager', 0.0)

            raw_type = data.get('type', 'Game')
            result = data.get('result', data.get('outcome', 'N/A')).capitalize()

            text = f"🎯 <b>Bet Details #{bet_id}</b>\n\n"
            text += f"📅 Time: <code>{time_str}</code>\n"
            text += f"🎮 Game: <b>{raw_type.replace('_', ' ').title()}</b>\n"
            text += f"💰 Wager: <code>${wager:.2f}</code>\n"
            text += f"🏆 Result: <b>{result}</b>\n"

            # Add specific details based on game type
            if 'p_pts' in data and 'b_pts' in data:
                text += f"📊 Score: <code>{data['p_pts']}-{data['b_pts']}</code>\n"
            elif 'p1_pts' in data and 'p2_pts' in data:
                text += f"📊 Score: <code>{data['p1_pts']}-{data['p2_pts']}</code>\n"

            if 'multiplier' in data:
                text += f"📈 Multiplier: <code>{data['multiplier']}x</code>\n"

            if 'payout' in data:
                text += f"💵 Payout: <code>${data['payout']:.2f}</code>\n"

            await update.message.reply_text(text, parse_mode="HTML")

    async def v2_pvp_accept_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show confirmation menu for accepting a PvP challenge"""
        query = update.callback_query
        data = query.data
        # Format: v2_pvp_accept_confirm_{game}_{wager}_{rolls}_{mode}_{pts}_{challenge_id}
        parts = data.split("_")
        if len(parts) >= 10:
            game = parts[4]
            wager = float(parts[5])
            rolls = int(parts[6])
            mode = parts[7]
            pts = int(parts[8])
            cid = parts[9]
        else:
            # Fallback for shorter format if needed
            game = parts[4]
            wager = float(parts[5])
            rolls = int(parts[6])
            mode = parts[7]
            pts = int(parts[8])
            cid = "unknown"

        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)

        if wager > user_data['balance']:
            await query.answer(f"❌ Insufficient balance! (${user_data['balance']:.2f})", show_alert=True)
            return

        text = (
            f"🎲 **Accept PvP Challenge**\n\n"
            f"Game: <b>{game.capitalize()}</b>\n"
            f"Wager: <b>${wager:.2f}</b>\n"
            f"Rolls: <b>{rolls}</b>\n"
            f"Target: <b>{pts}</b>\n"
            f"Mode: <b>{mode.capitalize()}</b>\n\n"
            f"Do you want to accept this wager?"
        )

        keyboard = [
            [InlineKeyboardButton("✅ Accept Wager", callback_data=f"v2_accept_{cid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"v2_pvp_back_{cid}")]
        ]

        sent_msg = await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all button interactions"""
        query = update.callback_query
        user_id = query.from_user.id
        chat = query.message.chat
        data = query.data
        message_id = query.message.message_id
        owner_id = self.button_ownership.get((chat.id, message_id))

        # PvP acceptance: allow anyone but the challenger to click accept
        if data.startswith("v2_accept_"):
            parts = data.split("_")
            cid = parts[2]
            challenge = self.pending_pvp.get(cid)
            if challenge and user_id == challenge.get('challenger'):
                await query.answer("❌ You cannot accept your own challenge.", show_alert=True)
                return
            # If not challenger, they can proceed (ownership will be handled in handle_pvp_acceptance)

        # Blackjack Action Buttons: bj_hit_{user_id}, bj_stand_{user_id}, etc.
        elif data.startswith("bj_") and not data.startswith("bj_play_again_"):
            parts = data.split("_")

            # Handle bj_bot_{wager} - start a new blackjack game from bet menu
            if len(parts) >= 3 and parts[1] == "bot":
                try:
                    wager = float(parts[2])
                except (IndexError, ValueError):
                    await query.answer("❌ Invalid wager!", show_alert=True)
                    return

                user_data = self.db.get_user(user_id)
                if wager > user_data['balance']:
                    await query.answer(f"❌ Insufficient balance! (${user_data['balance']:.2f})", show_alert=True)
                    return

                if user_id in self.blackjack_sessions:
                    await query.answer("❌ You already have an active Blackjack game!", show_alert=True)
                    return

                # Deduct wager
                self.db.update_user(user_id, {'balance': user_data['balance'] - wager})
                self.db.add_transaction(user_id, "blackjack_bet", -wager, f"Blackjack Bet: {wager}")

                from blackjack import BlackjackGame
                game = BlackjackGame(bet_amount=wager)
                game.start_game()
                self.blackjack_sessions[user_id] = game
                await self._display_blackjack_state(update, context, user_id)
                return

            # Handle bet change (Half/Double on game-over screen)
            if len(parts) >= 4 and parts[1] == "bet" and parts[2] == "change":
                try:
                    target_user_id = int(parts[3])
                    new_bet = float(parts[4])
                except (ValueError, IndexError):
                    return

                if user_id != target_user_id:
                    await query.answer("❌ This is not your game!", show_alert=True)
                    return

                user_data = self.db.get_user(user_id)
                balance = user_data.get('balance', 0)
                new_bet = max(1.0, min(new_bet, balance))

                keyboard = [
                    [InlineKeyboardButton("✅ Start Game", callback_data=f"bj_bot_{new_bet:.2f}")],
                    [
                        InlineKeyboardButton("Half Bet", callback_data=f"bj_bet_change_{user_id}_{max(1.0, new_bet/2):.2f}"),
                        InlineKeyboardButton(f"Bet: ${new_bet:.2f}", callback_data="dummy"),
                        InlineKeyboardButton("Double Bet", callback_data=f"bj_bet_change_{user_id}_{min(new_bet*2, balance):.2f}")
                    ],
                    [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
                ]
                try:
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
                    await query.answer(f"Bet: ${new_bet:.2f}")
                except Exception:
                    await query.answer(f"Bet: ${new_bet:.2f}")
                return

            # Handle gameplay actions: bj_action_{user_id}
            if len(parts) >= 3:
                try:
                    action = parts[1]
                    target_user_id = int(parts[2])
                except (ValueError, IndexError):
                    return

                if user_id != target_user_id:
                    await query.answer("❌ This is not your game!", show_alert=True)
                    return

                if user_id not in self.blackjack_sessions:
                    await query.answer("❌ No active game found.")
                    return

                game = self.blackjack_sessions[user_id]
                msg = ""
                if action == "hit":
                    msg = game.hit()
                elif action == "stand":
                    msg = game.stand()
                elif action == "double":
                    user_data = self.db.get_user(user_id)
                    current_hand = game.player_hands[game.current_hand_index]
                    additional_bet = current_hand['bet']
                    if user_data['balance'] < additional_bet:
                        await query.answer("❌ Insufficient balance to double down!", show_alert=True)
                        return
                    self.db.update_user(user_id, {'balance': user_data['balance'] - additional_bet})
                    self.db.add_transaction(user_id, "blackjack_double", -additional_bet, f"Blackjack Double Down")
                    msg = game.double_down()
                elif action == "split":
                    user_data = self.db.get_user(user_id)
                    current_hand = game.player_hands[game.current_hand_index]
                    additional_bet = current_hand['bet']
                    if user_data['balance'] < additional_bet:
                        await query.answer("❌ Insufficient balance to split!", show_alert=True)
                        return
                    self.db.update_user(user_id, {'balance': user_data['balance'] - additional_bet})
                    self.db.add_transaction(user_id, "blackjack_split", -additional_bet, f"Blackjack Split")
                    msg = game.split()
                elif action == "surrender":
                    msg = game.surrender()
                elif action == "insurance":
                    user_data = self.db.get_user(user_id)
                    insurance_cost = game.initial_bet / 2
                    if user_data['balance'] < insurance_cost:
                        await query.answer("❌ Insufficient balance for insurance!", show_alert=True)
                        return
                    self.db.update_user(user_id, {'balance': user_data['balance'] - insurance_cost})
                    self.db.add_transaction(user_id, "blackjack_insurance", -insurance_cost, f"Blackjack Insurance")
                    msg = game.take_insurance()

                try:
                    await query.answer()
                except Exception:
                    pass
                try:
                    await self._display_blackjack_state(update, context, user_id)
                except Exception as e:
                    logger.error(f"Error in _display_blackjack_state: {e}", exc_info=True)
                    try:
                        await query.edit_message_text(f"❌ An error occurred processing your blackjack game. Please try /blackjack again.")
                    except Exception:
                        pass
                    if user_id in self.blackjack_sessions:
                        del self.blackjack_sessions[user_id]
                return

        # Play Again callback: bj_play_again_{user_id}_{amount}
        if data.startswith("bj_play_again_"):
            parts = data.split("_")
            if len(parts) >= 5:
                target_user_id = int(parts[3])
                amount_str = parts[4]

                if user_id != target_user_id:
                    await query.answer("❌ This is not your game!", show_alert=True)
                    return

                # Mock context args to re-trigger blackjack_command
                context.args = [amount_str]
                await self.blackjack_command(update, context)
                return

        # Weekly Bonus Menu
        if data == "bonus_weekly":
            user_data = self.db.get_user(user_id)
            achievements = user_data.get('achievements', {}) or {}

            # Weekly bonus pool = rakeback accumulated this period
            bonus_pool = achievements.get('weekly_bonus_pool', 0)

            # Check if claim window is open: Saturday 9PM EST to Sunday 9PM EST
            import pytz
            est = pytz.timezone('US/Eastern')
            now_est = datetime.now(est)

            # Find last Saturday 9PM EST
            days_since_saturday = (now_est.weekday() - 5) % 7
            last_saturday = now_est - timedelta(days=days_since_saturday)
            claim_open = last_saturday.replace(hour=21, minute=0, second=0, microsecond=0)
            if claim_open > now_est:
                claim_open -= timedelta(weeks=1)
            claim_close = claim_open + timedelta(hours=24)

            # Check admin bypass
            is_claim_window = claim_open <= now_est <= claim_close
            admin_bypass = getattr(self, '_bonus_bypass', False) and self.is_admin(user_id)
            can_claim = (is_claim_window or admin_bypass) and bonus_pool > 0

            bonus_amount = round(bonus_pool, 2)

            # Time info
            if is_claim_window:
                time_left = claim_close - now_est
                hours_left = int(time_left.total_seconds() // 3600)
                mins_left = int((time_left.total_seconds() % 3600) // 60)
                time_text = f"⏰ Claim window open! <b>{hours_left}h {mins_left}m</b> remaining"
            else:
                next_open = claim_open + timedelta(weeks=1)
                time_left = next_open - now_est
                days_left = time_left.days
                hours_left = int((time_left.total_seconds() % 86400) // 3600)
                time_text = f"🔒 Next claim: <b>Saturday 9:00 PM EST</b> (in {days_left}d {hours_left}h)"

            weekly_text = (
                "🎁 <b>Weekly Bonus</b>\n\n"
                "Get a percentage of fees from your games as a bonus!\n"
                "You can claim it or try to double it with a dice roll.\n\n"
                "<b>Dice Multipliers:</b>\n"
                "🎲 1 = 0x  |  2 = 0.5x  |  3 = 1x\n"
                "🎲 4 = 1x  |  5 = 1.5x  |  6 = 2x\n\n"
                f"{time_text}\n\n"
                f"🎁 Bonus: <b>${bonus_amount:,.2f}</b>"
            )

            if can_claim:
                keyboard = [
                    [
                        InlineKeyboardButton(f"🎁 Claim ${bonus_amount:.2f}", callback_data=f"bonus_weekly_claim_{bonus_amount:.2f}"),
                        InlineKeyboardButton("🎲 Try To Double", callback_data=f"bonus_weekly_double_{bonus_amount:.2f}")
                    ],
                    [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]
                ]
            else:
                keyboard = [
                    [
                        InlineKeyboardButton("🔒 Claim Bonus 🔒", callback_data="bonus_weekly_locked"),
                        InlineKeyboardButton("🔒 Try To Double 🔒", callback_data="bonus_weekly_locked")
                    ],
                    [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]
                ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(weekly_text, reply_markup=reply_markup, parse_mode="HTML")
            return

        if data == "bonus_weekly_locked":
            await query.answer("🔒 Bonus can only be claimed on Saturday 9PM - Sunday 9PM EST!", show_alert=True)
            return

        if data == "bonus_weekly_none":
            await query.answer("Play some games first to earn a bonus!", show_alert=True)
            return

        if data.startswith("bonus_weekly_claim_"):
            bonus = float(data.split("_")[-1])
            if bonus <= 0:
                await query.answer("❌ No bonus to claim!", show_alert=True)
                return
            user_data = self.db.get_user(user_id)
            achievements = user_data.get('achievements', {}) or {}

            # Verify claim window or admin bypass
            import pytz
            est = pytz.timezone('US/Eastern')
            now_est = datetime.now(est)
            days_since_saturday = (now_est.weekday() - 5) % 7
            last_saturday = now_est - timedelta(days=days_since_saturday)
            claim_open = last_saturday.replace(hour=21, minute=0, second=0, microsecond=0)
            if claim_open > now_est:
                claim_open -= timedelta(weeks=1)
            claim_close = claim_open + timedelta(hours=24)

            admin_bypass = getattr(self, '_bonus_bypass', False) and self.is_admin(user_id)
            if not (claim_open <= now_est <= claim_close) and not admin_bypass:
                await query.answer("🔒 Claim window has closed!", show_alert=True)
                return

            # Clear the pool and pay out
            achievements['weekly_bonus_pool'] = 0
            self.db.update_user(user_id, {
                'balance': user_data['balance'] + bonus,
                'achievements': achievements
            })
            self.db.add_transaction(user_id, "weekly_bonus", bonus, f"Weekly Bonus Claim: ${bonus:.2f}")
            await query.answer(f"🎉 Claimed ${bonus:.2f}!", show_alert=True)
            # Refresh
            query.data = "bonus_weekly"
            await self.button_callback(update, context)
            return

        if data.startswith("bonus_weekly_double_"):
            bonus = float(data.split("_")[-1])
            if bonus <= 0:
                await query.answer("❌ No bonus to double!", show_alert=True)
                return

            user_data = self.db.get_user(user_id)
            achievements = user_data.get('achievements', {}) or {}

            # Verify claim window or admin bypass
            import pytz
            est = pytz.timezone('US/Eastern')
            now_est = datetime.now(est)
            days_since_saturday = (now_est.weekday() - 5) % 7
            last_saturday = now_est - timedelta(days=days_since_saturday)
            claim_open = last_saturday.replace(hour=21, minute=0, second=0, microsecond=0)
            if claim_open > now_est:
                claim_open -= timedelta(weeks=1)
            claim_close = claim_open + timedelta(hours=24)

            admin_bypass = getattr(self, '_bonus_bypass', False) and self.is_admin(user_id)
            if not (claim_open <= now_est <= claim_close) and not admin_bypass:
                await query.answer("🔒 Claim window has closed!", show_alert=True)
                return

            # Remove both buttons
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

            # Send dice
            chat_id = query.message.chat_id
            sent_dice = await context.bot.send_dice(chat_id=chat_id, emoji="🎲")
            dice_val = sent_dice.dice.value

            multipliers = {1: 0, 2: 0.5, 3: 1, 4: 1, 5: 1.5, 6: 2}
            mult = multipliers.get(dice_val, 1)
            final_bonus = round(bonus * mult, 2)

            await asyncio.sleep(4)  # Wait for animation

            # Clear pool regardless of outcome
            achievements['weekly_bonus_pool'] = 0

            if final_bonus > 0:
                self.db.update_user(user_id, {
                    'balance': user_data['balance'] + final_bonus,
                    'achievements': achievements
                })
                self.db.add_transaction(user_id, "weekly_bonus_double", final_bonus, f"Weekly Bonus Double: {dice_val} ({mult}x)")
                result_text = f"🎲 Rolled <b>{dice_val}</b> ({mult}x)\n\n🎉 You won <b>${final_bonus:.2f}</b>!"
            else:
                self.db.update_user(user_id, {'achievements': achievements})
                result_text = f"🎲 Rolled <b>{dice_val}</b> (0x)\n\n😔 Better luck next time!"

            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
            await context.bot.send_message(chat_id=chat_id, text=result_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        # Level Up Bonus Menu
        if data == "bonus_levelup":
            user_data = self.db.get_user(user_id)
            total_wagered = user_data.get('total_wagered', 0)
            achievements = user_data.get('achievements', {}) or {}
            claimed_levels = achievements.get('claimed_levels', [])

            # Level tiers: (name, wager_threshold, bonus_amount)
            LEVELS = [
                ("🥉 Bronze I", 0, 0),
                ("🥉 Bronze II", 1000, 5),
                ("🥉 Bronze III", 2500, 10),
                ("🥉 Bronze IV", 5000, 15),
                ("🥉 Bronze V", 7500, 20),
                ("🥈 Silver I", 10000, 25),
                ("🥈 Silver II", 15000, 35),
                ("🥈 Silver III", 25000, 50),
                ("🥈 Silver IV", 50000, 75),
                ("🥈 Silver V", 75000, 100),
                ("🥇 Gold I", 100000, 150),
            ]

            # Find current level
            current_idx = 0
            for i, (name, threshold, bonus) in enumerate(LEVELS):
                if total_wagered >= threshold:
                    current_idx = i

            current_name, current_threshold, _ = LEVELS[current_idx]

            # Find next level
            next_idx = min(current_idx + 1, len(LEVELS) - 1)
            next_name, next_threshold, next_bonus = LEVELS[next_idx]

            # Find next unclaimed level bonus
            claimable_level = None
            claimable_bonus = 0
            for i, (name, threshold, bonus) in enumerate(LEVELS):
                if i == 0:
                    continue  # Bronze I has no bonus
                if total_wagered >= threshold and str(i) not in claimed_levels:
                    claimable_level = i
                    claimable_bonus = bonus
                    break

            wager_to_upgrade = max(0, next_threshold - total_wagered)

            levelup_text = (
                "🌲 <b>Level Up Bonus</b>\n\n"
                "Play games, level up and get even more bonuses!\n\n"
                "Your current level:\n"
                f"<b>{current_name} - ${total_wagered:,.0f} wagered</b>\n\n"
            )

            if current_idx < len(LEVELS) - 1:
                levelup_text += (
                    "Next Level:\n"
                    f"<b>{next_name} - ${next_threshold:,.0f} wagered</b>\n\n"
                    f"Wager <b>${wager_to_upgrade:,.0f}</b> more to upgrade your level!"
                )
            else:
                levelup_text += "🏆 You've reached the highest level!"

            keyboard = []
            if claimable_level is not None:
                keyboard.append([InlineKeyboardButton(f"🎁 Claim ${claimable_bonus:,.0f} Bonus 🎁", callback_data=f"bonus_levelup_claim_{claimable_level}")])
            else:
                keyboard.append([InlineKeyboardButton(f"🔒 Claim ${next_bonus:,.0f} Bonus 🔒", callback_data="bonus_levelup_claim_locked")])
            keyboard.append([InlineKeyboardButton("Levels List", callback_data="bonus_levels_list")])
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(levelup_text, reply_markup=reply_markup, parse_mode="HTML")
            return

        # Handle level up bonus claim
        if data.startswith("bonus_levelup_claim_") and data != "bonus_levelup_claim_locked":
            level_idx = int(data.split("_")[-1])
            user_data = self.db.get_user(user_id)
            total_wagered = user_data.get('total_wagered', 0)
            achievements = user_data.get('achievements', {}) or {}
            claimed_levels = achievements.get('claimed_levels', [])

            LEVELS = [
                ("🥉 Bronze I", 0, 0),
                ("🥉 Bronze II", 1000, 5),
                ("🥉 Bronze III", 2500, 10),
                ("🥉 Bronze IV", 5000, 15),
                ("🥉 Bronze V", 7500, 20),
                ("🥈 Silver I", 10000, 25),
                ("🥈 Silver II", 15000, 35),
                ("🥈 Silver III", 25000, 50),
                ("🥈 Silver IV", 50000, 75),
                ("🥈 Silver V", 75000, 100),
                ("🥇 Gold I", 100000, 150),
            ]

            if level_idx >= len(LEVELS):
                await query.answer("❌ Invalid level!", show_alert=True)
                return

            level_name, threshold, bonus = LEVELS[level_idx]

            if total_wagered < threshold:
                await query.answer(f"❌ You need ${threshold:,.0f} wagered to claim this!", show_alert=True)
                return

            if str(level_idx) in claimed_levels:
                await query.answer("❌ Already claimed!", show_alert=True)
                return

            # Claim the bonus
            claimed_levels.append(str(level_idx))
            achievements['claimed_levels'] = claimed_levels
            self.db.update_user(user_id, {
                'balance': user_data['balance'] + bonus,
                'achievements': achievements
            })
            self.db.add_transaction(user_id, "level_bonus", bonus, f"Level Up Bonus: {level_name}")

            await query.answer(f"🎉 Claimed ${bonus:,.0f} for reaching {level_name}!", show_alert=True)

            # Refresh the levelup page
            # Re-trigger the bonus_levelup display
            query.data = "bonus_levelup"
            await self.button_callback(update, context)
            return

        if data == "bonus_levelup_claim_locked":
            await query.answer("🔒 Keep wagering to unlock this bonus!", show_alert=True)
            return

        # Levels List Menu
        if data == "bonus_levels_list":
            levels_text = (
                "📊 <b>Levels List</b>\n\n"
                "🥉 <b>Bronze I</b>: $0 wagered\n"
                "🥉 <b>Bronze II</b>: $1,000 wagered\n"
                "🥉 <b>Bronze III</b>: $2,500 wagered\n"
                "🥉 <b>Bronze IV</b>: $5,000 wagered\n"
                "🥉 <b>Bronze V</b>: $7,500 wagered\n"
                "🥈 <b>Silver I</b>: $10,000 wagered\n"
                "🥈 <b>Silver II</b>: $15,000 wagered\n"
                "🥈 <b>Silver III</b>: $25,000 wagered\n"
                "🥈 <b>Silver IV</b>: $50,000 wagered\n"
                "🥈 <b>Silver V</b>: $75,000 wagered\n"
                "🥇 <b>Gold I</b>: $100,000 wagered\n\n"
                "Keep playing to climb the ranks and unlock bigger rewards!"
            )

            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bonus_levelup")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(levels_text, reply_markup=reply_markup, parse_mode="HTML")
            return

        # Bonus main menu (back button from weekly/levelup)
        if data == "bonus_main":
            bonus_text = (
                "🎁 <b>Bonus</b>\n\n"
                "In this section you can find bonuses that you can get by playing games!\n\n"
                "💎 <b>Weekly Bonus</b>\n"
                "Play different games during the week and claim your bonus every Saturday. Just don't slip up or the bonus will burn out!\n\n"
                "💎 <b>Level Up Bonus</b>\n"
                "Play games, level up and earn money!"
            )

            keyboard = [
                [
                    InlineKeyboardButton("🎁 Weekly Bonus", callback_data="bonus_weekly"),
                    InlineKeyboardButton("🎁 Level Up Bonus", callback_data="bonus_levelup")
                ],
                [InlineKeyboardButton("⬅️ Back", callback_data="start_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(bonus_text, reply_markup=reply_markup, parse_mode="HTML")
            return

        # Start menu back button
        if data == "start_back":
            # Re-trigger start command via message-like handling if needed, 
            # but usually it just shows the main menu again.
            # For simplicity, we can just call the start_command with a dummy update/context
            # or just edit the message to main menu text.
            # Assuming main menu text is what we want here.
            await self.start_command(update, context)
            return

        if owner_id and owner_id != user_id:
            await query.answer("❌ This menu isn't for you.", show_alert=True)
            return

        data = query.data

        # Handle Withdraw button from balance menu
        if data == "withdraw_mock":
            user_data = self.db.get_user(user_id)
            withdraw_text = f"Your balance <b>${user_data['balance']:,.2f}</b>\n\n🟢 Select withdrawal currency"

            keyboard = [
                [InlineKeyboardButton("Litecoin", callback_data="wit_ltc")],
                [InlineKeyboardButton("Bitcoin", callback_data="wit_btc"),
                 InlineKeyboardButton("Ethereum", callback_data="wit_eth")],
                [InlineKeyboardButton("USDT", callback_data="wit_usdt"),
                 InlineKeyboardButton("USDC", callback_data="wit_usdc")],
                [InlineKeyboardButton("Solana", callback_data="wit_sol"),
                 InlineKeyboardButton("BNB", callback_data="wit_bnb")],
                [InlineKeyboardButton("Monero", callback_data="wit_xmr"),
                 InlineKeyboardButton("Toncoin", callback_data="wit_ton")],
                [InlineKeyboardButton("⬅️ Back", callback_data="start_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if chat.type in ["group", "supergroup"]:
                try:
                    await query.answer()

                    # Edit the balance message to notification message
                    notification_text = f"Hey {self.get_mention(user_id, query.from_user.first_name)}, I've sent you a private message with instructions on how to withdraw!"
                    await query.edit_message_text(text=notification_text, parse_mode="HTML")

                    # Send private message with currency selection
                    await self.app.bot.send_message(
                        chat_id=user_id,
                        text=withdraw_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )

                    # Schedule deletion of both messages
                    async def cleanup_messages(chat_id, msg_to_delete, user_msg_to_delete):
                        # Delete user's / message immediately
                        if user_msg_to_delete:
                            try:
                                await context.bot.delete_message(chat_id=chat_id, message_id=user_msg_to_delete)
                            except:
                                pass

                        await asyncio.sleep(5)
                        # Delete notification message
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=msg_to_delete)
                        except:
                            pass

                    # Try to get the user command message ID from user_data first
                    user_msg_id = context.user_data.get(f"cmd_msg_{query.message.message_id}")
                    if not user_msg_id and query.message.reply_to_message:
                        user_msg_id = query.message.reply_to_message.message_id

                    asyncio.create_task(cleanup_messages(chat.id, query.message.message_id, user_msg_id))
                except Exception as e:
                    logger.error(f"Error in group withdraw button: {e}")
                    await query.answer("❌ Please start a private chat with me first.", show_alert=True)
                return
            else:
                # In private chat, edit current message to show currency selection
                await query.answer()
                await query.edit_message_text(withdraw_text, reply_markup=reply_markup, parse_mode="HTML")
                return

        if data == "start_back":
            await self.start_command(update, context)
            return

        if data == "menu_settings":
            keyboard = [
                [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en"),
                 InlineKeyboardButton("🇷🇺 Russian", callback_data="lang_ru")],
                [InlineKeyboardButton("⬅️ Back", callback_data="start_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("⚙️ <b>Settings</b>\n\nSelect your language:", reply_markup=reply_markup, parse_mode="HTML")
            return

        if data == "menu_deposit":
            await self.deposit_command(update, context)
            return

        if data == "menu_withdraw":
            await self.withdraw_command(update, context)
            return

        if data == "menu_bonus":
            await self.bonus_command(update, context)
            return

        if data == "menu_more":
            keyboard = [
                [InlineKeyboardButton("📊 Stats", callback_data="menu_stats")],
                [InlineKeyboardButton("👥 Referrals", callback_data="menu_referrals")],
                [InlineKeyboardButton("🏆 Contests", callback_data="menu_contests")],
                [InlineKeyboardButton("⬅️ Back", callback_data="start_back")]
            ]
            await query.edit_message_text("📁 <b>More Content</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "collect_rakeback":
            user_data = self.db.get_user(user_id)
            rakeback = user_data.get('rakeback_balance', 0)
            if rakeback <= 0:
                await query.answer("❌ You have no rakeback to collect!", show_alert=True)
                return
            
            # Reset rakeback and add to balance
            self.db.update_user(user_id, {
                'balance': user_data['balance'] + rakeback,
                'rakeback_balance': 0.0
            })
            self.db.add_transaction(user_id, "rakeback_claim", rakeback, f"Claimed ${rakeback:.2f} rakeback")
            
            await query.answer(f"✅ Collected ${rakeback:,.2f} rakeback!", show_alert=True)
            # Refresh bonus menu
            await self.bonus_command(update, context)
            return

        if data == "menu_stats":
            user_data = self.db.get_user(user_id)
            username = query.from_user.username or query.from_user.first_name
            if username.startswith('@'): username = username[1:]

            stats_text = self._build_stats_text(user_id, username, user_data)

            keyboard = [
                [InlineKeyboardButton("📅 Match History", callback_data="matches_page_0")]
            ]
            # Removed back button as requested
            await query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "menu_contests":
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_more")]]
            await query.edit_message_text("🏆 <b>Contests</b>\n\n🔜 Coming soon! Stay tuned.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "menu_referrals":
            user_data = self.db.get_user(user_id)
            ref_code = user_data.get('referral_code', '')
            ref_count = user_data.get('referral_count', 0)
            ref_earnings = user_data.get('referral_earnings', 0)

            bot_username = (await context.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start=ref_{ref_code}" if ref_code else "No referral code"

            ref_text = (
                f"👥 <b>Referrals</b>\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"Your link:\n<code>{ref_link}</code>\n\n"
                f"Referrals:  <b>{ref_count}</b>\n"
                f"Earnings:  <b>${ref_earnings:,.2f}</b>\n\n"
                f"Share your link and earn from your referrals' wagers!"
            )

            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_more")]]
            await query.edit_message_text(ref_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "menu_matches":
            from sqlalchemy import or_, cast, String
            user_data = self.db.get_user(user_id)
            with self.db.app.app_context():
                from models import Game
                games = Game.query.filter(or_(
                    cast(Game.data['player_id'], String) == str(user_id),
                    cast(Game.data['challenger'], String) == str(user_id)
                )).order_by(Game.timestamp.desc()).limit(15).all()

            if not games:
                matches_text = "📅 <b>Matches History</b>\n\nNo matches found."
            else:
                matches_text = "📅 <b>Matches History</b>\n\n"
                for match in games:
                    game_data = match.data
                    date_str = match.timestamp.strftime("%m/%d %H:%M")
                    game_type = game_data.get('type', 'unknown').upper()
                    wager = game_data.get('wager', 0)
                    winner = game_data.get('winner')
                    if winner == user_id:
                        res_emoji = "✅"
                    elif winner is not None:
                        res_emoji = "❌"
                    else:
                        res_emoji = "➖"
                    matches_text += f"{res_emoji} {game_type} | ${wager:.2f} | {date_str}\n"

            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_more")]]
            await query.edit_message_text(matches_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data.startswith("matches_page_"):
            page = int(data.split("_")[2])
            await self._show_matches_page(update, context, user_id, page, edit=True)
            return

        if data == "menu_leaderboard":
            await self._show_leaderboard_menu(query, "most_wagered")
            return

        if data == "lb_most_wagered":
            await self._show_leaderboard_menu(query, "most_wagered")
            return

        if data == "lb_biggest_week":
            await self._show_leaderboard_menu(query, "biggest_week")
            return

        if data == "lb_biggest_alltime":
            await self._show_leaderboard_menu(query, "biggest_alltime")
            return

        # Handle Admin Withdrawal Actions
        if data.startswith("adm_wit_"):
            if not self.is_admin(user_id):
                await query.answer("❌ Admin only!", show_alert=True)
                return

            parts = data.split("_")
            action = parts[2]
            target_user_id = int(parts[3])
            amount = float(parts[4])

            if action == "approve":
                # Mark as processed in DB
                pending = self.db.data.get('pending_withdrawals', [])
                target_username = "User"
                for wit in pending:
                    if wit['user_id'] == target_user_id and wit.get('status') == 'pending' and wit['amount'] == amount:
                        wit['status'] = 'processed'
                        target_username = wit.get('username', "User")
                        break
                self.db.data['pending_withdrawals'] = pending

                # Clickable mention without @ for the approval message in group
                target_mention = f'<a href="tg://user?id={target_user_id}">{target_username}</a>'
                await query.edit_message_text(f"✅ Withdrawal of ${amount:,.2f} for user {target_mention} approved!", parse_mode="HTML")
                # Notify user
                try:
                    await self.app.bot.send_message(target_user_id, f"✅ Your withdrawal of **${amount:,.2f}** has been approved and sent!")
                except:
                    pass

            elif action == "deny":
                # Mark as denied and REFUND balance
                pending = self.db.data.get('pending_withdrawals', [])
                target_username = "User"
                for wit in pending:
                    if wit['user_id'] == target_user_id and wit.get('status') == 'pending' and wit['amount'] == amount:
                        wit['status'] = 'denied'
                        target_username = wit.get('username', "User")
                        break
                self.db.data['pending_withdrawals'] = pending

                # Refund
                target_user_data = self.db.get_user(target_user_id)
                target_user_data['balance'] += amount
                self.db.update_user(target_user_id, target_user_data)

                # Clickable mention without @ for the denial message in group
                target_mention = f'<a href="tg://user?id={target_user_id}">{target_username}</a>'
                await query.edit_message_text(f"❌ Withdrawal of ${amount:,.2f} for user {target_mention} denied. Balance refunded.", parse_mode="HTML")
                # Notify user
                try:
                    await self.app.bot.send_message(target_user_id, f"your withdraw of {amount:,.2f} was unsuccesful. Your balance has been refunded")
                except:
                    pass
            return

        # Handle Currency selection for withdrawal
        if data.startswith("wit_"):
            currency = data.split("_")[1].upper()
            context.user_data['wit_currency'] = currency
            user_data = self.db.get_user(user_id)

            # Message as requested from screenshot (without emojis except back)
            withdraw_info_text = (
                f"Enter withdrawal amount\n"
                f"Withdrawal fee: $0.01 + 2.00%\n\n"
                f"Current balance: ${user_data['balance']:,.2f}"
            )

            context.user_data['awaiting_wit_amount'] = True
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="withdraw_mock")],
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="start_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.answer()
            await query.edit_message_text(withdraw_info_text, reply_markup=reply_markup)
            return

        # Handle Back to balance menu
        if data == "balance_menu":
            await self.balance_command(update, context)
            return

        # Handle Deposit button from balance menu
        if data == "deposit_mock":
            if chat.type in ["group", "supergroup"]:
                try:
                    await query.answer()

                    # Edit the balance message to notification message
                    notification_text = f"Hey {self.get_mention(user_id, query.from_user.first_name)}, I've sent you a private message with instructions on how to deposit!"
                    await query.edit_message_text(text=notification_text, parse_mode="HTML")

                    # Fetch live LTC rate
                    ltc_usd_rate = await self.get_live_rate("litecoin")
                    user_data = self.db.get_user(user_id)

                    # Get LTC address from environment
                    ltc_address = os.environ.get("LTC_ADDRESS", "YOUR_LTC_ADDRESS_HERE")

                    deposit_text = f"""
💳 **LTC Deposit Request**

Your balance **${user_data['balance']:,.2f}**

To deposit, send LTC to the address below:
`{ltc_address}`
"""
                    # Send private message
                    await self.app.bot.send_message(
                        chat_id=user_id,
                        text=deposit_text,
                        parse_mode="Markdown"
                    )

                    # Schedule deletion of both messages
                    async def cleanup_messages(chat_id, msg_to_delete, user_msg_to_delete):
                        # Delete user's / message immediately
                        if user_msg_to_delete:
                            try:
                                await context.bot.delete_message(chat_id=chat_id, message_id=user_msg_to_delete)
                            except:
                                pass

                        await asyncio.sleep(5)
                        # Delete notification message
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=msg_to_delete)
                        except:
                            pass

                    # Try to get the user command message ID from user_data first
                    user_msg_id = context.user_data.get(f"cmd_msg_{query.message.message_id}")
                    if not user_msg_id and query.message.reply_to_message:
                        user_msg_id = query.message.reply_to_message.message_id

                    asyncio.create_task(cleanup_messages(chat.id, query.message.message_id, user_msg_id))
                except Exception as e:
                    logger.error(f"Error in group deposit button: {e}")
                    await query.answer("❌ Please start a private chat with me first.", show_alert=True)
                return
            else:
                # In private chat, show deposit info (existing logic would be here)
                # For now, let's implement it here as well since it was likely using balance_command redirection
                ltc_usd_rate = await self.get_live_rate("litecoin")
                user_data = self.db.get_user(user_id)
                ltc_address = os.environ.get("LTC_ADDRESS", "YOUR_LTC_ADDRESS_HERE")

                deposit_text = f"""
💳 **LTC Deposit Request**

Your balance **${user_data['balance']:,.2f}**

To deposit, send LTC to the address below:
`{ltc_address}`
"""
                keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="start_back")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.answer()
                await query.edit_message_text(deposit_text, reply_markup=reply_markup, parse_mode="Markdown")
                return

        """Handles all inline button presses."""
        query = update.callback_query
        if not query:
            return

        # Ensure user is registered and username is updated
        self.ensure_user_registered(update)

        data = query.data
        user_id = query.from_user.id
        chat_id = query.message.chat_id
        message_id = query.message.message_id

        # Check if button was already clicked (prevent spam)
        # We EXEMPT everything related to setup or prediction menus from this check
        setup_prefixes = [
            "setup_mode_", "setup_bet_", "setup_predict_", "setup_cancel_", 
            "predict_start_", "v2_bot_", "v2_pvp_", "v2_accept_",
            "emoji_setup_", "setup_roll_", "flip_bot_", "setup_mode_normal_", "setup_mode_crazy_",
            "bj_bet_change_", "bj_bot_", "bj_hit_", "bj_stand_", "bj_double_", "bj_split_",
            "matches_page_", "bonus_levelup", "bonus_weekly", "bonus_main",
        ]
        is_setup_button = any(data.startswith(prefix) for prefix in setup_prefixes) or data.startswith("setup_predict_select_")

        if not is_setup_button:
            button_key = (chat_id, message_id, data)
            if button_key in self.clicked_buttons:
                await query.answer("❌ This button has already been used!", show_alert=True)
                return

        if data.startswith("v2_pvp_create_"):
            # Mark the button as clicked to prevent further interaction with this setup menu
            self.clicked_buttons.add(button_key)
            parts = data.split("_")
            game, wager, rolls, mode, pts = parts[3], float(parts[4]), int(parts[5]), parts[6], int(parts[7])
            await self.start_generic_v2_pvp(update, context, game, wager, rolls, mode, pts)
            return

        if data.startswith("v2_pvp_accept_confirm_"):
            await self.v2_pvp_accept_confirm(update, context)
            return

        elif data.startswith("v2_pvp_back_"):
            cid = data.replace("v2_pvp_back_", "")
            challenge = self.pending_pvp.get(cid)
            if not challenge:
                await query.answer("❌ Challenge no longer exists!", show_alert=True)
                return

            # Re-render the initial join challenge message
            game = challenge.get('game', 'dice')
            wager = challenge.get('wager', 1.0)
            pts = challenge.get('pts', 1)
            mode = challenge.get('mode', 'normal')
            emoji = challenge.get('emoji', '🎲')
            challenger_data = self.db.get_user(challenge['challenger'])

            keyboard = [[InlineKeyboardButton("Join Challenge", callback_data=f"v2_pvp_accept_confirm_{game}_{wager:.2f}_{challenge['rolls']}_{mode}_{pts}_{cid}")]]
            msg_text = f"{emoji} **{game.capitalize()} PvP**\nChallenger: @{challenger_data.get('username', 'User')}\nWager: ${wager:.2f}\nMode: {mode.capitalize()}\nTarget: {pts}\n\nClick below to join!"
            sent_msg = await query.edit_message_text(text=msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            # Public button, no ownership required but we track it if needed
            # self.button_ownership[(chat_id, sent_msg.message_id)] = user_id 
            return

        # Check button ownership
        public_buttons = ["v2_accept_", "lb_page_", "transactions_history", "deposit_mock", "withdraw_mock"]
        is_public = any(data.startswith(prefix) for prefix in public_buttons)

        # Ownership: ensure ALL interactive buttons (Blackjack, Roulette, Games, Menus) are registered in `self.button_ownership`.
        # This prevents users from interacting with someone else's game.

        # 1. Register ownership for all sent messages with InlineKeyboardMarkup
        # 2. Check ownership in button_callback for all non-public buttons

        ownership_key = (chat_id, message_id)

        # DEBUG LOGGING (optional, but good for tracking)
        # logger.debug(f"Button Callback: user={user_id}, msg={message_id}, chat={chat_id}, data={data}, owner={self.button_ownership.get(ownership_key)}")

        if not is_public and ownership_key in self.button_ownership:
            owner_id = self.button_ownership[ownership_key]
            if owner_id != user_id:
                await query.answer("❌ This button is not for you!", show_alert=True)
                return

        await query.answer()

        # Mark button as clicked for game buttons (not setup buttons)
        if not is_setup_button:
            if any(data.startswith(prefix) for prefix in ["v2_accept_", "claim_daily_bonus", "flip_bot_"]):
                self.clicked_buttons.add((chat_id, message_id, data))

        try:
            if data == "none":
                try:
                    await query.answer()
                except:
                    pass
                return

            if data.startswith("v2_cancel_"):
                cid = data.replace("v2_cancel_", "")
                challenge = self.pending_pvp.get(cid)
                if challenge:
                    # Refund players
                    wager = challenge.get('wager', 0)
                    if cid.startswith("v2_bot_"):
                        pid = challenge.get('player')
                        if pid and challenge.get('wager_deducted'):
                            user_data = self.db.get_user(pid)
                            user_data['balance'] += wager
                            self.db.update_user(pid, user_data)
                    elif cid.startswith("v2_pvp_"):
                        p1, p2 = challenge.get('challenger'), challenge.get('opponent')
                        if p1 and challenge.get('p1_deducted'):
                            user_data = self.db.get_user(p1)
                            user_data['balance'] += wager
                            self.db.update_user(p1, user_data)
                        if p2 and challenge.get('p2_deducted'):
                            user_data = self.db.get_user(p2)
                            user_data['balance'] += wager
                            self.db.update_user(p2, user_data)

                    del self.pending_pvp[cid]
                    self.db.update_pending_pvp(self.pending_pvp)

                    # Try to delete the original command message if it exists
                    try:
                        cmd_msg_id = challenge.get('message_id') # Original command message id is stored here
                        if cmd_msg_id:
                            await context.bot.delete_message(chat_id=chat_id, message_id=cmd_msg_id)
                    except Exception as e:
                        logger.debug(f"Could not delete original command message: {e}")

                    await query.edit_message_text(f"❌ Game cancelled and wager refunded.")
                else:
                    await query.answer("❌ Game no longer exists!", show_alert=True)
                return

            if data.startswith("emoji_setup_") or data.startswith("v2_send_emoji_"):
                from roll_handler import handle_roll
                await handle_roll(self, update, context)
                return

            # Emoji game setup callbacks
            if data.startswith("v2_send_emoji_"):
                # Mark it here too to be absolutely sure
                self.clicked_buttons.add(button_key)
                parts = data.split("_")

                # Format: v2_bot_{game}_{wager}_{rolls}_{mode}_{pts}
                if len(parts) >= 7 and parts[1] == "bot":
                    # Remove the button immediately when user clicks "Send emoji"
                    try:
                        await query.edit_message_reply_markup(reply_markup=None)
                    except Exception as e:
                        logger.debug(f"Error removing reply markup: {e}")

                    g_mode = parts[2]
                    wager = float(parts[3])
                    rolls = int(parts[4])
                    mode = parts[5]
                    pts = int(parts[6])

                    # Call the bot start function which handles the actual game logic
                    # IMPORTANT: start_generic_v2_bot now uses send_message instead of edit_message
                    await self.start_generic_v2_bot(update, context, g_mode, wager, rolls, mode, pts)
                    return

                # Format: v2_send_emoji_bot_{g_mode}_{wager}_{rolls}_{mode}_{pts}
                # OR v2_send_emoji_{cid}
                if len(parts) > 3 and parts[2] == "bot":
                    # Remove the button immediately
                    try:
                        await query.edit_message_reply_markup(reply_markup=None)
                    except Exception as e:
                        logger.debug(f"Error removing reply markup: {e}")

                    g_mode = parts[3]
                    wager = float(parts[4])
                    rolls = int(parts[5])
                    mode = parts[6]
                    pts = int(parts[7])

                    # Call the bot start function which handles the actual game logic
                    await self.start_generic_v2_bot(update, context, g_mode, wager, rolls, mode, pts)
                    return

                cid = data.replace("v2_send_emoji_", "")
                challenge = self.pending_pvp.get(cid)
                if not challenge or challenge.get('player') != user_id:
                    await query.answer("❌ Game no longer valid.", show_alert=True)
                    return

                await query.answer()
                # Remove the button
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception as e:
                    logger.error(f"Error removing reply markup: {e}")

                # Mark that bot is rolling to ignore manual rolls
                challenge['bot_is_rolling'] = True
                self.db.update_pending_pvp(self.pending_pvp)

                emoji = challenge['emoji']
                # Send emojis for user based on number of rolls
                num_rolls = challenge.get('rolls', 1)
                pts = challenge.get('pts', 1)

                for i in range(num_rolls):
                    try:
                        msg = await context.bot.send_dice(chat_id=chat_id, emoji=emoji)
                        val = msg.dice.value
                        score = (1 if val >= 4 else 0) if emoji in ["⚽", "🏀"] else val
                        challenge['p_rolls'].append(score)
                        challenge['cur_rolls'] += 1
                        self.db.update_pending_pvp(self.pending_pvp)
                    except Exception as e:
                        logger.error(f"Error sending dice: {e}")

                # After rolls are complete, trigger resolution
                # For single-point games after a draw, num_rolls=1
                # This ensures resolve_bot_game is called to calculate the new round result
                await self.resolve_bot_game(update, context, cid)
                return

                await asyncio.sleep(4)

                # Re-load challenge for safety
                self.pending_pvp = self.db.data.get('pending_pvp', {})
                challenge = self.pending_pvp.get(cid)
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
                        self.db.update_pending_pvp(self.pending_pvp)
                    except Exception as e:
                        logger.warning(f"Failed to remove button from old cashout message: {e}")

                # await context.bot.send_message(chat_id=chat_id, text=f"<b>Rukia</b>, your turn!", parse_mode="HTML")

                # Bot rolls
                challenge['b_rolls'] = [] # Clear bot rolls for the round
                for _ in range(challenge['rolls']):
                    try:
                        d = await context.bot.send_dice(chat_id=chat_id, emoji=emoji)
                        val = d.dice.value
                        score = (1 if val >= 4 else 0) if emoji in ["⚽", "🏀"] else val
                        challenge['b_rolls'].append(score)
                    except Exception as e:
                        logger.error(f"Error sending bot dice: {e}")

                # Re-calculate b_tot from the rolls we just made
                b_tot = sum(challenge['b_rolls'])

                # Save bot progress
                self.db.update_pending_pvp(self.pending_pvp)

                # Wait for bot dice animation to finish
                await asyncio.sleep(4)

                # Re-load challenge for safety to get the absolute latest state
                self.pending_pvp = self.db.data.get('pending_pvp', {})
                challenge = self.pending_pvp.get(cid)
                if not challenge:
                    logger.error(f"Challenge {cid} not found after rolls")
                    return

                # RE-CALCULATE totals from the persistent rolls right before comparison
                # This is critical because challenge['p_rolls'] and challenge['b_rolls'] 
                # are the source of truth
                current_p_rolls = challenge.get('p_rolls', [])
                current_b_rolls = challenge.get('b_rolls', [])
                p_tot = sum(current_p_rolls)
                b_tot = sum(current_b_rolls)

                # Resolve Round/Series
                round_win = None
                if challenge.get('mode', 'normal') == "normal":
                    if p_tot > b_tot: round_win = "p"
                    elif b_tot > p_tot: round_win = "b"
                    else: round_win = "draw"
                else:
                    if p_tot < b_tot: round_win = "p"
                    elif b_tot < p_tot: round_win = "b"
                    else: round_win = "draw"

                # RE-LOAD BEFORE INCREMENTING to ensure we have the most accurate pts
                self.pending_pvp = self.db.data.get('pending_pvp', {})
                challenge = self.pending_pvp.get(cid)
                if not challenge: return

                if round_win == "p":
                    challenge['p_pts'] += 1
                elif round_win == "b":
                    challenge['b_pts'] += 1

                # Update database IMMEDIATELY after incrementing points
                self.db.update_pending_pvp(self.pending_pvp)

                if round_win == "draw":
                    # Tie pays 0.95x - house takes 5% edge, game ends
                    w = challenge['wager']
                    tie_payout = round(w * 0.95, 2)
                    u = self.db.get_user(user_id)
                    self.db.update_user(user_id, {'balance': u['balance'] + tie_payout})
                    self.db.update_house_balance(-(tie_payout - w))
                    self.db.add_transaction(user_id, "game_tie", tie_payout, f"Game tie payout (0.95x)")
                    self._update_user_stats(user_id, w, tie_payout - w, "draw")

                    user_username = u.get('username', f'User{user_id}')
                    tie_text = f"🤝 <b>Draw!</b> {user_username} cashed out <b>${tie_payout:,.2f}</b>"

                    game_name = challenge.get('game', 'dice')
                    keyboard = [
                        [
                            InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{game_name}_{w:.2f}_{challenge['rolls']}_{challenge['mode']}_{challenge['pts']}"),
                            InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{game_name}_{w*2:.2f}_{challenge['rolls']}_{challenge['mode']}_{challenge['pts']}")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    sent_msg = await context.bot.send_message(chat_id=chat_id, text=tie_text, reply_markup=reply_markup, parse_mode="HTML")
                    self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

                    del self.pending_pvp[cid]
                    self.db.update_pending_pvp(self.pending_pvp)
                    return

                target_pts = challenge.get('pts', 1)
                if challenge['p_pts'] >= target_pts or challenge['b_pts'] >= target_pts:
                    # Series End - UPDATE BALANCE BUT DON'T SEND MESSAGE
                    w = challenge['wager']
                    if challenge['p_pts'] >= target_pts:
                        payout = w * 1.95
                        u = self.db.get_user(user_id)
                        u['balance'] += payout
                        self.db.update_user(user_id, {'balance': u['balance']})
                        self.db.update_house_balance(-(payout - w))
                    else:
                        self.db.update_house_balance(w)

                    del self.pending_pvp[cid]
                    self.db.update_pending_pvp(self.pending_pvp)
                    return
                else:
                    if target_pts > 1:
                        # SILENCE: Don't send Score/Cashout message for mid-series rounds
                        challenge['p_rolls'] = []
                        challenge['b_rolls'] = []
                        self.db.update_pending_pvp(self.pending_pvp)
                        return
                    else:
                        # For 1-point games, we don't need a "Next Round" or Cashout message
                        pass

                self.db.update_pending_pvp(self.pending_pvp)
                return

            if data == "button_unavailable":
                await query.answer("❌ This button is no longer available as the game has started!", show_alert=True)
                return

            if data == "none":
                try:
                    await query.answer()
                except:
                    pass
                return

            if data.startswith("v2_cancel_"):
                cid = data.replace("v2_cancel_", "")
                challenge = self.pending_pvp.get(cid)
                if challenge:
                    # Refund players
                    wager = challenge.get('wager', 0)
                    if cid.startswith("v2_bot_"):
                        pid = challenge.get('player')
                        if pid and challenge.get('wager_deducted'):
                            user_data = self.db.get_user(pid)
                            user_data['balance'] += wager
                            self.db.update_user(pid, user_data)
                    elif cid.startswith("v2_pvp_"):
                        p1, p2 = challenge.get('challenger'), challenge.get('opponent')
                        if p1 and challenge.get('p1_deducted'):
                            user_data = self.db.get_user(p1)
                            user_data['balance'] += wager
                            self.db.update_user(p1, user_data)
                        if p2 and challenge.get('p2_deducted'):
                            user_data = self.db.get_user(p2)
                            user_data['balance'] += wager
                            self.db.update_user(p2, user_data)

                    del self.pending_pvp[cid]
                    self.db.update_pending_pvp(self.pending_pvp)

                    # Try to delete the original command message if it exists
                    try:
                        cmd_msg_id = challenge.get('message_id') # Original command message id is stored here
                        if cmd_msg_id:
                            await context.bot.delete_message(chat_id=chat_id, message_id=cmd_msg_id)
                    except Exception as e:
                        logger.debug(f"Could not delete original command message: {e}")

                    await query.edit_message_text(f"❌ Game cancelled and wager refunded.")
                else:
                    await query.answer("❌ Game no longer exists!", show_alert=True)
                return

            if data.startswith("emoji_setup_"):
                parts = data.split("_")
                # Parts: emoji_setup, game_mode, wager, step, [pts, rolls, mode, opponent]
                if len(parts) < 5:
                    await query.answer("❌ Invalid setup data!", show_alert=True)
                    return

                # Correction: The game mode is at index 2, wager at 3, step at 4
                # Callback format: emoji_setup_{game_mode}_{wager}_{step}_{pts}_{rolls}_{mode}
                g_mode = parts[2]
                try:
                    wager = float(parts[3])
                except ValueError:
                    await query.answer("❌ Invalid wager!", show_alert=True)
                    return
                next_step = parts[4]

                params = {}
                if next_step == "rolls":
                    if len(parts) > 5:
                        params["mode"] = parts[5]
                elif next_step == "points":
                    if len(parts) > 5:
                        params["rolls"] = int(parts[5])
                    if len(parts) > 6:
                        params["mode"] = parts[6]
                elif next_step == "final":
                    if len(parts) > 5:
                        params["pts"] = int(parts[5])
                    if len(parts) > 6:
                        params["rolls"] = int(parts[6])
                    if len(parts) > 7:
                        params["mode"] = parts[7]
                    if len(parts) > 8:
                        params["opponent"] = parts[8]
                elif next_step == "start":
                    # For emoji_setup_dice_1.00_start_1_1_normal
                    if len(parts) >= 8:
                        try:
                            pts = int(parts[5])
                            rolls = int(parts[6])
                            mode = parts[7]
                        except (ValueError, IndexError):
                            # Fallback if indices are shifted
                            # Let's try to find the numeric parts
                            num_parts = [p for p in parts if p.isdigit()]
                            if len(num_parts) >= 2:
                                pts = int(num_parts[0])
                                rolls = int(num_parts[1])
                                mode = parts[-1] if not parts[-1].isdigit() else "normal"
                            else:
                                raise ValueError(f"Could not parse game settings from {parts}")

                        # Remove buttons instead of deleting message
                        try:
                            # Update message to include "Send your emoji" and the button
                            emoji = self.emoji_map.get(g_mode, "🎲")
                            # Use bold tags for user name as before, but ensure formatting is preserved
                            # Adding multiple lines of invisible characters to force message width
                            invisible_padding = "󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡"
                            # new_text = query.message.text_html + f"\n\n<b>{query.from_user.first_name}</b>, your turn! {emoji}\n{invisible_padding}\n{invisible_padding}"
                            kb = [[
                                InlineKeyboardButton("❌ Cancel", callback_data="setup_cancel"),
                                InlineKeyboardButton("✅ Send emoji", callback_data=f"v2_send_emoji_bot_{g_mode}_{wager:.2f}_{rolls}_{mode}_{pts}")
                            ]]
                            await query.edit_message_text(text=new_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Error updating setup message: {e}")

                        # Start the game (Note: the actual game logic will handle the send_emoji callback)
                        return

                elif next_step == "start_game":
                    if len(parts) >= 8:
                        try:
                            pts = int(parts[5])
                            rolls = int(parts[6])
                            mode = parts[7]
                        except (ValueError, IndexError):
                            num_parts = [p for p in parts if p.isdigit()]
                            if len(num_parts) >= 2:
                                pts = int(num_parts[0])
                                rolls = int(num_parts[1])
                                mode = parts[-1] if not parts[-1].isdigit() else "normal"
                            else:
                                raise ValueError(f"Could not parse game settings from {parts}")

                        # Remove buttons instead of deleting message
                        try:
                            emoji = self.emoji_map.get(g_mode, "🎲")
                            invisible_padding = "󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡󠁔󠁨󠁩󠁳󠀠󠁴󠁥󠁸󠁴󠀠󠁳󠁨󠁡󠁬󠁬󠀠󠁢󠁥󠁣󠁯󠁭󠁥󠀠󠁩󠁮󠁶󠁩󠁳󠁩󠁢󠁬󠁥󠀡"
                            # new_text = query.message.text_html + f"\n\n<b>{query.from_user.first_name}</b>, your turn! {emoji}\n{invisible_padding}\n{invisible_padding}"
                            kb = [[
                                InlineKeyboardButton("❌ Cancel", callback_data="setup_cancel"),
                                InlineKeyboardButton("✅ Send emoji", callback_data=f"v2_send_emoji_bot_{g_mode}_{wager:.2f}_{rolls}_{mode}_{pts}")
                            ]]
                            await query.edit_message_text(text=new_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Error updating setup message: {e}")

                        return

                elif next_step == "mode":
                    # Cycle emoji modes
                    await self._show_emoji_game_setup(update, context, wager, g_mode, "mode", params)
                    return

                await self._show_emoji_game_setup(update, context, wager, g_mode, next_step, params)
                return

            # Custom menu switching
            if data.startswith("predict_menu_") or data.startswith("emoji_setup_"):
                parts = data.split("_")
                wager_idx = 2 if data.startswith("predict_menu_") else 3
                try:
                    wager = float(parts[wager_idx])
                    if wager < 1.0:
                        # Auto-fix wager if it's below minimum
                        new_parts = list(parts)
                        new_parts[wager_idx] = "1.00"
                        data = "_".join(new_parts)
                        await query.answer("⚠️ Minimum bet is $1.00. Adjusted to $1.00.", show_alert=True)
                except (ValueError, IndexError):
                    pass

            if data == "none":
                try:
                    await query.answer()
                except:
                    pass
                return

            if data.startswith("v2_cancel_"):
                cid = data.replace("v2_cancel_", "")
                challenge = self.pending_pvp.get(cid)
                if challenge:
                    # Refund players
                    wager = challenge.get('wager', 0)
                    if cid.startswith("v2_bot_"):
                        pid = challenge.get('player')
                        if pid and challenge.get('wager_deducted'):
                            user_data = self.db.get_user(pid)
                            user_data['balance'] += wager
                            self.db.update_user(pid, user_data)
                    elif cid.startswith("v2_pvp_"):
                        p1, p2 = challenge.get('challenger'), challenge.get('opponent')
                        if p1 and challenge.get('p1_deducted'):
                            user_data = self.db.get_user(p1)
                            user_data['balance'] += wager
                            self.db.update_user(p1, user_data)
                        if p2 and challenge.get('p2_deducted'):
                            user_data = self.db.get_user(p2)
                            user_data['balance'] += wager
                            self.db.update_user(p2, user_data)

                    del self.pending_pvp[cid]
                    self.db.update_pending_pvp(self.pending_pvp)

                    # Try to delete the original command message if it exists
                    try:
                        cmd_msg_id = challenge.get('message_id') # Original command message id is stored here
                        if cmd_msg_id:
                            await context.bot.delete_message(chat_id=chat_id, message_id=cmd_msg_id)
                    except Exception as e:
                        logger.debug(f"Could not delete original command message: {e}")

                    await query.edit_message_text(f"❌ Game cancelled and wager refunded.")
                else:
                    await query.answer("❌ Game no longer exists!", show_alert=True)
                return

            if data.startswith("emoji_setup_"):
                parts = data.split("_")
                if len(parts) >= 5:
                    game_mode = parts[2]
                    wager = float(parts[3])
                    step = parts[4]

                    # Parse params from suffix
                    params = {}
                    if step == "mode":
                        # emoji_setup_{game_mode}_{wager}_mode
                        pass
                    elif step == "rolls":
                        # emoji_setup_{game_mode}_{wager}_rolls_{mode}
                        params["mode"] = parts[5] if len(parts) > 5 else "normal"
                    elif step == "points":
                        # emoji_setup_{game_mode}_{wager}_points_{rolls}_{mode}
                        params["rolls"] = int(parts[5]) if len(parts) > 5 else 1
                        params["mode"] = parts[6] if len(parts) > 6 else "normal"
                    elif step == "final":
                        # emoji_setup_{game_mode}_{wager}_final_{pts}_{rolls}_{mode}_{opt_opponent}
                        params["pts"] = int(parts[5]) if len(parts) > 5 else 1
                        params["rolls"] = int(parts[6]) if len(parts) > 6 else 1
                        params["mode"] = parts[7] if len(parts) > 7 else "normal"
                        if len(parts) > 8:
                            params["opponent"] = parts[8]

                    await self._show_emoji_game_setup(update, context, wager, game_mode, step, params)
                    return

            if data.startswith("predict_menu_"):
                parts = data.split("_")
                wager = float(parts[2])
                game_mode = parts[3]
                await self._show_game_prediction_menu(update, context, wager, game_mode)
                return

            if data.startswith("setup_bet_"):
                parts = data.split("_")
                action = parts[2]
                wager = float(parts[3])
                game_mode = parts[4]

                new_wager = wager
                if action == "half":
                    new_wager = wager / 2
                elif action == "double":
                    new_wager = wager * 2

                if new_wager < 1.0:
                    try:
                        await query.answer("❌ Minimum bet is $1.00", show_alert=False)
                    except Exception as e:
                        logger.error(f"Error answering query: {e}")
                    return

                try:
                    await query.answer()
                except:
                    pass

                await self._show_game_prediction_menu(update, context, new_wager, game_mode)
                return

            if data.startswith("setup_mode_dice_"):
                wager = float(data.split("_")[3])
                await self._show_game_prediction_menu(update, context, wager, "dice")
                return

            if data.startswith("setup_mode_darts_"):
                wager = float(data.split("_")[3])
                await self._show_game_prediction_menu(update, context, wager, "darts")
                return

            if data.startswith("setup_mode_basketball_"):
                wager = float(data.split("_")[3])
                await self._show_game_prediction_menu(update, context, wager, "basketball")
                return

            if data.startswith("setup_mode_soccer_"):
                wager = float(data.split("_")[3])
                await self._show_game_prediction_menu(update, context, wager, "soccer")
                return

            if data.startswith("setup_mode_bowling_"):
                wager = float(data.split("_")[3])
                await self._show_game_prediction_menu(update, context, wager, "bowling")
                return

            if data.startswith("flip_bot_"):
                wager = float(data.split("_")[2])
                await self._show_game_prediction_menu(update, context, wager, "coinflip")
                return

            if data.startswith("setup_mode_predict_edit_"):
                parts = data.split("_")
                wager = float(parts[4])
                game_mode = parts[5] if len(parts) > 5 else "dice"
                await self._setup_predict_interface(update, context, wager, game_mode, force_new=False)
                return

            if data.startswith("setup_mode_predict_"):
                # Remove buttons from the result message
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception as e:
                    logger.error(f"Error removing markup: {e}")

                parts = data.split("_")
                try:
                    wager = float(parts[3])
                except (ValueError, IndexError):
                    wager = 10.0
                game_mode = parts[4] if len(parts) > 4 else "dice"
                # Force a new message instead of editing
                await self._setup_predict_interface(update, context, wager, game_mode, force_new=True)
                return

            elif data == "setup_cancel":
                user_id = query.from_user.id
                if hasattr(self, "_predict_selections") and user_id in self._predict_selections:
                    del self._predict_selections[user_id]

                # IMPORTANT: Remove from pending_pvp to allow new games
                for cid in list(self.pending_pvp.keys()):
                    challenge = self.pending_pvp[cid]
                    if (challenge.get('player') == user_id or 
                        challenge.get('challenger') == user_id or 
                        challenge.get('opponent') == user_id):
                        # Refund if wager was deducted
                        wager = challenge.get('wager', 0)
                        if challenge.get('wager_deducted') or challenge.get('p1_deducted'):
                            user_data = self.db.get_user(user_id)
                            user_data['balance'] += wager
                            self.db.update_user(user_id, user_data)

                        del self.pending_pvp[cid]

                self.db.update_pending_pvp(self.pending_pvp)

                try:
                    # Delete the match accepted message
                    await query.message.delete()

                    # Try to delete original command message
                    cmd_id = context.user_data.get('last_dice_cmd_id')
                    if cmd_id:
                        await context.bot.delete_message(chat_id=chat_id, message_id=cmd_id)
                        context.user_data.pop('last_dice_cmd_id', None)
                    elif query.message.reply_to_message:
                        try:
                            await query.message.reply_to_message.delete()
                        except:
                            pass
                except Exception as e:
                    logger.error(f"Error in setup_cancel: {e}")
                return

            elif data == "setup_cancel_roll":
                user_id = query.from_user.id
                if hasattr(self, "_predict_selections") and user_id in self._predict_selections:
                    del self._predict_selections[user_id]

                # IMPORTANT: Remove from pending_pvp to allow new games
                for cid in list(self.pending_pvp.keys()):
                    challenge = self.pending_pvp[cid]
                    if (challenge.get('player') == user_id or 
                        challenge.get('challenger') == user_id or 
                        challenge.get('opponent') == user_id):
                        # Refund if wager was deducted
                        wager = challenge.get('wager', 0)
                        if challenge.get('wager_deducted') or challenge.get('p1_deducted'):
                            user_data = self.db.get_user(user_id)
                            user_data['balance'] += wager
                            self.db.update_user(user_id, user_data)

                        del self.pending_pvp[cid]

                self.db.update_pending_pvp(self.pending_pvp)

                try:
                    # Delete the match accepted message
                    await query.message.delete()

                    # Try to delete original command message
                    last_cmd_id = context.user_data.get('last_dice_cmd_id') or context.user_data.get('last_roll_cmd_id')
                    if last_cmd_id:
                        await context.bot.delete_message(chat_id=chat_id, message_id=last_cmd_id)
                        context.user_data.pop('last_dice_cmd_id', None)
                        context.user_data.pop('last_roll_cmd_id', None)
                    elif query.message.reply_to_message:
                        try:
                            await query.message.reply_to_message.delete()
                        except:
                            pass
                except Exception as e:
                    logger.error(f"Error in setup_cancel_roll: {e}")
                return

            elif data.startswith("setup_bet_back_"):
                parts = data.split("_")
                if len(parts) < 4:
                    await query.answer("❌ Invalid button data!", show_alert=True)
                    return
                wager = float(parts[3])
                await self.bet_command(update, context, amount=wager)
                return

            if data.startswith("setup_predict_select_") or data.startswith("predict_start_"):
                # DISABLED: user requested to not show "Game in Progress" button
                # try:
                #     # Replace with a dummy button that does nothing
                #     dummy_kb = [[InlineKeyboardButton("⏳ Game in Progress...", callback_data="dummy")]]
                #     await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(dummy_kb))
                # except Exception as e:
                #     logger.error(f"Error making markup unclickable: {e}")

                from predict_handler import handle_predict
                await handle_predict(self, update, context)
                return

            if ownership_key in self.button_ownership:
                owner_id = self.button_ownership[ownership_key]
                if user_id != owner_id:
                    await query.answer("❌ This is not your game/menu!", show_alert=True)
                    return

            if data == "tip_cancel":
                try:
                    await query.message.delete()
                    if query.message.reply_to_message:
                        await query.message.reply_to_message.delete()
                except Exception as e:
                    logger.error(f"Error in tip_cancel: {e}")
                return

            elif data.startswith("tip_confirm_"):
                parts = data.split("_")
                recipient_id = int(parts[2])
                amount = float(parts[3])

                user_data = self.db.get_user(user_id)
                if amount > user_data['balance']:
                    await query.answer("❌ Insufficient balance for this tip.", show_alert=True)
                    return

                recipient_data = self.db.get_user(recipient_id)
                recipient_display_name = recipient_data.get('username') or recipient_data.get('first_name') or f"User{recipient_id}"

                # Deduct from sender
                user_data['balance'] -= amount
                self.db.update_user(user_id, user_data)

                # Add to recipient
                recipient_data['balance'] += amount
                self.db.update_user(recipient_id, recipient_data)

                # Record transactions
                self.db.add_transaction(user_id, "tip_sent", -amount, f"Tip to {recipient_display_name}")
                self.db.add_transaction(recipient_id, "tip_received", amount, f"Tip from {user_data.get('username', user_id)}")

                # Use mention_html for clickable link
                mention = f'<a href="tg://user?id={recipient_id}">{recipient_display_name}</a>'

                await query.message.delete()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🎉 Tip succesful!! {mention} received <b>${amount:,.2f}</b>",
                    parse_mode="HTML"
                )

                # Notify receiver via DM
                try:
                    # Use mention for sender as well
                    sender_name = user_data.get('username') or user_data.get('first_name') or f"User{user_id}"
                    sender_mention = f'<a href="tg://user?id={user_id}">{sender_name}</a>'
                    await context.bot.send_message(
                        chat_id=recipient_id,
                        text=f"🎁 You received a tip of <b>${amount:,.2f}</b> from {sender_mention}!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                return

            # Generic game setup (Initial step from /bet menu)
            if data.startswith("setup_mode_") and not (data.startswith("setup_mode_normal_") or data.startswith("setup_mode_crazy_")):
                parts = data.split("_")
                if len(parts) >= 3:
                    game, wager = parts[2], float(parts[3])
                    keyboard = [
                        [InlineKeyboardButton("Normal", callback_data=f"setup_mode_normal_{game}_{wager:.2f}"),
                         InlineKeyboardButton("Crazy", callback_data=f"setup_mode_crazy_{game}_{wager:.2f}")]
                    ]
                    sent_msg = await query.edit_message_text(f"**{game.capitalize()}**\nWager: ${wager:.2f}\n\nChoose Game Mode:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                    self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            # Generic setup handlers
            elif data.startswith("setup_mode_normal_"):
                parts = data.split('_')
                if len(parts) >= 4:
                    game, wager = parts[3], float(parts[4])
                    keyboard = [
                        [InlineKeyboardButton("1", callback_data=f"setup_pts_{game}_{wager:.2f}_normal_1")],
                        [InlineKeyboardButton("2", callback_data=f"setup_pts_{game}_{wager:.2f}_normal_2")]
                    ]
                    sent_msg = await query.edit_message_text(f"**{game.capitalize()}**\nWager: ${wager:.2f}\nMode: Normal\n\nHow many rolls per round?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                    self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            elif data.startswith("setup_mode_crazy_"):
                parts = data.split('_')
                if len(parts) >= 4:
                    game, wager = parts[3], float(parts[4])
                    keyboard = [
                        [InlineKeyboardButton("1", callback_data=f"setup_pts_{game}_{wager:.2f}_crazy_1")],
                        [InlineKeyboardButton("2", callback_data=f"setup_pts_{game}_{wager:.2f}_crazy_2")]
                    ]
                    sent_msg = await query.edit_message_text(f"**{game.capitalize()}**\nWager: ${wager:.2f}\nMode: Crazy\n\nHow many rolls per round?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                    self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            elif data.startswith("setup_rolls_"):
                parts = data.split('_')
                if len(parts) >= 5:
                    game, wager, mode = parts[2], float(parts[3]), parts[4]
                    keyboard = [
                        [InlineKeyboardButton("1", callback_data=f"setup_pts_{game}_{wager:.2f}_{mode}_1")],
                        [InlineKeyboardButton("2", callback_data=f"setup_pts_{game}_{wager:.2f}_{mode}_2")]
                    ]
                    sent_msg = await query.edit_message_text(f"**{game.capitalize()}**\nWager: ${wager:.2f}\nMode: {mode}\n\nHow many rolls per round?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                    self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            elif data.startswith("setup_pts_"):
                parts = data.split('_')
                if len(parts) >= 6:
                    game, wager, mode, rolls = parts[2], float(parts[3]), parts[4], int(parts[5])
                    keyboard = [
                        [InlineKeyboardButton("1", callback_data=f"setup_opp_{game}_{wager:.2f}_{mode}_{rolls}_1")],
                        [InlineKeyboardButton("2", callback_data=f"setup_opp_{game}_{wager:.2f}_{mode}_{rolls}_2")],
                        [InlineKeyboardButton("3", callback_data=f"setup_opp_{game}_{wager:.2f}_{mode}_{rolls}_3")]
                    ]
                    sent_msg = await query.edit_message_text(f"**{game.capitalize()}**\nWager: ${wager:.2f}\nMode: {mode}\nRolls: {rolls}\n\nChoose Target Score:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                    self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            elif data.startswith("setup_opp_"):
                parts = data.split('_')
                if len(parts) >= 7:
                    game, wager, mode, rolls, pts = parts[2], float(parts[3]), parts[4], int(parts[5]), int(parts[6])
                    keyboard = [
                        [InlineKeyboardButton("🤖 Play vs Bot", callback_data=f"v2_bot_{game}_{wager:.2f}_{rolls}_{mode}_{pts}")],
                        [InlineKeyboardButton("👥 Create PvP", callback_data=f"v2_pvp_{game}_{wager:.2f}_{rolls}_{mode}_{pts}")]
                    ]
                    sent_msg = await query.edit_message_text(f"**{game.capitalize()}** Ready!\n\nWager: ${wager:.2f}\nMode: {mode}\nRolls: {rolls}\nTarget: {pts}\n\nChoose Opponent:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                    self.button_ownership[(chat_id, sent_msg.message_id)] = user_id

            if data.startswith("v2_bot_") or data.startswith("dice_bot_") or data.startswith("basketball_bot_") or data.startswith("soccer_bot_") or data.startswith("darts_bot_") or data.startswith("bowling_bot_"):
                # DISABLED: user requested to not show "Game in Progress" button
                # try:
                #     dummy_kb = [[InlineKeyboardButton("⏳ Game in Progress...", callback_data="dummy")]]
                #     await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(dummy_kb))
                # except:
                #     pass

                parts = data.split('_')
                if len(parts) >= 3:
                    if data.startswith("v2_bot_"):
                        game = parts[2]
                        wager = float(parts[3])

                        # If it's a full "Play Again" / "Double" callback (v2_bot_game_wager_rolls_mode_pts)
                        if len(parts) >= 7:
                            # Remove buttons from the current message
                            try:
                                await query.edit_message_reply_markup(reply_markup=None)
                            except:
                                pass

                            rolls = int(parts[4])
                            mode = parts[5]
                            pts = int(parts[6])

                            # Force a new message by passing new_message=True
                            await self._show_emoji_game_setup(update, context, wager, game, "final", {"rolls": rolls, "mode": mode, "pts": pts}, new_message=True)
                            return
                        # Special edit buttons: v2_bot_edit_{field}_{game}_{wager}_{rolls}_{mode}_{pts}
                        elif len(parts) >= 8 and parts[2] == "edit":
                            field = parts[3]
                            game = parts[4]
                            wager = float(parts[5])
                            rolls = int(parts[6])
                            mode = parts[7]
                            pts = int(parts[8])
                            await self._show_emoji_game_setup(update, context, wager, game, field, {"rolls": rolls, "mode": mode, "pts": pts})
                            return
                        # If it's just the initiation (v2_bot_game_wager)
                        else:
                            await self._show_emoji_game_setup(update, context, wager, game, "mode", {})
                            return
                    else:
                        game = parts[0]
                        wager = float(parts[2])
                        await self._show_emoji_game_setup(update, context, wager, game, "mode", {})
                        return
                return

            elif data.startswith("v2_pvp_"):
                parts = data.split('_')
                if len(parts) >= 7:
                    game, wager, rolls, mode, pts = parts[2], float(parts[3]), int(parts[4]), parts[5], int(parts[6])
                    await self.start_generic_v2_pvp(update, context, game, wager, rolls, mode, pts)
                return

            elif data.startswith("v2_accept_"):
                cid = data.replace("v2_accept_", "")
                await self.accept_generic_v2_pvp(update, context, cid)

            elif data.startswith("v2_cashout_"):
                await query.answer()
                # Re-load pending pvp to ensure we have latest data
                self.pending_pvp = self.db.data.get('pending_pvp', {})
                cid = data.replace("v2_cashout_", "")
                challenge = self.pending_pvp.get(cid)
                if not challenge or challenge.get('player') != user_id:
                    # Try to find it if cid was slightly modified or check if it's already resolved
                    await query.answer("❌ Game already finished or not found!", show_alert=True)
                    return

                # Check if player has already rolled in this round
                if len(challenge.get('p_rolls', [])) > 0 or challenge.get('cur_rolls', 0) > 0:
                    # In single-point games after a draw, p_rolls is cleared and cur_rolls is reset
                    # But if they JUST rolled, we should check cur_rolls or p_rolls
                    await query.answer("❌ You already sent your emoji! Cannot cashout now.", show_alert=True)
                    return

                # Special handling for single point games in draw state
                # If target_pts == 1 and it's a draw, they can cash out
                target_pts = challenge.get('pts', 1)

                # Edit the original cashout message to show result
                cashout_val = self.calculate_cashout(challenge['p_pts'], challenge['b_pts'], target_pts, challenge['wager'])
                user_data = self.db.get_user(user_id)

                # Update user balance
                user_data['balance'] += cashout_val
                self.db.update_user(user_id, user_data)

                profit = cashout_val - challenge['wager']
                self.db.update_house_balance(-profit)

                # Commit changes (Postgres)
                with self.db.app.app_context():
                    db.session.commit()

                username = user_data.get('username', f'User{user_id}')
                bold_username = f"<b>{username}</b>"
                bold_amount = f"<b>${cashout_val:.2f}</b>"

                # Format final text for the cashout message, preserving the score
                p1_name = user_data.get('username', f'User{user_id}')
                cashout_text = (
                    f"🏆 <b>Game Over!</b>\n\n"
                    f"{p1_name}: {challenge['p_pts']}\n"
                    f"Rukia: {challenge['b_pts']}\n\n"
                    f"{p1_name} cashed out <b>${cashout_val:.2f}</b>!"
                )

                # Create Play Again / Double buttons
                target_pts = challenge.get('pts', 1)
                w = challenge['wager']
                rolls = challenge['rolls']
                mode = challenge['mode']
                game = challenge.get('game', 'dice')

                kb = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"v2_bot_{game}_{w:.2f}_{rolls}_{mode}_{target_pts}"),
                       InlineKeyboardButton("🔄 Double", callback_data=f"v2_bot_{game}_{w*2:.2f}_{rolls}_{mode}_{target_pts}")]]

                try:
                    # Edit the original message to show result with Play Again / Double buttons
                    await query.edit_message_text(
                        text=cashout_text,
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Error handling cashout UI: {e}")
                    # Fallback
                    await context.bot.send_message(chat_id=chat_id, text=cashout_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

                del self.pending_pvp[cid]

                # Update global state for pending_pvp
                with self.db.app.app_context():
                    gs = db.session.get(GlobalState, "pending_pvp")
                    if gs:
                        # Ensure we are using the local copy that has the deleted cid
                        gs.value = dict(self.pending_pvp)
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(gs, "value")
                        db.session.commit()
                return

            if data.startswith("bj_bot_"):
                # Already handled in the bj_ block above
                return

            elif data.startswith("slots_bot_"):
                wager = float(data.split("_")[2])
                user_data = self.db.get_user(user_id)
                if wager > user_data['balance']:
                    await query.answer(f"❌ Insufficient balance! (${user_data['balance']:.2f})", show_alert=True)
                    return
                # Deduct wager and start slots
                self.db.update_user(user_id, {'balance': user_data['balance'] - wager})
                dice_message = await context.bot.send_dice(chat_id=chat_id, emoji="🎰")
                slot_value = dice_message.dice.value
                double_match_values = [2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 23, 24, 25, 26, 27, 30, 31, 32, 33, 34, 37, 38, 39, 40, 41, 44, 45, 46, 47, 48, 51, 52, 53, 54, 55, 58, 59, 60, 61, 62]
                await asyncio.sleep(3)
                payout_multiplier = 0
                if slot_value == 64: payout_multiplier = 10
                elif slot_value in [1, 22, 43]: payout_multiplier = 5
                elif slot_value in double_match_values: payout_multiplier = 2
                payout = wager * payout_multiplier
                profit = payout - wager
                keyboard = [[InlineKeyboardButton("Play Again", callback_data=f"slots_{wager:.2f}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                if payout > 0:
                    user_data['balance'] += payout
                    user_data['total_wagered'] += wager
                    user_data['wagered_since_last_withdrawal'] = user_data.get('wagered_since_last_withdrawal', 0) + wager
                    user_data['games_played'] += 1
                    user_data['games_won'] += 1
                    user_data['total_won'] = user_data.get('total_won', 0) + payout
                    self.db.update_user(user_id, user_data)
                    self.db.update_house_balance(-profit)
                    sent_msg = await context.bot.send_message(chat_id=chat_id, text=f"<b>{user_data['username']}</b> won <b>${profit:.2f}</b>", parse_mode="HTML")
                else:
                    user_data['total_wagered'] += wager
                    user_data['wagered_since_last_withdrawal'] = user_data.get('wagered_since_last_withdrawal', 0) + wager
                    user_data['games_played'] += 1
                    self.db.update_user(user_id, user_data)
                    self.db.update_house_balance(wager)
                    sent_msg = await context.bot.send_message(chat_id=chat_id, text=f"<b>emojigamblebot</b> won <b>${wager:.2f}</b>", reply_markup=reply_markup, parse_mode="HTML")
                self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id
                self.db.record_game({'type': 'slots_bot', 'player_id': user_id, 'wager': wager, 'slot_value': slot_value, 'result': 'win' if profit > 0 else 'loss', 'payout': profit})
                return

            # Game Callbacks (Darts PvP)
            elif data.startswith("darts_player_open_"):
                wager = float(data.split('_')[3])
                await self.create_emoji_pvp_challenge(update, context, wager, "darts", "🎯")

            elif data.startswith("accept_darts_"):
                challenge_id = data.split('_', 2)[2]
                await self.accept_emoji_pvp_challenge(update, context, challenge_id)

            # Game Callbacks (Basketball PvP)
            elif data.startswith("basketball_player_open_"):
                wager = float(data.split('_')[3])
                await self.create_emoji_pvp_challenge(update, context, wager, "basketball", "🏀")

            elif data.startswith("accept_basketball_"):
                challenge_id = data.split('_', 2)[2]
                await self.accept_emoji_pvp_challenge(update, context, challenge_id)

            # Game Callbacks (Soccer PvP)
            elif data.startswith("soccer_player_open_"):
                wager = float(data.split('_')[3])
                await self.create_emoji_pvp_challenge(update, context, wager, "soccer", "⚽")

            elif data.startswith("accept_soccer_"):
                challenge_id = data.split('_', 2)[2]
                await self.accept_emoji_pvp_challenge(update, context, challenge_id)

            # Game Callbacks (Bowling PvP)
            elif data.startswith("bowling_player_open_"):
                wager = float(data.split('_')[3])
                await self.create_emoji_pvp_challenge(update, context, wager, "bowling", "🎳")

            elif data.startswith("accept_bowling_"):
                challenge_id = data.split('_', 2)[2]
                await self.accept_emoji_pvp_challenge(update, context, challenge_id)

            # Game Callbacks (CoinFlip vs Bot)
            elif data.startswith("flip_bot_"):
                parts = data.split('_')
                wager = float(parts[2])
                choice = parts[3]
                await self.coinflip_vs_bot(update, context, wager, choice)

            # Game Callbacks (Slots play again)
            elif data.startswith("slots_"):
                wager = float(data.split('_')[1])

                user_data = self.db.get_user(user_id)

                if wager > user_data['balance']:
                    await context.bot.send_message(chat_id=chat_id, text=f"❌ Balance: ${user_data['balance']:.2f}")
                    return

                # Deduct wager from user balance
                self.db.update_user(user_id, {'balance': user_data['balance'] - wager})

                # Send the slot machine emoji and wait for result
                dice_message = await context.bot.send_dice(chat_id=chat_id, emoji="🎰")
                slot_value = dice_message.dice.value

                # Slot machine values range from 1-64
                double_match_values = [2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 23, 24, 25, 26, 27, 30, 31, 32, 33, 34, 37, 38, 39, 40, 41, 44, 45, 46, 47, 48, 51, 52, 53, 54, 55, 58, 59, 60, 61, 62]

                await asyncio.sleep(3)

                payout_multiplier = 0

                if slot_value == 64:
                    payout_multiplier = 10
                elif slot_value in [1, 22, 43]:
                    payout_multiplier = 5
                elif slot_value in double_match_values:
                    payout_multiplier = 2

                payout = wager * payout_multiplier
                profit = payout - wager

                # Add play-again button
                keyboard = [[InlineKeyboardButton("Play Again", callback_data=f"slots_{wager:.2f}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # Update user balance and stats
                if payout > 0:
                    new_balance = user_data['balance'] + payout
                    self.db.update_user(user_id, {
                        'balance': new_balance,
                        'total_wagered': user_data['total_wagered'] + wager,
                        'wagered_since_last_withdrawal': user_data.get('wagered_since_last_withdrawal', 0) + wager,
                        'games_played': user_data['games_played'] + 1,
                        'games_won': user_data['games_won'] + 1,
                        'total_won': user_data.get('total_won', 0) + payout
                    })
                    self.db.update_house_balance(-profit)
                    sent_msg = await context.bot.send_message(chat_id=chat_id, text=f"<b>{user_data['username']}</b> won <b>${profit:.2f}</b>", reply_markup=reply_markup, parse_mode="HTML")
                    self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id
                else:
                    self.db.update_user(user_id, {
                        'total_wagered': user_data['total_wagered'] + wager,
                        'wagered_since_last_withdrawal': user_data.get('wagered_since_last_withdrawal', 0) + wager,
                        'games_played': user_data['games_played'] + 1
                    })
                    self.db.update_house_balance(wager)
                    sent_msg = await context.bot.send_message(chat_id=chat_id, text=f"<b>emojigamblebot</b> won <b>${wager:.2f}</b>", reply_markup=reply_markup, parse_mode="HTML")
                    self.button_ownership[(sent_msg.chat_id, sent_msg.message_id)] = user_id

                # Record game
                self.db.record_game({
                    'type': 'slots_bot',
                    'player_id': user_id,
                    'wager': wager,
                    'slot_value': slot_value,
                    'result': 'win' if profit > 0 else 'loss',
                    'payout': profit
                })

            # Leaderboard Pagination
            elif data.startswith("lb_page_"):
                page = int(data.split('_')[2])
                await self.show_leaderboard_page(update, page)

            # Utility Callbacks
            elif data == "claim_daily_bonus":
                user_data = self.db.get_user(user_id)
                bonus_amount = user_data.get('wagered_since_last_withdrawal', 0) * 0.01

                if bonus_amount < 0.01:
                     await query.edit_message_text("❌ Minimum bonus to claim is $0.01.")
                     return

                # Process claim
                user_data['balance'] += bonus_amount
                user_data['wagered_since_last_withdrawal'] = 0.0 # Reset wagered amount
                self.db.update_user(user_id, user_data)

                self.db.add_transaction(user_id, "bonus_claim", bonus_amount, "Bonus Claim")

                await query.edit_message_text(f"✅ **Bonus Claimed!**\nYou received **${bonus_amount:.2f}**.\n\nYour new balance is ${user_data['balance']:.2f}.", parse_mode="Markdown")

            # Deposit/Withdrawal buttons
            elif data == "deposit_mock":
                deposit_text = "💳 **Deposits coming soon!**\n\nWe are currently updating our payment providers. Please check back later."
                await query.edit_message_text(deposit_text, parse_mode="Markdown")

            elif data == "withdraw_mock":
                user_data = self.db.get_user(user_id)
                if user_data['balance'] < 1.00:
                    try:
                        # Delete the bot message
                        await query.delete_message()
                        # Try to delete the user message if it exists
                        if update.callback_query.message.reply_to_message:
                            await update.callback_query.message.reply_to_message.delete()
                    except Exception as e:
                        logger.error(f"Failed to delete messages in withdrawal check: {e}")
                        await query.answer("❌ Minimum withdrawal is $1.00.", show_alert=True)
                else:
                    withdraw_text = f"""💸 **LTC Withdrawal Request**

Your balance **${user_data['balance']:.2f}**

To withdraw, use:
`/withdraw <amount> <your_ltc_address>`

**Example:** `/withdraw 50 LTC1abc123...`

⚠️ Withdrawals are processed manually by admin."""
                    await query.edit_message_text(withdraw_text, parse_mode="Markdown")

            elif data == "transactions_history":
                user_transactions = self.db.data['transactions'].get(str(user_id), [])[-10:] # Last 10

                if not user_transactions:
                    await query.edit_message_text("📜 No transaction history found.")
                    return

                history_text = "📜 **Last 10 Transactions**\n\n"
                for tx in reversed(user_transactions):
                    time_str = datetime.fromisoformat(tx['timestamp']).strftime("%m/%d %H:%M")
                    sign = "+" if tx['amount'] >= 0 else ""
                    history_text += f"*{time_str}* | `{sign}{tx['amount']:.2f}`: {tx['description']}\n"

                await query.edit_message_text(history_text, parse_mode="Markdown")

            # Handle decline of PvP (general)
            elif data.startswith("decline_"):
                challenge_id = data.split('_', 1)[1]
                if challenge_id in self.pending_pvp and self.pending_pvp[challenge_id]['challenger'] == user_id:
                    await query.edit_message_text("✅ Challenge canceled.")
                    del self.pending_pvp[challenge_id]
                    self.db.update_pending_pvp(self.pending_pvp)
                else:
                    await query.answer("❌ Only the challenger can cancel this game.", show_alert=True)

            # Blackjack button handlers
            else:
                await query.edit_message_text("Something went wrong or this button is for a different command!")
        except Exception as e:
            import traceback
            error_str = str(e)
            logger.error(f"Error in button_callback: {error_str}\n{traceback.format_exc()}")
            # Don't send error message for known non-critical issues or if message was already handled
            if "Minimum bet" in error_str or "query is answered" in error_str or "Message is not modified" in error_str:
                try:
                    await query.answer()
                except:
                    pass
                return
            try:
                await context.bot.send_message(chat_id=query.message.chat_id, text=f"An unexpected error occurred: {error_str}. Please try again.")
            except:
                pass


    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages for withdrawal flow."""
        user_id = update.effective_user.id
        text = update.message.text

        # 1. Handle Withdrawal Amount
        if context.user_data.get('awaiting_wit_amount'):
            try:
                amount = float(text)
                user_data = self.db.get_user(user_id)

                if amount > user_data['balance']:
                    await update.message.reply_text(
                        f"Insufficient balance\n Current balance: ${user_data['balance']:,.2f}",
                        parse_mode="HTML"
                    )
                    return

                # Success: Save amount and ask for address
                context.user_data['wit_amount'] = amount
                context.user_data['awaiting_wit_amount'] = False
                context.user_data['awaiting_wit_address'] = True

                currency = context.user_data.get('wit_currency', 'crypto')
                # Map currency names for better display if needed
                currency_map = {
                    "LTC": "Litecoin", "BTC": "Bitcoin", "ETH": "Ethereum",
                    "SOL": "Solana", "XMR": "Monero", "USDT": "USDT",
                    "USDC": "USDC", "TON": "Toncoin", "BNB": "BNB"
                }
                display_currency = currency_map.get(currency, currency)

                await update.message.reply_text(f"send your {display_currency} adress")

            except ValueError:
                await update.message.reply_text("❌ Please enter a valid number for the amount.")
            return

        # 2. Handle Withdrawal Address
        if context.user_data.get('awaiting_wit_address'):
            address = text
            amount = context.user_data.get('wit_amount')
            currency = context.user_data.get('wit_currency')

            # Clear states
            context.user_data['awaiting_wit_address'] = False

            # Log pending withdrawal in DB
            user_data = self.db.get_user(user_id)
            pending_withdrawals = self.db.data.get('pending_withdrawals', [])
            pending_withdrawals.append({
                'user_id': user_id,
                'username': update.effective_user.username or "Unknown",
                'amount': amount,
                'currency': currency,
                'address': address,
                'status': 'pending'
            })
            self.db.data['pending_withdrawals'] = pending_withdrawals

            # Deduct balance immediately
            user_data['balance'] -= amount
            self.db.update_user(user_id, user_data)

            # Send request to channel
            channel_id = "@emojigamblegroup"
            username = update.effective_user.username or update.effective_user.first_name
            # Clickable mention without @
            user_mention = f'<a href="tg://user?id={user_id}">{username}</a>'

            request_text = (
                f"📤 <b>New Withdrawal Request</b>\n\n"
                f"User: {user_mention}\n"
                f"Amount: ${amount:,.2f}\n"
                f"Currency: {currency}\n"
                f"Address: <code>{address}</code>"
            )

            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"adm_wit_approve_{user_id}_{amount}"),
                    InlineKeyboardButton("❌ Deny", callback_data=f"adm_wit_deny_{user_id}_{amount}")
                ]
            ]

            try:
                await self.app.bot.send_message(
                    chat_id=channel_id,
                    text=request_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to send withdrawal request to channel: {e}")

            await update.message.reply_text("withdraw initiated")
            return

    async def sk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reset the database/pending games (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is restricted to admins.")
            return

        # Clear pending games in memory
        self.pending_pvp.clear()

        # Clear pending games in database
        with self.db.app.app_context():
            from models import GlobalState
            gs = GlobalState.query.filter_by(key='pending_pvp').first()
            if gs:
                gs.value = '{}'
                db.session.commit()

        await update.message.reply_text("✅ Database reset! All pending games cleared.")

    async def ss_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle bonus bypass for testing (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            return

        self._bonus_bypass = not getattr(self, '_bonus_bypass', False)
        status = "ON ✅" if self._bonus_bypass else "OFF ❌"
        await update.message.reply_text(f"Bonus bypass: {status}")

    async def ks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hard reset the bot and database (Admin only)"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ This command is for administrators only.")
            return

        await update.message.reply_text("🔄 Initiating hard reset... wiping database and restarting bot.")

        with self.app.app_context():
            # Delete all data from tables
            db.session.query(User).delete()
            db.session.query(Game).delete()
            db.session.query(Transaction).delete()
            db.session.query(GlobalState).delete()
            db.session.commit()

        # Clear in-memory states
        self.blackjack_sessions.clear()
        self.pending_pvp.clear()
        self.button_ownership.clear()

        await update.message.reply_text("✅ Wipe complete. Restarting bot process...")

        # Kill the process to trigger a restart from the workflow
        os._exit(0)

    def run(self):
        """Start the bot."""
        # Schedule task to check for expired challenges every 5 seconds
        if not self.app.job_queue:
            logger.warning("JobQueue is not available. Timer-based features will not work.")
        else:
            self.app.job_queue.run_repeating(self.check_expired_challenges, interval=5, first=5)

        # Set bot commands for the menu
        from telegram import BotCommand
        async def set_commands():
            commands = [
                BotCommand("start", "Start the bot"),
                BotCommand("bal", "Check balance"),
                BotCommand("tip", "Tip a user"),
                BotCommand("blackjack", "Play Blackjack"),
                BotCommand("housebal", "Check house balance"),
                BotCommand("leaderboard", "View leaderboard"),
                BotCommand("stats", "View your stats"),
                BotCommand("help", "Get help")
            ]
            await self.app.bot.set_my_commands(commands)

        # We need to run this in the loop
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(set_commands())

        self.app.run_polling(poll_interval=1.0)


    # --- RAKEBACK ---

    async def rakeback_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Claim rakeback balance"""
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        rakeback = user_data.get('rakeback_balance', 0) or 0
        
        if rakeback < 0.01:
            await update.message.reply_text(f"Your rakeback balance is too low to claim: **${rakeback:,.2f}**", parse_mode="Markdown")
            return
            
        # Add to balance and reset rakeback
        self.db.update_user(user_id, {
            'balance': user_data['balance'] + rakeback,
            'rakeback_balance': 0
        })
        self.db.add_transaction(user_id, "rakeback_claim", rakeback, "Claimed 2% Rakeback")
        
        await update.message.reply_text(f"✅ Successfully claimed **${rakeback:,.2f}** rakeback!", parse_mode="Markdown")

async def main():
    BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")).strip()

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("!!! FATAL ERROR: Please set the TELEGRAM_BOT_TOKEN environment variable. !!!")
        return

    logger.info("Starting Antaria Casino Bot...")
    bot = AntariaCasinoBot(token=BOT_TOKEN)

    # Ensure no other instances are running by deleting webhook
    await bot.app.bot.delete_webhook(drop_pending_updates=True)

    # Set bot commands for the menu
    from telegram import BotCommand
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("bal", "Check balance"),
        BotCommand("tip", "Tip a user"),
        BotCommand("blackjack", "Play Blackjack"),
        BotCommand("housebal", "Check house balance"),
        BotCommand("leaderboard", "View leaderboard"),
        BotCommand("stats", "View your stats"),
        BotCommand("help", "Get help")
    ]

    if bot.app.job_queue:
        bot.app.job_queue.run_repeating(bot.check_expired_challenges, interval=5, first=5)
    else:
        logger.warning("JobQueue is not available. Timer-based features will not work.")

    await bot.app.initialize()
    try:
        # Set a request timeout to avoid startup failure if Telegram is slow
        await bot.app.bot.set_my_commands(commands, write_timeout=30, read_timeout=30, connect_timeout=30)
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}")
    await bot.app.start()
    await bot.app.updater.start_polling(poll_interval=1.0)

    logger.info("Bot is running with polling mode...")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if bot.app.updater:
            await bot.app.updater.stop()
        await bot.app.stop()
        await bot.app.shutdown()

if __name__ == '__main__':
    # Ensure only one instance is running using a socket lock
    try:
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Use a high port number that is unlikely to be used by other processes
        # but is within the internal range.
        # Changed from 47123 to 3001 to avoid potential conflicts in some environments
        lock_socket.bind(('127.0.0.1', 3001))
    except socket.error:
        print("Another instance of the bot is already running. Exiting.")
        sys.exit(1)

    asyncio.run(main())
