
import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import ReactDOM from 'react-dom/client';

// ==========================================
// AUDIO ENGINE
// ==========================================

const SoundEngine = {
  ctx: null as AudioContext | null,
  enabled: true,
  init: () => {
    if (!SoundEngine.ctx) { SoundEngine.ctx = new (window.AudioContext || (window as any).webkitAudioContext)(); }
    if (SoundEngine.ctx.state === 'suspended') SoundEngine.ctx.resume();
  },
  play: (type: string) => {
    if (!SoundEngine.enabled) return;
    if (!SoundEngine.ctx) SoundEngine.init();
    const ctx = SoundEngine.ctx!;
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
        osc.frequency.setValueAtTime(1200, t); gain.gain.setValueAtTime(0.01, t); gain.gain.exponentialRampToValueAtTime(0.001, t+0.03);
        osc.start(t); osc.stop(t+0.03);
    } else if (type === 'spin') {
        osc.type='square'; osc.frequency.setValueAtTime(150, t); osc.frequency.linearRampToValueAtTime(100, t+0.1);
        gain.gain.setValueAtTime(0.02, t); gain.gain.linearRampToValueAtTime(0, t+0.1);
        osc.start(t); osc.stop(t+0.1);
    } else if (type === 'chat') {
        osc.type='sine'; osc.frequency.setValueAtTime(800, t); 
        gain.gain.setValueAtTime(0.01, t); gain.gain.exponentialRampToValueAtTime(0.001, t+0.1);
        osc.start(t); osc.stop(t+0.1);
    }
  }
};

// ==========================================
// UTILS & MATH
// ==========================================

const generateRandomHex = () => Array.from(crypto.getRandomValues(new Uint8Array(32))).map(b => b.toString(16).padStart(2, '0')).join('');
async function sha256(m: string) {
    const msgBuffer = new TextEncoder().encode(m);
    const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
    return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
}

// Provably Fair Generators
const getGameHash = (s:string, c:string, n:number) => sha256(`${s}:${c}:${n}`);
const getCrashPoint = (h:string) => {
   if (parseInt(h.slice(0, 13), 16) % 33 === 0) return 1.00;
   const hVal = parseInt(h.slice(0, 13), 16);
   return Math.max(1, Math.floor((100 * Math.pow(2, 52) - hVal) / (Math.pow(2, 52) - hVal)) / 100);
};
const getDiceRoll = (h:string) => (parseInt(h.slice(0, 8), 16) % 10001) / 100;
const getWheelIndex = (h:string, segments:number) => parseInt(h.slice(0, 8), 16) % segments;
const getMinesBoard = (h:string, mines:number) => {
    const deck = Array.from({length:25},(_,i)=>i);
    const board = new Array(25).fill(false);
    let seed = h;
    for(let i=0; i<mines; i++) {
        const val = parseInt(seed.slice(0,8), 16);
        const idx = val % deck.length;
        board[deck.splice(idx,1)[0]] = true;
        seed = seed.slice(1) + seed[0];
    }
    return board;
};
const getKenoDraw = (h:string) => {
    const nums = Array.from({length:40},(_,i)=>i+1);
    const draw = [];
    let seed = h;
    for(let i=0; i<10; i++) {
        const val = parseInt(seed.slice(0,8), 16);
        draw.push(nums.splice(val % nums.length, 1)[0]);
        seed = seed.slice(1) + seed[0];
    }
    return draw;
};
const getPlinkoPath = (h:string, rows:number) => {
    const path = [];
    for(let i=0; i<rows; i++) path.push(parseInt(h.slice(i, i+1), 16) % 2);
    return path;
}
const getSlotsResult = (h:string) => [parseInt(h.slice(0,5),16)%7, parseInt(h.slice(5,10),16)%7, parseInt(h.slice(10,15),16)%7];

const getAvatarColor = (name: string) => {
    const colors = ['bg-red-500', 'bg-orange-500', 'bg-amber-500', 'bg-green-500', 'bg-emerald-500', 'bg-teal-500', 'bg-cyan-500', 'bg-sky-500', 'bg-blue-500', 'bg-indigo-500', 'bg-violet-500', 'bg-purple-500', 'bg-fuchsia-500', 'bg-pink-500', 'bg-rose-500'];
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    return colors[Math.abs(hash) % colors.length];
};

// ==========================================
// UI COMPONENTS
// ==========================================

const Button = ({ onClick, children, variant = 'primary', className = '', disabled = false }: any) => {
  const base = "relative rounded-xl font-black uppercase tracking-widest transition-all active:scale-95 disabled:opacity-50 disabled:active:scale-100 flex items-center justify-center cursor-pointer select-none";
  const variants: any = {
    primary: "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/20 border-t border-white/10",
    success: "bg-emerald-500 hover:bg-emerald-400 text-slate-900 shadow-lg shadow-emerald-500/20",
    danger: "bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-500/20",
    secondary: "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-white/5",
  };
  return (
    <button onClick={() => { if(!disabled) { SoundEngine.play('click'); onClick(); } }} disabled={disabled} className={`${base} ${variants[variant]} ${className}`}>
      {children}
    </button>
  );
};

const BetInput = ({ value, onChange, balance, autoCashout, onAutoCashoutChange }: any) => (
    <div className="bg-[#0b1221] p-3 rounded-2xl border border-white/5 shadow-inner space-y-3">
        <div>
            <div className="flex justify-between px-2 mb-2"><span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Bet Amount</span><span className="text-[10px] font-bold text-slate-400">${balance.toFixed(2)}</span></div>
            <div className="relative group">
                <span className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500 font-mono text-sm group-focus-within:text-indigo-400 transition-colors">$</span>
                <input type="number" value={value} onChange={e => onChange(Math.max(0, parseFloat(e.target.value)||0))} className="w-full bg-slate-900 rounded-xl py-3 pl-8 pr-4 font-mono font-bold text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition-all border border-transparent focus:border-indigo-500/20" />
            </div>
            <div className="grid grid-cols-4 gap-2 mt-2">
                {[0.5, 2].map(m => <button key={m} onClick={()=>onChange(parseFloat((value*m).toFixed(2)))} className="bg-slate-800/50 rounded-lg py-1.5 text-[10px] font-bold text-slate-400 hover:text-white hover:bg-slate-800 transition-colors">{m}x</button>)}
                <button onClick={()=>onChange(balance)} className="bg-slate-800/50 rounded-lg py-1.5 text-[10px] font-bold text-slate-400 hover:text-white hover:bg-slate-800 transition-colors">MAX</button>
                <button onClick={()=>onChange(1.00)} className="bg-slate-800/50 rounded-lg py-1.5 text-[10px] font-bold text-slate-400 hover:text-white hover:bg-slate-800 transition-colors">MIN</button>
            </div>
        </div>
        
        {onAutoCashoutChange && (
            <div>
                 <div className="flex justify-between px-2 mb-2"><span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Auto Cashout</span></div>
                 <div className="relative group">
                    <span className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500 font-mono text-sm group-focus-within:text-indigo-400 transition-colors">x</span>
                    <input type="number" value={autoCashout} onChange={e => onAutoCashoutChange(e.target.value)} placeholder="At (e.g. 2.00)" className="w-full bg-slate-900 rounded-xl py-3 pl-8 pr-4 font-mono font-bold text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition-all border border-transparent focus:border-indigo-500/20 placeholder:text-slate-600" />
                </div>
            </div>
        )}
    </div>
);

