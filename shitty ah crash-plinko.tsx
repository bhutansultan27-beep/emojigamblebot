
import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import ReactDOM from 'react-dom/client';

// ==========================================
// CONFIG & TYPES
// ==========================================

enum GameState { WAITING = 'WAITING', RUNNING = 'RUNNING', CRASHED = 'CRASHED', WON = 'WON', LOST = 'LOST', CASHOUT = 'CASHOUT' }
type GameMode = 'crash' | 'limbo' | 'plinko' | 'dice' | 'mines' | 'fairness';
type BetMode = 'manual' | 'auto';

interface UserState { balance: number; username: string; wagered: number; wins: number; profit: number; }

interface GameHistoryItem {
  id: string;
  game: GameMode;
  result: number;
  wager: number;
  payout: number;
  timestamp: number;
  serverSeed: string;
  clientSeed: string;
  nonce: number;
  hash: string;
}

interface AutoBetConfig {
  enabled: boolean;
  onWin: { action: 'reset' | 'increase'; value: number }; 
  onLoss: { action: 'reset' | 'increase'; value: number };
  stopProfit: number;
  stopLoss: number;
  baseBet: number;
}

// ==========================================
// LIB: CRYPTO & FAIRNESS
// ==========================================

const generateRandomHex = () => Array.from(crypto.getRandomValues(new Uint8Array(32))).map(b => b.toString(16).padStart(2, '0')).join('');

