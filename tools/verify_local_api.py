"""Start a temporary loopback API, verify ownership/deletion over TCP, then stop it."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as directory:
        with socket.socket() as candidate:
            candidate.bind(('127.0.0.1', 0))
            port = candidate.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        opener = build_opener(ProxyHandler({}))
        def call(method, path, *, token=None, value=None, content=None, filename=None):
            headers = {}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            if filename:
                headers['X-Filename'] = filename
            if value is not None:
                content = json.dumps(value).encode('utf-8')
                headers['Content-Type'] = 'application/json'
            request = Request(base + path, data=content, headers=headers, method=method)
            try:
                with opener.open(request, timeout=10) as response:
                    body = response.read()
                    return response.status, json.loads(body) if response.headers.get_content_type() == 'application/json' else body
            except HTTPError as exc:
                code = exc.code
                exc.close()
                return code, None
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (Path(directory) / 'server.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, '-m', 'src.api', '--store', str(Path(directory) / 'store'),
                                        '--port', str(port)], cwd=ROOT, stdout=log, stderr=log,
                                       creationflags=flags)
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Local API exited before startup.')
                    try:
                        if call('GET', '/health')[0] == 200:
                            break
                    except (URLError, TimeoutError):
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError('Local API startup timed out.')
                    time.sleep(.1)
                status, issued = call('POST', '/v1/sessions', value={})
                assert status == 201
                owner = issued['access_token']
                _, issued = call('POST', '/v1/sessions', value={})
                other = issued['access_token']
                source = b'date,sales,profit\n2026-09-01,100,10\n2026-10-01,120,15\n'
                status, uploaded = call('POST', '/v1/datasets', token=owner, content=source, filename='sales.csv')
                assert status == 201
                path = '/v1/datasets/' + uploaded['dataset_id']
                status, detail = call('GET', path, token=owner)
                assert status == 200 and detail['status'] == 'uploaded'
                assert detail['preview']['rows'] == 2 and detail['available_fields'] == []
                assert call('PUT', path + '/mapping', token=owner, value={
                    'mapping': {'sales': 'sales', 'profit': 'profit', 'date': 'date'},
                    'mapping_reviewed': True, 'currency': 'TRY'})[0] == 200
                status, prepared = call('POST', path + '/prepare', token=owner, value={})
                assert status == 200
                status, detail = call('GET', path, token=owner)
                assert status == 200 and detail['status'] == 'ready'
                assert detail['preparation_id'] == prepared['preparation_id']
                assert set(detail['available_fields']) == {'sales', 'profit', 'date'}
                body = {'preparation_id': prepared['preparation_id'], 'request': {'analysis': 'summary'}}
                status, answer = call('POST', path + '/questions', token=owner, value=body)
                assert status == 200 and answer['result']['answer']['results'][0]['metrics']['sales'] == 220
                status, monthly = call('POST', path + '/questions', token=owner, value={
                    'preparation_id': prepared['preparation_id'], 'request': {'analysis': 'monthly_trend'}})
                assert status == 200 and monthly['preparation_id'] == prepared['preparation_id']
                assert [(r['month'], r['sales']) for r in monthly['result']['answer']['results'][0]['records']] == [
                    ('2026-09', 100), ('2026-10', 120)]
                status, empty = call('POST', path + '/questions', token=owner, value={
                    'preparation_id': prepared['preparation_id'], 'request': {
                        'analysis': 'summary', 'filters': {'start': '2027-01-01', 'end': '2027-01-31'}}})
                assert status == 200 and empty['result']['status'] == 'empty'
                assert empty['result']['answer']['results'][0]['metrics'] == {}
                status, insufficient = call('POST', path + '/questions', token=owner, value={
                    'preparation_id': prepared['preparation_id'], 'request': {
                        'analysis': 'period_comparison', 'comparison': {
                            'baseline_start': '2026-08-01', 'baseline_end': '2026-08-31',
                            'current_start': '2026-09-01', 'current_end': '2026-09-30'}}})
                assert status == 200 and insufficient['result']['status'] == 'insufficient_data'
                assert insufficient['result']['answer']['results'][0]['metrics'] == {}
                status, report = call('POST', path + '/reports', token=owner, value=body)
                assert status == 201
                status, visual = call('POST', path + '/reports', token=owner, value={**body, 'format': 'html'})
                assert status == 201 and visual['media_type'] == 'text/html'
                status, rendered = call('GET', path + '/reports/' + visual['report_id'], token=owner)
                assert status == 200 and b'data:image/png;base64,' in rendered
                for method, suffix, value in [('POST', '/questions', body), ('GET', '/source', None),
                                              ('GET', '/reports/' + report['report_id'], None),
                                              ('GET', '/reports/' + visual['report_id'], None), ('DELETE', '', None)]:
                    assert call(method, path + suffix, token=other, value=value)[0] == 404
                assert call('GET', path + '/source', token=owner) == (200, source)
                assert call('GET', path + '/reports/' + report['report_id'], token=owner)[0] == 200
                assert call('DELETE', path, token=owner)[0] == 204
                assert call('GET', path, token=owner)[0] == 404
                assert not (Path(directory) / 'store' / uploaded['dataset_id']).exists()
                print('Local API TCP flow: PASS (upload, prepare, two queries, empty/insufficient data, JSON/HTML reports, isolation, delete)')
            finally:
                if os.name == 'nt' and process.poll() is None:
                    # The Windows venv launcher can have a separate interpreter child.
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=flags, check=False)
                elif process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
