(function () {
  'use strict';
  const isCloudflare = location.hostname.endsWith('.workers.dev') || location.hostname.endsWith('.pages.dev');
  const isPages = location.hostname.endsWith('.github.io') || isCloudflare || new URLSearchParams(location.search).has('static-demo');
  if (!isPages) return;

  const nativeFetch = window.fetch.bind(window);
  const storageKey = 'shike-github-pages-demo-v1';
  const now = () => new Date().toISOString();
  const id = prefix => prefix + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const clone = value => JSON.parse(JSON.stringify(value));
  const emptyProfile = () => ({version: 0, nodes: {}, blocked: []});
  const initial = () => ({sessions: [], cards: [], profile: emptyProfile()});
  const read = () => {
    try { return {...initial(), ...JSON.parse(localStorage.getItem(storageKey) || '{}')}; }
    catch { return initial(); }
  };
  const write = data => localStorage.setItem(storageKey, JSON.stringify(data));
  const json = (value, status = 200) => Promise.resolve(new Response(JSON.stringify(value), {
    status, headers: {'Content-Type': 'application/json; charset=utf-8'}
  }));
  const title = text => text.replace(/\s+/g, ' ').trim().slice(0, 24) || '一段新的探索';
  const label = text => {
    const parts = text.replace(/[“”「」]/g, '').split(/[，。！？、；：\n]/).map(x => x.trim()).filter(Boolean);
    return (parts.sort((a, b) => b.length - a.length)[0] || text).slice(0, 18);
  };
  const summary = text => `用户提到「${text.slice(0, 80)}」。静态演示只记录这条线索，不对它作进一步推断。`;
  const responseFor = async (text, turn, session) => {
    const quote = text.length > 42 ? text.slice(0, 42) + '…' : text;
    const fallback = turn === 0
      ? `我先不急着替「${quote}」找答案。演示版会把这句话收进右侧的元素树。此刻如果只挑一个细节继续说，哪个部分最让你停了一下？`
      : `你补充的「${quote}」让前面的理解多了一层。我们可以先不决定它最终通向哪里：你更想继续描述这个细节，还是看看它和上一条感受有什么不同？`;
    if (!isCloudflare) return fallback;
    try {
      const messages = session.messages.filter(message => ['user', 'assistant'].includes(message.role) && message.task !== 'reflect')
        .map(message => ({role: message.role, content: message.text}));
      const response = await nativeFetch('/ai', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({messages})});
      const value = await response.json();
      if (!response.ok || !value.reply) throw new Error(value.error || '模型回复失败');
      return value.reply;
    } catch {
      return fallback;
    }
  };
  const addTurn = async (session, text) => {
    const userId = id('m_'), assistantId = id('m_'), nodeId = id('n_'), at = now();
    const previousAssistant = [...session.messages].reverse().find(m => m.role === 'assistant');
    session.messages.push({id: userId, role: 'user', text, at, question: previousAssistant?.text || ''});
    const parent = session.focus || null;
    const node = {
      id: nodeId, parent, relation: parent ? 'deepen' : 'root', created_at: at,
      occurrences: [{message_id: userId, quote: text, note: '用户在静态演示中输入的线索', at, question: previousAssistant?.text || ''}],
      versions: [{at, summary: summary(text), label: label(text), kind: 'fact', status: 'active', message_ids: [userId]}],
      label: label(text), summary: summary(text), kind: 'fact', status: 'active', updated_at: at
    };
    session.nodes[nodeId] = node;
    const reply = await responseFor(text, session.messages.filter(m => m.role === 'user').length - 1, session);
    session.messages.push({
      id: assistantId, role: 'assistant', text: reply, at: now(), focus: nodeId, changed_ids: [nodeId], task: 'message',
      decision: {mode: parent ? 'EXPLORE' : 'DISCOVER', steering: 'USER', readiness: '静态演示已记录用户输入', intent: text, uncertainty: parent ? 'none' : 'direction', focus: nodeId}
    });
    session.focus = nodeId;
    session.mode = parent ? 'EXPLORE' : 'DISCOVER';
    session.decision = session.messages[session.messages.length - 1].decision;
    session.candidates = [{node_id: nodeId, angle: node.label, benefit: '继续展开刚刚出现的细节', effort: 'low'}];
    session.path.push({id: id('p_'), type: 'turn', at: now(), message_id: assistantId, focus: nodeId, mode: session.mode, steering: 'USER'});
    session.version += 1;
    session.updated_at = now();
  };
  const createSession = async text => {
    const session = {
      id: id('s_'), title: title(text), status: 'ACTIVE', mode: 'DISCOVER', version: 0,
      created_at: now(), updated_at: now(), messages: [], nodes: {}, focus: null, decision: null,
      candidates: [], path: [], reflection: null, saved_cards: [], request_status: 'IDLE', error: null
    };
    await addTurn(session, text);
    return session;
  };
  const makeReflection = session => {
    const lines = session.messages.filter(m => m.role === 'user').map(m => `「${m.text.slice(0, 55)}${m.text.length > 55 ? '…' : ''}」`);
    return `这段对话从${lines.join('，后来又说到')}开始。现在留下的不是一个结论，而是 ${Object.keys(session.nodes).length} 条可以继续生长的线索。`;
  };
  const event = async (state, session, payload) => {
    const action = payload.action;
    if (action === 'message') await addTurn(session, String(payload.text || '').trim());
    else if (action === 'change') {
      session.messages.push({id: id('m_'), role: 'assistant', text: '可以。我们先不沿着刚才的解释走。回到你最开始那句话，如果把原因和目标都拿掉，哪个具体画面还留在那里？', at: now(), task: 'message'});
      session.version += 1;
    } else if (action === 'skip') {
      session.messages.push({id: id('m_'), role: 'assistant', text: '好，这句先放在这里。你可以随便说一个此刻想到的细节，我们从新的地方接上。', at: now(), task: 'message'});
      session.version += 1;
    } else if (action === 'reflect') {
      const text = makeReflection(session), messageId = id('m_');
      session.messages.push({id: messageId, role: 'assistant', text, at: now(), task: 'reflect'});
      session.reflection = {message_id: messageId, text};
      session.version += 1;
    } else if (action === 'save') {
      const card = {id: id('c_'), title: session.title, text: String(payload.text || session.reflection?.text || ''), session_id: session.id, at: now()};
      state.cards.unshift(card); session.saved_cards.push(card.id); session.version += 1;
    } else if (action === 'focus' && session.nodes[payload.node_id]) {
      session.focus = payload.node_id; session.path.push({id: id('p_'), type: 'action', action: 'focus', at: now(), focus: payload.node_id}); session.version += 1;
    } else if (action === 'finish') { session.status = 'DONE'; session.version += 1; }
    else if (action === 'resume') { session.status = 'ACTIVE'; session.version += 1; }
    session.request_status = 'IDLE'; session.updated_at = now();
    return session;
  };
  const listItem = session => ({id: session.id, title: session.title, status: session.status, mode: session.mode, request_status: session.request_status, updated_at: session.updated_at});

  window.fetch = async function (input, init = {}) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const marker = '/api';
    const index = url.pathname.indexOf(marker);
    if (index < 0) return nativeFetch(input, init);
    const path = url.pathname.slice(index + marker.length) || '/';
    let payload = {};
    if (init.body) { try { payload = JSON.parse(init.body); } catch {} }
    const state = read();

    if (path === '/config') return json({
      app: 'shike-tree', version: 2, build: 'local-guidance-2', patch: 'github-pages-demo-1',
      public_mode: true, demo_mode: true,
      settings: {base_url: '', model: isCloudflare ? 'deepseek-chat' : '浏览器演示引擎', has_key: false, health: 'connected', error: ''}
    });
    if (path === '/example') return nativeFetch('examples/tree.json').then(response => response.ok ? response : Promise.reject(new Error('示例读取失败')));
    if (path === '/sessions' && (!init.method || init.method === 'GET')) return json(state.sessions.map(listItem));
    if (path === '/sessions' && init.method === 'POST') {
      const session = await createSession(String(payload.text || '').trim()); state.sessions.unshift(session); write(state); return json(clone(session), 201);
    }
    if (path === '/cards') return json(state.cards);
    if (path === '/profile') return json(state.profile);
    if (path === '/profile/events') return json(state.profile);
    if (path === '/settings') return json({error: '静态演示不连接模型'}, 403);
    const match = path.match(/^\/sessions\/([^/]+)(\/events)?$/);
    if (match) {
      const session = state.sessions.find(item => item.id === decodeURIComponent(match[1]));
      if (!session) return json({error: '会话不存在'}, 404);
      if (!match[2]) return json(clone(session));
      await event(state, session, payload); write(state); return json(clone(session));
    }
    return json({error: '演示接口不存在'}, 404);
  };
})();
