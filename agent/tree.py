"""Validate and apply a model proposal to a copy; never partially commit a tree."""
import copy
import re
import uuid
from .model import FormatError
from .harness import validate_interaction
from .routing import is_control_only


def require(ok, message):
    if not ok:
        raise FormatError(message)


def string(value, limit=500, field="文字字段"):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= limit, f"{field}缺失或过长（1—{limit}字）")
    return value.strip()


def apply_proposal(session, proposal, context, now):
    require(isinstance(proposal, dict), "输出须为对象")
    reply = string(proposal.get("reply"), 3000)
    interaction = validate_interaction(proposal, context)
    require(reply.count("？") + reply.count("?") <= 1, "一轮最多一个主问题")
    question = re.split(r"[。！!\n]", reply)[-1].strip()
    if "？" in question or "?" in question:
        previous = [m["text"] for m in session["messages"] if m["role"] == "assistant"][-5:]
        require(not any(question in text for text in previous), "重复了最近的问题，请改变推进方式")
    changes = proposal.get("changes")
    require(isinstance(changes, list) and len(changes) <= 8, "每轮最多八个树变动")
    result = copy.deepcopy(session["nodes"])
    source = {m["id"]: m for m in context["evidence_messages"]}
    aliases = {}
    touched = set()
    for change in changes:
        require(isinstance(change, dict), "节点变动须为对象")
        key = string(change.get("key"), 80)
        op = change.get("op")
        require(op in {"add", "update"}, "未知树操作")
        require(key not in touched, "同轮重复变动同一节点")
        touched.add(key)
        parent = change.get("parent")
        require(parent is None or isinstance(parent, str), "父节点格式错误")
        parent = aliases.get(parent, parent)
        require(parent is None or parent in result, "父节点必须已存在；不能形成循环")
        kind, status, relation = change.get("kind"), change.get("status"), change.get("relation")
        require(kind in {"fact", "hypothesis"} and status in {"active", "rejected"}, "节点类别或状态错误")
        require(relation in {"root", "deepen", "alternative"}, "未知生长关系")
        require((parent is None) == (relation == "root"), "根节点关系不匹配")
        if op == "add" and relation == "alternative" and parent and result[parent]["parent"]:
            parent = result[parent]["parent"]
        label, summary = string(change.get("label"), 50, "节点label"), string(change.get("summary"), 500, "节点summary")
        evidence = change.get("evidence")
        require(isinstance(evidence, list) and 1 <= len(evidence) <= 6, "节点必须有用户来源")
        occurrences = []
        for item in evidence:
            require(isinstance(item, dict), "来源格式错误")
            message_id = item.get("message_id")
            require(isinstance(message_id, str) and message_id in source, "不能引用不存在或其他会话的来源")
            require(message_id not in context.get("non_topic_message_ids", []), "不能把对话控制或选路委托作为主题证据")
            quote, note = string(item.get("quote"), 2000, "evidence.quote"), string(item.get("note"), 300, "evidence.note")
            require(quote in source[message_id]["text"], "引用不是用户原文连续片段")
            require(not is_control_only(quote), "来源片段只有对话控制语，请引用同一消息中的具体事件、经历或偏好")
            # A non-answer provides interaction feedback, not a confirmed topic fact.
            compact = re.sub(r"[\s，。！？,.!?]", "", source[message_id]["text"])
            require(compact not in {"不知道", "我不知道", "不知道啊", "不清楚", "想不起来", "不知道哎呀"},
                    "不能把纯粹的不知道写成主题证据")
            occurrences.append({"message_id": message_id, "quote": quote, "note": note,
                                "at": source[message_id]["at"], "question": source[message_id].get("question", "")})
        if op == "add":
            require(key not in result and key not in aliases, "新增节点key重复")
            node_id = "n_" + uuid.uuid4().hex[:16]
            aliases[key] = node_id
            result[node_id] = {"id": node_id, "parent": parent, "relation": relation,
                               "created_at": now, "occurrences": [], "versions": []}
        else:
            require(key in result, "更新节点不存在")
            node_id = key
            require(result[key]["parent"] == parent and result[key]["relation"] == relation,
                    "更新不能移动旧节点；不同理解请新增分支")
        node = result[node_id]
        known = {x["message_id"] for x in node["occurrences"]}
        for occurrence in occurrences:
            if occurrence["message_id"] not in known:
                node["occurrences"].append(occurrence)
                known.add(occurrence["message_id"])
        node["occurrences"].sort(key=lambda occurrence: occurrence["at"])
        node.update(label=label, summary=summary, kind=kind, status=status, updated_at=now)
        node["versions"].append({"at": now, "summary": summary, "label": label, "kind": kind,
                                 "status": status, "message_ids": [x["message_id"] for x in occurrences]})
    decision = proposal.get("decision")
    require(isinstance(decision, dict), "缺少本轮方向决策")
    decision = copy.deepcopy(decision)
    require(decision.get("mode") in {"DISCOVER", "EXPLORE"}, "未知探索模式")
    require(decision.get("steering") in {"USER", "AGENT", "CONTINUE"}, "未知方向来源")
    require(decision.get("uncertainty") in {"none", "answer", "direction"}, "未知不确定性类型")
    signal = context.get("routing", {}).get("signal")
    expected = {"DELEGATE": "AGENT", "PREFERENCE": "USER"}.get(signal)
    if expected:
        require(decision["steering"] == expected, "方向来源与用户意图不匹配：委托选路是AGENT，具体偏向是USER")
    if signal == "DELEGATE":
        decision["uncertainty"] = "direction"
    string(decision.get("readiness"), 300, "decision.readiness")
    string(decision.get("intent"), 300, "decision.intent")

    def resolve(key):
        require(key is None or isinstance(key, str), "方向节点格式错误")
        key = aliases.get(key, key)
        require(key is None or (key in result and result[key]["status"] == "active"),
                f"方向 {key} 不存在或已拒绝：node_id/focus 只能用 tree 中的 id 或本次 changes 中的 add key；不要用标签或未创建的key")
        return key

    decision["focus"] = resolve(decision.get("focus"))
    if signal == "PREFERENCE" and context["evidence_messages"] and not context.get("routing", {}).get("pure_control"):
        latest_id = context["evidence_messages"][-1]["id"]
        require(decision["focus"] is not None and any(o["message_id"] == latest_id for o in result[decision["focus"]]["occurrences"]),
                "用户表达了具体偏向，focus必须指向包含本轮用户证据的相应节点，不能停留在旧分支")
    forced = context.get("focus_override")
    if forced:
        require(decision["steering"] == "USER", "点击分支代表用户选择，必须优先")
        cursor = decision["focus"]
        while cursor and cursor != forced:
            cursor = result[cursor]["parent"]
        require(cursor == forced, "不能偏离用户点选的分支")
    candidates = proposal.get("candidates")
    require(isinstance(candidates, list) and len(candidates) <= 3, "可提供零至三个话头，不必每轮选择路线")
    candidates = copy.deepcopy(candidates)
    for candidate in candidates:
        require(isinstance(candidate, dict), "路线摘要格式错误")
        # Candidate ideas may not have a node yet. Drop an invalid optional link;
        # never fabricate a node or relax the chosen focus/source validation.
        key = candidate.get("node_id")
        require(key is None or isinstance(key, str), "路线节点格式错误")
        resolved = aliases.get(key, key)
        candidate["node_id"] = resolved if resolved in result and result[resolved]["status"] == "active" else None
        string(candidate.get("angle"), 150)
        string(candidate.get("benefit"), 300)
        require(candidate.get("effort") in {"low", "medium"}, "问题负担应低或中等")
    return {"nodes": result, "decision": decision, "candidates": candidates, "reply": reply,
            "changed_ids": [aliases.get(key, key) for key in touched], "interaction": interaction}
