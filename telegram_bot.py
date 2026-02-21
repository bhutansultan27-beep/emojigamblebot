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
            from sqlalchemy import select, or_
            # Use JSON extraction to filter by user_id or player_id in the 'data' column
            # In PostgreSQL, this is: data->>'user_id' = :user_id OR data->>'player_id' = :user_id
            # However, for broader compatibility and since we are already loading all games in the previous implementation,
            # let's optimize to filter correctly.
            games = Game.query.order_by(Game.timestamp.desc()).all()
            user_games = []
            for g in games:
                if not g.data:
                    continue
                
                # Check all possible player ID fields in game data
                g_player_id = g.data.get('player_id') or g.data.get('user_id') or g.data.get('player')
                
                # Ensure we are comparing as strings or ints consistently
                if str(g_player_id) == str(user_id):
                    # Local copy to avoid mutating the database object directly if it's reused
                    game_display_data = dict(g.data)
                    
                    # Replace specific bot username with "Bot" in the display data
                    # Also handle case-insensitive check just in case
                    for key in ['bot', 'challenger', 'opponent', 'winner']:
                        val = game_display_data.get(key)
                        if isinstance(val, str):
                            lower_val = val.lower()
                            if lower_val in ["@davaulte", "davaulte", "emoji gamble bot", "emojigamblebot"]:
                                game_display_data[key] = 'Bot'
                        
                    user_games.append({**game_display_data, 'timestamp': g.timestamp.isoformat() if g.timestamp else None})
                if len(user_games) >= limit:
                    break
            return user_games

    def record_game(self, game_data: Dict[str, Any]):
        with self.db.app.app_context():
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
