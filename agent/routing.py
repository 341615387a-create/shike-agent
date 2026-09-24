"""Conservative source exclusion, independent of conversational uncertainty."""
import re


# Excluding a whole message loses evidence. Only do so for recognized, complete
# control-only utterances; uncertainty words inside a substantive message are
# never sufficient. Unrecognized wording stays available for memory extraction.
_CONTROL = re.compile(
    r"(?:(?:我)?(?:真的?|也|还是)?(?:不知道(?:怎么想|选什么|做啥|做什么)?|不清楚|想不起来|没想法)"
    r"|(?:你)?(?:帮我|替我)(?:选(?:一个方向|条路)?|挑(?:一个方向|条路)?)"
    r"|(?:你)?(?:自己选|随便选|选条路|带着我聊|随便聊两句|随便说两句)"
    r"|换个方向|换个话题|先跳过这题|跳过|别再问我了|别问了|不想回答问题"
    r"|先放一放|暂停|这次到这里|继续|嗯|对|对啊|对的|确实|有道理|是|是的|好吧|好|好的|哎呀"
    r"|谢谢|谢谢你|感谢|辛苦了|明白了|收到)(?:啊|呀|吧|呢|哎)*"
)


def is_control_only(text):
    compact = re.sub(r"[\s，。！？,.!?、；;：:…]+", "", text)
    return bool(compact) and re.fullmatch(f"(?:{_CONTROL.pattern})+", compact) is not None


def normalize_content_routing(routing, text):
    normalized = {**routing, "pure_control": is_control_only(text)}
    if is_closing_acknowledgement(text):
        normalized.update(signal='NONE', direction='', quote='', uptake='closing', uptake_quote=text.strip(),
                          pure_control=True)
    elif is_acknowledgement(text):
        normalized.update(signal='NONE', direction='', quote='', uptake='acknowledgement', uptake_quote=text.strip())
    return normalized


def is_closing_acknowledgement(text):
    compact = re.sub(r'[\s，。！？,.!?、；;：:…]+', '', text)
    return compact in {
        '谢谢', '谢谢你', '好的谢谢', '好谢谢', '嗯谢谢', '明白了谢谢', '收到谢谢',
        '感谢', '感谢你', '好的感谢', '辛苦了', '好的辛苦了', '谢谢辛苦了'
    }


def is_acknowledgement(text):
    return is_closing_acknowledgement(text) or re.sub(r'[\s，。！？,.!?、；;：:…]+', '', text) in {
        '对', '对啊', '对的', '是', '是的', '嗯', '嗯嗯', '好', '好的', '确实', '有道理',
        '对有道理', '确实有道理'
    }


def resolve_support(context, update):
    """Retain the user's preferred kind of help, never an assistant's goal."""
    from .model import FormatError
    previous = context.get('support') or {'mode': 'explore', 'source_id': None, 'quote': ''}
    if context.get('task') in {'change', 'focus'}:
        return {'mode': 'explore', 'source_id': None, 'quote': ''}
    if update is None:  # Older adapters and non-message controls.
        return previous
    if not isinstance(update, dict) or update.get('mode') not in {'inherit', 'explore', 'act', 'pause'}:
        raise FormatError('缺少有效的帮助偏好：inherit/explore/act/pause')
    sources = {m['id']: m['text'] for m in context.get('evidence_messages', [])}
    latest = context.get('evidence_messages', [])[-1:]
    frame_mode = (context.get('routing', {}).get('frame_update') or {}).get('mode')
    changed = frame_mode in {'replace', 'clear'} or (frame_mode is None and context.get('routing', {}).get('uptake') == 'new_topic')
    if context.get('support') and latest and is_acknowledgement(latest[0]['text']):
        return previous  # A bare acknowledgement cannot cancel a preference.
    if update['mode'] == 'inherit':
        return {'mode': 'explore', 'source_id': None, 'quote': ''} if changed else previous
    key, quote = update.get('source_id'), update.get('quote')
    if update['mode'] == 'explore' and key is None and quote == '':
        return {'mode': 'explore', 'source_id': None, 'quote': ''} if not previous or changed else previous
    if isinstance(quote, str) and quote.strip() and (key not in sources or quote not in sources[key]):
        matches = [k for k, text in sources.items() if quote in text]
        if len(matches) == 1:
            key = matches[0]  # An exact unique quote repairs a mistyped opaque id.
    if key not in sources or not isinstance(quote, str) or not quote.strip() or quote not in sources[key]:
        raise FormatError('帮助偏好必须引用真实用户消息及连续原文，不能引用助手的劝说')
    if update['mode'] == 'explore' and previous.get('mode') == 'act' and not changed:
        explicit = re.search(r'(?:只想|就想).{0,8}(?:聊聊|随便聊|探索)|(?:不要|不想|别).{0,8}(?:建议|方案|推进|行动)', quote)
        if not explicit:
            return previous
    if changed and latest and key != latest[0]['id']:
        return {'mode': 'explore', 'source_id': None, 'quote': ''}
    return {'mode': update['mode'], 'source_id': key, 'quote': quote}
