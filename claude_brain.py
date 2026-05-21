import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, TICK_SIZE, TICK_VALUE
from logger import logger, log_analysis

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are an institutional-grade futures trader specializing in MNQ (Micro E-mini Nasdaq).
You combine ICT (Inner Circle Trader) methodology with order flow analysis to find high probability setups.

═══════════════════════════════════════
CORE FRAMEWORK — READ EVERY ANALYSIS
═══════════════════════════════════════

STEP 1 — HIGHER TIMEFRAME BIAS
Before anything else: what is the daily and 15min structure?
- Bullish structure (HH/HL) = look for LONGS primarily
- Bearish structure (LH/LL) = look for SHORTS primarily
- Mixed = wait for clarity or take only the cleanest setups

STEP 2 — AMD CYCLE PHASE
- ACCUMULATION (Asia): range-bound, institutions building. Note the range.
- MANIPULATION (London): fake move to hunt stops. If London sweeps Asia low → expect move UP. If sweeps Asia high → expect move DOWN.
- DISTRIBUTION (NY): real institutional move. Trade WITH this, not against it.

STEP 3 — KILLZONE TIMING
Only trade during killzones:
- NY AM killzone (9:30-11am ET) — highest probability
- NY PM killzone (1:30-3:30pm ET) — second best
- Outside killzones = be very selective or wait

STEP 4 — KEY LEVELS (hierarchy)
Previous Week High/Low > Previous Day High/Low > Asia High/Low > London High/Low > Session High/Low
- Price ABOVE level = bullish, level is support
- Price BELOW level = bearish, level is resistance
- Level just swept = likely reversal incoming

STEP 5 — ICT ENTRY MODEL
Look for confluence of:
a) Fair Value Gap (FVG) — price returning to fill imbalance
b) Order Block (OB) — institutional footprint at reversal point
c) Liquidity swept — stops taken out before real move
d) VWAP — price relationship to VWAP for institutional benchmark
e) Delta confirmation — buyers/sellers winning at the level

STEP 6 — ORDER FLOW CONFIRMATION
Never enter without:
- Delta confirming direction (not diverging)
- Volume above average on entry candle
- DOM showing path of least resistance clear (when available)

═══════════════════════════════════════
TRADE RULES
═══════════════════════════════════════
SESSION: RTH only. Core hours 9:45am-11:30am and 1:30pm-3:30pm ET.
AVOID: 11:30am-1:30pm unless exceptional momentum already in play.
PRE-MARKET (9:30-9:45): Analyze only, build bias, no entries.

SCALP (1min setup):
- All 6 steps above confirmed
- Stop: 4 ticks, Target: 8-10 ticks minimum
- Only during killzone

SWING (5-15min setup):
- Steps 1-5 confirmed, momentum building
- Stop: below/above OB or FVG that triggered entry
- Target: next liquidity pool (equal highs/lows above/below)
- Trail stop on momentum

MOMENTUM OVERRIDE:
- Trade running, delta accelerating, path clear
- Move stop to breakeven
- Trail aggressively to next liquidity target
- Do NOT take profit early just because it feels good

BOTH DIRECTIONS:
- LONG when: manipulation swept sell-side (Asia/London lows), now in distribution up
- SHORT when: manipulation swept buy-side (Asia/London highs), now in distribution down
- Equal opportunity — bias from HTF structure only

RISK:
- Low confidence = NO TRADE
- 3 losses = 30 min pause
- No news within 5 min
- Max 1 MNQ contract

