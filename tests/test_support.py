import unittest
from agent.routing import resolve_support
from agent.harness import turn_contract, validate_interaction
from agent.model import FormatError


class SupportTests(unittest.TestCase):
    def setUp(self):
        self.user = {'id':'u1','role':'user','text':'我现在想开始，不想一直放着'}
        self.preference = {'mode':'act','source_id':'u1','quote':'不想一直放着'}
        self.c = {'task':'message','routing':{'request_kind':'chat'},'evidence_messages':[self.user],
                  'conversation':[self.user], 'support':self.preference}

    def test_ack_keeps_local_help_permission_without_allowing_full_plan(self):
        self.c['conversation'].append({'id':'u2','role':'user','text':'对'})
        self.c['support']=resolve_support(self.c,{'mode':'inherit'})
        contract=turn_contract(self.c)
        self.assertTrue(contract['help_required'])
        self.assertTrue(contract['allow_advice'])
        self.assertFalse(contract['allow_plan'])
        self.assertIsNone(contract['destination'])

    def test_rest_overrides_action(self):
        self.c['evidence_messages'].append({'id':'u2','text':'算了今天先歇会儿'})
        self.c['support']=resolve_support(self.c,{'mode':'pause','source_id':'u2','quote':'今天先歇会儿'})
        self.assertFalse(turn_contract(self.c)['help_required'])

    def test_new_topic_and_clicked_branch_do_not_inherit_action_goal(self):
        self.c['routing']['uptake']='new_topic'
        self.c['support']=resolve_support(self.c,{'mode':'inherit'})
        self.assertFalse(turn_contract(self.c)['allow_advice'])
        self.c['support']=self.preference
        self.c['task']='focus'
        self.assertEqual(resolve_support(self.c,None)['mode'],'explore')

    def test_subject_shift_inside_same_task_keeps_action_help(self):
        self.c['evidence_messages'].append({'id':'u2','text':'不做比赛流程工具，想从短视频经历找项目'})
        self.c['routing'].update(uptake='new_topic', frame_update={'mode':'patch'})
        attempted={'mode':'explore','source_id':'u2','quote':'想从短视频经历找项目'}
        self.assertEqual(resolve_support(self.c,attempted),self.preference)

    def test_explicit_request_to_only_chat_can_override_action_help(self):
        self.c['evidence_messages'].append({'id':'u2','text':'先别给建议，我只想聊聊'})
        update={'mode':'explore','source_id':'u2','quote':'先别给建议，我只想聊聊'}
        self.assertEqual(resolve_support(self.c,update)['mode'],'explore')

    def test_assistant_cannot_create_user_preference(self):
        self.c['conversation'].append({'id':'a1','role':'assistant','text':'你应该行动'})
        with self.assertRaises(FormatError):
            resolve_support(self.c,{'mode':'act','source_id':'a1','quote':'你应该行动'})

    def test_old_conversation_can_recover_explicit_preference(self):
        self.c['support']=None
        self.c['evidence_messages'].append({'id':'u2','text':'对'})
        self.assertEqual(resolve_support(self.c,self.preference),self.preference)

    def test_help_does_not_override_no_questions(self):
        self.c['routing']['declines_questions']=True
        contract=turn_contract(self.c)
        self.assertTrue(contract['allow_advice'])
        self.assertFalse(contract['allow_question'])

    def test_actual_deferral_is_blocked_but_acknowledging_rejection_is_allowed(self):
        self.c['harness']=turn_contract(self.c)
        for text in ['嗯，那就先这样搁着。','可以先放一放。','那就先停在自己想做的东西这里吧。','不用急着把它变成项目。']:
            with self.subTest(text=text),self.assertRaises(FormatError):
                validate_interaction({'reply':text,'interaction':{'act':'respond','question':None}},self.c)
        validate_interaction({'reply':'不用先搁着，可以试一小段。','interaction':{'act':'advise','question':None}},self.c)

    def test_unspecified_interest_still_does_not_authorize_advice(self):
        self.c['support']=None
        self.assertFalse(turn_contract(self.c)['allow_advice'])

    def test_ack_cannot_reset_preference_even_when_model_says_explore(self):
        self.c['evidence_messages'].append({'id':'u2','text':'对啊'})
        self.assertEqual(resolve_support(self.c,{'mode':'explore','source_id':None,'quote':''}),self.preference)

    def test_unique_exact_quote_repairs_id_without_inventing_a_source(self):
        self.assertEqual(resolve_support(self.c,{'mode':'act','source_id':'typo','quote':'不想一直放着'}),self.preference)
        with self.assertRaises(FormatError):
            resolve_support(self.c,{'mode':'act','source_id':'typo','quote':'用户已决定账号定位'})

    def test_specific_question_takes_priority_over_previous_action_preference(self):
        self.c['routing']['request_kind']='answer'
        contract=turn_contract(self.c)
        self.assertFalse(contract['help_required'])
        self.assertFalse(contract['allow_advice'])
