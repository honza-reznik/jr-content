"""Provozní URL lze změnit bez změny tajemství; prázdná předpona je záměrná."""
import tempfile
import unittest
from pathlib import Path
import jrc_backup as jb


class PublicUrls(unittest.TestCase):
    def test_old_env_without_operational_settings_matches_new_env(self):
        saved = {'PGPASSWORD': 'fixture', 'JRC_BB_SECRET_KEY': 'fixture-key'}
        current = {**saved, 'JRC_MODE': 'prod', 'JRC_PUBLIC_ORIGIN': 'http://localhost:13443',
                   'JRC_BASE_PATH': '/jrc', 'JRC_FF_MAX_CONCURRENT_UPLOADS_PER_USER': '5',
                   'JRC_FF_MAX_PENDING_UPLOAD_BYTES_PER_PROJECT': '1024'}
        self.assertEqual(jb.compare_env(current, saved), [])
        current['JRC_MODE'] = 'dev'
        self.assertEqual(jb.compare_env(current, saved), [])
        for key in ['PGPASSWORD', 'JRC_BB_SECRET_KEY', 'JRC_FF_JWT_SECRET',
                    'JRC_FF_SSO_SECRET', 'JRC_FF_SYNC_PASSWORD', 'JRC_FF_S3_SECRET_KEY',
                    'JRC_BB_ENCRYPTION_KEY_SALT', 'NEW_UNKNOWN_SECRET']:
            with self.subTest(key=key):
                self.assertEqual(jb.compare_env({**current, key: 'different'}, saved), [key])

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
