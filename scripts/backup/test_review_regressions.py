"""Regrese nálezů review; falešný Docker zde nikdy neovládá stroj."""
import io
import re
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jrc_backup as jb
from test_jrc_backup import Base, make_backup, RestoreEmptinessGuards

SCRIPTS = Path(__file__).parent


class IntegrityRegressions(Base):
    def manifest(self, directory, change):
        path = directory / jb.MANIFEST
        manifest = json.loads(path.read_text())
        change(manifest)
        path.write_text(json.dumps(manifest))

    def rehash(self, directory, name):
        digest, size = jb.sha256_file(directory / name)
        self.manifest(directory, lambda m: m['files'].update({name: {'size': size, 'sha256': digest}}))

    def partial(self):
        directory = make_backup(self.root, '20261024-100001')
        partial = directory.with_name(directory.name + '.partial')
        directory.rename(partial)
        (partial / 'state.json').write_text(json.dumps({
            'project': self.root.name, 'stamp': directory.name, 'postgres_version': '18.6',
            'buckets': json.loads((partial / jb.MANIFEST).read_text())['buckets'], 'vanished': {},
        }))
        (partial / jb.MANIFEST).unlink()
        return partial

    def finish(self):
        jb.cmd_finish(SimpleNamespace(project=self.root.name, workspace=str(self.work), keep='1',
                                     stamp='20261024-100001', media='absent', no_env=True,
                                     git_rev='test', git_dirty='no'))

    def test_5_vanished_never_finishes_or_rotates(self):
        previous = make_backup(self.root, '20261024-100000')
        partial = self.partial()
        state = json.loads((partial / 'state.json').read_text())
        state['vanished'] = {'jrc-app': ['deleted']}
        (partial / 'state.json').write_text(json.dumps(state))
        with mock.patch.object(jb, 'rotate') as rotate:
            with self.assertRaises(jb.BackupError):
                self.finish()
            rotate.assert_not_called()
        self.assertTrue(previous.is_dir())
        self.assertFalse((self.root / '20261024-100001').exists())

    def test_5_missing_before_listing_detected_from_exact_dump_references(self):
        partial = self.partial()
        path = partial / 'postgres/brightbean.references.json'
        checkpoint = json.loads(path.read_text())
        checkpoint['buckets'] = {'jrc-app': ['deleted-before-list']}
        path.write_text(json.dumps(checkpoint))
        with self.assertRaisesRegex(jb.BackupError, 'chybějící objekt'):
            self.finish()
        self.assertFalse(json.loads((partial / jb.MANIFEST).read_text())['complete'])

    def test_5_dump_copy_parser_decodes_paths_and_ignores_pending_uploads(self):
        partial = self.partial()
        source = partial / 'postgres/brightbean.data.sql'
        source.write_text('COPY public.media_library_mediaasset (id, file, thumbnail) FROM stdin;\n'
                          '1\tmedia/žluťoučký\\tfile.png\t\\N\n\\.\n'
                          'COPY public.media_library_pendingupload (storage_key) FROM stdin;\n'
                          'not-yet-uploaded\n\\.\n')
        jb.cmd_references(SimpleNamespace(workspace=str(self.work), project=self.root.name,
                                         stamp='20261024-100001', database='brightbean'))
        refs = json.loads((partial / 'postgres/brightbean.references.json').read_text())
        self.assertEqual(refs['buckets'], {'jrc-app': ['media/žluťoučký\tfile.png']})
        self.assertFalse(source.exists())

    def test_5_branding_references_and_truncated_copy(self):
        partial = self.partial()
        source = partial / 'postgres/freeframe.data.sql'
        source.write_text('COPY public.instance_branding (id, logo_light_key, favicon_key) FROM stdin;\n'
                          '1\tbranding/light/logo.png\tbranding/icon.png\n\\.\n')
        args = SimpleNamespace(workspace=str(self.work), project=self.root.name,
                               stamp='20261024-100001', database='freeframe')
        jb.cmd_references(args)
        refs = json.loads((partial / 'postgres/freeframe.references.json').read_text())
        self.assertEqual(refs['buckets'], {'jrc-video': ['branding/icon.png', 'branding/light/logo.png']})
        for text in ('', 'COPY public.media_files (s3_key_raw) FROM stdin;\nmissing-terminator\n'):
            source.write_text(text)
            with self.assertRaises(jb.BackupError):
                jb.cmd_references(args)

    def test_5_pre_copy_retains_object_deleted_during_dump(self):
        partial = self.partial()
        env = RestoreEmptinessGuards.ENV
        args = SimpleNamespace(workspace=str(self.work), project=self.root.name, stamp='20261024-100001')
        with mock.patch.object(jb, 'merged_env', return_value=env), mock.patch.object(jb, 's3_client'), \
                mock.patch.object(jb, 'list_objects', return_value=[]):
            jb.cmd_copy_objects(args)
        self.assertEqual(jb.read_index(partial, 'jrc-app')[0]['key'], 'a/b.txt')
        self.finish()
        jb.validate_backup(self.root / '20261024-100001')

    def test_5_no_such_key_aborts_immediately(self):
        self.partial()
        client = mock.Mock()
        class Missing(Exception):
            pass
        client.exceptions.NoSuchKey = Missing
        client.get_object.side_effect = Missing
        with mock.patch.object(jb, 'merged_env', return_value=RestoreEmptinessGuards.ENV), \
                mock.patch.object(jb, 's3_client', return_value=client), \
                mock.patch.object(jb, 'list_objects', return_value=[{'Key': 'gone'}]):
            with self.assertRaisesRegex(jb.BackupError, 'zmizel'):
                jb.cmd_copy_objects(SimpleNamespace(workspace=str(self.work), project=self.root.name,
                                                    stamp='20261024-100001'))

    def test_5_missing_hls_segment_refuses_complete_backup(self):
        directory = make_backup(self.root, '20261024-100000')
        payload = b'#EXTM3U\nsegment.ts\n'
        (directory / 's3/jrc-video/objects/00000001').write_bytes(payload)
        entry = {'key': 'hls/master.m3u8', 'file': '00000001', 'size': len(payload),
                 'sha256': jb.hashlib.sha256(payload).hexdigest()}
        (directory / 's3/jrc-video/index.json').write_text(json.dumps([entry]))
        self.rehash(directory, 's3/jrc-video/index.json')
        self.manifest(directory, lambda m: m['buckets']['jrc-video'].update(objects=1, bytes=len(payload)))
        with self.assertRaisesRegex(jb.BackupError, 'chybějící segment'):
            jb.validate_backup(directory)

    def test_6_corrupt_s3_prevents_finish_and_rotation(self):
        old = make_backup(self.root, '20261024-100000')
        partial = self.partial()
        obj = partial / 's3/jrc-app/objects/00000001'
        obj.write_bytes(b'x' * obj.stat().st_size)
        with mock.patch.object(jb, 'rotate') as rotate:
            with self.assertRaises(jb.BackupError):
                self.finish()
            rotate.assert_not_called()
        self.assertFalse(json.loads((partial / jb.MANIFEST).read_text())['complete'])
        self.assertTrue(old.exists())

    def test_6_dump_corrupted_after_pg_restore_verification_never_completes(self):
        previous = make_backup(self.root, '20261024-100000')
        partial = self.partial()
        path = partial / 'postgres/freeframe.dump'
        path.write_bytes(b'x' * path.stat().st_size)
        with mock.patch.object(jb, 'rotate') as rotate:
            with self.assertRaisesRegex(jb.BackupError, 'pg_restore'):
                self.finish()
            rotate.assert_not_called()
        self.assertFalse(json.loads((partial / jb.MANIFEST).read_text())['complete'])
        self.assertTrue(previous.exists())

    def test_6_corrupt_newest_does_not_count_towards_keep(self):
        old = make_backup(self.root, '20261024-100000')
        newest = make_backup(self.root, '20261024-100001')
        (newest / 's3/jrc-app/objects/00000001').write_bytes(b'x' * len(b'obsah-objektu'))
        self.assertEqual(jb.rotate(self.root, 1), [])
        self.assertTrue(old.exists())
        self.assertTrue(newest.exists())

    def test_6_restore_corruption_refused_before_env_db_or_upload_write(self):
        directory = make_backup(self.root, '20261024-100000', tamper_manifest={'env_included': True})
        obj = directory / 's3/jrc-app/objects/00000001'
        obj.write_bytes(b'x' * obj.stat().st_size)
        for command in ('precheck', 'restore-check', 'restore-objects'):
            with mock.patch.object(jb, 'install_env') as install, mock.patch.object(jb, 'ensure_database') as ensure, \
                    mock.patch.object(jb, 's3_client') as s3:
                self.assertEqual(jb.main([command, '--project', 'jrc-cil', '--backup', str(directory),
                                          '--workspace', str(self.work)]), 1)
                install.assert_not_called()
                ensure.assert_not_called()
                s3.assert_not_called()
        self.assertFalse((self.work / '.env').exists())

    def test_7_required_schema_and_consistency(self):
        changes = [lambda m: m['postgres']['databases'].pop('freeframe'),
                   lambda m: m['files'].pop('postgres/freeframe.dump'),
                   lambda m: m.update(buckets={}),
                   lambda m: m['buckets'].pop('jrc-local'),
                   lambda m: m['buckets'].update({'../outside': {'objects': 0, 'bytes': 0}}),
                   lambda m: m.update(env_included=True),
                   lambda m: m['bb_media'].update(included=True),
                   lambda m: m['bb_media'].update(bytes=100),
                   lambda m: m['buckets']['jrc-app'].update(bytes=123),
                   lambda m: m['postgres']['databases'].update(brightbean=999),
                   lambda m: m.pop('code'), lambda m: m.update(vanished_during_backup={'x': ['y']})]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                directory = make_backup(self.root, f'20261024-100000-{i}')
                self.manifest(directory, change)
                with self.assertRaises(jb.BackupError):
                    jb.validate_backup(directory)

    def test_7_invalid_index_paths_duplicates_and_counts(self):
        for i, field in enumerate(('file', 'key', 'size', 'sha256')):
            directory = make_backup(self.root, f'20261024-100000-{i}')
            index = directory / 's3/jrc-app/index.json'
            entries = json.loads(index.read_text())
            entries[0][field] = {'file': '../../escape', 'key': None, 'size': True, 'sha256': 'bad'}[field]
            index.write_text(json.dumps(entries))
            self.rehash(directory, 's3/jrc-app/index.json')
            with self.assertRaises(jb.BackupError):
                jb.validate_backup(directory)

    def test_7_false_env_flag_rejects_unlisted_env_file(self):
        directory = make_backup(self.root, '20261024-100000')
        (directory / 'env.backup').write_text('must not be ignored')
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)

    def test_10_ancestor_symlink_rotation_and_start_leave_target_untouched(self):
        outside = self.work / 'outside'
        outside.mkdir()
        (outside / 'sentinel').write_text('unchanged')
        shutil.rmtree(self.work / 'backups')
        (self.work / 'backups').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(jb.BackupError):
            jb.rotate(self.root, 1)
        with mock.patch.object(jb, 'merged_env', return_value=RestoreEmptinessGuards.ENV):
            with self.assertRaises(jb.BackupError):
                jb.cmd_start(SimpleNamespace(workspace=str(self.work), project=self.root.name,
                                             stamp='20261024-100000', keep='1', buckets='jrc-app jrc-video jrc-local'))
        self.assertEqual(list(outside.iterdir()), [outside / 'sentinel'])

    def test_10_intermediate_symlinks_rejected_before_restore(self):
        directory = make_backup(self.root, '20261024-100000')
        real = self.work / 'real-postgres'
        (directory / 'postgres').rename(real)
        (directory / 'postgres').symlink_to(real, target_is_directory=True)
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)

    def test_10_tar_paths_and_links_cannot_escape_volume(self):
        for i, kind in enumerate(('path', 'symlink', 'hardlink')):
            archive = self.work / f'{i}.tar'
            with tarfile.open(archive, 'w') as tar:
                member = tarfile.TarInfo('../../escape' if kind == 'path' else 'link')
                if kind != 'path':
                    member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                    member.linkname = '/outside'
                tar.addfile(member, io.BytesIO())
            with self.assertRaises(jb.BackupError):
                jb.validate_media(archive)


