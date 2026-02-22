import os
import logging
import random
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, User, GlobalState, Game

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
    state = GlobalState.query.filter_by(key='house_balance').first()
    if state and state.value:
        return state.value.get('amount', 0.0)
    return 0.0

def update_house_balance(change):
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
    public_endpoints = ['login', 'do_login', 'static']
    if request.endpoint in public_endpoints:
        return
    
    if 'telegram_user_id' not in session:
        if request.endpoint not in ['login', 'do_login']:
            return redirect(url_for('login'))
        return
    
    telegram_user_id = session.get('telegram_user_id')
    user = User.query.filter_by(user_id=telegram_user_id).first()
    if not user:
        session.pop('telegram_user_id', None)
        return redirect(url_for('login'))

@app.route('/login')
def login():
    if 'telegram_user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def do_login():
    telegram_id = request.form.get('telegram_id', '').strip()
    
    if not telegram_id:
        return render_template('login.html', error='Please enter your Telegram User ID')
    
    try:
        telegram_id = int(telegram_id)
    except ValueError:
        return render_template('login.html', error='Invalid Telegram User ID. It should be a number.')
    
    user = User.query.filter_by(user_id=telegram_id).first()
    if not user:
        return render_template('login.html', error='No account found with this Telegram User ID. Please use the Telegram bot first to create an account.')
    
    session['telegram_user_id'] = telegram_id
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('telegram_user_id', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/crash')
def crash_page():
    return "Game currently unavailable. Please check back later.", 503

@app.route('/plinko')
def plinko_page():
    return "Game currently unavailable. Please check back later.", 503

@app.route('/limbo')
def limbo_page():
    return "Game currently unavailable. Please check back later.", 503

@app.route('/mines')
def mines_page():
    return "Game currently unavailable. Please check back later.", 503

@app.route('/keno')
def keno_page():
    return "Game currently unavailable. Please check back later.", 503

@app.route('/slots')
def slots_page():
    return "Game currently unavailable. Please check back later.", 503

@app.route('/api/user')
def get_user():
    telegram_user_id = session.get('telegram_user_id')
    user = User.query.filter_by(user_id=telegram_user_id).first()
    return jsonify({
        'username': user.username,
        'balance': user.balance
    })

@app.route('/api/play', methods=['POST'])
def play_game():
    data = request.json
    game = data.get('game')
    bet = float(data.get('bet', 0))
    
    telegram_user_id = session.get('telegram_user_id')
    user = User.query.filter_by(user_id=telegram_user_id).first()
    if bet > user.balance or bet <= 0:
        return jsonify({'error': 'Insufficient balance'}), 400
        
    user.balance -= bet
    db.session.commit()
    
    session['last_bet'] = bet
    
    if game == 'crash':
        r = random.random()
        crash_point = 1.00 if r < 0.03 else max(1.01, round(0.99 / (1 - random.random()), 2))
        session['crash_point'] = crash_point
        
        # We'll need a way to record crash games when the user cashes out.
        # For now, let's at least ensure we track the bet.
        telegram_user_id = session.get('telegram_user_id')
        user = User.query.filter_by(user_id=telegram_user_id).first()
        user.total_wagered = (user.total_wagered or 0) + bet
        db.session.commit()

        return jsonify({'crash_point': crash_point})
    
    if game == 'plinko':
        mult = random.choice([0.3, 0.6, 1.1, 2, 4, 11, 33])
        payout = bet * mult
        profit = payout - bet
        user.balance += payout

        # Update stats
        user.total_wagered = (user.total_wagered or 0) + bet
        user.total_won = (user.total_won or 0) + payout
        user.total_pnl = (user.total_pnl or 0) + profit
        user.games_played = (user.games_played or 0) + 1
        if profit > 0:
            user.games_won = (user.games_won or 0) + 1
            user.win_streak = (user.win_streak or 0) + 1
            if user.win_streak > (user.best_win_streak or 0):
                user.best_win_streak = user.win_streak
        else:
            user.win_streak = 0

        # Add to weekly bonus pool (0.1% rakeback)
        achievements = user.achievements or {}
        pool = achievements.get('weekly_bonus_pool', 0)
        # Calculate bonus percentage: base 0.1% + 20% if @davaulte in username
        bonus_percent = 0.001
        if user.username and '@davaulte' in user.username:
            bonus_percent = 0.0012  # 0.1% + 20% boost

        achievements['weekly_bonus_pool'] = round(pool + bet * bonus_percent, 2)
        user.achievements = achievements

        update_house_balance(-profit)
        db.session.commit()

        # Record game
        game_record = Game(data={
            'game': 'plinko',
            'user_id': user.user_id,
            'player_id': user.user_id,
            'bet': bet,
            'wager': bet,
            'multiplier': mult,
            'payout': payout,
            'profit': profit,
            'result': 'win' if profit > 0 else 'loss'
        })
        db.session.add(game_record)
        db.session.commit()

        return jsonify({'multiplier': mult, 'payout': payout, 'balance': user.balance})

    if game == 'limbo':
        r = max(1.00, round(0.99 / (1 - random.random()), 2))
        session['limbo_result'] = r
        
        # Record game and update stats for Limbo
        target = float(data.get('target', 2.0))
        payout = bet * target if r >= target else 0
        profit = payout - bet
        
        telegram_user_id = session.get('telegram_user_id')
        user = User.query.filter_by(user_id=telegram_user_id).first()
        user.balance += payout
        user.total_wagered = (user.total_wagered or 0) + bet
        user.total_won = (user.total_won or 0) + payout
        user.total_pnl = (user.total_pnl or 0) + profit
        user.games_played = (user.games_played or 0) + 1
        if profit > 0:
            user.games_won = (user.games_won or 0) + 1
            user.win_streak = (user.win_streak or 0) + 1
            if user.win_streak > (user.best_win_streak or 0):
                user.best_win_streak = user.win_streak
        else:
            user.win_streak = 0

        # Record game
        game_record = Game(data={
            'game': 'limbo',
            'user_id': user.user_id,
            'player_id': user.user_id,
            'bet': bet,
            'wager': bet,
            'result': r,
            'target': target,
            'payout': payout,
            'profit': profit,
            'result_type': 'win' if profit > 0 else 'loss'
        })
        db.session.add(game_record)
        db.session.commit()
        
        return jsonify({'result': r, 'payout': payout, 'balance': user.balance})

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
        
        # Update stats for loss
        telegram_user_id = session.get('telegram_user_id')
        user = User.query.filter_by(user_id=telegram_user_id).first()
        user.total_wagered = (user.total_wagered or 0) + bet
        user.total_pnl = (user.total_pnl or 0) - bet
        user.games_played = (user.games_played or 0) + 1
        user.win_streak = 0
        
        # Weekly bonus pool
        achievements = user.achievements or {}
        pool = achievements.get('weekly_bonus_pool', 0)
        
        # Calculate bonus percentage: base 0.1% + 20% if @davaulte in username
        bonus_percent = 0.001
        if user.username and '@davaulte' in user.username:
            bonus_percent = 0.0012  # 0.1% + 20% boost
            
        achievements['weekly_bonus_pool'] = round(pool + bet * bonus_percent, 2)
        user.achievements = achievements
        
        # Record game
        game_record = Game(data={
            'game': 'mines',
            'user_id': user.user_id,
            'bet': bet,
            'result': 'loss',
            'payout': 0
        })
        db.session.add(game_record)
        db.session.commit()

        session['mines_board'] = None
        session['mines_bet'] = 0
        return jsonify({'is_mine': True})
    
    def fact(n):
        res = 1
        for i in range(2, n + 1): res *= i
        return res
    
    def nCr(n, r):
        if r < 0 or r > n: return 0
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
        if r < 0 or r > n: return 0
        return fact(n) // (fact(r) * fact(n - r))
    
    rev_count = len(revealed)
    mult = 0.99 * (nCr(25, rev_count) / nCr(25 - mines_count, rev_count))
    payout = bet * mult
    profit = payout - bet
    
    telegram_user_id = session.get('telegram_user_id')
    user = User.query.filter_by(user_id=telegram_user_id).first()
    user.balance += payout

    # Update stats
    user.total_wagered = (user.total_wagered or 0) + bet
    user.total_pnl = (user.total_pnl or 0) + profit
    user.games_played = (user.games_played or 0) + 1
    user.games_won = (user.games_won or 0) + 1
    user.total_won = (user.total_won or 0) + payout
    user.win_streak = (user.win_streak or 0) + 1
    if user.win_streak > (user.best_win_streak or 0):
        user.best_win_streak = user.win_streak

    # Weekly bonus pool
    achievements = user.achievements or {}
    pool = achievements.get('weekly_bonus_pool', 0)
    
    # Calculate bonus percentage: base 0.1% + 20% if @davaulte in username
    bonus_percent = 0.001
    if user.username and '@davaulte' in user.username:
        bonus_percent = 0.0012  # 0.1% + 20% boost
        
    achievements['weekly_bonus_pool'] = round(pool + bet * bonus_percent, 2)
    user.achievements = achievements

    update_house_balance(-profit)
    db.session.commit()
    
    # Record game
    game_record = Game(data={
        'game': 'mines',
        'user_id': user.user_id,
        'bet': bet,
        'multiplier': round(mult, 2),
        'payout': payout,
        'result': 'win'
    })
    db.session.add(game_record)
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
    
    telegram_user_id = session.get('telegram_user_id')
    user = User.query.filter_by(user_id=telegram_user_id).first()
    bet = session.get('last_bet', 0)
    
    if bet <= 0:
        return jsonify({'error': 'No active bet found'}), 400

    # Verification for Crash/Limbo
    if game == 'crash':
        cp = session.get('crash_point', 0)
        if multiplier > cp: multiplier = 0
    elif game == 'limbo':
        lr = session.get('limbo_result', 0)
        target = float(data.get('target', 2.0))
        if lr < target: multiplier = 0

    payout = bet * multiplier
    profit = payout - bet
    
    user.balance += payout

    # Update stats
    user.total_wagered = (user.total_wagered or 0) + bet
    user.total_pnl = (user.total_pnl or 0) + profit
    user.games_played = (user.games_played or 0) + 1
    if profit > 0:
        user.games_won = (user.games_won or 0) + 1
        user.total_won = (user.total_won or 0) + payout
        user.win_streak = (user.win_streak or 0) + 1
        if user.win_streak > (user.best_win_streak or 0):
            user.best_win_streak = user.win_streak
    else:
        user.win_streak = 0

    # Add to weekly bonus pool (0.1% rakeback)
    achievements = user.achievements or {}
    pool = achievements.get('weekly_bonus_pool', 0)
    
    # Calculate bonus percentage: base 0.1% + 20% if @davaulte in username
    bonus_percent = 0.001
    if user.username and '@davaulte' in user.username:
        bonus_percent = 0.0012  # 0.1% + 20% boost
        
    achievements['weekly_bonus_pool'] = round(pool + bet * bonus_percent, 2)
    user.achievements = achievements

    update_house_balance(-profit)
    db.session.commit()
    
    # Record game
    game_record = Game(data={
        'game': game,
        'user_id': user.user_id,
        'bet': bet,
        'multiplier': multiplier,
        'payout': payout,
        'profit': profit,
        'result': 'win' if profit > 0 else 'loss'
    })
    db.session.add(game_record)
    db.session.commit()
    
    session['last_bet'] = 0
    
    return jsonify({'payout': payout, 'balance': user.balance})

@app.route('/api/house_balance')
def house_balance():
    return jsonify({'house_balance': get_house_balance()})

@app.route('/health')
def health():
    return {'status': 'ok'}
