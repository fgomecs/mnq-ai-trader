"""
Claude Brain — MNQ AI Trader
=============================
Session 1 fixes from audit:
  P0.5 / P1.9 — Model split + prompt caching:
                 entry analysis = Opus 4.7 with cached system prompt
                 position mgmt  = Sonnet 4.6 with cached system prompt
                 watchlist      = Sonnet 4.6 with cached system prompt
  P1.7 — parse_decision demotes BUY/SELL to HOLD when stop_price <= 0,
         surfacing Claude parse failures instead of silently aborting at
         the executor's sanity check.
  P2.8 — reset_session_state() called by main at EOD; module globals
         no longer carry across days.

Pricing note: cached input tokens cost ~10% of standard. The static system
prompt (~2500 tokens) caches once per 5-min TTL window; the perf/watchlist
block (~800 tokens) caches too. Dynamic snapshot stays uncached.
"""

import json
import time
import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_ENTRY_MODEL,
    CLAUDE_POSITION_MODEL,
    CLAUDE_STRUCTURE_MODEL,
    CLAUDE_USE_CACHING,
    MIN_THESIS_PROBABILITY,
    TICK_SIZE, TICK_VALUE,
    FEATURE_BIDIRECTIONAL, FEATURE_BIAS_DECAY, FEATURE_ORB_BIAS,
    FEATURE_OFI, FEATURE_MTF_SCORE, FEATURE_THESIS_GATE,
    FEATURE_NEWS_GATE, FEATURE_DEAD_ZONE, FEATURE_EARLY_EXIT,
    FEATURE_LEARNING_INJECT,
    VERSION,
    PRE_FILTER_SIGNAL_THRESHOLD, COUNTER_TREND_SIGNAL_THRESHOLD,
    SKIP_CACHE_PRICE_DELTA, SKIP_CACHE_MAX_AGE_SECS,
    SKIP_CACHE_WATCHLIST_AGE_SECS, SKIP_LOG_EVERY_N,
    OR_THESIS_INVALIDATION_POINTS,
    DOM_BUY_PRESSURE_BULL_THRESHOLD, DOM_SELL_PRESSURE_BEAR_THRESHOLD,
    WATCHLIST_REFRESH_SECS,
)
from logger import logger
from data_recorder import recorder as _recorder

try:
    from strategy_stats import generate_performance_context as _get_perf_ctx
except Exception:
    _get_perf_ctx = None   # optional module — graceful degradation

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ─── System prompts ────────────────────────────────────────

SYSTEM_PROMPT = """You are an institutional-grade MNQ futures trader using ICT methodology.
You trade structure, not rules. You follow price — not predetermined bias.

═══════════════════════════════════════
STEP 1 — READ THE WATCHLIST BIAS
═══════════════════════════════════════
The watchlist provides:
  "bias": LONG_PREFERRED / SHORT_PREFERRED / NEUTRAL / NO_TRADE
  "bias_strength": 0-100 (how confident the watchlist is)
  "bias_invalidated": true/false

This is a PREFERENCE, not a law. You override it when current structure disagrees.

LONG_PREFERRED  → favor longs, but if structure is clearly bearish, SELL is allowed
SHORT_PREFERRED → favor shorts, but if structure is clearly bullish, BUY is allowed
NEUTRAL         → trade both sides freely, structure decides
NO_TRADE        → HOLD only, no entries

If "bias_invalidated" is true → treat as NEUTRAL regardless of bias field.

═══════════════════════════════════════
STEP 2 — HTF + CURRENT STRUCTURE ALIGNMENT
═══════════════════════════════════════
Daily HTF: 3 consecutive higher closes = BULLISH. Lower highs/lows = BEARISH.
15min: HH/HL = bullish. LH/LL = bearish.

MTF alignment overrides OR direction when they disagree:
  BULLISH_ALIGNED = all TFs bullish → strong long bias
  BEARISH_ALIGNED = all TFs bearish → strong short bias
  CONFLICTED = TFs disagree → HOLD (no entries, ever)
  PARTIAL_BULL/BEAR = 2/3 agree → trade cautiously in that direction

If MTF is BEARISH_ALIGNED, do NOT enter long regardless of OR direction.
If MTF is BULLISH_ALIGNED, do NOT enter short regardless of OR direction.

═══════════════════════════════════════
STEP 3 — OPENING RANGE CONTEXT
═══════════════════════════════════════
The OR is the first 15 minutes of RTH (9:30-9:45 ET) — three 5-min bars. High = range high, Low = range low. This filters out the opening manipulation spike that typically fakes direction in the first 5 minutes.
It is a STARTING POINT, not a prison.

OR direction matters most: within the first 90 minutes, and when price is
near the OR level. It becomes less relevant as the session ages and structure evolves.

ORB ENTRY MODEL (when OR thesis is valid):
  Stage 1: Confirmed CLOSE outside OR range
  Stage 2: Price pulls back toward OR level
  Stage 3: 1-min close above pullback low (long) or below pullback high (short)
  Stop: just below pullback low (long) / just above pullback high (short)

or_entry_zone_active = True → valid ORB trigger exists now
or_pullback_in_progress = True → not yet, wait

OR THESIS IS EXPIRED/INVALID when:
  - More than 90 minutes since OR AND price is far from OR level
  - Price has moved significantly against OR direction (50+ points)
  - bias_invalidated = true in the watchlist
  → In these cases, ignore OR direction and trade structure

═══════════════════════════════════════
STEP 4 — NEWS DANGER ZONE
═══════════════════════════════════════
If news_danger_zone is True → DO NOT ENTER under any circumstances.

═══════════════════════════════════════
STEP 4.5 — CANDLESTICK CONFIRMATION
═══════════════════════════════════════
A bullish/bearish engulfing or hammer/shooting star at an OB or FVG level adds significant confluence. Inside bar breakouts at key levels are the cleanest ICT entry triggers.
- Bullish engulfing or hammer AT an OB/FVG → strong long confluence, can substitute for weak CHoCH
- Bearish engulfing or shooting star AT an OB/FVG → strong short confluence
- Inside bar breakout: price breaks above prior bar high (1m) → long trigger; breaks below prior bar low → short trigger
- Pattern labeled [OR aligned] means it reinforces the opening range thesis
- Pattern alone (no OB/FVG nearby) is context, not entry justification

═══════════════════════════════════════
STEP 5 — CHOCH + DELTA CONFIRMATION
═══════════════════════════════════════
For LONG: CHoCH must be BULLISH. Delta trend should be positive or neutral.
For SHORT: CHoCH must be BEARISH. Delta trend should be negative or neutral.
If CHoCH and delta both disagree with your intended direction → HOLD.

═══════════════════════════════════════
STEP 6 — KILL ZONES
═══════════════════════════════════════
★★ NY AM (8:30-11:00 ET) — PRIME for both directions
★  NY PM (1:30-4:00 ET) — GOOD for both directions
✗  Dead (11:00-1:30 ET) — AVOID unless 8+ confluence

═══════════════════════════════════════
STEP 7 — STOP AND TARGET
═══════════════════════════════════════
LONG stop: just below pullback low or nearest BULL OB/FVG bottom
SHORT stop: just above pullback high or nearest BEAR OB/FVG top
Never use fixed tick stops. Always structure-based.
STOP_PRICE is MANDATORY. If you cannot find a structural stop, output HOLD.

TARGET: next key liquidity level (session H/L, OR level, prev day H/L, FVG)
R:R minimum 2:1. Prefer 3:1+.

═══════════════════════════════════════
DIRECTION DECISION SUMMARY
═══════════════════════════════════════
BUY when ALL of: CHoCH bullish + MTF not bearish + delta not strongly negative
               + structural support below + OR thesis valid OR bias NEUTRAL/LONG_PREFERRED
SELL when ALL of: CHoCH bearish + MTF not bullish + delta not strongly positive
                + structural resistance above + OR thesis invalid OR bias NEUTRAL/SHORT_PREFERRED
HOLD when: conflicting signals, no structure, in dead zone, news danger

═══════════════════════════════════════
THESIS PROBABILITY — V4.0
═══════════════════════════════════════
After deciding BUY/SELL/HOLD, assign a THESIS_PROBABILITY (0-100).
This is your confidence that the trade will reach TARGET_1 before hitting STOP_PRICE.

Calibration guide:
  90-100: Everything aligned — CHoCH confirmed, MTF bullish, OFI accelerating,
          cluster magnet target visible, iceberg support below, clean news window.
          Rare. Only when every signal points the same way.
  75-89:  Strong setup — most signals aligned, one minor conflict.
          This is the normal entry range. Tradeable.
  60-74:  Decent setup — 2-3 signals conflicting. Marginal.
          Reduce conviction. Only enter in prime kill zones.
  40-59:  Weak — too many conflicts. HOLD unless score is still above minimum.
  0-39:   No trade. Force HOLD regardless of other signals.

Be honest. If your reasoning includes "but..." or "however..." that drops probability.
Iceberg support below: +5-10. OFI accelerating: +5-10. Spoof on opposite side: +5.
MTF conflict: -10-15. Delta diverging from price: -10. Near news event: -5 to -20.

The bot will only enter if THESIS_PROBABILITY >= threshold (default 70).
Your probability directly controls trade frequency — be calibrated, not optimistic.

═══════════════════════════════════════
RESPONSE FORMAT — EXACT, NO EXCEPTIONS
═══════════════════════════════════════
DECISION: [BUY / SELL / HOLD]
CONFIDENCE: [LOW / MEDIUM / HIGH]
THESIS_PROBABILITY: [0-100]
MODE: [SCALP / SWING / NONE]
STOP_PRICE: [actual price level]
TARGET_1: [first key level price]
TARGET_2: [second key level price or TRAIL]
STRATEGY: [ORB_BREAKOUT / ORB_PULLBACK / ICT_SWEEP_REVERSAL / VWAP_RECLAIM / OB_BOUNCE / FVG_FILL / CHOCH_ENTRY / BEAR_SWEEP / COMBINED]
CONFLUENCE: [factors e.g. OR_BULL + SWEEP + CHOCH_BEAR + BELOW_VWAP + DELTA_NEG + NY_PM_KZ + MTF_PARTIAL_BEAR]
CONFLUENCE_SCORE: [1-10]
REASONING: [3 sentences: what structure shows, why entering NOW, what invalidates thesis]
"""

