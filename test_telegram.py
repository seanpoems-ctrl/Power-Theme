#!/usr/bin/env python3
"""
Telegram Bot Integration Stress Test
=====================================
Generates a highly realistic mock pre-market / post-market trading brief and
sends it via Telegram MarkdownV2.  Designed to surface every common formatting
failure before you wire in live data.

Usage
-----
    python test_telegram.py --dry-run          # Preview without sending
    python test_telegram.py                    # Send (reads .env credentials)
    python test_telegram.py --token T --chat-id C
"""

import os
import re
import sys
import requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Configuration ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")


def _api_url(token: str) -> str:
    return f"https://api.telegram.org/bot{token}/sendMessage"


# ── MarkdownV2 helpers ────────────────────────────────────────────────────────
# Official special characters that must be escaped in regular (non-code) text.
# NOTE: '&' is NOT in this list — escaping it is undefined behavior and causes 400s.
_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def esc(value) -> str:
    """Escape all MarkdownV2 special characters for use in plain / bold / italic text."""
    return _SPECIAL.sub(r'\\\1', str(value))


def code(value) -> str:
    """
    Wrap a value in a MarkdownV2 inline code span.
    Inside code spans only '`' and '\\' need escaping — everything else is literal.
    """
    safe = str(value).replace("\\", "\\\\").replace("`", "\\`")
    return f"`{safe}`"


# ── Mock data ─────────────────────────────────────────────────────────────────

MOCK_STOCKS = [
    {
        "ticker":     "NVDA",
        "company":    "NVIDIA Corp",
        "adr_pct":    5.8,
        "setup":      "High Tight Flag",
        "gap_pct":    3.2,
        "catalyst":   "AI datacenter demand upgrade cycle",
        "conviction": 92,
        "basket":     "AI Infrastructure",
        "close":      875.40,
        "pivot":      850.00,
        "perf_1d":    +1.42,
    },
    {
        "ticker":     "PLTR",
        "company":    "Palantir Technologies",
        "adr_pct":    6.1,
        "setup":      "Episodic Pivot",
        "gap_pct":    7.4,
        # edge case: '$' and parentheses in catalyst text
        "catalyst":   "DoD contract $480M expansion (AIP deployment)",
        "conviction": 87,
        "basket":     "AI Infrastructure",
        "close":      24.82,
        "pivot":      24.50,
        "perf_1d":    +7.41,
    },
    {
        "ticker":     "RKLB",
        "company":    "Rocket Lab USA",
        "adr_pct":    8.3,
        "setup":      "Breakthrough",
        "gap_pct":    4.9,
        "catalyst":   "NASA ESCAPADE mission greenlit",
        "conviction": 81,
        "basket":     "Space Tech",
        "close":      18.75,
        "pivot":      17.80,
        "perf_1d":    +4.88,
    },
    {
        # edge case: underscore in ticker — collides with _italic_ in MarkdownV2
        "ticker":     "BRK_B",
        "company":    "Berkshire Hathaway B",
        "adr_pct":    1.4,
        "setup":      "Flat Base",
        "gap_pct":    0.6,
        # edge case: parentheses + '$' in catalyst
        "catalyst":   "Buffett annual letter (insurance float $168B)",
        "conviction": 55,
        "basket":     "Value & Financials",
        "close":      415.20,
        "pivot":      410.00,
        "perf_1d":    +0.58,
    },
    {
        "ticker":     "CEG",
        "company":    "Constellation Energy",
        "adr_pct":    4.7,
        "setup":      "High Tight Flag",
        "gap_pct":    5.1,
        # edge cases: '>' operator and '$' ticker reference
        "catalyst":   "Nuclear deal >3GW capacity with $MSFT data centers",
        "conviction": 89,
        "basket":     "AI Infrastructure",
        "close":      198.33,
        "pivot":      195.00,
        "perf_1d":    +5.09,
    },
]


def _vix_metrics(vix: float) -> dict:
    """VIX Rule-of-16: expected daily 1σ move = VIX / 16."""
    daily_move = round(vix / 16, 2)
    if vix < 15:
        regime = "Low Vol — Trending"
    elif vix < 20:
        regime = "Normal"
    elif vix < 30:
        regime = "Elevated — Cautious"
    else:
        regime = "Fear Spike — Reduce Size"
    return {"vix": vix, "daily_move": daily_move, "regime": regime}


