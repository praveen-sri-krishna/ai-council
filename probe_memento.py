"""One-off: speak MCP to the installed memento-mcp server, list tools, dump the
schemas we care about. Confirms the real API before wiring memory.py."""
import json
import subprocess

MAIN = (r"C:\Users\Player 1\AppData\Local\hermes\node\node_modules"
        r"\@luispmonteiro\memento-memory-mcp\dist\cli\main.js")


def main() -> None:
    p = subprocess.Popen(["node", MAIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()

    def read_id(want_id):
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == want_id:
                return msg

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "ai-council", "version": "0.1"}}})
    init = read_id(1)
    print("initialized:", init.get("result", {}).get("serverInfo"))

    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = read_id(2)["result"]["tools"]
    print(f"\n{len(tools)} tools:", ", ".join(t["name"] for t in tools))

    for name in ("memory_store", "memory_search"):
        t = next((x for x in tools if x["name"] == name), None)
        if t:
            print(f"\n=== {name} ===")
            print("desc:", (t.get("description") or "")[:200])
            print("schema:", json.dumps(t.get("inputSchema", {}), indent=2)[:1500])

    p.stdin.close()
    p.terminate()


if __name__ == "__main__":
    main()
