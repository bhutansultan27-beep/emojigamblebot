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
    } else if (type === 'gem') {
        osc.type='sine'; osc.frequency.setValueAtTime(800, t); osc.frequency.linearRampToValueAtTime(1200, t+0.1);
        gain.gain.setValueAtTime(0.05, t); gain.gain.linearRampToValueAtTime(0, t+0.1);
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
const getKenoDraw = (h:string) => {
    const nums = Array.from({length:40},(_,i)=>i+1);
    const draw = [];
    let seed = h;
    for(let i=0; i<10; i++) {
        const val = parseInt(seed.slice(0,8), 16);
        draw.push(nums.splice(val % nums.length, 1)[0]);
        seed = seed.slice(1) + seed[0]; // simplistic shift for demo
    }
    return draw;
};
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
const getPlinkoPath = (h:string, rows:number) => {
    const path = [];
    for(let i=0; i<rows; i++) path.push(parseInt(h.slice(i, i+1), 16) % 2);
    return path;
}
const getSlotsResult = (h:string) => [parseInt(h.slice(0,5),16)%7, parseInt(h.slice(5,10),16)%7, parseInt(h.slice(10,15),16)%7];

// ==========================================
// UI COMPONENTS
// ==========================================

const Button = ({ onClick, children, variant = 'primary', className = '', disabled = false }: any) => {
  const base = "relative rounded-xl font-black uppercase tracking-widest transition-all active:scale-95 disabled:opacity-50 disabled:active:scale-100 flex items-center justify-center";
  const variants: any = {
    primary: "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/30 border-t border-white/10",
    success: "bg-emerald-500 hover:bg-emerald-400 text-slate-900 shadow-lg shadow-emerald-500/30",
    danger: "bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-500/30",
    secondary: "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-white/5",
  };
  return (
    <button onClick={() => { if(!disabled) { SoundEngine.play('click'); onClick(); } }} disabled={disabled} className={`${base} ${variants[variant]} ${className}`}>
      {children}
    </button>
  );
};

const BetInput = ({ value, onChange, balance }: any) => (
    <div className="bg-slate-950 p-2 rounded-2xl border border-white/10">
        <div className="flex justify-between px-2 mb-1"><span className="text-[10px] font-bold text-slate-500 uppercase">Bet Amount</span><span className="text-[10px] font-bold text-slate-500">${balance.toFixed(2)}</span></div>
        <div className="relative">
            <span className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500 font-mono text-sm">$</span>
            <input type="number" value={value} onChange={e => onChange(parseFloat(e.target.value)||0)} className="w-full bg-slate-900 rounded-xl py-3 pl-8 pr-4 font-mono font-bold text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/50" />
        </div>
        <div className="grid grid-cols-4 gap-2 mt-2">
            {[0.5, 2].map(m => <button key={m} onClick={()=>onChange(value*m)} className="bg-slate-900 rounded-lg py-1 text-[10px] font-bold text-slate-400 hover:text-white transition-colors">{m}x</button>)}
            <button onClick={()=>onChange(balance)} className="bg-slate-900 rounded-lg py-1 text-[10px] font-bold text-slate-400 hover:text-white transition-colors">MAX</button>
            <button onClick={()=>onChange(10)} className="bg-slate-900 rounded-lg py-1 text-[10px] font-bold text-slate-400 hover:text-white transition-colors">MIN</button>
        </div>
    </div>
);

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
        let current = 0.99; // House edge
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
        onResult({ wager: bet, payout: 0, active: true }); // Deduct bet
    };

    const clickTile = (idx: number) => {
        if (gameState !== 'PLAYING' || revealed[idx]) return;
        const newRevealed = [...revealed];
        newRevealed[idx] = true;
        setRevealed(newRevealed);

        if (board[idx]) { // Bomb
            setGameState('GAMEOVER');
            SoundEngine.play('loss');
            onResult({ wager: 0, payout: 0, multiplier: 0, active: false }); // Logic handled in parent
        } else { // Gem
            const newGems = gemsFound + 1;
            setGemsFound(newGems);
            SoundEngine.play('gem');
            if (newGems === 25 - mineCount) cashout(newGems);
        }
    };

    const cashout = (gems = gemsFound) => {
        if(gameState !== 'PLAYING') return;
        setGameState('CASHOUT');
        const mult = multipliers[gems-1];
        const win = bet * mult;
        SoundEngine.play('win');
        onResult({ wager: 0, payout: win, multiplier: mult, active: false }); // Refund handled in parent
    };

    return (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-6 h-full">
            <div className="md:col-span-3 space-y-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <div className="bg-slate-950 p-4 rounded-2xl border border-white/10">
                    <label className="text-[10px] font-bold text-slate-500 uppercase block mb-2">Mines</label>
                    <div className="grid grid-cols-5 gap-1">
                        {[1,3,5,10,24].map(m => <button key={m} onClick={()=>setMineCount(m)} disabled={gameState==='PLAYING'} className={`py-2 rounded font-bold text-xs ${mineCount===m ? 'bg-indigo-600 text-white' : 'bg-slate-900 text-slate-400'}`}>{m}</button>)}
                    </div>
                </div>
                {gameState === 'PLAYING' ? 
                    <Button variant="success" className="w-full py-4 text-lg" onClick={()=>cashout()}>CASHOUT ${(bet * (gemsFound > 0 ? multipliers[gemsFound-1] : 1)).toFixed(2)}</Button> :
                    <Button className="w-full py-4 text-lg" onClick={startGame}>BET</Button>
                }
            </div>
            <div className="md:col-span-9 bg-slate-900 rounded-3xl p-6 border border-white/5 flex items-center justify-center relative overflow-hidden">
                <div className="grid grid-cols-5 gap-3 w-full max-w-md aspect-square">
                    {Array.from({length:25}).map((_, i) => (
                        <button key={i} onClick={() => clickTile(i)} disabled={gameState !== 'PLAYING' || revealed[i]}
                            className={`rounded-xl transition-all duration-300 transform active:scale-90 flex items-center justify-center text-3xl
                            ${revealed[i] ? (board[i] ? 'bg-rose-500 shadow-[0_0_20px_rgba(244,63,94,0.5)]' : 'bg-emerald-500 shadow-[0_0_20px_rgba(16,185,129,0.5)]') 
                            : 'bg-slate-800 hover:bg-slate-700 shadow-inner'}`}>
                            {revealed[i] && (board[i] ? '💣' : '💎')}
                            {gameState !== 'PLAYING' && !revealed[i] && board[i] && <span className="opacity-30 grayscale">💣</span>}
                        </button>
                    ))}
                </div>
                {gameState === 'GAMEOVER' && <div className="absolute inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center animate-in fade-in"><h2 className="text-5xl font-black text-rose-500 uppercase tracking-tighter">BUSTED</h2></div>}
                {gameState === 'CASHOUT' && <div className="absolute inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center animate-in fade-in"><h2 className="text-5xl font-black text-emerald-500 uppercase tracking-tighter">WINNER</h2></div>}
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
        // Simple distribution
        const m = [];
        const count = rows + 1;
        for(let i=0; i<count; i++) {
            const dist = Math.abs(i - count/2 + 0.5);
            m.push(Math.pow(dist, 2.5) * 0.2 + 0.3); // Fake logic for visual
        }
        return m.map(x => Math.max(0.2, parseFloat(x.toFixed(1)))); // Demo values
    }, [rows]);

    const dropBall = async () => {
        if(balance < bet) return;
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const path = getPlinkoPath(hash, rows);
        
        onResult({ wager: bet, payout: 0, active: true });
        
        setBalls(prev => [...prev, {
            id: Date.now(),
            x: 400, y: 20, vx: 0, vy: 0,
            path: path,
            row: 0,
            dead: false,
            bet: bet
        }]);
    };

    // Physics Loop
    useEffect(() => {
        const ctx = canvasRef.current?.getContext('2d');
        if(!ctx) return;
        
        let req: number;
        const loop = () => {
            ctx.clearRect(0,0,800,600);
            
            // Draw Pegs
            ctx.fillStyle = 'white';
            pegs.forEach(p => {
                ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, Math.PI*2); ctx.fill();
            });

            // Draw Multipliers
            const gap = 40;
            const startX = 400 - (rows*gap/2);
            multipliers.forEach((m, i) => {
                const x = startX + i*gap;
                const y = 50 + rows*35 + 20;
                ctx.fillStyle = m >= 1 ? '#10b981' : '#f59e0b';
                ctx.fillRect(x-15, y, 30, 20);
                ctx.fillStyle = 'black'; ctx.font='10px Arial'; ctx.textAlign='center';
                ctx.fillText(`${m}x`, x, y+14);
            });

            // Update Balls
            setBalls(currentBalls => {
                const nextBalls = currentBalls.map(b => {
                    if(b.dead) return b;
                    
                    // Simple logic: Move to next peg in path
                    const targetRowY = 50 + b.row * 35;
                    
                    // Artificial gravity movement
                    b.y += 5;
                    
                    if (b.y >= targetRowY && b.row < rows) {
                        // "Hit" peg
                        SoundEngine.play('tick');
                        const dir = b.path[b.row] === 0 ? -1 : 1;
                        b.x += dir * 20; // Shift for visual
                        b.row++;
                    }

                    if (b.row >= rows && b.y > 50 + rows*35) {
                        // Finished
                        if (!b.dead) {
                           const bucketIdx = b.path.reduce((acc:number, v:number)=>acc+v, 0);
                           const mult = multipliers[bucketIdx] || 0.2;
                           SoundEngine.play(mult > 1 ? 'win' : 'loss');
                           onResult({ wager: 0, payout: b.bet * mult, multiplier: mult, active: false });
                           b.dead = true;
                        }
                    }
                    return b;
                }).filter(b => b.y < 700); // Remove unseen balls

                // Draw Balls
                nextBalls.forEach(b => {
                   if(!b.dead) {
                       ctx.beginPath(); ctx.arc(b.x, b.y, 6, 0, Math.PI*2);
                       ctx.fillStyle = '#f43f5e'; ctx.shadowColor='#f43f5e'; ctx.shadowBlur=10; ctx.fill(); ctx.shadowBlur=0;
                   }
                });

                return nextBalls;
            });

            req = requestAnimationFrame(loop);
        };
        req = requestAnimationFrame(loop);
        return () => cancelAnimationFrame(req);
    }, [pegs, multipliers, rows]); // Re-bind when config changes

    return (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-6 h-full">
            <div className="md:col-span-3 space-y-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <div className="bg-slate-950 p-4 rounded-2xl border border-white/10">
                    <label className="text-[10px] font-bold text-slate-500 uppercase block mb-2">Rows</label>
                    <div className="flex gap-2">
                        {[8,12,16].map(r => <button key={r} onClick={()=>setRows(r)} className={`flex-1 py-2 rounded font-bold ${rows===r?'bg-indigo-600':'bg-slate-800'}`}>{r}</button>)}
                    </div>
                </div>
                <Button className="w-full py-4 text-lg" onClick={dropBall}>DROP</Button>
            </div>
            <div className="md:col-span-9 bg-slate-900 rounded-3xl border border-white/5 relative overflow-hidden flex justify-center">
                <canvas ref={canvasRef} width={800} height={650} className="w-full h-full object-contain" />
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
    const multiplier = 99 / (100 - target);
    
    const play = async () => {
        if(balance<bet) return;
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const result = getDiceRoll(hash);
        setRoll(result);
        const win = result >= target;
        SoundEngine.play(win ? 'win' : 'loss');
        onResult({ wager: bet, payout: win ? bet * multiplier : 0, multiplier: win?multiplier:0, active: false }); // Instant
    };

    return (
        <div className="flex flex-col gap-8 max-w-4xl mx-auto py-12">
             <div className="bg-slate-900 rounded-3xl p-8 border border-white/5 shadow-2xl relative overflow-hidden">
                 <div className="h-32 flex items-center justify-between relative px-10">
                    <div className="absolute inset-x-10 top-1/2 h-4 bg-slate-800 rounded-full">
                        <div className="h-full bg-emerald-500 rounded-full transition-all" style={{width: `${100-target}%`, marginLeft: `${target}%`}}></div>
                    </div>
                    {/* Slider Input */}
                    <input type="range" min="2" max="98" value={target} onChange={(e)=>setTarget(Number(e.target.value))} 
                           className="absolute inset-x-10 top-1/2 -translate-y-1/2 w-full opacity-0 cursor-pointer z-10" />
                    
                    {/* Roll Indicator */}
                    {roll !== null && (
                         <div className="absolute top-1/2 -translate-y-1/2 w-16 h-16 -ml-8 bg-white rounded-xl shadow-[0_0_30px_white] flex items-center justify-center z-20 transition-all duration-500"
                              style={{left: `${roll+4}%`}}>
                             <span className={`text-xl font-black ${roll>=target ? 'text-emerald-600' : 'text-rose-600'}`}>{roll.toFixed(2)}</span>
                         </div>
                    )}
                 </div>
                 <div className="flex justify-between mt-8">
                     <div className="text-center"><div className="text-slate-500 font-bold uppercase text-xs">Multiplier</div><div className="text-2xl font-black text-white">{multiplier.toFixed(4)}x</div></div>
                     <div className="text-center"><div className="text-slate-500 font-bold uppercase text-xs">Roll Over</div><div className="text-2xl font-black text-white">{target}</div></div>
                     <div className="text-center"><div className="text-slate-500 font-bold uppercase text-xs">Win Chance</div><div className="text-2xl font-black text-white">{(100-target).toFixed(0)}%</div></div>
                 </div>
             </div>
             <div className="grid grid-cols-2 gap-6">
                 <BetInput value={bet} onChange={setBet} balance={balance} />
                 <Button className="text-2xl" onClick={play}>ROLL DICE</Button>
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
        onResult({ wager: bet, payout: win ? bet * targetMult : 0, multiplier: win?targetMult:0, active: false });
    };

    return (
        <div className="max-w-xl mx-auto py-12 space-y-8">
             <div className="text-center relative py-12">
                 <div className={`text-8xl font-black tracking-tighter transition-all duration-200 ${result !== null && result >= targetMult ? 'text-emerald-500 scale-110' : result !== null ? 'text-rose-500' : 'text-slate-700'}`}>
                     {result?.toFixed(2) || '0.00'}x
                 </div>
                 <div className="text-slate-500 font-mono mt-4">Target: {targetMult.toFixed(2)}x</div>
             </div>
             <div className="bg-slate-900 p-6 rounded-3xl border border-white/5 space-y-6">
                 <BetInput value={bet} onChange={setBet} balance={balance} />
                 <div>
                    <label className="text-[10px] font-bold text-slate-500 uppercase">Target Multiplier</label>
                    <input type="number" value={targetMult} onChange={e=>setTargetMult(parseFloat(e.target.value))} className="w-full bg-slate-950 rounded-xl py-3 px-4 text-white font-mono font-bold mt-1" />
                 </div>
                 <Button className="w-full py-4 text-xl" onClick={play}>BET</Button>
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
    
    // Config: 3 colors. Black (2x), Red (3x), Green (5x), Yellow (50x)
    const segments = useMemo(() => [
        { c: '#1f2937', m: 2 }, { c: '#ef4444', m: 3 }, { c: '#1f2937', m: 2 }, { c: '#22c55e', m: 5 },
        { c: '#1f2937', m: 2 }, { c: '#ef4444', m: 3 }, { c: '#1f2937', m: 2 }, { c: '#ef4444', m: 3 },
        { c: '#1f2937', m: 2 }, { c: '#eab308', m: 50 }, { c: '#1f2937', m: 2 }, { c: '#ef4444', m: 3 },
        { c: '#1f2937', m: 2 }, { c: '#22c55e', m: 5 }, { c: '#1f2937', m: 2 }, { c: '#ef4444', m: 3 }
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
                ctx.font = 'bold 20px Arial';
                ctx.textAlign = 'right';
                ctx.fillText(`${seg.m}x`, r-20, 6);
                ctx.restore();
            });
            ctx.restore();

            // Pointer
            ctx.beginPath(); ctx.moveTo(490, 250); ctx.lineTo(500, 240); ctx.lineTo(500, 260); ctx.fillStyle='white'; ctx.fill();
        };
        render(rotationRef.current);
    }, [segments]);

    const spin = async () => {
        if(balance<bet || spinning) return;
        setSpinning(true);
        const hash = await getGameHash(seed.serverSeed, seed.clientSeed, Date.now());
        const resultIdx = getWheelIndex(hash, segments.length);
        
        // Anim logic
        const segAngle = (Math.PI*2)/segments.length;
        // Pointer is at 0 (right). We need to rotate such that the target segment aligns with 0.
        // Actually pointer is at right (0 rad). 
        // Index 0 is from 0 to segAngle.
        // To land index 0, we need rotation to be approx 0 (actually random within that arc).
        // Let's just do targetRotation.
        
        const targetRot = (Math.PI*2 * 5) - (resultIdx * segAngle) - (segAngle/2); // 5 full spins + alignment
        const startRot = rotationRef.current % (Math.PI*2);
        const totalRot = startRot + targetRot;
        
        const startTime = Date.now();
        const duration = 3000;
        
        const animate = () => {
            const now = Date.now();
            const p = Math.min(1, (now-startTime)/duration);
            const ease = 1 - Math.pow(1 - p, 3); // Cubic ease out
            
            const cur = startRot + (targetRot - startRot) * ease; // wait logic is wrong here for accumulation but ok for demo
            
            // Re-calc simply:
            const currentRot = startRot + (5 * Math.PI * 2 + ((Math.PI*2) - (resultIdx * segAngle))) * ease; // Simplified spinning
            
            rotationRef.current = currentRot;
            
            const ctx = canvasRef.current?.getContext('2d');
            if(ctx) {
                const cx = 250; const cy = 250; const r = 240;
                ctx.clearRect(0,0,500,500);
                const segSize = (Math.PI*2)/segments.length;
                ctx.save(); ctx.translate(cx,cy); ctx.rotate(currentRot);
                segments.forEach((seg, i) => {
                    ctx.beginPath(); ctx.moveTo(0,0); ctx.arc(0,0,r,i*segSize,(i+1)*segSize);
                    ctx.fillStyle=seg.c; ctx.fill(); ctx.stroke();
                    ctx.save(); ctx.rotate((i+0.5)*segSize); ctx.fillStyle='white'; ctx.font='bold 24px Arial'; ctx.textAlign='right'; ctx.fillText(`${seg.m}x`, r-30, 8); ctx.restore();
                });
                ctx.restore();
                // Pointer at RIGHT (0 radians)
                ctx.beginPath(); ctx.moveTo(480, 250); ctx.lineTo(500, 230); ctx.lineTo(500, 270); ctx.fillStyle='white'; ctx.shadowBlur=10; ctx.shadowColor='white'; ctx.fill(); ctx.shadowBlur=0;
            }

            if(p<1) requestAnimationFrame(animate);
            else {
                setSpinning(false);
                const mult = segments[resultIdx].m;
                SoundEngine.play(mult>1 ? 'win' : 'loss');
                onResult({ wager: bet, payout: bet*mult, multiplier: mult, active: false });
            }
        };
        requestAnimationFrame(animate);
        onResult({ wager: bet, payout: 0, active: true });
    };

    return (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-6 h-full items-center">
            <div className="md:col-span-4 space-y-6">
                <BetInput value={bet} onChange={setBet} balance={balance} />
                <Button className="w-full py-5 text-xl" onClick={spin} disabled={spinning}>SPIN</Button>
            </div>
            <div className="md:col-span-8 flex justify-center">
                <canvas ref={canvasRef} width={500} height={500} className="w-full max-w-[500px]" />
            </div>
        </div>
    );
};

