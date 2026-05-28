"""
etf_trendline_service.py — ETF 主題趨勢線突破／拉回偵測 (Part 1-3)

Algorithm:
  1. yfinance 抓 6 個月日線 OHLC
  2. Swing point 偵測（視窗 W=5，尾端放寬）
  3. 對 swing highs / lows 各做線性迴歸 → 阻力線 / 支撐線
  4. 品質驗證（touches ≥ 2，R² ≥ R2_MIN）
  5. 延伸到最後一個交易日 → 判斷 breakout / near_support
  6. 診斷輸出 — 不寫 JSON，不動前端

禁用 SMA/EMA 做突破/拉回判斷。
"""

import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ─── 可調參數 ─────────────────────────────────────────────────────────────────
SWING_WINDOW    = 5     # W — swing point 偵測左右各看幾根
MIN_BARS        = 60    # 資料筆數下限；不足直接淘汰
ATR_TOUCH_TOL   = 0.5   # ±N×ATR 容許誤差，算作「觸及」線
MIN_TOUCH_COUNT = 2     # 至少幾個 swing 點觸及線才合格
R2_MIN          = 0.0   # R² 下限（第一版寬鬆，≥0 即可）
ATR_PERIOD      = 14    # ATR 計算天數

# Signal 門檻（純趨勢線，禁用 SMA）
BREAKOUT_MAX_DIST = 0.04   # 0 < (close - resistance) / resistance ≤ 4%
SUPPORT_MAX_DIST  = 0.03   # 0 < (close - support)   / support    ≤ 3%


# ─── 載入白名單 ───────────────────────────────────────────────────────────────
def load_universe() -> list:
    p = Path(__file__).parent / "etf_universe.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ─── OHLC 抓取 ────────────────────────────────────────────────────────────────
def fetch_ohlc(ticker: str, months: int = 6) -> "pd.DataFrame | None":
    try:
        end   = datetime.today()
        start = end - timedelta(days=months * 31 + 10)  # 多抓幾天確保夠 bars
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )
        if df is None or df.empty:
            return None
        # 相容新版 yfinance 多層 column
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        return df
    except Exception as e:
        logger.error(f"{ticker}: fetch_ohlc failed — {e}")
        return None


# ─── ATR ──────────────────────────────────────────────────────────────────────
def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    high  = df["High"].values
    low   = df["Low"].values
    close = df["Close"].values
    if len(high) < 2:
        return float(np.nanmean(high - low)) if len(high) > 0 else 1.0
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:]  - close[:-1]),
        ),
    )
    window = min(period, len(tr))
    return float(np.nanmean(tr[-window:])) if window > 0 else 1.0


# ─── Swing Point 偵測 ─────────────────────────────────────────────────────────
def detect_swings(df: pd.DataFrame, W: int = SWING_WINDOW):
    """
    回傳 (swing_high_indices, swing_low_indices)。

    尾端修正：最後 W 根改用右側現有根數比較，確保最新的轉折點不被漏掉。
    嚴格內部（兩側各 W 根）用嚴格不等號 (>/<)。
    尾端（右側 < W）用非嚴格 (>=/<= )，避免漏掉收盤當天的轉折。
    """
    highs = df["High"].values
    lows  = df["Low"].values
    n     = len(highs)

    swing_high_idx: list[int] = []
    swing_low_idx:  list[int] = []

    for i in range(n):
        left_avail  = min(W, i)
        right_avail = min(W, n - 1 - i)
        is_tail     = (n - 1 - i) < W

        if left_avail == 0:
            continue  # 最左端，沒有左側鄰居，跳過

        if right_avail == 0:
            continue  # 最右端那根，沒有右側鄰居，跳過

        # ── Swing High ──────────────────────────────────────────────
        if is_tail:
            left_ok  = all(highs[i] >= highs[i - j] for j in range(1, left_avail  + 1))
            right_ok = all(highs[i] >= highs[i + j] for j in range(1, right_avail + 1))
        else:
            left_ok  = all(highs[i] > highs[i - j] for j in range(1, W + 1))
            right_ok = all(highs[i] > highs[i + j] for j in range(1, W + 1))

        if left_ok and right_ok:
            swing_high_idx.append(i)

        # ── Swing Low ───────────────────────────────────────────────
        if is_tail:
            left_ok  = all(lows[i] <= lows[i - j] for j in range(1, left_avail  + 1))
            right_ok = all(lows[i] <= lows[i + j] for j in range(1, right_avail + 1))
        else:
            left_ok  = all(lows[i] < lows[i - j] for j in range(1, W + 1))
            right_ok = all(lows[i] < lows[i + j] for j in range(1, W + 1))

        if left_ok and right_ok:
            swing_low_idx.append(i)

    return swing_high_idx, swing_low_idx


