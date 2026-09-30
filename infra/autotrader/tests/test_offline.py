from pathlib import Path
import os
import subprocess
import sys
import unittest

PLUGIN = Path(__file__).resolve().parents[1]/'offline.py'

class OfflineTests(unittest.TestCase):
    def test_plugin_isolates_runtime_credentials_and_network(self):
        self.assertTrue(PLUGIN.exists(), 'offline test isolation is missing')
        code = '''
import os, socket, offline
assert os.environ['AUTOTRADER_PROJECT_DIR'] != '/live/project'
assert os.environ.get('OKX_API_KEY') is None
assert os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') is None
for call in [lambda: socket.create_connection(('example.com',443)),
             lambda: socket.socket().connect(('127.0.0.1',443)),
             lambda: socket.getaddrinfo('example.com',443)]:
    try: call()
    except RuntimeError as error: assert 'Network disabled' in str(error)
    else: raise AssertionError('network escape')
'''
        env = dict(os.environ, PYTHONPATH=str(PLUGIN.parent), OKX_API_KEY='fake-test-key',
                   GOOGLE_APPLICATION_CREDENTIALS='/fake/credential.json', AUTOTRADER_PROJECT_DIR='/live/project')
        result = subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__ == '__main__': unittest.main()