POSITION_SYSTEM = """You are an ICT-trained position manager for MNQ futures.
Use market structure — not mechanical rules.

TRAIL RULES:
- New high + pullback → stop below that pullback low
- +50 ticks profit → stop to entry minimum
- +100 ticks → stop to entry + 40 ticks minimum
- Give NEW_STOP as actual price level

CLOSE WHEN:
- Price at resistance/support with rejection + delta flip
- Lower high forming below previous swing high (longs)
- Delta diverging from price
- Grinding 10+ minutes with no progress
- +$40 trade giving it all back

NEVER let a +$40 trade become a full stop out.

RESPONSE FORMAT — EXACT:
DECISION: [HOLD / CLOSE / TRAIL]
NEW_STOP: [price level]
CONFIDENCE: [LOW / MEDIUM / HIGH]
THESIS_STATUS: [INTACT / WEAKENING / INVALIDATED]
REASONING: [2 sentences — specific level and current structure]
"""

STRUCTURE_SYSTEM = """You are an institutional-grade ICT market analyst for MNQ futures.
Your job: read current market structure honestly and produce an actionable game plan.
You are NOT biased by the opening range direction. You follow STRUCTURE, not rules.

═══════════════════════════════════════
BIAS FRAMEWORK (Version 3.0)
═══════════════════════════════════════
The opening range (OR) gives a STARTING bias — not a permanent law.

OR direction = institutional intent at the OPEN. It matters most in the first 90 minutes.
After 90 minutes, current structure, HTF alignment, and price action override OR intent.

BIAS DECISION TREE:
1. Is it within 90 min of the OR? AND is price respecting OR direction?
   → Use OR direction as primary bias (LONG_PREFERRED or SHORT_PREFERRED)
2. Has price broken the OR thesis? (see invalidation rules below)
   → Set bias to NEUTRAL. Identify setups in BOTH directions.
3. Is HTF strongly aligned in one direction AND current structure confirms?
   → Set bias to that direction regardless of OR.
4. Is structure choppy, MTF conflicted, or no clear setup?
   → Set bias to NEUTRAL or NO_TRADE.

OR THESIS INVALIDATION (set bias to NEUTRAL when ANY of these occur):
  - Price has moved MORE than 80 points AGAINST OR direction from the OR level
  - More than 90 minutes since OR AND price is below VWAP on a BULL day
    (or above VWAP on a BEAR day) with CHoCH in the wrong direction
  - MTF alignment has flipped FULLY against OR direction (all 3 TFs disagree)
  - The OR pullback low (for bulls) has been broken by > 20 points

BIAS VALUES:
  "LONG_PREFERRED"  — structure favors longs, but shorts allowed if signal is very strong
  "SHORT_PREFERRED" — structure favors shorts, but longs allowed if signal is very strong
  "NEUTRAL"         — no directional bias, trade BOTH sides on structure triggers
  "NO_TRADE"        — no setup, chop, or dangerous conditions

═══════════════════════════════════════
DUAL-SIDED ANALYSIS (always provide both)
═══════════════════════════════════════
Regardless of bias, identify the BEST bull setup AND the BEST bear setup currently visible.
The entry Opus model will decide which side to take based on real-time conditions.
You are the game plan writer — provide the full picture.

For the PRIMARY side (bias direction), give a detailed setup.
For the SECONDARY side (opposite direction), give the key level and trigger only.

═══════════════════════════════════════
JSON OUTPUT FORMAT
═══════════════════════════════════════
Respond ONLY with valid JSON, no preamble, no markdown fences.
ALL string values on a single line. No embedded newlines. Plain ASCII quotes only.
Numbers: plain numerics (no commas, no units). No trailing commas. No comments.

{
  "bias": "LONG_PREFERRED | SHORT_PREFERRED | NEUTRAL | NO_TRADE",
  "bias_strength": 0-100,
  "bias_invalidated": true/false,
  "bias_invalidation_reason": "why the OR thesis failed, or empty string",
  "or_direction": "BULL | BEAR | DOJI | PENDING",
  "setup_watching": "ORB_PULLBACK | ICT_SWEEP | VWAP_RECLAIM | OB_BOUNCE | NONE",
  "entry_trigger": "plain text — exact trigger for primary side",
  "entry_zone": [low_price, high_price],
  "stop_price": price_level,
  "target_1": price_level,
  "target_2": price_level,
  "invalidation": "plain text — what cancels the primary setup",
  "bear_setup": "plain text — best short entry trigger and key level, or NONE",
  "bear_entry_zone": [low_price, high_price],
  "bear_stop": price_level,
  "bear_target_1": price_level,
  "key_levels_above": [price1, price2, price3],
  "key_levels_below": [price1, price2, price3],
  "avoid_until": "time or condition, or empty string",
  "confidence": "LOW | MEDIUM | HIGH",
  "notes": "1-2 sentences of context"
}
"""


# ─── Cache-aware API helper ────────────────────────────────

def _build_system(prompt: str) -> list | str:
    """
    Build the system parameter. With caching enabled, returns a list with
    cache_control on the (single) text block. Without caching, returns a
    plain string for max compatibility.
    """
    if CLAUDE_USE_CACHING:
        return [{
            "type": "text",
            "text": prompt,
            "cache_control": {"type": "ephemeral"},
        }]
    return prompt


def _build_user_content(static_blocks: list[str], dynamic_block: str) -> list:
    """
    Build a user message content list with cache_control on the last static
    block (so everything up to that point caches), and the dynamic block
    uncached at the end.
    """
    if not CLAUDE_USE_CACHING:
        # Plain text, all concatenated
        return "\n\n".join([b for b in static_blocks if b] + [dynamic_block])

    content = []
    static_blocks = [b for b in static_blocks if b]
    if static_blocks:
        # All-but-last static blocks: plain text
        for b in static_blocks[:-1]:
            content.append({"type": "text", "text": b})
        # Last static block carries the cache marker
        content.append({
            "type": "text",
            "text": static_blocks[-1],
            "cache_control": {"type": "ephemeral"},
        })
    # Dynamic content — uncached, last
    content.append({"type": "text", "text": dynamic_block})
    return content


