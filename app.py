import os
import logging
from flask import Flask, render_template_string
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
db.init_app(app)

with app.app_context():
    db.create_all()

@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>Antaria Casino Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
        }
        .container {
            text-align: center;
            padding: 40px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            max-width: 500px;
            margin: 20px;
        }
        h1 {
            font-size: 2.5rem;
            margin-bottom: 20px;
            background: linear-gradient(45deg, #e94560, #f39c12);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .emoji { font-size: 4rem; margin-bottom: 20px; }
        p { font-size: 1.1rem; line-height: 1.6; opacity: 0.9; margin-bottom: 20px; }
        .status {
            display: inline-block;
            padding: 10px 20px;
            background: rgba(39, 174, 96, 0.3);
            border: 1px solid #27ae60;
            border-radius: 20px;
            font-size: 0.9rem;
        }
        .status::before { content: "●"; margin-right: 8px; color: #27ae60; }
    </style>
</head>
<body>
    <div class="container">
        <div class="emoji">🎰</div>
        <h1>Antaria Casino Bot</h1>
        <p>Welcome to Antaria Casino! This is a Telegram-based casino bot featuring dice games, blackjack, roulette, coinflip, and more!</p>
        <div class="status">Bot is running</div>
    </div>
</body>
</html>
''')

@app.route('/health')
def health():
    return {'status': 'ok'}
