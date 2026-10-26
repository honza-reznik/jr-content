"""Provozní URL lze změnit bez změny tajemství; prázdná předpona je záměrná."""
import tempfile
import unittest
from pathlib import Path
import jrc_backup as jb


class PublicUrls(unittest.TestCase):
    def test_restored_origin_and_explicit_empty_base(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / 'saved'
            target = Path(directory) / '.env'
            saved.write_text('JRC_PUBLIC_ORIGIN=http://127.0.0.1:13473\nJRC_BASE_PATH=/jrc\nPGPASSWORD=fixture\n')
            jb.install_env(saved, target, {'JRC_PUBLIC_ORIGIN': 'http://127.0.0.1:13483', 'JRC_BASE_PATH': ''})
            actual = jb.load_env_file(target)
            self.assertEqual(actual['JRC_BASE_PATH'], '')
            self.assertEqual(actual['JRC_PUBLIC_ORIGIN'], 'http://127.0.0.1:13483')
            self.assertEqual(jb.compare_env(actual, jb.load_env_file(saved)), [])
            actual['PGPASSWORD'] = 'other'
            self.assertEqual(jb.compare_env(actual, jb.load_env_file(saved)), ['PGPASSWORD'])