# ─── Cost tracking ─────────────────────────────────────────
# Anthropic public pricing (USD per million tokens) as of 2026-05.
# input | cache_write (1.25×) | cache_read (0.1×) | output
_MODEL_PRICING = {
    "claude-opus-4-7":     {"in": 15.00, "cw": 18.75, "cr": 1.50, "out": 75.00},
    "claude-opus-4-6":     {"in": 15.00, "cw": 18.75, "cr": 1.50, "out": 75.00},
    "claude-sonnet-4-6":   {"in":  3.00, "cw":  3.75, "cr": 0.30, "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 1.00, "cw": 1.25, "cr": 0.10, "out": 5.00},
}
_DEFAULT_PRICING = {"in": 15.00, "cw": 18.75, "cr": 1.50, "out": 75.00}

# Running totals across the session
_cost_tracker = {
    "total_usd":  0.0,
    "by_model":   {},        # {model_str: {"calls": N, "usd": X.XX}}
    "by_purpose": {},        # {"entry": {...}, "position": {...}, "watchlist": {...}, "eod": {...}}
    "skipped_calls": 0,      # A.1: how many Opus calls we avoided
}


def _log_cache_usage(resp, model: str = "", purpose: str = "") -> dict:
    """
    Log cache hit rate AND compute call cost. Returns a dict with cost details.
    """
    info = {"usd": 0.0, "cached": 0, "written": 0, "fresh": 0, "out": 0, "hit_rate": 0.0}
    try:
        u = resp.usage
        cached  = getattr(u, "cache_read_input_tokens", 0)     or 0
        written = getattr(u, "cache_creation_input_tokens", 0) or 0
        fresh   = getattr(u, "input_tokens", 0)                or 0
        out     = getattr(u, "output_tokens", 0)               or 0

        price = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
        cost = (
            cached  * price["cr"] / 1_000_000 +
            written * price["cw"] / 1_000_000 +
            fresh   * price["in"] / 1_000_000 +
            out     * price["out"] / 1_000_000
        )
        info.update({
            "usd": cost, "cached": cached, "written": written,
            "fresh": fresh, "out": out,
            "hit_rate": (cached / max(cached + fresh, 1)) * 100,
        })

        # Update running totals
        _cost_tracker["total_usd"] += cost
        bm = _cost_tracker["by_model"].setdefault(model or "?", {"calls": 0, "usd": 0.0})
        bm["calls"] += 1
        bm["usd"]   += cost
        if purpose:
            bp = _cost_tracker["by_purpose"].setdefault(purpose, {"calls": 0, "usd": 0.0})
            bp["calls"] += 1
            bp["usd"]   += cost

        if cached or written or out > 100:
            logger.info(
                f"Cache: read={cached} write={written} fresh={fresh} out={out} "
                f"hit_rate={info['hit_rate']:.0f}% cost=${cost:.4f} "
                f"session_total=${_cost_tracker['total_usd']:.2f}"
            )
    except Exception as e:
        logger.debug(f"Cache stats error: {e}")
    return info


def get_cost_summary() -> dict:
    """Return a snapshot of session cost stats for dashboard/EOD reporting."""
    return {
        "total_usd":     round(_cost_tracker["total_usd"], 4),
        "skipped_calls": _cost_tracker["skipped_calls"],
        "by_model":      {k: {"calls": v["calls"], "usd": round(v["usd"], 4)}
                          for k, v in _cost_tracker["by_model"].items()},
        "by_purpose":    {k: {"calls": v["calls"], "usd": round(v["usd"], 4)}
                          for k, v in _cost_tracker["by_purpose"].items()},
    }


def reset_cost_tracker() -> None:
    """Reset cost tracker at EOD."""
    global _cost_tracker
    _cost_tracker = {
        "total_usd":  0.0,
        "by_model":   {},
        "by_purpose": {},
        "skipped_calls": 0,
    }


def _tolerant_json_parse(raw: str) -> dict:
    """
    Parse JSON that may contain bare newlines/tabs inside string values.
    Claude (Sonnet especially) occasionally returns multi-line strings
    inside JSON, which strict json.loads rejects as "Unterminated string".

    Strategy:
      1. Strip markdown fences if present
      2. Try strict parse first (happy path)
      3. On failure, walk the string and escape any control characters
         that appear inside a string literal, then retry
      4. Final fallback: regex-extract just the {...} braces with greedy
         match, escape, then parse
    """
    # Strip fences and surrounding whitespace
    s = raw.replace("```json", "").replace("```", "").strip()

    # Happy path
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Walk and escape control chars inside string literals
    out = []
    in_string = False
    escape_next = False
    for ch in s:
        if escape_next:
            out.append(ch)
            escape_next = False
            continue
        if ch == "\\":
            out.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch in "\n\r\t":
            # Escape bare control chars that appear inside string values
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
            continue
        out.append(ch)
    cleaned = "".join(out)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Last resort — try to find the outermost { ... } and parse just that
        import re
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise e


# ─── Session Watchlist ────────────────────────────────────

_session_watchlist: dict = {}
_watchlist_time:    float = 0.0


def update_watchlist(snapshot: dict) -> dict:
    """
    Called every 5 minutes. Produces a structured game plan Claude uses
    as context for entry calls. Uses Sonnet (cheap, structured JSON output).
    """
    global _session_watchlist, _watchlist_time
    if time.time() - _watchlist_time < WATCHLIST_REFRESH_SECS and _session_watchlist:
        return _session_watchlist

    dynamic = f"""
MARKET STRUCTURE SNAPSHOT — {snapshot.get('time_et', 'N/A')} ET

OR Direction: {snapshot.get('or_direction', 'PENDING')}
OR High: {snapshot.get('or_high')} | OR Low: {snapshot.get('or_low')}
OR Relative Volume: {snapshot.get('or_relative_volume', 'N/A')}
OR Broken Up: {snapshot.get('or_broken_up')} | Broken Down: {snapshot.get('or_broken_down')}
OR Entry Zone Active: {snapshot.get('or_entry_zone_active')}
OR Pullback Low: {snapshot.get('or_pullback_low')}

MTF Alignment: {snapshot.get('mtf_alignment', 'N/A')}
HTF Bias: {snapshot.get('htf_bias', 'N/A')}
Structure: {snapshot.get('market_structure', 'N/A')}

Price: {snapshot.get('last_price')} | VWAP: {snapshot.get('vwap')}
Session H: {snapshot.get('session_high')} | L: {snapshot.get('session_low')}

{snapshot.get('session_levels', '')}

FVGs: {snapshot.get('fair_value_gaps', '')}
OBs: {snapshot.get('order_blocks', '')}
Liq: {snapshot.get('liquidity_pools', '')}

CHoCH: {snapshot.get('choch', '')}
Delta trend: {snapshot.get('delta_trend', '')}
Kill zone: {snapshot.get('killzone', '')}

Produce the Watchlist JSON for this session.
"""

    try:
        resp = client.messages.create(
            model=CLAUDE_STRUCTURE_MODEL,
            max_tokens=1024,
            system=_build_system(STRUCTURE_SYSTEM),
            messages=[{"role": "user", "content": dynamic}],
        )
        _log_cache_usage(resp, model=CLAUDE_STRUCTURE_MODEL, purpose="watchlist")

        # Detect output truncation — if stop_reason is "max_tokens", the JSON
        # is definitely incomplete and any parse will fail. Log loudly.
        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason == "max_tokens":
            logger.warning(
                f"Watchlist response hit max_tokens limit — output truncated. "
                f"Increase max_tokens or shorten the prompt."
            )

        raw = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "text") == "text"
        )
        watchlist = _tolerant_json_parse(raw)

        # ── V3.0 — Bias validation, decay, and invalidation ──
        # Replace hard OR-direction override with structure-aware logic.
        # The watchlist Sonnet is now trusted to set its own bias, but we
        # validate it, apply structural decay rules, and force NEUTRAL when
        # the OR thesis is structurally broken.

        or_dir    = snapshot.get("or_direction")
        or_high   = snapshot.get("or_high", 0) or 0
        or_low    = snapshot.get("or_low",  0) or 0
        price     = snapshot.get("last_price", 0) or 0
        vwap      = snapshot.get("vwap", 0) or 0
        mtf       = snapshot.get("mtf_alignment", "")
        choch     = snapshot.get("choch", "")
        mins_since_or = snapshot.get("mins_since_or", 999)

        current_bias = watchlist.get("bias", "NEUTRAL")

        # Rule 1 — DOJI OR = no trade, non-negotiable
        if or_dir == "DOJI":
            watchlist["bias"] = "NO_TRADE"
            watchlist["bias_invalidated"] = True
            watchlist["bias_invalidation_reason"] = "Doji OR — no institutional bias established"

        # Rule 2 — Full MTF disagreement overrides OR entirely
        elif "BEARISH_ALIGNED" in mtf and current_bias == "LONG_PREFERRED":
            watchlist["bias"] = "NEUTRAL"
            watchlist["bias_invalidated"] = True
            watchlist["bias_invalidation_reason"] = "MTF fully bearish aligned — OR long bias overridden by structure"
            logger.info("V3.0 bias override: LONG_PREFERRED → NEUTRAL (MTF fully bearish)")

        elif "BULLISH_ALIGNED" in mtf and current_bias == "SHORT_PREFERRED":
            watchlist["bias"] = "NEUTRAL"
            watchlist["bias_invalidated"] = True
            watchlist["bias_invalidation_reason"] = "MTF fully bullish aligned — OR short bias overridden by structure"
            logger.info("V3.0 bias override: SHORT_PREFERRED → NEUTRAL (MTF fully bullish)")

        # Rule 3 — Structural decay: BULL day, but price, VWAP, and CHoCH are bearish
        # After 90 min the OR is stale — if every structural signal disagrees, flip to NEUTRAL
        elif (or_dir == "BULL"
              and current_bias == "LONG_PREFERRED"
              and mins_since_or >= 90
              and price < vwap
              and "BEARISH" in choch
              and ("PARTIAL_BEAR" in mtf or "BEARISH" in mtf)):
            watchlist["bias"] = "NEUTRAL"
            watchlist["bias_invalidated"] = True
            watchlist["bias_invalidation_reason"] = (
                f"OR BULL thesis expired after {mins_since_or:.0f}min — "
                f"price below VWAP, CHoCH bearish, MTF partial bear"
            )
            logger.info(f"V3.0 bias decay: LONG_PREFERRED → NEUTRAL (OR expired + structure bearish, {mins_since_or:.0f}min elapsed)")

        elif (or_dir == "BEAR"
              and current_bias == "SHORT_PREFERRED"
              and mins_since_or >= 90
              and price > vwap
              and "BULLISH" in choch
              and ("PARTIAL_BULL" in mtf or "BULLISH" in mtf)):
            watchlist["bias"] = "NEUTRAL"
            watchlist["bias_invalidated"] = True
            watchlist["bias_invalidation_reason"] = (
                f"OR BEAR thesis expired after {mins_since_or:.0f}min — "
                f"price above VWAP, CHoCH bullish, MTF partial bull"
            )
            logger.info(f"V3.0 bias decay: SHORT_PREFERRED → NEUTRAL (OR expired + structure bullish, {mins_since_or:.0f}min elapsed)")

        # Rule 4 — Large adverse move against OR invalidates the thesis
        elif or_dir == "BULL" and or_high > 0 and price < (or_high - OR_THESIS_INVALIDATION_POINTS):
            if current_bias == "LONG_PREFERRED":
                watchlist["bias"] = "NEUTRAL"
                watchlist["bias_invalidated"] = True
                watchlist["bias_invalidation_reason"] = (
                    f"Price {price} is {or_high - price:.0f}pts below OR high {or_high} — OR bull thesis failed"
                )
                logger.info(f"V3.0 bias invalidated: LONG_PREFERRED → NEUTRAL (price {or_high - price:.0f}pts below OR high)")

        elif or_dir == "BEAR" and or_low > 0 and price > (or_low + OR_THESIS_INVALIDATION_POINTS):
            if current_bias == "SHORT_PREFERRED":
                watchlist["bias"] = "NEUTRAL"
                watchlist["bias_invalidated"] = True
                watchlist["bias_invalidation_reason"] = (
                    f"Price {price} is {price - or_low:.0f}pts above OR low {or_low} — OR bear thesis failed"
                )
                logger.info(f"V3.0 bias invalidated: SHORT_PREFERRED → NEUTRAL (price {price - or_low:.0f}pts above OR low)")

        # Rule 5 — Map legacy LONG_ONLY/SHORT_ONLY to new preferred values
        # (In case Sonnet still outputs the old field values)
        if watchlist.get("bias") == "LONG_ONLY":
            watchlist["bias"] = "LONG_PREFERRED"
        elif watchlist.get("bias") == "SHORT_ONLY":
            watchlist["bias"] = "SHORT_PREFERRED"

        # Ensure bias_invalidated exists
        if "bias_invalidated" not in watchlist:
            watchlist["bias_invalidated"] = False

        _session_watchlist = watchlist
        _watchlist_time    = time.time()
        logger.info(
            f"Watchlist updated — bias:{watchlist.get('bias')} "
            f"setup:{watchlist.get('setup_watching')} "
            f"trigger:{watchlist.get('entry_trigger','')[:60]}"
        )
        return watchlist
    except Exception as e:
        # Log a snippet of the raw response so failures can be diagnosed.
        raw_tail = ""
        try:
            raw_tail = raw[-200:] if raw else ""
        except (NameError, UnboundLocalError):
            pass
        logger.warning(f"Watchlist update failed: {e} | raw tail: ...{raw_tail}")
        return _session_watchlist


