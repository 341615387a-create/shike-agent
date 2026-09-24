import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from agent.model import FormatError, ModelError, Settings
from agent.service import Conflict, Service, uid, now
from agent.tree import apply_proposal


def proposal(context, label="食堂", parent=None, key="new_1", op="add", relation="root", focus=None):
    m = context['evidence_messages'][-1]
    return {'reply':'这里有一个值得继续看的细节。', 'interaction':{'act':'respond','question':None},
            'decision':{'mode':'DISCOVER','steering':'USER' if context.get('focus_override') else 'CONTINUE',
                        'readiness':'已有地点，尚缺细节','intent':'继续探索','uncertainty':'none','focus':focus or key},
            'candidates':[{'node_id':focus or key,'angle':label,'benefit':'理解这个场景','effort':'low'}],
            'changes':[{'op':op,'key':key,'parent':parent,'relation':relation,'label':label,'summary':m['text'],
                        'kind':'fact','status':'active','evidence':[{'message_id':m['id'],'quote':m['text'],'note':m['text']}]}]}


class Stub:
    def __init__(self, fn=proposal):
        self.fn=fn
        self.calls=0
    def generate(self, context, repair=None):
        self.calls+=1
        return self.fn(context)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(Path(self.tmp.name)/'settings.json')
        self.model=Stub()
        self.svc=Service(Path(self.tmp.name)/'db.sqlite3',self.settings,lambda:self.model,background=False)
    def tearDown(self):
        self.tmp.cleanup()
    def new(self,text='今天去食堂吃饭了'):
        return self.svc.create(text,uid())
    def raw(self,s):
        with self.svc.db() as db:
            return self.svc.read(db,s['id'])
    def complete(self,s):
        raw=self.raw(s)
        self.svc.generate(raw['id'],raw['job'],raw)
        return self.svc.get(s['id'])
    def event(self,s,action,**kwargs):
        return self.svc.event(s['id'],{'action':action,'request_id':uid(),'version':s['version'],**kwargs})

    def test_real_input_saved_before_model_call(self):
        s=self.new()
        self.assertEqual(s['request_status'],'GENERATING')
        self.assertEqual(len(s['messages']),1)
        self.assertEqual(self.model.calls,0)
        self.assertNotIn('pending',s)

    def test_creation_idempotent_and_payload_collision(self):
        request=uid()
        s=self.svc.create('饭',request)
        self.assertEqual(s['id'],self.svc.create('饭',request)['id'])
        with self.assertRaises(Conflict):self.svc.create('别的',request)
        self.assertEqual(len(self.svc.list()),1)

    def test_event_retry_idempotent_even_after_version_change(self):
        s=self.complete(self.new())
        payload={'action':'message','text':'人很多','request_id':uid(),'version':s['version']}
        updated=self.svc.event(s['id'],payload)
        repeated=self.svc.event(s['id'],payload)
        self.assertEqual(updated['version'],repeated['version'])
        self.assertEqual(len(repeated['messages']),3)

    def test_stale_event_does_not_append_input(self):
        s=self.complete(self.new())
        with self.assertRaises(Conflict):
            self.svc.event(s['id'],{'action':'message','text':'乱入','request_id':uid(),'version':0})
        self.assertEqual(len(self.svc.get(s['id'])['messages']),2)

    def test_source_must_be_real_quote_and_same_session(self):
        s=self.new();raw=self.raw(s);c=raw['pending']['context'];p=proposal(c)
        p['changes'][0]['evidence'][0]['quote']='凭空捏造'
        with self.assertRaises(FormatError):apply_proposal(raw,p,c,now())
        p=proposal(c);p['changes'][0]['evidence'][0]['message_id']='another-session'
        with self.assertRaises(FormatError):apply_proposal(raw,p,c,now())
        self.assertEqual(self.svc.get(s['id'])['nodes'],{})

    def test_invalid_second_change_cannot_partially_commit(self):
        def bad(c):
            p=proposal(c);p['changes'].append({**copy.deepcopy(p['changes'][0]),'key':'new_2','parent':'missing'})
            return p
        self.model=Stub(bad)
        s=self.complete(self.new())
        self.assertEqual(s['request_status'],'ERROR')
        self.assertEqual(s['nodes'],{})
        self.assertEqual(len(s['messages']),1)
        self.assertEqual(self.model.calls,2)

    def test_unknown_is_not_topic_evidence(self):
        s=self.complete(self.new('我不知道'))
        self.assertEqual(s['request_status'],'ERROR')
        self.assertEqual(s['nodes'],{})

    def test_reinforcement_retains_occurrences_and_summary_history(self):
        s=self.complete(self.new());nid=s['focus']
        s=self.event(s,'message',text='今天这里很热闹')
        self.model=Stub(lambda c:proposal(c,key=nid,op='update'))
        s=self.complete(s);node=s['nodes'][nid]
        self.assertEqual(len(node['occurrences']),2)
        self.assertEqual(len(node['versions']),2)
        self.assertEqual(node['versions'][0]['summary'],'今天去食堂吃饭了')
        self.assertTrue(node['occurrences'][1]['question'])

    def test_two_interpretations_coexist_under_same_element(self):
        s=self.complete(self.new());root=s['focus']
        for label,relation in [('热闹让我安心','deepen'),('热闹有时反而衬出孤独','alternative')]:
            s=self.event(s,'message',text=label)
            self.model=Stub(lambda c,l=label,r=relation:proposal(c,label=l,parent=root,relation=r))
            s=self.complete(s)
        children=[n for n in s['nodes'].values() if n['parent']==root]
        self.assertEqual(len(children),2)
        self.assertEqual({n['label'] for n in children},{'热闹让我安心','热闹有时反而衬出孤独'})

    def test_alternative_forks_sibling_of_selected_interpretation(self):
        s=self.complete(self.new());root=s['focus']
        s=self.event(s,'message',text='热闹让我安心')
        self.model=Stub(lambda c:proposal(c,parent=root,relation='deepen'))
        s=self.complete(s);first=s['focus']
        s=self.event(s,'message',text='有时热闹反而衬出孤独')
        self.model=Stub(lambda c:proposal(c,parent=first,relation='alternative'))
        s=self.complete(s)
        self.assertEqual(s['nodes'][s['focus']]['parent'],root)
        self.assertEqual(s['nodes'][first]['parent'],root)

    def test_delegation_does_not_become_a_topic_node(self):
        s=self.new('我不知道怎么想，你帮我选')
        raw=self.raw(s);c=raw['pending']['context']
        c['routing']={'signal':'DELEGATE'}
        c['non_topic_message_ids']=[c['evidence_messages'][-1]['id']]
        with self.assertRaises(FormatError):apply_proposal(raw,proposal(c),c,now())

    def test_mixed_information_survives_wrong_control_classification(self):
        for text in ['对啊，我前面做了个复古贴图的 现在不知道做啥了',
                     '八点前要交海报，可我不知道该选什么风格',
                     '不知道就算了是我写的歌名']:
            with self.subTest(text=text):
                class Misclassified(Stub):
                    def interpret(self, context):
                        return {'signal':'NO_ANSWER','pure_control':True,'request_kind':'chat'}
                self.model=Misclassified()
                s=self.complete(self.new(text))
                self.assertEqual(s['request_status'],'IDLE',s.get('error'))
                self.assertEqual(len(s['messages']),2)
                self.assertEqual(next(iter(s['nodes'].values()))['occurrences'][0]['quote'],text)
                self.assertFalse(s['messages'][-1]['harness']['allow_question'])

    def test_pure_unknown_is_excluded_even_when_model_calls_it_content(self):
        class Misclassified(Stub):
            def interpret(self, context):
                return {'signal':'NO_ANSWER','pure_control':False,'request_kind':'chat'}
        self.model=Misclassified()
        s=self.complete(self.new('我不知道怎么想，你帮我选'))
        self.assertEqual(s['request_status'],'ERROR')
        self.assertEqual(s['nodes'],{})

    def test_control_fragment_cannot_replace_useful_evidence(self):
        s=self.new('我做过复古贴图，现在不知道')
        raw=self.raw(s);c=raw['pending']['context'];p=proposal(c)
        p['changes'][0]['evidence'][0]['quote']='不知道'
        with self.assertRaises(FormatError):apply_proposal(raw,p,c,now())
        p['changes'][0]['evidence'][0]['quote']='我做过复古贴图'
        self.assertEqual(len(apply_proposal(raw,p,c,now())['nodes']),1)

    def test_delegation_requires_agent_steering(self):
        s=self.new();raw=self.raw(s);c=raw['pending']['context']
        c['routing']={'signal':'DELEGATE'}
        p=proposal(c)
        with self.assertRaises(FormatError):apply_proposal(raw,p,c,now())
        p['decision']['steering']='AGENT'
        result=apply_proposal(raw,p,c,now())
        self.assertEqual(result['decision']['uncertainty'],'direction')

    def test_explicit_preference_moves_focus_to_current_evidence(self):
        s=self.complete(self.new());old=s['focus']
        s=self.event(s,'message',text='我不聊食物，想聊排队软件')
        raw=self.raw(s);c=raw['pending']['context'];c['routing']={'signal':'PREFERENCE'}
        p=proposal(c,focus=old);p['decision']['steering']='USER'
        with self.assertRaises(FormatError):apply_proposal(raw,p,c,now())
        p['decision']['focus']='new_1'
        result=apply_proposal(raw,p,c,now())
        self.assertNotEqual(result['decision']['focus'],old)

    def test_same_message_revisited_is_not_counted_as_new_mention(self):
        s=self.complete(self.new());root=s['focus'];raw=self.raw(s)
        c=self.svc.context(raw,'reflect');p=proposal(c,key=root,op='update')
        p['changes'][0]['evidence'][0]['note']='同一来源的另一种压缩说法'
        result=apply_proposal(raw,p,c,now())
        self.assertEqual(len(result['nodes'][root]['occurrences']),1)
        self.assertEqual(len(result['nodes'][root]['versions']),2)

    def test_reparenting_existing_node_rejected(self):
        s=self.complete(self.new());nid=s['focus']
        s=self.event(s,'message',text='新的理解')
        self.model=Stub(lambda c:proposal(c,parent=nid,key=nid,op='update',relation='deepen'))
        s=self.complete(s)
        self.assertEqual(s['request_status'],'ERROR')
        self.assertIsNone(s['nodes'][nid]['parent'])

    def test_user_clicked_focus_cannot_be_overridden(self):
        s=self.complete(self.new());nid=s['focus']
        s=self.event(s,'focus',node_id=nid)
        self.model=Stub()  # Attempts to create and choose an unrelated root.
        s=self.complete(s)
        self.assertEqual(s['request_status'],'ERROR')
        self.assertEqual(s['focus'],nid)

    def test_pause_discards_late_response_and_resume_retries_once(self):
        s=self.new();raw=self.raw(s)
        paused=self.event(s,'pause')
        self.svc.generate(raw['id'],raw['job'],raw)
        self.assertEqual(self.svc.get(s['id'])['status'],'PAUSED')
        self.assertEqual(len(self.svc.get(s['id'])['messages']),1)
        resumed=self.event(paused,'resume')
        s=self.complete(resumed)
        self.assertEqual(len(s['messages']),2)

    def test_pause_during_network_call_discards_result(self):
        started,release=threading.Event(),threading.Event()
        def slow(c):
            started.set();release.wait(3);return proposal(c)
        self.model=Stub(slow)
        s=self.new();raw=self.raw(s)
        thread=threading.Thread(target=self.svc.generate,args=(raw['id'],raw['job'],raw))
        thread.start();self.assertTrue(started.wait(2))
        paused=self.event(s,'pause');release.set();thread.join(3)
        self.assertFalse(thread.is_alive())
        final=self.svc.get(s['id'])
        self.assertEqual(final['version'],paused['version'])
        self.assertEqual(len(final['messages']),1)

    def test_finish_invalidates_job(self):
        s=self.new();raw=self.raw(s)
        self.event(s,'finish');self.svc.generate(raw['id'],raw['job'],raw)
        final=self.svc.get(s['id'])
        self.assertEqual(final['status'],'DONE');self.assertEqual(len(final['messages']),1)

    def test_stop_discards_inflight_reply_and_accepts_new_input(self):
        started, release = threading.Event(), threading.Event()
        def slow(c):
            started.set(); release.wait(3); return proposal(c)
        self.model = Stub(slow)
        s = self.new(); raw = self.raw(s)
        thread = threading.Thread(target=self.svc.generate, args=(raw['id'], raw['job'], raw))
        thread.start(); self.assertTrue(started.wait(2))
        stopped = self.event(s, 'stop')
        release.set(); thread.join(3)
        final = self.svc.get(s['id'])
        self.assertFalse(thread.is_alive())
        self.assertEqual(final['version'], stopped['version'])
        self.assertEqual(final['status'], 'ACTIVE')
        self.assertEqual(final['request_status'], 'IDLE')
        self.assertTrue(final['response_stopped'])
        self.assertEqual(len(final['messages']), 1)
        self.assertIsNone(self.raw(final)['pending'])
        self.model = Stub()
        final = self.complete(self.event(final, 'message', text='我想聊食堂的座位'))
        self.assertEqual(final['request_status'], 'IDLE')
        self.assertEqual(len(final['messages']), 3)
        self.assertFalse(final['response_stopped'])

    def test_stop_finished_turn_does_not_change_session(self):
        s = self.complete(self.new())
        with self.assertRaises(Conflict): self.event(s, 'stop')
        self.assertEqual(self.svc.get(s['id']), s)

    def test_archive_resume_preserves_history_without_model_or_summary(self):
        s = self.complete(self.new()); calls = self.model.calls
        archived = self.event(s, 'finish')
        self.assertEqual(archived['status'], 'DONE')
        self.assertTrue(archived['archived_at'])
        self.assertEqual(archived['messages'], s['messages'])
        resumed = self.event(archived, 'resume')
        self.assertEqual(resumed['status'], 'ACTIVE')
        self.assertEqual(resumed['request_status'], 'IDLE')
        self.assertNotIn('archived_at', resumed)
        self.assertEqual(resumed['nodes'], s['nodes'])
        self.assertEqual(resumed['messages'], s['messages'])
        self.assertEqual(self.model.calls, calls)
        self.assertIsNone(resumed['reflection'])

    def test_failure_retry_keeps_one_user_message(self):
        def bad(c):raise ModelError('连接失败')
        self.model=Stub(bad)
        s=self.complete(self.new());self.assertEqual(s['request_status'],'ERROR')
        self.model=Stub();s=self.complete(self.event(s,'retry'))
        self.assertEqual(s['request_status'],'IDLE')
        self.assertEqual(len([m for m in s['messages'] if m['role']=='user']),1)

    def test_restart_marks_interrupted_generation_retryable(self):
        s=self.new()
        reopened=Service(self.svc.path,self.settings,lambda:Stub(),background=False)
        self.assertEqual(reopened.get(s['id'])['request_status'],'ERROR')
        self.assertEqual(len(reopened.get(s['id'])['messages']),1)

    def test_save_is_explicit_editable_and_idempotent(self):
        s=self.complete(self.new())
        with self.assertRaises(Conflict):self.event(s,'save',text='未整理不能保存')
        def reflect(c):
            p=proposal(c);p['changes']=[];p['decision']['focus']=None;p['candidates'][0]['node_id']=None;return p
        self.model=Stub(reflect);s=self.complete(self.event(s,'reflect'))
        payload={'action':'save','text':'我自己改过的理解','request_id':uid(),'version':s['version']}
        self.svc.event(s['id'],payload);self.svc.event(s['id'],payload)
        self.assertEqual(len(self.svc.cards()),1)
        self.assertEqual(self.svc.cards()[0]['text'],'我自己改过的理解')

    def test_model_review_requests_one_revision_without_endless_loop(self):
        class Audited(Stub):
            def audit(self,context,result):raise FormatError('用户转向被忽略')
        self.model=Audited();s=self.complete(self.new())
        self.assertEqual(s['request_status'],'IDLE');self.assertEqual(self.model.calls,2)

    def test_exact_lifecycle_commands_do_not_call_model(self):
        s=self.complete(self.new());calls=self.model.calls
        s=self.event(s,'message',text='先放一放')
        self.assertEqual(s['status'],'PAUSED');self.assertEqual(self.model.calls,calls)

    def test_public_settings_never_include_secret(self):
        self.settings.path.write_text(json.dumps({'base_url':'https://api.example.com','model':'test','api_key':'secret-value'}))
        self.assertNotIn('secret-value',json.dumps(self.settings.public()))
        self.assertTrue(self.settings.public()['has_key'])


if __name__=='__main__':unittest.main()