def _bar(score: int, width: int = 10) -> str:
    """ASCII conviction bar, e.g. '████████░░'."""
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ── Brief generator ───────────────────────────────────────────────────────────

def generate_mock_brief() -> str:
    """
    Returns a complete MarkdownV2-formatted string containing both a
    pre-market and post-market trading brief.

    Edge cases deliberately included:
      - Underscores in tickers (BRK_B) → esc() must convert to BRK\\_B
      - Dollar signs in catalyst text ($480M, $MSFT)
      - Greater-than operator in catalyst (>3GW) → esc() escapes to \\>3GW
      - Parentheses in text → escaped to \\( \\)
      - '#', '!', '+', '-', '.', '=' in plain text → all escaped
      - Nested bullet points (3 levels deep)
      - '|' pipe separators between code spans → escaped to \\|
    """
    date_s = datetime.now().strftime("%B %d %Y")   # "March 28 2026" — no special chars
    time_s = datetime.now().strftime("%H:%M")

    s5fi = 42.3   # % of S&P 500 above 50-day MA  (& is NOT special in MDv2)
    mmth = 31.7   # % of S&P 500 above 200-day MA
    vix  = _vix_metrics(22.4)

    if s5fi < 40:
        breadth_icon = "🔴 Bearish Breadth"
    elif s5fi < 60:
        breadth_icon = "🟡 Mixed Breadth"
    else:
        breadth_icon = "🟢 Bullish Breadth"

    baskets: dict[str, list] = {}
    for s in MOCK_STOCKS:
        baskets.setdefault(s["basket"], []).append(s)

    daily_move   = vix["daily_move"]
    vix_val      = vix["vix"]
    vix_regime   = vix["regime"]
    daily_code   = code(f"±{daily_move}%")
    vix_code     = code(vix_val)

    L = []   # output lines

    # ══════════════════════════════════════════════════════════════
    # SECTION 1 — PRE-MARKET BRIEF
    # ══════════════════════════════════════════════════════════════

    L += [
        f"🌅 *PRE\\-MARKET BRIEF* — {date_s}",
        f"_{time_s} ET \\| Mock Data \\| Stress Test_",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📊 *Futures Snapshot*",
        # '&' in "S&P" needs no escaping
        f"  • S&P 500 futures:  {code('+0.42%')}",
        f"  • Nasdaq futures:   {code('+0.71%')}",
        f"  • Dow futures:      {code('-0.08%')}",
        f"  • Russell 2000:     {code('-0.19%')}",
        # '|' between items must be escaped outside code spans
        f"  • 10Y Yield: {code('4.31%')} \\| DXY: {code('103.7')}",
        f"  • WTI Oil: {code('$78.40')} \\| Gold: {code('$2,341')}",
        "",
        # '(' and ')' in section header must be escaped
        "🧭 *Market Breadth \\(S&P 500\\)*",
        f"  • S5FI \\(% above 50MA\\):  {code(f'{s5fi}%')}  {breadth_icon}",
        f"  • MMTH \\(% above 200MA\\): {code(f'{mmth}%')}",
        f"  • VIX: {vix_code}",
        # '±' is NOT special; '/' is NOT special
        f"    ↳ Rule of 16: daily 1σ move ≈ {daily_code}",
        # '_italic_' wraps esc'd text — esc() will escape any '_' inside the value
        f"    ↳ Regime: _{esc(vix_regime)}_",
        f"    ↳ At VIX {vix_code}, a {daily_code} daily "
        f"swing is _within normal range_ \\— size positions accordingly",
        "",
        "🚀 *Pre\\-Market Gappers \\(Top Setups\\)*",
    ]

    for s in sorted(MOCK_STOCKS, key=lambda x: x["gap_pct"], reverse=True):
        ticker     = esc(s["ticker"])    # BRK_B → BRK\_B
        company    = esc(s["company"])
        setup      = esc(s["setup"])
        catalyst   = esc(s["catalyst"])  # escapes >, (, ), . in the text
        adr_flag   = " ⚡ _high ADR_" if s["adr_pct"] > 4.5 else ""
        conv       = s["conviction"]
        gap_code   = code(f"+{s['gap_pct']}%")
        adr_code   = code(f"{s['adr_pct']}%")
        pivot_code = code(f"${s['pivot']:,.2f}")
        conv_code  = code(f"{conv}/99")

        L += [
            "",
            f"  *{ticker}* — {company}",
            f"    ├ Gap: {gap_code}  ADR: {adr_code}{adr_flag}",
            f"    ├ Setup: _{setup}_",
            f"    ├ Pivot: {pivot_code}",
            f"    ├ Catalyst: {catalyst}",
            f"    └ Conviction: {conv_code}  {_bar(conv)}",
        ]

    L += [
        "",
        "🗂 *Thematic Baskets*",
    ]
    for basket_name, stocks in baskets.items():
        tickers = "  ".join(code(s["ticker"]) for s in stocks)
        # '&' in "Value & Financials" needs no escaping
        L.append(f"  • *{esc(basket_name)}*: {tickers}")

    # ── Intentional edge-case stress section ──────────────────────────────
    L += [
        "",
        "⚠️ *Edge Case Formatting Stress Test*",
        "_Characters that commonly break MarkdownV2 parsers:_",
        "",
        # Underscore in ticker — must not open an italic span
        f"  • Underscore ticker: {code('BRK_B')} and {code('BF_B')} \\(no italic bleed\\)",
        # Dollar signs — '$' is NOT special, but adjacent '.' IS
        f"  • Dollar amounts: {code('$125.50')} and {code('$1,200.00')}",
        # '>' in comparison — special char, must be escaped in plain text
        f"  • Comparison: price \\>\\${code('500')} or \\<\\${code('10')}",
        # '-' in a range — must be escaped outside code
        f"  • Price range: \\$420\\-\\$440",
        # '+' and '.' — both special outside code
        f"  • Signed pct: \\+3\\.2% → \\+8\\.7% over 5 sessions",
        # Nested parentheses with '$'
        f"  • Parens: \\(float {code('$168B')}\\)",
        # '#' and '!' — both special
        f"  • Hash tag: \\#momentum \\#AI",
        f"  • Exclamation: Buy alert\\!",
        # '|' between code spans — must be escaped
        f"  • Pipe: SPY {code('+0.89%')} \\| QQQ {code('+1.12%')} \\| IWM {code('-0.31%')}",
        # '=' in formula — special
        f"  • Formula: PnL \\= \\(exit \\- entry\\) × shares",
        # Nested bullets (3 levels deep)
        f"  • Nested bullets:",
        f"      ◦ Level 2 — AI \\+ Energy thesis",
        f"          ▪ Level 3 — nuclear \\(CEG\\) × cloud \\(MSFT\\)",
        f"              · Level 4 — capacity target {code('>3GW')}",
    ]

    # ══════════════════════════════════════════════════════════════
    # SECTION 2 — POST-MARKET BRIEF
    # ══════════════════════════════════════════════════════════════

    L += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🌆 *POST\\-MARKET BRIEF* — {date_s}",
        "",
        # apostrophe ' is NOT special in MDv2 — no escaping needed
        "📈 *Today's Close*",
        f"  • SPY:  {code('+0.89%')}  closed _above_ 50MA ✅",
        f"  • QQQ:  {code('+1.12%')}  momentum intact ✅",
        f"  • IWM:  {code('-0.31%')}  small\\-cap lagging ⚠️",
        f"  • UVXY: {code('-4.2%')}   vol crush ongoing",
        "",
        "📋 *Watchlist Review*",
    ]

    for s in MOCK_STOCKS:
        ticker = esc(s["ticker"])
        setup  = esc(s["setup"])
        conv   = s["conviction"]
        close  = f"${s['close']:,.2f}"
        sign   = "+" if s["perf_1d"] >= 0 else ""
        perf   = f"{sign}{s['perf_1d']:.2f}%"
        status = "✅ Triggered" if conv >= 85 else ("👀 Watch" if conv >= 70 else "⏳ Wait")

        L.append(
            f"  • *{ticker}* {code(close)} {code(perf)}"
            f"  _{setup}_  {status}  \\({code(f'{conv}/99')}\\)"
        )

    L += [
        "",
        "🔮 *Tomorrow's Game Plan*",
        f"  1\\. *NVDA* — hold {code('$850')} pivot; add on heavy volume",
        f"  2\\. *PLTR* EP follow\\-through above {code('$24.50')}",
        # '>' in plain text — must be escaped
        f"  3\\. *CEG* nuclear — buy dip if SPY \\> 20MA",
        # BRK_B: esc() converts the '_' to '\_'
        f"  4\\. *{esc('BRK_B')}* flat base — low conviction \\(skip if VIX \\> 25\\)",
        f"  5\\. *RKLB* volatile — size at {code('½ position')} only",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"_Generated by Thematic Scanner \\| {date_s}_",
    ]

    return "\n".join(L)