def get_watchlist() -> dict:
    return _session_watchlist


# ─── Session context ──────────────────────────────────────

_session_context: dict = {
    "or_direction":          None,
    "or_high":               None,
    "or_pullback_low":       None,
    "last_decision":         "HOLD",
    "last_decision_reason":  "",
    "last_decision_time":    "",
    "setups_passed":         [],
    "in_entry_zone":         False,
    "bars_consolidating":    0,
    "trades_today":          0,
    "consecutive_holds":     0,
}


def update_session_context(snapshot: dict, decision: str, reasoning: str) -> None:
    """Update session context after every Claude call."""
    global _session_context
    now_str = snapshot.get("time_et", "")
    _session_context["or_direction"]     = snapshot.get("or_direction")
    _session_context["or_high"]          = snapshot.get("or_high")
    _session_context["or_pullback_low"]  = snapshot.get("or_pullback_low")
    _session_context["in_entry_zone"]    = snapshot.get("or_entry_zone_active", False)
    _session_context["last_decision"]    = decision
    _session_context["last_decision_reason"] = reasoning[:150] if reasoning else ""
    _session_context["last_decision_time"]   = now_str

    if decision == "HOLD":
        _session_context["consecutive_holds"] = _session_context.get("consecutive_holds", 0) + 1
        if reasoning:
            passed = _session_context.get("setups_passed", [])
            passed = ([f"{now_str} — {reasoning[:80]}"] + passed)[:5]
            _session_context["setups_passed"] = passed
    else:
        _session_context["consecutive_holds"] = 0


def reset_session_state() -> None:
    """
    P2.8 — Wipe per-session module globals at EOD so tomorrow doesn't start
    with yesterday's context.
    """
    global _session_watchlist, _watchlist_time, _session_context, _last_entry_call
    _session_watchlist = {}
    _watchlist_time    = 0.0
    _session_context   = {
        "or_direction":          None,
        "or_high":               None,
        "or_pullback_low":       None,
        "last_decision":         "HOLD",
        "last_decision_reason":  "",
        "last_decision_time":    "",
        "setups_passed":         [],
        "in_entry_zone":         False,
        "bars_consolidating":    0,
        "trades_today":          0,
        "consecutive_holds":     0,
    }
    # A.1 — clear skip-cache too
    _last_entry_call = {
        "ts": 0.0, "price": 0.0, "last_bar_ts": None,
        "watchlist_ts": 0.0, "decision": None,
    }
    # A.3 — reset cost tracker
    reset_cost_tracker()
    logger.info("Session state reset — watchlist + context + cost tracker cleared")


def _format_session_context_static() -> str:
    """
    STATIC portion of session context — fields that are stable for at least
    several minutes. Goes in the cached prompt block.

    A.2 — Previously _format_session_context() included consecutive_holds and
    last_decision_time which change every call, busting prompt cache. Split
    fixed: stable bits cache, volatile bits go in dynamic block.
    """
    ctx = _session_context
    lines = ["\n═══ SESSION CONTEXT (stable) ═══"]
    if ctx.get("or_direction"):
        lines.append(f"OR Direction: {ctx['or_direction']} | OR High: {ctx.get('or_high')}")
    if ctx.get("or_pullback_low"):
        lines.append(f"OR pullback low: {ctx['or_pullback_low']} — stop anchor for next trade")
    lines.append("═══════════════════════════════════════")
    return "\n".join(lines)


def _format_session_context_dynamic() -> str:
    """
    DYNAMIC portion of session context — volatile fields that change every
    call (consecutive_holds increments, last_decision_time updates each scan).
    Goes in the UNCACHED dynamic block.
    """
    ctx = _session_context
    lines = []
    if ctx.get("last_decision") and ctx.get("last_decision_time"):
        lines.append(
            f"Last decision: {ctx['last_decision']} at {ctx['last_decision_time']} — "
            f"{ctx.get('last_decision_reason','')}"
        )
    if ctx.get("setups_passed"):
        lines.append("Recent HOLDs:")
        for s in ctx["setups_passed"][:3]:
            lines.append(f"  • {s}")
    if ctx.get("consecutive_holds", 0) > 3:
        lines.append(f"⚠️ {ctx['consecutive_holds']} consecutive HOLDs — be patient, wait for the trigger")
    if ctx.get("in_entry_zone"):
        lines.append("★ IN ENTRY ZONE — OR pullback complete, next valid 1-min close triggers entry")
    return "\n".join(lines) if lines else ""


# Keep the old name for backwards compat with any external callers
def _format_session_context() -> str:
    return _format_session_context_static() + "\n" + _format_session_context_dynamic()


def _format_watchlist_context() -> str:
    """Format current watchlist for Claude prompt injection. V3.0 — includes both sides."""
    wl = _session_watchlist
    if not wl:
        return ""
    lines = ["\n═══ ACTIVE WATCHLIST (game plan — V3.0 dual-sided) ═══"]

    # Bias with strength and invalidation status
    bias = wl.get("bias", "UNKNOWN")
    strength = wl.get("bias_strength", "?")
    invalidated = wl.get("bias_invalidated", False)
    inv_reason = wl.get("bias_invalidation_reason", "")

    if invalidated:
        lines.append(f"Bias: {bias} (INVALIDATED — {inv_reason})")
        lines.append("→ Treat as NEUTRAL. Trade structure, not bias.")
    else:
        lines.append(f"Bias: {bias} (strength: {strength}/100)")

    lines.append(f"Setup: {wl.get('setup_watching')} | Confidence: {wl.get('confidence','')}")

    # Primary (bull/long) setup
    lines.append("── BULL SETUP ──")
    lines.append(f"Trigger: {wl.get('entry_trigger','')}")
    if wl.get("entry_zone"):
        lines.append(f"Zone: {wl['entry_zone']}")
    lines.append(f"Stop: {wl.get('stop_price')} | T1: {wl.get('target_1')} | T2: {wl.get('target_2')}")
    lines.append(f"Invalidation: {wl.get('invalidation','')}")

    # Bear (short) setup — always shown in V3.0
    bear_setup = wl.get("bear_setup", "")
    if bear_setup and bear_setup.upper() != "NONE":
        lines.append("── BEAR SETUP ──")
        lines.append(f"Trigger: {bear_setup}")
        if wl.get("bear_entry_zone"):
            lines.append(f"Zone: {wl['bear_entry_zone']}")
        if wl.get("bear_stop") and wl.get("bear_target_1"):
            lines.append(f"Stop: {wl.get('bear_stop')} | T1: {wl.get('bear_target_1')}")
    else:
        lines.append("── BEAR SETUP: none identified ──")

    if wl.get("avoid_until"):
        lines.append(f"Avoid until: {wl['avoid_until']}")

    # D.3 — Key levels passthrough (these were in watchlist JSON but never shown)
    if wl.get("key_levels_above"):
        lines.append(f"Key levels above: {wl['key_levels_above']}")
    if wl.get("key_levels_below"):
        lines.append(f"Key levels below: {wl['key_levels_below']}")

    lines.append(f"Notes: {wl.get('notes','')}")
    lines.append("═══════════════════════════════════════")
    return "\n".join(lines)


# ─── Pre-filter ────────────────────────────────────────────

