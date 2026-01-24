
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
    }
  }
};

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

const App = () => {
    const [game, setGame] = useState('lobby');
    const [balance, setBalance] = useState(0);

    const fetchUser = async () => {
        const res = await fetch('/api/user');
        const data = await res.json();
        setBalance(data.balance);
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

    if(game === 'keno') return <KenoGame balance={balance} onResult={fetchUser} />;
    if(game === 'slots') return <SlotsGame balance={balance} onResult={fetchUser} />;
    
    return (
        <div className="text-center p-12">
            <h2 className="text-4xl font-black mb-4">LOBBY</h2>
            <p className="text-slate-400">Please select a game from the main menu.</p>
        </div>
    );
};

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
            <h2 className="text-3xl font-black mb-8 gradient-text">SLOTS</h2>
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


const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