const GameInfo = ({ title, children }: any) => {
    const [open, setOpen] = useState(false);
    return (
        <>
            <button onClick={()=>setOpen(true)} className="absolute top-4 right-4 z-20 w-8 h-8 rounded-full bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700 font-bold flex items-center justify-center transition-colors">?</button>
            {open && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in">
                    <div className="bg-[#0f172a] border border-white/10 rounded-2xl max-w-md w-full p-6 relative shadow-2xl">
                        <button onClick={()=>setOpen(false)} className="absolute top-4 right-4 text-slate-500 hover:text-white">✕</button>
                        <h3 className="text-xl font-black text-white mb-4 uppercase tracking-wide">{title} Rules</h3>
                        <div className="text-sm text-slate-400 space-y-2 leading-relaxed">
                            {children}
                        </div>
                        <div className="mt-6 pt-4 border-t border-white/5 text-xs text-slate-600 font-mono">
                            Provably Fair • SHA-256
                        </div>
                    </div>
                </div>
            )}
        </>
    )
}

// ==========================================
// GAME: MINES
// ==========================================

const MinesGame = ({ onResult, balance, seed }: any) => {
    const [bet, setBet] = useState(10);
    const [mineCount, setMineCount] = useState(3);
    const [gameState, setGameState] = useState<'IDLE'|'PLAYING'|'GAMEOVER'|'CASHOUT'>('IDLE');
    const [board, setBoard] = useState<boolean[]>([]);
    const [revealed, setRevealed] = useState<boolean[]>([]);
    const [gemsFound, setGemsFound] = useState(0);

    const multipliers = useMemo(() => {
        const m = []; 
        let current = 0.99; // 1% House Edge preserved
        for(let i=0; i<25-mineCount; i++) {
            current = current * (25-i) / (25-mineCount-i);
            m.push(current);
        }
        return m;
    }, [mineCount]);

    const startGame = async () => {
        if (balance < bet) return;
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        setBoard(getMinesBoard(hash, mineCount));
        setRevealed(new Array(25).fill(false));
        setGemsFound(0);
        setGameState('PLAYING');
        onResult({ wager: bet, payout: 0, active: true, hash }); 
    };

    const clickTile = (idx: number) => {
        if (gameState !== 'PLAYING' || revealed[idx]) return;
        const newRevealed = [...revealed];
        newRevealed[idx] = true;
        setRevealed(newRevealed);

        if (board[idx]) { // Bomb
            setGameState('GAMEOVER');
            SoundEngine.play('loss');
            onResult({ wager: 0, payout: 0, multiplier: 0, active: false });
        } else { // Gem
            const newGems = gemsFound + 1;
            setGemsFound(newGems);
            SoundEngine.play('win'); // Small win sound for gem
            if (newGems === 25 - mineCount) cashout(newGems);
        }
    };

    const cashout = (gems = gemsFound) => {
        if(gameState !== 'PLAYING') return;
        setGameState('CASHOUT');
        const mult = multipliers[gems-1];
        const win = bet * mult;
        SoundEngine.play('win');
        onResult({ wager: 0, payout: win, multiplier: mult, active: false });
    };

    return (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-full">
            <GameInfo title="Mines">
                <p>1. Set your bet and number of mines.</p>
                <p>2. Click tiles to reveal Gems 💎 and increase your multiplier.</p>
                <p>3. Avoid the Bombs 💣! If you hit one, you lose your bet.</p>
                <p>4. Cashout at any time to secure your winnings.</p>
            </GameInfo>
            <div className="lg:col-span-3 space-y-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <div className="bg-[#0b1221] p-4 rounded-2xl border border-white/5 shadow-inner">
                    <label className="text-[10px] font-bold text-slate-500 uppercase block mb-2">Mines Amount</label>
                    <div className="grid grid-cols-5 gap-2">
                        {[1,3,5,10,24].map(m => <button key={m} onClick={()=>setMineCount(m)} disabled={gameState==='PLAYING'} className={`py-2 rounded-lg font-bold text-xs transition-all ${mineCount===m ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-500/20' : 'bg-slate-900 text-slate-400 hover:bg-slate-800'}`}>{m}</button>)}
                    </div>
                </div>
                {gameState === 'PLAYING' ? 
                    <Button variant="success" className="w-full py-5 text-xl font-black" onClick={()=>cashout()}>CASHOUT ${(bet * (gemsFound > 0 ? multipliers[gemsFound-1] : 1)).toFixed(2)}</Button> :
                    <Button className="w-full py-5 text-xl font-black" onClick={startGame}>BET</Button>
                }
            </div>
            <div className="lg:col-span-9 bg-[#0b1221] rounded-3xl p-8 border border-white/5 flex items-center justify-center relative overflow-hidden shadow-2xl">
                <div className="grid grid-cols-5 gap-3 w-full max-w-[500px] aspect-square">
                    {Array.from({length:25}).map((_, i) => (
                        <button key={i} onClick={() => clickTile(i)} disabled={gameState !== 'PLAYING' || revealed[i]}
                            className={`rounded-xl transition-all duration-300 transform active:scale-95 flex items-center justify-center text-4xl relative overflow-hidden
                            ${revealed[i] ? (board[i] ? 'bg-rose-500/20 border-2 border-rose-500' : 'bg-emerald-500/20 border-2 border-emerald-500') 
                            : 'bg-slate-800 hover:bg-slate-700 border-2 border-slate-700 hover:border-slate-600'}`}>
                            <div className={`transition-all duration-300 transform ${revealed[i] ? 'scale-100 opacity-100' : 'scale-0 opacity-0'}`}>
                                {board[i] ? '💣' : '💎'}
                            </div>
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
};

// ==========================================
// GAME: KENO
// ==========================================

const KenoGame = ({ onResult, balance, seed }: any) => {
    const [bet, setBet] = useState(10);
    const [selected, setSelected] = useState<number[]>([]);
    const [drawn, setDrawn] = useState<number[]>([]);
    const [playing, setPlaying] = useState(false);
    
    // Classic Keno Payouts (simplified)
    const payouts: any = {
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

    const toggleNum = (n: number) => {
        if (playing) return;
        if (selected.includes(n)) setSelected(s => s.filter(x => x !== n));
        else if (selected.length < 10) setSelected(s => [...s, n]);
    };

    const play = async () => {
        if (balance < bet || selected.length === 0 || playing) return;
        setPlaying(true);
        setDrawn([]);
        onResult({ wager: bet, payout: 0, active: true });
        
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const result = getKenoDraw(hash);
        
        // Animate Draw
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
                onResult({ wager: 0, payout: bet * mult, multiplier: mult, active: false, hash });
            }
        }, 200);
    };

    return (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-full">
            <GameInfo title="Keno">
                <p>1. Select up to 10 numbers.</p>
                <p>2. We draw 10 numbers randomly.</p>
                <p>3. The more matches you get, the higher the payout!</p>
            </GameInfo>
            <div className="lg:col-span-3 space-y-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <div className="bg-[#0b1221] p-4 rounded-2xl border border-white/5 shadow-inner">
                   <div className="text-[10px] font-bold text-slate-500 uppercase mb-2">Payouts ({selected.length} Selected)</div>
                   <div className="space-y-1">
                       {payouts[Math.max(1, selected.length)].map((m:number, i:number) => (
                           <div key={i} className="flex justify-between text-xs px-2 py-1 rounded bg-slate-900/50">
                               <span className="text-slate-400">{i}x Hit</span>
                               <span className="font-mono font-bold text-white">{m}x</span>
                           </div>
                       ))}
                   </div>
                </div>
                <Button className="w-full py-5 text-xl font-black" onClick={play} disabled={playing || selected.length === 0}>BET</Button>
            </div>
            <div className="lg:col-span-9 bg-[#0b1221] rounded-3xl p-8 border border-white/5 flex items-center justify-center relative overflow-hidden shadow-2xl">
                 <div className="grid grid-cols-8 gap-2 w-full max-w-[600px]">
                     {Array.from({length:40}).map((_, i) => {
                         const n = i+1;
                         const isSel = selected.includes(n);
                         const isDrawn = drawn.includes(n);
                         const isHit = isSel && isDrawn;
                         return (
                             <button key={n} onClick={()=>toggleNum(n)} disabled={playing}
                                className={`aspect-square rounded-lg font-bold text-sm transition-all duration-300 relative
                                ${isHit ? 'bg-emerald-500 text-slate-900 shadow-[0_0_15px_rgba(16,185,129,0.5)] z-10 scale-110' :
                                  isDrawn ? 'bg-slate-700 text-white scale-90' :
                                  isSel ? 'bg-indigo-600 text-white' :
                                  'bg-slate-800 text-slate-500 hover:bg-slate-700'}`}>
                                 {n}
                             </button>
                         )
                     })}
                 </div>
            </div>
        </div>
    );
};

// ==========================================
// GAME: PLINKO
// ==========================================

const PlinkoGame = ({ onResult, balance, seed }: any) => {
    const [bet, setBet] = useState(10);
    const [rows, setRows] = useState(16);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [balls, setBalls] = useState<any[]>([]);
    
    // Config
    const pegs: any[] = useMemo(() => {
        const p = [];
        const startX = 400;
        const startY = 50;
        const gap = 40; 
        for(let r=0; r<rows; r++) {
            for(let c=0; c<=r; c++) {
                p.push({x: startX - (r*gap/2) + (c*gap), y: startY + r*35});
            }
        }
        return p;
    }, [rows]);

    const multipliers = useMemo(() => {
        const m = [];
        const count = rows + 1;
        for(let i=0; i<count; i++) {
            const dist = Math.abs(i - count/2 + 0.5);
            m.push(Math.pow(dist, 2.8) * 0.2 + 0.2); // Tweaked for "High Risk" feel
        }
        return m.map(x => Math.max(0.2, parseFloat(x.toFixed(1)))); 
    }, [rows]);

    const dropBall = async () => {
        if(balance < bet) return;
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const path = getPlinkoPath(hash, rows);
        onResult({ wager: bet, payout: 0, active: true, hash });
        setBalls(prev => [...prev, { id: Date.now(), x: 400, y: 20, vx: 0, vy: 0, path: path, row: 0, dead: false, bet: bet }]);
    };

    useEffect(() => {
        const ctx = canvasRef.current?.getContext('2d');
        if(!ctx) return;
        let req: number;
        const loop = () => {
            ctx.clearRect(0,0,800,600);
            
            // Draw Pegs
            ctx.fillStyle = 'rgba(255,255,255,0.1)';
            pegs.forEach(p => { ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, Math.PI*2); ctx.fill(); });

            // Draw Multipliers
            const gap = 40;
            const startX = 400 - (rows*gap/2);
            multipliers.forEach((m, i) => {
                const x = startX + i*gap;
                const y = 50 + rows*35 + 20;
                ctx.fillStyle = m >= 10 ? '#f43f5e' : m >= 2 ? '#10b981' : '#f59e0b';
                ctx.beginPath(); ctx.roundRect(x-15, y, 30, 20, 4); ctx.fill();
                ctx.fillStyle = '#0f172a'; ctx.font='bold 10px Inter'; ctx.textAlign='center'; ctx.fillText(`${m}x`, x, y+14);
            });

            setBalls(currentBalls => {
                const nextBalls = currentBalls.map(b => {
                    if(b.dead) return b;
                    const targetRowY = 50 + b.row * 35;
                    b.y += 8; // Faster physics
                    if (b.y >= targetRowY && b.row < rows) {
                        SoundEngine.play('tick');
                        const dir = b.path[b.row] === 0 ? -1 : 1;
                        b.x += dir * 20; 
                        b.row++;
                    }
                    if (b.row >= rows && b.y > 50 + rows*35) {
                        if (!b.dead) {
                           const bucketIdx = b.path.reduce((acc:number, v:number)=>acc+v, 0);
                           const mult = multipliers[bucketIdx] || 0.2;
                           SoundEngine.play(mult > 1 ? 'win' : 'loss');
                           onResult({ wager: 0, payout: b.bet * mult, multiplier: mult, active: false });
                           b.dead = true;
                        }
                    }
                    return b;
                }).filter(b => b.y < 700); 

                nextBalls.forEach(b => {
                   if(!b.dead) {
                       ctx.beginPath(); ctx.arc(b.x, b.y, 6, 0, Math.PI*2);
                       ctx.fillStyle = '#f43f5e'; ctx.shadowColor='#f43f5e'; ctx.shadowBlur=15; ctx.fill(); ctx.shadowBlur=0;
                   }
                });
                return nextBalls;
            });
            req = requestAnimationFrame(loop);
        };
        req = requestAnimationFrame(loop);
        return () => cancelAnimationFrame(req);
    }, [pegs, multipliers, rows]);

    return (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-full">
            <GameInfo title="Plinko">
                <p>1. Drop the ball from the top of the pyramid.</p>
                <p>2. The ball bounces off pegs, moving left or right.</p>
                <p>3. It lands in a multiplier slot at the bottom.</p>
                <p>4. Edges have higher payouts, center has lower.</p>
            </GameInfo>
            <div className="lg:col-span-3 space-y-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <div className="bg-[#0b1221] p-4 rounded-2xl border border-white/5 shadow-inner">
                    <label className="text-[10px] font-bold text-slate-500 uppercase block mb-2">Difficulty</label>
                    <div className="flex gap-2">
                        {[8,12,16].map(r => <button key={r} onClick={()=>setRows(r)} className={`flex-1 py-2 rounded-lg font-bold text-xs ${rows===r?'bg-indigo-600 text-white shadow-lg':'bg-slate-900 text-slate-400'}`}>{r} Rows</button>)}
                    </div>
                </div>
                <Button className="w-full py-5 text-xl font-black" onClick={dropBall}>DROP BALL</Button>
            </div>
            <div className="lg:col-span-9 bg-[#0b1221] rounded-3xl border border-white/5 relative overflow-hidden flex justify-center shadow-2xl">
                <canvas ref={canvasRef} width={800} height={650} className="w-full h-full object-contain mix-blend-screen" />
            </div>
        </div>
    );
};

// ==========================================
// GAME: DICE
// ==========================================

const DiceGame = ({ onResult, balance, seed }: any) => {
    const [bet, setBet] = useState(10);
    const [target, setTarget] = useState(50);
    const [roll, setRoll] = useState<number|null>(null);
    const multiplier = 99 / (100 - target); // 1% House Edge
    
    const play = async () => {
        if(balance<bet) return;
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const result = getDiceRoll(hash);
        setRoll(result);
        const win = result >= target;
        SoundEngine.play(win ? 'win' : 'loss');
        onResult({ wager: bet, payout: win ? bet * multiplier : 0, multiplier: win?multiplier:0, active: false, hash });
    };

    return (
        <div className="flex flex-col gap-8 max-w-4xl mx-auto py-12">
             <GameInfo title="Dice">
                <p>1. Set your target number using the slider.</p>
                <p>2. Higher target = Higher Multiplier = Lower Win Chance.</p>
                <p>3. If the dice rolls ABOVE your target, you win!</p>
            </GameInfo>
             <div className="bg-[#0b1221] rounded-3xl p-10 border border-white/5 shadow-2xl relative overflow-hidden">
                 <div className="h-32 flex items-center justify-between relative px-10">
                    <div className="absolute inset-x-10 top-1/2 h-6 bg-slate-900 rounded-full border border-white/5">
                        <div className="h-full bg-indigo-500 rounded-full transition-all shadow-[0_0_20px_rgba(99,102,241,0.5)]" style={{width: `${100-target}%`, marginLeft: `${target}%`}}></div>
                    </div>
                    <input type="range" min="2" max="98" value={target} onChange={(e)=>setTarget(Number(e.target.value))} 
                           className="absolute inset-x-10 top-1/2 -translate-y-1/2 w-full opacity-0 cursor-pointer z-10 h-10" />
                    {roll !== null && (
                         <div className="absolute top-1/2 -translate-y-1/2 w-20 h-20 -ml-10 bg-white rounded-2xl shadow-[0_0_50px_rgba(255,255,255,0.3)] flex items-center justify-center z-20 transition-all duration-500"
                              style={{left: `${roll+4}%`}}>
                             <span className={`text-2xl font-black ${roll>=target ? 'text-emerald-600' : 'text-rose-600'}`}>{roll.toFixed(2)}</span>
                         </div>
                    )}
                 </div>
                 <div className="grid grid-cols-3 gap-8 mt-10">
                     <div className="text-center bg-slate-900/50 p-4 rounded-2xl border border-white/5"><div className="text-slate-500 font-bold uppercase text-[10px] tracking-widest mb-1">Multiplier</div><div className="text-3xl font-black text-emerald-400">{multiplier.toFixed(4)}x</div></div>
                     <div className="text-center bg-slate-900/50 p-4 rounded-2xl border border-white/5"><div className="text-slate-500 font-bold uppercase text-[10px] tracking-widest mb-1">Roll Over</div><div className="text-3xl font-black text-white">{target}</div></div>
                     <div className="text-center bg-slate-900/50 p-4 rounded-2xl border border-white/5"><div className="text-slate-500 font-bold uppercase text-[10px] tracking-widest mb-1">Win Chance</div><div className="text-3xl font-black text-indigo-400">{(100-target).toFixed(0)}%</div></div>
                 </div>
             </div>
             <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
                 <BetInput value={bet} onChange={setBet} balance={balance} />
                 <Button className="w-full py-6 text-2xl font-black bg-emerald-500 hover:bg-emerald-400 text-slate-900 shadow-[0_0_30px_rgba(16,185,129,0.3)]" onClick={play}>ROLL DICE</Button>
             </div>
        </div>
    );
};

// ==========================================
// GAME: SLOTS
// ==========================================

const SlotsGame = ({ onResult, balance, seed }: any) => {
    const [bet, setBet] = useState(10);
    const [spinning, setSpinning] = useState(false);
    const [reels, setReels] = useState([0,0,0]);
    // Symbols: 0=Cherry(1x), 1=Lemon(2x), 2=Grape(5x), 3=Diamond(10x), 4=Seven(50x), 5=Bar(5x), 6=Bell(5x)
    const symbols = ['🍒','🍋','🍇','💎','7️⃣','🎰','🔔'];
    const payouts = [1.2, 2.5, 5, 10, 50, 5, 5]; 

    const spin = async () => {
        if(balance < bet || spinning) return;
        setSpinning(true);
        onResult({ wager: bet, payout: 0, active: true });
        
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const result = getSlotsResult(hash);
        
        let spinCount = 0;
        const interval = setInterval(() => {
            setReels(prev => prev.map(() => Math.floor(Math.random() * symbols.length)));
            SoundEngine.play('spin');
            spinCount++;
            if(spinCount > 15) {
                clearInterval(interval);
                setReels(result);
                setSpinning(false);
                
                // Win Logic: 3 match
                if(result[0] === result[1] && result[1] === result[2]) {
                    const mult = payouts[result[0]];
                    SoundEngine.play('win');
                    onResult({ wager: 0, payout: bet*mult, multiplier: mult, active: false, hash });
                } else {
                    SoundEngine.play('loss');
                    onResult({ wager: 0, payout: 0, multiplier: 0, active: false, hash });
                }
            }
        }, 100);
    };

    return (
        <div className="max-w-2xl mx-auto py-12 space-y-8">
            <GameInfo title="Slots">
                <p>1. Spin the reels.</p>
                <p>2. Match 3 symbols to win.</p>
                <p>3. 7️⃣ is the jackpot (50x)!</p>
            </GameInfo>
            <div className="bg-[#0b1221] border-4 border-yellow-500/20 rounded-[3rem] p-10 shadow-2xl relative">
                <div className="absolute -top-4 left-1/2 -translate-x-1/2 bg-yellow-500 text-slate-900 font-black px-8 py-2 rounded-full text-sm uppercase tracking-widest shadow-lg z-10">Jackpot 5000x</div>
                <div className="flex gap-4 justify-center bg-slate-950 p-6 rounded-3xl border-inset border-4 border-black/50 shadow-inner">
                    {reels.map((r,i) => (
                        <div key={i} className="w-32 h-40 bg-gradient-to-b from-slate-100 to-slate-300 rounded-xl flex items-center justify-center text-7xl shadow-inner border-y-8 border-slate-200 overflow-hidden relative">
                             <div className={`transition-all duration-100 ${spinning ? 'blur-sm scale-110' : ''}`}>
                                {symbols[r]}
                             </div>
                             <div className="absolute inset-0 bg-gradient-to-b from-black/20 via-transparent to-black/20 pointer-events-none"></div>
                        </div>
                    ))}
                </div>
                <div className="absolute -bottom-14 left-0 right-0 h-10 bg-black/50 blur-xl -z-10"></div>
            </div>
            <div className="grid grid-cols-2 gap-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <Button className="text-2xl font-black bg-yellow-500 hover:bg-yellow-400 text-black shadow-[0_0_30px_rgba(234,179,8,0.4)] border-none" onClick={spin} disabled={spinning}>
                    {spinning ? 'SPINNING...' : 'SPIN'}
                </Button>
            </div>
        </div>
    );
};

// ==========================================
// GAME: LIMBO
// ==========================================

const LimboGame = ({ onResult, balance, seed }: any) => {
    const [bet, setBet] = useState(10);
    const [targetMult, setTargetMult] = useState(2.0);
    const [result, setResult] = useState<number|null>(null);

    const play = async () => {
        if(balance<bet) return;
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const crash = getCrashPoint(hash);
        setResult(crash);
        const win = crash >= targetMult;
        SoundEngine.play(win ? 'win' : 'loss');
        onResult({ wager: bet, payout: win ? bet * targetMult : 0, multiplier: win?targetMult:0, active: false, hash });
    };

    return (
        <div className="max-w-xl mx-auto py-12 space-y-8">
            <GameInfo title="Limbo">
                <p>1. Predict the minimum multiplier.</p>
                <p>2. If the result is higher than your prediction, you win!</p>
                <p>3. Go safe (1.1x) or moon (1000x).</p>
            </GameInfo>
             <div className="text-center relative py-16 bg-[#0b1221] rounded-3xl border border-white/5 shadow-2xl">
                 <div className={`text-9xl font-black tracking-tighter transition-all duration-200 ${result !== null && result >= targetMult ? 'text-emerald-500 scale-110 drop-shadow-[0_0_30px_rgba(16,185,129,0.5)]' : result !== null ? 'text-rose-500' : 'text-slate-700'}`}>
                     {result?.toFixed(2) || '0.00'}x
                 </div>
                 <div className="text-slate-500 font-mono mt-4 font-bold">TARGET: {targetMult.toFixed(2)}x</div>
             </div>
             <div className="bg-[#0b1221] p-6 rounded-3xl border border-white/5 space-y-6">
                 <BetInput value={bet} onChange={setBet} balance={balance} />
                 <div>
                    <label className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Target Multiplier</label>
                    <input type="number" value={targetMult} onChange={e=>setTargetMult(parseFloat(e.target.value))} className="w-full bg-slate-900 rounded-xl py-3 px-4 text-white font-mono font-bold border border-white/5 focus:border-indigo-500/50 focus:outline-none" />
                 </div>
                 <Button className="w-full py-5 text-xl font-black" onClick={play}>BET</Button>
             </div>
        </div>
    );
};

// ==========================================
// GAME: WHEEL
// ==========================================

const WheelGame = ({ onResult, balance, seed }: any) => {
    const [bet, setBet] = useState(10);
    const [spinning, setSpinning] = useState(false);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const rotationRef = useRef(0);
    
    // Updated Segments with realistic House Edge (0.00x)
    const segments = useMemo(() => [
        { c: '#1f2937', m: 0.0 }, { c: '#374151', m: 0.5 }, { c: '#ef4444', m: 1.5 }, { c: '#1f2937', m: 0.0 }, 
        { c: '#22c55e', m: 2.0 }, { c: '#1f2937', m: 0.0 }, { c: '#374151', m: 0.5 }, { c: '#eab308', m: 10.0 },
        { c: '#1f2937', m: 0.0 }, { c: '#374151', m: 0.5 }, { c: '#ef4444', m: 1.5 }, { c: '#1f2937', m: 0.0 }, 
        { c: '#22c55e', m: 2.0 }, { c: '#1f2937', m: 0.0 }, { c: '#374151', m: 0.5 }, { c: '#ef4444', m: 1.5 }
    ], []);

    useEffect(() => {
        const ctx = canvasRef.current?.getContext('2d');
        if(!ctx) return;
        const render = (rot: number) => {
            const cx = 250; const cy = 250; const r = 240;
            ctx.clearRect(0,0,500,500);
            const segAngle = (Math.PI*2)/segments.length;
            
            ctx.save();
            ctx.translate(cx, cy);
            ctx.rotate(rot);
            
            segments.forEach((seg, i) => {
                ctx.beginPath();
                ctx.moveTo(0,0);
                ctx.arc(0, 0, r, i*segAngle, (i+1)*segAngle);
                ctx.fillStyle = seg.c;
                ctx.fill();
                ctx.stroke();
                
                ctx.save();
                ctx.rotate((i+0.5)*segAngle);
                ctx.fillStyle = 'white';
                ctx.font = 'bold 20px Inter';
                ctx.textAlign = 'right';
                ctx.fillText(`${seg.m}x`, r-20, 6);
                ctx.restore();
            });
            ctx.restore();
            // Center Cap
            ctx.beginPath(); ctx.arc(250,250,20,0,Math.PI*2); ctx.fillStyle='white'; ctx.fill();
            // Pointer
            ctx.beginPath(); ctx.moveTo(480, 250); ctx.lineTo(500, 230); ctx.lineTo(500, 270); ctx.fillStyle='white'; ctx.shadowBlur=15; ctx.shadowColor='white'; ctx.fill();
        };
        render(rotationRef.current);
    }, [segments]);

    const spin = async () => {
        if(balance<bet || spinning) return;
        setSpinning(true);
        onResult({ wager: bet, payout: 0, active: true });
        
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const resultIdx = getWheelIndex(hash, segments.length);
        const segAngle = (Math.PI*2)/segments.length;
        const targetRot = (Math.PI*2 * 5) - (resultIdx * segAngle) - (segAngle/2); 
        const startRot = rotationRef.current % (Math.PI*2);
        
        const startTime = Date.now();
        const duration = 3000;
        
        const animate = () => {
            const now = Date.now();
            const p = Math.min(1, (now-startTime)/duration);
            const ease = 1 - Math.pow(1 - p, 4); // Quartic ease out
            const currentRot = startRot + (5 * Math.PI * 2 + ((Math.PI*2) - (resultIdx * segAngle))) * ease;
            rotationRef.current = currentRot;
            
            const ctx = canvasRef.current?.getContext('2d');
            if(ctx) {
                // Re-render loop (simplified for brevity, relies on useEffect loop ideally but here manually for sync)
                const cx = 250; const cy = 250; const r = 240;
                ctx.clearRect(0,0,500,500);
                const segSize = (Math.PI*2)/segments.length;
                ctx.save(); ctx.translate(cx,cy); ctx.rotate(currentRot);
                segments.forEach((seg, i) => {
                    ctx.beginPath(); ctx.moveTo(0,0); ctx.arc(0,0,r,i*segSize,(i+1)*segSize);
                    ctx.fillStyle=seg.c; ctx.fill(); ctx.stroke();
                    ctx.save(); ctx.rotate((i+0.5)*segSize); ctx.fillStyle='white'; ctx.font='bold 24px Inter'; ctx.textAlign='right'; ctx.fillText(`${seg.m}x`, r-30, 8); ctx.restore();
                });
                ctx.restore();
                ctx.beginPath(); ctx.arc(250,250,20,0,Math.PI*2); ctx.fillStyle='white'; ctx.fill();
                ctx.beginPath(); ctx.moveTo(480, 250); ctx.lineTo(500, 230); ctx.lineTo(500, 270); ctx.fillStyle='white'; ctx.shadowBlur=15; ctx.shadowColor='white'; ctx.fill();
            }

            if(p<1) requestAnimationFrame(animate);
            else {
                setSpinning(false);
                const mult = segments[resultIdx].m;
                SoundEngine.play(mult>1 ? 'win' : 'loss');
                onResult({ wager: 0, payout: bet*mult, multiplier: mult, active: false, hash });
            }
        };
        requestAnimationFrame(animate);
    };

    return (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-full items-center">
            <GameInfo title="Wheel">
                <p>1. Place your bet.</p>
                <p>2. Spin the wheel.</p>
                <p>3. Watch out for the Grey (0.5x) and Black (0.0x) segments!</p>
                <p>4. Hit Gold for 10x!</p>
            </GameInfo>
            <div className="lg:col-span-4 space-y-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <Button className="w-full py-6 text-2xl font-black" onClick={spin} disabled={spinning}>SPIN WHEEL</Button>
            </div>
            <div className="lg:col-span-8 flex justify-center">
                <canvas ref={canvasRef} width={500} height={500} className="w-full max-w-[500px] drop-shadow-2xl" />
            </div>
        </div>
    );
};

// ==========================================
// MAIN APP & CRASH (Re-integrated)
// ==========================================

enum GameState { BETTING='BETTING', RUNNING='RUNNING', CRASHED='CRASHED' }

const CrashGame = ({ seed, onPlay, onResult, balance }: any) => {
  const [bet, setBet] = useState(10);
  const [autoCashout, setAutoCashout] = useState<string>('');
  const [gameState, setGameState] = useState<GameState>(GameState.BETTING);
  const [multiplier, setMultiplier] = useState(1.00);
  const [hasQueuedBet, setHasQueuedBet] = useState(false);
  const [isPlayingRound, setIsPlayingRound] = useState(false);
  const [cashedOutAt, setCashedOutAt] = useState<number | null>(null);
  const [timeUntilStart, setTimeUntilStart] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reqRef = useRef(0);
  const startT = useRef(0);
  const cpRef = useRef(1);
  const activeBetSettings = useRef<{autoCashout: number|null}>({autoCashout: null});

  useEffect(() => {
     const startBettingPhase = () => {
        setGameState(GameState.BETTING); setMultiplier(1.00);
        let count = 5000; const startTime = Date.now();
        const tick = () => {
           const left = Math.max(0, count - (Date.now() - startTime));
           setTimeUntilStart(left / 1000);
           if (left > 0) reqRef.current = requestAnimationFrame(tick); else startGamePhase();
        };
        reqRef.current = requestAnimationFrame(tick);
     };

     const startGamePhase = async () => {
        setGameState(GameState.RUNNING);
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const cp = getCrashPoint(hash);
        cpRef.current = cp;
        startT.current = Date.now();
        
        // Determine if user is playing
        let localUserPlaying = false;
        let localAutoCashout: number | null = null;
        setHasQueuedBet(prev => {
           if (prev) {
               localUserPlaying = true;
               localAutoCashout = activeBetSettings.current.autoCashout;
           }
           return false;
        });

        if (localUserPlaying) {
             setIsPlayingRound(true);
             setCashedOutAt(null);
             onPlay((u: any) => ({ ...u, balance: u.balance - bet }));
        } else {
             setIsPlayingRound(false);
        }

        SoundEngine.play('start');

        let hasAutoCashed = false;
        const loop = () => {
           const elapsed = Date.now() - startT.current;
           const m = Math.max(1, Math.floor(Math.exp(0.065 * elapsed/1000) * 100) / 100); 
           setMultiplier(m);
           
           // Auto Cashout Check
           if (localUserPlaying && localAutoCashout && m >= localAutoCashout && !hasAutoCashed) {
                hasAutoCashed = true; 
                SoundEngine.play('win');
                setCashedOutAt(localAutoCashout);
                onResult(localAutoCashout, 'cashed', bet, bet * localAutoCashout);
           }

           if (m >= cp) handleCrash(cp, hash); else reqRef.current = requestAnimationFrame(loop);
        };
        reqRef.current = requestAnimationFrame(loop);
     };

     const handleCrash = (finalCp: number, hash: string) => {
        setGameState(GameState.CRASHED); setMultiplier(finalCp); SoundEngine.play('loss');
        setIsPlayingRound(isPlaying => {
            if (isPlaying) onResult(finalCp, hash, bet, 0); 
            return false;
        });
        setTimeout(() => startBettingPhase(), 3000);
     };
     startBettingPhase();
     return () => cancelAnimationFrame(reqRef.current);
  }, []);

  const handleBet = () => { 
      if(gameState === GameState.BETTING && balance >= bet) { 
          // Lock settings
          activeBetSettings.current = { autoCashout: parseFloat(autoCashout) || null };
          setHasQueuedBet(true); 
          SoundEngine.play('click'); 
      } 
  };
  
  const cashout = (forcedM?: number) => {
      const m = forcedM || multiplier;
      if (gameState === GameState.RUNNING && isPlayingRound && !cashedOutAt) {
        setCashedOutAt(m); SoundEngine.play('win'); onResult(m, 'cashed', bet, bet * m);
      }
  };

  useEffect(() => {
      const ctx = canvasRef.current?.getContext('2d');
      if(!ctx) return;
      ctx.clearRect(0,0,800,500);
      
      if(gameState===GameState.BETTING) {
          ctx.fillStyle='#64748b'; ctx.font='700 24px Inter'; ctx.textAlign='center'; ctx.fillText('NEXT ROUND IN', 400, 230);
          ctx.fillStyle='#fff'; ctx.font='900 64px Inter'; ctx.fillText(`${timeUntilStart.toFixed(1)}s`, 400, 300);
          const barW = 300; const p = timeUntilStart/5;
          ctx.fillStyle='#1e293b'; ctx.fillRect(250, 340, barW, 6);
          ctx.fillStyle='#6366f1'; ctx.fillRect(250, 340, barW*p, 6);
          return;
      }

      const t = Math.log(multiplier) / 0.065;
      const maxT = Math.max(8, t); // Zoomed in
      const scaleX = 800 / maxT;
      const scaleY = 500 / Math.max(2, multiplier * 1.2);
      
      ctx.beginPath(); ctx.moveTo(0, 500);
      for(let i=0; i<=t; i+=0.1) ctx.lineTo(i*scaleX, 500 - (Math.exp(0.065*i)-1)*scaleY);
      ctx.lineTo(t*scaleX, 500 - (multiplier-1)*scaleY);
      ctx.strokeStyle = gameState===GameState.CRASHED ? '#f43f5e' : '#6366f1';
      ctx.lineWidth=6; ctx.lineCap='round'; ctx.stroke();
      
      // Gradient
      ctx.lineTo(t*scaleX, 500); ctx.lineTo(0,500);
      const grad = ctx.createLinearGradient(0,0,0,500);
      grad.addColorStop(0, gameState===GameState.CRASHED ? 'rgba(244,63,94,0.2)' : 'rgba(99,102,241,0.2)');
      grad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle=grad; ctx.fill();

      if(gameState===GameState.CRASHED) {
          ctx.fillStyle='#f43f5e'; ctx.font='900 64px Inter'; ctx.textAlign='center'; ctx.fillText(`CRASHED`, 400, 220);
          ctx.font='700 32px Inter'; ctx.fillText(`@ ${multiplier.toFixed(2)}x`, 400, 270);
      }
  }, [multiplier, gameState, timeUntilStart]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-full">
       <GameInfo title="Crash">
           <p>1. Place your bet before the round starts.</p>
           <p>2. The multiplier increases from 1.00x upwards.</p>
           <p>3. Cashout before the rocket crashes!</p>
           <p>4. Use Auto-Cashout to exit automatically at a specific target.</p>
       </GameInfo>
       <div className="lg:col-span-3 space-y-4">
          <BetInput value={bet} onChange={setBet} balance={balance} autoCashout={autoCashout} onAutoCashoutChange={setAutoCashout} />
          {gameState === GameState.BETTING ? 
             (hasQueuedBet ? <Button variant="danger" onClick={()=>setHasQueuedBet(false)} className="w-full py-5 text-lg">CANCEL BET</Button> : <Button onClick={handleBet} className="w-full py-5 text-lg font-black">PLACE BET</Button>) :
             (isPlayingRound && !cashedOutAt ? <Button variant="success" onClick={()=>cashout()} className="w-full py-5 text-lg font-black animate-pulse shadow-[0_0_30px_rgba(16,185,129,0.5)]">CASHOUT ${(bet*multiplier).toFixed(2)}</Button> : <Button disabled className="w-full py-5 opacity-50 text-lg">WAITING</Button>)
          }
       </div>
       <div className="lg:col-span-9 bg-[#0b1221] rounded-3xl border border-white/5 relative overflow-hidden shadow-2xl">
          <div className="absolute top-8 left-8 z-10">
              <div className={`text-7xl font-black tracking-tighter ${gameState===GameState.CRASHED ? 'text-rose-500' : 'text-white'}`}>{multiplier.toFixed(2)}x</div>
              <div className="text-slate-500 font-bold uppercase tracking-widest text-sm mt-1">Current Payout</div>
          </div>
          <canvas ref={canvasRef} width={800} height={500} className="w-full h-full object-cover" />
       </div>
    </div>
  );
};

// ==========================================
// CHAT SIDEBAR
// ==========================================

const ChatSidebar = () => {
    const [msgs, setMsgs] = useState([
        { id: 1, user: 'System', level: 99, text: 'Welcome to NovaBet! Provably Fair & Live.', type: 'info', time: new Date().toLocaleTimeString() }
    ]);
    const [input, setInput] = useState('');
    const scrollRef = useRef<HTMLDivElement>(null);

    const bots = ['Whale_0x', 'SatoshiNaka', 'MoonBoi', 'RektCity', 'HODLer', 'LuckyStrike', 'DogeFather', 'Sniper'];
    const phrases = [
        "LFG!!! 🚀", "Rigged or bad luck?", "Just sniped 100x on Limbo", "Anyone seen rain?", 
        "Rip my balance", "Another green train incoming?", "Cashed out early... regret", "Big win on Plinko!"
    ];

    useEffect(() => {
        const interval = setInterval(() => {
            if(Math.random() > 0.7) {
                const isWin = Math.random() > 0.9;
                const user = bots[Math.floor(Math.random() * bots.length)];
                const text = isWin ? `Just won $${Math.floor(Math.random()*2000)} on Crash!` : phrases[Math.floor(Math.random() * phrases.length)];
                const type = isWin ? 'win' : 'chat';
                const level = Math.floor(Math.random() * 50) + 1;
                addMsg(user, text, type, level);
                SoundEngine.play('chat');
            }
        }, 3000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => { scrollRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs]);

    const addMsg = (user:string, text:string, type='chat', level=1) => {
        setMsgs(p => [...p.slice(-50), { id: Date.now(), user, text, type, level, time: new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) }]);
    };

    const send = (e:any) => {
        e.preventDefault();
        if(!input.trim()) return;
        addMsg('You', input, 'chat', 5); // Simulating User Level 5
        setInput('');
    };

    return (
        <div className="w-80 bg-[#0f172a] border-l border-white/5 flex flex-col shrink-0 z-20 shadow-2xl">
            <div className="p-4 border-b border-white/5 flex items-center justify-between bg-[#0b1221]">
                <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
                    <span className="font-bold text-xs uppercase tracking-widest text-slate-400">Global Chat</span>
                </div>
                <div className="text-[10px] text-slate-500 font-mono">1,420 Online</div>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
                {msgs.map((m, i) => (
                    <div key={i} className={`text-xs animate-in slide-in-from-right-2 fade-in duration-300 flex gap-3 ${m.type === 'win' ? 'bg-emerald-500/10 border border-emerald-500/20 p-2 rounded-lg' : ''}`}>
                         <div className={`w-8 h-8 rounded-full shrink-0 flex items-center justify-center font-bold text-[10px] text-white ${getAvatarColor(m.user)}`}>
                             {m.user.substring(0,2).toUpperCase()}
                         </div>
                         <div className="flex-1 min-w-0">
                             <div className="flex justify-between text-[10px] text-slate-600 mb-0.5 items-center">
                                 <div className="flex items-center gap-1.5">
                                     {m.level > 0 && <span className="bg-slate-700 text-[9px] px-1 rounded text-slate-300 font-mono">{m.level}</span>}
                                     <span className={`font-bold truncate ${m.type==='win'?'text-emerald-500':'text-slate-400'}`}>{m.user}</span>
                                 </div>
                                 <span className="shrink-0">{m.time}</span>
                             </div>
                             <div className={`break-words ${m.type==='win'?'text-emerald-300 font-bold':'text-slate-300'}`}>{m.text}</div>
                         </div>
                    </div>
                ))}
                <div ref={scrollRef} />
            </div>
            <form onSubmit={send} className="p-3 bg-[#0b1221] border-t border-white/5">
                <input value={input} onChange={e=>setInput(e.target.value)} placeholder="Type a message..." className="w-full bg-slate-900 border border-white/10 rounded-xl px-4 py-3 text-xs text-white focus:outline-none focus:border-indigo-500/50 transition-colors" />
            </form>
        </div>
    );
};

// ==========================================
// MAIN APP COMPONENT
// ==========================================

const App = () => {
  const [activeGame, setActiveGame] = useState('lobby');
  const [user, setUser] = useState({ 
      balance: 2500.00, 
      username: 'Player1', 
      level: 1,
      xp: 0,
      wagered: 0,
      wins: 0,
      losses: 0
  });
  const [seed] = useState({ serverSeed: generateRandomHex(), clientSeed: generateRandomHex().slice(0,16) });
  const [recentWins, setRecentWins] = useState<any[]>([]);
  const [myBets, setMyBets] = useState<any[]>([]);

  const handleResult = ({ wager, payout, active, hash }: any) => {
      setUser(u => {
          const newWagered = u.wagered + (active ? wager : 0);
          const newXp = u.xp + (active ? wager : 0);
          const newLevel = Math.floor(newXp / 1000) + 1;
          const newBalance = u.balance + payout - (active ? wager : 0);
          return {
              ...u,
              balance: newBalance,
              wagered: newWagered,
              xp: newXp,
              level: newLevel,
              wins: payout > 0 ? u.wins + 1 : u.wins,
              losses: payout === 0 && active ? u.losses + 1 : u.losses
          };
      });
      
      // Update History
      if(!active) {
          const multiplier = wager > 0 ? payout / wager : 0;
          setMyBets(prev => [{
              id: Date.now(),
              game: activeGame,
              wager,
              payout,
              multiplier,
              hash: hash || '---',
              time: new Date().toLocaleTimeString()
          }, ...prev].slice(0, 10));
      }

      if(payout > 0) {
          SoundEngine.play('win');
          setRecentWins(p => [{game:activeGame, user:'You', amount:payout}, ...p].slice(0,5));
      }
  };

  const games: any = {
      'crash': <CrashGame seed={seed} onPlay={setUser} onResult={(m:number, h:string, w:number, p:number) => handleResult({wager:w, payout:p, active:false, hash:h})} balance={user.balance} />,
      'plinko': <PlinkoGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'mines': <MinesGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'dice': <DiceGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'limbo': <LimboGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'wheel': <WheelGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'keno': <KenoGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'slots': <SlotsGame seed={seed} onResult={handleResult} balance={user.balance} />,
  };

  return (
    <div className="flex h-screen bg-[#020617] text-slate-200 font-sans selection:bg-indigo-500/30 overflow-hidden">
      {/* Sidebar Nav */}
      <aside className="w-20 lg:w-64 bg-[#0f172a] border-r border-white/5 flex flex-col shrink-0 z-30">
        <div className="p-6 flex items-center gap-3 cursor-pointer" onClick={()=>setActiveGame('lobby')}>
          <div className="w-10 h-10 bg-gradient-to-br from-indigo-600 to-indigo-800 rounded-xl flex items-center justify-center shadow-lg"><span className="text-xl font-black text-white">N</span></div>
          <span className="hidden lg:block font-black text-xl tracking-tight text-white">NOVA<span className="text-indigo-500">BET</span></span>
        </div>
        
        <div className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
            <button onClick={()=>setActiveGame('lobby')} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl font-bold transition-all ${activeGame==='lobby'?'bg-indigo-600 text-white shadow-lg':'text-slate-400 hover:bg-white/5'}`}>
                <span>🏠</span><span className="hidden lg:block">Lobby</span>
            </button>
            <div className="text-[10px] font-black text-slate-600 uppercase tracking-widest px-4 pt-4 pb-2 hidden lg:block">Games</div>
            {Object.keys(games).map(g => (
                <button key={g} onClick={()=>setActiveGame(g)} className={`w-full flex items-center gap-4 px-4 py-3 rounded-xl font-bold capitalize transition-all ${activeGame===g?'bg-[#1e293b] text-white border border-white/5':'text-slate-400 hover:bg-white/5'}`}>
                   <span>🎲</span><span className="hidden lg:block">{g}</span>
                </button>
            ))}
        </div>

        {/* User Stats/XP Area */}
        <div className="px-4 pb-2 hidden lg:block">
            <div className="flex justify-between text-[10px] font-bold text-slate-500 uppercase mb-1">
                <span>Level {user.level}</span>
                <span>{(user.xp % 1000) / 10}%</span>
            </div>
            <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                <div className="h-full bg-indigo-500 transition-all duration-500" style={{ width: `${(user.xp % 1000) / 10}%` }}></div>
            </div>
        </div>

        <div className="p-4 bg-[#0b1221] border-t border-white/5">
             <div className="flex justify-between items-center mb-1">
                 <span className="text-[10px] font-bold text-slate-500 uppercase">Wallet</span>
                 <span className="text-[10px] font-bold text-emerald-500">VERIFIED</span>
             </div>
             <div className="text-xl font-mono font-bold text-white mb-2">${user.balance.toFixed(2)}</div>
             <button className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-black uppercase rounded-lg shadow-lg shadow-emerald-500/20" onClick={()=>setUser(u=>({...u, balance: u.balance+1000}))}>Deposit</button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 bg-[#020617] relative z-10">
         {/* Top Bar ticker */}
         <div className="h-12 border-b border-white/5 bg-[#0f172a] flex items-center px-4 gap-8 overflow-hidden">
             <div className="text-[10px] font-black text-indigo-500 uppercase tracking-widest whitespace-nowrap">Recent Wins</div>
             <div className="flex gap-8 animate-marquee whitespace-nowrap">
                 {recentWins.concat([{game:'Crash', user:'Whale_0x', amount: 5400}, {game:'Plinko', user:'MoonBoi', amount: 120}]).map((w,i) => (
                     <div key={i} className="text-xs font-mono text-slate-400 flex items-center gap-2">
                         <span className="text-slate-500">{w.user}</span>
                         <span className="text-emerald-400 font-bold">${w.amount.toFixed(2)}</span>
                         <span className="text-slate-600 text-[10px] uppercase">in {w.game}</span>
                     </div>
                 ))}
             </div>
         </div>

         <div className="flex-1 p-6 lg:p-8 overflow-y-auto">
            {activeGame === 'lobby' ? (
                 <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 max-w-7xl mx-auto">
                     <div className="relative rounded-3xl overflow-hidden bg-gradient-to-r from-indigo-900 to-purple-900 p-8 mb-10 shadow-2xl">
                         <div className="relative z-10">
                             <h1 className="text-4xl font-black text-white italic tracking-tighter mb-2">WELCOME BONUS</h1>
                             <p className="text-indigo-200 mb-6 max-w-lg">Join the highest paying crypto casino. Provably Fair. Instant Withdrawals.</p>
                             <button onClick={()=>setActiveGame('crash')} className="px-8 py-3 bg-white text-indigo-900 font-black rounded-xl hover:scale-105 transition-transform">PLAY NOW</button>
                         </div>
                         <div className="absolute right-0 bottom-0 top-0 w-1/2 bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-20 mix-blend-overlay"></div>
                         <div className="absolute -right-20 -bottom-20 w-96 h-96 bg-indigo-500 rounded-full blur-[100px] opacity-50"></div>
                     </div>

                     <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-6">
                         {Object.keys(games).map(g => (
                             <button key={g} onClick={()=>setActiveGame(g)} className="group relative bg-[#0f172a] border border-white/5 rounded-3xl p-8 aspect-[4/3] flex flex-col items-center justify-center gap-4 hover:-translate-y-2 transition-all hover:shadow-[0_20px_40px_-15px_rgba(79,70,229,0.3)] overflow-hidden">
                                 <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/10 to-purple-500/0 opacity-0 group-hover:opacity-100 transition-opacity"></div>
                                 <span className="text-5xl group-hover:scale-110 transition-transform duration-300 drop-shadow-2xl">
                                     {g==='crash'?'🚀':g==='plinko'?'🎱':g==='mines'?'💣':g==='dice'?'🎲':g==='limbo'?'🎯':g==='wheel'?'🎡':g==='keno'?'🔢':'🎰'}
                                 </span>
                                 <span className="text-xl font-black uppercase tracking-widest text-slate-300 group-hover:text-white z-10">{g}</span>
                             </button>
                         ))}
                     </div>
                 </div>
             ) : (
                <div className="max-w-[1600px] mx-auto h-full flex flex-col animate-in zoom-in-95 duration-300">
                    <div className="flex items-center gap-4 mb-6">
                        <h2 className="text-2xl font-black text-white uppercase tracking-tighter flex items-center gap-3">
                            <span className="text-indigo-500 text-3xl">
                                {activeGame==='crash'?'🚀':activeGame==='plinko'?'🎱':activeGame==='mines'?'💣':activeGame==='dice'?'🎲':activeGame==='limbo'?'🎯':activeGame==='wheel'?'🎡':activeGame==='keno'?'🔢':'🎰'}
                            </span> 
                            {activeGame}
                        </h2>
                        <span className="px-3 py-1 rounded-full bg-slate-800 text-[10px] font-bold text-slate-500 border border-white/5">PROVABLY FAIR</span>
                    </div>
                    <div className="bg-[#0f172a]/50 rounded-3xl border border-white/5 p-1 mb-8">
                        {games[activeGame]}
                    </div>

                    {/* My Bets History Table */}
                    <div className="bg-[#0f172a] rounded-2xl border border-white/5 overflow-hidden">
                        <div className="px-6 py-4 border-b border-white/5 font-bold uppercase text-xs text-slate-400">My Bets</div>
                        <table className="w-full text-left text-sm text-slate-400">
                            <thead className="bg-[#0b1221] text-xs uppercase font-bold text-slate-500">
                                <tr>
                                    <th className="px-6 py-3">Game</th>
                                    <th className="px-6 py-3">Bet</th>
                                    <th className="px-6 py-3">Mult</th>
                                    <th className="px-6 py-3">Payout</th>
                                </tr>
                            </thead>
                            <tbody>
                                {myBets.length === 0 ? (
                                    <tr><td colSpan={4} className="px-6 py-8 text-center text-slate-600 italic">No bets placed yet.</td></tr>
                                ) : (
                                    myBets.map(b => (
                                        <tr key={b.id} className="border-b border-white/5 hover:bg-white/5 transition-colors">
                                            <td className="px-6 py-4 font-bold capitalize text-white">{b.game}</td>
                                            <td className="px-6 py-4">${b.wager.toFixed(2)}</td>
                                            <td className={`px-6 py-4 font-bold ${b.multiplier>=1 ? 'text-emerald-500' : 'text-rose-500'}`}>
                                                {b.multiplier.toFixed(2)}x
                                            </td>
                                            <td className={`px-6 py-4 font-bold ${b.payout>0 ? 'text-emerald-500' : 'text-slate-500'}`}>
                                                ${b.payout.toFixed(2)}
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
             )}
         </div>
      </main>

      {/* Chat Sidebar */}
      <ChatSidebar />
    </div>
  );
};

const root = ReactDOM.createRoot(document.getElementById('root')!);
root.render(<React.StrictMode><App /></React.StrictMode>);
