import unittest
from agent.frame import empty_frame, resolve_task_frame, session_frame
from agent.model import FormatError


def claim(text, message='u2', quote=None):
    return {'text': text, 'source_id': message, 'quote': quote or text}


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.objective = claim('做一个黑客松AI项目', 'u1', '想做黑客松项目')
        self.previous = {**empty_frame(), 'status': 'active', 'objective': self.objective,
                         'open_need': claim('寻找项目灵感', 'u1', '想做黑客松项目')}
        self.latest = {'id': 'u2', 'role': 'user',
                       'text': '不想做跟黑客松比赛本身相关的，想跟自己的短视频拍摄经历相关'}
        self.context = {'task_frame': self.previous, 'evidence_messages': [
            {'id': 'u1', 'role': 'user', 'text': '想做黑客松项目'}, self.latest]}

    def test_subject_shift_preserves_objective_and_adds_exclusion(self):
        update = {'mode': 'patch',
                  'subject': claim('自己的短视频拍摄经历', quote='自己的短视频拍摄经历相关'),
                  'exclusions': [claim('黑客松比赛流程相关的项目', quote='不想做跟黑客松比赛本身相关的')],
                  'requirements': [], 'open_need': None, 'objective': None}
        frame = resolve_task_frame(self.context, update)
        self.assertEqual(frame['objective'], self.objective)
        self.assertEqual(frame['subject']['text'], '自己的短视频拍摄经历')
        self.assertEqual(frame['exclusions'][0]['text'], '黑客松比赛流程相关的项目')

    def test_acknowledgement_cannot_clear_or_replace_frame(self):
        self.context['evidence_messages'].append({'id': 'u3', 'role': 'user', 'text': '确实'})
        hostile = {'mode': 'clear', 'reason': claim('结束任务', 'u3', '确实')}
        self.assertEqual(resolve_task_frame(self.context, hostile), self.previous)

    def test_replace_requires_new_objective_from_latest_message(self):
        update = {'mode': 'replace', 'objective': claim('准备求职简历', quote='自己的短视频拍摄经历相关'),
                  'subject': None, 'open_need': None, 'requirements': [], 'exclusions': []}
        frame = resolve_task_frame(self.context, update)
        self.assertEqual(frame['objective']['text'], '准备求职简历')
        self.assertEqual(frame['requirements'], [])
        with self.assertRaises(FormatError):
            resolve_task_frame(self.context, {'mode': 'replace', 'objective': self.objective,
                                              'subject': None, 'open_need': None, 'requirements': [], 'exclusions': []})

    def test_clear_requires_current_user_evidence(self):
        update = {'mode': 'clear', 'reason': claim('先不做项目了', quote='自己的短视频拍摄经历相关')}
        self.assertEqual(resolve_task_frame(self.context, update)['status'], 'none')
        update['reason']['source_id'] = 'u1'
        with self.assertRaises(FormatError): resolve_task_frame(self.context, update)

    def test_legacy_action_session_gets_conservative_frame(self):
        session = {'messages': [{'id': 'u1', 'role': 'user', 'text': '我想做黑客松项目'}],
                   'support': {'mode': 'act', 'source_id': 'u1', 'quote': '想做黑客松项目'},
                   'decision': {'intent': '寻找一个与创作经历相关的黑客松AI项目'}}
        frame = session_frame(session)
        self.assertEqual(frame['status'], 'active')
        self.assertEqual(frame['objective']['source_id'], 'u1')


if __name__ == '__main__': unittest.main()
