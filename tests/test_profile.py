import tempfile
import unittest
from pathlib import Path

from agent.frame import empty_frame
from agent.model import FormatError, Settings
from agent.profile import apply_profile_changes, empty_profile, profile_context
from agent.service import Service, uid
from test_core import Stub, proposal


def message(key, text, at='2026-09-24'):
    return {'id': key, 'role': 'user', 'text': text, 'at': at}


def change(key='new_profile_1', op='add', category='work', kind='fact',
           label='短视频创作者', summary='用户是短视频内容创作者', source='u1', quote='短视频内容创作者'):
    return {'op': op, 'key': key, 'category': category, 'label': label, 'summary': summary,
            'kind': kind, 'status': 'active',
            'evidence': [{'message_id': source, 'quote': quote, 'note': '用户明确介绍自己的工作'}]}


class ProfileTests(unittest.TestCase):
    def test_explicit_fact_keeps_source_and_is_recalled(self):
        context = {'evidence_messages': [message('u1', '我是短视频内容创作者')]}
        profile, ids = apply_profile_changes(empty_profile(), [change()], context, 's1', 'now')
        node = profile['nodes'][ids[0]]
        self.assertEqual(node['kind'], 'fact')
        self.assertEqual(node['confidence'], 'high')
        self.assertEqual(node['evidence'][0]['session_id'], 's1')
        recalled = profile_context(profile, '最近又在拍视频', empty_frame())['profile_memory']
        self.assertEqual(recalled[0]['id'], node['id'])

    def test_thinking_style_is_hypothesis_and_confidence_needs_repetition(self):
        profile = empty_profile(); key = None
        for index in range(5):
            source = f'u{index}'
            context = {'evidence_messages': [message(source, '我还是会先拿自己的经历判断这个点子')]}
            item = change(key or 'new', 'update' if key else 'add', 'thinking', 'fact',
                          '用经历筛选想法', '用户似乎倾向用自身经历检验想法', source,
                          '先拿自己的经历判断这个点子')
            profile, ids = apply_profile_changes(profile, [item], context,
                                                  's1' if index < 3 else 's2', f't{index}')
            key = ids[0]
        node = profile['nodes'][key]
        self.assertEqual(node['kind'], 'hypothesis')
        self.assertEqual(node['confidence'], 'high')
        self.assertEqual(len(node['evidence']), 5)

    def test_invalid_or_control_only_evidence_is_rejected(self):
        context = {'evidence_messages': [message('u1', '确实')]}
        with self.assertRaises(FormatError):
            apply_profile_changes(empty_profile(), [change(quote='确实')], context, 's1', 'now')
        context = {'evidence_messages': [message('u1', '我是学生')]}
        with self.assertRaises(FormatError):
            apply_profile_changes(empty_profile(), [change(quote='短视频内容创作者')], context, 's1', 'now')

    def test_recall_is_bounded_and_rejected_nodes_are_hidden(self):
        profile = empty_profile()
        for index in range(20):
            context = {'evidence_messages': [message(f'u{index}', f'兴趣{index}做视频')]}
            item = change(category='interests', label=f'兴趣{index}', summary=f'用户喜欢兴趣{index}',
                          source=f'u{index}', quote=f'兴趣{index}做视频')
            profile, ids = apply_profile_changes(profile, [item], context, f's{index}', f't{index:02}')
        profile['nodes'][ids[0]]['status'] = 'rejected'
        recalled = profile_context(profile, '视频', empty_frame())['profile_memory']
        self.assertLessEqual(len(recalled), 12)
        self.assertNotIn(ids[0], [n['id'] for n in recalled])

    def test_cross_session_profile_is_available_to_next_conversation(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(Path(folder) / 'settings.json')
            def first(c):
                p = proposal(c)
                source = c['evidence_messages'][-1]
                p['profile_changes'] = [change(source=source['id'], quote='短视频内容创作者')]
                return p
            model = Stub(first)
            service = Service(Path(folder) / 'db.sqlite3', settings, lambda: model, background=False)
            first_session = service.create('我是短视频内容创作者', uid())
            with service.db() as db: raw = service.read(db, first_session['id'])
            service.generate(raw['id'], raw['job'], raw)
            self.assertEqual(len(service.profile()['nodes']), 1)
            second = service.create('今天想聊个新点子', uid())
            with service.db() as db: second_raw = service.read(db, second['id'])
            self.assertEqual(second_raw['pending']['context']['profile_memory'][0]['label'], '短视频创作者')

    def test_profile_controls_reject_restore_and_forget(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(Path(folder) / 'settings.json')
            service = Service(Path(folder) / 'db.sqlite3', settings, background=False)
            context = {'evidence_messages': [message('u1', '我是短视频内容创作者')]}
            profile, ids = apply_profile_changes(empty_profile(), [change()], context, 's1', 'now')
            with service.db() as db: service.write_profile(db, profile)
            value = service.profile_event({'action': 'reject', 'node_id': ids[0], 'version': profile['version'], 'request_id': uid()})
            self.assertEqual(value['nodes'][ids[0]]['status'], 'rejected')
            value = service.profile_event({'action': 'restore', 'node_id': ids[0], 'version': value['version'], 'request_id': uid()})
            self.assertEqual(value['nodes'][ids[0]]['status'], 'active')
            value = service.profile_event({'action': 'forget', 'node_id': ids[0], 'version': value['version'], 'request_id': uid()})
            self.assertNotIn(ids[0], value['nodes'])
            with service.db() as db: internal = service.read_profile(db)
            relearned, changed = apply_profile_changes(internal, [change()], context, 's2', 'later')
            self.assertEqual(changed, [])
            self.assertEqual(relearned['nodes'], {})

    def test_bad_profile_extraction_does_not_hide_valid_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(Path(folder) / 'settings.json')
            def bad_profile(c):
                p = proposal(c)
                p['profile_changes'] = [change(source=c['evidence_messages'][-1]['id'], quote='并不存在的原话')]
                return p
            model = Stub(bad_profile)
            service = Service(Path(folder) / 'db.sqlite3', settings, lambda: model, background=False)
            session = service.create('我是短视频内容创作者', uid())
            with service.db() as db: raw = service.read(db, session['id'])
            service.generate(raw['id'], raw['job'], raw)
            final = service.get(session['id'])
            self.assertEqual(final['request_status'], 'IDLE')
            self.assertTrue(final['messages'][-1]['profile_memory_skipped'])
            self.assertEqual(service.profile()['nodes'], {})


if __name__ == '__main__': unittest.main()
