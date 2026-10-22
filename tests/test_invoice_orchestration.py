"""Exercise the actual Ansible gates with isolated API response fixtures."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ProductionGateTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        self.wheel = 'b' * 64
        self.variables = {
            'invoice_candidate_sha':self.sha, 'invoice_approved_wheel_sha256':self.wheel,
            'invoice_production_workflow':{'json':{'status':'successful','name':'invoice-promote-'+self.sha+'-'+self.wheel}},
            'invoice_production_nodes':{'json':{'count':2}},
            'invoice_production_approval':[{'summary_fields':{'job':{'type':'workflow_approval','status':'successful'}}}],
            'invoice_production_deploy':[{'summary_fields':{'job':{'status':'successful'}}}],
            'invoice_production_job':{'json':{'status':'successful','scm_revision':self.sha,'artifacts':{
                'invoice_release_evidence':{'passed':True,'release':{'wheel_sha256':self.wheel}}}}},
        }
        tasks = yaml.safe_load((ROOT/'playbooks/invoice/verify-deployment.yml').read_text())[0]['tasks']
        self.tasks = [t for t in tasks if t['name'] in [
            'Require successful approval and deployment in the exact release workflow',
            'Require execution of the independently tested artifact and source']]

    def check(self, variables, passed):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'gate.yml'
            path.write_text(yaml.safe_dump([{'name':'Exercise protected production assertions', 'hosts':'localhost',
                'connection':'local','gather_facts':False,'tasks':self.tasks}]))
            result = subprocess.run(['ansible-playbook','-i','localhost,',str(path),'-e',json.dumps(variables)],
                                    capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode==0,passed,result.stdout+result.stderr)

    def test_exact_approved_execution_passes(self):
        self.check(self.variables,True)

    def test_rejected_pending_or_timed_out_approval_fails(self):
        for status in ['failed','pending','canceled']:
            with self.subTest(status=status):
                v=copy.deepcopy(self.variables)
                v['invoice_production_approval'][0]['summary_fields']['job']['status']=status
                self.check(v,False)

    def test_failed_deployment_fails(self):
        self.variables['invoice_production_deploy'][0]['summary_fields']['job']['status']='failed'
        self.check(self.variables,False)

    def test_wrong_source_or_artifact_fails(self):
        for field in ['source','wheel']:
            with self.subTest(field=field):
                v=copy.deepcopy(self.variables)
                if field=='source':v['invoice_production_job']['json']['scm_revision']='c'*40
                else:v['invoice_production_job']['json']['artifacts']['invoice_release_evidence']['release']['wheel_sha256']='c'*64
                self.check(v,False)

    def test_missing_approval_or_unexpected_workflow_graph_fails(self):
        self.variables['invoice_production_nodes']['json']['count']=3
        self.check(self.variables,False)


class ValidationStatusTests(unittest.TestCase):
    def setUp(self):
        play = yaml.safe_load((ROOT/'playbooks/invoice/publish-validation.yml').read_text())[0]
        block = next(t['block'] for t in play['tasks'] if 'block' in t)
        self.tasks = [t for t in block if t['name'] in [
            'Require the trusted validator and requested source',
            'Derive success only from completed independent acceptance']]
        self.variables = {'invoice_candidate_sha':'a'*40, 'invoice_review_record':{'json':{
            'summary_fields':{'job_template':{'name':'invoice_validate'},'project':{'name':'demojam-ansible'}},
            'extra_vars':json.dumps({'invoice_candidate_sha':'a'*40}), 'status':'successful',
            'artifacts':{'invoice_validation':{'passed':True,'source_revision':'a'*40,
                          'acceptance_suite':'trusted-invoice-v1','backend':'aws'}}}}}

    def check(self, expected, passes=True):
        tasks = self.tasks + [{'name':'Require the expected validation outcome',
            'ansible.builtin.assert':{'that':'invoice_review_passed == expected_result'}}]
        variables = dict(self.variables, expected_result=expected)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'gate.yml'
            path.write_text(yaml.safe_dump([{'name':'Exercise validation provenance', 'hosts':'localhost',
                'connection':'local','gather_facts':False,'vars':variables,'tasks':tasks}]))
            result=subprocess.run(['ansible-playbook','-i','localhost,',str(path)],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode==0, passes, result.stdout+result.stderr)

    def test_only_trusted_successful_acceptance_can_publish_success(self):
        self.check(True)

    def test_failed_or_incomplete_acceptance_cannot_publish_success(self):
        for failure in ['job','suite','artifact']:
            with self.subTest(failure=failure):
                original=copy.deepcopy(self.variables)
                job=self.variables['invoice_review_record']['json']
                if failure=='job':job['status']='failed'
                if failure=='suite':job['artifacts']['invoice_validation']['acceptance_suite']='candidate-only'
                if failure=='artifact':job['artifacts']={}
                self.check(False)
                self.variables=original

    def test_other_job_templates_or_heads_are_rejected(self):
        self.variables['invoice_review_record']['json']['summary_fields']['job_template']['name']='invoice_baseline'
        self.check(False,False)


if __name__=='__main__':
    unittest.main()
