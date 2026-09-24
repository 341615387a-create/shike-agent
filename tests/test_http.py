import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from agent.model import Settings
from agent.service import Service
from app import make_handler


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        settings=Settings(Path(cls.tmp.name)/'settings.json')
        settings.path.write_text(json.dumps({'base_url':'https://api.example.com','model':'example','api_key':'test-secret'}))
        service=Service(Path(cls.tmp.name)/'db.sqlite3',settings,background=False)
        cls.service=service
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),lambda *args:None)
        cls.port=cls.server.server_port
        cls.server.RequestHandlerClass=make_handler(service,settings,cls.port)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    def request(self,path='/',data=None,headers=None):
        req=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=data,headers=headers or {})
        return urllib.request.urlopen(req,timeout=3)
    def test_config_hides_credentials(self):
        with self.request('/api/config') as r:
            body=r.read().decode()
        self.assertNotIn('test-secret',body)
        self.assertEqual(json.loads(body)['app'],'shike-tree')
    def test_path_traversal_cannot_read_config(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/../data/settings.json')
        self.assertEqual(error.exception.code,404)
        error.exception.close()
    def test_other_origin_cannot_mutate(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/sessions',b'{}',{'Origin':'https://evil.example','Content-Type':'application/json'})
        self.assertEqual(error.exception.code,403)
        error.exception.close()
    def test_host_header_rebinding_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/config',headers={'Host':'evil.example'})
        self.assertEqual(error.exception.code,403)
        error.exception.close()
    def test_static_response_has_content_security_policy(self):
        with self.request('/') as r:
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
            self.assertIn('拾刻',r.read().decode())
    def test_form_post_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/settings',b'api_key=oops')
        self.assertEqual(error.exception.code,400)
        error.exception.close()

    def test_profile_endpoint_does_not_expose_internal_forgetting_keys(self):
        with self.request('/api/profile') as r:
            profile=json.load(r)
        self.assertEqual(profile['nodes'],{})
        self.assertNotIn('forgotten',profile)

    def test_z_graceful_restart_endpoint(self):
        with self.request('/api/restart',b'{}',{'Content-Type':'application/json'}) as r:
            self.assertEqual(r.status,202)
            self.assertTrue(json.load(r)['restarting'])
        self.assertTrue(self.server.restart_requested)


if __name__=='__main__':unittest.main()
