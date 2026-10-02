"""
Throwaway checks for inverse_arsenal_builder.py. Run from the repo root:

    python test_inverse_arsenal.py

Each case writes to a temp file (never public/inverse_arsenal.json) and prints PASS or FAIL.
Case 1 needs internet (TradingView + Yahoo); cases 2-4 simulate the failures seen in CI.
"""
import importlib.util
import json
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent
TMP = Path(tempfile.mkdtemp())
results = []


def load():
    spec = importlib.util.spec_from_file_location("arsenal", ROOT / "inverse_arsenal_builder.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.OUTPUT_JSON = TMP / "arsenal.json"
    m.OUTPUT_JSON.unlink(missing_ok=True)  # every case starts with no output file
    m.time.sleep = lambda s: None          # skip retry waits
    return m


def report(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def read(m):
    return json.loads(m.OUTPUT_JSON.read_text(encoding="utf-8")) if m.OUTPUT_JSON.exists() else None


# 1. Normal run
m = load()
m.main()
d = read(m)
report("1 normal run", bool(d) and len(d["core"]) >= 14 and d["perf_source"] == "yahoo",
       f"core={len(d['core']) if d else 0} hot={len(d['hot']) if d else 0} perf_source={d and d['perf_source']}")
tickers = {r["ticker"] for r in d["core"]} if d else set()
report("1b core has the pinned names", {"SQQQ", "SOXS", "LABD", "TZA", "UVIX"} <= tickers, f"missing={ {'SQQQ','SOXS','LABD','TZA','UVIX'} - tickers }")
report("1c leverage labels", bool(d) and {r["ticker"]: r["leverage"] for r in d["core"]}.get("SQQQ") == "-3x"
       and {r["ticker"]: r["leverage"] for r in d["core"]}.get("UVXY") == "+1.5x")

# 2. TradingView's flag query returns nothing (what CI did) -> fall back to by-name query
m = load()
real_tv = m._tv
m._tv = lambda label, *a, **k: m.pd.DataFrame() if label == "inverse flag" else real_tv(label, *a, **k)
m.main()
d2 = read(m)
report("2 flag query empty -> name fallback", bool(d2) and len(d2["core"]) >= 12,
       f"core={len(d2['core']) if d2 else 0} hot={len(d2['hot']) if d2 else 0}")

# 3. Fund filters broken AND the by-name fallback blind to leverage fields (flag/ratio columns all None)
m = load()
real_tv = m._tv
def blind(label, *a, **k):
    if label == "inverse flag":
        return m.pd.DataFrame()
    df = real_tv(label, *a, **k)
    if not df.empty:
        df["leveraged_flag"] = None
        df["leverage_ratio"] = None
    return df
m._tv = blind
m.main()
d3 = read(m)
lab = {r["ticker"]: r["leverage"] for r in d3["core"]} if d3 else {}
report("3 no flag/ratio columns -> labels still right",
       bool(d3) and lab.get("SQQQ") == "-3x" and lab.get("SOXS") == "-3x" and lab.get("UVXY") == "+1.5x" and lab.get("UVIX") == "+2x",
       f"SQQQ={lab.get('SQQQ')} SOXS={lab.get('SOXS')} UVXY={lab.get('UVXY')} UVIX={lab.get('UVIX')}")

# 4. TradingView returns nothing at all -> clean exit, never touches Yahoo, leaves the file alone
m = load()
SENTINEL = '{"sentinel": "last good file"}'
m.OUTPUT_JSON.write_text(SENTINEL, encoding="utf-8")   # stands in for last night's good output
m._tv = lambda *a, **k: m.pd.DataFrame()
yahoo_calls = []
m.yf.download = lambda *a, **k: yahoo_calls.append(1)
m.main()
report("4 everything empty -> clean stop, old file kept",
       m.OUTPUT_JSON.read_text(encoding="utf-8") == SENTINEL and not yahoo_calls,
       f"old_file_untouched={m.OUTPUT_JSON.read_text(encoding='utf-8') == SENTINEL} yahoo_calls={len(yahoo_calls)}")

# 5. TradingView fine, Yahoo down -> TradingView performance fallback
m = load()
def boom(*a, **k):
    raise ValueError("No objects to concatenate")
m.yf.download = boom
m.main()
d5 = read(m)
report("5 Yahoo down -> TradingView perf", bool(d5) and d5["perf_source"] == "tradingview" and len(d5["core"]) >= 14,
       f"perf_source={d5 and d5['perf_source']} core={len(d5['core']) if d5 else 0}")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
