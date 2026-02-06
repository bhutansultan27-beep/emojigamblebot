# Antaria Casino Telegram Bot

## Overview
A Telegram casino bot that allows users to play various games like dice, blackjack, roulette, and coinflip. Uses PostgreSQL for database persistence and Flask-SQLAlchemy for ORM.

## Recent Changes
- Synchronized balance between Telegram bot and web app games using shared User model from models.py
- Added house balance tracking to web app games (Crash, Plinko, Limbo, Mines)
- When player wins: house balance decreases by the profit amount
- When player loses: house balance increases by the bet amount
- Fixed Telegram Bot import conflicts by properly managing `python-telegram-bot` installation.
- Configured workflows for both Flask web server and Telegram Bot.
- Updated `telegram_bot.py` to handle v20+ imports safely.

## Key Files
- `models.py` - SQLAlchemy database models (User, Game, Transaction, GlobalState)
- `app.py` - Flask web app with game endpoints and house balance tracking
- `blackjack.py` - Blackjack game logic
- `predict_handler.py` - Prediction/match betting handler

## Environment Variables
Required:
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `DATABASE_URL` - PostgreSQL connection string

Optional:
- `ADMIN_IDS` - Comma-separated list of Telegram user IDs who have admin privileges

## Running
- **Web App**: "Start application" workflow runs `gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app`
- **Telegram Bot**: "Telegram Bot" workflow runs `python telegram_bot.py`

## Database
Uses PostgreSQL (Replit built-in) with the following tables:
- `users` - User accounts with balance, stats, referrals
- `games` - Game history records
- `transactions` - Transaction history
- `global_state` - Key-value store for bot configuration

## Deployment
Configured for VM deployment since the bot needs to run continuously.