# ── Self-correction diagnostics ───────────────────────────────────────────────

def _diagnose_400(response_json: dict, message: str) -> None:
    """
    Parse Telegram's 400 Bad Request response and pinpoint exactly what
    broke the MarkdownV2 parser so you can fix it without guessing.
    """
    description = response_json.get("description", "No description returned")

    print()
    print("═" * 64)
    print("  TELEGRAM 400 — SELF-CORRECTION REPORT")
    print("═" * 64)
    print(f"  Telegram error  : {description}")
    print()

    # ── 1. Extract byte offset reported by Telegram ───────────────────
    offset_match = re.search(r"byte offset (\d+)", description)
    if offset_match:
        offset        = int(offset_match.group(1))
        ctx_start     = max(0, offset - 40)
        ctx_end       = min(len(message), offset + 40)
        context       = repr(message[ctx_start:ctx_end])
        bad_char      = repr(message[offset]) if offset < len(message) else "EOF"
        arrow_pad     = " " * (min(offset - ctx_start, 40) + 4)

        print(f"  Byte offset     : {offset}")
        print(f"  Offending char  : {bad_char}")
        print(f"  Context (±40)   : {context}")
        print(f"  {arrow_pad}^  ← here")
        print()

    # ── 2. Heuristic scan for unescaped specials ──────────────────────
    # MarkdownV2 special chars that must be escaped in regular text.
    # NOTE: '*', '_', '`' also appear here as valid formatting markers —
    # flagged instances may be intentional bold/italic/code spans.
    specials = set(r'_*[]()~`>#+\-=|{}.!\/')
    issues   = []
    for i, ch in enumerate(message):
        if ch in specials and (i == 0 or message[i - 1] != "\\"):
            ctx = repr(message[max(0, i - 15): i + 15])
            issues.append((i, ch, ctx))

    print(f"  Heuristic scan  : {len(issues)} unescaped special char(s) found")
    print(f"  (Note: '*', '_', '`' flagged here may be valid formatting markers)")
    if issues:
        print()
        print(f"  {'Offset':>7}  {'Char':>4}  Context")
        print(f"  {'------':>7}  {'----':>4}  -------")
        for off, ch, ctx in issues[:30]:
            print(f"  {off:>7}  {ch!r:>4}  {ctx}")
        if len(issues) > 30:
            print(f"  ... and {len(issues) - 30} more")
    else:
        print("  No unescaped specials found — error may be structural")
        print("  (unbalanced *bold* / _italic_ markers, or nested entities)")

    print()
    print("  Quick-fix recipe:")
    print(r"    import re")
    print(r"    _SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')")
    print(r"    escaped  = _SPECIAL.sub(r'\\\1', raw_text)")
    print()
    print("  Common MDv2 footguns:")
    print("    • '&' is NOT special — \\& causes a 400, use & directly")
    print("    • Inside `code spans`, only ` and \\ need escaping")
    print("    • Unbalanced *bold* or _italic_ markers across a chunk boundary")
    print("    • Nested entities (bold inside italic) are not allowed")
    print("═" * 64)
    print()


