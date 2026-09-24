"""Bound model input independently of the size of the persisted tree."""
import json
from .attention import terms

MAX_NODES = 16
TREE_CHARS = 14000
EVIDENCE_CHARS = 32000
DIALOGUE_CHARS = 20000


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


def memory_context(session, focus=None):
    from .frame import session_frame, source_ids
    nodes = session['nodes']
    messages = session['messages']
    users = [m for m in messages if m['role'] == 'user']
    query = terms(users[-1]['text']) if users else set()
    selected = []
    cursor = focus or session.get('focus')
    # Near ancestors provide structure without bringing a deep tree in full.
    for _ in range(4):
        if cursor not in nodes or cursor in selected:
            break
        selected.append(cursor)
        cursor = nodes[cursor]['parent']
    roots = [n for n in nodes.values() if n['parent'] is None]
    ranked = sorted(nodes.values(), key=lambda n: (
        len(query & terms(n['label'] + ' ' + n['summary'])), n.get('updated_at', '')
    ), reverse=True)
    for n in ranked:
        if n['id'] not in selected:
            selected.append(n['id'])
        if len(selected) >= MAX_NODES:
            break
    tree, used = [], 2
    for key in selected[:MAX_NODES]:
        n = nodes[key]
        item = {k: n[k] for k in ('id', 'parent', 'relation', 'label', 'summary', 'kind', 'status')}
        item['recent_mentions'] = [dict(o, quote=o['quote'][:500], note=o['note'][:180], question=o.get('question', '')[:300])
                                   for o in n['occurrences'][-2:]]
        item['recent_understandings'] = n['versions'][-1:]
        if used + size(item) + 2 > TREE_CHARS:
            item['recent_mentions'] = []
            item['recent_understandings'] = []
        if used + size(item) + 2 <= TREE_CHARS:
            tree.append(item)
            used += size(item) + 2

    by_id = {m['id']: m for m in users}
    priorities = ([users[-1]['id']] if users else [])
    priorities += [(session.get('support') or {}).get('source_id')]
    priorities += source_ids(session_frame(session))
    priorities += [o['message_id'] for n in tree for o in reversed(n['recent_mentions'])]
    priorities += [m['id'] for m in reversed(users[-14:])]
    priorities += [users[0]['id']] if users else []
    evidence, included, used = [], set(), 2
    for key in priorities:
        if key not in by_id or key in included:
            continue
        m = by_id[key]
        item = {k: m[k] for k in ('id', 'role', 'text', 'at')}
        item['question'] = m.get('question', '')[:500]
        if used + size(item) + 2 <= EVIDENCE_CHARS:
            evidence.append(item)
            included.add(key)
            used += size(item) + 2
    order = {m['id']: i for i, m in enumerate(users)}
    evidence.sort(key=lambda m: order[m['id']])
    for n in tree:
        n['recent_mentions'] = [o for o in n['recent_mentions'] if o['message_id'] in included]

    conversation, used = [], 2
    fields = {'id', 'role', 'text', 'at', 'interaction', 'local_move', 'questions_paused'}
    for m in reversed(messages[-24:]):
        item = {k: v for k, v in m.items() if k in fields}
        if used + size(item) + 2 > DIALOGUE_CHARS:
            break
        conversation.append(item)
        used += size(item) + 2
    conversation.reverse()
    recent_roots = sorted(roots, key=lambda n: n.get('updated_at', ''), reverse=True)[:8]
    overview = {'total_nodes': len(nodes), 'included_nodes': len(tree), 'total_roots': len(roots),
                'recent_topics': [{'label': n['label'], 'summary': n['summary'][:100]} for n in recent_roots],
                'note': '这是相关记忆的局部视图，未展示的分支仍完整保存；省略不表示删除或否定。'}
    return {'conversation': conversation, 'evidence_messages': evidence, 'tree': tree, 'memory_overview': overview}
