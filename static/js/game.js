
const { useState, useEffect, useCallback, useRef, useMemo } = React;

const SoundEngine = {
  ctx: null,
  enabled: true,
  init: () => {
    if (!SoundEngine.ctx) { SoundEngine.ctx = new (window.AudioContext || window.webkitAudioContext)(); }
    if (SoundEngine.ctx.state === 'suspended') SoundEngine.ctx.resume();
  },
  play: (type) => {
    if (!SoundEngine.enabled) return;
    if (!SoundEngine.ctx) SoundEngine.init();
    const ctx = SoundEngine.ctx;
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);

    if (type === 'click') {
        osc.frequency.setValueAtTime(600, t); osc.frequency.exponentialRampToValueAtTime(300, t + 0.1);
        gain.gain.setValueAtTime(0.05, t); gain.gain.exponentialRampToValueAtTime(0.001, t + 0.1);
        osc.start(t); osc.stop(t + 0.1);
    } else if (type === 'win') {
        [523.25, 659.25, 783.99, 1046.50].forEach((f, i) => {
            const o = ctx.createOscillator(); const g = ctx.createGain();
            o.connect(g); g.connect(ctx.destination); o.type='triangle'; o.frequency.value=f;
            g.gain.setValueAtTime(0, t+i*0.1); g.gain.linearRampToValueAtTime(0.05, t+i*0.1+0.05); g.gain.exponentialRampToValueAtTime(0.001, t+i*0.1+0.3);
            o.start(t+i*0.1); o.stop(t+i*0.1+0.3);
        });
    } else if (type === 'loss') {
        osc.type='sawtooth'; osc.frequency.setValueAtTime(100, t); osc.frequency.linearRampToValueAtTime(50, t+0.3);
        gain.gain.setValueAtTime(0.1, t); gain.gain.linearRampToValueAtTime(0, t+0.3);
        osc.start(t); osc.stop(t+0.3);
    } else if (type === 'tick') {
        osc.frequency.setValueAtTime(800, t);
        gain.gain.setValueAtTime(0.02, t); gain.gain.exponentialRampToValueAtTime(0.001, t + 0.05);
        osc.start(t); osc.stop(t + 0.05);
    }
  }
};

