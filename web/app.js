'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {session:null, profile:null, config:null, selected:null, inspecting:false, tab:'tree', mobileTree:false, loading:false, timer:null, route:0, replay:false,summaryEditing:null,treeRoot:null,treeManual:false,treeExpanded:new Set(),treeCollapsed:new Set(),treePages:{},historyLimit:8,pathLimit:40};
const store = {get(k){try{return localStorage.getItem(k)||'';}catch{return '';}},set(k,v){try{localStorage.setItem(k,v);}catch{}},remove(k){try{localStorage.removeItem(k);}catch{}}};
const draftKey = () => 'shike-v2-draft:' + (state.session?.id || 'new');
const timestamp = s => new Date(s).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
async function api(path,data){
  const response=await fetch('/api'+path,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});
  const value=await response.json();
  if(!response.ok){const error=new Error(value.error||'操作失败');error.status=response.status;throw error;}
  return value;
}
async function config(){
  state.config=await api('/config');const s=state.config.settings;
  $('#connection-dot').className='dot '+(s.health==='connected'?'ready':s.health==='error'?'error':'');
  if(!state.config.build&&location.port==='8780'){
    // Retire the old server before rendering controls or posting any events.
    // replace avoids leaving a broken entry in browser back/forward history.
    const destination=new URL(location.href);destination.port='8782';
    $('#app').textContent='正在连接修复后的服务…';
    location.replace(destination.href);
    await new Promise(()=>{});
  }
  let notice=$('#build-notice');
  if(state.config.build!=='local-guidance-2'){
    if(!notice){notice=document.createElement('div');notice.id='build-notice';notice.className='notice';notice.setAttribute('role','status');document.body.prepend(notice);}
    notice.textContent='新版已准备好，当前服务尚未更新。请运行项目里的 apply-update.cmd，再刷新页面。聊天记录会保留。';
  }else if(notice){notice.remove();}
}
async function sidebar(){const sessions=await api('/sessions');const link=s=>`<a href="#session/${esc(s.id)}" class="${state.session?.id===s.id?'active':''}" title="${esc(s.title)}">${esc(s.title)}</a>`;const active=sessions.filter(s=>s.status!=='DONE'),archived=sessions.filter(s=>s.status==='DONE');$('#sessions').innerHTML=(active.map(link).join('')||'<p class="muted small">暂无进行中的聊天</p>')+(archived.length?`<details class="archived-sessions" ${state.session?.status==='DONE'?'open':''}><summary>已归档 · ${archived.length}</summary>${archived.map(link).join('')}</details>`:'');}
function composer(home=false){const text=store.get(draftKey());return `<form id="message-form" class="composer"><textarea id="message-input" maxlength="12000" aria-label="你的想法" placeholder="${home?'一段经历、一个困惑，或还没想明白的念头……':'说说刚想到的，也可以说「不知道」……'}">${esc(text)}</textarea><div class="composer-footer"><span>${home?'从你此刻想到的开始。':'Enter 发送 · Shift + Enter 换行'}</span><button class="primary" type="submit" ${state.loading?'disabled':''}>${home?'开始聊聊':'发送'} ↗</button></div></form>`;}
function home(){
  const health=state.config?.settings;
  $('#app').innerHTML=`<main class="home"><div class="home-top"><span class="eyebrow">A SPACE FOR UNFINISHED THOUGHTS</span><span class="edition">拾刻 · 元素树</span></div><h1>一个念头，<br>也可以长出<em>很多可能。</em></h1><p class="lead">把正在发生的事带来。<br>我们从这里出发，聊到哪里，就从哪里接下去。</p>${health?.health==='error'?`<div class="notice">模型连接需要更新。<button data-action="settings">打开设置</button></div>`:''}${composer(true)}<div class="home-notes"><div class="home-note"><span>01 &nbsp; 从一个细节开始</span><p>不需要完整的想法。今天吃了一顿饭，也可以成为起点。</p></div><div class="home-note"><span>02 &nbsp; 顺着理解生长</span><p>同一种理解慢慢深入，新的感受长出另一条分支。</p></div><div class="home-note"><span>03 &nbsp; 聊着聊着，也许会想到</span><p>可以接住一个联想，也可以推翻它。没有结论也没关系。</p></div></div><button class="text-link" data-action="example">看看一棵树如何生长 ↗</button></main>`;
}
const profileCategories={identity:'身份与经历',work:'职业与能力',ongoing:'正在做的事',interests:'兴趣与偏好',interaction:'怎样和你交流',thinking:'思考方式 · 待确认'};
function renderProfile(){
  const p=state.profile,nodes=Object.values(p.nodes||{}),active=nodes.filter(n=>n.status==='active'),rejected=nodes.filter(n=>n.status==='rejected');
  const card=n=>{const last=n.evidence[n.evidence.length-1];return `<article class="profile-card ${n.status==='rejected'?'rejected':''}"><div class="profile-card-head"><span>${n.kind==='fact'?'你明确说过':'逐渐形成的理解'} · ${n.kind==='hypothesis'?({low:'还在观察',medium:'多次出现',high:'较为稳定'}[n.confidence]):'有原话依据'}</span><span>${n.evidence.length} 条来源</span></div><h3>${esc(n.label)}</h3><p>${esc(n.summary)}</p>${last?`<details><summary>最近的依据</summary><blockquote>${esc(last.quote)}</blockquote><a class="text-link" href="#session/${esc(last.session_id)}">回到这段聊天 ↗</a></details>`:''}<div class="profile-actions">${n.status==='active'?`<button data-action="profile-reject" data-node="${esc(n.id)}">这不准确</button>`:`<button data-action="profile-restore" data-node="${esc(n.id)}">恢复这条</button>`}<button data-action="profile-forget" data-node="${esc(n.id)}">忘记这条</button></div></article>`;};
  const sections=Object.entries(profileCategories).map(([key,title])=>{const items=active.filter(n=>n.category===key);return items.length?`<section class="profile-group"><h2>${title}</h2>${items.map(card).join('')}</section>`:'';}).join('');
  $('#app').innerHTML=`<main class="profile-page"><span class="eyebrow">ACROSS CONVERSATIONS</span><h1>关于你</h1><p class="profile-lead">这里保存跨聊天仍有帮助的理解。事实来自你的原话；思考方式只是会被继续验证的假设。你可以纠正或忘记任何一条。</p>${sections||'<div class="profile-empty"><div class="seed">⌁</div>还没有形成个人记忆。<br>继续聊天后，稳定的信息会慢慢留在这里。</div>'}${rejected.length?`<details class="rejected-profile"><summary>你认为不准确的 · ${rejected.length}</summary>${rejected.map(card).join('')}</details>`:''}</main>`;
}
async function profilePage(){state.profile=await api('/profile');renderProfile();}
async function profileEvent(action,node){
  if(action==='forget'&&!confirm('确定忘记这条吗？它会从个人记忆中删除，并阻止相同标签被自动重新加入。'))return;
  state.profile=await api('/profile/events',{action,node_id:node,version:state.profile.version,request_id:crypto.randomUUID()});renderProfile();toast(action==='forget'?'已经忘记这条。':action==='reject'?'已标记为不准确。':'已经恢复。');
}
function nodeTree(){
  const s=state.session,nodes=Object.values(s.nodes);
  if(!nodes.length)return '<div class="empty-tree"><div class="seed">⌁</div>聊到的理解会在这里留下。<br>每条分支都能回看它来自哪句话。</div>';
  const children=new Map();for(const n of nodes){if(!children.has(n.parent))children.set(n.parent,[]);children.get(n.parent).push(n);}
  const trail=new Set();let cursor=s.focus;while(cursor&&s.nodes[cursor]&&!trail.has(cursor)){trail.add(cursor);cursor=s.nodes[cursor].parent;}
  if(!state.treeManual){let chain=[...trail];state.treeRoot=chain.length>3?chain[2]:null;}
  if(state.treeRoot&&!s.nodes[state.treeRoot])state.treeRoot=null;
  const root=state.treeRoot;
  const list=(items,parent,depth)=>{
    if(!items.length)return '';
    const key=parent||'roots',count=items.length,pageSize=[6,3,2][depth];
    const currentIndex=items.findIndex(n=>trail.has(n.id));
    const page=Math.min(state.treePages[key]??Math.max(0,Math.floor(currentIndex/pageSize)),Math.floor((count-1)/pageSize));
    const visible=items.slice(page*pageSize,(page+1)*pageSize);
    const cards=visible.map(n=>{
      const child=children.get(n.id)||[];
      const expanded=!state.treeCollapsed.has(n.id)&&(trail.has(n.id)||state.treeExpanded.has(n.id));
      const canExpand=depth<2;
      const controls=child.length?`<div class="branch-controls">${canExpand?`<button data-action="toggle-branch" data-node="${esc(n.id)}" aria-expanded="${expanded}" aria-label="${expanded?'收起':'展开'}${esc(n.label)}">${expanded?'▾ 收起':'▸ 展开'} · ${child.length}</button>`:''}<button data-action="enter-branch" data-node="${esc(n.id)}">进入分支${canExpand?'':` · ${child.length}`} ↗</button></div>`:'';
      return `<li><button class="node ${n.kind} ${n.status==='rejected'?'rejected':''} ${s.focus===n.id?'current':''} ${state.selected===n.id?'viewed':''}" data-action="select-node" data-node="${esc(n.id)}" aria-pressed="${state.selected===n.id}">${s.focus===n.id?'<span class="node-current">正在聊</span>':''}<span class="node-label">${esc(n.label)}</span><span class="node-meta">${n.kind==='hypothesis'?'待确认的想法':'你提到的'} · ${n.occurrences.length} 次提及${n.relation==='alternative'?' · 另一种理解':''}${n.status==='rejected'?' · 已否定':''}</span></button>${controls}${expanded&&canExpand?list(child,n.id,depth+1):''}</li>`;
    }).join('');
    const pages=count>pageSize?`<li class="branch-pages"><button data-action="tree-page" data-parent="${esc(key)}" data-page="${page-1}" ${page===0?'disabled':''}>上一组</button><span>${page*pageSize+1}–${Math.min(count,(page+1)*pageSize)} / ${count}</span><button data-action="tree-page" data-parent="${esc(key)}" data-page="${page+1}" ${(page+1)*pageSize>=count?'disabled':''}>下一组</button></li>`:'';
    return `<ul class="tree-list">${cards}${pages}</ul>`;
  };
  const controls=`<div class="tree-navigation"><button data-action="tree-home">全部主题</button>${root?'<button data-action="tree-up">↑ 上一层</button>':''}<button data-action="tree-current">回到正在聊的分支</button></div>${root?`<p class="tree-location">当前查看：${esc(s.nodes[root].label)}</p>`:''}`;
  return controls+list(root?[s.nodes[root]]:(children.get(null)||[]),root?'view-root':null,0)+'<div class="legend"><span>用户提供的信息</span><span>尚未确认的猜想</span></div>';
}
function nodeDetail(){
  const s=state.session,n=s.nodes[state.selected];if(!n)return '';
  const offset=Math.max(0,n.occurrences.length-state.historyLimit),versionOffset=Math.max(0,n.versions.length-state.historyLimit);
  return `<section class="node-detail"><span class="eyebrow">这条理解的生长记录</span><h3>${esc(n.label)}</h3><p>${esc(n.summary)}</p><button class="pill" data-action="focus" data-node="${esc(n.id)}" ${state.replay||n.status==='rejected'||s.request_status==='GENERATING'||['PAUSED','DONE'].includes(s.status)?'disabled':''}>沿着这里聊 ↗</button>${offset||versionOffset?'<button class="text-link history-more" data-action="more-history">查看更早的记录</button>':''}${n.occurrences.slice(offset).map((o,i)=>`<div class="occurrence"><div class="index">${String(offset+i+1).padStart(2,'0')} &nbsp; ${timestamp(o.at)}</div><p>${esc(o.note)}</p><blockquote>${esc(o.quote)}</blockquote><details><summary>当时的上下文</summary><p>${esc(o.question||'这是这段探索的起点。')}</p><button class="text-link" data-action="jump" data-message="${esc(o.message_id)}">回到这条消息 ↑</button></details></div>`).join('')}${n.versions.length>1?`<details class="decision"><summary>理解的变化 · ${n.versions.length} 个版本</summary>${n.versions.slice(versionOffset).map((v,i)=>`<p>${versionOffset+i+1}. ${esc(v.summary)} <span class="muted">${timestamp(v.at)}</span></p>`).join('')}</details>`:''}</section>`;
}
const actionNames={focus:'选择了这条分支',change:'换一个方向',skip:'跳过这个问题',reflect:'总结了当前对话',pause:'暂停探索',stop:'停止了这次回复',finish:'归档了这段聊天'};
function pathView(){const s=state.session;return `<div class="path-item"><button data-action="jump" data-message="${esc(s.messages[0]?.id)}">${esc(s.title)}</button><p>故事的起点</p></div>`+(s.path.length>state.pathLimit?'<button class="text-link history-more" data-action="more-path">查看更早的足迹</button>':'')+s.path.slice(-state.pathLimit).map(p=>{const n=s.nodes[p.focus],m=s.messages.find(m=>m.id===p.message_id);return `<div class="path-item"><button data-action="${p.type==='turn'?'jump':'select-node'}" data-message="${esc(p.message_id||'')}" data-node="${esc(p.focus||'')}">${esc(p.type==='turn'?(n?.label||'继续理解'):actionNames[p.action]||p.action)}</button><p>${p.type==='turn'?`${p.steering==='USER'?'接着你这句':p.steering==='AGENT'?'拾刻接了一个话头':'接着聊'} · ${p.mode==='DISCOVER'?'了解更多':'展开思路'}`:timestamp(p.at)}</p>${m?`<p>${esc(m.text.slice(0,90))}${m.text.length>90?'…':''}</p>`:''}</div>`;}).join('');}
function treePanel(){const s=state.session,d=s.decision;return `<div class="tree-title"><h2>正在生长的理解</h2><button class="mobile-switch" data-action="toggle-tree">回到对话</button><span>${Object.keys(s.nodes).length} 个节点</span></div><p class="tree-subtitle">留住聊过的细节，也容得下不同的理解。</p><div class="tabs"><button class="${state.tab==='tree'?'selected':''}" data-action="tab-tree">元素树</button><button class="${state.tab==='path'?'selected':''}" data-action="tab-path">对话足迹</button></div>${state.tab==='tree'?nodeTree()+nodeDetail():pathView()}${d?`<details class="decision"><summary>这轮接住了什么</summary><p>${esc(d.readiness)}</p><p>你的意愿：${esc(d.intent)}</p>${s.candidates.map(c=>`<p>· ${esc(c.angle)}：${esc(c.benefit)}</p>`).join('')}</details>`:''}`;}
function messageView(m,s){
  const summary=m.role==='assistant'&&m.task==='reflect';
  if(summary){
    const latest=s.reflection?.message_id===m.id,open=latest&&state.summaryEditing===m.id,tag=latest?'button':'div';
    return `<article class="message assistant summary-message" id="m-${esc(m.id)}"><${tag} class="summary-card" ${latest?`type="button" data-action="edit-summary" data-message="${esc(m.id)}" aria-expanded="${open}" title="点击编辑；电脑上也可以右键"`:''}><span class="summary-kicker">阶段总结</span><span class="summary-text">${esc(m.text)}</span>${latest?'<span class="summary-hint">点击编辑与保存 ↗</span>':''}</${tag}>${open?`<section class="summary-editor"><label for="reflection-input">编辑这份总结</label><textarea id="reflection-input" aria-label="编辑对话总结">${esc(store.get('reflection:'+s.id)||m.text)}</textarea><div class="summary-editor-actions"><button class="primary" data-action="save" ${state.replay?'disabled':''}>保存到灵感笔记</button><button class="pill" data-action="close-summary-editor">收起</button></div></section>`:''}</article>`;
  }
  return `<article class="message ${m.role}" id="m-${esc(m.id)}">${m.role==='assistant'?'<div class="speaker">拾刻</div>':''}<div class="text">${esc(m.text)}</div>${(m.local_move?.memory_ids||[]).length?`<div class="turn-meta">${m.local_move.memory_ids.filter(id=>s.nodes[id]).map(id=>`<button data-action="select-node" data-node="${esc(id)}">想起之前聊的 · ${esc(s.nodes[id].label)}</button>`).join('')}</div>`:''}</article>`;
}
function renderSession(){
  const s=state.session,old=$('.messages'),scroll=old?.scrollTop||0,stick=!old||old.scrollHeight-old.scrollTop-old.clientHeight<100;
  const treeScroll=$('.tree-pane')?.scrollTop||0;
  const busy=s.request_status==='GENERATING'||state.loading,stopped=['PAUSED','DONE'].includes(s.status);
  const modelName=state.config?.settings?.model||'模型';
  $('#app').innerHTML=`<main class="session-shell ${state.mobileTree?'show-tree':''}"><section class="conversation"><header class="chat-header"><div><span class="eyebrow">不必想好要聊到哪里</span><h2>${esc(s.title)}</h2></div><span class="status-label">${stopped?(s.status==='PAUSED'?'暂时放一放':'已归档'):s.mode==='DISCOVER'?'○ 随便聊聊':'◉ 顺着聊聊'}</span><button class="mobile-switch" data-action="toggle-tree">看元素树</button></header>${state.replay?'<div class="example-tag">真实模型对话回放 · 用于查看树的生长，不是当前实时生成</div>':''}<div class="messages" id="messages">${s.messages.map(m=>messageView(m,s)).join('')}${s.request_status==='GENERATING'?`<div class="waiting"><span>正在想怎么接你这句话…</span><p>${esc(modelName)} 正在生成；输入已经保存，可以停止回复。</p></div>`:''}${s.response_stopped&&!stopped?'<div class="stopped-notice" role="status">已停止这次回复。你的输入仍在，可以接着聊。</div>':''}${s.request_status==='ERROR'?`<div class="error-box" role="alert"><p>${esc(s.error)}</p><p class="small">你的输入已保存。本轮尚未生成回复。</p><button data-action="retry">重试这一轮</button><button data-action="settings">模型设置</button></div>`:''}</div><footer class="chat-bottom">${state.replay?'<button class="primary" data-action="home">带着自己的念头开始 ↗</button>':stopped?`<div class="status-ended">${s.status==='PAUSED'?'这段思路先放在这里。':'这段聊天已归档，记录仍然保留。'}<button data-action="resume">接着聊</button></div>`:`<div class="actions"><button class="pill" data-action="change" ${busy?'disabled':''}>换个方向</button><button class="pill" data-action="skip" ${busy?'disabled':''}>这句先放着</button><button class="pill" data-action="reflect" ${busy?'disabled':''}>总结对话</button>${s.request_status==='GENERATING'?'<button class="pill" data-action="stop">停止回复</button>':''}<button class="pill" data-action="finish">这次到这里</button></div>${composer()}`}</footer></section><aside class="tree-pane">${treePanel()}</aside></main>`;
  if(busy){const button=$('#message-form button');if(button)button.disabled=true;}
  // The scroller was recreated at 0. Restore before paint without inheriting
  // CSS smooth scrolling; explicit history jumps still animate separately.
  const messages=$('.messages');if(messages)messages.scrollTo({top:stick?messages.scrollHeight:scroll,behavior:'instant'});
  $('.tree-pane')?.scrollTo({top:treeScroll,behavior:'instant'});
}
function refreshTree(){const pane=$('.tree-pane');if(pane){const top=pane.scrollTop;pane.innerHTML=treePanel();pane.scrollTop=top;}}
function resetTreeView(){state.treeRoot=null;state.treeManual=false;state.treeExpanded.clear();state.treeCollapsed.clear();state.treePages={};state.historyLimit=8;state.pathLimit=40;}
async function poll(){
  clearTimeout(state.timer);if(!state.session||state.replay)return;
  const sid=state.session.id,route=state.route;
  try{const s=await api('/sessions/'+sid);if(route!==state.route)return;if(s.version!==state.session.version){state.session=s;if(!state.inspecting&&s.focus)state.selected=s.focus;renderSession();await config();}}
  catch(error){if(route===state.route)toast('连接暂时中断，正在尝试重新读取。');}
  if(route===state.route)state.timer=setTimeout(poll,state.session?.request_status==='GENERATING'?1000:3500);
}
async function navigate(){
  resetTreeView();
  $('.sidebar').classList.remove('history-open');$('[data-action="history"]').setAttribute('aria-expanded','false');
  clearTimeout(state.timer);state.route++;state.session=null;state.selected=null;state.inspecting=false;state.mobileTree=false;state.loading=false;state.replay=false;state.summaryEditing=null;
  const route=state.route,hash=location.hash.slice(1);
  try{
    if(hash==='example'){const s=await api('/example');if(route!==state.route)return;state.session=s;state.replay=true;state.selected=s.focus;renderSession();}
    else if(hash==='profile'){await profilePage();if(route!==state.route)return;}
    else if(hash==='cards'){const cards=await api('/cards');if(route!==state.route)return;$('#app').innerHTML=`<main class="cards"><span class="eyebrow">留给以后的自己</span><h1>灵感笔记</h1><p class="muted">暂时的理解，也值得留下。</p>${cards.map(c=>`<article class="card"><h3>${esc(c.title)}</h3><p>${esc(c.text)}</p><a href="#session/${esc(c.session_id)}">回到这段探索 ↗</a></article>`).join('')||'<p class="muted">聊天中点「总结对话」，可以编辑并保存笔记。</p>'}</main>`;}
    else if(hash.startsWith('session/')){const s=await api('/sessions/'+encodeURIComponent(hash.slice(8)));if(route!==state.route)return;state.session=s;state.selected=s.focus;renderSession();poll();}
    else home();
    await sidebar();
  }catch(error){if(route===state.route){$('#app').innerHTML=`<main class="home"><h2>暂时无法打开</h2><p>${esc(error.message)}</p><button data-action="home">回到开始</button></main>`;}}
}
async function event(action,extra={}){
  if(!state.session||state.replay||state.loading)return;
  const sid=state.session.id,route=state.route;
  state.loading=true;
  try{const s=await api('/sessions/'+sid+'/events',{action,request_id:crypto.randomUUID(),version:state.session.version,...extra});
    if(route!==state.route)return;
    state.session=s;if(action==='message')store.remove(draftKey());if(action==='save'){store.remove('reflection:'+s.id);state.summaryEditing=null;toast('已经保存到灵感笔记。');}
    if(action==='focus')state.selected=extra.node_id;
  }catch(error){toast(error.message);if(error.status===409&&route===state.route)state.session=await api('/sessions/'+sid);}
  finally{if(route===state.route){state.loading=false;renderSession();poll();sidebar().catch(()=>{});}}
}
async function sendMessage(){
  const text=$('#message-input')?.value.trim();if(!text||state.loading||state.session?.request_status==='GENERATING')return;
  store.set(draftKey(),text);
  if(state.session){await event('message',{text});return;}
  state.loading=true;const button=$('#message-form button');button.disabled=true;
  const route=state.route;
  // Persist the creation id until acknowledgement, allowing safe retry after a dropped response.
  let pending;try{pending=JSON.parse(store.get('shike-create')||'null');}catch{}
  if(!pending||pending.text!==text)pending={text,request_id:crypto.randomUUID()};store.set('shike-create',JSON.stringify(pending));
  try{const s=await api('/sessions',pending);store.remove('shike-v2-draft:new');store.remove('shike-create');if(route===state.route)location.hash='session/'+s.id;}
  catch(error){toast(error.message);}finally{if(route===state.route){state.loading=false;if($('#message-form button'))$('#message-form button').disabled=false;}}
}
async function showSettings(){await config();const s=state.config.settings;$('#base-url').value=s.base_url;$('#model-name').value=s.model;$('#api-key').value='';$('#api-key').placeholder=s.has_key?'已配置 · 留空沿用当前密钥':'填写 API Key';$('#settings-result').textContent=s.error||'';$('#settings-dialog').showModal();}
document.addEventListener('click',async e=>{
  if(e.target.closest('#sessions a')){$('.sidebar').classList.remove('history-open');$('[data-action="history"]').setAttribute('aria-expanded','false');}
  const button=e.target.closest('[data-action]');if(!button||button.disabled)return;const action=button.dataset.action;
  try{
    if(action==='home'){location.hash='';if(!state.session)navigate();}
    else if(action==='cards')location.hash='cards';
    else if(action==='profile')location.hash='profile';
    else if(action==='profile-reject')await profileEvent('reject',button.dataset.node);
    else if(action==='profile-restore')await profileEvent('restore',button.dataset.node);
    else if(action==='profile-forget')await profileEvent('forget',button.dataset.node);
    else if(action==='history'){const open=$('.sidebar').classList.toggle('history-open');button.setAttribute('aria-expanded',String(open));}
    else if(action==='example')location.hash='example';
    else if(action==='settings')await showSettings();
    else if(action==='close-settings')$('#settings-dialog').close();
    else if(action==='select-node'){state.selected=button.dataset.node;state.historyLimit=8;state.inspecting=true;state.tab='tree';refreshTree();}
    else if(action==='toggle-branch'){const id=button.dataset.node;if(button.getAttribute('aria-expanded')==='true'){state.treeCollapsed.add(id);state.treeExpanded.delete(id);}else{state.treeCollapsed.delete(id);state.treeExpanded.add(id);}refreshTree();}
    else if(action==='enter-branch'){const id=button.dataset.node;state.treeRoot=id;state.treeManual=true;state.treeExpanded.add(id);state.treeCollapsed.delete(id);state.treePages={};state.selected=id;state.historyLimit=8;state.inspecting=true;refreshTree();$('.tree-pane').scrollTop=0;}
    else if(action==='tree-up'){state.treeRoot=state.session.nodes[state.treeRoot]?.parent||null;state.treeManual=true;state.treePages={};if(state.treeRoot){state.treeExpanded.add(state.treeRoot);state.treeCollapsed.delete(state.treeRoot);}refreshTree();$('.tree-pane').scrollTop=0;}
    else if(action==='tree-home'){state.treeRoot=null;state.treeManual=true;state.treePages={};refreshTree();$('.tree-pane').scrollTop=0;}
    else if(action==='tree-current'){resetTreeView();state.selected=state.session.focus;state.inspecting=false;refreshTree();$('.tree-pane').scrollTop=0;}
    else if(action==='tree-page'){state.treePages[button.dataset.parent]=Math.max(0,Number(button.dataset.page)||0);refreshTree();}
    else if(action==='more-history'){state.historyLimit+=8;refreshTree();}
    else if(action==='more-path'){state.pathLimit+=40;refreshTree();}
    else if(action==='tab-tree'||action==='tab-path'){state.tab=action==='tab-tree'?'tree':'path';refreshTree();}
    else if(action==='toggle-tree'){state.mobileTree=!state.mobileTree;$('.session-shell').classList.toggle('show-tree',state.mobileTree);}
    else if(action==='jump'){state.mobileTree=false;$('.session-shell').classList.remove('show-tree');document.getElementById('m-'+button.dataset.message)?.scrollIntoView({behavior:'smooth',block:'center'});}
    else if(action==='edit-summary'){state.summaryEditing=state.summaryEditing===button.dataset.message?null:button.dataset.message;renderSession();}
    else if(action==='close-summary-editor'){state.summaryEditing=null;renderSession();}
    else if(action==='focus')await event('focus',{node_id:button.dataset.node});
    else if(action==='save')await event('save',{text:$('#reflection-input').value});
    else await event(action);
  }catch(error){toast(error.message);}
});
document.addEventListener('contextmenu',e=>{const card=e.target.closest('.summary-card[data-action="edit-summary"]');if(!card)return;e.preventDefault();state.summaryEditing=card.dataset.message;renderSession();});
document.addEventListener('input',e=>{if(e.target.id==='message-input')store.set(draftKey(),e.target.value);if(e.target.id==='reflection-input')store.set('reflection:'+state.session.id,e.target.value);});
document.addEventListener('keydown',e=>{if(e.target.id==='message-input'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendMessage();}});
document.addEventListener('submit',async e=>{
  if(e.target.id==='message-form'){e.preventDefault();await sendMessage();}
  if(e.target.id==='settings-form'){
    e.preventDefault();$('#connect-button').disabled=true;$('#settings-result').textContent='正在测试连接…';
    try{await api('/settings',{base_url:$('#base-url').value,model:$('#model-name').value,api_key:$('#api-key').value});$('#api-key').value='';await config();$('#settings-result').textContent='连接成功。可以回到对话，重试刚才的输入。';toast('模型连接成功');}
    catch(error){$('#settings-result').textContent=error.message;}finally{$('#connect-button').disabled=false;}
  }
});
window.addEventListener('hashchange',navigate);
(async()=>{try{await config();await navigate();}catch(error){$('#app').innerHTML='<main class="home"><h2>本地服务暂时不可用</h2><p>请运行项目中的 start.cmd，然后刷新页面。</p></main>';}})();
