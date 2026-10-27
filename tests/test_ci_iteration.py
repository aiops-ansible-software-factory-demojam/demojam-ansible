"""Execute the actual CI controller against isolated API fixtures."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class IterationTests(unittest.TestCase):
    def test_pending_failed_active_repaired_and_green_heads(self):
        fixture={};session='a'*32
        class Handler(BaseHTTPRequestHandler):
            def send(self,value,status=200):
                self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(value).encode())
            def do_GET(self):
                if self.path=='/candidate':
                    green=fixture['case']=='green' or (fixture['case']=='repaired' and fixture['published'])
                    existing=fixture['case']=='existing-failure'
                    self.send({'ready':fixture['case']!='no-pr','head_sha':'c'*40 if fixture['case']=='repaired'else'a'*40,
                        'base_sha':'b'*40,'number':2,'ci_passed':green,'ci_failed':existing or (fixture['published']and not green),
                        'validation_required':not green and not existing and not fixture['published']and fixture['case']not in ['no-pr','pending']})
                elif '/job_templates/?'in self.path:
                    name=parse_qs(urlsplit(self.path).query)['name'][0]
                    if name not in ['demo_ci_publish','invoice_validate']:raise AssertionError(name)
                    template=10 if name=='demo_ci_publish'else 20
                    self.send({'count':1,'results':[{'id':template,'summary_fields':{'project':{'name':'demojam-ansible'}}}]})
                elif '/jobs/'in self.path:
                    jid=int(self.path.rstrip('/').split('/')[-1]);self.send(fixture['jobs'][jid])
                elif '/v1/sessions/'in self.path:
                    self.send({'id':session,'agent_name':'automation-developer','title':'Invoice remediation issue 1',
                        'status':'running'if fixture['case']in ['active','already-notified']else'idle',
                        'items':[{'type':'message','data':{'role':'user','content':[
                            {'type':'input_text','text':'<!-- demo-ci-feedback sha='+('a'*40)+' -->'}]}}]
                        if fixture['case'] in ['already-notified', 'idle-already-notified', 'repaired'] else []})
                else:raise AssertionError(self.path)
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                if self.path=='/oauth/token':return self.send({'access_token':'fixture'})
                body=json.loads(raw)
                if self.path.endswith('/events'):
                    fixture['events'].append((self.path,body));return self.send({'id':session},202)
                template=int(self.path.split('/')[-3]);jid=len(fixture['jobs'])+1
                fixture['launches'].append((template,body))
                fixture['jobs'][jid]={'id':jid,'status':'failed'if template==20 and fixture['case']!='repaired'else'successful'}
                if template==10 and not body['extra_vars'].get('ci_pending'):fixture['published']=True
                self.send({'job':jid},201)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for case in ['no-pr','pending','failure','active','already-notified','idle-already-notified','repaired','green','existing-failure']:
                fixture.clear();fixture.update(case=case,jobs={},launches=[],events=[],published=False)
                url=f'http://127.0.0.1:{server.server_port}'
                values={'ci_repository':'demo-owner/ansible-collection-demo.pythonapp','ci_issue_number':1,'ci_author_session':session,
                    'ci_controller':url+'/api/controller/v2','ci_gate':url+'/candidate','ci_agent_api':url}
                result=subprocess.run(['ansible-playbook','-i','localhost,',str(ROOT/'playbooks/ci/iterate.yml'),'-e',json.dumps(values)],
                    env=dict(os.environ,AAP_HOST=url,AAP_USERNAME='fixture',AAP_PASSWORD='fixture',OMNIGENT_CI_CLIENT_ID='fixture',OMNIGENT_CI_CLIENT_SECRET='fixture'),
                    text=True,capture_output=True,timeout=60)
                with self.subTest(case=case):
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    expected=[]if case in ['no-pr','pending','green','existing-failure']else[10,20,10]
                    self.assertEqual([x[0]for x in fixture['launches']],expected)
                    if expected:self.assertEqual(int(fixture['launches'][-1][1]['extra_vars']['ci_validation_job_id']),2)
                    self.assertEqual(len(fixture['events']),1 if case in ['failure','active','existing-failure']else 0)
                    if fixture['events']:
                        self.assertIn(session,fixture['events'][0][0]);self.assertIn('same task',fixture['events'][0][1]['data']['content'][0]['text'])
                        self.assertEqual(fixture['events'][0][1]['type'],'message')
                        self.assertIn('demo-ci-feedback sha=',fixture['events'][0][1]['data']['content'][0]['text'])
        finally:server.shutdown();server.server_close();worker.join()


if __name__ == '__main__':unittest.main()
