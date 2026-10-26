"""Execute the real publisher against a local Controller/Forgejo API fixture."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublisherTests(unittest.TestCase):
    def test_only_trusted_exact_source_acceptance_can_publish_green(self):
        sha, base = 'a'*40, 'b'*40
        good = {'status':'successful','summary_fields':{'project':{'name':'demojam-ansible'},
            'job_template':{'name':'webapp_validate'}},'extra_vars':json.dumps({'webapp_candidate_sha':sha}),
            'artifacts':{'ci_validation':{'passed':True,'source_revision':sha,'base_revision':base,
                'suite':'webapp-ci-v2','lint':True,'build':True,'unit':True,'molecule':True,
                'red':True,'green':True,'durable':True,'feedback':'all passed'}}}
        fixture={'job':None,'posts':[]}
        class Handler(BaseHTTPRequestHandler):
            def send(self, value, status=200):
                body=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
            def do_GET(self):
                self.send(fixture['job'] if '/jobs/'in self.path else {'state':'open','merge_base':base,
                    'head':{'sha':sha,'repo':{'full_name':'demo-owner/ansible-collection-demo.webapp'}},
                    'base':{'ref':'main','repo':{'full_name':'demo-owner/ansible-collection-demo.webapp'}}})
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture['posts'].append((self.path,payload,self.headers.get('Sudo')))
                self.send({'creator':{'login':'demo-reviewer'}},201)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for case in ['good','failed','missing-durable','wrong-sha','wrong-template','wrong-project']:
                job=copy.deepcopy(good);fixture['posts']=[];fixture['job']=job
                if case=='failed':job['status']='failed';job['artifacts']['ci_validation'].update(passed=False,feedback='YAML lint failed')
                if case=='missing-durable':job['artifacts']['ci_validation']['durable']=False
                if case=='wrong-sha':job['extra_vars']=json.dumps({'webapp_candidate_sha':'c'*40})
                if case=='wrong-template':job['summary_fields']['job_template']['name']='agent-job'
                if case=='wrong-project':job['summary_fields']['project']['name']='candidate'
                url=f'http://127.0.0.1:{server.server_port}'
                result=subprocess.run(['ansible-playbook','-i','localhost,',str(ROOT/'playbooks/ci/publish.yml'),'-e',json.dumps({
                    'ci_repository':'demo-owner/ansible-collection-demo.webapp','ci_candidate_sha':sha,'ci_pr_number':2,'ci_validation_job_id':101})],
                    env=dict(os.environ,FORGEJO_URL=url,FORGEJO_TOKEN='fixture',AAP_HOST=url,AAP_USERNAME='fixture',AAP_PASSWORD='fixture'),
                    text=True,capture_output=True,timeout=60)
                with self.subTest(case=case):
                    if case.startswith('wrong'):
                        self.assertNotEqual(result.returncode,0);self.assertEqual(fixture['posts'],[])
                    else:
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        status=fixture['posts'][0]
                        self.assertEqual(status[1]['state'],'success'if case=='good'else'failure')
                        self.assertEqual(status[2],'demo-reviewer')
                        self.assertIn(f'<!-- demo-ci sha={sha} -->',fixture['posts'][1][1]['body'])
                        if case=='failed':self.assertIn('YAML lint failed',fixture['posts'][1][1]['body'])
        finally:server.shutdown();server.server_close();worker.join()

    def test_shared_feedback_is_bounded_and_does_not_expose_credentials(self):
        spec=importlib.util.spec_from_file_location('ci_feedback',ROOT/'playbooks/ci/feedback.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        text=module.sanitize({'stage':'lint','stderr':'authorization: Bearer abcdefgh\npassword=secretvalue\nhttps://user:pass@host/path\nPRIVATE-CLOUD-SECRET'},
            {'AWS_SECRET_ACCESS_KEY':'PRIVATE-CLOUD-SECRET'})
        for secret in ['abcdefgh','secretvalue','user:pass','PRIVATE-CLOUD-SECRET']:self.assertNotIn(secret,text)
        self.assertLessEqual(len(module.sanitize({'stdout':'x'*10000},{})),6000)
        report = {'stage':'Molecule', 'stderr':'setup\n'*2000 + 'TASK [Keep policy idempotent]\nchanged=1\nCRITICAL Idempotence test failed\n' + 'cleanup succeeded\n'*2000}
        feedback = module.sanitize(report,{})
        self.assertIn('Keep policy idempotent',feedback)
        self.assertIn('Idempotence test failed',feedback)
        self.assertLessEqual(len(feedback),6000)


if __name__ == '__main__':unittest.main()