═══════════════════════════════════════
RESPONSE FORMAT — EXACT
═══════════════════════════════════════
DECISION: [BUY / SELL / HOLD / CLOSE]
MODE: [SCALP / SWING / MOMENTUM / NONE]
CONTRACTS: [number]
STOP_TICKS: [number]
TARGET_TICKS: [number or TRAIL]
CONFIDENCE: [LOW / MEDIUM / HIGH]
REASONING: [Walk through each step: HTF bias → AMD phase → killzone → key levels → ICT setup → order flow. Be specific about what you see and what's missing.]
"""


def analyze_market(snapshot: dict) -> dict:
    """Send market snapshot to Claude and get trading decision"""

    market_message = f"""
═══════════════════════════════════════
MNQ MARKET SNAPSHOT — {snapshot.get('time_et')} ET
═══════════════════════════════════════

TIMING & PHASE:
Session: {snapshot.get('session_phase')}
Killzone: {snapshot.get('killzone')}
AMD Phase: {snapshot.get('amd_phase')}

HIGHER TIMEFRAME BIAS:
{snapshot.get('htf_bias')}

MARKET STRUCTURE:
{snapshot.get('market_structure')}

═══════════════════════════════════════
PRICE ACTION
═══════════════════════════════════════
Current Price: {snapshot.get('last_price')}
Bid: {snapshot.get('bid')} x {snapshot.get('bid_size')}
Ask: {snapshot.get('ask')} x {snapshot.get('ask_size')}
VWAP: {snapshot.get('vwap')}
Session High: {snapshot.get('session_high')}
Session Low: {snapshot.get('session_low')}
Volume: {snapshot.get('volume')}

{snapshot.get('session_levels')}

═══════════════════════════════════════
ICT CONCEPTS
═══════════════════════════════════════
FAIR VALUE GAPS:
{snapshot.get('fair_value_gaps')}

ORDER BLOCKS:
{snapshot.get('order_blocks')}

LIQUIDITY POOLS:
{snapshot.get('liquidity_pools')}

═══════════════════════════════════════
CANDLES
═══════════════════════════════════════
{snapshot.get('candles', 'No candle data')}

═══════════════════════════════════════
ORDER FLOW
═══════════════════════════════════════
Cumulative Delta: {snapshot.get('cumulative_delta')}
Delta Last Bar: {snapshot.get('delta_last_bar')}
Large Prints: {snapshot.get('large_prints')}

DOM / LEVEL 2:
{snapshot.get('dom')}

═══════════════════════════════════════
RISK CONTEXT
═══════════════════════════════════════
Current Position: {snapshot.get('current_position')}
Daily P&L: ${snapshot.get('daily_pnl')}
Daily Loss Remaining: ${snapshot.get('daily_loss_remaining')}
Consecutive Losses: {snapshot.get('consecutive_losses')}
News Next 30min: {snapshot.get('upcoming_news')}

Analyze using the full ICT framework and make your decision.
"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": market_message}]
        )

        raw_response = response.content[0].text
        decision = parse_decision(raw_response)
        decision['raw'] = raw_response

        log_analysis(snapshot, raw_response)

        logger.info(f"Claude decision: {decision.get('decision')} | "
                   f"Confidence: {decision.get('confidence')} | "
                   f"Mode: {decision.get('mode')}")
        logger.info(f"REASONING: {decision.get('reasoning')[:200]}")

        return decision

    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return {"decision": "HOLD", "mode": "NONE", "confidence": "LOW", "raw": str(e)}


def analyze_position(snapshot: dict, position: int, entry_price: float,
                     stop_price: float, target_price: float, trade_mode: str) -> dict:
    """Fast position management — called every 10 seconds when in trade"""

    direction = "LONG" if position > 0 else "SHORT"
    current_price = snapshot.get('last_price', 0)

    if current_price and entry_price:
        unrealized_pnl = ((current_price - entry_price) / TICK_SIZE * TICK_VALUE
                         if position > 0 else
                         (entry_price - current_price) / TICK_SIZE * TICK_VALUE)
    else:
        unrealized_pnl = 0

    ticks_from_stop = abs(current_price - stop_price) / TICK_SIZE if current_price else 0
    ticks_from_target = abs(current_price - target_price) / TICK_SIZE if current_price and target_price else 0

    position_message = f"""
POSITION MANAGEMENT — {snapshot.get('time_et')} ET

POSITION: {direction} {abs(position)} MNQ @ {entry_price}
Current: {current_price} | Stop: {stop_price} | Target: {target_price}
Unrealized P&L: ${unrealized_pnl:.2f}
Ticks from stop: {ticks_from_stop:.1f} | Ticks from target: {ticks_from_target:.1f}
Mode: {trade_mode}

PRICE:
Bid: {snapshot.get('bid')} x {snapshot.get('bid_size')}
Ask: {snapshot.get('ask')} x {snapshot.get('ask_size')}
VWAP: {snapshot.get('vwap')}

RECENT CANDLES:
{snapshot.get('candles', '')[:400]}

ORDER FLOW:
Cumulative Delta: {snapshot.get('cumulative_delta')}
Delta Last Bar: {snapshot.get('delta_last_bar')}
Large Prints: {snapshot.get('large_prints')}

DOM / ORDER BOOK:
{snapshot.get('dom')}

ICT LEVELS:
{snapshot.get('fair_value_gaps', '')}
{snapshot.get('order_blocks', '')}
{snapshot.get('liquidity_pools', '')}

DECISION FORMAT:
DECISION: [HOLD / CLOSE / TRAIL]
NEW_STOP: [specific price or KEEP]
CONFIDENCE: [LOW / MEDIUM / HIGH]
REASONING: [max 2 sentences — what is delta doing, is there a wall, is momentum intact]
"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=250,
            system="""You are managing an open MNQ futures position using ICT methodology.
