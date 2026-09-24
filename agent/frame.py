"""A cited task frame, separate from the conversational element tree."""
import copy
from .model import FormatError
from .routing import is_acknowledgement


SCALARS = ('objective', 'subject', 'open_need')
LISTS = ('requirements', 'exclusions')


def empty_frame():
    return {'status': 'none', 'objective': None, 'subject': None,
            'requirements': [], 'exclusions': [], 'open_need': None}


def _claim(value, sources, latest_id=None):
    if not isinstance(value, dict):
        raise FormatError('任务框架中的内容必须带有用户原文来源')
    text, source_id, quote = value.get('text'), value.get('source_id'), value.get('quote')
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        raise FormatError('任务框架摘要须为1—300字')
    if source_id not in sources or not isinstance(quote, str) or not quote.strip() or quote not in sources[source_id]:
        raise FormatError('任务框架只能引用真实用户消息及连续原文')
    if latest_id and source_id != latest_id:
        raise FormatError('任务框架的新变动必须来自本轮用户原文')
    return {'text': text.strip(), 'source_id': source_id, 'quote': quote.strip()}


def session_frame(session):
    """Return the stored frame, or a conservative bridge for pre-frame sessions."""
    value = session.get('task_frame')
    if isinstance(value, dict) and value.get('status') in {'none', 'active'}:
        return copy.deepcopy(value)
    support, decision = session.get('support') or {}, session.get('decision') or {}
    source_id, quote, intent = support.get('source_id'), support.get('quote'), decision.get('intent')
    user_sources = {m['id']: m['text'] for m in session.get('messages', []) if m.get('role') == 'user'}
    if support.get('mode') == 'act' and source_id in user_sources and isinstance(quote, str) and quote in user_sources[source_id] and intent:
        claim = {'text': str(intent)[:300], 'source_id': source_id, 'quote': quote}
        return {'status': 'active', 'objective': claim, 'subject': None,
                'requirements': [], 'exclusions': [], 'open_need': copy.deepcopy(claim)}
    return empty_frame()


def resolve_task_frame(context, update):
    """Apply only cited changes; a bare acknowledgement can never rewrite the frame."""
    previous = copy.deepcopy(context.get('task_frame') or empty_frame())
    users = context.get('evidence_messages', [])
    latest = users[-1] if users else None
    if latest and is_acknowledgement(latest['text']):
        return previous
    if update is None:
        return previous
    if not isinstance(update, dict) or update.get('mode') not in {'inherit', 'patch', 'replace', 'clear'}:
        raise FormatError('缺少有效的任务框架变动：inherit/patch/replace/clear')
    mode = update['mode']
    if mode == 'inherit':
        return previous
    sources = {m['id']: m['text'] for m in users}
    latest_id = latest['id'] if latest else None
    if mode == 'clear':
        _claim(update.get('reason'), sources, latest_id)
        return empty_frame()
    base = empty_frame() if mode == 'replace' else previous
    for field in SCALARS:
        value = update.get(field)
        if value is not None:
            base[field] = _claim(value, sources, latest_id)
    for field in LISTS:
        values = update.get(field, [])
        if not isinstance(values, list) or len(values) > 6:
            raise FormatError(f'任务框架的{field}格式无效')
        known = {item['text'] for item in base[field]}
        for value in values:
            item = _claim(value, sources, latest_id)
            if item['text'] not in known:
                base[field].append(item)
                known.add(item['text'])
        base[field] = base[field][-6:]
    if mode == 'replace' and base['objective'] is None:
        raise FormatError('替换任务框架必须给出本轮用户明确的新任务')
    base['status'] = 'active' if base['objective'] or base['open_need'] else 'none'
    return base


def source_ids(frame):
    ids = []
    for field in SCALARS:
        if isinstance(frame.get(field), dict):
            ids.append(frame[field].get('source_id'))
    for field in LISTS:
        ids.extend(item.get('source_id') for item in frame.get(field, []) if isinstance(item, dict))
    return [key for key in ids if isinstance(key, str)]
