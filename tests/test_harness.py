import unittest
from agent.harness import turn_contract, validate_interaction
from agent.model import FormatError


def context(texts=(), signal="NONE", request="chat", task="message", invited=False):
    c = {"task": task, "conversation": [{"role": "assistant", "text": t} for t in texts],
         "routing": {"signal": signal, "request_kind": request, "invites_questions": invited}}
    c["harness"] = turn_contract(c)
    return c


class HarnessTests(unittest.TestCase):
    def test_acknowledgement_continues_open_help_without_another_question(self):
        c=context()
        c['routing']['uptake']='acknowledgement'
        c['support']={'mode':'act','source_id':'u1','quote':'帮我想项目'}
        c['task_frame']={'status':'active','objective':{'text':'做黑客松项目'}}
        c['harness']=turn_contract(c)
        self.assertTrue(c['harness']['help_required'])
        self.assertTrue(c['harness']['continue_open_task'])
        self.assertFalse(c['harness']['allow_question'])
        self.assertEqual(c['harness']['initiative'],'contribute')
    def test_closing_thanks_does_not_repeat_persistent_action_help(self):
        c=context(['那就先只填一格：闪过画面时先别描述，只定机位。'])
        c['routing']['uptake']='closing'
        c['support']={'mode':'act','source_id':'u1','quote':'那要怎么克服呢'}
        c['harness']=turn_contract(c)
        self.assertTrue(c['harness']['closing'])
        self.assertFalse(c['harness']['help_required'])
        self.assertFalse(c['harness']['allow_advice'])
        self.assertFalse(c['harness']['allow_question'])
        self.assertEqual(c['harness']['support']['mode'],'act')
        validate_interaction({'reply':'不客气，有新想法时我们再接着聊。','interaction':{'act':'respond','question':None}},c)
        with self.assertRaises(FormatError):
            validate_interaction({'reply':'你可以继续只填机位。','interaction':{'act':'advise','question':None}},c)

    def test_confirmation_and_closing_reject_near_duplicate_reply(self):
        old='那就先只填一格。闪过画面的时候，别管构图，只定机位，是贴着人、齐眼平视，还是从高处往下压。'
        repeated='那就从最小的一格开始。闪过画面时先别管构图，只定机位，是贴着人、齐眼平视，还是从高处往下压。'
        for uptake in ('acknowledgement','closing'):
            with self.subTest(uptake=uptake):
                c=context([old]); c['routing']['uptake']=uptake; c['harness']=turn_contract(c)
                with self.assertRaises(FormatError):
                    validate_interaction({'reply':repeated,'interaction':{'act':'respond','question':None}},c)

    def test_confirmation_can_continue_with_a_genuinely_new_step(self):
        c=context(['先只定机位。']); c['routing']['uptake']='acknowledgement'; c['harness']=turn_contract(c)
        validate_interaction({'reply':'接下来把它写成一句：镜头从门口看向靠窗那桌。','interaction':{'act':'respond','question':None}},c)
    def test_topic_selection_is_not_advice_permission(self):
        c = context(signal="PREFERENCE")
        self.assertFalse(c["harness"]["allow_advice"])
        self.assertIsNone(c["harness"]["destination"])
        with self.assertRaises(FormatError):
            validate_interaction({"reply":"建议你拍一个校园短剧。", "interaction":{"act":"advise","question":None}},c)

    def test_delegating_a_topic_does_not_request_a_plan(self):
        c=context(signal="DELEGATE")
        self.assertFalse(c["harness"]["allow_plan"])
        self.assertFalse(c["harness"]["allow_advice"])

    def test_two_questions_trigger_a_breath(self):
        c=context(["哪种视频？","有什么场景？"])
        self.assertFalse(c["harness"]["allow_question"])
        with self.assertRaises(FormatError):
            validate_interaction({"reply":"那有什么瞬间？", "interaction":{"act":"ask","question":"那有什么瞬间？"}},c)

    def test_unknown_and_skip_allow_a_reply_without_homework(self):
        for c in (context(signal="NO_ANSWER"),context(task="skip")):
            self.assertFalse(c["harness"]["allow_question"])
            validate_interaction({"reply":"那这句先放着。我刚想到一个有意思的小反差。", "interaction":{"act":"share","question":None}},c)

    def test_user_can_explicitly_invite_questions(self):
        c=context(["什么？","为什么？"],invited=True)
        self.assertTrue(c["harness"]["allow_question"])

    def test_explicit_no_questions_wins_over_topic_preference(self):
        c=context(signal="PREFERENCE",request="chat",invited=True)
        c['routing']['declines_questions']=True
        c['harness']=turn_contract(c)
        self.assertFalse(c['harness']['allow_question'])

    def test_explicit_request_allows_advice_but_not_unrequested_plan(self):
        c=context(request="advice")
        validate_interaction({"reply":"你可以先试一个简单的版本。", "interaction":{"act":"advise","question":None}},c)
        with self.assertRaises(FormatError):
            validate_interaction({"reply":"接下来一周按这套安排。", "interaction":{"act":"plan","question":None}},c)

    def test_plan_permission_does_not_carry_to_later_turn(self):
        self.assertTrue(context(request="plan")["harness"]["allow_plan"])
        later=context(["可以按这三个步骤做。"],signal="PREFERENCE")
        self.assertFalse(later["harness"]["allow_plan"])

    def test_claiming_no_question_does_not_bypass_actual_text_check(self):
        with self.assertRaises(FormatError):
            validate_interaction({"reply":"你还记得什么？", "interaction":{"act":"share","question":None}},context())

    def test_period_and_imperative_do_not_bypass_no_question_rule(self):
        for text in ['今天还有什么别的没。','那你呢。','说说你当时的想法。']:
            with self.subTest(text=text),self.assertRaises(FormatError):
                validate_interaction({'reply':text,'interaction':{'act':'respond','question':None}},context(signal='NO_ANSWER'))

    def test_old_question_streak_ends_after_non_question_reply(self):
        c=context(["什么？","为什么？","那确实会有点尴尬。"])
        self.assertEqual(c["harness"]["consecutive_questions"],0)
        self.assertTrue(c["harness"]["allow_question"])


if __name__=="__main__":unittest.main()
