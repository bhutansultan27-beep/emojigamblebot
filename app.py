import os
import logging
import random
import time
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

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

# Simplified models for the web app
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, unique=True)
    username = db.Column(db.String(64))
    balance = db.Column(db.Float, default=1000.0)

with app.app_context():
    db.create_all()

@app.before_request
def ensure_user():
    # Mocking user session for demo/migration
    if 'user_id' not in session:
        user = User.query.first()
        if not user:
            user = User(telegram_id=12345, username="DemoUser", balance=1000.0)
            db.session.add(user)
            db.session.commit()
        session['user_id'] = user.id

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/crash')
def crash_page():
    return render_template('crash.html')

@app.route('/plinko')
def plinko_page():
    return render_template('plinko.html') # Need to create this

@app.route('/limbo')
def limbo_page():
    return render_template('limbo.html') # Need to create this

@app.route('/mines')
def mines_page():
    return render_template('mines.html') # Need to create this

@app.route('/api/user')
def get_user():
    user = User.query.get(session['user_id'])
    return jsonify({
        'username': user.username,
        'balance': user.balance
    })

@app.route('/api/play', methods=['POST'])
def play_game():
    data = request.json
    game = data.get('game')
    bet = float(data.get('bet', 0))
    
    user = User.query.get(session['user_id'])
    if bet > user.balance or bet <= 0:
        return jsonify({'error': 'Insufficient balance'}), 400
        
    user.balance -= bet
    db.session.commit()
    
    # Game specific logic
    if game == 'crash':
        # Generate crash point (99% RTP logic)
        r = random.random()
        if r < 0.03: # 3% chance of instant crash
            crash_point = 1.00
        else:
            crash_point = 0.99 / (1 - random.random())
            crash_point = max(1.01, round(crash_point, 2))
        
        session['last_bet'] = bet
        session['crash_point'] = crash_point
        return jsonify({'crash_point': crash_point})
    
    if game == 'plinko':
        # Simple random outcome for plinko
        mult = random.choice([0.3, 0.6, 1.1, 2, 4, 11, 33])
        payout = bet * mult
        user.balance += payout
        db.session.commit()
        return jsonify({'multiplier': mult, 'payout': payout})

    if game == 'limbo':
        # Generate limbo result
        r = 0.99 / (1 - random.random())
        r = max(1.00, round(r, 2))
        session['last_bet'] = bet
        session['limbo_result'] = r
        return jsonify({'result': r})

    if game == 'mines':
        mines_count = int(data.get('mines', 3))
        board = [False] * 25
        indices = list(range(25))
        random.shuffle(indices)
        for i in range(mines_count):
            board[indices[i]] = True
        
        session['mines_board'] = board
        session['mines_bet'] = bet
        session['mines_count'] = mines_count
        session['mines_revealed'] = []
        return jsonify({'status': 'started'})
        
    return jsonify({'status': 'ok'})

@app.route('/api/mines/reveal', methods=['POST'])
def mines_reveal():
    index = request.json.get('index')
    board = session.get('mines_board')
    revealed = session.get('mines_revealed', [])
    mines_count = session.get('mines_count', 3)
    
    if index in revealed:
        return jsonify({'error': 'Already revealed'}), 400
        
    revealed.append(index)
    session['mines_revealed'] = revealed
    
    is_mine = board[index]
    if is_mine:
        return jsonify({'is_mine': True})
    
    # Calculate multiplier: (25! / (25-rev)!) / ((25-mines)! / (25-mines-rev)!)
    def fact(n):
        res = 1
        for i in range(2, n + 1): res *= i
        return res
    
    def nCr(n, r):
        return fact(n) // (fact(r) * fact(n - r))
    
    rev_count = len(revealed)
    # Simplified multiplier logic for mines
    mult = 0.99 * (nCr(25, rev_count) / nCr(25 - mines_count, rev_count))
    
    return jsonify({'is_mine': False, 'multiplier': round(mult, 2)})

@app.route('/api/result', methods=['POST'])
def game_result():
    data = request.json
    game = data.get('game')
    multiplier = float(data.get('multiplier', 0))
    
    user = User.query.get(session['user_id'])
    bet = session.get('last_bet', 0)
    
    if game == 'crash':
        crash_point = session.get('crash_point', 0)
        if multiplier <= crash_point:
            payout = bet * multiplier
            user.balance += payout
            db.session.commit()
            return jsonify({'payout': payout, 'balance': user.balance})
            
    return jsonify({'error': 'Invalid result'}), 400

@app.route('/health')
def health():
    return {'status': 'ok'}
