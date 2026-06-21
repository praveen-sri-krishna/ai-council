"""Verify the user never sits in the dark: start a debate, then poll the live
status the way the GUI does, confirming progress appears within seconds."""
import time

import gui

print("calling start_debate (should return immediately)...")
t0 = time.time()
out = gui.start_debate("What is a quick win to improve onboarding for a SaaS app?",
                       "open", ["cerebras-fast", "groq-fast", "gemini-chair"])
print(f"start_debate returned in {time.time()-t0:.2f}s (immediate = good)")

import re
for i in range(20):
    time.sleep(4)
    st = gui._read_live()
    evs = st.get("events", [])
    last = evs[-1] if evs else {}
    desc = last.get("name") or last.get("seat") or last.get("verdict") or last.get("type", "")
    print(f"[{int(time.time()-t0):3d}s] status={st.get('status')} events={len(evs)} latest={str(desc)[:60]}")
    if st.get("status") in ("done", "error"):
        f = st.get("final", {})
        print("FINAL verdict:", (f.get("verdict") or {}).get("call"), "| confidence:", f.get("confidence"))
        break
