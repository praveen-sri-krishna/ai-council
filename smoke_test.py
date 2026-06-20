"""Phase 1 smoke test: prove call_seat() works against one local (Ollama) and
one hosted seat, and that extract_json() recovers structured output."""
from seats import load_config, available_seats, call_seat, extract_json


def test_seat(seat, defaults) -> None:
    print(f"\n--- {seat.id} ({seat.provider}:{seat.model}, role={seat.role}) ---")
    messages = [
        {"role": "system", "content": "You answer only with compact JSON."},
        {"role": "user", "content": 'Return JSON: {"city": "capital of France", "ok": true}'},
    ]
    try:
        text = call_seat(seat, messages, defaults, max_tokens=200, temperature=0)
        parsed = extract_json(text)
        print(f"raw   : {text[:120]!r}")
        print(f"parsed: {parsed}")
        print("PASS" if parsed else "WARN: no JSON parsed (model still responded)")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")


def main() -> None:
    cfg = load_config()
    seats = available_seats(cfg)
    print(f"privacy_mode={cfg.privacy_mode} | {len(seats)} seats available:",
          ", ".join(s.id for s in seats))

    local = next((s for s in seats if s.local), None)
    hosted = next((s for s in seats if not s.local), None)

    if local:
        test_seat(local, cfg.defaults)
    else:
        print("\n(no local seat available)")
    if hosted:
        test_seat(hosted, cfg.defaults)
    else:
        print("\n(no hosted seat available — check keys or privacy_mode)")


if __name__ == "__main__":
    main()
