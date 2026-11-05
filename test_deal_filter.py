"""Tests for deal_filter.py (pending cash-takeover detection). Run: python -m pytest test_deal_filter.py"""
from deal_filter import jump_then_pinned, apply_pending_deals, _DEAL_RE


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
    c = b + [b[-1] * 1.5] + [b[-1] * 1.5] * 40
    assert jump_then_pinned(c) is None


def test_headline_regex_matches_target_not_buyer():
    assert _DEAL_RE.search("Braze to be acquired by Salesforce for $5.7 billion")
    assert _DEAL_RE.search("ADT agrees to take-private deal at $10 per share in cash")
    assert not _DEAL_RE.search("Salesforce to acquire Braze")


def _row(t, **kw):
    return {"ticker": t, "company": t, "perf_1d": 0.2, "perf_1w": 0, "perf_1m": 0, **kw}


def test_seed_only_while_field_absent_and_dropped_on_break():
    assert apply_pending_deals([_row("BRZE")], {}) == 1                       # never evaluated -> seed
    s = [_row("BRZE")]
    assert apply_pending_deals(s, {"BRZE": {"pending_deal": False}}) == 0     # evaluated and cleared -> no re-seed
    s = [_row("BRZE", perf_1d=-12.0)]
    assert apply_pending_deals(s, {"BRZE": {"pending_deal": True, "deal_note": "x"}}) == 0   # broke -> unflag
    assert s[0]["pending_deal"] is False


def test_flag_persists_after_jump_rolls_out_of_windows():
    s = [_row("ADT")]
    assert apply_pending_deals(s, {"ADT": {"pending_deal": True, "deal_note": "Thrive"}}) == 1
    assert s[0]["deal_note"] == "Thrive"
