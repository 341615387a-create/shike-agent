import unittest
from agent.routing import normalize_content_routing
from agent.attention import attention_context, validate_move
from agent.harness import turn_contract
from agent.model import CompatibleModel, FormatError


def node(key, label, status="active", kind="fact"):
    return {"id": key, "label": label, "summary": label, "status": status, "kind": kind}


def context():
    c = {"task": "message", "routing": {}, "conversation": [
        {"id": "u1", "role": "user", "text": "今天在食堂吃饭，想起以前的食堂"}],
        "tree": [node("old", "以前的食堂"), node("bad", "食堂", "rejected"), node("other", "短视频")],
        "active_focus": "other"}
    c["harness"] = turn_contract(c)
    c["attention"] = attention_context(c)
    return c


class AttentionTests(unittest.TestCase):
    def test_bare_confirmation_is_not_low_energy_or_topic_evidence(self):
        routed=normalize_content_routing({'signal':'NO_ANSWER','uptake':'low_energy','direction':'wrong','quote':'确实'},'确实')
        self.assertEqual(routed['uptake'],'acknowledgement')
        self.assertEqual(routed['signal'],'NONE')
        self.assertTrue(routed['pure_control'])
    def test_thanks_closes_only_the_current_turn(self):
        routed=normalize_content_routing({'signal':'NO_ANSWER','uptake':'low_energy','direction':'wrong','quote':'好的 谢谢'},'好的 谢谢')
        self.assertEqual(routed['uptake'],'closing')
        self.assertEqual(routed['signal'],'NONE')
        self.assertTrue(routed['pure_control'])
    def test_confirmation_with_reason_remains_an_acknowledgement(self):
        routed=normalize_content_routing({'signal':'NONE','uptake':'none'},'确实 有道理')
        self.assertEqual(routed['uptake'],'acknowledgement')
    def test_recall_keeps_source_type_and_excludes_rejected(self):
        c = context()
        self.assertEqual(c["attention"]["memories"][0]["id"], "old")
        self.assertNotIn("bad", [n["id"] for n in c["attention"]["memories"]])
        self.assertEqual(c["attention"]["memories"][0]["kind"], "fact")

    def test_explicit_new_topic_does_not_promote_old_focus(self):
        c = context()
        c["routing"]["uptake"] = "new_topic"
        self.assertNotIn("other", [n["id"] for n in attention_context(c)["memories"]])

    def test_recall_is_bounded_and_clicked_node_is_prioritized(self):
        c = context()
        c["tree"] += [node(str(i), "食堂") for i in range(30)]
        c["focus_override"] = "29"
        memories = attention_context(c)["memories"]
        self.assertEqual(len(memories), 6)
        self.assertEqual(memories[0]["id"], "29")

    def test_fabricated_quote_and_unavailable_memory_rejected(self):
        c = context()
        move = {"kind": "connect", "anchor_id": "u1", "anchor_quote": "食堂", "memory_ids": ["old"]}
        self.assertEqual(validate_move({"local_move": move}, c), move)
        for patch in ({"anchor_quote": "我很孤独"}, {"memory_ids": ["bad"]}, {"memory_ids": []}, {"anchor_id": "invented"}):
            with self.subTest(patch=patch), self.assertRaises(FormatError):
                validate_move({"local_move": move | patch}, c)

    def test_cross_session_profile_can_be_a_traceable_connection(self):
        c=context();c['profile_memory']=[{'id':'profile-work'}]
        move={"kind":"connect","anchor_id":"u1","anchor_quote":"食堂","memory_ids":["profile-work"]}
        self.assertEqual(validate_move({'local_move':move},c),move)

    def test_unknown_does_not_require_user_to_choose_or_answer(self):
        c = context()
        c["routing"] = {"uptake": "low_energy", "signal": "PREFERENCE"}
        c["harness"] = turn_contract(c)
        self.assertFalse(c["harness"]["allow_question"])
        self.assertEqual(c["harness"]["initiative"], "contribute")
        with self.assertRaises(FormatError):
            validate_move({"local_move": {"kind": "ask", "anchor_id": "u1", "anchor_quote": "食堂", "memory_ids": []}}, c)

    def test_only_local_action_is_retained_no_future_goal(self):
        c = context()
        result = validate_move({"local_move": {"kind": "stay", "anchor_id": "u1", "anchor_quote": "食堂", "memory_ids": [],
                                                  "goal": "最终让用户选短视频", "next_steps": ["推进"]}}, c)
        self.assertNotIn("goal", result)
        self.assertNotIn("next_steps", result)

    def test_recent_moves_retained_for_repetition_check(self):
        c = context()
        c["conversation"] += [{"role": "assistant", "local_move": {"kind": "notice", "anchor_quote": str(i)}} for i in range(5)]
        self.assertEqual([m["anchor_quote"] for m in attention_context(c)["recent_moves"]], ["2", "3", "4"])

    def test_explicit_stop_questions_persists_until_user_reinvites(self):
        c = context()
        c['conversation'].append({'role': 'assistant', 'text': '接着聊。', 'questions_paused': True})
        self.assertFalse(turn_contract(c)['allow_question'])
        c['routing']['invites_questions'] = True
        self.assertTrue(turn_contract(c)['allow_question'])
        c['routing']['declines_questions'] = True
        self.assertFalse(turn_contract(c)['allow_question'])

    def test_memory_call_cannot_rewrite_chat_or_conflict_with_routing(self):
        c = context()
        c['routing']['signal'] = 'PREFERENCE'
        c['evidence_messages'] = c['conversation'][:]
        turn = {'reply': '人多有时也像有人陪着。', 'interaction': {'act': 'share', 'question': None},
                'local_move': {'kind': 'notice', 'anchor_id': 'u1', 'anchor_quote': '食堂', 'memory_ids': []}}
        class Model(CompatibleModel):
            def __init__(self):
                self.inputs = []
            def call(self, messages, limit):
                self.inputs.append(messages)
                if len(self.inputs) == 1:
                    return turn
                return {'reply': '下一步去做短视频。', 'interaction': {'act': 'plan'}, 'decision': {'steering': 'AGENT'}}
        model = Model()
        result = model.generate(c)
        self.assertEqual(result['reply'], turn['reply'])
        self.assertEqual(result['decision']['steering'], 'USER')
        self.assertEqual(result['interaction']['act'], 'share')
        self.assertIn('以前的食堂', model.inputs[0][1]['content'])

    def test_final_review_is_bounded_and_excludes_tree_editor(self):
        class Model(CompatibleModel):
            def __init__(self):
                self.inputs = None
            def call(self, messages, limit):
                self.inputs = messages
                return {'ok': True, 'issues': []}
        import json
        model = Model()
        model.audit_harness(context(), {'reply': '食堂听起来很热闹。'})
        submitted = json.loads(model.inputs[1]['content'])
        self.assertEqual(set(submitted), {'harness', 'routing', 'task_frame', 'latest_user', 'previous_reply', 'reply'})