// --- CRASH GAME ---
const CrashGame = ({ balance, onResult }) => {
    const [bet, setBet] = useState(10);
    const [playing, setPlaying] = useState(false);
    const [crashed, setCrashed] = useState(false);
    const [cashedOut, setCashedOut] = useState(false);
    const [multiplier, setMultiplier] = useState(1.00);
    const [crashPoint, setCrashPoint] = useState(null);
    const [history, setHistory] = useState([]);
    const intervalRef = useRef(null);

    const startGame = async () => {
        if (balance < bet || playing) return;
        setPlaying(true);
        setCrashed(false);
        setCashedOut(false);
        setMultiplier(1.00);

        const res = await fetch('/api/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game: 'crash', bet })
        });
        const data = await res.json();
        const cp = data.crash_point;
        setCrashPoint(cp);

        let m = 1.00;
        intervalRef.current = setInterval(() => {
            m += 0.01 * (1 + (m - 1) * 0.1);
            m = Math.round(m * 100) / 100;
            setMultiplier(m);
            SoundEngine.play('tick');

            if (m >= cp) {
                clearInterval(intervalRef.current);
                setCrashed(true);
                setPlaying(false);
                SoundEngine.play('loss');
                setHistory(h => [{ mult: cp, won: false }, ...h.slice(0, 9)]);
                fetch('/api/result', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ game: 'crash', multiplier: 0 })
                }).then(() => onResult());
            }
        }, 100);
    };

    const cashOut = async () => {
        if (!playing || crashed || cashedOut) return;
        clearInterval(intervalRef.current);
        setCashedOut(true);
        setPlaying(false);
        SoundEngine.play('win');
        setHistory(h => [{ mult: multiplier, won: true }, ...h.slice(0, 9)]);

        await fetch('/api/result', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game: 'crash', multiplier })
        });
        onResult();
    };

    useEffect(() => {
        return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
    }, []);

    const getColor = () => {
        if (crashed) return 'text-red-500';
        if (cashedOut) return 'text-emerald-400';
        if (multiplier >= 2) return 'text-emerald-400';
        return 'text-white';
    };

    return (
        <div className="max-w-3xl mx-auto">
            <div className="bg-slate-900 rounded-[2rem] border border-white/5 shadow-2xl overflow-hidden">
                <div className="h-64 bg-slate-950 flex items-center justify-center relative">
                    <div className={`text-7xl font-black ${getColor()} transition-colors`}>
                        {multiplier.toFixed(2)}x
                    </div>
                    {crashed && <div className="absolute bottom-4 text-red-500 font-bold text-lg animate-pulse">CRASHED!</div>}
                    {cashedOut && <div className="absolute bottom-4 text-emerald-400 font-bold text-lg">Cashed out at {multiplier.toFixed(2)}x!</div>}
                </div>
                <div className="p-6 space-y-4">
                    <div className="flex gap-4">
                        <div className="flex-1">
                            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Bet Amount</label>
                            <input type="number" value={bet} onChange={e => setBet(parseFloat(e.target.value))} disabled={playing}
                                className="w-full bg-slate-950 border border-white/10 rounded-xl px-4 py-3 font-mono text-white focus:outline-none focus:border-indigo-500" />
                        </div>
                        <div className="flex-1 flex items-end">
                            {!playing ? (
                                <button onClick={startGame} disabled={balance < bet}
                                    className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 py-3 rounded-xl font-black uppercase tracking-widest transition-all">
                                    BET
                                </button>
                            ) : (
                                <button onClick={cashOut}
                                    className="w-full bg-emerald-600 hover:bg-emerald-500 py-3 rounded-xl font-black uppercase tracking-widest transition-all animate-pulse">
                                    CASH OUT ({(bet * multiplier).toFixed(2)})
                                </button>
                            )}
                        </div>
                    </div>
                    {history.length > 0 && (
                        <div className="flex gap-2 flex-wrap">
                            {history.map((h, i) => (
                                <span key={i} className={`text-xs px-2 py-1 rounded-full font-bold ${h.won ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                                    {h.mult.toFixed(2)}x
                                </span>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

// --- LIMBO GAME ---
const LimboGame = ({ balance, onResult }) => {
    const [bet, setBet] = useState(10);
    const [target, setTarget] = useState(2.0);
    const [playing, setPlaying] = useState(false);
    const [result, setResult] = useState(null);
    const [history, setHistory] = useState([]);

    const play = async () => {
        if (balance < bet || playing) return;
        setPlaying(true);
        setResult(null);

        const res = await fetch('/api/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game: 'limbo', bet })
        });
        const data = await res.json();
        const limboResult = data.result;

        // Animate counting
        let count = 1.0;
        const countTo = limboResult;
        const steps = 30;
        const increment = (countTo - 1.0) / steps;
        let step = 0;

        const interval = setInterval(() => {
            count += increment;
            step++;
            setResult(Math.max(1.0, count).toFixed(2));
            SoundEngine.play('tick');

            if (step >= steps) {
                clearInterval(interval);
                setResult(limboResult.toFixed(2));
                const won = limboResult >= target;
                const mult = won ? target : 0;
                setHistory(h => [{ result: limboResult, target, won }, ...h.slice(0, 9)]);
                
                if (won) SoundEngine.play('win');
                else SoundEngine.play('loss');
                
                setPlaying(false);
                fetch('/api/result', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ game: 'limbo', multiplier: mult, target })
                }).then(() => onResult());
            }
        }, 50);
    };

    const payout = (bet * target).toFixed(2);

    return (
        <div className="max-w-xl mx-auto p-8 bg-slate-900 rounded-[2.5rem] border border-white/5 shadow-2xl">
            <h2 className="text-3xl font-black mb-8 text-center bg-gradient-to-r from-emerald-400 to-cyan-400 bg-clip-text text-transparent">LIMBO</h2>
            
            <div className="h-40 bg-slate-950 rounded-2xl flex items-center justify-center mb-6">
                <div className={`text-6xl font-black ${result !== null ? (parseFloat(result) >= target ? 'text-emerald-400' : 'text-red-500') : 'text-slate-600'}`}>
                    {result !== null ? `${result}x` : '?.??x'}
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4 mb-6">
                <div>
                    <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Bet Amount</label>
                    <input type="number" value={bet} onChange={e => setBet(parseFloat(e.target.value))} disabled={playing}
                        className="w-full bg-slate-950 border border-white/10 rounded-xl px-4 py-3 font-mono text-white focus:outline-none focus:border-indigo-500" />
                </div>
                <div>
                    <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Target ({payout})</label>
                    <input type="number" step="0.1" min="1.01" value={target} onChange={e => setTarget(Math.max(1.01, parseFloat(e.target.value) || 1.01))} disabled={playing}
                        className="w-full bg-slate-950 border border-white/10 rounded-xl px-4 py-3 font-mono text-white focus:outline-none focus:border-indigo-500" />
                </div>
            </div>

            <button onClick={play} disabled={playing || balance < bet}
                className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 py-4 rounded-xl font-black uppercase tracking-widest transition-all">
                {playing ? 'ROLLING...' : 'PLAY LIMBO'}
            </button>

            {history.length > 0 && (
                <div className="flex gap-2 flex-wrap mt-4">
                    {history.map((h, i) => (
                        <span key={i} className={`text-xs px-2 py-1 rounded-full font-bold ${h.won ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                            {h.result.toFixed(2)}x
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
};

// --- MINES GAME ---
const MinesGame = ({ balance, onResult }) => {
    const [bet, setBet] = useState(10);
    const [mineCount, setMineCount] = useState(3);
    const [board, setBoard] = useState(Array(25).fill('hidden'));
    const [playing, setPlaying] = useState(false);
    const [gameOver, setGameOver] = useState(false);
    const [multiplier, setMultiplier] = useState(1.00);
    const [revealed, setRevealed] = useState(0);

    const startGame = async () => {
        if (balance < bet || playing) return;
        
        const res = await fetch('/api/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game: 'mines', bet, mines: mineCount })
        });
        const data = await res.json();
        if (data.status === 'started') {
            setBoard(Array(25).fill('hidden'));
            setPlaying(true);
            setGameOver(false);
            setMultiplier(1.00);
            setRevealed(0);
            onResult();
        }
    };

    const revealTile = async (index) => {
        if (!playing || gameOver || board[index] !== 'hidden') return;
        SoundEngine.play('click');

        const res = await fetch('/api/mines/reveal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index })
        });
        const data = await res.json();

        const newBoard = [...board];
        if (data.is_mine) {
            newBoard[index] = 'mine';
            setBoard(newBoard);
            setGameOver(true);
            setPlaying(false);
            SoundEngine.play('loss');
            onResult();
        } else {
            newBoard[index] = 'gem';
            setBoard(newBoard);
            setMultiplier(data.multiplier);
            setRevealed(r => r + 1);
            SoundEngine.play('win');
        }
    };

    const cashOut = async () => {
        if (!playing || gameOver || revealed === 0) return;
        
        const res = await fetch('/api/mines/cashout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        setGameOver(true);
        setPlaying(false);
        SoundEngine.play('win');
        onResult();
    };

    return (
        <div className="max-w-xl mx-auto p-6 bg-slate-900 rounded-[2rem] border border-white/5 shadow-2xl">
            <h2 className="text-3xl font-black mb-6 text-center bg-gradient-to-r from-rose-400 to-orange-400 bg-clip-text text-transparent">MINES</h2>
            
            <div className="grid grid-cols-5 gap-2 mb-6">
                {board.map((cell, i) => (
                    <button key={i} onClick={() => revealTile(i)} disabled={!playing || cell !== 'hidden'}
                        className={`aspect-square rounded-xl font-bold text-xl flex items-center justify-center transition-all transform active:scale-90
                            ${cell === 'hidden' ? 'bg-slate-800 hover:bg-slate-700 border border-white/5' : ''}
                            ${cell === 'gem' ? 'bg-emerald-500/20 border-2 border-emerald-500' : ''}
                            ${cell === 'mine' ? 'bg-red-500/20 border-2 border-red-500' : ''}
                        `}>
                        {cell === 'gem' && '💎'}
                        {cell === 'mine' && '💣'}
                    </button>
                ))}
            </div>

            <div className="flex gap-4 mb-4">
                <div className="flex-1">
                    <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Bet</label>
                    <input type="number" value={bet} onChange={e => setBet(parseFloat(e.target.value))} disabled={playing}
                        className="w-full bg-slate-950 border border-white/10 rounded-xl px-4 py-3 font-mono text-white focus:outline-none focus:border-indigo-500" />
                </div>
                <div className="flex-1">
                    <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Mines</label>
                    <select value={mineCount} onChange={e => setMineCount(parseInt(e.target.value))} disabled={playing}
                        className="w-full bg-slate-950 border border-white/10 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-indigo-500">
                        {[1,2,3,5,10,15,20,24].map(n => <option key={n} value={n}>{n}</option>)}
                    </select>
                </div>
            </div>

            {playing && revealed > 0 && (
                <div className="text-center mb-4">
                    <span className="text-emerald-400 font-bold text-lg">{multiplier.toFixed(2)}x</span>
                    <span className="text-slate-500 ml-2">(${(bet * multiplier).toFixed(2)})</span>
                </div>
            )}

            {!playing ? (
                <button onClick={startGame} disabled={balance < bet}
                    className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 py-4 rounded-xl font-black uppercase tracking-widest transition-all">
                    {gameOver ? 'PLAY AGAIN' : 'START GAME'}
                </button>
            ) : (
                <button onClick={cashOut} disabled={revealed === 0}
                    className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 py-4 rounded-xl font-black uppercase tracking-widest transition-all">
                    CASH OUT (${(bet * multiplier).toFixed(2)})
                </button>
            )}
        </div>
    );
};

// --- PLINKO GAME ---
const PlinkoGame = ({ balance, onResult }) => {
    const [bet, setBet] = useState(10);
    const [playing, setPlaying] = useState(false);
    const [lastResult, setLastResult] = useState(null);
    const [history, setHistory] = useState([]);
    const multipliers = [0.3, 0.6, 1.1, 2, 4, 11, 33];
    const labels = ['0.3x', '0.6x', '1.1x', '2x', '4x', '11x', '33x'];
    const colors = ['bg-red-500', 'bg-orange-500', 'bg-yellow-500', 'bg-emerald-500', 'bg-blue-500', 'bg-indigo-500', 'bg-purple-500'];

    const drop = async () => {
        if (balance < bet || playing) return;
        setPlaying(true);
        setLastResult(null);
        SoundEngine.play('click');

        const res = await fetch('/api/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game: 'plinko', bet })
        });
        const data = await res.json();
        
        // Animate ball bouncing
        await new Promise(resolve => setTimeout(resolve, 1500));
        
        const mult = data.multiplier;
        const payout = data.payout;
        setLastResult({ mult, payout });
        setHistory(h => [{ mult, payout }, ...h.slice(0, 9)]);
        
        if (mult > 1) SoundEngine.play('win');
        else SoundEngine.play('loss');
        
        setPlaying(false);
        onResult();
    };

    return (
        <div className="max-w-xl mx-auto p-8 bg-slate-900 rounded-[2.5rem] border border-white/5 shadow-2xl">
            <h2 className="text-3xl font-black mb-8 text-center bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">PLINKO</h2>
            
            <div className="bg-slate-950 rounded-2xl p-6 mb-6">
                {/* Peg visualization */}
                <div className="space-y-3 mb-6">
                    {[3,4,5,6,7].map(row => (
                        <div key={row} className="flex justify-center gap-4">
                            {Array(row).fill(0).map((_, i) => (
                                <div key={i} className="w-2 h-2 bg-slate-600 rounded-full"></div>
                            ))}
                        </div>
                    ))}
                </div>

                {/* Multiplier slots */}
                <div className="flex gap-1 justify-center">
                    {multipliers.map((m, i) => (
                        <div key={i} className={`${colors[i]} ${lastResult && lastResult.mult === m ? 'ring-2 ring-white scale-110' : 'opacity-70'} 
                            rounded-lg px-2 py-2 text-center transition-all`}>
                            <div className="text-xs font-black text-white">{labels[i]}</div>
                        </div>
                    ))}
                </div>
            </div>

            {lastResult && (
                <div className="text-center mb-4">
                    <span className={`text-2xl font-black ${lastResult.mult > 1 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {lastResult.mult}x
                    </span>
                    <span className="text-slate-400 ml-2">(${lastResult.payout.toFixed(2)})</span>
                </div>
            )}

            <div className="space-y-4">
                <div>
                    <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Bet Amount</label>
                    <input type="number" value={bet} onChange={e => setBet(parseFloat(e.target.value))} disabled={playing}
                        className="w-full bg-slate-950 border border-white/10 rounded-xl px-4 py-3 font-mono text-white focus:outline-none focus:border-indigo-500" />
                </div>
                <button onClick={drop} disabled={playing || balance < bet}
                    className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 py-4 rounded-xl font-black uppercase tracking-widest transition-all">
                    {playing ? 'DROPPING...' : 'DROP BALL'}
                </button>
            </div>

            {history.length > 0 && (
                <div className="flex gap-2 flex-wrap mt-4">
                    {history.map((h, i) => (
                        <span key={i} className={`text-xs px-2 py-1 rounded-full font-bold ${h.mult > 1 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                            {h.mult}x
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
};

// --- KENO GAME ---
const KenoGame = ({ balance, onResult }) => {
    const [bet, setBet] = useState(10);
    const [selected, setSelected] = useState([]);
    const [drawn, setDrawn] = useState([]);
    const [playing, setPlaying] = useState(false);

    const payouts = {
        1: [0, 3.8],
        2: [0, 1.7, 5.2],
        3: [0, 0, 2.7, 25],
        4: [0, 0, 1.5, 5, 80],
        5: [0, 0, 1.2, 3, 12, 300],
        6: [0, 0, 0, 1.5, 8, 20, 500],
        7: [0, 0, 0, 1.2, 4, 15, 100, 1000],
        8: [0, 0, 0, 1, 3, 8, 50, 500, 2000],
        9: [0, 0, 0, 1, 2, 4, 20, 100, 1000, 4000],
        10:[0, 0, 0, 0, 2, 4, 15, 60, 400, 2000, 5000]
    };

    const toggleNum = (n) => {
        if (playing) return;
        if (selected.includes(n)) setSelected(s => s.filter(x => x !== n));
        else if (selected.length < 10) setSelected(s => [...s, n]);
        SoundEngine.play('click');
    };

    const play = async () => {
        if (balance < bet || selected.length === 0 || playing) return;
        setPlaying(true);
        setDrawn([]);

        const res = await fetch('/api/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game: 'keno', bet })
        });
        const data = await res.json();
        
        // Simplified draw simulation
        const result = Array.from({length: 10}, () => Math.floor(Math.random() * 40) + 1);
        
        let i = 0;
        const interval = setInterval(() => {
            setDrawn(prev => [...prev, result[i]]);
            if (selected.includes(result[i])) SoundEngine.play('win');
            else SoundEngine.play('click');
            
            i++;
            if (i >= 10) {
                clearInterval(interval);
                const hits = result.filter(r => selected.includes(r)).length;
                const mult = payouts[selected.length][hits] || 0;
                setPlaying(false);
                if (mult > 0) SoundEngine.play('win'); else SoundEngine.play('loss');
                
                fetch('/api/result', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ game: 'keno', multiplier: mult })
                }).then(() => onResult());
            }
        }, 200);
    };

    return (
        <div className="max-w-4xl mx-auto p-6 bg-slate-900 rounded-[2rem] border border-white/5 shadow-2xl">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
                <div className="space-y-6">
                    <div className="space-y-2">
                        <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Bet Amount</label>
                        <input type="number" value={bet} onChange={e => setBet(parseFloat(e.target.value))} className="w-full bg-slate-950 border border-white/10 rounded-xl px-4 py-3 font-mono text-white focus:outline-none focus:border-indigo-500" />
                    </div>
                    <button onClick={play} disabled={playing || selected.length === 0} className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 py-4 rounded-xl font-black uppercase tracking-widest transition-all transform active:scale-95">
                        {playing ? 'DRAWING...' : 'PLAY KENO'}
                    </button>
                    <div className="text-[10px] text-slate-500 uppercase font-bold text-center">Hits: {drawn.filter(n => selected.includes(n)).length}</div>
                </div>
                <div className="md:col-span-3 grid grid-cols-8 gap-2">
                    {Array.from({length: 40}, (_, i) => i + 1).map(n => (
                        <button key={n} onClick={() => toggleNum(n)} className={`aspect-square rounded-lg font-bold transition-all transform active:scale-90 flex items-center justify-center text-sm
                            ${drawn.includes(n) ? (selected.includes(n) ? 'bg-emerald-500 text-slate-950 scale-110 z-10' : 'bg-slate-700 text-white') : 
                            (selected.includes(n) ? 'bg-indigo-500 text-white border-2 border-indigo-400' : 'bg-slate-800 text-slate-400 hover:bg-slate-700')}`}>
                            {n}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
};

// --- SLOTS GAME ---
const SlotsGame = ({ balance, onResult }) => {
    const [bet, setBet] = useState(10);
    const [spinning, setSpinning] = useState(false);
    const [reels, setReels] = useState([0, 0, 0]);
    const symbols = ['🍒', '🍋', '🍇', '🔔', '💎', '7️⃣', '🍀'];

    const spin = async () => {
        if (balance < bet || spinning) return;
        setSpinning(true);
        
        await fetch('/api/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ game: 'slots', bet })
        });

        let iterations = 0;
        const interval = setInterval(() => {
            setReels([
                Math.floor(Math.random() * symbols.length),
                Math.floor(Math.random() * symbols.length),
                Math.floor(Math.random() * symbols.length)
            ]);
            SoundEngine.play('click');
            iterations++;
            
            if (iterations > 20) {
                clearInterval(interval);
                const finalResult = [
                    Math.floor(Math.random() * symbols.length),
                    Math.floor(Math.random() * symbols.length),
                    Math.floor(Math.random() * symbols.length)
                ];
                setReels(finalResult);
                
                let mult = 0;
                if (finalResult[0] === finalResult[1] && finalResult[1] === finalResult[2]) {
                    mult = 10;
                    SoundEngine.play('win');
                } else if (finalResult[0] === finalResult[1] || finalResult[1] === finalResult[2] || finalResult[0] === finalResult[2]) {
                    mult = 2;
                    SoundEngine.play('win');
                } else {
                    SoundEngine.play('loss');
                }

                fetch('/api/result', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ game: 'slots', multiplier: mult })
                }).then(() => {
                    setSpinning(false);
                    onResult();
                });
            }
        }, 50);
    };

    return (
        <div className="max-w-xl mx-auto p-8 bg-slate-900 rounded-[2.5rem] border border-white/5 shadow-2xl text-center">
            <h2 className="text-3xl font-black mb-8 bg-gradient-to-r from-pink-400 to-rose-400 bg-clip-text text-transparent">SLOTS</h2>
            <div className="flex justify-center gap-4 mb-8">
                {reels.map((r, i) => (
                    <div key={i} className="w-24 h-32 bg-slate-950 border-2 border-white/5 rounded-2xl flex items-center justify-center text-5xl shadow-inner">
                        {symbols[r]}
                    </div>
                ))}
            </div>
            <div className="space-y-6">
                <div className="flex flex-col items-center gap-2">
                    <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Bet Amount</label>
                    <input type="number" value={bet} onChange={e => setBet(parseFloat(e.target.value))} className="w-32 bg-slate-950 border border-white/10 rounded-xl px-4 py-3 font-mono text-center text-white focus:outline-none focus:border-indigo-500" />
                </div>
                <button onClick={spin} disabled={spinning} className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 py-6 rounded-2xl font-black uppercase tracking-widest shadow-lg shadow-indigo-500/20 transition-all transform active:scale-95">
                    {spinning ? 'SPINNING...' : 'SPIN'}
                </button>
            </div>
        </div>
    );
};

// --- MAIN APP ---
const App = () => {
    const [game, setGame] = useState('lobby');
    const [balance, setBalance] = useState(0);

    const fetchUser = async () => {
        const res = await fetch('/api/user');
        const data = await res.json();
        setBalance(data.balance);
        if (document.getElementById('user-balance')) {
            document.getElementById('user-balance').textContent = '$' + data.balance.toFixed(2);
        }
    };

    useEffect(() => {
        fetchUser();
        const handleHash = () => {
            const h = window.location.hash.replace('#', '');
            if(h) setGame(h);
        };
        window.addEventListener('hashchange', handleHash);
        handleHash();
        return () => window.removeEventListener('hashchange', handleHash);
    }, []);

    if(game === 'crash') return <CrashGame balance={balance} onResult={fetchUser} />;
    if(game === 'plinko') return <PlinkoGame balance={balance} onResult={fetchUser} />;
    if(game === 'limbo') return <LimboGame balance={balance} onResult={fetchUser} />;
    if(game === 'mines') return <MinesGame balance={balance} onResult={fetchUser} />;
    if(game === 'keno') return <KenoGame balance={balance} onResult={fetchUser} />;
    if(game === 'slots') return <SlotsGame balance={balance} onResult={fetchUser} />;
    
    return (
        <div className="text-center p-12">
            <h2 className="text-4xl font-black mb-4">LOBBY</h2>
            <p className="text-slate-400">Please select a game from the main menu.</p>
        </div>
    );
};

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
