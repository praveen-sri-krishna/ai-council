"""The Leader: the best-performing seat from a debate, holding the full debate
context. Chat 1:1 with FULL authority -- it answers directly, convenes the group
on sub-topics, and triggers web research when facts are needed. It never tells
the user to go run a separate debate; the leader IS the council's voice.
"""
import json
from pathlib import Path

from research import search_web
from seats import Config, Seat, call_seat, extract_json, load_config


def load_session(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _seat_by_id(cfg: Config, sid: str) -> Seat | None:
    return next((s for s in cfg.seats if s.id == sid), None)


def _ask_json(seat: Seat, system: str, user: str, defaults: dict):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text = call_seat(seat, msgs, defaults)
    data = extract_json(text)
    if data is None:
        msgs += [{"role": "assistant", "content": text},
                 {"role": "user", "content": "Output ONLY valid JSON."}]
        data = extract_json(call_seat(seat, msgs, defaults))
    return data, text


def _context_brief(session: dict) -> str:
    final = session.get("final", {})
    proposals = {k: str(v.get("proposal", ""))[:300] for k, v in session.get("proposals", {}).items()}
    return json.dumps({
        "idea": session.get("prompt"),
        "final_plan": final.get("ranked_plan"),
        "builds_on": final.get("builds_on"),
        "unresolved_dissent": final.get("dissent"),
        "minority_report": final.get("minority_report"),
        "evidence_verdicts": [{"verdict": v.get("verdict"), "claim": v.get("claim")}
                              for v in final.get("verdicts", [])],
        "leaderboard": final.get("leaderboard"),
        "proposals": proposals,
    }, indent=2, ensure_ascii=False)[:9000]


def leader_id(session: dict) -> str:
    return session.get("final", {}).get("best_model", "")


def consult_group(question: str, session: dict, cfg: Config, exclude: str,
                  evidence: list[dict] | None = None) -> list[dict]:
    """Poll the other seats on a specific question (graceful: skips unavailable)."""
    import os
    out = []
    for s in cfg.seats:
        if s.id == exclude:
            continue
        if not s.local and (not s.key_env or not os.environ.get(s.key_env)):
            continue
        if s.id not in session.get("proposals", {}):  # only seats that were in the debate
            continue
        system = (f"You are {s.role} on a council. {s.personality} Answer the leader's question "
                  'briefly and concretely, using the evidence if provided. '
                  'ONLY JSON: {"answer": "", "confidence": 0.0}')
        ev = ("\n\nEvidence:\n" + "\n".join(f"- {e['snippet'][:200]}" for e in (evidence or [])[:5])
              if evidence else "")
        data, _ = _ask_json(s, system, f"Question: {question}{ev}", cfg.defaults)
        if data:
            out.append({"seat": s.id, "answer": data.get("answer", ""),
                        "confidence": data.get("confidence")})
    return out


def leader_chat(session: dict, user_msg: str, history: list[tuple[str, str]],
                cfg: Config | None = None) -> dict:
    """Returns {leader, decision, answer, question_for_group, group, researched}.

    The leader has FULL authority: answer directly, convene the group, and/or run
    web research. It must NEVER tell the user to go run a separate debate."""
    cfg = cfg or load_config()
    lid = leader_id(session)
    leader = _seat_by_id(cfg, lid) or cfg.seats[0]
    ctx = _context_brief(session)
    convo = "\n".join(f"{role}: {msg}" for role, msg in history[-6:])
    grounded = cfg.privacy_mode != "local_only"

    system = (
        f"You are the LEADER of an AI council, chosen because you performed best in the debate. "
        f"{leader.personality} You hold the full debate context and you speak 1:1 with the user. "
        f"You have FULL authority and tools -- do not defer or stall:\n"
        f"- Answer directly when you can.\n"
        f"- CONSULT THE GROUP on any sub-topic that benefits from other members' views.\n"
        f"- Request WEB RESEARCH (set research_query) when current facts/data would help"
        f"{' (research is enabled)' if grounded else ' (note: research disabled in local_only mode)'}.\n"
        f"NEVER tell the user this needs a separate think-tank/debate or that you can't help here "
        f"-- you ARE the council's voice; use consult_group and research_query to handle anything, "
        f"however deep. ONLY JSON: "
        f'{{"decision": "answer|consult_group", "answer": "<answer if deciding yourself>", '
        f'"question_for_group": "<focused question if consulting>", '
        f'"research_query": "<web query if facts needed, else empty>"}}'
    )
    user = f"Debate context:\n{ctx}\n\nConversation so far:\n{convo}\n\nUser: {user_msg}"
    data, raw = _ask_json(leader, system, user, cfg.defaults)
    if not data:
        return {"leader": lid, "decision": "answer", "answer": raw[:1200],
                "group": [], "researched": ""}

    rq = (data.get("research_query") or "").strip()
    evidence = search_web(rq, max_results=5) if (rq and grounded) else []

    if data.get("decision") == "consult_group" and data.get("question_for_group"):
        group = consult_group(data["question_for_group"], session, cfg, exclude=lid,
                              evidence=evidence)
        system2 = (
            f"You are the LEADER. You consulted the group on {data['question_for_group']!r}"
            f"{' and gathered web evidence' if evidence else ''}. Write a complete, decisive "
            f"final answer for the user in clear prose (markdown ok) -- do not defer, do not "
            f"output JSON. {leader.personality}"
        )
        payload = {"original_question": user_msg, "group_input": group,
                   "evidence": [e["snippet"][:200] for e in evidence]}
        answer = call_seat(leader, [{"role": "system", "content": system2},
                                    {"role": "user", "content": json.dumps(payload, indent=2)}],
                           cfg.defaults).strip()
        return {"leader": lid, "decision": "consult_group",
                "question_for_group": data["question_for_group"],
                "answer": answer, "group": group, "researched": rq if evidence else ""}

    # Leader answers itself; if it asked for facts, fold the evidence in.
    if evidence:
        system3 = (f"You are the LEADER. Using this web evidence, give the user a complete, "
                   f"decisive answer in clear prose (markdown ok) -- do not defer, do not output "
                   f"JSON. {leader.personality}")
        payload = {"question": user_msg, "evidence": [e["snippet"][:200] for e in evidence]}
        answer = call_seat(leader, [{"role": "system", "content": system3},
                                    {"role": "user", "content": json.dumps(payload, indent=2)}],
                           cfg.defaults).strip()
        return {"leader": lid, "decision": "answer", "group": [], "researched": rq,
                "answer": answer}

    return {"leader": lid, "decision": "answer", "answer": data.get("answer", raw[:1200]),
            "group": [], "researched": ""}
