# Antaria Casino Telegram Bot

## Overview
A Telegram casino bot that allows users to play various games like dice, blackjack, roulette, and coinflip. Uses PostgreSQL for database persistence and Flask-SQLAlchemy for ORM.

## Project Structure
- `main.py` - Main bot application with all command handlers and game logic
- `models.py` - SQLAlchemy database models (User, Game, Transaction, GlobalState)
- `blackjack.py` - Blackjack game logic
- `predict_handler.py` - Prediction/match betting handler

## Environment Variables
Required:
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `DATABASE_URL` - PostgreSQL connection string

Optional:
- `ADMIN_IDS` - Comma-separated list of Telegram user IDs who have admin privileges

## Running the Bot
The bot runs via the "Telegram Bot" workflow which executes `python main.py`.

## Database
Uses PostgreSQL with the following tables:
- `users` - User accounts with balance, stats, referrals
- `games` - Game history records
- `transactions` - Transaction history
- `global_state` - Key-value store for bot configuration

## Deployment
Configured for VM deployment since the bot needs to run continuously.
