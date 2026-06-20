"""The Leader: the best-performing seat from a debate, holding the full debate
context. Chat 1:1 -- the leader answers directly, or consults the group when the
question needs fresh deliberation, then synthesizes the group's input.
"""
import json
from pathlib import Path

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
    proposals = {k: v.get("proposal", "")[:300] for k, v in session.get("proposals", {}).items()}
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


def consult_group(question: str, session: dict, cfg: Config, exclude: str) -> list[dict]:
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
                  'briefly and concretely. ONLY JSON: {"answer": "", "confidence": 0.0}')
        data, _ = _ask_json(s, system, f"Question: {question}", cfg.defaults)
        if data:
            out.append({"seat": s.id, "answer": data.get("answer", ""),
                        "confidence": data.get("confidence")})
    return out


def leader_chat(session: dict, user_msg: str, history: list[tuple[str, str]],
                cfg: Config | None = None) -> dict:
    """Returns {leader, decision, answer, question_for_group, group}."""
    cfg = cfg or load_config()
    lid = leader_id(session)
    leader = _seat_by_id(cfg, lid) or cfg.seats[0]
    ctx = _context_brief(session)
    convo = "\n".join(f"{role}: {msg}" for role, msg in history[-6:])

    system = (
        f"You are the LEADER of an AI council that already debated an idea, chosen because you "
        f"performed best. {leader.personality} You hold the full debate context. The user talks "
        f"to you 1:1 instead of the whole panel. Decide: answer yourself when the debate already "
        f"covers it; CONSULT the group only when the question needs fresh deliberation (new "
        f"information, a trade-off the panel must own, or a genuinely new direction). "
        f'ONLY JSON: {{"decision": "answer|consult_group", '
        f'"answer": "<full answer if deciding yourself>", '
        f'"question_for_group": "<focused question if consulting>"}}'
    )
    user = f"Debate context:\n{ctx}\n\nConversation so far:\n{convo}\n\nUser: {user_msg}"
    data, raw = _ask_json(leader, system, user, cfg.defaults)
    if not data:
        return {"leader": lid, "decision": "answer", "answer": raw[:1200], "group": []}

    if data.get("decision") == "consult_group" and data.get("question_for_group"):
        group = consult_group(data["question_for_group"], session, cfg, exclude=lid)
        system2 = (
            f"You are the LEADER. You consulted the group on: {data['question_for_group']!r}. "
            f"Synthesize their input into a final answer for the user. {leader.personality} "
            'ONLY JSON: {"answer": ""}'
        )
        synth, raw2 = _ask_json(leader, system2,
                                json.dumps({"original_question": user_msg,
                                            "group_input": group}, indent=2), cfg.defaults)
        return {"leader": lid, "decision": "consult_group",
                "question_for_group": data["question_for_group"],
                "answer": (synth or {}).get("answer", raw2[:1200]), "group": group}

    return {"leader": lid, "decision": "answer", "answer": data.get("answer", raw[:1200]),
            "group": []}
