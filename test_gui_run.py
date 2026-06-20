"""Reproduce the GUI run path headlessly: does run_debate_stream actually stream
events for the default selection, or bail with the 'need seats' error?"""
import time

import gui

idea = "What is a good caching strategy for a small web API?"
privacy = "open"
selected = ["cerebras-fast", "groq-fast", "gemini-chair"]

print(f"selected={selected} privacy={privacy}")
t0 = time.time()
n = 0
for out in gui.run_debate_stream(idea, privacy, selected):
    n += 1
    trace = out[0]
    # pull the latest event line out of the rendered trace HTML
    import re
    txt = re.sub("<[^>]+>", " ", trace)
    txt = re.sub(r"\s+", " ", txt).strip()
    print(f"[{time.time()-t0:6.1f}s] yield #{n}: ...{txt[:120]}")
    if n > 60:
        print("(stopping early)")
        break
print(f"done: {n} yields in {time.time()-t0:.1f}s")