def pre_filter_signal(snapshot: dict) -> tuple:
    """
    Fast Python checks before touching the Claude API.
    Returns (worth_calling_claude: bool, reason: str).
    """
    # Hard blocks
    if snapshot.get("news_danger_zone"):
        return False, "news danger zone"

    or_dir = snapshot.get("or_direction")
    if not or_dir or or_dir == "DOJI":
        return False, f"no OR direction ({or_dir})"

    watchlist_bias       = get_watchlist().get("bias", "")
    watchlist_invalidated = get_watchlist().get("bias_invalidated", False)

    # V3.0 — Bias-aware pre-filter direction logic
    # Old: hard-block shorts on BULL days, longs on BEAR days
    # New: allow both directions when bias is NEUTRAL or invalidated;
    #      still soft-prefer the bias direction on PREFERRED days

    # Absolute gates (still apply)
    if watchlist_bias == "NO_TRADE":
        return False, "bias is NO_TRADE"

    rv = snapshot.get("or_relative_volume", 0)
    if rv and rv < 80:
        return False, f"rel vol too low ({rv:.0f}%)"

    mtf = snapshot.get("mtf_alignment", "")
    if "CONFLICTED" in mtf:
        return False, "MTF conflicted"

    price   = snapshot.get("last_price", 0)
    vwap    = snapshot.get("vwap",  0) or 0
    delta   = snapshot.get("cumulative_delta", 0) or 0
    or_high = snapshot.get("or_high", 0) or 0
    or_low  = snapshot.get("or_low",  0) or 0
    choch   = snapshot.get("choch", "")

    dom_imbalance  = snapshot.get("dom_imbalance", "NEUTRAL")
    dom_vacuum_up  = snapshot.get("dom_vacuum_above", False)
    dom_vacuum_dn  = snapshot.get("dom_vacuum_below", False)
    dom_bp         = snapshot.get("dom_buy_pressure", 0.5)
    dom_sweep_up   = snapshot.get("dom_sweep_up",   False)
    dom_sweep_dn   = snapshot.get("dom_sweep_down", False)
    dom_iceberg_b  = snapshot.get("dom_iceberg_bid")
    dom_iceberg_a  = snapshot.get("dom_iceberg_ask")
    dom_cluster_b  = snapshot.get("dom_cluster_below")
    dom_cluster_a  = snapshot.get("dom_cluster_above")
    last_price     = snapshot.get("last_price", 0) or 0

    # V4.0 — OFI signals
    ofi            = snapshot.get("ofi", {})
    ofi_score      = ofi.get("score", 0)
    ofi_signal     = ofi.get("signal", "NEUTRAL")
    ofi_accel      = ofi.get("acceleration", "STABLE")

    vp_above_vah   = snapshot.get("vp_above_vah", False)
    vp_below_val   = snapshot.get("vp_below_val", False)
    vp_inside_va   = snapshot.get("vp_inside_va", False)

    # Build bull signals
    bull_signals = 0
    bull_reasons = []
    if price > or_high and or_high > 0:
        bull_signals += 2; bull_reasons.append("above OR high")
    if price > vwap and vwap > 0:
        bull_signals += 1; bull_reasons.append("above VWAP")
    if delta > 0:
        bull_signals += 1; bull_reasons.append("delta positive")
    if "BULLISH" in choch:
        bull_signals += 2; bull_reasons.append("CHoCH bullish")
    if snapshot.get("or_entry_zone_active"):
        bull_signals += 2; bull_reasons.append("entry zone active")
    if "BULLISH_ALIGNED" in mtf:
        bull_signals += 1; bull_reasons.append("MTF aligned")
    elif mtf.startswith("PARTIAL_BULL"):
        bull_signals += 1; bull_reasons.append("MTF partial bull")
    if "BID_HEAVY" in dom_imbalance:
        bull_signals += 1; bull_reasons.append("DOM bid heavy")
    if dom_vacuum_up:
        bull_signals += 1; bull_reasons.append("DOM vacuum above")
    if dom_bp > DOM_BUY_PRESSURE_BULL_THRESHOLD:
        bull_signals += 1; bull_reasons.append(f"buy pressure {dom_bp:.0%}")
    if dom_sweep_up:
        bull_signals += 2; bull_reasons.append("DOM ask sweep — aggressive buyers")
    if dom_iceberg_b and last_price:
        bull_signals += 1; bull_reasons.append(f"iceberg bid @ {dom_iceberg_b}")
    if dom_cluster_b and last_price and abs(last_price - dom_cluster_b) < 10:
        bull_signals += 1; bull_reasons.append(f"cluster magnet below @ {dom_cluster_b}")
    # V4.0 — OFI signals (gated by feature flag)
    if FEATURE_OFI:
        if ofi_signal in ("STRONG_BUY", "BUY"):
            bull_signals += 2 if ofi_signal == "STRONG_BUY" else 1
            bull_reasons.append(f"OFI {ofi_signal} ({ofi_score:+d})")
        if ofi_signal in ("STRONG_BUY", "BUY") and ofi_accel == "ACCELERATING":
            bull_signals += 1; bull_reasons.append("OFI accelerating")
    if vp_above_vah:
        bull_signals += 1; bull_reasons.append("above VAH breakout")
    if vp_inside_va and price > snapshot.get("vp_poc", 0):
        bull_signals += 1; bull_reasons.append("above POC in VA")
    # Candle patterns — bullish engulfing or hammer near OB/FVG = +2; OR-aligned pattern = +1
    _cp = snapshot.get("candle_patterns", "")
    _at_level = any(kw in snapshot.get("fair_value_gaps", "") or snapshot.get("order_blocks", "")
                    for kw in ("★ INSIDE", "★ AT OB", "dist:0", "dist:1", "dist:2", "dist:3", "dist:4", "dist:5"))
    if ("BULLISH ENGULFING" in _cp or "HAMMER" in _cp) and _at_level:
        bull_signals += 2; bull_reasons.append("bullish pattern at OB/FVG")
    if "MORNING STAR" in _cp:
        bull_signals += 1; bull_reasons.append("morning star")
    if "[OR aligned]" in _cp and any(p in _cp for p in ("BULLISH ENGULFING", "HAMMER", "INSIDE BAR BREAKOUT UP", "MORNING STAR")):
        bull_signals += 1; bull_reasons.append("OR-aligned candle pattern")

    # Build bear signals
    bear_signals = 0
    bear_reasons = []
    if price < or_low and or_low > 0:
        bear_signals += 2; bear_reasons.append("below OR low")
    if price < vwap and vwap > 0:
        bear_signals += 1; bear_reasons.append("below VWAP")
    if delta < 0:
        bear_signals += 1; bear_reasons.append("delta negative")
    if "BEARISH" in choch:
        bear_signals += 2; bear_reasons.append("CHoCH bearish")
    if snapshot.get("or_entry_zone_active"):
        bear_signals += 2; bear_reasons.append("entry zone active")
    if "BEARISH_ALIGNED" in mtf:
        bear_signals += 1; bear_reasons.append("MTF aligned")
    elif mtf.startswith("PARTIAL_BEAR"):
        bear_signals += 1; bear_reasons.append("MTF partial bear")
    if "ASK_HEAVY" in dom_imbalance:
        bear_signals += 1; bear_reasons.append("DOM ask heavy")
    if dom_vacuum_dn:
        bear_signals += 1; bear_reasons.append("DOM vacuum below")
    if dom_bp < DOM_SELL_PRESSURE_BEAR_THRESHOLD:
        bear_signals += 1; bear_reasons.append(f"sell pressure {1-dom_bp:.0%}")
    if dom_sweep_dn:
        bear_signals += 2; bear_reasons.append("DOM bid sweep — aggressive sellers")
    if dom_iceberg_a and last_price:
        bear_signals += 1; bear_reasons.append(f"iceberg ask @ {dom_iceberg_a}")
    if dom_cluster_a and last_price and abs(last_price - dom_cluster_a) < 10:
        bear_signals += 1; bear_reasons.append(f"cluster magnet above @ {dom_cluster_a}")
    # V4.0 — OFI signals (gated by feature flag)
    if FEATURE_OFI:
        if ofi_signal in ("STRONG_SELL", "SELL"):
            bear_signals += 2 if ofi_signal == "STRONG_SELL" else 1
            bear_reasons.append(f"OFI {ofi_signal} ({ofi_score:+d})")
        if ofi_signal in ("STRONG_SELL", "SELL") and ofi_accel == "ACCELERATING":
            bear_signals += 1; bear_reasons.append("OFI accelerating")
    if vp_below_val:
        bear_signals += 1; bear_reasons.append("below VAL breakdown")
    if vp_inside_va and price < snapshot.get("vp_poc", 999999):
        bear_signals += 1; bear_reasons.append("below POC in VA")
    # Candle patterns — bearish engulfing or shooting star near OB/FVG = +2; OR-aligned = +1
    if ("BEARISH ENGULFING" in _cp or "SHOOTING STAR" in _cp) and _at_level:
        bear_signals += 2; bear_reasons.append("bearish pattern at OB/FVG")
    if "EVENING STAR" in _cp:
        bear_signals += 1; bear_reasons.append("evening star")
    if "[OR aligned]" in _cp and any(p in _cp for p in ("BEARISH ENGULFING", "SHOOTING STAR", "INSIDE BAR BREAKOUT DOWN", "EVENING STAR")):
        bear_signals += 1; bear_reasons.append("OR-aligned candle pattern")

    # Tape / large print signals
    _tape_bias = snapshot.get("tape_bias", "NEUTRAL")
    if _tape_bias == "AGGRESSIVE_BUYING":
        bull_signals += 2; bull_reasons.append("large block buying on tape")
    elif _tape_bias == "AGGRESSIVE_SELLING":
        bear_signals += 2; bear_reasons.append("large block selling on tape")

    # V3.0 direction gating:
    # NEUTRAL or invalidated bias → both directions allowed, pass stronger side
    # LONG_PREFERRED → bull signals pass freely; bear signals need extra strength (5+)
    # SHORT_PREFERRED → bear signals pass freely; bull signals need extra strength (5+)

    is_neutral = (watchlist_bias == "NEUTRAL" or watchlist_invalidated)
    is_long_pref  = watchlist_bias == "LONG_PREFERRED"
    is_short_pref = watchlist_bias == "SHORT_PREFERRED"

    THRESHOLD      = PRE_FILTER_SIGNAL_THRESHOLD
    COUNTER_THRESH = COUNTER_TREND_SIGNAL_THRESHOLD

    bull_passes = bull_signals >= THRESHOLD
    bear_passes = bear_signals >= THRESHOLD
    bull_counter_passes = bull_signals >= COUNTER_THRESH
    bear_counter_passes = bear_signals >= COUNTER_THRESH

    if is_neutral:
        # Both sides eligible — pass the stronger signal if above threshold
        if bull_passes and bear_passes:
            if bull_signals >= bear_signals:
                return True, f"BULL {bull_signals} signals (NEUTRAL bias) [{', '.join(bull_reasons[:4])}]"
            else:
                return True, f"BEAR {bear_signals} signals (NEUTRAL bias) [{', '.join(bear_reasons[:4])}]"
        if bull_passes:
            return True, f"BULL {bull_signals} signals (NEUTRAL bias) [{', '.join(bull_reasons[:4])}]"
        if bear_passes:
            return True, f"BEAR {bear_signals} signals (NEUTRAL bias) [{', '.join(bear_reasons[:4])}]"
        return False, f"insufficient signals (bull:{bull_signals} bear:{bear_signals})"

    elif is_long_pref:
        if bull_passes:
            return True, f"BULL {bull_signals} signals [{', '.join(bull_reasons[:4])}]"
        if bear_counter_passes:
            return True, f"BEAR {bear_signals} signals (counter-trend vs LONG_PREFERRED) [{', '.join(bear_reasons[:4])}]"
        return False, f"bull signals {bull_signals}/{THRESHOLD} (bear {bear_signals}/{COUNTER_THRESH} insufficient)"

    elif is_short_pref:
        if bear_passes:
            return True, f"BEAR {bear_signals} signals [{', '.join(bear_reasons[:4])}]"
        if bull_counter_passes:
            return True, f"BULL {bull_signals} signals (counter-trend vs SHORT_PREFERRED) [{', '.join(bull_reasons[:4])}]"
        return False, f"bear signals {bear_signals}/{THRESHOLD} (bull {bull_signals}/{COUNTER_THRESH} insufficient)"

    return False, "no qualifying setup"


