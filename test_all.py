"""End-to-end test pass: every configured API model, Phase 5 hardening (retry,
failover, graceful skip, privacy), and extract_json. Run: uv run python test_all.py"""
import urllib.error

import seats as S
from orchestrator import synth_with_failover

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


# 1) Every configured seat actually answers through call_seat (verifies model strings)
def test_seats():
    cfg = S.load_config()
    avail = S.available_seats(cfg)
    print(f"\n--- Seat connectivity ({len(avail)} available) ---")
    d = dict(cfg.defaults)
    d["max_tokens"] = 2000  # enough for thinking models to finish a one-word reply
    for s in avail:
        try:
            txt = S.call_seat(s, [{"role": "user", "content": "Reply with one word: READY"}],
                              d, max_tokens=2000)
            check(f"seat {s.id} ({s.model})", bool(txt.strip()), txt.strip()[:40])
        except Exception as e:
            check(f"seat {s.id} ({s.model})", False, f"{type(e).__name__}: {str(e)[:80]}")


# 2) Phase 5: retry/backoff on a transient error, then success
def test_retry():
    print("\n--- Phase 5: retry/backoff ---")
    calls = {"n": 0}
    orig = S._once

    def flaky(seat, messages, defaults, mt, temp):
        calls["n"] += 1
        if calls["n"] < 3:                      # fail twice (429), succeed on 3rd
            raise urllib.error.HTTPError("u", 429, "rate", {}, None)
        return "recovered"

    S._once = flaky
    try:
        seat = S.Seat(id="t", provider="x", base_url="", model="m", role="r")
        out = S.call_seat(seat, [], {"max_retries": 3, "retry_base": 0.01, "retry_cap": 0.1})
        check("retry recovers after transient 429s", out == "recovered" and calls["n"] == 3,
              f"{calls['n']} attempts")
    finally:
        S._once = orig


# 3) Phase 5: non-retryable error is raised (not retried forever)
def test_nonretryable():
    print("\n--- Phase 5: non-retryable fails fast ---")
    calls = {"n": 0}
    orig = S._once

    def boom(seat, messages, defaults, mt, temp):
        calls["n"] += 1
        raise ValueError("bad request")        # not transient

    S._once = boom
    try:
        seat = S.Seat(id="t", provider="x", base_url="", model="m", role="r")
        raised = False
        try:
            S.call_seat(seat, [], {"max_retries": 3, "retry_base": 0.01})
        except ValueError:
            raised = True
        check("non-retryable raises immediately", raised and calls["n"] == 1, f"{calls['n']} call(s)")
    finally:
        S._once = orig


# 4) Phase 5: chair failover when primary can't produce a usable plan
def test_failover():
    print("\n--- Phase 5: chair failover ---")
    import orchestrator as O
    orig = O.phase_synthesize
    bad = S.Seat(id="bad", provider="x", base_url="", model="m", role="chair")
    good = S.Seat(id="good", provider="x", base_url="", model="m", role="generalist")

    def fake(seat, mp, best, defaults):
        if seat.id == "good":
            return {"ranked_plan": ["step"], "rationale": "ok", "fixes": [], "builds_on": [], "dissent": []}
        return {"ranked_plan": [], "rationale": "(unparsed)", "fixes": [], "builds_on": [], "dissent": []}

    O.phase_synthesize = fake
    try:
        out = synth_with_failover(bad, None, None, {}, [bad, good])
        check("failover reaches a working seat", out.get("rationale") == "ok", out.get("rationale"))
    finally:
        O.phase_synthesize = orig


# 5) Phase 5: graceful skip on missing key + privacy_mode local_only
def test_availability():
    print("\n--- Phase 5: graceful skip + privacy ---")
    cfg = S.load_config()
    open_seats = S.available_seats(cfg)
    has_hosted = any(not s.local for s in open_seats)
    check("open mode includes hosted seats", has_hosted, f"{len(open_seats)} seats")

    cfg.privacy_mode = "local_only"
    local = S.available_seats(cfg)
    check("local_only drops ALL hosted seats", all(s.local for s in local),
          f"{len(local)} local seats")

    # missing key -> seat skipped
    cfg.privacy_mode = "open"
    cfg.seats.append(S.Seat(id="ghost", provider="x", base_url="", model="m",
                            role="r", key_env="DEFINITELY_MISSING_KEY_XYZ"))
    ids = [s.id for s in S.available_seats(cfg)]
    check("missing-key seat is skipped", "ghost" not in ids)


# 6) extract_json robustness
def test_extract():
    print("\n--- extract_json ---")
    cases = [
        ('{"a":1}', {"a": 1}),
        ('```json\n{"a":1}\n```', {"a": 1}),
        ('text {"a":{"b":2}} tail', {"a": {"b": 2}}),
        ('garbage', None),
    ]
    ok = all(S.extract_json(inp) == exp for inp, exp in cases)
    check("extract_json handles raw/fenced/nested/garbage", ok)


def test_objections_shapes():
    # Regression: some models return critique fields as lists, not strings.
    print("\n--- _objections handles list-valued critique fields ---")
    from memory import MemoryPalace
    from orchestrator import _objections
    mp = MemoryPalace(prompt="x")
    mp.critiques = {"a": {"b": {"weaknesses": ["w1", "w2"],          # list
                               "failure_modes": "fm"}}}             # str
    best = {"votes": {"a": {"reason": ["r1", "r2"]}, "b": {"reason": "r3"}}}  # list + str
    try:
        objs = _objections(mp, best)
        check("_objections coerces list fields without crashing",
              isinstance(objs, list) and all(isinstance(o, str) for o in objs))
    except Exception as e:
        check("_objections coerces list fields without crashing", False,
              f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    test_seats()
    test_retry()
    test_nonretryable()
    test_failover()
    test_availability()
    test_extract()
    test_objections_shapes()
    n_pass = sum(1 for _, ok in results if ok)
    print(f"\n==== {n_pass}/{len(results)} passed ====")
    if n_pass != len(results):
        print("FAILURES:", [n for n, ok in results if not ok])