class ShellRegressions(Base):
    def setUp(self):
        super().setUp()
        self.bin = self.work / 'bin'
        self.bin.mkdir()
        self.log = self.work / 'calls'
        self.env = dict(os.environ, PATH=f'{self.bin}:' + os.environ['PATH'], CALLS=str(self.log),
                        JRC_PROJECT='jrc-regression-unique', MOCK_PROJECT='jrc-regression-unique',
                        MOCK_PORT='13303', MOCK_IP='127.0.0.1')
        (self.work / '.cache').mkdir()
        scripts = self.work / 'scripts/backup'
        scripts.mkdir(parents=True)
        shutil.copy(SCRIPTS / 'backup.sh', scripts)
        (self.work / 'scripts/workspace.sh').write_text('exit 0\n')
        self.docker('exit 0')

    def docker(self, body):
        script = self.bin / 'docker'
        script.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n' + body + '\n')
        script.chmod(0o700)

    def shell(self, source, env=None):
        return subprocess.run(['sh', '-ec', source], cwd=self.work, env=env or self.env,
                              text=True, capture_output=True)

    def lib(self, source):
        return self.shell(f'. "{SCRIPTS}/test-lib.sh"\nnew_run\n{source}')

    def calls(self):
        return self.log.read_text() if self.log.exists() else ''

    def fixture(self):
        folder = self.work / 'backups/jrc-regression-unique/fixture'
        folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text('{}')
        return folder

    def test_1_existing_volume_is_never_adopted_or_removed(self):
        self.docker('case "$*" in "volume ls"*) echo jrc-regression-unique-s3-data;; esac')
        result = self.lib('require_unused_project jrc-regression-unique\necho MUST-NOT-RUN')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('MUST-NOT-RUN', result.stdout)
        self.assertNotIn(' rm ', self.calls())
        self.assertNotIn('create', self.calls())

    def test_1_unique_names_and_foreign_label_stop_cleanup(self):
        first = self.lib('printf "%s" "$run_id"').stdout
        second = self.lib('printf "%s" "$run_id"').stdout
        self.assertNotEqual(first, second)
        self.docker('case "$*" in "volume ls"*) echo jrc-regression-unique-s3-data;; "volume inspect"*) echo foreign;; esac')
        result = self.lib('cleanup_project jrc-regression-unique')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('down', self.calls())
        self.assertNotIn(' rm ', self.calls())

    def test_1_cleanup_never_removes_replaced_image_tag(self):
        self.docker('case "$*" in "image ls"*) echo "jrc-dev:jrc-regression-unique changed";; esac')
        result = self.lib('printf "jrc-dev:jrc-regression-unique original\\n" > "$run_dir/jrc-regression-unique.images"\ncleanup_project jrc-regression-unique')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('image rm', self.calls())

    def test_2_cleanup_guidance_pins_both_project_parameters(self):
        fixture = self.fixture()
        self.docker('case "$*" in "volume ls"*) echo jrc-regression-unique-postgres-data;; "run "*) echo data;; esac')
        result = self.shell(f'sh scripts/backup/backup.sh restore-precheck "{fixture}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('JRC_PROJECT=jrc-regression-unique docker compose --project-name jrc-regression-unique --file docker-compose.yml --env-file .env down --volumes', result.stderr)

    def test_3_docker_listing_failure_stops_without_cleanup(self):
        self.docker('exit 71')
        result = self.lib('trap \'[ -f "$run_dir/docker-failed" ] || cleanup_project jrc-regression-unique\' EXIT\nif require_unused_project jrc-regression-unique; then echo MUST-NOT-RUN; fi')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('MUST-NOT-RUN', result.stdout)
        self.assertEqual(len(self.calls().splitlines()), 1)
        self.assertNotIn('down', self.calls())

    def test_3_restore_volume_listing_failure_is_not_empty_volume(self):
        fixture = self.fixture()
        self.docker('case "$*" in "volume ls"*) exit 71;; esac')
        result = self.shell(f'sh scripts/backup/backup.sh restore-precheck "{fixture}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('run ', self.calls())

    def test_3_integration_failure_without_marker_never_calls_docker_for_cleanup(self):
        for name in ('test-backup.sh', 'test-lib.sh'):
            shutil.copy(SCRIPTS / name, self.work / 'scripts/backup' / name)
        (self.work / '.env').write_text('JRC_APP_PORT=13303\nJRC_S3_PORT=14023\nJRC_POSTGRES_PORT=15553\nJRC_BB_PORT=18123\n')
        (self.work / 'scripts/workspace.sh').write_text('exit 42\n')
        make = self.bin / 'make'
        make.write_text('#!/bin/sh\nexit 0\n')
        make.chmod(0o700)
        result = self.shell('sh scripts/backup/test-backup.sh')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn('down', self.calls())
        self.assertNotIn('image rm', self.calls())
        self.assertIn('bez dalšího volání Dockeru', result.stderr)

    def test_3_guard_script_propagates_docker_failure_to_cleanup_marker(self):
        # Nenastartuje ani úklid po selhání prvního dotazu Dockeru, i když je volání uvnitř if.
        self.docker('exit 71')
        marker = self.work / 'docker-failed'
        self.env['JRC_BACKUP_DOCKER_FAILURE_FILE'] = str(marker)
        result = self.shell('if sh scripts/backup/backup.sh restore-volume-check; then echo MUST-NOT-RUN; fi')
        self.assertTrue(marker.exists())
        self.assertNotIn('MUST-NOT-RUN', result.stdout)
        self.assertNotIn('run ', self.calls())
        self.assertNotIn('down', self.calls())

    def makeflags_fixture(self):
        # GNU Make je skutečný; jen Docker a data aplikace jsou nahrazené.
        # Existující projekt má jiný název i porty než prostředí shellového testu.
        for name in ('test-backup.sh', 'test-guards.sh', 'test-lib.sh'):
            shutil.copy(SCRIPTS / name, self.work / 'scripts/backup' / name)
        (self.work / 'scripts/backup/seed-bb.py').touch()
        ports = dict(JRC_APP_PORT='13303', JRC_S3_PORT='14023',
                     JRC_POSTGRES_PORT='15553', JRC_BB_PORT='18123')
        self.env.update(ports, MAKEFLAGS='-- JRC_PROJECT=jrc-inherited-existing '
                        'JRC_APP_PORT=13313 JRC_S3_PORT=14033 '
                        'JRC_POSTGRES_PORT=15563 JRC_BB_PORT=18133 JRC_BACKUP_KEEP=99')
        (self.work / '.env').write_text(''.join(f'{key}={value}\n' for key, value in ports.items()))
        sentinel = self.work / 'existing-project'
        sentinel.mkdir()
        (sentinel / 'data').write_text('existující vlastní projekt: beze změny')
        self.docker(r'''case "$*" in
            "volume ls"*) echo jrc-inherited-existing-s3-data
                test ! -f guard-volume || cat guard-volume;;
            "network ls"*) case "$*" in *--filter*) ;; *) echo jrc-inherited-existing_default;; esac;;
            "ps -a "*) echo jrc-inherited-existing-jrc-dev-1;;
            "image ls"*) echo 'jrc-dev:jrc-inherited-existing sentinel-id';;
            "image inspect"*) echo image-id;;
            "volume create"*)
                printf '%s' "$4" > guard-label
                for arg do volume=$arg; done; echo "$volume" > guard-volume;;
            "volume inspect"*) sed 's/^jrc.backup-test=//' guard-label;;
            "volume rm"*) rm guard-volume;;
            "compose "*"ps -q jrc-proxy") echo proxy-id;;
            *Config.Labels*) echo "$JRC_PROJECT";;
            *HostPort*) echo "$JRC_APP_PORT";;
            *HostIp*) echo 127.0.0.1;;
            *"manage.py shell"*) echo '{"workspace_id": "test"}';;
            *"seed-ff.py"*) echo '{"source_sha256": "test"}';;
            *" inventory") echo '{}';;
            *'echo data > /v/neco'*) echo data > guard-data;;
            *'find /v -mindepth'*) cat guard-data;;
            *'rm /v/neco;'*) : > guard-data;;
        esac
        exit 0''')
        return sentinel

    def test_makeflags_integration_pins_every_project_port_and_rotation_limit(self):
        sentinel = self.makeflags_fixture()
        git = self.bin / 'git'
        git.write_text('#!/bin/sh\nexit 0\n')
        git.chmod(0o700)
        (self.work / 'Makefile').write_text(
            'export JRC_PROJECT JRC_APP_PORT JRC_S3_PORT JRC_POSTGRES_PORT JRC_BB_PORT JRC_BACKUP_KEEP\n'
            '.PHONY: up backup backup-verify restore\n'
            'up backup backup-verify restore:\n\t@python3 make-fixture.py $@\n')
        (self.work / 'make-fixture.py').write_text('''import json, os, shutil, sys
from pathlib import Path
target = sys.argv[1]
project = os.environ['JRC_PROJECT']
with open('make-calls', 'a') as log:
    values = {key: value for key, value in os.environ.items()
              if key in ('JRC_PROJECT', 'JRC_APP_PORT', 'JRC_S3_PORT',
                         'JRC_POSTGRES_PORT', 'JRC_BB_PORT', 'JRC_BACKUP_KEEP')}
    log.write(json.dumps(dict(values, target=target)) + '\\n')
if project == 'jrc-inherited-existing':
    Path('existing-project/data').write_text('CHANGED')
    sys.exit(91)
if target == 'backup':
    root = Path('backups') / project
    for previous in root.glob('20261024-*'):
        shutil.rmtree(previous)
    counter = Path('backup-counter')
    number = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(number))
    backup = root / f'20261024-10000{number}'
    backup.mkdir(mode=0o700)
    (backup / 'env.backup').touch(mode=0o600)
if target == 'restore':
    marker = Path(project + '.restored')
    if marker.exists():
        print('už obsahuje data', file=sys.stderr)
        sys.exit(1)
    marker.touch()
''')
        result = self.shell('sh scripts/backup/test-backup.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in (self.work / 'make-calls').read_text().splitlines()]
        self.assertEqual([call['target'] for call in calls],
                         ['up', 'backup', 'backup', 'backup-verify', 'restore', 'restore'])
        src = calls[0]['JRC_PROJECT']
        self.assertTrue(src.startswith(self.env['JRC_PROJECT'] + '-f2b09-'))
        for index, call in enumerate(calls):
            self.assertEqual(call['JRC_PROJECT'], src if index < 4 else src + 'r')
            self.assertEqual([call[key] for key in ('JRC_APP_PORT', 'JRC_S3_PORT',
                                                   'JRC_POSTGRES_PORT', 'JRC_BB_PORT')],
                             ['13303', '14023', '15553', '18123'] if index < 4
                             else ['13313', '14033', '15563', '18133'])
        self.assertEqual([calls[i]['JRC_BACKUP_KEEP'] for i in (1, 2)], ['1000', '1'])
        self.assertEqual((sentinel / 'data').read_text(), 'existující vlastní projekt: beze změny')
        self.assertNotIn('--project-name jrc-inherited-existing ', self.calls())

    def test_makeflags_guard_refusals_happen_before_any_main_project_docker_call(self):
        sentinel = self.makeflags_fixture()
        # Stejná pojistka při čtení jako v projektovém Makefile; při jejím obejití
        # se označí změna existujícího projektu a test musí selhat.
        (self.work / 'Makefile').write_text(
            'ifeq ($(JRC_PROJECT),jrc)\n'
            '$(error zakázáno pro hlavní projekt jrc)\n'
            'endif\n'
            'restore test-backup:\n\t@echo CHANGED > existing-project/data; exit 91\n')
        result = self.shell('sh scripts/backup/test-guards.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('OK pojistka:'), 34)
        self.assertNotIn('compose ', self.calls())
        self.assertNotIn('build ', self.calls())
        # F2-B09a: ani náhodou se nesmí objevit skutečný hlavní projekt a jeho svazky.
        for line in self.calls().splitlines():
            self.assertNotRegex(line, r'--project-name jrc( |$)')
            self.assertNotRegex(line, r'(src=|\b)jrc-(postgres|s3|redis)-data|jrc-bb-media')
        self.assertEqual((sentinel / 'data').read_text(), 'existující vlastní projekt: beze změny')

    def test_f2b09a_test_scripts_never_name_real_main_project_resources(self):
        # Testovací skripty nesmí obsahovat příkazy na skutečný jrc ani jeho svazky (jména skládáme, aby je nenašel ani tento test).
        main = 'jrc'
        forbidden = [re.compile(r'--project-name ' + main + r'(\s|"|$)'),
                     re.compile(r'(?<![\w-])' + main + r'-(postgres|s3|redis)-data'),
                     re.compile(r'(?<![\w-])' + main + r'-bb-media')]
        for name in ('test-guards.sh', 'test-lib.sh', 'test-backup.sh'):
            for number, line in enumerate((SCRIPTS / name).read_text().splitlines(), 1):
                if line.lstrip().startswith('#'):
                    continue
                for pattern in forbidden:
                    self.assertIsNone(pattern.search(line), f'{name}:{number}: {line}')

    def test_4_proxy_project_port_and_loopback_required(self):
        self.docker('case "$*" in "compose "*) echo proxy-id;; *Config.Labels*) echo "$MOCK_PROJECT";; *HostPort*) echo "$MOCK_PORT";; *HostIp*) echo "$MOCK_IP";; esac')
        result = self.lib('require_proxy_origin jrc-regression-unique 13303')
        self.assertEqual(result.returncode, 0, result.stderr)
        for key, value in [('MOCK_PROJECT', 'jrc'), ('MOCK_PORT', '13000'), ('MOCK_IP', '0.0.0.0')]:
            old = self.env[key]
            self.env[key] = value
            result = self.lib('require_proxy_origin jrc-regression-unique 13303')
            self.assertNotEqual(result.returncode, 0)
            self.env[key] = old

    def test_4_environment_port_override_is_forwarded_after_env_file(self):
        (self.work / '.env').write_text('JRC_APP_PORT=13000\nJRC_S3_PORT=13900\n')
        self.env.update(JRC_APP_PORT='13303', JRC_S3_PORT='14023')
        self.docker('case "$*" in "compose "*) echo proxy-id;; *Config.Labels*) echo "$MOCK_PROJECT";; *HostPort*) echo "$MOCK_PORT";; *HostIp*) echo "$MOCK_IP";; esac')
        result = self.lib('seed_freeframe jrc-regression-unique')
        self.assertEqual(result.returncode, 0, result.stderr)
        run = next(line for line in self.calls().splitlines() if line.startswith('run '))
        self.assertIn('--env JRC_APP_PORT=13303 --env JRC_S3_PORT=14023', run)
        self.assertLess(run.index('--env-file .env'), run.index('--env JRC_APP_PORT='))

    def test_4_seed_refuses_main_port_before_login(self):
        result = subprocess.run(['python3', str(SCRIPTS / 'seed-ff.py')], env=dict(self.env,
                                JRC_TEST_PROJECT='jrc-regression-unique', JRC_TEST_ORIGIN_VERIFIED='1',
                                JRC_APP_PORT='13000', JRC_S3_PORT='14023'), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nebyl ověřen', result.stderr)

    def test_8_foreign_fixture_never_overwrites_existing_directory(self):
        parent = self.work / 'backups/jrc-regression-unique'
        existing = parent / 'nechat-byt'
        existing.mkdir(parents=True)
        (existing / 'poznamka.txt').write_text('cizí data')
        first = self.lib('new_foreign_fixture jrc-regression-unique').stdout.strip()
        second = self.lib('new_foreign_fixture jrc-regression-unique').stdout.strip()
        self.assertNotEqual(first, second)
        self.assertNotEqual(Path(first).name, existing.name)
        self.assertEqual((existing / 'poznamka.txt').read_text(), 'cizí data')

    def test_9_garage_error_empty_or_invalid_output_stops_before_start(self):
        for body in ('exit 71', 'exit 0', 'echo invalid'):
            self.docker('case "$*" in *"garage bucket list"*) ' + body + ';; esac')
            result = self.shell('sh scripts/backup/backup.sh backup')
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(' start ', self.calls())
            self.assertNotIn('pg_dump', self.calls())

    def test_9_valid_garage_output_reaches_start(self):
        self.docker('case "$*" in *"garage bucket list"*) printf "ID                Created     Global aliases  Local aliases\\n235929363d6fc390  2026-10-24  jrc-local\\nce6f318b9e17f8a2  2026-10-24  jrc-video\\n9721ae0e4942005c  2026-10-24  jrc-app\\n";; *" start "*) exit 42;; esac')
        result = self.shell('sh scripts/backup/backup.sh backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(' start ', self.calls())
        self.assertNotIn('pg_dump', self.calls())

    def test_dump_reference_export_supplies_explicit_stdout_for_postgres_18(self):
        self.docker(r'''case "$*" in
            *"garage bucket list"*) printf 'ID                Created     Global aliases  Local aliases\n235929363d6fc390  2026-10-24  jrc-local\nce6f318b9e17f8a2  2026-10-24  jrc-video\n9721ae0e4942005c  2026-10-24  jrc-app\n';;
            *" start "*)
                while [ "$#" -gt 0 ]; do
                    case "$1" in --stamp) shift; stamp=$1;; --project) shift; project=$1;; esac
                    shift
                done
                mkdir -p "backups/$project/$stamp.partial/postgres";;
            *"pg_restore --list"*) echo 'TABLE DATA';;
            *"pg_restore --data-only"*) case "$*" in *--file=-*) echo SQL;; *) exit 1;; esac;;
            *" references "*) exit 42;;
        esac''')
        result = self.shell('sh scripts/backup/backup.sh backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pg_restore --data-only --file=-', self.calls())
        self.assertIn(' references ', self.calls())

    def test_10_test_output_ancestor_symlink_refused(self):
        outside = self.work / 'foreign-output'
        outside.mkdir()
        (self.work / 'ops').mkdir()
        (self.work / 'ops/test-output').symlink_to(outside)
        result = self.lib('require_local_path ops/test-output/new-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_10_host_rejects_ancestor_symlink_before_docker(self):
        outside = self.work / 'outside'
        outside.mkdir()
        shutil.rmtree(self.work / 'backups')
        (self.work / 'backups').symlink_to(outside)
        for command in ('restore-guard backups/anything', 'backup'):
            result = self.shell('sh scripts/backup/backup.sh ' + command)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), '')
        self.assertEqual(list(outside.iterdir()), [])