# ─── 線性迴歸 + 品質驗證 ──────────────────────────────────────────────────────
def fit_trendline(
    bar_indices: list[int],
    prices: list[float],
    atr: float,
) -> dict | None:
    """
    對 swing points 做線性迴歸。
    回傳 dict 含 slope, intercept, r2, touches, x_start, x_end；
    若點數 < 2 回傳 None。
    """
    if len(bar_indices) < 2:
        return None

    x = np.array(bar_indices, dtype=float)
    y = np.array(prices,      dtype=float)

    slope, intercept = np.polyfit(x, y, 1)

    y_pred = slope * x + intercept
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2     = 1.0 - ss_res / ss_tot if ss_tot > 1e-10 else 1.0

    tol     = ATR_TOUCH_TOL * atr
    touches = int(np.sum(np.abs(y - y_pred) <= tol))

    return {
        "slope":     float(slope),
        "intercept": float(intercept),
        "r2":        float(r2),
        "touches":   touches,
        "x_start":   int(x[0]),
        "x_end":     int(x[-1]),
    }


def line_y(fit: dict, x: int) -> float:
    return fit["slope"] * x + fit["intercept"]


# ─── 單一 ETF 分析 ────────────────────────────────────────────────────────────
def analyze_etf(ticker: str, theme: str, debug: bool = False) -> dict:
    """
    回傳 dict。
    若被淘汰：{"ticker": ..., "rejected": reason, ...}
    若有訊號：{"ticker": ..., "signal": "breakout"/"near_support", ...}
    若沒訊號但線合格：{"ticker": ..., "signal": None, ...}
    """
    base = {"ticker": ticker, "theme": theme, "rejected": None, "signal": None}

    # ── 1. 抓資料 ────────────────────────────────────────────────────────────
    df = fetch_ohlc(ticker)
    if df is None:
        return {**base, "rejected": "fetch failed"}
    if len(df) < MIN_BARS:
        return {**base, "rejected": f"資料不足 ({len(df)} bars < {MIN_BARS})"}

    atr    = calc_atr(df)
    dates  = df.index
    highs  = df["High"].values
    lows   = df["Low"].values
    closes = df["Close"].values
    n      = len(closes)

    # ── 2. Swing Points ──────────────────────────────────────────────────────
    sh_idx, sl_idx = detect_swings(df)

    if debug:
        print(f"\n{'='*65}")
        print(f"  ★ DEBUG: {ticker}  (theme={theme})  bars={n}  ATR={atr:.4f}")
        print(f"  {'─'*62}")
        print(f"  Swing Highs — {len(sh_idx)} 個:")
        for i in sh_idx:
            print(f"    bar[{i:3d}]  {dates[i].date()}  H = {highs[i]:.4f}")
        print(f"  Swing Lows  — {len(sl_idx)} 個:")
        for i in sl_idx:
            print(f"    bar[{i:3d}]  {dates[i].date()}  L = {lows[i]:.4f}")

    # ── 3. 擬合趨勢線 ────────────────────────────────────────────────────────
    res_fit = None
    if len(sh_idx) >= 2:
        res_fit = fit_trendline(sh_idx, [highs[i] for i in sh_idx], atr)

    sup_fit = None
    if len(sl_idx) >= 2:
        sup_fit = fit_trendline(sl_idx, [lows[i]  for i in sl_idx], atr)

    # ── 4. 品質驗證 ──────────────────────────────────────────────────────────
    def quality_ok(fit):
        return (fit is not None
                and fit["r2"]      >= R2_MIN
                and fit["touches"] >= MIN_TOUCH_COUNT)

    res_ok = quality_ok(res_fit)
    sup_ok = quality_ok(sup_fit)

    if not res_ok and not sup_ok:
        reasons = []
        for label, fit in [("阻力線", res_fit), ("支撐線", sup_fit)]:
            if fit is None:
                reasons.append(f"{label}: swing 點不足 (<2)")
            elif fit["r2"] < R2_MIN:
                reasons.append(f"{label}: R²={fit['r2']:.2f} < {R2_MIN}")
            elif fit["touches"] < MIN_TOUCH_COUNT:
                reasons.append(f"{label}: 觸及點 {fit['touches']} < {MIN_TOUCH_COUNT}")
        return {**base, "rejected": " | ".join(reasons)}

    # ── 5. 延伸到最後一根（最後一個交易日）────────────────────────────────────
    last_i      = n - 1
    last_date   = dates[last_i]
    close_now   = float(closes[last_i])

    resistance_today = line_y(res_fit, last_i) if res_ok else None
    support_today    = line_y(sup_fit, last_i) if sup_ok else None

    # ── 6. 形態分類 ──────────────────────────────────────────────────────────
    if res_ok and sup_ok:
        rs, ss = res_fit["slope"], sup_fit["slope"]
        if rs < 0 and ss > 0:
            pattern = "triangle_converging"
        elif rs > 0 and ss < 0:
            pattern = "triangle_expanding"
        elif rs > 0 and ss > 0:
            pattern = "channel_up"
        else:
            pattern = "channel_down"
    elif res_ok:
        pattern = "resistance_only"
    else:
        pattern = "support_only"

    # ── 7. 突破 / 拉回判斷（純趨勢線，禁用 SMA）────────────────────────────
    signal    = None
    dist_pct  = None
    line_ref  = None  # 用哪條線觸發

    if res_ok and resistance_today is not None and resistance_today > 0:
        dist_res = (close_now - resistance_today) / resistance_today
        if 0 < dist_res <= BREAKOUT_MAX_DIST:
            signal   = "breakout"
            dist_pct = dist_res * 100
            line_ref = "resistance"

    if signal is None and sup_ok and support_today is not None and support_today > 0:
        dist_sup = (close_now - support_today) / support_today
        if 0 < dist_sup <= SUPPORT_MAX_DIST:
            signal   = "near_support"
            dist_pct = dist_sup * 100
            line_ref = "support"

    # ── Debug 詳細輸出 ───────────────────────────────────────────────────────
    if debug:
        print(f"\n  趨勢線擬合結果:")
        for label, fit, ok, today_val in [
            ("阻力線(Resistance)", res_fit, res_ok, resistance_today),
            ("支撐線(Support)",    sup_fit, sup_ok, support_today),
        ]:
            if fit is None:
                print(f"    {label}: 無法擬合（swing 點 < 2）")
                continue
            x0, x1       = fit["x_start"], fit["x_end"]
            y0, y1        = line_y(fit, x0), line_y(fit, x1)
            d0, d1        = dates[x0].date(), dates[x1].date()
            status        = "✓ 合格" if ok else "✗ 淘汰"
            print(f"    {label} [{status}]")
            print(f"      端點 A: ({d0}, {y0:.4f})  [bar {x0}]")
            print(f"      端點 B: ({d1}, {y1:.4f})  [bar {x1}]")
            print(f"      斜率={fit['slope']:.6f}  截距={fit['intercept']:.4f}"
                  f"  R²={fit['r2']:.4f}  觸及={fit['touches']}")
            if ok and today_val is not None:
                print(f"      → 延伸至最後一根 [{last_date.date()} bar {last_i}]: {today_val:.4f}")

        print(f"\n  收盤價 [{last_date.date()}]: {close_now:.4f}")
        if resistance_today is not None:
            dist_r = (close_now - resistance_today) / resistance_today * 100
            print(f"  距阻力線: {dist_r:+.2f}%  ({'在線上方' if dist_r > 0 else '在線下方'})")
        if support_today is not None:
            dist_s = (close_now - support_today) / support_today * 100
            print(f"  距支撐線: {dist_s:+.2f}%  ({'在線上方' if dist_s > 0 else '在線下方'})")
        print(f"  形態: {pattern}  →  訊號: {signal or '（無訊號）'}  dist={f'{dist_pct:.2f}%' if dist_pct else '—'}")
        print(f"{'='*65}")

    return {
        "ticker":           ticker,
        "theme":            theme,
        "pattern":          pattern,
        "signal":           signal,
        "dist_pct":         dist_pct,
        "line_ref":         line_ref,
        "close":            close_now,
        "resistance_today": resistance_today,
        "support_today":    support_today,
        "res_fit":          res_fit if res_ok else None,
        "sup_fit":          sup_fit if sup_ok else None,
        "last_date":        str(last_date.date()),
        "rejected":         None,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    universe = load_universe()

    print(f"\n{'='*65}")
    print(f"  ETF 趨勢線掃描器  ({len(universe)} ETFs in universe)")
    print(f"  W={SWING_WINDOW}  MIN_BARS={MIN_BARS}  ATR_TOL=±{ATR_TOUCH_TOL}×ATR"
          f"  MIN_TOUCH={MIN_TOUCH_COUNT}  R²≥{R2_MIN}")
    print(f"  Breakout 門檻: 0~+{BREAKOUT_MAX_DIST*100:.0f}% 超阻力線")
    print(f"  Support  門檻: 0~+{SUPPORT_MAX_DIST*100:.0f}% 超支撐線")
    print(f"{'='*65}")

    signals:  list[dict] = []
    no_signal: list[dict] = []
    rejected: list[tuple] = []

    for entry in universe:
        ticker = entry["ticker"]
        theme  = entry["theme"]
        is_debug = (ticker == "DRNZ")  # 只有 DRNZ 印詳細 debug

        try:
            result = analyze_etf(ticker, theme, debug=is_debug)
        except Exception as e:
            logger.error(f"{ticker}: 意外錯誤 — {e}", exc_info=True)
            result = {"ticker": ticker, "theme": theme, "rejected": f"意外錯誤: {e}", "signal": None}

        if result["rejected"]:
            rejected.append((ticker, theme, result["rejected"]))
        elif result["signal"]:
            signals.append(result)
        else:
            no_signal.append(result)

    # ── 輸出：有訊號 ────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  📊 有訊號的 ETF（{len(signals)} 支）")
    print(f"{'─'*65}")
    if signals:
        hdr = f"  {'Sig':12s} {'ETF':6s} {'Theme':14s} {'Pattern':24s}"  \
              f" {'Close':>8s} {'Resist':>8s} {'Support':>8s} {'Dist%':>7s}"
        print(hdr)
        print(f"  {'─'*60}")
        for s in signals:
            tag   = "🟢 BREAKOUT   " if s["signal"] == "breakout" else "🟡 NEAR_SUPPORT"
            res_s = f"{s['resistance_today']:.3f}" if s["resistance_today"] is not None else "    —   "
            sup_s = f"{s['support_today']:.3f}"    if s["support_today"]    is not None else "    —   "
            print(f"  {tag} {s['ticker']:6s} {s['theme']:14s} {s['pattern']:24s}"
                  f" {s['close']:8.3f} {res_s:>8s} {sup_s:>8s} {s['dist_pct']:+6.2f}%")
    else:
        print("  （無）")

    # ── 輸出：線合格但無訊號 ────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  ⚪ 線合格、但目前無突破/拉回訊號（{len(no_signal)} 支）")
    print(f"{'─'*65}")
    for s in no_signal:
        res_s = f"{s['resistance_today']:.3f}" if s.get("resistance_today") is not None else "—"
        sup_s = f"{s['support_today']:.3f}"    if s.get("support_today")    is not None else "—"
        print(f"  {s['ticker']:6s} ({s['theme']:14s}) {s['pattern']:24s}"
              f"  close={s['close']:.3f}  res={res_s}  sup={sup_s}")

    # ── 輸出：被淘汰 ────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  ❌ 淘汰 / 跳過（{len(rejected)} 支）")
    print(f"{'─'*65}")
    for ticker, theme, reason in rejected:
        print(f"  {ticker:6s} ({theme:14s}) → {reason}")

    print(f"\n{'='*65}")
    print(f"  完成。signals={len(signals)}  no_signal={len(no_signal)}  rejected={len(rejected)}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