# ─── Entry Analysis ────────────────────────────────────────

# A.1 — Skip-cache state for analyze_market (thresholds now live in config.py)

_last_entry_call: dict = {
    "ts":           0.0,
    "price":        0.0,
    "last_bar_ts":  None,    # 1-min bar timestamp at time of call
    "watchlist_ts": 0.0,
    "decision":     None,    # full decision dict, returned on skip
}


def _maybe_skip_call(snapshot: dict) -> dict | None:
    """
    Return the cached HOLD decision if conditions haven't materially changed.
    Returns None to indicate a real Opus call must happen.

    Never skips on:
      - First call of session (no cache)
      - Previous decision wasn't HOLD (BUY/SELL must always re-evaluate)
      - Price moved beyond threshold
      - New 1-min bar closed
      - Watchlist freshly refreshed
      - Too much time elapsed since cached decision
    """
    cached = _last_entry_call
    if not cached.get("decision"):
        return None

    prev = cached["decision"]
    # Only short-circuit on HOLD — BUY/SELL must always re-evaluate
    if prev.get("decision") != "HOLD":
        return None

    now = time.time()
    age = now - cached["ts"]
    if age >= SKIP_CACHE_MAX_AGE_SECS:
        return None

    cur_price = snapshot.get("last_price", 0) or 0
    prev_price = cached.get("price", 0) or 0
    if abs(cur_price - prev_price) > SKIP_CACHE_PRICE_DELTA:
        return None

    # New 1-min bar closed? Look at the candles section — first bar is newest
    cur_bar_ts = _extract_last_bar_ts(snapshot)
    if cur_bar_ts and cur_bar_ts != cached.get("last_bar_ts"):
        return None

    # Watchlist freshly refreshed?
    if (now - _watchlist_time) < SKIP_CACHE_WATCHLIST_AGE_SECS:
        return None

    # All checks passed — return the cached decision and increment counter
    _cost_tracker["skipped_calls"] += 1
    if _cost_tracker["skipped_calls"] % SKIP_LOG_EVERY_N == 1:
        # Log every SKIP_LOG_EVERY_N skips so the log doesn't drown in skip lines
        logger.info(
            f"Skip Opus: conditions unchanged "
            f"(price {prev_price:.2f}→{cur_price:.2f}, age {age:.0f}s, "
            f"total skips this session: {_cost_tracker['skipped_calls']})"
        )
    # Update session context as if we'd just called — keeps consecutive_holds counting
    update_session_context(snapshot, "HOLD", prev.get("reasoning", ""))
    # Return a fresh dict so callers can mutate safely
    return dict(prev)


def _extract_last_bar_ts(snapshot: dict) -> str | None:
    """Pull the newest 1-min bar timestamp from the candles snapshot."""
    candles = snapshot.get("candles", "") or ""
    if not candles:
        return None
    # First line after "1-MINUTE BARS..." header is newest bar
    for line in candles.split("\n"):
        line = line.strip()
        if line and (line.startswith("2026-") or line.startswith("2025-") or line.startswith("2027-")):
            # First 19 chars are the timestamp
            return line[:19]
    return None


def _record_entry_call(snapshot: dict, decision: dict) -> None:
    """Record this call's conditions for the next skip-check."""
    _last_entry_call.update({
        "ts":           time.time(),
        "price":        snapshot.get("last_price", 0) or 0,
        "last_bar_ts":  _extract_last_bar_ts(snapshot),
        "watchlist_ts": _watchlist_time,
        "decision":     dict(decision),  # shallow copy is fine
    })


