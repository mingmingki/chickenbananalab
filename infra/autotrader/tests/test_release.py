import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'release.py'

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'source-only release builder is missing')
        spec = importlib.util.spec_from_file_location('release', SCRIPT)
        self.release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.release)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'app.py').write_text('value = 1\n')
        self.manifest = self.root/'source-files.txt'
        self.manifest.write_text('app.py\n')

    def test_build_only_manifest_files(self):
        (self.root/'.env').write_text('private')
        (self.root/'trades.jsonl').write_text('private')
        out = self.root/'release.tar.gz'
        self.release.build(self.root, self.manifest, out)
        with tarfile.open(out) as archive:
            self.assertEqual(archive.getnames(), ['app.py'])
            self.assertEqual(archive.extractfile('app.py').read(), b'value = 1\n')

    def test_reject_secret_or_trading_data_in_manifest(self):
        for name in ['.env', 'users/a.py', 'accounts.json', 'trades.jsonl', 'key.pem', 'state.sqlite3', '../app.py', '/app.py', 'deploy_stopped.py']:
            with self.subTest(name=name):
                self.manifest.write_text(name+'\n')
                with self.assertRaises(ValueError):
                    self.release.build(self.root,self.manifest,self.root/'bad.tar.gz')
                self.assertFalse((self.root/'bad.tar.gz').exists())

    def test_reject_symlink_even_if_name_is_allowed(self):
        (self.root/'alias.py').symlink_to(self.root/'app.py')
        self.manifest.write_text('alias.py\n')
        with self.assertRaises(ValueError):
            self.release.build(self.root,self.manifest,self.root/'bad.tar.gz')

    def test_reject_missing_source(self):
        self.manifest.write_text('missing.py\n')
        with self.assertRaises(ValueError):
            self.release.build(self.root,self.manifest,self.root/'bad.tar.gz')

    def test_reject_embedded_private_key_without_echoing_value(self):
        (self.root/'app.py').write_text('-----BEGIN PRIVATE KEY-----\nPRIVATE_DATA\n')
        with self.assertRaises(ValueError) as error:
            self.release.build(self.root,self.manifest,self.root/'bad.tar.gz')
        self.assertNotIn('PRIVATE_DATA',str(error.exception))

    def test_reject_symlinked_parent_directory(self):
        (self.root/'templates').symlink_to(self.root, target_is_directory=True)
        (self.root/'page.html').write_text('hello')
        self.manifest.write_text('templates/page.html\n')
        with self.assertRaises(ValueError):
            self.release.build(self.root,self.manifest,self.root/'bad.tar.gz')

    def test_reproducible_archive(self):
        first, second = self.root/'one.tar.gz', self.root/'two.tar.gz'
        self.release.build(self.root,self.manifest,first)
        self.release.build(self.root,self.manifest,second)
        self.assertEqual(first.read_bytes(),second.read_bytes())

if __name__ == '__main__':
    unittest.main()