# ── Telegram sender ───────────────────────────────────────────────────────────

def test_telegram_integration(
    bot_token: str = TELEGRAM_BOT_TOKEN,
    chat_id:   str = TELEGRAM_CHAT_ID,
    dry_run:   bool = False,
) -> bool:
    """
    Generate the mock brief and send it to Telegram via MarkdownV2.

    Returns True if all chunks were delivered, False on any failure.
    """
    message = generate_mock_brief()

    print("=" * 64)
    print("  TELEGRAM INTEGRATION STRESS TEST")
    print("=" * 64)
    print(f"  Message length  : {len(message):,} chars")
    print(f"  Parse mode      : MarkdownV2")
    print(f"  Dry run         : {dry_run}")
    print("=" * 64)

    if dry_run:
        print()
        print("── DRY RUN OUTPUT ──────────────────────────────────────────────")
        print(message)
        print("── END ─────────────────────────────────────────────────────────")
        return True

    # Split at section boundary (━━━ divider) if message exceeds Telegram's
    # 4096-char limit — splitting mid-entity causes its own 400 errors.
    MAX_CHARS = 4096
    if len(message) <= MAX_CHARS:
        chunks = [message]
    else:
        # Try to split at the second ━━━ divider (between pre/post-market)
        divider = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        parts   = message.split(divider)
        chunks  = []
        current = ""
        for part in parts:
            candidate = current + (divider if current else "") + part
            if len(candidate) <= MAX_CHARS:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = part
        if current:
            chunks.append(current)
        # Final safety: hard-split any chunk still over the limit
        safe = []
        for chunk in chunks:
            while len(chunk) > MAX_CHARS:
                split_at = chunk.rfind("\n", 0, MAX_CHARS)
                if split_at == -1:
                    split_at = MAX_CHARS
                safe.append(chunk[:split_at])
                chunk = chunk[split_at:].lstrip("\n")
            safe.append(chunk)
        chunks = safe

    print(f"\n  Sending {len(chunks)} chunk(s) to chat_id={chat_id} ...\n")

    all_ok = True
    for idx, chunk in enumerate(chunks, 1):
        payload = {
            "chat_id":    chat_id,
            "text":       chunk,
            "parse_mode": "MarkdownV2",
        }
        try:
            resp = requests.post(_api_url(bot_token), json=payload, timeout=15)
            data = resp.json()

            if resp.status_code == 200 and data.get("ok"):
                msg_id = data["result"]["message_id"]
                print(f"  ✅  Chunk {idx}/{len(chunks)} sent  (message_id={msg_id})")
            else:
                print(f"  ❌  Chunk {idx}/{len(chunks)} FAILED  HTTP {resp.status_code}")
                if resp.status_code == 400:
                    _diagnose_400(data, chunk)
                else:
                    print(f"      Response: {data}")
                all_ok = False

        except requests.exceptions.ConnectionError:
            print(f"  ❌  Chunk {idx}: connection error — check your network / bot token")
            all_ok = False
        except requests.exceptions.Timeout:
            print(f"  ❌  Chunk {idx}: request timed out after 15s")
            all_ok = False
        except requests.exceptions.RequestException as exc:
            print(f"  ❌  Chunk {idx}: {exc}")
            all_ok = False

    print()
    if all_ok:
        print("  ✅  All chunks delivered successfully.")
    else:
        print("  ❌  One or more chunks failed — see self-correction report above.")

    return all_ok


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Telegram MarkdownV2 stress test — realistic mock market brief",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python test_telegram.py --dry-run
  python test_telegram.py
  python test_telegram.py --token 123:ABC --chat-id -100123456
        """,
    )
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print the formatted message without sending")
    parser.add_argument("--token",    default=TELEGRAM_BOT_TOKEN,
                        help="Bot token (overrides TELEGRAM_BOT_TOKEN env var)")
    parser.add_argument("--chat-id",  default=TELEGRAM_CHAT_ID, dest="chat_id",
                        help="Chat ID (overrides TELEGRAM_CHAT_ID env var)")
    args = parser.parse_args()

    ok = test_telegram_integration(
        bot_token=args.token,
        chat_id=args.chat_id,
        dry_run=args.dry_run,
    )
    sys.exit(0 if ok else 1)
