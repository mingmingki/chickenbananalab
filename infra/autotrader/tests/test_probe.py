from pathlib import Path
import os
import subprocess
import tempfile
import unittest

PROBE = Path(__file__).resolve().parents[1]/'probe.sh'

class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        ssh=self.root/'ssh'
        ssh.write_text('#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["CAPTURE"]).write_text(json.dumps(sys.argv[1:]))\nprint(os.environ.get("SSH_OUTPUT",""))\nsys.exit(int(os.environ.get("SSH_STATUS","0")))\n')
        ssh.chmod(0o700)
        self.env=dict(os.environ, PATH=str(self.root)+os.pathsep+os.environ['PATH'],
            CAPTURE=str(self.root/'args'), AUTOTRADER_SSH_HOST='vm.example.invalid',
            AUTOTRADER_SSH_USER='probe', AUTOTRADER_SSH_KEY='fake-test-key',
            AUTOTRADER_SSH_KNOWN_HOSTS='fake-test-host-key')
    def run_probe(self):
        return subprocess.run(['bash',str(PROBE)],env=self.env,text=True,capture_output=True)
    def test_reject_ssh_success_without_probe_marker(self):
        self.env['SSH_OUTPUT']='unrelated forced command'
        self.assertNotEqual(self.run_probe().returncode,0)
    def test_accept_read_only_probe_and_remove_key_files(self):
        import json
        self.env['SSH_OUTPUT']='ActiveState=active\nSubState=running\nAUTOTRADER_READ_ONLY_CONNECTION_OK'
        self.assertEqual(self.run_probe().returncode,0)
        args=json.loads((self.root/'args').read_text())
        self.assertIn('StrictHostKeyChecking=yes',args)
        self.assertIn('BatchMode=yes',args)
        self.assertFalse(Path(args[args.index('-i')+1]).exists())
        command=args[-1]
        self.assertIn('systemctl show',command)
        for forbidden in ['sudo ', ' restart ', ' stop ', ' start ', 'scp ', 'python ', 'curl ']:
            self.assertNotIn(forbidden,command)
    def test_ssh_failure_propagates(self):
        self.env['SSH_STATUS']='255'
        self.assertNotEqual(self.run_probe().returncode,0)
    def test_missing_secret_fails_before_ssh(self):
        self.env.pop('AUTOTRADER_SSH_KEY')
        self.assertNotEqual(self.run_probe().returncode,0)
        self.assertFalse((self.root/'args').exists())
    def test_host_option_injection_rejected(self):
        self.env['AUTOTRADER_SSH_HOST']='-oProxyCommand=bad'
        self.assertNotEqual(self.run_probe().returncode,0)
        self.assertFalse((self.root/'args').exists())
