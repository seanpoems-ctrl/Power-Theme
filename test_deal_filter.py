"""Tests for deal_filter.py (pending cash-takeover detection). Run: python -m pytest test_deal_filter.py"""
import json

from deal_filter import (_DEAL_RE, _candidates, _search_name, drop_pending, hide_pending_in, jump_then_pinned, load_registry, pending_deal_tickers,
                         save_registry, update_registry)


def _base(n=30):
    return [10 + 0.1 * i for i in range(n)]


def test_deal_jump_then_flat_is_detected():
    b = _base()
    c = b + [b[-1] * 1.4, b[-1] * 1.4 * 1.003, b[-1] * 1.4 * 1.001]
    hit = jump_then_pinned(c)
    assert hit and hit["jump_pct"] == 40.0 and hit["sessions_after"] == 2


def test_earnings_gap_that_keeps_moving_is_not():
    b = _base()
    assert jump_then_pinned(b + [b[-1] * 1.2, b[-1] * 1.2 * 1.04, b[-1] * 1.2 * 1.02]) is None


def test_no_jump_and_short_history():
    assert jump_then_pinned(_base()) is None
    assert jump_then_pinned([1, 2, 3]) is None


def test_old_jump_outside_lookback_ignored():
    b = _base(10)
    assert jump_then_pinned(b + [b[-1] * 1.5] + [b[-1] * 1.5] * 130) is None


def test_headline_regex_matches_target_not_buyer():
    assert _DEAL_RE.search("Braze to be acquired by Salesforce for $5.7 billion")
    assert _DEAL_RE.search("ADT agrees to take-private deal at $10 per share in cash")
    assert not _DEAL_RE.search("Salesforce to acquire Braze")


def _row(t, **kw):
    return {"ticker": t, "company": t, "perf_1d": 0.2, "perf_1w": 0, "perf_1m": 0, **kw}


def _reg(deals=(), cleared=()):
    return {"deals": {t: {"note": "n", "since": "2026-11-01"} for t in deals}, "cleared": list(cleared)}


def test_seeds_only_for_never_evaluated_tickers():
    r = update_registry([_row("X")], _reg(), today="2026-11-05")
    assert {"BRZE", "ADT"} <= set(r["deals"])
    r = update_registry([_row("X")], _reg(cleared=["BRZE"]), today="2026-11-05")
    assert "BRZE" not in r["deals"] and "ADT" in r["deals"]


def test_big_move_clears_a_flag_and_it_is_not_reseeded():
    r = update_registry([_row("BRZE", perf_1d=-12.0)], _reg(deals=["BRZE", "ADT"]), today="2026-11-05")
    assert "BRZE" not in r["deals"] and "BRZE" in r["cleared"]
    r2 = update_registry([_row("BRZE")], r, today="2026-11-06")
    assert "BRZE" not in r2["deals"]


def test_flag_persists_and_survives_absence_from_universe():
    r = update_registry([_row("OTHER")], _reg(deals=["ZZZ", "BRZE", "ADT"]), today="2026-11-05")
    assert "ZZZ" in r["deals"]


def test_old_registrations_are_pruned():
    reg = {"deals": {"OLD": {"note": "n", "since": "2024-01-01"}}, "cleared": ["BRZE", "ADT"]}
    assert "OLD" not in update_registry([_row("X")], reg, today="2026-11-05")["deals"]


def test_drop_pending_and_registry_roundtrip(tmp_path):
    p = tmp_path / "pending_deals.json"
    save_registry(_reg(deals=["BRZE"]), p)
    assert json.loads(p.read_text())["deals"]["BRZE"]["note"] == "n"
    assert pending_deal_tickers(p) == {"BRZE": "n"}
    assert load_registry(tmp_path / "missing.json") == {"deals": {}, "cleared": []}
    kept, hidden = drop_pending([{"ticker": "BRZE"}, {"ticker": "AAPL"}], deals={"BRZE": "n"})
    assert [r["ticker"] for r in kept] == ["AAPL"] and hidden == ["BRZE"]
    assert hide_pending_in([{"ticker": "AAPL"}])[1] is not None   # never raises


def test_slow_creep_to_the_offer_over_months_is_still_pinned():
    b = _base(40)
    px = b[-1] * 1.33
    post = [px]
    for i in range(80):                       # 80 quiet sessions, one 1.8% day, drifting ~3% up
        post.append(post[-1] * (1.018 if i == 40 else 1.0004))
    assert jump_then_pinned(b + post)["sessions_after"] == 80


def test_search_name_strips_suffixes():
    assert _search_name("Silence Therapeutics Plc - ADR") == "Silence Therapeutics"
    assert _search_name("Pacira BioSciences, Inc.") == "Pacira BioSciences"
    assert _search_name("Genmab A/S") == "Genmab"


def test_prefilter_uses_3m_and_6m_not_just_1m():
    rows = [_row("PTC", perf_1m=-0.3, perf_3m=30), _row("FLAT", perf_3m=5), _row("MOVING", perf_3m=40, perf_1d=6)]
    assert [r["ticker"] for r in _candidates(rows)] == ["PTC"]
    assert len(_candidates(rows, everything=True)) == 3
