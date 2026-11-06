"""Budget boundaries and real process termination (no mocked timeout futures)."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
import zipfile

from fastapi.testclient import TestClient
from openpyxl import Workbook
import psutil

from src.api import create_app
from src.importing import ImportConfig, read_input
from src.owned_datasets import OwnedDatasetService
from src.resources import (BoundedWorker, ComputationTimeout, QueueFull,
                           ResourceLimit, ResourceProfile, check_response, execute)


def infinite_computation(marker):
    Path(marker).write_text(str(os.getpid()))
    while True:
        time.sleep(.02)
        with Path(marker).open('a') as stream:
            stream.write('.')


def allocate_forever():
    blocks = []
    while True:
        blocks.append(bytearray(4 * 1024**2))
        time.sleep(.01)


class ReaderLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_csv_exact_boundary_and_one_record_over(self):
        source = self.root / 'sales.csv'
        source.write_text('sales,profit\n10,2\n20,3\n', encoding='utf-8')
        profile = ResourceProfile(data_rows=2, data_columns=2)
        frame = read_input(source, ImportConfig(), profile)
        self.assertEqual(frame.to_dict('list'), {'sales': ['10', '20'], 'profit': ['2', '3']})
        with self.assertRaises(ResourceLimit):
            read_input(source, ImportConfig(), profile.model_copy(update={'data_rows': 1}))
        with self.assertRaises(ResourceLimit):
            read_input(source, ImportConfig(), profile.model_copy(update={'data_columns': 1}))

    def test_csv_multiline_is_one_record_and_ragged_is_rejected(self):
        source = self.root / 'sales.csv'
        source.write_text('sales,name\n10,"a\nb"\n', encoding='utf-8')
        self.assertEqual(len(read_input(source, ImportConfig(), ResourceProfile(data_rows=1))), 1)
        source.write_text('sales,name\n10,a,b\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            read_input(source, ImportConfig(), ResourceProfile())

    def test_file_bytes_checked_before_read(self):
        source = self.root / 'sales.csv'
        source.write_bytes(b'sales\n10\n')
        with self.assertRaises(ResourceLimit):
            read_input(source, ImportConfig(), ResourceProfile(upload_bytes=3))

    def test_xlsx_bounds_and_legacy_values_match(self):
        source = self.root / 'sales.xlsx'
        book = Workbook()
        sheet = book.active
        sheet.append(['sales', 'profit'])
        sheet.append([10, 2])
        sheet.append([20, 3])
        book.save(source)
        bounded = read_input(source, ImportConfig(), ResourceProfile(data_rows=2, data_columns=2))
        self.assertTrue(bounded.equals(read_input(source, ImportConfig())))
        for overrides in ({'data_rows': 1}, {'data_columns': 1}, {'xlsx_expanded_bytes': 100}, {'xlsx_entries': 1}):
            with self.subTest(overrides=overrides), self.assertRaises(ResourceLimit):
                read_input(source, ImportConfig(), ResourceProfile(**overrides))

    def test_zip_bomb_rejected_before_workbook_parser(self):
        source = self.root / 'bomb.xlsx'
        with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('large.xml', b'x' * 1_000_000)
        self.assertLess(source.stat().st_size, 20_000)
        with self.assertRaisesRegex(ResourceLimit, 'Expanded XLSX'):
            read_input(source, ImportConfig(), ResourceProfile(xlsx_expanded_bytes=50_000))

    def test_result_total_across_currencies_and_bytes(self):
        payload = {'results': [{'records': [{}, {}]}, {'records': [{}]}]}
        check_response(payload, ResourceProfile(result_rows=3))
        with self.assertRaises(ResourceLimit):
            check_response(payload, ResourceProfile(result_rows=2))
        with self.assertRaises(ResourceLimit):
            check_response({'text': 'ü' * 100}, ResourceProfile(response_bytes=100))


class WorkerTests(unittest.TestCase):
    def test_timeout_kills_actual_process_stops_side_effects_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'alive'
            worker = BoundedWorker(ResourceProfile())
            with self.assertRaises(ComputationTimeout), worker.admission(2.0, lambda: 0):
                execute(infinite_computation, str(marker))
            self.assertTrue(marker.exists(), 'The job must start, not only time out during spawn.')
            self.assertFalse(psutil.pid_exists(worker.last_pid))
            content = marker.read_text()
            time.sleep(.1)
            self.assertEqual(marker.read_text(), content)
            with worker.admission(5.0, lambda: 0):
                self.assertEqual(execute(abs, -9), 9)
            worker.close()

    def test_worker_memory_overage_is_killed_and_joined(self):
        worker = BoundedWorker(ResourceProfile(session_memory_bytes=100 * 1024**2))
        with self.assertRaisesRegex(ResourceLimit, 'memory'), worker.admission(10.0, lambda: 0):
            execute(allocate_forever)
        self.assertFalse(psutil.pid_exists(worker.last_pid))

    def test_total_memory_budget_terminates_worker(self):
        worker = BoundedWorker(ResourceProfile(total_memory_bytes=1))
        with self.assertRaisesRegex(ResourceLimit, 'memory'), worker.admission(5.0, lambda: 0):
            execute(abs, -1)
        self.assertFalse(psutil.pid_exists(worker.last_pid))

    def test_queue_wait_expiry_releases_ticket(self):
        worker = BoundedWorker(ResourceProfile(queue_seconds=.02))
        with worker.admission(5.0, lambda: 0), ThreadPoolExecutor(max_workers=1) as pool:
            def blocked():
                with worker.admission(5.0, lambda: 0):
                    self.fail('Queued job unexpectedly started')
            with self.assertRaises(QueueFull):
                pool.submit(blocked).result(1)
            self.assertFalse(worker._waiting)

    def test_one_active_four_waiting_fifth_rejected_and_queue_recovers(self):
        worker = BoundedWorker(ResourceProfile())
        release, started = Event(), Event()
        active = maximum = 0
        def job(first=False):
            nonlocal active, maximum
            with worker.admission(10.0, lambda: 0):
                active += 1
                maximum = max(active, maximum)
                if first:
                    started.set()
                    release.wait(5)
                active -= 1
        with ThreadPoolExecutor(max_workers=5) as pool:
            first = pool.submit(job, True)
            self.assertTrue(started.wait(2))
            pending = [pool.submit(job) for _ in range(4)]
            deadline = time.monotonic() + 2
            while len(worker._waiting) < 4 and time.monotonic() < deadline:
                time.sleep(.005)
            try:
                self.assertEqual(len(worker._waiting), 4)
                with self.assertRaises(QueueFull):
                    job()
            finally:
                release.set()
            first.result(5)
            for future in pending:
                future.result(5)
        self.assertEqual(maximum, 1)
        self.assertFalse(worker._waiting)


class ResourceAPITests(unittest.TestCase):
    def client(self, **overrides):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        app = create_app(Path(temporary.name) / 'store', limits=ResourceProfile(**overrides))
        client = TestClient(app, base_url='http://localhost')
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        token = client.post('/v1/sessions', json={}).json()['access_token']
        return client, app.state.datasets, {'Authorization': 'Bearer ' + token}

    def upload(self, client, headers, content=b'sales,product\n10,a\n20,b\n'):
        return client.post('/v1/datasets', headers={**headers, 'X-Filename': 'sales.csv'}, content=content)

    def test_stream_upload_limit_and_no_partial_file_or_reservation(self):
        client, service, headers = self.client(upload_bytes=10)
        self.assertEqual(self.upload(client, headers).status_code, 413)
        self.assertFalse(service._reservations)
        self.assertFalse(service._repository._datasets)

    def test_storage_reserved_before_body_and_released_on_failure(self):
        client, service, headers = self.client(upload_bytes=100, session_storage_bytes=199)
        self.assertEqual(self.upload(client, headers).status_code, 413)
        self.assertFalse(service._reservations)

    def test_oversized_data_rejected_by_preview_and_prepare_mapping(self):
        client, service, headers = self.client(data_rows=1)
        dataset = self.upload(client, headers).json()['dataset_id']
        self.assertEqual(client.post(f'/v1/datasets/{dataset}/preview', headers=headers, json={}).status_code, 413)
        self.assertFalse(service._repository._datasets[dataset].raw_cache)
        self.assertEqual(client.put(f'/v1/datasets/{dataset}/mapping', headers=headers,
                                   json={'mapping': {'sales': 'sales'}, 'mapping_reviewed': True}).status_code, 413)

    def test_no_silent_result_truncation_and_good_query_after_failure(self):
        client, service, headers = self.client(result_rows=1)
        dataset = self.upload(client, headers).json()['dataset_id']
        response = client.put(f'/v1/datasets/{dataset}/mapping', headers=headers,
                              json={'mapping': {'sales': 'sales', 'product': 'product'}, 'mapping_reviewed': True})
        self.assertEqual(response.status_code, 200, response.text)
        prepared = client.post(f'/v1/datasets/{dataset}/prepare', headers=headers, json={}).json()['preparation_id']
        path = f'/v1/datasets/{dataset}/questions'
        response = client.post(path, headers=headers, json={'preparation_id': prepared,
                              'request': {'analysis': 'segment_performance', 'by': ['product']}})
        self.assertEqual(response.status_code, 413, response.text)
        response = client.post(path, headers=headers, json={'preparation_id': prepared, 'request': {'analysis': 'summary'}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['result']['answer']['results'][0]['metrics']['sales'], 30)

    def test_preparation_timeout_http_and_next_job_works(self):
        client, service, headers = self.client(prepare_seconds=.01)
        dataset = self.upload(client, headers).json()['dataset_id']
        response = client.post(f'/v1/datasets/{dataset}/preview', headers=headers, json={})
        self.assertEqual(response.status_code, 504, response.text)
        self.assertFalse(psutil.pid_exists(service.worker.last_pid))
        self.assertEqual(client.get('/health').status_code, 200)

    def test_query_timeout_kills_worker_without_losing_preparation(self):
        client, service, headers = self.client(query_seconds=.01)
        dataset = self.upload(client, headers).json()['dataset_id']
        response = client.put(f'/v1/datasets/{dataset}/mapping', headers=headers,
                              json={'mapping': {'sales': 'sales'}, 'mapping_reviewed': True})
        self.assertEqual(response.status_code, 200, response.text)
        preparation = client.post(f'/v1/datasets/{dataset}/prepare', headers=headers, json={}).json()['preparation_id']
        response = client.post(f'/v1/datasets/{dataset}/questions', headers=headers,
                               json={'preparation_id': preparation, 'request': {'analysis': 'summary'}})
        self.assertEqual(response.status_code, 504, response.text)
        self.assertFalse(psutil.pid_exists(service.worker.last_pid))
        self.assertIn(preparation, service._repository._frames)

    def test_full_computation_queue_http_429(self):
        client, service, headers = self.client(waiting_jobs=0)
        dataset = self.upload(client, headers).json()['dataset_id']
        with service.worker.admission(5.0, lambda: 0):
            response = client.post(f'/v1/datasets/{dataset}/preview', headers=headers, json={})
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.headers['retry-after'], '1')

    def test_json_response_byte_cap(self):
        client, service, headers = self.client(response_bytes=200)
        # Session response fits; larger metadata response is blocked centrally.
        response = self.upload(client, headers)
        self.assertEqual(response.status_code, 413, response.text)


class StorageQuotaTests(unittest.TestCase):
    def test_total_storage_quota_counts_other_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            service = OwnedDatasetService(Path(directory) / 'store', limits=ResourceProfile(
                upload_bytes=5000, session_storage_bytes=50_000, total_storage_bytes=9000))
            try:
                first = service.sessions.authenticate(service.sessions.issue().access_token)
                second = service.sessions.authenticate(service.sessions.issue().access_token)
                data = b'sales\n' + b'1\n' * 2497
                original = service.upload(first, BytesIO(data), filename='a.csv')
                with self.assertRaises(ResourceLimit):
                    service.upload(second, BytesIO(data), filename='b.csv')
                service.delete(first, original.dataset_id)
                self.assertIsNotNone(service.upload(second, BytesIO(data), filename='b.csv'))
            finally:
                service.close()

    def test_session_and_total_quotas_restart_accounting_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'store'
            profile = ResourceProfile(upload_bytes=5000, session_storage_bytes=15_000, total_storage_bytes=25_000)
            service = OwnedDatasetService(root, limits=profile)
            first_token = service.sessions.issue().access_token
            second_token = service.sessions.issue().access_token
            first = service.sessions.authenticate(first_token)
            second = service.sessions.authenticate(second_token)
            content = b'sales\n' + b'1\n' * 2497
            try:
                one = service.upload(first, BytesIO(content), filename='a.csv')
                two = service.upload(first, BytesIO(content), filename='b.csv')
                with self.assertRaisesRegex(ResourceLimit, 'storage'):
                    service.upload(first, BytesIO(content), filename='c.csv')
                three = service.upload(second, BytesIO(content), filename='a.csv')
                four = service.upload(second, BytesIO(content), filename='b.csv')
                # Existing usage survives process restart, rather than resetting counters.
                service.close()
                service = OwnedDatasetService(root, limits=profile)
                second = service.sessions.authenticate(second_token)
                with self.assertRaises(ResourceLimit):
                    service.upload(second, BytesIO(content), filename='c.csv')
                service.delete(second, three.dataset_id)
                again = service.upload(second, BytesIO(content), filename='d.csv')
                self.assertTrue((root / again.dataset_id).exists())
                self.assertFalse((root / three.dataset_id).exists())
            finally:
                service.close()

    def test_direct_service_upload_cannot_bypass_file_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            service = OwnedDatasetService(Path(directory) / 'store', limits=ResourceProfile(upload_bytes=5))
            try:
                credentials = service.sessions.issue()
                context = service.sessions.authenticate(credentials.access_token)
                with self.assertRaises(ResourceLimit):
                    service.upload(context, BytesIO(b'sales\n10\n'), filename='sales.csv')
                self.assertFalse(service._repository._datasets)
                self.assertFalse(list(service._repository._root.glob('*/upload.part')))
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