def analyze_market(snapshot: dict) -> dict:
    """
    Entry decision. Uses Opus 4.7 with prompt caching:
      - System prompt cached (~2500 tokens)
      - Performance + watchlist + stable session context cached as one block
      - Snapshot + volatile session bits stay uncached (changes every call)

    A.1 — Skip the Opus call entirely when conditions haven't materially
    changed since the last HOLD. "Materially" means:
      - Price moved > _SKIP_PRICE_DELTA points, OR
      - New 1-min bar closed, OR
      - Watchlist refreshed, OR
      - More than _SKIP_MAX_AGE_SECS elapsed
    During the chop windows where Claude HOLDs 23 times in 6 min on
    essentially the same conditions, this cuts cost ~70%.
    """
    # A.1 — Skip-if-unchanged guard
    skip_result = _maybe_skip_call(snapshot)
    if skip_result is not None:
        return skip_result

    perf_context = ""
    if _get_perf_ctx:
        try:
            perf_context = _get_perf_ctx()
        except Exception as e:
            logger.debug(f"Performance context error: {e}")

    # A.2 — Static block: watchlist (5-min TTL) + stable session context.
    # perf_context is moved to DYNAMIC because it changes whenever a trade
    # completes — if it's in the static block it busts the cache on every trade.
    # The system prompt (SYSTEM_PROMPT via _build_system) is the primary cache
    # anchor at ~2000 tokens. Static user content adds the watchlist game plan.
    static_context = (
        _format_watchlist_context()
        + _format_session_context_static()
    )

    # Dynamic block — snapshot, volatile session bits, AND perf context.
    # Perf context only changes on trade events (rare), not every scan.
    dynamic_session = _format_session_context_dynamic()
    perf_header = f"\n{perf_context}\n" if perf_context else ""
    dynamic_snapshot = f"""
═══════════════════════════════════════
MNQ MARKET SNAPSHOT — {snapshot.get('time_et', 'N/A')} ET
═══════════════════════════════════════
{perf_header}
{dynamic_session}

TIMING:
Kill Zone: {snapshot.get('killzone', 'N/A')}
AMD Phase: {snapshot.get('amd_phase', 'N/A')}
Session:   {snapshot.get('session_phase', 'N/A')}

HTF BIAS:
{snapshot.get('htf_bias', 'N/A')}

MTF ALIGNMENT: {snapshot.get('mtf_alignment', 'N/A')}
MTF SCORE: {snapshot.get('mtf_score', {}).get('score', '?')}/100 — {snapshot.get('mtf_score', {}).get('bull_tfs', '?')} bull TFs / {snapshot.get('mtf_score', {}).get('bear_tfs', '?')} bear TFs

MARKET STRUCTURE:
{snapshot.get('market_structure', 'N/A')}

{snapshot.get('opening_range', 'Opening range not available')}

═══════════════════════════════════════
ECONOMIC CALENDAR CONTEXT
═══════════════════════════════════════
Next event:    {snapshot.get('next_event_full') or 'none scheduled for rest of session'}
Time until:    {('%d min' % snapshot['next_event_minutes']) if snapshot.get('next_event_minutes') is not None else 'n/a'}
Recently released: {snapshot.get('recent_event') or 'none in past hour'}

NEWS-AWARE ENTRY RULES (apply BEFORE confluence scoring):
- If next event is HIGH impact and ≤45 min away → heavily de-rate this setup, prefer HOLD/wait
- If next event is MEDIUM impact and ≤20 min away → reduce position size or skip
- If a HIGH event released in the last 30 min → treat current price as REACTIVE noise, not structural; only enter on extreme confluence (8+)
- If FOMC press conference is active (typically 14:30-15:30 ET on FOMC days) → no entries
- If "none scheduled" → standard rules apply, this is a clean technical window

The hard danger-zone blackout is already enforced by the bot. Your job here
is the SOFT context: a setup right before NFP or just after CPI is lower
quality than the same setup in a clean window.

═══════════════════════════════════════

{snapshot.get('news_text', 'No news data')}

{snapshot.get('ibkr_headlines_text', '')}

Candle Patterns: {snapshot.get('candle_patterns', 'N/A')}
Change of Character (1-min): {snapshot.get('choch', 'N/A')}
Inducement: {snapshot.get('inducement', 'N/A')}
Delta Trend: {snapshot.get('delta_trend', 'N/A')} {'(true bid/ask classification)' if snapshot.get('delta_is_live') else '(signed-volume approximation — delayed data, less reliable)'}
{snapshot.get('tape_text', '')}

═══════════════════════════════════════
PRICE ACTION
═══════════════════════════════════════
Price:       {snapshot.get('last_price', 'N/A')}
Bid/Ask:     {snapshot.get('bid', 'N/A')} / {snapshot.get('ask', 'N/A')}
VWAP:        {snapshot.get('vwap', 'N/A')}
Session H/L: {snapshot.get('session_high', 'N/A')} / {snapshot.get('session_low', 'N/A')}
Volume:      {snapshot.get('volume', 'N/A')}

{snapshot.get('session_levels', '')}

═══════════════════════════════════════
ICT LEVELS
═══════════════════════════════════════
FVGs: {snapshot.get('fair_value_gaps', 'N/A')}
OBs:  {snapshot.get('order_blocks', 'N/A')}
Liq:  {snapshot.get('liquidity_pools', 'N/A')}

═══════════════════════════════════════
CANDLES
═══════════════════════════════════════
{snapshot.get('candles', 'No candle data')[:1200]}

═══════════════════════════════════════
ORDER FLOW
═══════════════════════════════════════
Cumulative Delta: {snapshot.get('cumulative_delta', 'N/A')}
Delta Last Bar:   {snapshot.get('delta_last_bar', 'N/A')}
Large Prints:     {snapshot.get('large_prints', 'N/A')}

DOM (Level 2 — 20 levels each side):
{snapshot.get('dom', 'Not available')}

DOM SIGNALS (structured):
  Imbalance: {snapshot.get('dom_imbalance', 'N/A')} | Buy pressure: {snapshot.get('dom_buy_pressure', 'N/A')}
  Resistance wall: {snapshot.get('dom_resistance_wall', 'N/A')} | Support wall: {snapshot.get('dom_support_wall', 'N/A')}
  Vacuum above: {snapshot.get('dom_vacuum_above', False)} | Vacuum below: {snapshot.get('dom_vacuum_below', False)}
  Cluster magnet above: {snapshot.get('dom_cluster_above') or 'none'} | Cluster magnet below: {snapshot.get('dom_cluster_below') or 'none'}
  Iceberg ask: {snapshot.get('dom_iceberg_ask') or 'none'} | Iceberg bid: {snapshot.get('dom_iceberg_bid') or 'none'}
  Spoof ask: {snapshot.get('dom_spoof_ask') or 'none'} | Spoof bid: {snapshot.get('dom_spoof_bid') or 'none'}
  Sweep up: {snapshot.get('dom_sweep_up', False)} | Sweep down: {snapshot.get('dom_sweep_down', False)}

DOM INTERPRETATION RULES:
- CLUSTER MAGNET: group of large orders within 5 ticks — price tends to run to this level
- ICEBERG: replenishing order — treat as strong S/R, harder to break than it looks
- SPOOF: large order vanished — likely manipulation, ignore that level for bias
- SWEEP: 3+ levels consumed — directional conviction, trade with the sweep direction
- VACUUM: thin book — price moves quickly through this zone, useful for target extension

Volume Profile (session):
{snapshot.get('volume_profile', 'N/A')}
  POC: {snapshot.get('vp_poc', 'N/A')} | VAH: {snapshot.get('vp_vah', 'N/A')} | VAL: {snapshot.get('vp_val', 'N/A')}
  Status: {snapshot.get('vp_status', 'N/A')}

═══════════════════════════════════════
RISK
═══════════════════════════════════════
Position:           {snapshot.get('current_position', 0)}
Daily P&L:          ${snapshot.get('daily_pnl', 0)}
Loss Remaining:     ${snapshot.get('daily_loss_remaining', 0)}
Consecutive Losses: {snapshot.get('consecutive_losses', 0)}

Respond in EXACT format. Use price levels for STOP_PRICE, TARGET_1, TARGET_2 — NOT tick counts.
STOP_PRICE is MANDATORY for BUY/SELL. If you cannot identify a structure-based stop, output HOLD.
"""

    try:
        response = client.messages.create(
            model=CLAUDE_ENTRY_MODEL,
            max_tokens=500,
            system=_build_system(SYSTEM_PROMPT),
            messages=[{
                "role": "user",
                "content": _build_user_content([static_context], dynamic_snapshot),
            }],
        )
        cost_info = _log_cache_usage(response, model=CLAUDE_ENTRY_MODEL, purpose="entry")

        # Concatenate text blocks if Claude returns multiple
        raw = "".join(
            block.text for block in response.content
            if getattr(block, "type", "text") == "text"
        )
        decision = parse_decision(raw)
        decision["raw"] = raw

        update_session_context(snapshot, decision.get("decision", "HOLD"), decision.get("reasoning", ""))

        # A.1 — Record this call so next call can skip if unchanged
        _record_entry_call(snapshot, decision)

        # Backtest recorder — persist input + output for replay
        _recorder.record_decision(
            snapshot       = snapshot,
            raw_response   = raw,
            parsed_decision= decision,
            model          = CLAUDE_ENTRY_MODEL,
            cost_usd       = cost_info.get("usd", 0.0),
            pre_filter_reason = snapshot.get("_pre_filter_reason", ""),
        )

        logger.info(
            f"Claude: {decision['decision']} | "
            f"Prob: {decision.get('thesis_probability', '?')}% | "
            f"Conf: {decision['confidence']} | Mode: {decision['mode']}"
        )
        if decision.get("reasoning"):
            logger.info(f"Reasoning: {decision['reasoning'][:200]}")
        if decision.get("confluence"):
            logger.info(f"Confluence [{decision.get('confluence_score',0)}/10]: {decision['confluence']}")

        return decision

    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return {
            "decision": "HOLD", "mode": "NONE", "confidence": "LOW",
            "reasoning": "", "strategy": "", "confluence": "",
            "confluence_score": 0, "raw": str(e),
        }


# ─── Position Management ───────────────────────────────────

def analyze_position(
    snapshot: dict, position: int, entry_price: float,
    stop_price: float, target_price: float, trade_mode: str,
) -> dict:
    """
    In-position decision. Uses Sonnet 4.6 with cached system prompt.
    Much cheaper than the entry call and runs more often.
    """
    direction     = "LONG" if position > 0 else "SHORT"
    current_price = snapshot.get("last_price", 0)

    if current_price and entry_price:
        pnl = (
            (current_price - entry_price) / TICK_SIZE * TICK_VALUE
            if position > 0
            else (entry_price - current_price) / TICK_SIZE * TICK_VALUE
        )
    else:
        pnl = 0.0

    ticks_stop   = abs(current_price - stop_price) / TICK_SIZE if current_price else 0
    ticks_target = abs(current_price - (target_price or 0)) / TICK_SIZE if current_price and target_price else 0

    msg = f"""
POSITION MANAGEMENT — {snapshot.get('time_et', 'N/A')} ET

POSITION:
Direction : {direction}
Entry     : {entry_price}
Current   : {current_price}
Stop      : {stop_price}
Target    : {target_price}
Unrealized: ${pnl:.2f}
Ticks→Stop:   {ticks_stop:.0f}t
Ticks→Target: {ticks_target:.0f}t
Mode: {trade_mode}

PRICE: {current_price} | VWAP: {snapshot.get('vwap')}
Bid: {snapshot.get('bid')} / Ask: {snapshot.get('ask')}

MTF Alignment: {snapshot.get('mtf_alignment', 'N/A')}
Delta Trend:   {snapshot.get('delta_trend', 'N/A')}

CANDLES:
{snapshot.get('candles', '')[:600]}

ORDER FLOW {'(live L2 — reliable)' if snapshot.get('delta_is_live') else '(delayed — treat as approximate)'}:
Cumulative delta: {snapshot.get('cumulative_delta')} {'→ net buyers in control' if (snapshot.get('cumulative_delta') or 0) > 0 else '→ net sellers in control'}
Delta last bar:   {snapshot.get('delta_last_bar')} {'→ buying pressure this bar' if (snapshot.get('delta_last_bar') or 0) > 0 else '→ selling pressure this bar'}
Delta trend:      {snapshot.get('delta_trend', 'N/A')}
Large prints:     {snapshot.get('large_prints', 'none')}

OFI (Order Flow Imbalance — V4.0 predictive signal):
{snapshot.get('ofi', {}).get('text', 'OFI unavailable')}
OFI score: {snapshot.get('ofi', {}).get('score', 0):+d}/100 | Signal: {snapshot.get('ofi', {}).get('signal', 'NEUTRAL')} | Accel: {snapshot.get('ofi', {}).get('acceleration', 'STABLE')}
OFI divergence: {snapshot.get('ofi', {}).get('divergence', False)}

OFI RULES (use these to adjust THESIS_PROBABILITY):
- STRONG_BUY + ACCELERATING: +10 to thesis probability for longs
- BUY + ACCELERATING: +5 to thesis probability for longs
- NEUTRAL: no adjustment
- SELL/STRONG_SELL against your direction: -10 to thesis probability
- DIVERGENCE (OFI disagrees with price): -10 — treat current move as weak

ICT LEVELS:
{snapshot.get('fair_value_gaps', '')}
{snapshot.get('order_blocks', '')}
{snapshot.get('liquidity_pools', '')}

DOM: {snapshot.get('dom', 'N/A')[:200]}
  Imbalance: {snapshot.get('dom_imbalance','N/A')} | Buy pressure: {snapshot.get('dom_buy_pressure','N/A')}
  VP: POC:{snapshot.get('vp_poc','N/A')} VAH:{snapshot.get('vp_vah','N/A')} VAL:{snapshot.get('vp_val','N/A')}

Respond in EXACT format:
DECISION: [HOLD / CLOSE / TRAIL]
NEW_STOP: [price level]
CONFIDENCE: [LOW / MEDIUM / HIGH]
THESIS_STATUS: [INTACT / WEAKENING / INVALIDATED]
REASONING: [2 sentences max]
"""

    try:
        response = client.messages.create(
            model=CLAUDE_POSITION_MODEL,
            max_tokens=200,
            system=_build_system(POSITION_SYSTEM),
            messages=[{"role": "user", "content": msg}],
        )
        _log_cache_usage(response, model=CLAUDE_POSITION_MODEL, purpose="position")
        raw    = "".join(
            block.text for block in response.content
            if getattr(block, "type", "text") == "text"
        )
        result = parse_position_decision(raw)
        result["raw"] = raw

        logger.info(
            f"Position: {result['decision']} | Stop: {result['new_stop']} | "
            f"{result.get('reasoning','')[:120]}"
        )
        return result

    except Exception as e:
        logger.error(f"Position analysis error: {e}")
        return {
            "decision": "HOLD", "new_stop": "KEEP",
            "confidence": "LOW", "thesis_status": "INTACT",
            "reasoning": str(e),
        }


