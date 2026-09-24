"""Turn-level constraints: follow the conversation, without imposing a destination."""
from difflib import SequenceMatcher
import re
from .model import FormatError


# A period must not disguise a request for another answer. These are narrow
# conversational forms, in addition to the model review of actual meaning.
COVERT_QUESTION = re.compile(r"(?:还有什么|有没有什么|想聊什么|想说什么|你觉得怎样|你觉得如何|你呢|您呢)[^。！!\n]{0,45}(?:[。！!]|$)")
HOMEWORK = re.compile(r"(?:^|[。！!\n，,])\s*(?:你(?:再|可以|要不)?\s*)?(?:说说|讲讲|回忆一下|想一想|试着想想)")
DEFERRAL = re.compile(r"(?:^|[，,。！!\n])\s*(?:(?:那就|那|你可以|我们可以|可以)?(?:先)?(?:这样)?(?:搁着|放着|放一放|缓一缓|等一等)|(?:那就)?先?停在.{0,28}(?:这|那)(?:儿|里)|不用急着.{0,40})")


def turn_contract(context):
    routing = context.get("routing", {})
    request = routing.get("request_kind", "chat")
    task = context.get("task", "message")
    if task != "message":
        request = "reflect" if task == "reflect" else "chat"
    support = context.get('support') or {'mode': 'explore'}
    closing = routing.get('uptake') == 'closing'
    action_help = task == 'message' and request == 'chat' and support['mode'] == 'act' and not closing
    assistants = [m for m in context.get("conversation", []) if m["role"] == "assistant"]
    streak = 0
    for message in reversed(assistants):
        interaction = message.get("interaction")
        asked = bool(interaction.get("question")) if interaction else any(x in message["text"] for x in "?？")
        if not asked:
            break
        streak += 1
    questions_paused = next((m["questions_paused"] for m in reversed(assistants) if "questions_paused" in m), False)
    if routing.get("invites_questions") is True:
        questions_paused = False
    if routing.get("declines_questions") is True:
        questions_paused = True
    acknowledgement = routing.get('uptake') == 'acknowledgement'
    no_answer = routing.get("signal") == "NO_ANSWER" or routing.get("uptake") == "low_energy" or task == "skip" or questions_paused
    return {"policy": "local-guidance-v2", "request_kind": request,
            "allow_advice": not closing and (request in {"advice", "plan"} or action_help),
            "allow_plan": not closing and request == "plan",
            "support": support, "help_required": not closing and (request in {'advice', 'plan'} or action_help),
            "advice_scope": "只回应当前话题的一小步，不替用户决定长期方向",
            "allow_question": not closing and not no_answer and not acknowledgement and request != "reflect" and
                              (streak < 2 or routing.get("invites_questions") is True),
            "consecutive_questions": streak, "destination": None,
            "questions_paused": questions_paused,
            "focus_scope": "this_turn_only", "allow_no_tree_change": True,
            "initiative": "close" if closing else ("contribute" if no_answer or acknowledgement or routing.get("signal") == "DELEGATE" else "responsive"),
            "acknowledgement": acknowledgement,
            "closing": closing,
            "continue_open_task": acknowledgement and action_help,
            "note": "话题不是目标；没有生成灵感、决定选题或做出行动的完成要求。"}


def validate_interaction(proposal, context):
    contract = context.get("harness")
    if not contract:
        return None
    interaction = proposal.get("interaction")
    if not isinstance(interaction, dict):
        raise FormatError("缺少interaction：act与question，question可为null；不用强制提问")
    act, question = interaction.get("act"), interaction.get("question")
    reply = proposal['reply']
    if contract.get('help_required') and DEFERRAL.search(proposal['reply']):
        raise FormatError('用户明确想开始或请求做法，不能再劝先搁着；请提供当前话题的一小步实际帮助')
    if act not in {"respond", "share", "ask", "answer", "advise", "plan", "reflect"}:
        raise FormatError("未知对话动作")
    if question is not None and (not isinstance(question, str) or not question.strip()):
        raise FormatError("question须为实际问题原文，或null")
    if act == "ask" and not question:
        raise FormatError("ask动作须标出实际问题")
    if question and question not in proposal["reply"]:
        raise FormatError("标记的问题必须出现在实际回复中")
    if not question and any(mark in proposal["reply"] for mark in "?？"):
        raise FormatError("实际回复有问题，却未在interaction.question中标出")
    if not contract["allow_question"] and (question or act == "ask"):
        raise FormatError("本轮停止追问：用户答不上来、跳过或已连续被问。请贡献观察/联想，允许到此停住；不要改用祈使句索取答案")
    if not contract["allow_question"] and (COVERT_QUESTION.search(proposal["reply"]) or HOMEWORK.search(proposal["reply"])):
        raise FormatError("本轮不再索取答案：句号结尾的『还有什么/你呢』和『说说/想一想』也属于追问，请删去并自然停住")
    if act in {"advise", "plan"} and not contract["allow_advice"]:
        raise FormatError("用户没有明确索要做法，不能把聊天转成建议或方案")
    if act == "plan" and not contract["allow_plan"]:
        raise FormatError("用户没有请求行动计划；建议也不等于规划授权")
    if contract.get('closing') and act in {'ask', 'advise', 'plan'}:
        raise FormatError('用户正在致谢并收住当前话头；只简短接住，不继续追问或补充建议')
    previous = next((m.get('text', '') for m in reversed(context.get('conversation', []))
                     if m.get('role') == 'assistant' and m.get('text')), '')
    if previous and (contract.get('closing') or contract.get('acknowledgement')):
        normalize = lambda value: re.sub(r'[\s，。！？,.!?、；;：:—“”「」『』（）()]+', '', value)
        old, new = normalize(previous), normalize(reply)
        if min(len(old), len(new)) >= 24 and SequenceMatcher(None, old, new).ratio() >= .72:
            raise FormatError('回复与上一轮内容近乎重复；不要换一种说法重述建议。致谢时简短收住，确认时应真正推进新的内容')
    return {"act": act, "question": question}
