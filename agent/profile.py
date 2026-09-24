"""Cross-session, cited user memory with bounded retrieval."""
import copy
import hashlib
import json
import re
import uuid
from .attention import terms
from .model import FormatError
from .routing import is_control_only


CATEGORIES = {'identity', 'work', 'interests', 'interaction', 'thinking', 'ongoing'}
MAX_RECALLED = 12
PROFILE_CHARS = 14000


def empty_profile():
    return {'version': 0, 'nodes': {}, 'forgotten': [], 'updated_at': None}


def public_profile(profile):
    return {k: copy.deepcopy(v) for k, v in profile.items() if k != 'forgotten'}


def _text(value, limit, field):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise FormatError(f'{field}须为1—{limit}字')
    return value.strip()


def signature(category, label):
    compact = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', label.lower())
    return hashlib.sha256(f'{category}:{compact}'.encode()).hexdigest()[:24]


def _confidence(node):
    if node['kind'] == 'fact':
        return 'high'
    messages = {(item['session_id'], item['message_id']) for item in node['evidence']}
    sessions = {item['session_id'] for item in node['evidence']}
    if len(messages) >= 5 and len(sessions) >= 2:
        return 'high'
    if len(messages) >= 3:
        return 'medium'
    return 'low'


def apply_profile_changes(profile, changes, context, session_id, at):
    if changes is None:
        changes = []
    if not isinstance(changes, list) or len(changes) > 4:
        raise FormatError('每轮最多四条个人记忆变动')
    result = copy.deepcopy(profile)
    sources = {m['id']: m for m in context.get('evidence_messages', [])}
    touched = []
    forgotten = set(result.get('forgotten', []))
    for change in changes:
        if not isinstance(change, dict) or change.get('op') not in {'add', 'update'}:
            raise FormatError('个人记忆操作只能是add或update')
        op = change['op']
        category = change.get('category')
        if category not in CATEGORIES:
            raise FormatError('个人记忆类别无效')
        label = _text(change.get('label'), 50, '个人记忆标签')
        summary = _text(change.get('summary'), 500, '个人记忆摘要')
        kind, status = change.get('kind'), change.get('status')
        if kind not in {'fact', 'hypothesis'} or status not in {'active', 'rejected'}:
            raise FormatError('个人记忆的事实边界或状态无效')
        if category == 'thinking':
            kind = 'hypothesis'
        evidence = change.get('evidence')
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
            raise FormatError('个人记忆必须有1—4条用户来源')
        occurrences = []
        for item in evidence:
            if not isinstance(item, dict) or item.get('message_id') not in sources:
                raise FormatError('个人记忆不能引用不存在或其他会话的消息')
            message = sources[item['message_id']]
            quote = _text(item.get('quote'), 1000, '个人记忆原文')
            note = _text(item.get('note'), 300, '个人记忆来源说明')
            if quote not in message['text'] or is_control_only(quote):
                raise FormatError('个人记忆须引用用户有实际内容的连续原文')
            occurrences.append({'session_id': session_id, 'message_id': message['id'], 'quote': quote,
                                'note': note, 'at': message['at']})
        key = change.get('key')
        sig = signature(category, label)
        if op == 'add':
            if sig in forgotten:
                continue
            existing = next((n for n in result['nodes'].values()
                             if signature(n['category'], n['label']) == sig), None)
            if existing:
                node = existing
                key = node['id']
            else:
                key = 'p_' + uuid.uuid4().hex[:16]
                node = {'id': key, 'category': category, 'created_at': at,
                        'evidence': [], 'versions': []}
                result['nodes'][key] = node
        else:
            if not isinstance(key, str) or key not in result['nodes']:
                raise FormatError('更新的个人记忆不存在')
            node = result['nodes'][key]
            if node['category'] != category:
                raise FormatError('更新不能改变个人记忆类别')
        known = {(item['session_id'], item['message_id']) for item in node['evidence']}
        for item in occurrences:
            marker = (item['session_id'], item['message_id'])
            if marker not in known:
                node['evidence'].append(item)
                known.add(marker)
        node.update(label=label, summary=summary, kind=kind, status=status, updated_at=at)
        node['confidence'] = _confidence(node)
        node['versions'].append({'at': at, 'label': label, 'summary': summary,
                                 'kind': kind, 'status': status,
                                 'message_ids': [item['message_id'] for item in occurrences]})
        touched.append(key)
    if touched:
        result['version'] = int(result.get('version', 0)) + 1
        result['updated_at'] = at
    return result, touched


def profile_context(profile, latest_text='', task_frame=None):
    frame_text = ' '.join(item.get('text', '') for item in [
        (task_frame or {}).get('objective') or {}, (task_frame or {}).get('subject') or {},
        (task_frame or {}).get('open_need') or {}])
    query = terms(latest_text + ' ' + frame_text)
    active = [node for node in profile.get('nodes', {}).values() if node.get('status') == 'active']
    category_weight = {'interaction': 7, 'identity': 4, 'work': 4, 'ongoing': 4,
                       'interests': 2, 'thinking': 1}
    ranked = sorted(active, key=lambda node: (
        len(query & terms(node['label'] + ' ' + node['summary'])) * 10 + category_weight[node['category']],
        node.get('updated_at', '')), reverse=True)
    selected, used = [], 2
    for node in ranked[:MAX_RECALLED]:
        item = {k: node[k] for k in ('id', 'category', 'label', 'summary', 'kind', 'confidence')}
        item['recent_evidence'] = node.get('evidence', [])[-2:]
        cost = len(json.dumps(item, ensure_ascii=False)) + 2
        if used + cost <= PROFILE_CHARS:
            selected.append(item)
            used += cost
    counts = {category: len([n for n in active if n['category'] == category]) for category in CATEGORIES}
    return {'profile_memory': selected,
            'profile_overview': {'total_active': len(active), 'included': len(selected), 'categories': counts,
                                 'note': '这是跨会话个人记忆的相关子集；当前用户原话始终优先。'}}