# ─── Pre-Market ────────────────────────────────────────────

def analyze_premarket(snapshot: dict, memory_context: str) -> dict:

    # V4.1 — Inject learning findings from recent sessions
    learning_context = ""
    if FEATURE_LEARNING_INJECT:
        try:
            from learning_session import load_learning_for_premarket
            learning_context = load_learning_for_premarket(n_days=3)
        except Exception:
            pass

    msg = f"""
PRE-MARKET ANALYSIS — {snapshot.get('time_et', 'N/A')} ET

{memory_context}

{learning_context}

CURRENT DATA:
AMD Phase: {snapshot.get('amd_phase')}
HTF Bias:  {snapshot.get('htf_bias')}
Structure: {snapshot.get('market_structure')}
MTF:       {snapshot.get('mtf_alignment', 'N/A')}
Price:     {snapshot.get('last_price')}
Session H/L: {snapshot.get('session_high')} / {snapshot.get('session_low')}

{snapshot.get('session_levels', '')}

ICT Levels:
FVGs: {snapshot.get('fair_value_gaps')}
OBs:  {snapshot.get('order_blocks')}
Liq:  {snapshot.get('liquidity_pools')}

Delta: {snapshot.get('cumulative_delta')}
Delta trend: {snapshot.get('delta_trend', 'N/A')}

{snapshot.get('news_text', '')}

Build your game plan:
1. HTF bias?
2. Did London manipulate Asia? Which direction?
3. Most likely NY distribution direction?
4. Key levels to watch (with exact prices)?
5. Exact setups and triggers you are looking for?
6. What would make you NOT trade today?

DECISION: HOLD
MODE: NONE
CONTRACTS: 0
STOP_PRICE: 0
TARGET_1: 0
TARGET_2: 0
CONFIDENCE: HIGH
REASONING: [full game plan]
"""

    try:
        response = client.messages.create(
            model=CLAUDE_ENTRY_MODEL,
            max_tokens=800,
            system=_build_system(SYSTEM_PROMPT),
            messages=[{"role": "user", "content": msg}],
        )
        _log_cache_usage(response, model=CLAUDE_ENTRY_MODEL, purpose="premarket")
        raw      = "".join(
            block.text for block in response.content
            if getattr(block, "type", "text") == "text"
        )
        decision = parse_decision(raw, allow_zero_stop=True)  # pre-market is HOLD
        decision["raw"] = raw

        logger.info("=" * 50)
        logger.info("PRE-MARKET GAME PLAN:")
        logger.info(raw[:500])
        logger.info("=" * 50)
        return decision

    except Exception as e:
        logger.error(f"Pre-market error: {e}")
        return {"decision": "HOLD", "mode": "NONE", "confidence": "LOW", "raw": str(e)}


# ─── Parsers ───────────────────────────────────────────────

def _first_match(value: str, candidates: list) -> str | None:
    v = value.upper()
    for c in candidates:
        if c in v:
            return c
    return None


def _extract_int(s: str, default: int) -> int:
    digits = "".join(c for c in s if c.isdigit())
    return int(digits) if digits else default


def _extract_float(s: str, default: float) -> float:
    import re
    m = re.search(r"[\d,]+\.?\d*", s.replace(",", ""))
    if m:
        try:
            return float(m.group())
        except Exception:
            pass
    return default


def parse_decision(text: str, allow_zero_stop: bool = False) -> dict:
    """
    Parse Claude's structured response into a dict.

    V4.0 — Added THESIS_PROBABILITY (0-100). Replaces LOW/MEDIUM/HIGH as the
    primary entry gate. Entries blocked when probability < MIN_THESIS_PROBABILITY.

    P1.7 — If decision is BUY/SELL but stop_price <= 0, demote to HOLD.
    """
    d = {
        "decision": "HOLD", "mode": "NONE", "contracts": 1,
        "stop_price": 0.0, "target_1": 0.0, "target_2": 0.0,
        "stop_ticks": 100, "target_ticks": 200,
        "confidence": "LOW",
        "thesis_probability": 0,   # V4.0 — 0-100
        "strategy": "", "confluence": "", "confluence_score": 0,
        "reasoning": "",
    }

    for raw_line in text.strip().splitlines():
        line = raw_line.strip().replace("**", "").replace("*", "")
        if not line or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().upper()
        val = line.split(":", 1)[1].strip()

        if key == "DECISION":
            d["decision"] = _first_match(val, ["BUY", "SELL", "HOLD"]) or "HOLD"
        elif key == "MODE":
            d["mode"] = _first_match(val, ["SCALP", "SWING", "MOMENTUM", "NONE"]) or "NONE"
        elif key == "CONTRACTS":
            d["contracts"] = _extract_int(val, 1)
        elif key == "STOP_PRICE":
            d["stop_price"] = _extract_float(val, 0.0)
        elif key == "STOP_TICKS":
            d["stop_ticks"] = _extract_int(val, 100)
        elif key == "TARGET_1":
            d["target_1"] = _extract_float(val, 0.0)
        elif key == "TARGET_2":
            if "TRAIL" in val.upper():
                d["target_2"] = "TRAIL"
            else:
                d["target_2"] = _extract_float(val, 0.0)
        elif key == "TARGET_TICKS":
            d["target_ticks"] = _extract_int(val, 200)
        elif key == "CONFIDENCE":
            d["confidence"] = _first_match(val, ["HIGH", "MEDIUM", "LOW"]) or "LOW"
        elif key == "THESIS_PROBABILITY":
            prob = _extract_int(val, 0)
            d["thesis_probability"] = max(0, min(100, prob))   # clamp 0-100
        elif key == "STRATEGY":
            d["strategy"] = val
        elif key == "CONFLUENCE":
            d["confluence"] = val
        elif key == "CONFLUENCE_SCORE":
            d["confluence_score"] = _extract_int(val, 0)
        elif key == "REASONING":
            d["reasoning"] = val

    # P1.7 — demote BUY/SELL with missing stop to HOLD
    if not allow_zero_stop and d["decision"] in ("BUY", "SELL"):
        if d["stop_price"] <= 0:
            logger.warning(
                f"Claude returned {d['decision']} without STOP_PRICE — demoting to HOLD. "
                f"Reasoning: {d['reasoning'][:120]}"
            )
            d["decision"]  = "HOLD"
            d["reasoning"] = f"[DEMOTED — no STOP_PRICE] {d['reasoning']}"

    # V4.0 — demote BUY/SELL below probability threshold to HOLD
    if FEATURE_THESIS_GATE and not allow_zero_stop and d["decision"] in ("BUY", "SELL"):
        prob = d["thesis_probability"]
        if prob > 0 and prob < MIN_THESIS_PROBABILITY:
            logger.info(
                f"Thesis probability {prob}% below threshold {MIN_THESIS_PROBABILITY}% "
                f"— demoting {d['decision']} to HOLD"
            )
            d["decision"]  = "HOLD"
            d["reasoning"] = (
                f"[PROB GATE: {prob}% < {MIN_THESIS_PROBABILITY}% threshold] "
                f"{d['reasoning']}"
            )

    return d


def parse_position_decision(text: str) -> dict:
    r = {
        "decision": "HOLD", "new_stop": "KEEP",
        "confidence": "LOW", "thesis_status": "INTACT",
        "reasoning": "",
    }
    for raw_line in text.strip().splitlines():
        line = raw_line.strip().replace("**", "").replace("*", "")
        if not line or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().upper()
        val = line.split(":", 1)[1].strip()

        if key == "DECISION":
            r["decision"] = _first_match(val, ["CLOSE", "TRAIL", "HOLD"]) or "HOLD"
        elif key == "NEW_STOP":
            r["new_stop"] = val
        elif key == "CONFIDENCE":
            r["confidence"] = _first_match(val, ["HIGH", "MEDIUM", "LOW"]) or "LOW"
        elif key == "THESIS_STATUS":
            r["thesis_status"] = _first_match(val, ["INVALIDATED", "WEAKENING", "INTACT"]) or "INTACT"
        elif key == "REASONING":
            r["reasoning"] = val
    return r


print(f"Claude brain loaded — entry:{CLAUDE_ENTRY_MODEL} pos:{CLAUDE_POSITION_MODEL} "
      f"caching:{'ON' if CLAUDE_USE_CACHING else 'OFF'}")
