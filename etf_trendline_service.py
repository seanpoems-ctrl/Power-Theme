"""
etf_trendline_service.py — ETF 主題趨勢線突破／拉回偵測 (Part 1-3)

Algorithm:
  1. yfinance 抓 6 個月日線 OHLC（完整形態）
  2. Swing point 偵測（視窗 W=5，尾端放寬）
  3. 外包絡線（convex hull）：阻力線必須壓在所有 close 上方，支撐線必須撐在所有 close 下方
     → 以最高 swing high / 最低 swing low 為錨點，找時間跨度最大的合格兩點組合
  4. 延伸到最後一個交易日 → 判斷 breakout / near_resistance / near_support
  5. 診斷輸出 — 不寫 JSON，不動前端

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
SWING_WINDOW      = 5     # W — swing point 偵測左右各看幾根
MIN_BARS          = 60    # 資料筆數下限（6 個月 ≈ 126 根）
ATR_TOUCH_TOL     = 0.5   # ±N×ATR 容許誤差，算作「觸及」線
MIN_TOUCH_COUNT   = 2     # 至少幾個 swing 點觸及線才合格
ATR_PERIOD        = 14    # ATR 計算天數
LOOKBACK_MONTHS   = 6     # 回看月數（全形態）
ENVELOPE_CLOSE_TOL = 0.5  # 外包絡線容差：bar high/low 突出線外 ≤ N×ATR 算可接受（與 ATR_TOUCH_TOL 統一）
MAX_P2_AGE_BARS    = 30   # P2（線右端點）距今最多幾根 K 棒，超過視為過時的線

# Signal 門檻（純趨勢線，禁用 SMA）
BREAKOUT_MAX_DIST        = 0.04  # 0 < (close - resistance) / resistance ≤ 4%
NEAR_RESISTANCE_MAX_DIST = 0.02  # -2% ≤ (close - resistance) / resistance < 0
SUPPORT_MAX_DIST         = 0.03  # 0 < (close - support)   / support    ≤ 3%
BREAKOUT_SCAN_BARS       = 5     # 往回掃幾根找突破日（0 或 1 天前才發訊號）


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
        # 確保 OHLC 是 1D Series（yfinance 偶發回傳 2D DataFrame column）
        for col in ["Open", "High", "Low", "Close"]:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]
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


# ─── 阻力線：P1~P2 區間內 bar high 不得突出，P2 後允許突破 ─────────────────
def find_upper_envelope(
    sh_idx:    list[int],
    sh_prices: list[float],
    highs:     np.ndarray,
    atr:       float,
) -> "dict | None":
    """
    阻力線算法：
    1. 枚舉所有 (P1, P2) swing high 組合（P1 在前）
    2. 驗證：P1~P2 區間內所有 bar high 不得超出線上方 ENVELOPE_CLOSE_TOL×ATR
       P2 之後的 bar 不驗證（允許突破）
    3. 品質分數 = touches×2 + span×0.05
       touches = P1~P2 區間內 swing high 距線 ≤ ATR_TOUCH_TOL×ATR 的數量
    4. 選分數最高的合格組合
    """
    n = len(sh_idx)
    if n < 2:
        return None

    tol_env   = ENVELOPE_CLOSE_TOL * atr
    tol_touch = ATR_TOUCH_TOL * atr
    last_i    = len(highs) - 1

    best_score  = -1.0
    best_result = None

    for i in range(n):
        for j in range(i + 1, n):
            p1_x, p1_y = sh_idx[i], sh_prices[i]
            p2_x, p2_y = sh_idx[j], sh_prices[j]

            # P2 時效性過濾：P2 距今超過 MAX_P2_AGE_BARS 根視為過時的線
            if last_i - p2_x > MAX_P2_AGE_BARS:
                continue

            slope     = (p2_y - p1_y) / (p2_x - p1_x)
            intercept = p1_y - slope * p1_x

            # 驗證 P1~P2 區間（含端點）所有 bar high ≤ 線 + tol_env
            valid = True
            for k in range(p1_x, p2_x + 1):
                if highs[k] > slope * k + intercept + tol_env:
                    valid = False
                    break
            if not valid:
                continue

            # 計算 P1~P2 區間內 swing high 觸及數
            touches = sum(
                1 for idx, price in zip(sh_idx, sh_prices)
                if p1_x <= idx <= p2_x
                and abs(price - (slope * idx + intercept)) <= tol_touch
            )
            if touches < MIN_TOUCH_COUNT:
                continue

            span  = p2_x - p1_x
            score = touches * 2 + span * 0.05
            if score > best_score:
                best_score  = score
                best_result = {
                    "slope":      float(slope),
                    "intercept":  float(intercept),
                    "r2":         1.0,
                    "touches":    touches,
                    "combo_pos":  [i, j],
                    "combo_bars": [p1_x, p2_x],
                    "x_start":    p1_x,
                    "x_end":      p2_x,
                }

    return best_result


# ─── 支撐線：P1~P2 區間內 bar low 不得跌穿，P2 後允許跌破 ──────────────────
def find_lower_envelope(
    sl_idx:    list[int],
    sl_prices: list[float],
    lows:      np.ndarray,
    atr:       float,
) -> "dict | None":
    """
    支撐線算法（與阻力線完全鏡像）：
    1. 枚舉所有 (P1, P2) swing low 組合（P1 在前）
    2. 驗證：P1~P2 區間內所有 bar low 不得低於線下方 ENVELOPE_CLOSE_TOL×ATR
       P2 之後的 bar 不驗證（允許跌破）
    3. P2 距今不得超過 MAX_P2_AGE_BARS 根（過時的線不採用）
    4. 品質分數 = touches×2 + span×0.05
    5. 選分數最高的合格組合
    """
    n = len(sl_idx)
    if n < 2:
        return None

    tol_env   = ENVELOPE_CLOSE_TOL * atr
    tol_touch = ATR_TOUCH_TOL * atr
    last_i    = len(lows) - 1

    best_score  = -1.0
    best_result = None

    for i in range(n):
        for j in range(i + 1, n):
            p1_x, p1_y = sl_idx[i], sl_prices[i]
            p2_x, p2_y = sl_idx[j], sl_prices[j]

            # P2 時效性過濾
            if last_i - p2_x > MAX_P2_AGE_BARS:
                continue

            slope     = (p2_y - p1_y) / (p2_x - p1_x)
            intercept = p1_y - slope * p1_x

            # 驗證 P1~P2 區間（含端點）所有 bar low ≥ 線 - tol_env
            valid = True
            for k in range(p1_x, p2_x + 1):
                if lows[k] < slope * k + intercept - tol_env:
                    valid = False
                    break
            if not valid:
                continue

            # 計算 P1~P2 區間內 swing low 觸及數
            touches = sum(
                1 for idx, price in zip(sl_idx, sl_prices)
                if p1_x <= idx <= p2_x
                and abs(price - (slope * idx + intercept)) <= tol_touch
            )
            if touches < MIN_TOUCH_COUNT:
                continue

            span  = p2_x - p1_x
            score = touches * 2 + span * 0.05
            if score > best_score:
                best_score  = score
                best_result = {
                    "slope":      float(slope),
                    "intercept":  float(intercept),
                    "r2":         1.0,
                    "touches":    touches,
                    "combo_pos":  [i, j],
                    "combo_bars": [p1_x, p2_x],
                    "x_start":    p1_x,
                    "x_end":      p2_x,
                }

    return best_result


def line_y(fit: dict, x: int) -> float:
    return fit["slope"] * x + fit["intercept"]


def find_breakout_day(
    closes: np.ndarray,
    last_i: int,
    res_fit: dict,
    scan_bars: int = BREAKOUT_SCAN_BARS,
) -> "tuple[int, int] | tuple[None, None]":
    """
    往回最多掃 scan_bars 根 K 棒，找「第一根收盤 > 當天阻力線值」的 bar。

    回傳 (breakout_bar_index, days_since)。
    days_since = last_i - breakout_bar_index（0 = 今天, 1 = 昨天, …）。
    若窗口內完全沒有收盤站上阻力線，回傳 (None, None)。
    """
    scan_start = max(0, last_i - scan_bars + 1)
    for j in range(scan_start, last_i + 1):
        if closes[j] > line_y(res_fit, j):
            return j, last_i - j
    return None, None


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

    # ── 3. 外包絡線：阻力線壓上方，支撐線撐下方 ────────────────────────────
    sh_prices = [highs[i] for i in sh_idx]
    sl_prices = [lows[i]  for i in sl_idx]

    res_fit = find_upper_envelope(sh_idx, sh_prices, highs, atr) if len(sh_idx) >= 2 else None
    sup_fit = find_lower_envelope(sl_idx, sl_prices, lows,  atr) if len(sl_idx) >= 2 else None

    # ── 4. 品質驗證：外包絡線函數已確保 touches ≥ MIN_TOUCH_COUNT ──────────
    res_ok = res_fit is not None
    sup_ok = sup_fit is not None

    if not res_ok and not sup_ok:
        reasons = []
        for label, idx_list in [("阻力線", sh_idx), ("支撐線", sl_idx)]:
            if len(idx_list) < 2:
                reasons.append(f"{label}: swing 點不足 ({len(idx_list)} < 2)")
            else:
                reasons.append(f"{label}: 外包絡線無合格兩點組合（touches<{MIN_TOUCH_COUNT} 或 close 突出）")
        return {**base, "rejected": " | ".join(reasons)}

    # ── 5. 延伸到最後一根（最後一個交易日）────────────────────────────────────
    last_i    = n - 1
    last_date = dates[last_i]
    close_now = float(closes[last_i])

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

    # ── [過濾 A] 單線參考：只有一條線，不發訊號 ─────────────────────────────
    if not (res_ok and sup_ok):
        res_s = f"{resistance_today:.3f}" if resistance_today else "—"
        sup_s = f"{support_today:.3f}"    if support_today    else "—"
        if debug:
            print(f"\n  ⚠ 單線（{pattern}）：不發訊號，供參考。"
                  f"  res={res_s}  sup={sup_s}")
            print(f"{'='*65}")
        return {
            **base,
            "pattern":          pattern,
            "reference_only":   True,
            "close":            close_now,
            "resistance_today": resistance_today,
            "support_today":    support_today,
            "last_date":        str(last_date.date()),
        }

    # ── [過濾 B] 通道顛倒：支撐線值 >= 阻力線值 ────────────────────────────
    if support_today >= resistance_today:
        reason = (f"通道顛倒 support({support_today:.2f}) >= "
                  f"resistance({resistance_today:.2f})")
        if debug:
            print(f"\n  ✗ {reason}")
            print(f"{'='*65}")
        return {**base, "rejected": reason}

    # ── 7. 突破 / 拉回判斷（純趨勢線，禁用 SMA）────────────────────────────
    signal        = None
    dist_pct      = None
    line_ref      = None
    breakout_date = None   # 只在 signal=="breakout" 時有值

    # ── [Breakout] 突破後第 0~1 天才發訊號 ──────────────────────────────────
    if resistance_today is not None and resistance_today > 0:
        dist_res = (close_now - resistance_today) / resistance_today
        if 0 < dist_res <= BREAKOUT_MAX_DIST:
            # 找突破日
            bo_bar, days_since = find_breakout_day(closes, last_i, res_fit)
            if bo_bar is not None and days_since <= 1:
                signal        = "breakout"
                dist_pct      = dist_res * 100
                line_ref      = "resistance"
                breakout_date = str(dates[bo_bar].date())
            # days_since >= 2 → 突破太久，不發訊號（近距離當作 near_resistance 處理）
        if signal is None and -NEAR_RESISTANCE_MAX_DIST <= dist_res < 0:
            signal   = "near_resistance"
            dist_pct = dist_res * 100
            line_ref = "resistance"

    # ── [Near Support] 接近支撐 ──────────────────────────────────────────────
    if signal is None and support_today is not None and support_today > 0:
        dist_sup = (close_now - support_today) / support_today
        if 0 < dist_sup <= SUPPORT_MAX_DIST:
            signal   = "near_support"
            dist_pct = dist_sup * 100
            line_ref = "support"

    # ── Debug 詳細輸出 ───────────────────────────────────────────────────────
    if debug:
        print(f"\n  外包絡線結果（錨點+最大時間跨度，所有 close 在線外側）:")
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

            print(f"    {label}  [外包絡線 {len(combo_bars)} 點, touches={fit['touches']}, tol={ENVELOPE_CLOSE_TOL}×ATR={ENVELOPE_CLOSE_TOL*atr:.4f}]")
            print(f"      錨點 + 配對點（實際價格）:")
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
        if resistance_today is not None:
            dist_r = (close_now - resistance_today) / resistance_today * 100
            direct = "在線上方 ↑" if dist_r > 0 else "在線下方 ↓"
            print(f"  距阻力線: {dist_r:+.2f}%  ({direct})")
            # 印出突破掃描
            bo_bar, days_since = find_breakout_day(closes, last_i, res_fit)
            if bo_bar is not None:
                print(f"  突破掃描: 最近站上阻力線 = {dates[bo_bar].date()}"
                      f"  ({days_since} 天前)  →  {'✓ 有效（≤1天）' if days_since <= 1 else '✗ 過舊（>1天）'}")
            else:
                print(f"  突破掃描: 近 {BREAKOUT_SCAN_BARS} 根內未站上阻力線")
        if support_today is not None:
            dist_s = (close_now - support_today) / support_today * 100
            direct = "在線上方 ↑" if dist_s > 0 else "在線下方 ↓"
            print(f"  距支撐線: {dist_s:+.2f}%  ({direct})")
        if resistance_today is not None and support_today is not None:
            if support_today >= resistance_today:
                print(f"  ✗ 通道顛倒：support({support_today:.2f}) >= resistance({resistance_today:.2f})")
        sig_str  = signal or "（無訊號）"
        dist_str = f"{dist_pct:+.2f}%" if dist_pct is not None else "—"
        bo_str   = f"  breakout_day={breakout_date}" if breakout_date else ""
        print(f"  形態: {pattern}  →  訊號: {sig_str}  dist={dist_str}{bo_str}")
        print(f"{'='*65}")

    # ── 觸及點明細（供 TradingView 比對用）───────────────────────────────────
    tol = ATR_TOUCH_TOL * atr

    def touch_pts(fit, bar_idx_list, price_list, date_list):
        """回傳所有觸及此線的 swing 點，格式 [(date_str, price), ...]"""
        pts = []
        for bi, pr in zip(bar_idx_list, price_list):
            if abs(pr - line_y(fit, bi)) <= tol:
                pts.append((str(date_list[bi].date()), round(float(pr), 4)))
        return pts

    res_touch_pts = (touch_pts(res_fit, sh_idx, sh_prices, dates)
                     if res_fit is not None else [])
    sup_touch_pts = (touch_pts(sup_fit, sl_idx, sl_prices, dates)
                     if sup_fit is not None else [])

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
        "breakout_date":    breakout_date,
        "res_fit":          res_fit,
        "sup_fit":          sup_fit,
        "res_touch_pts":    res_touch_pts,
        "sup_touch_pts":    sup_touch_pts,
        "last_date":        str(last_date.date()),
        "rejected":         None,
        "reference_only":   False,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    universe = load_universe()

    print(f"\n{'='*65}")
    print(f"  ETF 趨勢線掃描器  ({len(universe)} ETFs in universe)")
    print(f"  W={SWING_WINDOW}  MIN_BARS={MIN_BARS}  ATR_TOUCH=±{ATR_TOUCH_TOL}×ATR"
          f"  ENVELOPE_TOL={ENVELOPE_CLOSE_TOL}×ATR  MIN_TOUCH={MIN_TOUCH_COUNT}  回看={LOOKBACK_MONTHS}個月")
    print(f"  🟢 Breakout       : 0 ~ +{BREAKOUT_MAX_DIST*100:.0f}% 超阻力線")
    print(f"  🔵 Near_Resistance: -{NEAR_RESISTANCE_MAX_DIST*100:.0f}% ~ 0  距阻力線（快突破）")
    print(f"  🟡 Near_Support   : 0 ~ +{SUPPORT_MAX_DIST*100:.0f}% 超支撐線（逢低布局）")
    print(f"{'='*65}")

    signals:    list[dict]  = []
    no_signal:  list[dict]  = []
    ref_only:   list[dict]  = []   # 單線，供參考
    rejected:   list[tuple] = []

    for entry in universe:
        ticker   = entry["ticker"]
        theme    = entry["theme"]
        is_debug = ticker in {"DRNZ", "PPA", "XBI", "IBB"}

        try:
            result = analyze_etf(ticker, theme, debug=is_debug)
        except Exception as e:
            logger.error(f"{ticker}: 意外錯誤 — {e}", exc_info=True)
            result = {"ticker": ticker, "theme": theme,
                      "rejected": f"意外錯誤: {e}", "signal": None}

        if result.get("rejected"):
            rejected.append((ticker, theme, result["rejected"]))
        elif result.get("reference_only"):
            ref_only.append(result)
        elif result.get("signal"):
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
                bo   = f"  breakout={s['breakout_date']}" if s.get("breakout_date") else ""
                tag  = "🟢 BREAKOUT      "
            elif sig == "near_resistance":
                bo  = ""
                tag = "🔵 NEAR_RESIST   "
            else:
                bo  = ""
                tag = "🟡 NEAR_SUPPORT  "
            res_s = f"{s['resistance_today']:.3f}" if s["resistance_today"] is not None else "      —"
            sup_s = f"{s['support_today']:.3f}"    if s["support_today"]    is not None else "      —"
            dist  = s["dist_pct"]
            print(f"  {tag} {s['ticker']:6s} {s['theme']:14s} {s['pattern']:22s}"
                  f" {s['close']:8.3f} {res_s:>8s} {sup_s:>8s} {dist:+7.2f}%{bo}")
    else:
        print("  （無）")

    # ── TradingView 比對段落 ─────────────────────────────────────────────────
    if signals:
        print(f"\n{'─'*65}")
        print(f"  📐 TradingView 手動比對資料")
        print(f"{'─'*65}")
        for s in signals:
            rf  = s.get("res_fit")
            sf  = s.get("sup_fit")
            r_t = s.get("res_touch_pts", [])
            s_t = s.get("sup_touch_pts", [])

            print(f"\n  ETF: {s['ticker']}  ({s['theme']})  close={s['close']:.2f}"
                  f"  signal={s['signal']}  pattern={s['pattern']}")

            if rf is not None:
                d0 = rf["x_start"]   # bar index of first combo point
                d1 = rf["x_end"]     # bar index of last combo point
                # 用 combo_bars 拿日期（比 x_start/x_end 更明確）
                cb = rf.get("combo_bars", [d0, d1])
                # 找對應的日期和實際 high 價格
                # 我們用線的端點值（回歸線上的值），不是實際 high
                # 端點 A 和端點 B 是回歸線在 combo 起終點的預測值
                # 實際 combo 定義點的日期
                combo_bar_dates = [f"bar={b}" for b in cb]
                r_y0 = line_y(rf, cb[0])
                r_y1 = line_y(rf, cb[-1])
                # 我們想要的是 yfinance 日期，但這裡在 main() 裡沒有 df
                # 所以用 res_touch_pts 的第一個和最後一個點日期當端點
                if r_t:
                    r_start = f"{r_t[0][0]}  ${r_t[0][1]:.2f}"
                    r_end   = f"{r_t[-1][0]}  ${r_t[-1][1]:.2f}"
                else:
                    r_start = "—"
                    r_end   = "—"
                print(f"  阻力線: 從 {r_start}")
                print(f"          到 {r_end}")
                print(f"          今日延伸值 ${s['resistance_today']:.2f}")
                touch_str = ", ".join(f"({d}, ${p:.2f})" for d, p in r_t)
                print(f"  阻力觸及點: [{touch_str}]")

            if sf is not None:
                if s_t:
                    s_start = f"{s_t[0][0]}  ${s_t[0][1]:.2f}"
                    s_end   = f"{s_t[-1][0]}  ${s_t[-1][1]:.2f}"
                else:
                    s_start = "—"
                    s_end   = "—"
                print(f"  支撐線: 從 {s_start}")
                print(f"          到 {s_end}")
                print(f"          今日延伸值 ${s['support_today']:.2f}")
                touch_str = ", ".join(f"({d}, ${p:.2f})" for d, p in s_t)
                print(f"  支撐觸及點: [{touch_str}]")

    # ── 輸出：線合格但無訊號 ────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  ⚪ 雙線合格、無訊號（{len(no_signal)} 支）")
    print(f"{'─'*65}")
    for s in no_signal:
        res_s = f"{s['resistance_today']:.3f}" if s.get("resistance_today") is not None else "—"
        sup_s = f"{s['support_today']:.3f}"    if s.get("support_today")    is not None else "—"
        print(f"  {s['ticker']:6s} ({s['theme']:14s}) {s['pattern']:24s}"
              f"  close={s['close']:.3f}  res={res_s}  sup={sup_s}")

    # ── 輸出：單線供參考 ────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  📋 單線（不發訊號，供參考）（{len(ref_only)} 支）")
    print(f"{'─'*65}")
    for s in ref_only:
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
    print(f"  完成。signals={len(signals)}  no_signal={len(no_signal)}"
          f"  ref_only={len(ref_only)}  rejected={len(rejected)}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