async function sha256(message: string): Promise<string> {
  const msgBuffer = new TextEncoder().encode(message);
  const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
  return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function getGameHash(serverSeed: string, clientSeed: string, nonce: number) {
  return await sha256(`${serverSeed}:${clientSeed}:${nonce}`);
}

// --- Generators ---

async function generateCrashPoint(hash: string) {
  const h = parseInt(hash.slice(0, 13), 16);
  const e = Math.pow(2, 52);
  if (h % 33 === 0) return 1.00; 
  const result = Math.floor(((100 * e - h) / (e - h))) / 100;
  return Math.max(1.00, result);
}

function generateDiceRoll(hash: string) {
  const val = parseInt(hash.slice(0, 8), 16);
  return (val % 10001) / 100;
}

function generateLimboResult(hash: string) {
  const h = parseInt(hash.slice(0, 13), 16);
  const e = Math.pow(2, 52);
  const result = Math.floor(((100 * e - h) / (e - h))) / 100;
  return Math.max(1.00, result);
}

function generateMinesBoard(hash: string, mines: number) {
  const deck = Array.from({ length: 25 }, (_, i) => i);
  let seed = hash;
  const board = new Array(25).fill(false);
  for (let i = 0; i < mines; i++) {
    const val = parseInt(seed.slice(0, 8), 16);
    const idx = val % deck.length;
    const pos = deck.splice(idx, 1)[0];
    board[pos] = true;
    seed = seed.slice(1) + seed[0]; // simple rotate for demo
  }
  return board;
}

function generatePlinkoPath(hash: string, rows: number) {
  const path = [];
  for (let i = 0; i < rows; i++) {
    const val = parseInt(hash.slice(i, i+1), 16); 
    path.push(val % 2 === 0 ? 0 : 1); 
  }
  return path;
}

// Plinko Multipliers (Simplified set for 8-16 rows, Low/Med/High)
// In a full app, this would be a complete matrix.
const PLINKO_MULT: any = {
  '8-low': [5.6, 2.1, 1.1, 1, 0.5, 1, 1.1, 2.1, 5.6],
  '8-medium': [13, 3, 1.3, 0.7, 0.4, 0.4, 0.7, 1.3, 3, 13],
  '8-high': [29, 4, 1.5, 0.3, 0.2, 0.2, 0.2, 0.3, 1.5, 4, 29],
  '12-medium': [33, 11, 4, 2, 1.1, 0.6, 0.3, 0.6, 1.1, 2, 4, 11, 33],
  '16-medium': [110, 41, 10, 5, 3, 1.5, 1, 0.5, 0.3, 0.5, 1, 1.5, 3, 5, 10, 41, 110],
  // Fallback
  'default': [10, 5, 2, 1, 0.5, 1, 2, 5, 10]
};

const getPlinkoMultipliers = (rows: number, risk: string) => {
  return PLINKO_MULT[`${rows}-${risk}`] || PLINKO_MULT['16-medium'] || PLINKO_MULT['default']; // Safety Fallback
};


// ==========================================
// UI COMPONENTS
// ==========================================

const Button = ({ onClick, children, variant = 'primary', className = '', disabled = false, size = 'md' }: any) => {
  const base = "relative rounded-xl font-black uppercase tracking-widest transition-all transform active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 flex items-center justify-center";
  const sizes = { sm: 'text-[10px] px-3 py-2', md: 'text-xs px-6 py-4', lg: 'text-sm px-8 py-5' };
  const variants: any = {
    primary: "bg-indigo-600 hover:bg-indigo-500 text-white shadow-[0_4px_20px_rgba(79,70,229,0.3)] border-t border-white/10",
    success: "bg-emerald-500 hover:bg-emerald-400 text-slate-900 shadow-[0_4px_20px_rgba(16,185,129,0.3)] border-t border-white/20",
    danger: "bg-rose-600 hover:bg-rose-500 text-white shadow-[0_4px_20px_rgba(225,29,72,0.3)] border-t border-white/10",
    secondary: "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-white/5",
    ghost: "bg-transparent hover:bg-white/5 text-slate-400 hover:text-white"
  };
  return <button onClick={onClick} disabled={disabled} className={`${base} ${sizes[size as keyof typeof sizes]} ${variants[variant]} ${className}`}>{children}</button>;
};

const Input = ({ label, value, onChange, disabled, type="number", prefix }: any) => (
  <div className="bg-slate-950/50 p-2 rounded-2xl border border-white/5 space-y-1 focus-within:border-indigo-500/50 transition-colors">
    {label && <div className="flex justify-between px-2"><label className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{label}</label></div>}
    <div className="relative">
      {prefix && <div className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 font-mono text-xs pointer-events-none">{prefix}</div>}
      <input 
        type={type} 
        value={value} 
        onChange={(e) => onChange(type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value)}
        disabled={disabled}
        className={`w-full bg-slate-900 border border-white/10 rounded-xl ${prefix ? 'pl-8' : 'pl-4'} pr-4 py-2.5 font-mono text-white text-sm focus:outline-none focus:bg-slate-800 transition-all`}
      />
    </div>
  </div>
);

// --- Generic Auto-Bet Controller ---
const useAutoBet = (config: AutoBetConfig, setConfig: any, setBet: any) => {
  const state = useRef({ profit: 0, loss: 0 });

  const processResult = (payout: number, bet: number) => {
    if (!config.enabled) return bet;
    
    const profit = payout - bet;
    state.current.profit += profit;

    // Check Limits
    if (state.current.profit >= config.stopProfit || state.current.profit <= -config.stopLoss) {
      setConfig((c:any) => ({ ...c, enabled: false }));
      return null; // Stop
    }

    let nextBet = bet;
    if (profit > 0) {
      // Win
      if (config.onWin.action === 'increase') nextBet = bet * (1 + config.onWin.value / 100);
      else nextBet = config.baseBet;
    } else {
      // Loss
      if (config.onLoss.action === 'increase') nextBet = bet * (1 + config.onLoss.value / 100);
      else nextBet = config.baseBet;
    }
    setBet(nextBet);
    return nextBet;
  };

  const start = (currentBet: number) => {
    setConfig((c:any) => ({ ...c, enabled: true, baseBet: currentBet }));
    state.current = { profit: 0, loss: 0 };
  };

  const stop = () => setConfig((c:any) => ({ ...c, enabled: false }));

  return { processResult, start, stop };
};

const BetControls = ({ bet, setBet, mode, setMode, autoConfig, setAutoConfig, onAutoStart, autoActive, children }: any) => {
  return (
    <div className="bg-slate-900 rounded-3xl p-6 border border-white/5 shadow-2xl space-y-6">
      <div className="bg-slate-950 p-1 rounded-xl flex">
         {(['manual', 'auto'] as BetMode[]).map(m => (
           <button key={m} onClick={() => !autoActive && setMode(m)} className={`flex-1 py-2 rounded-lg text-[10px] font-bold uppercase transition-all ${mode === m ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}>{m}</button>
         ))}
      </div>

      <div className="space-y-4">
        <Input label="Bet Amount" value={bet} onChange={setBet} disabled={autoActive} prefix="$" />
        <div className="grid grid-cols-2 gap-2">
           <Button variant="secondary" size="sm" onClick={() => setBet((b:number) => b/2)} disabled={autoActive}>1/2</Button>
           <Button variant="secondary" size="sm" onClick={() => setBet((b:number) => b*2)} disabled={autoActive}>2x</Button>
        </div>
      </div>

      {children}

      {mode === 'auto' && (
        <div className="space-y-4 animate-in fade-in slide-in-from-top-4 pt-4 border-t border-white/5">
          <div className="grid grid-cols-2 gap-4">
            <Input label="On Win %" value={autoConfig.onWin.value} onChange={(v:number) => setAutoConfig({...autoConfig, onWin: {...autoConfig.onWin, value: v}})} disabled={autoActive} prefix="+" />
            <Input label="On Loss %" value={autoConfig.onLoss.value} onChange={(v:number) => setAutoConfig({...autoConfig, onLoss: {...autoConfig.onLoss, value: v}})} disabled={autoActive} prefix="+" />
          </div>
          <Input label="Stop Profit" value={autoConfig.stopProfit} onChange={(v:number) => setAutoConfig({...autoConfig, stopProfit: v})} disabled={autoActive} prefix="$" />
          <Input label="Stop Loss" value={autoConfig.stopLoss} onChange={(v:number) => setAutoConfig({...autoConfig, stopLoss: v})} disabled={autoActive} prefix="$" />
        </div>
      )}

      {mode === 'auto' && (
        <Button onClick={onAutoStart} variant={autoActive ? 'danger' : 'success'} className="w-full py-5 text-sm">
          {autoActive ? 'STOP AUTO' : 'START AUTO'}
        </Button>
      )}
    </div>
  );
};

// ==========================================
// GAMES
// ==========================================

// --- CRASH ---
const CrashGame = ({ seed, onPlay, onResult, balance }: any) => {
  const [bet, setBet] = useState(10);
  const [autoCashout, setAutoCashout] = useState(2.00);
  const [mode, setMode] = useState<BetMode>('manual');
  const [gameState, setGameState] = useState<GameState>(GameState.WAITING);
  const [multiplier, setMultiplier] = useState(1.00);
  const [cashedOutAt, setCashedOutAt] = useState<number | null>(null);
  
  // Auto Bet
  const [autoActive, setAutoActive] = useState(false);
  const [autoConfig, setAutoConfig] = useState<AutoBetConfig>({
    enabled: false, baseBet: 10,
    onWin: { action: 'reset', value: 0 }, onLoss: { action: 'increase', value: 100 },
    stopProfit: 1000, stopLoss: 500
  });
  const auto = useAutoBet(autoConfig, setAutoConfig, setBet);
  
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const crashPointRef = useRef(0);
  const startTimeRef = useRef(0);
  const reqRef = useRef(0);
  const gameRef = useRef({ gameState, multiplier, cashedOutAt, autoCashout });

  useEffect(() => { gameRef.current = { gameState, multiplier, cashedOutAt, autoCashout }; }, [gameState, multiplier, cashedOutAt, autoCashout]);

  const startGame = useCallback(async () => {
    if (balance < bet) { setAutoActive(false); return; }
    onPlay(bet);
    setGameState(GameState.RUNNING);
    setCashedOutAt(null);
    setMultiplier(1.00);

    const hash = await getGameHash(seed.serverSeed, seed.clientSeed, seed.nonce);
    const cp = await generateCrashPoint(hash);
    crashPointRef.current = cp;
    startTimeRef.current = Date.now();
    
    const loop = () => {
      const elapsed = Date.now() - startTimeRef.current;
      const nextMult = Math.floor(Math.pow(Math.E, 0.00006 * elapsed) * 100) / 100;
      setMultiplier(nextMult);

      if (gameRef.current.gameState === GameState.RUNNING && !gameRef.current.cashedOutAt && gameRef.current.autoCashout > 1 && nextMult >= gameRef.current.autoCashout) {
        handleFinish(cp, hash, gameRef.current.autoCashout); return;
      }

      if (nextMult >= cp) handleFinish(cp, hash, null);
      else reqRef.current = requestAnimationFrame(loop);
    };
    reqRef.current = requestAnimationFrame(loop);
  }, [balance, bet, seed, onPlay]);

  const handleFinish = (cp: number, hash: string, userCashout: number | null) => {
     cancelAnimationFrame(reqRef.current);
     let payout = 0;
     if (userCashout) {
       setCashedOutAt(userCashout); setMultiplier(userCashout);
       payout = bet * userCashout; onResult(userCashout, hash, bet, payout); setGameState(GameState.WON);
     } else {
       setMultiplier(cp); onResult(cp, hash, bet, 0); setGameState(GameState.CRASHED);
     }

     if (autoConfig.enabled) {
        const next = auto.processResult(payout, bet);
        if (next) setTimeout(startGame, 2000);
        else setAutoActive(false);
     }
  };

  const manualCashout = () => {
    if (gameState === GameState.RUNNING && !cashedOutAt) handleFinish(crashPointRef.current, 'pending...', multiplier);
  };

  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d');
    if (!ctx || !canvasRef.current) return;
    const { width, height } = canvasRef.current;
    ctx.clearRect(0, 0, width, height);

    ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 1; ctx.beginPath();
    for (let i = 0; i < 6; i++) { ctx.moveTo(0, height - (height/5)*i); ctx.lineTo(width, height - (height/5)*i); }
    ctx.stroke();

    if (gameState === GameState.WAITING) {
       ctx.fillStyle = '#475569'; ctx.font = '700 24px Inter'; ctx.textAlign = 'center'; ctx.fillText("READY TO LAUNCH", width/2, height/2); return;
    }
    const t = Math.min(1, (multiplier - 1) / 5); 
    const x = t * (width - 60); const y = height - (t * (height - 60)) - 30;
    
    ctx.beginPath(); ctx.moveTo(0, height); ctx.quadraticCurveTo(x/2, height, x, y);
    ctx.lineWidth = 6; ctx.strokeStyle = gameState === GameState.CRASHED ? '#ef4444' : '#6366f1'; ctx.lineCap = 'round'; ctx.stroke();
    
    ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill(); 

  }, [multiplier, gameState, cashedOutAt]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-12 gap-6 h-full">
      <div className="md:col-span-4 flex flex-col gap-4">
        <BetControls bet={bet} setBet={setBet} mode={mode} setMode={setMode} autoConfig={autoConfig} setAutoConfig={setAutoConfig} autoActive={autoActive} 
          onAutoStart={() => { if(autoActive) auto.stop(); else { auto.start(bet); setAutoActive(true); startGame(); } }}>
           <Input label="Auto Cashout (x)" value={autoCashout} onChange={setAutoCashout} disabled={autoActive || gameState === GameState.RUNNING} />
           {gameState === GameState.RUNNING && !cashedOutAt ? (
             <Button onClick={manualCashout} variant="success" className="w-full py-5 text-xl animate-pulse">CASHOUT ${(bet * multiplier)?.toFixed(2) || '0.00'}</Button>
           ) : (
             mode === 'manual' && <Button onClick={startGame} className="w-full py-5 text-xl">BET</Button>
           )}
        </BetControls>
      </div>
      <div className="md:col-span-8 relative bg-slate-900 rounded-3xl border border-white/5 overflow-hidden flex flex-col shadow-2xl">
        <div className="absolute top-10 w-full text-center z-10 pointer-events-none">
          <div className={`text-8xl font-black tabular-nums ${gameState === GameState.CRASHED ? 'text-rose-500' : cashedOutAt ? 'text-emerald-400' : 'text-white'}`}>{multiplier?.toFixed(2) || '1.00'}x</div>
        </div>
        <canvas ref={canvasRef} width={800} height={500} className="w-full h-full object-cover" />
      </div>
    </div>
  );
};

// --- DICE ---
const DiceGame = ({ seed, onPlay, onResult, balance }: any) => {
  const [bet, setBet] = useState(10);
  const [target, setTarget] = useState(50);
  const [mode, setMode] = useState<BetMode>('manual');
  const [rollMode, setRollMode] = useState<'over'|'under'>('over');
  const [lastRoll, setLastRoll] = useState<number | null>(null);
  const [isRolling, setIsRolling] = useState(false);
  const [autoActive, setAutoActive] = useState(false);
  const [autoConfig, setAutoConfig] = useState<AutoBetConfig>({
    enabled: false, baseBet: 10, onWin: { action: 'reset', value: 0 }, onLoss: { action: 'increase', value: 100 }, stopProfit: 1000, stopLoss: 500
  });
  const auto = useAutoBet(autoConfig, setAutoConfig, setBet);

  const winChance = rollMode === 'over' ? (100 - target) : target;
  const multiplier = (99) / winChance;

  const roll = async () => {
    if (balance < bet) { setAutoActive(false); return; }
    setIsRolling(true);
    onPlay(bet);
    
    await new Promise(r => setTimeout(r, 400)); // Anim delay
    const hash = await getGameHash(seed.serverSeed, seed.clientSeed, seed.nonce);
    const result = generateDiceRoll(hash);
    setLastRoll(result); setIsRolling(false);
    
    const win = rollMode === 'over' ? result > target : result < target;
    const payout = win ? bet * multiplier : 0;
    onResult(multiplier, hash, bet, payout);

    if (autoConfig.enabled) {
      const next = auto.processResult(payout, bet);
      if (next) setTimeout(roll, 500); else setAutoActive(false);
    }
  };

  return (
     <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 h-full">
        <div className="lg:col-span-4 flex flex-col gap-4">
           <BetControls bet={bet} setBet={setBet} mode={mode} setMode={setMode} autoConfig={autoConfig} setAutoConfig={setAutoConfig} autoActive={autoActive}
             onAutoStart={() => { if(autoActive) auto.stop(); else { auto.start(bet); setAutoActive(true); roll(); } }}>
              {mode === 'manual' && <Button onClick={roll} disabled={isRolling} className="w-full py-5 text-xl">ROLL DICE</Button>}
           </BetControls>
        </div>
        <div className="lg:col-span-8 bg-slate-900 rounded-[40px] p-10 border border-white/5 shadow-2xl relative flex flex-col items-center justify-center gap-12">
           <div className={`text-8xl font-black ${lastRoll !== null ? ((rollMode === 'over' ? lastRoll > target : lastRoll < target) ? 'text-emerald-400' : 'text-rose-500') : 'text-slate-700'}`}>{lastRoll?.toFixed(2) || '50.00'}</div>
           <div className="w-full max-w-xl bg-slate-950/50 p-6 rounded-3xl border border-white/5 space-y-6">
              <div className="relative h-4 bg-slate-800 rounded-full flex items-center">
                 <div className={`absolute h-full rounded-full transition-all ${rollMode === 'over' ? 'right-0 bg-emerald-500' : 'left-0 bg-emerald-500'}`} style={{ width: `${winChance}%` }} />
                 <input type="range" min="2" max="98" value={target} onChange={(e) => setTarget(Number(e.target.value))} className="absolute w-full h-full opacity-0 cursor-pointer z-20" disabled={autoActive || isRolling} />
                 <div className="absolute w-6 h-6 bg-white rounded-full shadow-lg z-10 pointer-events-none transition-all" style={{ left: `calc(${target}% - 12px)` }} />
              </div>
              <div className="flex justify-between items-center text-sm font-mono text-slate-400">
                 <div>MULT: <span className="text-white">{multiplier?.toFixed(4) || '0.0000'}x</span></div>
                 <button onClick={() => !autoActive && setRollMode(m => m === 'over' ? 'under' : 'over')} className="bg-slate-800 px-4 py-2 rounded-lg text-xs font-bold uppercase border border-white/5">Roll {rollMode}</button>
                 <div>CHANCE: <span className="text-white">{winChance?.toFixed(2) || '0.00'}%</span></div>
              </div>
           </div>
        </div>
     </div>
  );
};

// --- LIMBO ---
const LimboGame = ({ seed, onPlay, onResult, balance }: any) => {
  const [bet, setBet] = useState(10);
  const [target, setTarget] = useState(2.0);
  const [result, setResult] = useState<number|null>(null);
  const [mode, setMode] = useState<BetMode>('manual');
  const [autoActive, setAutoActive] = useState(false);
  const [autoConfig, setAutoConfig] = useState<AutoBetConfig>({
    enabled: false, baseBet: 10, onWin: { action: 'reset', value: 0 }, onLoss: { action: 'increase', value: 100 }, stopProfit: 1000, stopLoss: 500
  });
  const auto = useAutoBet(autoConfig, setAutoConfig, setBet);

  const fire = async () => {
    if (balance < bet) { setAutoActive(false); return; }
    onPlay(bet);
    const hash = await getGameHash(seed.serverSeed, seed.clientSeed, seed.nonce);
    const r = generateLimboResult(hash);
    setResult(r);
    const win = r >= target;
    const payout = win ? bet * target : 0;
    onResult(target, hash, bet, payout);

    if (autoConfig.enabled) {
      const next = auto.processResult(payout, bet);
      if (next) setTimeout(fire, 200); else setAutoActive(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 h-full">
      <div className="lg:col-span-4 flex flex-col gap-4">
        <BetControls bet={bet} setBet={setBet} mode={mode} setMode={setMode} autoConfig={autoConfig} setAutoConfig={setAutoConfig} autoActive={autoActive}
          onAutoStart={() => { if(autoActive) auto.stop(); else { auto.start(bet); setAutoActive(true); fire(); } }}>
           <Input label="Target Multiplier" value={target} onChange={setTarget} disabled={autoActive} prefix="x" />
           {mode === 'manual' && <Button onClick={fire} className="w-full py-5 text-xl">BET</Button>}
        </BetControls>
      </div>
      <div className="lg:col-span-8 bg-slate-900 rounded-[40px] p-10 border border-white/5 shadow-2xl relative flex items-center justify-center">
         <div className={`text-9xl font-black font-mono tracking-tighter ${result ? (result >= target ? 'text-emerald-400' : 'text-rose-500') : 'text-slate-700'}`}>
            {result ? result.toFixed(2) + 'x' : '0.00x'}
         </div>
         {result && <div className="absolute bottom-10 text-slate-500 font-bold uppercase tracking-widest">Target: {target?.toFixed(2) || '0.00'}x</div>}
      </div>
    </div>
  );
};

// --- MINES ---
const MinesGame = ({ seed, onPlay, onResult, balance }: any) => {
  const [bet, setBet] = useState(10);
  const [mines, setMines] = useState(3);
  const [board, setBoard] = useState<boolean[]>([]);
  const [revealed, setRevealed] = useState<number[]>([]);
  const [status, setStatus] = useState<GameState>(GameState.WAITING);
  
  const activeSeed = useRef({ server: '', client: '', nonce: 0 });

  const getMultiplier = (safeCount: number) => {
    let mult = 1; 
    for(let i=0; i<safeCount; i++) mult *= (25-i)/(25-mines-i);
    return mult;
  };

  const start = async () => {
    if (balance < bet) return;
    onPlay(bet);
    activeSeed.current = { ...seed }; // Lock seed for session
    const b = generateMinesBoard(await getGameHash(seed.serverSeed, seed.clientSeed, seed.nonce), mines);
    setBoard(b); setRevealed([]); setStatus(GameState.RUNNING);
  };

  const click = (i: number) => {
    if (status !== GameState.RUNNING || revealed.includes(i)) return;
    if (board[i]) {
      // Boom
      setRevealed([...revealed, i]); setStatus(GameState.LOST);
      onResult(0, 'mined', bet, 0); // Partial hash for simplicity here
    } else {
      setRevealed([...revealed, i]);
    }
  };

  const cashout = () => {
    const mult = getMultiplier(revealed.length);
    onResult(mult, 'cashed', bet, bet * mult);
    setStatus(GameState.WON);
  };

  const nextMult = getMultiplier(revealed.length + 1);
  const currMult = revealed.length > 0 ? getMultiplier(revealed.length) : 1;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 h-full">
      <div className="lg:col-span-4 flex flex-col gap-4">
        <div className="bg-slate-900 rounded-3xl p-6 border border-white/5 shadow-2xl space-y-6">
           <Input label="Bet Amount" value={bet} onChange={setBet} disabled={status === GameState.RUNNING} prefix="$" />
           <div className="space-y-2">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Mines</label>
              <div className="grid grid-cols-4 gap-2">
                 {[1,3,5,10].map(m => <button key={m} onClick={() => setMines(m)} disabled={status === GameState.RUNNING} className={`py-2 rounded border ${mines===m ? 'bg-indigo-600 border-indigo-500 text-white' : 'border-white/5 text-slate-500'}`}>{m}</button>)}
              </div>
           </div>
           {status === GameState.RUNNING ? (
             <Button onClick={cashout} variant="success" className="w-full py-5 text-xl" disabled={revealed.length===0}>CASHOUT ${(bet*currMult)?.toFixed(2) || '0.00'}</Button>
           ) : (
             <Button onClick={start} className="w-full py-5 text-xl">START GAME</Button>
           )}
        </div>
      </div>
      <div className="lg:col-span-8 bg-slate-900 rounded-[40px] p-10 border border-white/5 shadow-2xl flex items-center justify-center">
         <div className="grid grid-cols-5 gap-3 w-full max-w-[500px] aspect-square">
            {Array(25).fill(0).map((_, i) => (
              <button key={i} onClick={() => click(i)} disabled={status !== GameState.RUNNING || revealed.includes(i)}
                className={`rounded-xl text-2xl flex items-center justify-center transition-all duration-300 ${
                  revealed.includes(i) || status !== GameState.RUNNING && status !== GameState.WAITING 
                  ? (board[i] ? 'bg-rose-500 shadow-[0_0_15px_rgba(244,63,94,0.5)] scale-90' : 'bg-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.5)] scale-90') 
                  : 'bg-slate-800 hover:bg-slate-700 hover:-translate-y-1'
                }`}
              >
                {(revealed.includes(i) || (status !== GameState.RUNNING && status !== GameState.WAITING)) && (board[i] ? '💣' : '💎')}
              </button>
            ))}
         </div>
      </div>
    </div>
  );
};

// --- PLINKO ---
const PlinkoGame = ({ seed, onPlay, onResult, balance }: any) => {
  const [bet, setBet] = useState(10);
  const [rows, setRows] = useState(16);
  const [risk, setRisk] = useState('medium');
  const [mode, setMode] = useState<BetMode>('manual');
  const [balls, setBalls] = useState<any[]>([]);
  const [autoActive, setAutoActive] = useState(false);
  const [autoConfig, setAutoConfig] = useState<AutoBetConfig>({
    enabled: false, baseBet: 10, onWin: { action: 'reset', value: 0 }, onLoss: { action: 'increase', value: 100 }, stopProfit: 1000, stopLoss: 500
  });
  const auto = useAutoBet(autoConfig, setAutoConfig, setBet);
  
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const drop = async () => {
    if (balance < bet) { setAutoActive(false); return; }
    onPlay(bet);
    const hash = await getGameHash(seed.serverSeed, seed.clientSeed, seed.nonce);
    const path = generatePlinkoPath(hash, rows);
    const multipliers = getPlinkoMultipliers(rows, risk);
    const finalIndex = path.reduce((a, b) => a + b, 0);
    const multiplier = multipliers[finalIndex] || 0; // Safety
    const payout = bet * multiplier;
    onResult(multiplier, hash, bet, payout);

    setBalls(prev => [...prev, { x: 300, y: 20, path, progress: 0, finalIndex, multiplier }]);

    if (autoConfig.enabled) {
      const next = auto.processResult(payout, bet);
      if (next) setTimeout(drop, 300); else setAutoActive(false);
    }
  };

  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d');
    if (!ctx) return;
    let animId: number;
    const multipliers = getPlinkoMultipliers(rows, risk);

    const render = () => {
      ctx.clearRect(0, 0, 600, 500);
      
      // Pins
      ctx.fillStyle = '#334155';
      const gapY = 400 / rows;
      for(let r=0; r<=rows; r++) {
         for(let c=0; c<r+3; c++) {
            const px = 300 - ((r+2)*15) + (c*30);
            ctx.beginPath(); ctx.arc(px, 50+r*gapY, 3, 0, Math.PI*2); ctx.fill();
         }
      }

      // Multipliers
      multipliers.forEach((m: number, i: number) => {
         const bx = 300 - ((rows+2)*15) + (i*30) + 15;
         const by = 480;
         ctx.fillStyle = m >= 10 ? '#f59e0b' : m < 1 ? '#ef4444' : '#6366f1';
         ctx.fillRect(bx-12, by-10, 24, 20);
         ctx.fillStyle = '#fff'; ctx.font = '9px Inter'; ctx.textAlign = 'center'; ctx.fillText(`${m}x`, bx, by+4);
      });

      // Balls
      setBalls(prev => prev.map(b => {
         if (b.progress >= 1) return { ...b, finished: true };
         b.progress += 0.015;
         const r = Math.floor(b.progress * rows);
         const px = 300 - ((r)*15) + (b.path.slice(0, r).reduce((a:number,x:number)=>a+x,0) * 30);
         const nextDir = b.path[r] || 0;
         b.x += (px - b.x) * 0.1 + (nextDir ? 0.5 : -0.5);
         b.y = 50 + b.progress * 400;
         ctx.beginPath(); ctx.arc(b.x, b.y, 6, 0, Math.PI*2); ctx.fillStyle = '#fff'; ctx.shadowBlur = 10; ctx.shadowColor = '#6366f1'; ctx.fill(); ctx.shadowBlur = 0;
         return b;
      }).filter(b => !b.finished));
      animId = requestAnimationFrame(render);
    };
    render();
    return () => cancelAnimationFrame(animId);
  }, [balls, rows, risk]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 h-full">
      <div className="lg:col-span-4 flex flex-col gap-4">
        <BetControls bet={bet} setBet={setBet} mode={mode} setMode={setMode} autoConfig={autoConfig} setAutoConfig={setAutoConfig} autoActive={autoActive}
          onAutoStart={() => { if(autoActive) auto.stop(); else { auto.start(bet); setAutoActive(true); drop(); } }}>
           <div className="space-y-4">
             <div className="space-y-2"><label className="text-[10px] font-bold text-slate-500 uppercase">Risk</label><div className="flex bg-slate-950 p-1 rounded-xl">{['low','medium','high'].map(r=><button key={r} onClick={()=>setRisk(r)} className={`flex-1 py-2 rounded-lg text-[10px] font-bold uppercase ${risk===r?'bg-indigo-600':'text-slate-500'}`}>{r}</button>)}</div></div>
             <div className="space-y-2"><label className="text-[10px] font-bold text-slate-500 uppercase">Rows {rows}</label><input type="range" min="8" max="16" value={rows} onChange={e=>setRows(Number(e.target.value))} className="w-full" /></div>
           </div>
           {mode === 'manual' && <Button onClick={drop} className="w-full py-5 text-xl">DROP</Button>}
        </BetControls>
      </div>
      <div className="lg:col-span-8 bg-slate-900 rounded-[40px] border border-white/5 shadow-2xl flex items-center justify-center p-8 overflow-hidden relative">
         <canvas ref={canvasRef} width={600} height={500} className="relative z-10" />
      </div>
    </div>
  );
};


// ==========================================
// MAIN APP SHELL
// ==========================================

const App = () => {
  const [activeGame, setActiveGame] = useState<GameMode>('crash');
  const [user, setUser] = useState<UserState>({ balance: 2500.00, username: 'Player1', wagered: 0, profit: 0, wins: 0 });
  const [serverSeed, setServerSeed] = useState(generateRandomHex());
  const [clientSeed, setClientSeed] = useState(generateRandomHex().slice(0, 16));
  const [nonce, setNonce] = useState(0);
  const [history, setHistory] = useState<GameHistoryItem[]>([]);
  const [showFairness, setShowFairness] = useState(false);

  const handleGameResult = (multiplier: number, hash: string, wager: number, payout: number = 0) => {
    const profit = payout - wager;
    setUser(u => ({ ...u, balance: u.balance + profit, wagered: u.wagered + wager, profit: u.profit + profit, wins: payout > 0 ? u.wins + 1 : u.wins }));
    setHistory(prev => [{ id: generateRandomHex().slice(0, 8), game: activeGame, result: multiplier, wager, payout, timestamp: Date.now(), serverSeed, clientSeed, nonce, hash }, ...prev].slice(0, 50));
    setNonce(n => n + 1);
  };

  return (
    <div className="flex h-screen bg-[#020617] text-slate-200 font-sans selection:bg-indigo-500/30 overflow-hidden">
      <aside className="w-20 lg:w-64 bg-[#0f172a] border-r border-white/5 flex flex-col z-30">
        <div className="p-6 flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-indigo-600 to-indigo-800 rounded-xl flex items-center justify-center shadow-lg shadow-indigo-900/50 ring-1 ring-white/10"><span className="text-xl font-black text-white">N</span></div>
          <span className="hidden lg:block font-black text-xl tracking-tight text-white drop-shadow-md">NOVA<span className="text-indigo-400">BET</span></span>
        </div>
        <nav className="flex-1 px-4 space-y-2 py-4">
           {[{ id: 'crash', icon: '🚀' }, { id: 'limbo', icon: '🎯' }, { id: 'mines', icon: '💣' }, { id: 'plinko', icon: '🎱' }, { id: 'dice', icon: '🎲' }].map(item => (
             <button key={item.id} onClick={() => setActiveGame(item.id as GameMode)} className={`w-full flex items-center gap-4 px-4 py-3.5 rounded-xl transition-all ${activeGame === item.id ? 'bg-indigo-600 text-white shadow-lg' : 'text-slate-400 hover:bg-white/5 hover:text-white'}`}>
               <span className="text-xl">{item.icon}</span><span className="hidden lg:block text-sm font-bold capitalize">{item.id}</span>
             </button>
           ))}
        </nav>
        <div className="p-4 border-t border-white/5 bg-[#0f172a]"><button onClick={() => setShowFairness(true)} className="w-full flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-500/5 text-emerald-500 hover:bg-emerald-500/10 border border-emerald-500/20 transition-all"><span className="text-lg">⚖️</span><span className="hidden lg:block text-[10px] font-black uppercase tracking-wider">Fairness</span></button></div>
      </aside>

      <main className="flex-1 flex flex-col min-w-0 bg-[#020617] relative">
         <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-900/10 via-[#020617] to-[#020617] pointer-events-none"></div>
         <header className="h-20 border-b border-white/5 flex items-center justify-between px-8 bg-[#0f172a]/60 backdrop-blur-xl z-20 sticky top-0">
            <div className="flex gap-8">
               <div className="hidden xl:block"><div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-0.5">Profit</div><div className={`font-mono font-bold ${user.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>${user.profit?.toFixed(2) || '0.00'}</div></div>
            </div>
            <div className="bg-slate-900 border border-white/10 rounded-xl p-1.5 flex items-center gap-3 shadow-lg pl-4 pr-1.5">
               <div className="flex flex-col items-end"><span className="text-[10px] font-bold text-emerald-500 uppercase tracking-wider">Balance</span><div className="text-white font-mono font-bold leading-none">${user.balance?.toFixed(2) || '0.00'}</div></div>
               <button className="bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-black uppercase px-4 py-2.5 rounded-lg shadow-lg transition-all">Wallet</button>
            </div>
         </header>

         <div className="flex-1 p-6 lg:p-8 overflow-y-auto z-10">
            <div className="max-w-[1600px] mx-auto h-full flex flex-col gap-8">
               <div className="flex-1 min-h-[600px]">
                  {activeGame === 'crash' && <CrashGame seed={{serverSeed, clientSeed, nonce}} onPlay={setUser} onResult={handleGameResult} balance={user.balance} />}
                  {activeGame === 'dice' && <DiceGame seed={{serverSeed, clientSeed, nonce}} onPlay={setUser} onResult={handleGameResult} balance={user.balance} />}
                  {activeGame === 'limbo' && <LimboGame seed={{serverSeed, clientSeed, nonce}} onPlay={setUser} onResult={handleGameResult} balance={user.balance} />}
                  {activeGame === 'mines' && <MinesGame seed={{serverSeed, clientSeed, nonce}} onPlay={setUser} onResult={handleGameResult} balance={user.balance} />}
                  {activeGame === 'plinko' && <PlinkoGame seed={{serverSeed, clientSeed, nonce}} onPlay={setUser} onResult={handleGameResult} balance={user.balance} />}
               </div>
               <div className="bg-[#0f172a] rounded-3xl border border-white/5 p-6 shadow-xl">
                  <h3 className="text-xs font-black text-slate-500 uppercase tracking-widest mb-4 flex items-center gap-2"><span className="w-2 h-2 bg-indigo-500 rounded-full"></span> Live Bets</h3>
                  <div className="overflow-x-auto"><table className="w-full text-left text-sm whitespace-nowrap">
                        <thead><tr className="text-[10px] font-bold text-slate-500 uppercase tracking-wider border-b border-white/5"><th className="pb-3 pl-4">Game</th><th className="pb-3">Result</th><th className="pb-3">Bet</th><th className="pb-3">Payout</th></tr></thead>
                        <tbody className="font-mono text-xs">{history.map((item) => (
                             <tr key={item.id} className="border-b border-white/5 hover:bg-white/[0.02]">
                                <td className="py-3 pl-4 text-slate-300 capitalize flex items-center gap-2">{item.game}</td>
                                <td className={`py-3 font-bold ${item.payout > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>{item.result?.toFixed(2) || '0.00'}x</td>
                                <td className="py-3 text-slate-400">${item.wager?.toFixed(2) || '0.00'}</td>
                                <td className={`py-3 ${item.payout > 0 ? 'text-emerald-400' : 'text-slate-600'}`}>${item.payout?.toFixed(2) || '0.00'}</td>
                             </tr>
                           ))}</tbody></table></div>
               </div>
            </div>
         </div>
      </main>

      {showFairness && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in">
           <div className="bg-[#0f172a] border border-white/10 w-full max-w-lg rounded-3xl p-8 shadow-2xl relative">
              <button onClick={() => setShowFairness(false)} className="absolute top-6 right-6 text-slate-500 hover:text-white">✕</button>
              <h2 className="text-xl font-black text-white mb-6">PROVABLY FAIR</h2>
              <div className="space-y-4">
                 <div className="space-y-1"><label className="text-[10px] font-bold text-indigo-400 uppercase">Server Seed (Hashed)</label><div className="bg-slate-950 p-3 rounded-xl border border-white/5 font-mono text-[10px] break-all text-slate-400">Wait for rotation to reveal</div></div>
                 <div className="space-y-1"><label className="text-[10px] font-bold text-slate-500 uppercase">Client Seed</label><div className="flex gap-2"><input value={clientSeed} onChange={e=>setClientSeed(e.target.value)} className="flex-1 bg-slate-950 border border-white/10 rounded-xl px-3 py-2 font-mono text-xs" /><button onClick={()=>setClientSeed(generateRandomHex().slice(0,16))} className="px-3 bg-slate-800 rounded-xl">🎲</button></div></div>
                 <div className="space-y-1"><label className="text-[10px] font-bold text-slate-500 uppercase">Nonce</label><div className="bg-slate-950 border border-white/10 rounded-xl px-3 py-2 font-mono text-xs text-slate-400">{nonce}</div></div>
                 <Button onClick={()=>{setServerSeed(generateRandomHex()); setNonce(0);}} variant="secondary" className="w-full mt-4">ROTATE SEED</Button>
              </div>
           </div>
        </div>
      )}
    </div>
  );
};

const root = ReactDOM.createRoot(document.getElementById('root')!);
root.render(<React.StrictMode><App /></React.StrictMode>);
