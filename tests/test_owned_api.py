"""Authenticated ownership and lifecycle acceptance through the local HTTP API."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import create_app
from src.datasets import DatasetService, SourceIntegrityError
from src.sessions import AuthenticationRequired, SessionContext, StoreInUse
from src import datasets


CSV = b'date,sales,profit\n2026-09-01,100,10\n2026-10-01,120,15\n'
MAPPING = {'mapping': {'date': 'date', 'sales': 'sales', 'profit': 'profit'},
           'mapping_reviewed': True, 'currency': 'TRY'}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class OwnershipAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'store'
        self.clock = Clock()
        self.start()

    def start(self, **kwargs):
        self.app = create_app(self.root, clock=self.clock, **kwargs)
        client = TestClient(self.app, base_url='http://localhost')
        client.__enter__()
        self.addCleanup(lambda: client.__exit__(None, None, None) if client is self.client else None)
        self.client = client

    def restart(self, **kwargs):
        self.client.__exit__(None, None, None)
        self.start(**kwargs)

    def session(self):
        response = self.client.post('/v1/sessions', json={})
        self.assertEqual(response.status_code, 201)
        return {'Authorization': 'Bearer ' + response.json()['access_token']}

    def prepared(self, headers=None):
        headers = headers or self.session()
        response = self.client.post('/v1/datasets', headers={**headers, 'X-Filename': 'sales.csv'}, content=CSV)
        self.assertEqual(response.status_code, 201, response.text)
        dataset = response.json()['dataset_id']
        response = self.client.put(f'/v1/datasets/{dataset}/mapping', headers=headers, json=MAPPING)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(f'/v1/datasets/{dataset}/prepare', headers=headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        return headers, dataset, response.json()['preparation_id']

    def body(self, preparation):
        return {'preparation_id': preparation, 'request': {'analysis': 'summary'}}

    def report(self, headers, dataset, prepared):
        response = self.client.post(f'/v1/datasets/{dataset}/reports', headers=headers, json=self.body(prepared))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['report_id']

    def test_missing_invalid_and_forged_authentication_is_rejected(self):
        headers, dataset, prepared = self.prepared()
        for invalid in ({}, {'Authorization': 'Bearer ' + 'x' * 43}, {'Authorization': 'Basic abc'},
                        {'X-User-Id': 'someone', 'X-Session-Id': 'someone'}):
            with self.subTest(headers=invalid):
                self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=invalid).status_code, 401)
        valid = self.client.post(f'/v1/datasets/{dataset}/questions', headers=headers, json=self.body(prepared))
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json()['result']['answer']['results'][0]['metrics']['sales'], 220)

    def test_session_identity_is_server_generated_and_raw_token_is_not_persisted(self):
        self.assertEqual(self.client.post('/v1/sessions', json={'user_id': 'victim'}).status_code, 422)
        first, second = self.session(), self.session()
        self.assertNotEqual(first, second)
        token = first['Authorization'].split()[1]
        stored = (self.root / '.sessions.sqlite3').read_bytes()
        self.assertNotIn(token.encode(), stored)

    def test_client_cannot_supply_owner_or_source_paths_in_request_models(self):
        headers, dataset, prepared = self.prepared()
        cases = [('/mapping', {**MAPPING, 'user_id': 'victim'}),
                 ('/mapping', {**MAPPING, 'owner_session_id': 'victim'}),
                 ('/prepare', {'path': 'C:/Windows/secret.csv'}),
                 ('/questions', {**self.body(prepared), 'path': 'secret.csv'})]
        for suffix, body in cases:
            method = self.client.put if suffix == '/mapping' else self.client.post
            with self.subTest(suffix=suffix, body=body):
                self.assertEqual(method(f'/v1/datasets/{dataset}{suffix}', headers=headers, json=body).status_code, 422)

    def test_all_foreign_session_operations_are_blocked_before_repository_access(self):
        owner, dataset, prepared = self.prepared()
        report = self.report(owner, dataset, prepared)
        intruder = self.session()
        operations = [
            ('get', '', None), ('post', '/preview', {}), ('put', '/mapping', MAPPING),
            ('post', '/prepare', {}), ('get', '/preparations', None),
            ('post', '/questions', self.body(prepared)), ('get', '/source', None),
            ('post', '/reports', self.body(prepared)), ('get', '/reports/' + report, None),
            ('delete', '', None),
        ]
        with patch('src.datasets.read_input', side_effect=AssertionError('Foreign access read source')), \
                patch('src.datasets.answer_prepared_question', side_effect=AssertionError('Foreign access computed query')):
            for method, suffix, body in operations:
                kwargs = {'headers': intruder}
                if body is not None:
                    kwargs['json'] = body
                response = self.client.request(method, f'/v1/datasets/{dataset}{suffix}', **kwargs)
                with self.subTest(method=method, suffix=suffix):
                    self.assertEqual(response.status_code, 404, response.text)
                    self.assertNotIn('sales.csv', response.text)
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}/source', headers=owner).content, CSV)

    def test_preparation_and_report_must_belong_to_the_requested_dataset(self):
        owner, first, prepared_a = self.prepared()
        _, second, prepared_b = self.prepared(owner)
        report = self.report(owner, second, prepared_b)
        self.assertEqual(self.client.post(f'/v1/datasets/{first}/questions', headers=owner,
                                         json=self.body(prepared_b)).status_code, 404)
        self.assertEqual(self.client.get(f'/v1/datasets/{first}/reports/{report}', headers=owner).status_code, 404)
        self.assertEqual(self.client.post(f'/v1/datasets/{first}/questions', headers=owner,
                                         json=self.body(prepared_a)).status_code, 200)

    def test_spoofed_owner_headers_do_not_change_ownership(self):
        owner, dataset, _ = self.prepared()
        intruder = self.session()
        owner_sid = self.app.state.datasets.sessions.authenticate(owner['Authorization'].split()[1]).session_id
        response = self.client.get(f'/v1/datasets/{dataset}', headers={**intruder, 'X-User-Id': owner_sid})
        self.assertEqual(response.status_code, 404)
        response = self.client.post('/v1/datasets', headers={**intruder, 'X-Filename': '../../outside.csv', 'X-User-Id': owner_sid}, content=CSV)
        created = response.json()['dataset_id']
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['source']['file'], 'outside.csv')
        self.assertEqual(self.client.get(f'/v1/datasets/{created}', headers=owner).status_code, 404)

    def test_explicit_deletion_removes_source_frames_metadata_and_reports(self):
        owner, dataset, prepared = self.prepared()
        report = self.report(owner, dataset, prepared)
        directory = self.root / dataset
        self.assertTrue((directory / 'reports' / (report + '.json')).exists())
        self.assertEqual(self.client.delete(f'/v1/datasets/{dataset}', headers=owner).status_code, 204)
        self.assertFalse(directory.exists())
        repository = self.app.state.datasets._repository
        self.assertNotIn(prepared, repository._frames)
        self.assertNotIn(prepared, repository._preparations)
        self.assertNotIn(dataset, repository._datasets)
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 404)

    def test_expiry_immediately_denies_access_and_cleanup_removes_everything(self):
        owner, dataset, prepared = self.prepared()
        self.report(owner, dataset, prepared)
        self.clock.advance(3600)
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}/source', headers=owner).status_code, 404)
        self.assertFalse((self.root / dataset).exists())
        self.assertNotIn(prepared, self.app.state.datasets._repository._frames)

    def test_idle_periodic_cleanup_runs_without_a_client_request(self):
        self.restart(cleanup_interval=.02)
        owner, dataset, prepared = self.prepared()
        self.report(owner, dataset, prepared)
        self.clock.advance(3600)
        deadline = time.monotonic() + 3
        while (self.root / dataset).exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse((self.root / dataset).exists())

    def test_token_and_owner_binding_survive_restart_until_expiry(self):
        owner, dataset, prepared = self.prepared()
        report = self.report(owner, dataset, prepared)
        other = self.session()
        expires = self.client.get(f'/v1/datasets/{dataset}', headers=owner).json()['expires_at']
        self.clock.advance(600)
        self.restart()
        response = self.client.post(f'/v1/datasets/{dataset}/questions', headers=owner, json=self.body(prepared))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}/reports/{report}', headers=owner).status_code, 200)
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=other).status_code, 404)
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).json()['expires_at'], expires)

    def test_same_store_cannot_be_opened_by_two_api_instances(self):
        with self.assertRaises(StoreInUse):
            create_app(self.root)
        self.restart()
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_expired_files_and_reports_are_purged_at_restart(self):
        owner, dataset, prepared = self.prepared()
        self.report(owner, dataset, prepared)
        self.client.__exit__(None, None, None)
        self.clock.advance(3600)
        self.start()
        self.assertFalse((self.root / dataset).exists())
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 404)

    def test_session_expiry_caps_dataset_lifetime(self):
        self.restart(dataset_ttl=3600, session_ttl=30)
        owner, dataset, prepared = self.prepared()
        metadata = self.client.get(f'/v1/datasets/{dataset}', headers=owner).json()
        self.assertEqual(datetime.fromisoformat(metadata['expires_at'].replace('Z', '+00:00')), self.clock() + timedelta(seconds=30))
        self.clock.advance(30)
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 401)
        self.app.state.datasets.cleanup()
        self.assertFalse((self.root / dataset).exists())

    def test_revoking_session_removes_its_data_and_cannot_be_undone_by_restart(self):
        owner, dataset, prepared = self.prepared()
        self.report(owner, dataset, prepared)
        other, other_dataset, _ = self.prepared()
        self.assertEqual(self.client.delete('/v1/session', headers=owner).status_code, 204)
        self.assertFalse((self.root / dataset).exists())
        self.assertTrue((self.root / other_dataset).exists())
        self.restart()
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 401)
        self.assertEqual(self.client.get(f'/v1/datasets/{other_dataset}', headers=other).status_code, 200)

    def test_running_query_deletion_defers_files_and_discards_result(self):
        owner, dataset, prepared = self.prepared()
        self.report(owner, dataset, prepared)
        started, release = Event(), Event()
        original = datasets.answer_prepared_question
        def blocked(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('Query release timed out')
            return original(*args, **kwargs)
        with patch('src.datasets.answer_prepared_question', side_effect=blocked), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.post, f'/v1/datasets/{dataset}/questions', headers=owner, json=self.body(prepared))
            try:
                self.assertTrue(started.wait(5))
                response = self.client.delete(f'/v1/datasets/{dataset}', headers=owner)
                self.assertEqual(response.status_code, 202)
                self.assertTrue((self.root / dataset).exists())
                self.assertEqual(self.client.get(f'/v1/datasets/{dataset}/source', headers=owner).status_code, 404)
                self.assertNotIn(prepared, self.app.state.datasets._repository._frames)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status_code, 404)
        self.assertFalse((self.root / dataset).exists())

    def test_running_query_expiry_discards_result_and_cleans_after_release(self):
        owner, dataset, prepared = self.prepared()
        started, release = Event(), Event()
        original = datasets.answer_prepared_question
        def blocked(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('Query release timed out')
            return original(*args, **kwargs)
        with patch('src.datasets.answer_prepared_question', side_effect=blocked), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.post, f'/v1/datasets/{dataset}/questions', headers=owner, json=self.body(prepared))
            try:
                self.assertTrue(started.wait(5))
                self.clock.advance(3600)
                self.app.state.datasets.cleanup()
                self.assertTrue((self.root / dataset).exists())
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status_code, 404)
        self.assertFalse((self.root / dataset).exists())

    def test_running_query_session_revocation_returns_no_answer(self):
        owner, dataset, prepared = self.prepared()
        started, release = Event(), Event()
        original = datasets.answer_prepared_question
        def blocked(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('Query release timed out')
            return original(*args, **kwargs)
        with patch('src.datasets.answer_prepared_question', side_effect=blocked), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.post, f'/v1/datasets/{dataset}/questions', headers=owner, json=self.body(prepared))
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(self.client.delete('/v1/session', headers=owner).status_code, 204)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status_code, 401)
        self.assertFalse((self.root / dataset).exists())

    def test_pending_deletion_survives_crash_and_partial_file_removal(self):
        owner, dataset, prepared = self.prepared()
        self.report(owner, dataset, prepared)
        repository = self.app.state.datasets._repository
        record = repository._datasets[dataset]
        record.active_operations = 1  # Simulate a process dying while a lease was active.
        self.assertEqual(self.client.delete(f'/v1/datasets/{dataset}', headers=owner).status_code, 202)
        source = self.root / dataset / 'source.csv'
        source.chmod(stat.S_IREAD | stat.S_IWRITE)
        source.unlink()
        self.restart()
        self.assertFalse((self.root / dataset).exists())
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 404)

    def test_cleanup_failure_keeps_access_revoked_and_retries_after_restart(self):
        owner, dataset, prepared = self.prepared()
        with patch.object(self.app.state.datasets._repository, '_remove_directory', side_effect=OSError('busy file')):
            response = self.client.delete(f'/v1/datasets/{dataset}', headers=owner)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 404)
        self.restart()
        self.assertFalse((self.root / dataset).exists())

    def test_orphaned_unpublished_upload_is_cleaned_at_startup(self):
        orphan = self.root / ('f' * 32)
        orphan.mkdir()
        (orphan / 'upload.part').write_bytes(b'partial sensitive upload')
        self.restart()
        self.assertFalse(orphan.exists())

    def test_legacy_unowned_dataset_is_never_assigned_to_a_new_session(self):
        legacy = self.app.state.datasets._repository.upload(BytesIO(CSV), filename='legacy.csv')
        owner = self.session()
        self.assertEqual(self.client.get(f'/v1/datasets/{legacy.dataset_id}', headers=owner).status_code, 404)
        self.restart()
        self.assertEqual(self.client.get(f'/v1/datasets/{legacy.dataset_id}', headers=owner).status_code, 404)

    def test_unverified_python_context_is_not_a_user_identity(self):
        owner, dataset, prepared = self.prepared()
        authority = self.app.state.datasets.sessions
        valid = authority.authenticate(owner['Authorization'].split()[1])
        for forged in ({'user_id': valid.session_id}, SessionContext(valid.session_id, valid._digest, object())):
            with self.subTest(context=type(forged).__name__), self.assertRaises(AuthenticationRequired):
                self.app.state.datasets.get_dataset(forged, dataset)

    def test_safe_deletion_rejects_paths_outside_the_store(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        marker = outside / 'keep.txt'
        marker.write_text('keep', encoding='utf-8')
        for target in ('../outside', str(outside), []) :
            with self.subTest(target=target), self.assertRaises(SourceIntegrityError):
                self.app.state.datasets._repository._remove_directory(target)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')

    def test_deletion_journal_prevents_resurrection_if_manifest_cannot_be_written(self):
        owner, dataset, prepared = self.prepared()
        self.report(owner, dataset, prepared)
        with patch.object(self.app.state.datasets._repository, '_persist', side_effect=OSError('manifest locked')):
            self.assertEqual(self.client.delete(f'/v1/datasets/{dataset}', headers=owner).status_code, 503)
            self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 404)
        metadata = json.loads((self.root / dataset / 'metadata.json').read_text(encoding='utf-8'))
        self.assertFalse(metadata['deletion_requested'])
        # A crash after partial removal must not require intact old source/metadata.
        source = self.root / dataset / 'source.csv'
        source.chmod(stat.S_IREAD | stat.S_IWRITE)
        source.unlink()
        (self.root / dataset / 'metadata.json').write_text('interrupted metadata', encoding='utf-8')
        self.restart()
        self.assertFalse((self.root / dataset).exists())
        self.assertEqual(self.client.get(f'/v1/datasets/{dataset}', headers=owner).status_code, 404)

    def test_session_expiring_during_upload_does_not_publish_accessible_data(self):
        owner = self.session()
        service = self.app.state.datasets
        context = service.sessions.authenticate(owner['Authorization'].split()[1])
        clock = self.clock
        class ExpiringUpload(BytesIO):
            def read(self, size=-1):
                value = super().read(size)
                if value:
                    clock.advance(86400)
                return value
        with self.assertRaises(AuthenticationRequired):
            service.upload(context, ExpiringUpload(CSV), filename='sales.csv')
        self.assertEqual(len(service._repository._datasets), 0)
        self.assertEqual([p for p in self.root.iterdir() if p.is_dir()], [])

    def test_reports_are_private_json_and_caching_is_disabled(self):
        owner, dataset, prepared = self.prepared()
        report = self.report(owner, dataset, prepared)
        response = self.client.get(f'/v1/datasets/{dataset}/reports/{report}', headers=owner)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['dataset_id'], dataset)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('attachment', response.headers['content-disposition'])
        self.assertNotIn(str(self.root), response.text)

    def test_host_origin_and_byte_limit_are_enforced(self):
        self.assertEqual(self.client.post('/v1/sessions', json={}, headers={'Origin': 'https://attacker.example'}).status_code, 403)
        self.assertEqual(self.client.get('/health', headers={'Host': 'attacker.example'}).status_code, 400)
        self.restart(upload_limit=4)
        owner = self.session()
        response = self.client.post('/v1/datasets', headers={**owner, 'X-Filename': 'sales.csv'}, content=CSV)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(len(self.app.state.datasets._repository._datasets), 0)

    def test_openapi_has_no_user_id_or_server_path_input(self):
        schema = self.client.get('/openapi.json').json()
        for name in ('EmptyBody', 'MappingBody', 'PrepareBody', 'QueryBody', 'PreviewBody'):
            self.assertFalse(schema['components']['schemas'][name]['additionalProperties'])
            fields = schema['components']['schemas'][name].get('properties', {})
            self.assertNotIn('user_id', fields)
            self.assertNotIn('path', fields)


if __name__ == '__main__':
    unittest.main()
