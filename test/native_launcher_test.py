from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import sys
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_launcher import Advertisement,discover,make_handler


class FakeLauncher:
    def __init__(self):self.requests=[];self.stopped=False
    def status(self):return {'state':'idle'}
    def start(self,request):self.requests.append(request)
    def shutdown(self):self.stopped=True


class NativeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.launcher=FakeLauncher()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.launcher,'test-csrf',''))
        self.port=self.server.server_address[1];self.origin=f'http://127.0.0.1:{self.port}'
        self.server.RequestHandlerClass=make_handler(self.launcher,'test-csrf',self.origin)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def request(self,path,body=None,headers=None):
        connection=HTTPConnection('127.0.0.1',self.port,timeout=3)
        connection.request('GET' if body is None else 'POST',path,body,headers or {})
        response=connection.getresponse();data=response.read();connection.close()
        return response.status,data

    def test_local_page_and_utf8(self):
        status,body=self.request('/')
        self.assertEqual(status,200)
        self.assertIn('Repopulated · Native multiplayer alpha',body.decode('utf-8'))
        self.assertIn(b"sessionToken='test-csrf'",body)
        self.assertEqual(self.request('/',headers={'Host':'evil.example'})[0],403)

    def test_mutations_require_both_origin_and_token(self):
        good={'Origin':self.origin,'X-Session-Token':'test-csrf'}
        for headers in ({},{'Origin':self.origin},{'X-Session-Token':'test-csrf'},dict(good,Origin='http://evil.example')):
            self.assertEqual(self.request('/api/start','{"mode":"host"}',headers)[0],403)
        self.assertEqual(self.launcher.requests,[])
        self.assertEqual(self.request('/api/start','{"mode":"host"}',good)[0],202)
        self.assertEqual(self.launcher.requests,[{'mode':'host'}])
        self.assertEqual(self.request('/api/stop','{}',good)[0],202)
        self.assertTrue(self.launcher.stopped)

    def test_json_and_frame_limits(self):
        headers={'Origin':self.origin,'X-Session-Token':'test-csrf'}
        for body in ('not-json','[]','x'*4097):
            self.assertEqual(self.request('/api/start',body,headers)[0],400)
        self.assertEqual(self.launcher.requests,[])

    def test_discovery_does_not_disclose_join_token(self):
        host=type('Host',(),{'lock':threading.Lock(),'peer':None,'token':'secret'})()
        advertisement=Advertisement(host,'Two-player test',32916,discovery_port=0)
        try:
            servers=discover(advertisement.socket.getsockname()[1])
            self.assertTrue(servers)
            self.assertEqual(servers[0]['name'],'Two-player test')
            self.assertEqual(servers[0]['players'],1)
            self.assertTrue(servers[0]['compatible'])
            self.assertNotIn('token',servers[0])
        finally:advertisement.close()


if __name__=='__main__':unittest.main()
