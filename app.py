import os
import logging
import random
from flask import Flask, render_template, request, jsonify, session
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, User, GlobalState

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

def get_house_balance():
    """Get the current house balance"""
    state = GlobalState.query.filter_by(key='house_balance').first()
    if state and state.value:
        return state.value.get('amount', 0.0)
    return 0.0

def update_house_balance(change):
    """Update the house balance by a given amount (positive = house gains, negative = house loses)"""
    state = GlobalState.query.filter_by(key='house_balance').first()
    if not state:
        state = GlobalState(key='house_balance', value={'amount': 0.0})
        db.session.add(state)
    
    current = state.value.get('amount', 0.0) if state.value else 0.0
    state.value = {'amount': current + change}
    db.session.commit()
    return state.value['amount']

@app.before_request
def ensure_user():
    if 'user_id' not in session:
        user = User.query.first()
        if not user:
            user = User(user_id=12345, username="DemoUser", balance=1000.0)
            db.session.add(user)
            db.session.commit()
        session['user_id'] = user.id
    else:
        user = User.query.get(session['user_id'])
        if not user:
            user = User.query.first()
            if not user:
                user = User(user_id=12345, username="DemoUser", balance=1000.0)
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
    return render_template('plinko.html')

@app.route('/limbo')
def limbo_page():
    return render_template('limbo.html')

@app.route('/mines')
def mines_page():
    return render_template('mines.html')

@app.route('/keno')
def keno_page():
    return render_template('keno.html')

@app.route('/slots')
def slots_page():
    return render_template('slots.html')

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
    
    if game == 'crash':
        r = random.random()
        if r < 0.03:
            crash_point = 1.00
        else:
            crash_point = 0.99 / (1 - random.random())
            crash_point = max(1.01, round(crash_point, 2))
        
        session['last_bet'] = bet
        session['crash_point'] = crash_point
        return jsonify({'crash_point': crash_point})
    
    if game == 'plinko':
        mult = random.choice([0.3, 0.6, 1.1, 2, 4, 11, 33])
        payout = bet * mult
        profit = payout - bet
        
        user.balance += payout
        update_house_balance(-profit)
        db.session.commit()
        return jsonify({'multiplier': mult, 'payout': payout, 'balance': user.balance})

    if game == 'limbo':
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
    bet = session.get('mines_bet', 0)
    
    if index in revealed:
        return jsonify({'error': 'Already revealed'}), 400
        
    revealed.append(index)
    session['mines_revealed'] = revealed
    
    is_mine = board[index]
    if is_mine:
        update_house_balance(bet)
        session['mines_board'] = None
        session['mines_bet'] = 0
        return jsonify({'is_mine': True})
    
    def fact(n):
        res = 1
        for i in range(2, n + 1): res *= i
        return res
    
    def nCr(n, r):
        return fact(n) // (fact(r) * fact(n - r))
    
    rev_count = len(revealed)
    mult = 0.99 * (nCr(25, rev_count) / nCr(25 - mines_count, rev_count))
    
    return jsonify({'is_mine': False, 'multiplier': round(mult, 2)})

@app.route('/api/mines/cashout', methods=['POST'])
def mines_cashout():
    bet = session.get('mines_bet', 0)
    revealed = session.get('mines_revealed', [])
    mines_count = session.get('mines_count', 3)
    
    if not bet or not revealed:
        return jsonify({'error': 'No active game'}), 400
    
    def fact(n):
        res = 1
        for i in range(2, n + 1): res *= i
        return res
    
    def nCr(n, r):
        return fact(n) // (fact(r) * fact(n - r))
    
    rev_count = len(revealed)
    mult = 0.99 * (nCr(25, rev_count) / nCr(25 - mines_count, rev_count))
    payout = bet * mult
    profit = payout - bet
    
    user = User.query.get(session['user_id'])
    user.balance += payout
    update_house_balance(-profit)
    db.session.commit()
    
    session['mines_board'] = None
    session['mines_bet'] = 0
    session['mines_revealed'] = []
    
    return jsonify({'payout': payout, 'balance': user.balance})

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
            profit = payout - bet
            user.balance += payout
            update_house_balance(-profit)
            db.session.commit()
            return jsonify({'payout': payout, 'balance': user.balance})
        else:
            update_house_balance(bet)
            db.session.commit()
            return jsonify({'payout': 0, 'balance': user.balance})
    
    if game == 'limbo':
        target = float(data.get('target', 2.0))
        result = session.get('limbo_result', 0)
        if result >= target:
            payout = bet * target
            profit = payout - bet
            user.balance += payout
            update_house_balance(-profit)
            db.session.commit()
            return jsonify({'payout': payout, 'balance': user.balance, 'result': result, 'win': True})
        else:
            update_house_balance(bet)
            db.session.commit()
            return jsonify({'payout': 0, 'balance': user.balance, 'result': result, 'win': False})
            
    return jsonify({'error': 'Invalid result'}), 400

@app.route('/api/house_balance')
def house_balance():
    return jsonify({'house_balance': get_house_balance()})

@app.route('/health')
def health():
    return {'status': 'ok'}