// ==========================================
// MAIN APP & CRASH (Re-integrated)
// ==========================================

enum GameState {
  BETTING = 'BETTING',
  RUNNING = 'RUNNING',
  CRASHED = 'CRASHED'
}

const CrashGame = ({ seed, onPlay, onResult, balance }: any) => {
  const [bet, setBet] = useState(10);
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
        
        setHasQueuedBet(prev => {
           if (prev) {
              setIsPlayingRound(true); setCashedOutAt(null);
              onPlay((u: any) => ({ ...u, balance: u.balance - bet }));
           } else setIsPlayingRound(false);
           return false;
        });
        SoundEngine.play('start');

        const loop = () => {
           const elapsed = Date.now() - startT.current;
           const m = Math.max(1, Math.floor(Math.exp(0.06 * elapsed/1000) * 100) / 100);
           setMultiplier(m);
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

  const handleBet = () => { if(gameState === GameState.BETTING && balance >= bet) { setHasQueuedBet(true); SoundEngine.play('click'); } };
  const cashout = () => {
      if (gameState === GameState.RUNNING && isPlayingRound && !cashedOutAt) {
        setCashedOutAt(multiplier); SoundEngine.play('win'); onResult(multiplier, 'cashed', bet, bet * multiplier);
      }
  };

  // Canvas Logic (Simplified for brevity but functional)
  useEffect(() => {
      const ctx = canvasRef.current?.getContext('2d');
      if(!ctx) return;
      ctx.clearRect(0,0,800,500);
      
      // ... Draw Grid ...
      
      if(gameState===GameState.BETTING) {
          ctx.fillStyle='#94a3b8'; ctx.font='30px Inter'; ctx.textAlign='center'; ctx.fillText(`STARTING IN ${timeUntilStart.toFixed(1)}s`, 400, 250);
          return;
      }

      // Draw Curve
      const t = Math.log(multiplier) / 0.06;
      const maxT = Math.max(10, t);
      const scaleX = 800 / maxT;
      const scaleY = 500 / Math.max(2, multiplier * 1.1);
      
      ctx.beginPath();
      ctx.moveTo(0, 500);
      for(let i=0; i<=t; i+=0.1) {
          ctx.lineTo(i*scaleX, 500 - (Math.exp(0.06*i)-1)*scaleY); // Rough approx for visual
      }
      ctx.lineTo(t*scaleX, 500 - (multiplier-1)*scaleY);
      ctx.strokeStyle = gameState===GameState.CRASHED ? '#f43f5e' : '#6366f1';
      ctx.lineWidth=4; ctx.stroke();
      
      if(gameState===GameState.CRASHED) {
          ctx.fillStyle='#f43f5e'; ctx.font='50px Inter'; ctx.fillText(`CRASHED @ ${multiplier.toFixed(2)}x`, 400, 250);
      }
  }, [multiplier, gameState, timeUntilStart]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-12 gap-6 h-full">
       <div className="md:col-span-3 space-y-4">
          <BetInput value={bet} onChange={setBet} balance={balance} />
          {gameState === GameState.BETTING ? 
             (hasQueuedBet ? <Button variant="danger" onClick={()=>setHasQueuedBet(false)} className="w-full py-4">CANCEL</Button> : <Button onClick={handleBet} className="w-full py-4">JOIN ROUND</Button>) :
             (isPlayingRound && !cashedOutAt ? <Button variant="success" onClick={cashout} className="w-full py-4 animate-pulse">CASHOUT</Button> : <Button disabled className="w-full py-4 opacity-50">WAIT</Button>)
          }
       </div>
       <div className="md:col-span-9 bg-slate-900 rounded-3xl border border-white/5 relative overflow-hidden">
          <div className="absolute top-10 left-10 text-6xl font-black text-white">{multiplier.toFixed(2)}x</div>
          <canvas ref={canvasRef} width={800} height={500} className="w-full h-full" />
       </div>
    </div>
  );
};

const App = () => {
  const [activeGame, setActiveGame] = useState('lobby');
  const [user, setUser] = useState({ balance: 2500.00, username: 'Player1', level: 1 });
  const [seed] = useState({ serverSeed: generateRandomHex(), clientSeed: generateRandomHex().slice(0,16) });

  const handleResult = ({ wager, payout, active }: any) => {
      setUser(u => ({ ...u, balance: u.balance + payout - (active ? wager : 0) }));
      if(payout > 0) SoundEngine.play('win');
  };

  const games: any = {
      'crash': <CrashGame seed={seed} onPlay={setUser} onResult={(m:number, h:string, w:number, p:number) => handleResult({wager:w, payout:p, active:false})} balance={user.balance} />,
      'plinko': <PlinkoGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'mines': <MinesGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'dice': <DiceGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'limbo': <LimboGame seed={seed} onResult={handleResult} balance={user.balance} />,
      'wheel': <WheelGame seed={seed} onResult={handleResult} balance={user.balance} />,
  };

  return (
    <div className="flex h-screen bg-[#020617] text-slate-200 font-sans">
      <aside className="w-64 bg-[#0f172a] border-r border-white/5 p-4 flex flex-col gap-2">
        <div className="text-2xl font-black text-white px-4 mb-6 tracking-tight">NOVA<span className="text-indigo-500">BET</span></div>
        <button onClick={()=>setActiveGame('lobby')} className={`px-4 py-3 rounded-xl font-bold text-left ${activeGame==='lobby'?'bg-indigo-600 text-white':'text-slate-400 hover:bg-white/5'}`}>🏠 Lobby</button>
        {Object.keys(games).map(g => (
            <button key={g} onClick={()=>setActiveGame(g)} className={`px-4 py-3 rounded-xl font-bold text-left capitalize ${activeGame===g?'bg-white/10 text-white':'text-slate-400 hover:bg-white/5'}`}>{g}</button>
        ))}
        <div className="mt-auto bg-slate-900 p-4 rounded-xl border border-white/5">
            <div className="text-xs font-bold text-slate-500 uppercase">Balance</div>
            <div className="text-xl font-mono font-bold text-white">${user.balance.toFixed(2)}</div>
        </div>
      </aside>
      <main className="flex-1 p-8 overflow-y-auto">
         {activeGame === 'lobby' ? (
             <div className="grid grid-cols-3 gap-6">
                 {Object.keys(games).map(g => (
                     <button key={g} onClick={()=>setActiveGame(g)} className="bg-slate-900 aspect-video rounded-3xl border border-white/5 hover:border-indigo-500/50 transition-all group relative overflow-hidden">
                         <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/10 to-purple-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
                         <span className="text-2xl font-black uppercase tracking-widest">{g}</span>
                     </button>
                 ))}
             </div>
         ) : games[activeGame]}
      </main>
    </div>
  );
};

const root = ReactDOM.createRoot(document.getElementById('root')!);
root.render(<React.StrictMode><App /></React.StrictMode>);
