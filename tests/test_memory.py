import copy
import unittest
from agent.memory import memory_context, size, TREE_CHARS, EVIDENCE_CHARS, DIALOGUE_CHARS, MAX_NODES


def fixture(count=1200):
    messages, nodes = [], {}
    for i in range(count):
        key, message, at = f'n{i}', f'm{i}', f'2026-09-{i:05}'
        text = '散步看到街上的变化' if i else '第一次谈到了复古贴图和胶片'
        messages.append(dict(id=message, role='user', text=text, at=at))
        nodes[key] = dict(id=key, parent=f'n{i-1}' if i else None, relation='deepen' if i else 'root',
                          label=text, summary=text, kind='fact', status='active', updated_at=at,
                          occurrences=[dict(message_id=message, quote=text, note=text, at=at)],
                          versions=[dict(summary=text, at=at)])
    return dict(nodes=nodes, messages=messages, focus=f'n{count-1}',
                support=dict(mode='act', source_id='m1', quote=messages[1]['text']))


class MemoryTests(unittest.TestCase):
    def test_deep_tree_keeps_local_ancestors_and_recalls_ancient_relevant_memory(self):
        s = fixture(); s['messages'][-1]['text'] = '我又想到了复古贴图和胶片'
        original = copy.deepcopy(s)
        c = memory_context(s)
        ids = {n['id'] for n in c['tree']}
        self.assertTrue({'n1199', 'n1198', 'n1197', 'n1196', 'n0'} <= ids)
        self.assertLessEqual(len(ids), MAX_NODES)
        self.assertEqual(c['memory_overview']['total_nodes'], 1200)
        self.assertEqual(c['tree'][0]['parent'], 'n1198')
        sources = {m['id'] for m in c['evidence_messages']}
        self.assertTrue({'m1199', 'm1', 'm0'} <= sources)
        self.assertEqual(s, original)

    def test_character_budgets_keep_latest_input_and_support_source(self):
        s = fixture(100)
        for m in s['messages']: m['text'] = '长上下文' * 3000
        for n in s['nodes'].values():
            n['summary'] = '摘要' * 250
            n['occurrences'] *= 100
            n['versions'] *= 100
        c = memory_context(s)
        for field, bound in [('tree', TREE_CHARS), ('evidence_messages', EVIDENCE_CHARS), ('conversation', DIALOGUE_CHARS)]:
            self.assertLessEqual(size(c[field]), bound)
        sources = {m['id'] for m in c['evidence_messages']}
        self.assertTrue({'m99', 'm1'} <= sources)
        self.assertEqual(c['conversation'][-1]['id'], 'm99')
        self.assertEqual(c['conversation'][-1]['text'], s['messages'][-1]['text'])
        self.assertTrue(all(o['message_id'] in sources for n in c['tree'] for o in n['recent_mentions']))
        self.assertEqual(len(s['nodes']['n99']['occurrences']), 100)

    def test_explicit_focus_is_first_and_parent_identity_is_retained(self):
        s = fixture()
        c = memory_context(s, 'n200')
        self.assertEqual(c['tree'][0]['id'], 'n200')
        self.assertEqual(c['tree'][0]['parent'], 'n199')
        self.assertEqual(s['focus'], 'n1199')

    def test_empty_tree_and_cycle_do_not_break_retrieval(self):
        self.assertEqual(memory_context(dict(nodes={}, messages=[]))['tree'], [])
        s = fixture(3); s['nodes']['n0']['parent'] = 'n2'
        self.assertEqual(len(memory_context(s)['tree']), 3)

    def test_update_from_partial_context_preserves_full_deep_tree(self):
        from agent.tree import apply_proposal
        from test_core import proposal
        s = fixture(); s['messages'].append(dict(id='fresh', role='user', text='街上的变化让我留意到老树', at='2026-09-24'))
        c = dict(task='focus', focus_override='n1199', **memory_context(s, 'n1199'))
        p = proposal(c, key='n1199', op='update', parent='n1198', relation='deepen')
        result = apply_proposal(s, p, c, '2026-09-24')
        self.assertEqual(len(result['nodes']), 1200)
        self.assertEqual(result['nodes']['n1199']['parent'], 'n1198')
        self.assertEqual(result['nodes']['n0'], s['nodes']['n0'])
        self.assertEqual(len(result['nodes']['n1199']['occurrences']), 2)


if __name__ == '__main__': unittest.main()
