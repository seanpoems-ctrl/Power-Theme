"""
etf_trendline_service.py — ETF 主題趨勢線突破／拉回偵測 (Part 1-3)

Algorithm:
  1. yfinance 抓 6 個月日線 OHLC（完整形態）
  2. Swing point 偵測（視窗 W=5，尾端放寬）
  3. 在所有 swing highs / lows 中枚舉 2~3 點組合，選 (touches_all, R²) 最高的線
     → 自動挑「最整齊的那段」，不強制從最早的點開始
  4. 延伸到最後一個交易日 → 判斷 breakout / near_resistance / near_support
  5. 診斷輸出 — 不寫 JSON，不動前端

禁用 SMA/EMA 做突破/拉回判斷。
"""

import json
import logging
import sys
from itertools import combinations as _comb
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
MIN_BARS        = 60    # 資料筆數下限（6 個月 ≈ 126 根）
ATR_TOUCH_TOL   = 0.5   # ±N×ATR 容許誤差，算作「觸及」線
MIN_TOUCH_COUNT = 2     # 至少幾個 swing 點觸及線才合格（含非定義點）
ATR_PERIOD      = 14    # ATR 計算天數
LOOKBACK_MONTHS = 6     # 回看月數（全形態）
COMBO_MAX_K     = 3     # 枚舉組合最大點數（2~3）

# Signal 門檻（純趨勢線，禁用 SMA）
BREAKOUT_MAX_DIST      = 0.04   # 0 < (close - resistance) / resistance ≤ 4%
NEAR_RESISTANCE_MAX_DIST = 0.02 # -2% ≤ (close - resistance) / resistance < 0（接近阻力）
SUPPORT_MAX_DIST       = 0.03   # 0 < (close - support)   / support    ≤ 3%


# ─── 載入白名單 ───────────────────────────────────────────────────────────────
def load_universe() -> list:
    p = Path(__file__).parent / "etf_universe.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ─── OHLC 抓取 ────────────────────────────────────────────────────────────────
def fetch_ohlc(ticker: str, months: int = LOOKBACK_MONTHS) -> "pd.DataFrame | None":  # noqa: E501
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