Be fast and decisive.
CLOSE immediately if: delta flipping hard against you, large order block or FVG acting as wall blocking profit, momentum clearly stalling with reversal candle, price sweeping a liquidity level and reversing.
TRAIL if: momentum accelerating, delta strong, next liquidity target visible and clear path ahead. Provide new stop price.
HOLD if: trade within normal noise, thesis still intact, stop not in danger.
NEVER move stop against the position. NEVER let a winner turn into a full stop out if you can trail to breakeven.""",
            messages=[{"role": "user", "content": position_message}]
        )

        raw = response.content[0].text
        result = parse_position_decision(raw)
        result['raw'] = raw
        logger.info(f"Position check: {result.get('decision')} | "
                   f"Stop: {result.get('new_stop')} | "
                   f"{result.get('reasoning', '')[:120]}")
        return result

    except Exception as e:
        logger.error(f"Position analysis error: {e}")
        return {"decision": "HOLD", "new_stop": "KEEP", "confidence": "LOW"}


def parse_decision(response_text: str) -> dict:
    decision = {
        "decision": "HOLD",
        "mode": "NONE",
        "contracts": 1,
        "stop_ticks": 4,
        "target_ticks": 8,
        "confidence": "LOW",
        "reasoning": ""
    }

    lines = response_text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith("DECISION:"):
            decision["decision"] = line.split(":", 1)[1].strip()
        elif line.startswith("MODE:"):
            decision["mode"] = line.split(":", 1)[1].strip()
        elif line.startswith("CONTRACTS:"):
            try:
                decision["contracts"] = int(line.split(":", 1)[1].strip())
            except:
                decision["contracts"] = 1
        elif line.startswith("STOP_TICKS:"):
            try:
                decision["stop_ticks"] = int(line.split(":", 1)[1].strip())
            except:
                decision["stop_ticks"] = 4
        elif line.startswith("TARGET_TICKS:"):
            val = line.split(":", 1)[1].strip()
            decision["target_ticks"] = val if val == "TRAIL" else int(val) if val.isdigit() else 8
        elif line.startswith("CONFIDENCE:"):
            decision["confidence"] = line.split(":", 1)[1].strip()
        elif line.startswith("REASONING:"):
            decision["reasoning"] = line.split(":", 1)[1].strip()

    return decision


def parse_position_decision(response_text: str) -> dict:
    result = {
        "decision": "HOLD",
        "new_stop": "KEEP",
        "confidence": "LOW",
        "reasoning": ""
    }

    lines = response_text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith("DECISION:"):
            result["decision"] = line.split(":", 1)[1].strip()
        elif line.startswith("NEW_STOP:"):
            result["new_stop"] = line.split(":", 1)[1].strip()
        elif line.startswith("CONFIDENCE:"):
            result["confidence"] = line.split(":", 1)[1].strip()
        elif line.startswith("REASONING:"):
            result["reasoning"] = line.split(":", 1)[1].strip()

    return result


print("Claude brain loaded successfully")


def analyze_premarket(snapshot: dict, memory_context: str) -> dict:
    """Pre-market analysis with full memory context — runs 9:30-9:45am"""

    premarket_message = f"""
═══════════════════════════════════════
PRE-MARKET ANALYSIS — {snapshot.get('time_et')} ET
Build your bias and game plan for today's RTH session.
═══════════════════════════════════════

{memory_context}

TODAY'S CURRENT DATA:
Session: {snapshot.get('session_phase')}
AMD Phase: {snapshot.get('amd_phase')}

HTF BIAS:
{snapshot.get('htf_bias')}

MARKET STRUCTURE:
{snapshot.get('market_structure')}

PRICE:
Current: {snapshot.get('last_price')}
Session High: {snapshot.get('session_high')}
Session Low: {snapshot.get('session_low')}

{snapshot.get('session_levels')}

ICT LEVELS:
Fair Value Gaps: {snapshot.get('fair_value_gaps')}
Order Blocks: {snapshot.get('order_blocks')}
Liquidity Pools: {snapshot.get('liquidity_pools')}

CANDLES:
{snapshot.get('candles', '')[:600]}

ORDER FLOW:
Cumulative Delta: {snapshot.get('cumulative_delta')}
Delta Last Bar: {snapshot.get('delta_last_bar')}

Based on all of this build your game plan:
1. What is the HTF bias today?
2. Did London manipulate Asia levels? Which direction?
3. What is the most likely distribution direction for NY session?
4. What are the key levels to watch?
5. What setups are you looking for?
6. What would make you NOT trade today?

Respond in EXACT format:
DECISION: HOLD
MODE: NONE
CONTRACTS: 0
STOP_TICKS: 0
TARGET_TICKS: 0
CONFIDENCE: HIGH
REASONING: [Full game plan — be specific and detailed]
"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": premarket_message}]
        )

        raw_response = response.content[0].text
        decision = parse_decision(raw_response)
        decision['raw'] = raw_response

        log_analysis(snapshot, f"PRE-MARKET GAME PLAN:\n{raw_response}")
        logger.info("=" * 50)
        logger.info("PRE-MARKET GAME PLAN:")
        logger.info(raw_response)
        logger.info("=" * 50)

        return decision

    except Exception as e:
        logger.error(f"Pre-market analysis error: {e}")
        return {"decision": "HOLD", "mode": "NONE", "confidence": "LOW", "raw": str(e)}
