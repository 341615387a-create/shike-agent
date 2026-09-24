import copy
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .model import CompatibleModel, FormatError, ModelError
from .tree import apply_proposal
from .harness import turn_contract
from .routing import normalize_content_routing, resolve_support
from .memory import memory_context
from .frame import session_frame, resolve_task_frame
from .profile import empty_profile, public_profile, profile_context, apply_profile_changes, signature


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


class Conflict(Exception):
    pass


class Service:
    def __init__(self, path, settings, model_factory=None, background=True):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.model_factory = model_factory or (lambda: CompatibleModel(settings.read()))
        self.background = background
        self.lock = threading.RLock()
        self.capacity = threading.BoundedSemaphore(3)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, session_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profile(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profile_receipts(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
            """)
            for row in db.execute("SELECT data FROM sessions").fetchall():
                s = json.loads(row[0])
                if s["request_status"] == "GENERATING":
                    s.update(request_status="ERROR", error="服务重启中断了生成。输入已保存，请重试。", job=None)
                    s["version"] += 1
                    self.write(db, s)

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def read(self, db, sid, owner_id=None):
        row = db.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError("会话不存在")
        session = json.loads(row[0])
        if owner_id is not None and session.get("owner_id", "default") != owner_id:
            raise KeyError("会话不存在")
        return session

    def write(self, db, s):
        db.execute("INSERT INTO sessions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                   (s["id"], json.dumps(s, ensure_ascii=False)))

    def read_profile(self, db, owner_id="default"):
        row = db.execute("SELECT data FROM profile WHERE id=?", (owner_id,)).fetchone()
        return json.loads(row[0]) if row else empty_profile()

    def write_profile(self, db, profile, owner_id="default"):
        db.execute("INSERT INTO profile VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                   (owner_id, json.dumps(profile, ensure_ascii=False)))

    @staticmethod
    def public(s):
        return {k: copy.deepcopy(v) for k, v in s.items()
                if k not in {"job", "pending", "diagnostic", "owner_id"}}

    def get(self, sid, owner_id="default"):
        with self.db() as db:
            return self.public(self.read(db, sid, owner_id))

    def list(self, owner_id="default"):
        with self.db() as db:
            sessions = [s for s in (json.loads(r[0]) for r in db.execute("SELECT data FROM sessions"))
                        if s.get("owner_id", "default") == owner_id]
        return sorted([{k: s[k] for k in ("id", "title", "updated_at", "status", "mode")} for s in sessions],
                      key=lambda s: s["updated_at"], reverse=True)

    def profile(self, owner_id="default"):
        with self.db() as db:
            return public_profile(self.read_profile(db, owner_id))

    def profile_event(self, payload, owner_id="default"):
        action = payload.get('action')
        if action not in {'reject', 'restore', 'forget'}:
            raise ValueError('未知的个人记忆操作')
        request = payload.get('request_id')
        if not isinstance(request, str) or not 8 <= len(request) <= 100:
            raise ValueError('请求需要有效的 request_id')
        fingerprint = hashlib.sha256(json.dumps({"owner_id": owner_id, **payload}, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
        with self.db() as db:
            row = db.execute('SELECT fingerprint FROM profile_receipts WHERE id=?', (request,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise Conflict('同一请求编号不能用于不同内容。')
                return public_profile(self.read_profile(db, owner_id))
            profile = self.read_profile(db, owner_id)
            if payload.get('version') != profile['version']:
                raise Conflict('个人记忆已更新，请刷新后再操作。')
            key = payload.get('node_id')
            if not isinstance(key, str) or key not in profile['nodes']:
                raise ValueError('这条个人记忆不存在。')
            node = profile['nodes'][key]
            if action == 'forget':
                sig = signature(node['category'], node['label'])
                profile.setdefault('forgotten', []).append(sig)
                profile['forgotten'] = list(dict.fromkeys(profile['forgotten']))[-200:]
                del profile['nodes'][key]
            else:
                node['status'] = 'rejected' if action == 'reject' else 'active'
                node['updated_at'] = now()
                node['versions'].append({'at': node['updated_at'], 'label': node['label'],
                                         'summary': node['summary'], 'kind': node['kind'],
                                         'status': node['status'], 'message_ids': [], 'user_action': action})
            profile['version'] += 1
            profile['updated_at'] = now()
            self.write_profile(db, profile, owner_id)
            db.execute('INSERT INTO profile_receipts VALUES (?,?)', (request, fingerprint))
            return public_profile(profile)

    def receipt(self, db, request, payload):
        if not isinstance(request, str) or not 8 <= len(request) <= 100:
            raise ValueError("请求需要有效的 request_id")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        row = db.execute("SELECT fingerprint,session_id FROM receipts WHERE id=?", (request,)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise Conflict("同一请求编号不能用于不同内容。")
            return row[1], fingerprint
        return None, fingerprint

    def context(self, s, task, focus=None, profile=None):
        frame = session_frame(s)
        base = {"session_id": s['id'], "task": task, "focus_override": focus, "active_focus": s.get("focus"),
                "support": copy.deepcopy(s.get('support')),
                "task_frame": frame,
                "previous_decision": {k: v for k, v in (s.get("decision") or {}).items() if k in {"mode", "focus"}},
                **memory_context(s, focus)}
        latest = next((m['text'] for m in reversed(s['messages']) if m['role'] == 'user'), '')
        base.update(profile_context(profile or empty_profile(), latest, frame))
        return base

    def stage(self, s, task, focus=None, profile=None):
        s.update(status="ACTIVE", job=uid(), request_status="GENERATING", error=None, response_stopped=False,
                 pending={"task": task, "context": self.context(s, task, focus, profile)})

    def create(self, text, request_id, owner_id="default"):
        text = self.check_text(text)
        with self.db() as db:
            existing, fingerprint = self.receipt(db, request_id, {"owner_id": owner_id, "create": text})
            if existing:
                return self.public(self.read(db, existing, owner_id))
            s = {"id": uid(), "title": text[:32], "status": "ACTIVE", "mode": "DISCOVER", "version": 1,
                 "created_at": now(), "updated_at": now(), "messages": [], "nodes": {}, "focus": None,
                 "decision": None, "candidates": [], "path": [], "reflection": None, "saved_cards": [],
                 "owner_id": owner_id}
            self.add_message(s, text)
            self.stage(s, "message", profile=self.read_profile(db, owner_id))
            self.write(db, s)
            db.execute("INSERT INTO receipts VALUES (?,?,?)", (request_id, fingerprint, s["id"]))
        self.launch(s)
        return self.get(s["id"], owner_id)

    @staticmethod
    def check_text(text):
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("请输入 1—12000 字的内容。")
        return text.strip()

    def add_message(self, s, text):
        last = next((m["text"] for m in reversed(s["messages"]) if m["role"] == "assistant"), "")
        s["messages"].append({"id": uid(), "role": "user", "text": text, "at": now(), "question": last})

    def event(self, sid, payload, owner_id="default"):
        action = payload.get("action")
        allowed = {"message", "focus", "change", "skip", "reflect", "retry", "pause", "stop", "resume", "finish", "save"}
        if action not in allowed:
            raise ValueError("未知操作")
        run = False
        with self.db() as db:
            s = self.read(db, sid, owner_id)
            existing, fingerprint = self.receipt(
                db, payload.get("request_id"), {"owner_id": owner_id, "sid": sid, **payload})
            if existing:
                return self.public(s)
            if payload.get("version") != s["version"]:
                raise Conflict("会话已更新，请刷新状态后再操作。")
            if s["request_status"] == "GENERATING" and action not in {"pause", "finish", "stop"}:
                raise Conflict("正在生成。可以停止回复，或等本轮完成。")
            if s["status"] in {"PAUSED", "DONE"} and action not in {"resume", "finish"}:
                raise Conflict("请先继续会话。")
            if action == "message":
                text = self.check_text(payload.get("text"))
                # These exact commands control lifecycle only, never generate pretend semantic content.
                commands = {"先放一放": "pause", "暂停": "pause", "这次到这里": "finish", "结束": "finish",
                            "换个方向": "change", "跳过": "skip", "整理一下": "reflect", "总结对话": "reflect"}
                command = commands.get(text.strip("。.!！ "))
                if command:
                    action = command
                else:
                    self.add_message(s, text)
                    self.stage(s, "message", profile=self.read_profile(db, owner_id))
                    run = True
            if action in {"focus", "change", "skip", "reflect"}:
                focus = payload.get("node_id") if action == "focus" else None
                if action == "focus" and (focus not in s["nodes"] or s["nodes"][focus]["status"] != "active"):
                    raise ValueError("这个分支不可继续。")
                if focus:
                    s["focus"] = focus
                s["path"].append({"id": uid(), "type": "control", "action": action, "at": now(), "focus": focus})
                self.stage(s, action, focus, self.read_profile(db, owner_id))
                run = True
            elif action == "retry":
                if s["request_status"] != "ERROR" or not s.get("pending"):
                    raise Conflict("没有待重试的生成。")
                s.update(job=uid(), request_status="GENERATING", error=None)
                run = True
            elif action == 'stop':
                if s['request_status'] != 'GENERATING':
                    raise Conflict('这轮已经完成，无需停止。')
                s.update(status='ACTIVE', job=None, pending=None, request_status='IDLE', error=None, response_stopped=True)
                s['path'].append({'id': uid(), 'type': 'control', 'action': 'stop', 'at': now(), 'focus': s['focus']})
            elif action in {"pause", "finish"}:
                if action == "finish":
                    s["pending"] = None
                s.update(status="PAUSED" if action == "pause" else "DONE", job=None, request_status="IDLE", error=None)
                if action == 'finish':
                    s['archived_at'] = now()
                s["path"].append({"id": uid(), "type": "control", "action": action, "at": now(), "focus": s["focus"]})
            elif action == "resume":
                s["status"] = "ACTIVE"
                s.pop('archived_at', None)
                if s.get("pending"):
                    s.update(job=uid(), request_status="GENERATING", error=None)
                    run = True
            elif action == "save":
                if not s.get("reflection"):
                    raise Conflict("先整理本次思路，再编辑保存。")
                content = self.check_text(payload.get("text"))
                card = {"id": uid(), "session_id": sid, "owner_id": owner_id,
                        "title": s["title"], "text": content,
                        "at": now(), "source_message_id": s["reflection"]["message_id"]}
                db.execute("INSERT INTO cards VALUES (?,?)", (card["id"], json.dumps(card, ensure_ascii=False)))
                s["saved_cards"].append(card["id"])
            s["version"] += 1
            s["updated_at"] = now()
            self.write(db, s)
            db.execute("INSERT INTO receipts VALUES (?,?,?)", (payload["request_id"], fingerprint, sid))
        if run:
            self.launch(s)
        return self.get(sid, owner_id)

    def launch(self, s):
        if self.background:
            threading.Thread(target=self.generate, args=(s["id"], s["job"], copy.deepcopy(s)), daemon=True).start()

    def generate(self, sid, job, snapshot):
        stage = "排队等待"
        started = time.monotonic()
        owner_id = snapshot.get("owner_id", "default")
        try:
            with self.capacity:
                with self.db() as db:
                    if self.read(db, sid).get("job") != job:
                        return
                model = self.model_factory()
                context = snapshot["pending"]["context"]
                if hasattr(model, "interpret"):
                    stage = "理解你的意图"
                    context["routing"] = model.interpret(context)
                    if context["evidence_messages"]:
                        context["routing"] = normalize_content_routing(
                            context["routing"], context["evidence_messages"][-1]["text"])
                    context['task_frame'] = resolve_task_frame(context, context['routing'].get('frame_update'))
                    context["non_topic_message_ids"] = []
                    if context["routing"]["pure_control"] and context["evidence_messages"]:
                        context["non_topic_message_ids"] = [context["evidence_messages"][-1]["id"]]
                context['support'] = resolve_support(context, context.get('routing', {}).get('support_update'))
                context["harness"] = turn_contract(context)
                repair = None
                for attempt in range(2):
                    proposal = None
                    try:
                        stage = "生成回应" if attempt == 0 else "修订回应"
                        proposal = model.generate(context, repair)
                        result = apply_proposal(snapshot, proposal, context, now())
                        # One editorial review can request a revision. The revision is
                        # structurally validated; do not loop subjective model critiques.
                        if attempt == 0 and hasattr(model, "audit"):
                            stage = "检查回应"
                            model.audit(context, proposal)
                        elif attempt and hasattr(model, "audit_harness"):
                            model.audit_harness(context, proposal)
                        break
                    except FormatError as exc:
                        if attempt:
                            raise
                        repair = {"issues": str(exc), "previous": proposal}
                self.settings.mark("connected")
            with self.db() as db:
                s = self.read(db, sid)
                if s.get("job") != job or s["status"] != "ACTIVE":
                    return  # Paused, ended or superseded while the network call was in flight.
                profile = self.read_profile(db, owner_id)
                profile_changed, profile_error = [], None
                try:
                    profile, profile_changed = apply_profile_changes(
                        profile, proposal.get('profile_changes', []), context, sid, now())
                    if profile_changed:
                        self.write_profile(db, profile, owner_id)
                except FormatError as exc:
                    # Personalization is optional metadata. A bad extraction must
                    # never hide an otherwise valid conversational reply.
                    profile_error = str(exc)
                message = {"id": uid(), "role": "assistant", "text": result["reply"], "at": now(),
                           "focus": result["decision"]["focus"], "changed_ids": result["changed_ids"],
                           "decision": result["decision"], "task": snapshot["pending"]["task"]}
                message["interaction"] = result["interaction"]
                message["harness"] = context["harness"]
                message['support'] = context['support']
                s['support'] = context['support']
                message['task_frame'] = context['task_frame']
                s['task_frame'] = context['task_frame']
                message["questions_paused"] = context["harness"]["questions_paused"]
                if proposal.get("local_move"):
                    message["local_move"] = proposal["local_move"]
                if profile_changed:
                    message['profile_changed_ids'] = profile_changed
                if profile_error:
                    message['profile_memory_skipped'] = True
                s["messages"].append(message)
                s.update(nodes=result["nodes"], focus=result["decision"]["focus"], decision=result["decision"],
                         mode=result["decision"]["mode"], candidates=result["candidates"],
                         job=None, pending=None, request_status="IDLE", error=None)
                s["path"].append({"id": uid(), "type": "turn", "at": now(), "message_id": message["id"],
                                  "focus": s["focus"], "mode": s["mode"], "steering": s["decision"]["steering"]})
                if message["task"] == "reflect":
                    s["status"] = "REFLECT"
                    s["reflection"] = {"message_id": message["id"], "text": message["text"]}
                s["version"] += 1
                s["updated_at"] = now()
                self.write(db, s)
        except Exception as exc:
            error = ("这轮回复未能通过上下文检查，尚未显示。内容已保留，可以重试。" if isinstance(exc, FormatError)
                     else str(exc) if isinstance(exc, ModelError) else "生成未能完成。内容已保留，可以重试。")
            if isinstance(exc, ModelError) and not isinstance(exc, FormatError):
                self.settings.mark("error", error)
            with self.db() as db:
                s = self.read(db, sid)
                if s.get("job") == job:
                    s.update(job=None, request_status="ERROR", error=error,
                             diagnostic={"stage": stage, "elapsed_seconds": round(time.monotonic() - started, 2),
                                         "code": getattr(exc, "code", "internal_error"),
                                         "detail": str(exc)[:1500] if isinstance(exc, FormatError) else getattr(exc, "diagnostic", type(exc).__name__)})
                    s["version"] += 1
                    self.write(db, s)

    def cards(self, owner_id="default"):
        with self.db() as db:
            cards = [card for card in (json.loads(r[0]) for r in db.execute("SELECT data FROM cards"))
                     if card.get("owner_id", "default") == owner_id]
            return sorted(cards,
                          key=lambda x: x["at"], reverse=True)