# ─── 最佳趨勢線：枚舉 2~3 點組合，選 (touches_all, R²) 最高的 ──────────────
def find_best_trendline(
    all_idx:    list[int],
    all_prices: list[float],
    atr:        float,
    min_k:      int = 2,
    max_k:      int = COMBO_MAX_K,
) -> "dict | None":
    """
    在所有 swing points 中枚舉 min_k ~ max_k 個點的組合，
    對每個組合做線性擬合，計算：
      - r2        : 組合點的內部擬合品質（2 點時恆為 1.0）
      - touches_all : 所有 swing points 中落在 ±ATR_TOUCH_TOL×ATR 的數量
    排序準則：(touches_all DESC, r2 DESC)
    只接受 touches_all ≥ MIN_TOUCH_COUNT 的組合。
    回傳最佳組合的 dict，或 None（沒有合格組合）。
    """
    n = len(all_idx)
    if n < min_k:
        return None

    tol     = ATR_TOUCH_TOL * atr
    all_x   = np.array(all_idx,    dtype=float)
    all_y   = np.array(all_prices, dtype=float)

    best_score  = (-1, -1.0)
    best_result = None

    for k in range(min_k, min(max_k + 1, n + 1)):
        for combo in _comb(range(n), k):
            xi = np.array([all_idx[i]    for i in combo], dtype=float)
            yi = np.array([all_prices[i] for i in combo], dtype=float)

            slope, intercept = np.polyfit(xi, yi, 1)

            # R² on defining points
            yi_pred = slope * xi + intercept
            ss_res  = float(np.sum((yi - yi_pred) ** 2))
            ss_tot  = float(np.sum((yi - np.mean(yi)) ** 2))
            r2      = 1.0 - ss_res / ss_tot if ss_tot > 1e-10 else 1.0

            # touches_all — validate against every swing point in the universe
            all_pred    = slope * all_x + intercept
            touches_all = int(np.sum(np.abs(all_y - all_pred) <= tol))

            if touches_all < MIN_TOUCH_COUNT:
                continue

            score = (touches_all, r2)
            if score > best_score:
                best_score  = score
                best_result = {
                    "slope":      float(slope),
                    "intercept":  float(intercept),
                    "r2":         float(r2),
                    "touches":    touches_all,
                    "combo_pos":  list(combo),          # 在 all_idx 裡的位置
                    "combo_bars": [int(all_idx[i]) for i in combo],
                    "x_start":    int(xi[0]),
                    "x_end":      int(xi[-1]),
                }

    return best_result


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

    # ── 3. 枚舉最佳趨勢線 ───────────────────────────────────────────────────
    sh_prices = [highs[i] for i in sh_idx]
    sl_prices = [lows[i]  for i in sl_idx]

    res_fit = find_best_trendline(sh_idx, sh_prices, atr) if len(sh_idx) >= 2 else None
    sup_fit = find_best_trendline(sl_idx, sl_prices, atr) if len(sl_idx) >= 2 else None

    # ── 4. 品質驗證：find_best_trendline 已確保 touches ≥ MIN_TOUCH_COUNT ──
    res_ok = res_fit is not None
    sup_ok = sup_fit is not None

    if not res_ok and not sup_ok:
        reasons = []
        for label, idx_list in [("阻力線", sh_idx), ("支撐線", sl_idx)]:
            if len(idx_list) < 2:
                reasons.append(f"{label}: swing 點不足 ({len(idx_list)} < 2)")
            else:
                reasons.append(f"{label}: 無 {MIN_TOUCH_COUNT} 個以上觸及點的組合")
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
    # 優先順序：breakout > near_resistance（阻力線相關）；near_support 獨立判斷
    signal    = None
    dist_pct  = None
    line_ref  = None  # 用哪條線觸發

    if res_ok and resistance_today is not None and resistance_today > 0:
        dist_res = (close_now - resistance_today) / resistance_today
        if 0 < dist_res <= BREAKOUT_MAX_DIST:
            signal   = "breakout"
            dist_pct = dist_res * 100
            line_ref = "resistance"
        elif -NEAR_RESISTANCE_MAX_DIST <= dist_res < 0:
            signal   = "near_resistance"
            dist_pct = dist_res * 100   # 負值，代表還在線下方
            line_ref = "resistance"

    if signal is None and sup_ok and support_today is not None and support_today > 0:
        dist_sup = (close_now - support_today) / support_today
        if 0 < dist_sup <= SUPPORT_MAX_DIST:
            signal   = "near_support"
            dist_pct = dist_sup * 100
            line_ref = "support"

    # ── Debug 詳細輸出 ───────────────────────────────────────────────────────
    if debug:
        print(f"\n  趨勢線擬合結果（最佳組合）:")
        tol = ATR_TOUCH_TOL * atr
        for label, fit, ok, today_val, all_bar_idx, all_prices_list in [
            ("阻力線(Resistance)", res_fit, res_ok, resistance_today, sh_idx, sh_prices),
            ("支撐線(Support)",    sup_fit, sup_ok, support_today,    sl_idx, sl_prices),
        ]:
            if fit is None:
                if label.startswith("阻力"):
                    print(f"    {label}: 無合格組合（swing 高點 {len(sh_idx)} 個，均無 {MIN_TOUCH_COUNT} 觸及）")
                else:
                    print(f"    {label}: 無合格組合（swing 低點 {len(sl_idx)} 個，均無 {MIN_TOUCH_COUNT} 觸及）")
                continue

            x0, x1 = fit["x_start"], fit["x_end"]
            y0, y1  = line_y(fit, x0), line_y(fit, x1)
            d0, d1  = dates[x0].date(), dates[x1].date()

            # 勝出組合的定義點
            combo_bars   = fit["combo_bars"]
            combo_dates  = [dates[bi].date() for bi in combo_bars]
            combo_prices = [highs[bi] if label.startswith("阻力") else lows[bi]
                            for bi in combo_bars]

            print(f"    {label}  [勝出組合 {len(combo_bars)} 點, R²={fit['r2']:.4f}, touches={fit['touches']}]")
            print(f"      勝出組合定義點（實際價格）:")
            for bi, dp, pr in zip(combo_bars, combo_dates, combo_prices):
                pred = line_y(fit, bi)
                err  = pr - pred
                print(f"        ★ bar[{bi:3d}]  {dp}  price={pr:.4f}  line={pred:.4f}  err={err:+.4f}")

            print(f"      端點 A（線性延伸）: ({d0}, {y0:.4f})  [bar {x0}]")
            print(f"      端點 B（線性延伸）: ({d1}, {y1:.4f})  [bar {x1}]")
            print(f"      斜率={fit['slope']:.6f}  截距={fit['intercept']:.4f}")

            print(f"      全部 swing 點 vs 線（用來算 touches）:")
            for bi, pr in zip(all_bar_idx, all_prices_list):
                pred = line_y(fit, bi)
                err  = pr - pred
                mark = "✓ touch" if abs(err) <= tol else f"  gap={err:+.3f}"
                is_combo = "★" if bi in combo_bars else " "
                print(f"       {is_combo}  bar[{bi:3d}]  {dates[bi].date()}  "
                      f"price={pr:.4f}  line={pred:.4f}  {mark}")

            if today_val is not None:
                print(f"      → 延伸至最後一根 [{last_date.date()} bar {last_i}]: {today_val:.4f}")

        print(f"\n  收盤價 [{last_date.date()}]: {close_now:.4f}")
        if resistance_today is not None and res_ok:
            dist_r = (close_now - resistance_today) / resistance_today * 100
            direct = "在線上方 ↑" if dist_r > 0 else "在線下方 ↓"
            print(f"  距阻力線: {dist_r:+.2f}%  ({direct})")
        if support_today is not None and sup_ok:
            dist_s = (close_now - support_today) / support_today * 100
            direct = "在線上方 ↑" if dist_s > 0 else "在線下方 ↓"
            print(f"  距支撐線: {dist_s:+.2f}%  ({direct})")
        sig_str  = signal or "（無訊號）"
        dist_str = f"{dist_pct:+.2f}%" if dist_pct is not None else "—"
        print(f"  形態: {pattern}  →  訊號: {sig_str}  dist={dist_str}")
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
          f"  MIN_TOUCH={MIN_TOUCH_COUNT}  回看={LOOKBACK_MONTHS}個月  最佳{COMBO_MAX_K}點組合")
    print(f"  🟢 Breakout       : 0 ~ +{BREAKOUT_MAX_DIST*100:.0f}% 超阻力線")
    print(f"  🔵 Near_Resistance: -{NEAR_RESISTANCE_MAX_DIST*100:.0f}% ~ 0  距阻力線（快突破）")
    print(f"  🟡 Near_Support   : 0 ~ +{SUPPORT_MAX_DIST*100:.0f}% 超支撐線（逢低布局）")
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
        hdr = (f"  {'Sig':16s} {'ETF':6s} {'Theme':14s} {'Pattern':22s}"
               f" {'Close':>8s} {'Resist':>8s} {'Support':>8s} {'Dist%':>7s}")
        print(hdr)
        print(f"  {'─'*86}")
        for s in signals:
            sig = s["signal"]
            if sig == "breakout":
                tag = "🟢 BREAKOUT      "
            elif sig == "near_resistance":
                tag = "🔵 NEAR_RESIST   "
            else:
                tag = "🟡 NEAR_SUPPORT  "
            res_s = f"{s['resistance_today']:.3f}" if s["resistance_today"] is not None else "      —"
            sup_s = f"{s['support_today']:.3f}"    if s["support_today"]    is not None else "      —"
            dist  = s['dist_pct']
            print(f"  {tag} {s['ticker']:6s} {s['theme']:14s} {s['pattern']:22s}"
                  f" {s['close']:8.3f} {res_s:>8s} {sup_s:>8s} {dist:+7.2f}%")
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
