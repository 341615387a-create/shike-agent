"""Bounded conversational attention, with traceable memory and no end goal."""
import re


def terms(text):
    words = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words - {"今天", "觉得", "就是", "这个", "那个", "自己", "不是", "什么", "不知道", "可以"}


def attention_context(context):
    users = [m for m in context.get("conversation", []) if m["role"] == "user"]
    latest = users[-1]["text"] if users else ""
    query = terms(latest)
    focus = context.get("focus_override")
    active = context.get("active_focus")
    ranked = []
    for node in context.get("tree", []):
        if node.get("status") == "rejected":
            continue
        score = len(query & terms(node["label"] + " " + node["summary"]))
        if node["id"] == focus:
            score += 100
        elif node["id"] == active and context.get("routing", {}).get("uptake") != "new_topic":
            score += 2
        if score:
            ranked.append((score, node))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    memories = [{"id": n["id"], "label": n["label"], "summary": n["summary"], "kind": n["kind"],
                 "mentions": n.get("recent_mentions", [])[-2:],
                 "understandings": n.get("recent_understandings", [])[-2:]} for _, n in ranked[:6]]
    previous = [m["local_move"] for m in context.get("conversation", [])
                if m["role"] == "assistant" and m.get("local_move")][-3:]
    uptake = context.get("routing", {}).get("uptake", "none")
    return {"memories": memories, "recent_moves": previous, "user_response": uptake,
            "scope": "只选择此刻值得回应的一处，不能规划后续步骤或期望用户得出哪种答案",
            "avoid": "不复述刚说过的洞察，不把用户纠正视为阻碍，不把简短回应当成认同"}


def validate_move(turn, context):
    # Local annotations are a short, externally checkable action record, not
    # chain-of-thought or a plan for future turns.
    from .model import FormatError
    move = turn.get("local_move")
    allowed = {"notice", "connect", "imagine", "stay", "ask", "answer", "advise", "reflect"}
    if not isinstance(move, dict) or move.get("kind") not in allowed:
        raise FormatError("local_move需标出本轮动作notice/connect/imagine/stay/ask/answer/advise/reflect")
    sources = {m["id"]: m["text"] for m in context.get("conversation", []) if m["role"] == "user"}
    sources.update({m["id"]: m["text"] for m in context.get("evidence_messages", [])})
    source, quote = move.get("anchor_id"), move.get("anchor_quote")
    if source not in sources or not isinstance(quote, str) or not quote.strip() or quote not in sources[source]:
        raise FormatError("local_move的anchor_id和anchor_quote必须引用提供的用户消息原文")
    refs = move.get("memory_ids")
    available = {n["id"] for n in context.get("attention", {}).get("memories", [])}
    available.update(n["id"] for n in context.get('profile_memory', []))
    if not isinstance(refs, list) or len(refs) > 3 or any(not isinstance(x, str) or x not in available for x in refs):
        raise FormatError("memory_ids只能引用本轮召回的元素树或个人记忆节点；没有则[]")
    if move["kind"] == "connect" and not refs:
        raise FormatError("connect须引用实际回接的记忆节点；当前一句的观察请用notice")
    if move["kind"] == "ask" and not context["harness"]["allow_question"]:
        raise FormatError("本轮不能用提问来推进，请贡献一个观察或轻轻接话")
    # Return only the declared local fields. Never persist a generated goal or plan.
    return {"kind": move["kind"], "anchor_id": source, "anchor_quote": quote, "memory_ids": list(dict.fromkeys(refs))}
