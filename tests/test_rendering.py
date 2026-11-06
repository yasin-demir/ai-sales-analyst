"""Real concurrent report requests, style isolation and failure cleanup."""
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Barrier
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from src.api import create_app
from src.general_reports import make_charts
from src.plotting import matplotlib, plt, render_session, subplots, png
from src.report_jobs import RenderFailure, render_report
from src.resources import ResourceProfile, ResourceLimit


def image_from_report(content):
    encoded = re.search(rb'data:image/png;base64,([A-Za-z0-9+/=]+)', content).group(1)
    return Image.open(BytesIO(base64.b64decode(encoded))).convert('RGB')


def failing_renderer(answer, theme, limits):
    with render_session():
        fig, ax = subplots()
        ax.bar(['private'], [42])
        raise RenderFailure('Injected failure after figure creation')


def slow_renderer(answer, theme, limits):
    with render_session():
        fig, ax = subplots()
        ax.bar(['private'], [42])
        Path(os.environ['RENDER_TEST_MARKER']).write_text(str(os.getpid()))
        while True:
            time.sleep(.05)


class RenderSessionTests(unittest.TestCase):
    def test_same_process_renders_are_serial_and_styles_are_restored(self):
        barrier = Barrier(2)
        before = matplotlib.rcParams.copy()
        active = maximum = 0
        def render(color):
            nonlocal active, maximum
            barrier.wait(timeout=5)
            with render_session({'figure.facecolor': color, 'savefig.facecolor': color}):
                active += 1
                maximum = max(maximum, active)
                self.assertFalse(matplotlib.rcParams['text.usetex'])
                fig, ax = subplots(figsize=(2, 2))
                ax.bar([color], [10])
                time.sleep(.03)
                image = Image.open(BytesIO(png(fig))).convert('RGB')
                active -= 1
                return image.getpixel((0, 0))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(render, '#ffffff')
            second = pool.submit(render, '#172033')
            self.assertEqual(first.result(10), (255, 255, 255))
            self.assertEqual(second.result(10), (23, 32, 51))
        self.assertEqual(maximum, 1)
        self.assertEqual(matplotlib.rcParams, before)
        self.assertFalse(plt.get_fignums())

    def test_failed_save_closes_figures_restores_styles_and_next_render_works(self):
        before = matplotlib.rcParams.copy()
        with self.assertRaises(OSError), patch('matplotlib.figure.Figure.savefig', side_effect=OSError('encoder failed')):
            with render_session({'font.size': 23}):
                fig, ax = subplots()
                ax.plot([1, 2])
                png(fig)
        self.assertFalse(plt.get_fignums())
        self.assertEqual(matplotlib.rcParams, before)
        with render_session():
            fig, ax = subplots()
            ax.plot([2, 3])
            self.assertTrue(png(fig).startswith(b'\x89PNG'))

    def test_existing_report_closes_figure_when_artist_creation_fails(self):
        partition = {'unit': 'TRY', 'tables': {'category_sales': [{'category': 'private', 'sales': 10}]}, '_frame': None}
        before = matplotlib.rcParams.copy()
        with self.assertRaises(RuntimeError), patch('matplotlib.axes.Axes.barh', side_effect=RuntimeError('artist failed')):
            make_charts(partition)
        self.assertFalse(plt.get_fignums())
        self.assertEqual(matplotlib.rcParams, before)
        self.assertEqual(len(make_charts(partition)), 1)
        self.assertFalse(plt.get_fignums())

    def test_failed_axes_creation_closes_owned_figure_preserves_external_figure(self):
        external = plt.figure()
        try:
            with self.assertRaises(ValueError), render_session():
                subplots(nrows=0)
            self.assertEqual(plt.get_fignums(), [external.number])
        finally:
            plt.close(external)


class ReportAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'store'
        self.app = create_app(self.root)
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def prepared(self, label, amount):
        token = self.client.post('/v1/sessions', json={}).json()['access_token']
        headers = {'Authorization': 'Bearer ' + token}
        response = self.client.post('/v1/datasets', headers={**headers, 'X-Filename': label + '.csv'},
                                    content=f'sales,category\n{amount},{label}\n'.encode())
        self.assertEqual(response.status_code, 201, response.text)
        path = '/v1/datasets/' + response.json()['dataset_id']
        response = self.client.put(path + '/mapping', headers=headers, json={
            'mapping': {'sales': 'sales', 'category': 'category'}, 'mapping_reviewed': True, 'currency': 'TRY'})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(path + '/prepare', headers=headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        return headers, path, {'preparation_id': response.json()['preparation_id'],
                               'request': {'analysis': 'segment_performance', 'by': ['category']}}

    def report(self, headers, path, body, theme='light'):
        response = self.client.post(path + '/reports', headers=headers, json={**body, 'format': 'html', 'theme': theme})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['media_type'], 'text/html')
        report_id = response.json()['report_id']
        download = self.client.get(path + '/reports/' + report_id, headers=headers)
        self.assertEqual(download.status_code, 200, download.text)
        self.assertTrue(download.headers['content-type'].startswith('text/html'))
        self.assertIn('attachment', download.headers['content-disposition'])
        return report_id, download

    def test_simultaneous_users_have_distinct_data_images_styles_and_private_downloads(self):
        first = self.prepared('TENANT_ALPHA', 111)
        second = self.prepared('TENANT_BETA', 999)
        barrier = Barrier(2)
        def run(data, theme):
            barrier.wait(timeout=5)
            return self.report(*data, theme)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(run, first, 'light')
            b = pool.submit(run, second, 'dark')
            id_a, response_a = a.result(30)
            id_b, response_b = b.result(30)
        self.assertNotEqual(id_a, id_b)
        self.assertIn(b'TENANT_ALPHA', response_a.content)
        self.assertNotIn(b'TENANT_BETA', response_a.content)
        self.assertIn(b'TENANT_BETA', response_b.content)
        self.assertNotIn(b'TENANT_ALPHA', response_b.content)
        self.assertEqual(image_from_report(response_a.content).getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(image_from_report(response_b.content).getpixel((0, 0)), (23, 32, 51))
        self.assertEqual(self.client.get(first[1] + '/reports/' + id_a, headers=second[0]).status_code, 404)
        self.assertIn("default-src 'none'", response_a.headers['content-security-policy'])
        self.assertFalse(plt.get_fignums())
        # Source/prepared snapshots remain queryable and contain no graph output.
        numeric = self.client.post(first[1] + '/questions', headers=first[0], json=first[2])
        self.assertEqual(numeric.status_code, 200)
        self.assertNotIn('image', numeric.text)
        self.assertEqual(numeric.json()['result']['answer']['results'][0]['metrics']['sales'], 111)

    def test_failed_render_publishes_no_report_releases_slot_and_recovers(self):
        data = self.prepared('ONLY_THIS_USER', 42)
        headers, path, body = data
        service = self.app.state.datasets
        with patch('src.owned_datasets.render_report', new=failing_renderer):
            response = self.client.post(path + '/reports', headers=headers, json={**body, 'format': 'html'})
        self.assertEqual(response.status_code, 500, response.text)
        record = service._repository._datasets[path.rsplit('/', 1)[1]]
        self.assertFalse(record.manifest.reports)
        self.assertFalse(list((self.root / record.manifest.dataset_id).glob('reports/*')))
        self.assertFalse(service.worker._busy)
        self.report(*data)

    def test_write_failure_removes_artifact_and_metadata(self):
        headers, path, body = self.prepared('WRITE_FAILURE', 42)
        repository = self.app.state.datasets._repository
        with patch.object(repository, '_persist', side_effect=OSError('metadata write failed')):
            response = self.client.post(path + '/reports', headers=headers, json={**body, 'format': 'html'})
        self.assertEqual(response.status_code, 503, response.text)
        record = repository._datasets[path.rsplit('/', 1)[1]]
        self.assertFalse(record.manifest.reports)
        self.assertFalse(list((self.root / record.manifest.dataset_id).glob('reports/*')))

    def test_html_byte_limit_rejects_before_publishing_and_frees_worker(self):
        headers, path, body = self.prepared('BYTE_LIMIT', 42)
        service = self.app.state.datasets
        # Allow the numeric snapshot, but not its embedded chart.
        profile = service.limits.model_copy(update={'response_bytes': 20_000})
        service.limits = service.worker.profile = profile
        response = self.client.post(path + '/reports', headers=headers, json={**body, 'format': 'html'})
        self.assertEqual(response.status_code, 413, response.text)
        record = service._repository._datasets[path.rsplit('/', 1)[1]]
        self.assertFalse(record.manifest.reports)
        self.assertFalse(service.worker._busy)

    def test_rendered_html_escapes_source_text_and_preserves_empty_status(self):
        data = self.prepared('SAFE_<script>_$(x)', 42)
        _, response = self.report(*data)
        self.assertNotIn(b'<script>', response.content)
        self.assertIn(b'&lt;script&gt;', response.content)
        headers, path, body = data
        empty = {**body, 'request': {'analysis': 'summary', 'filters': {'segments': {'category': 'missing'}}}}
        _, response = self.report(headers, path, empty)
        self.assertNotIn(b'data:image/png', response.content)
        self.assertIn(b'Status: empty', response.content)

    def test_figure_count_failure_closes_previously_rendered_figures(self):
        headers, path, body = self.prepared('FIGURE_COUNT', 42)
        response = self.client.post(path + '/questions', headers=headers, json=body)
        answer = response.json()
        answer['result']['answer']['results'] *= 2
        with self.assertRaises(ResourceLimit):
            render_report(answer, 'dark', ResourceProfile(render_figures=1))
        self.assertFalse(plt.get_fignums())
        self.assertTrue(render_report(response.json(), 'light', ResourceProfile()).startswith(b'<!doctype html>'))

    def test_render_timeout_terminates_process_and_no_artifact_remains(self):
        headers, path, body = self.prepared('TIME_LIMIT', 42)
        service = self.app.state.datasets
        original = service.limits
        service.limits = service.worker.profile = service.limits.model_copy(update={'render_seconds': 4.0})
        marker = self.root / 'render-started'
        with patch.dict(os.environ, {'RENDER_TEST_MARKER': str(marker)}), \
                patch('src.owned_datasets.render_report', new=slow_renderer):
            response = self.client.post(path + '/reports', headers=headers, json={**body, 'format': 'html'})
        self.assertEqual(response.status_code, 504, response.text)
        import psutil
        self.assertTrue(marker.exists(), 'Timeout must occur after the actual renderer starts.')
        self.assertFalse(psutil.pid_exists(service.worker.last_pid))
        record = service._repository._datasets[path.rsplit('/', 1)[1]]
        self.assertFalse(record.manifest.reports)
        self.assertFalse(list((self.root / record.manifest.dataset_id).glob('reports/*')))
        service.limits = service.worker.profile = original
        self.report(headers, path, body)


class NumericIsolationTests(unittest.TestCase):
    def test_numeric_and_json_report_flow_does_not_import_matplotlib(self):
        script = '''
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from src.api import create_app
if __name__ == '__main__':
    with TestClient(create_app(Path(sys.argv[1]) / 'store'), base_url='http://localhost') as client:
        token = client.post('/v1/sessions', json={}).json()['access_token']
        headers = {'Authorization': 'Bearer ' + token}
        result = client.post('/v1/datasets', headers={**headers, 'X-Filename': 'sales.csv'}, content=b'sales\\n10\\n')
        path = '/v1/datasets/' + result.json()['dataset_id']
        assert client.put(path + '/mapping', headers=headers, json={'mapping': {'sales':'sales'}, 'mapping_reviewed':True}).status_code == 200
        prepared = client.post(path + '/prepare', headers=headers, json={}).json()['preparation_id']
        body = {'preparation_id':prepared, 'request':{'analysis':'summary'}}
        assert client.post(path + '/questions', headers=headers, json=body).status_code == 200
        assert client.post(path + '/reports', headers=headers, json=body).status_code == 201
        assert not any(name == 'matplotlib' or name.startswith('matplotlib.') for name in sys.modules)
    print('numeric flow imports no Matplotlib: PASS')
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', script, directory], cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, timeout=30,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
