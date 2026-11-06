"""First API acceptance: inspect, approve, prepare, reuse, semantic results, delete."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import create_app
from src.contracts import FileQuestionResponse
from src.importing import ImportConfig
from src.question_io import answer_file_question


CSV = (b'date,sales,profit,category,region\n'
       b'2026-09-01,100,10,A,North\n2026-09-30,50,-5,B,South\n'
       b'2026-10-01,120,24,A,North\n2026-10-31,30,-3,B,South\n')
MAPPING = {'mapping': {field: field for field in ('date', 'sales', 'profit', 'category', 'region')},
           'mapping_reviewed': True, 'currency': 'TRY'}


class FirstAPIFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = create_app(self.root / 'store')
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        token = self.client.post('/v1/sessions', json={}).json()['access_token']
        self.headers = {'Authorization': 'Bearer ' + token}

    def upload(self, content=CSV):
        response = self.client.post('/v1/datasets', headers={**self.headers, 'X-Filename': 'sales.csv'}, content=content)
        self.assertEqual(response.status_code, 201, response.text)
        return '/v1/datasets/' + response.json()['dataset_id']

    def approve(self, path, config=MAPPING):
        response = self.client.put(path + '/mapping', headers=self.headers, json=config)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def prepare(self, path):
        response = self.client.post(path + '/prepare', headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['preparation_id']

    def query(self, path, preparation, request):
        response = self.client.post(path + '/questions', headers=self.headers,
                                    json={'preparation_id': preparation, 'request': request})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['preparation_id'], preparation)
        return response.json()['result']

    def test_upload_inspect_prepare_two_questions_delete_entire_flow(self):
        path = self.upload()
        detail = self.client.get(path, headers=self.headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        info = detail.json()
        self.assertEqual(info['status'], 'uploaded')
        self.assertEqual(info['preview']['rows'], 4)
        self.assertEqual(info['preview']['columns'], ['date', 'sales', 'profit', 'category', 'region'])
        self.assertEqual(info['preview']['preview'][0]['sales'], '100')
        self.assertEqual(info['available_fields'], [])
        self.assertIsNone(info['preparation_id'])
        self.assertIsNone(info['quality'])
        self.assertEqual(self.client.post(path + '/prepare', headers=self.headers, json={}).status_code, 409)
        self.approve(path)
        self.assertEqual(self.client.get(path, headers=self.headers).json()['status'], 'mapped')
        prepared = self.prepare(path)
        info = self.client.get(path, headers=self.headers).json()
        self.assertEqual(info['status'], 'ready')
        self.assertEqual(info['preparation_id'], prepared)
        self.assertEqual(set(info['available_fields']), set(MAPPING['mapping']))
        self.assertTrue(info['quality']['capabilities']['profitability']['available'])
        # Both questions must reuse exactly the same prepared snapshot.
        repository = self.app.state.datasets._repository
        frame = repository._frames[prepared]
        with patch('src.datasets.read_input', side_effect=AssertionError('Warm query reread source')), \
                patch('src.datasets.prepare', side_effect=AssertionError('Warm query prepared again')):
            summary = self.query(path, prepared, {'analysis': 'summary'})
            ranking = self.query(path, prepared, {'analysis': 'segment_performance', 'by': ['category'], 'limit': 2})
        self.assertIs(repository._frames[prepared], frame)
        metrics = summary['answer']['results'][0]['metrics']
        self.assertEqual(summary['status'], 'ok')
        self.assertEqual(metrics['sales'], 300)
        self.assertEqual(metrics['profit'], 26)
        self.assertAlmostEqual(metrics['margin_percent'], 26 / 300 * 100)
        records = ranking['answer']['results'][0]['records']
        self.assertEqual([(r['category'], r['sales'], r['profit']) for r in records], [('A', 220, 34), ('B', 80, -8)])
        self.assertEqual(len(repository._datasets[path.rsplit('/', 1)[1]].manifest.preparations), 1)
        self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 204)
        self.assertFalse((self.root / 'store' / path.rsplit('/', 1)[1]).exists())
        self.assertNotIn(prepared, repository._frames)
        self.assertEqual(self.client.get(path, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post(path + '/questions', headers=self.headers,
                         json={'preparation_id': prepared, 'request': {'analysis': 'summary'}}).status_code, 404)

    def test_empty_and_insufficient_data_are_http_200_and_match_existing_analysis(self):
        path = self.upload()
        self.approve(path)
        preparation = self.prepare(path)
        source = self.root / 'sales.csv'
        source.write_bytes(CSV)
        cases = [
            ({'analysis': 'summary', 'filters': {'segments': {'region': 'Unknown'}}}, 'empty'),
            ({'analysis': 'summary', 'filters': {'start': '2027-01-01', 'end': '2027-01-31'}}, 'empty'),
            ({'analysis': 'period_comparison', 'comparison': {
                'baseline_start': '2026-08-01', 'baseline_end': '2026-08-31',
                'current_start': '2026-09-01', 'current_end': '2026-09-30'}}, 'insufficient_data'),
            ({'analysis': 'segment_performance', 'by': ['category'], 'min_records': 99}, 'empty_ranking'),
        ]
        for request, status in cases:
            with self.subTest(status=status, request=request):
                result = self.query(path, preparation, request)
                expected = FileQuestionResponse.model_validate(answer_file_question(source, ImportConfig(**MAPPING), request))
                self.assertEqual(result, expected.model_dump(mode='json'))
                self.assertEqual(result['status'], status)
                entry = result['answer']['results'][0]
                self.assertTrue(entry['warnings'])
                if status in ('empty', 'insufficient_data'):
                    self.assertEqual(entry['metrics'], {})
                    self.assertEqual(entry['records'], [])
        # Empty selection must not corrupt a later valid query.
        self.assertEqual(self.query(path, preparation, {'analysis': 'summary'})['answer']['results'][0]['metrics']['sales'], 300)

    def test_get_uses_approved_reader_and_mapping_change_hides_old_capabilities(self):
        path = self.upload(CSV.replace(b',', b';'))
        self.approve(path, {**MAPPING, 'delimiter': ';'})
        preparation = self.prepare(path)
        detail = self.client.get(path, headers=self.headers).json()
        self.assertEqual(detail['preview']['reader_options']['delimiter'], ';')
        self.assertEqual(detail['preparation_id'], preparation)
        self.approve(path, {'mapping': {'sales': 'sales'}, 'mapping_reviewed': True, 'delimiter': ';', 'currency': 'TRY'})
        detail = self.client.get(path, headers=self.headers).json()
        self.assertEqual(detail['status'], 'mapped')
        self.assertIsNone(detail['preparation_id'])
        self.assertIsNone(detail['quality'])
        self.assertEqual(detail['available_fields'], [])

    def test_needs_attention_is_preserved_and_does_not_claim_available_fields(self):
        path = self.upload(CSV.replace(b',100,', b',invalid,'))
        self.approve(path)
        preparation = self.prepare(path)
        detail = self.client.get(path, headers=self.headers).json()
        self.assertEqual(detail['status'], 'needs_attention')
        self.assertEqual(detail['available_fields'], [])
        self.assertTrue(any(issue['severity'] == 'error' for issue in detail['quality']['issues']))
        result = self.query(path, preparation, {'analysis': 'summary'})
        self.assertEqual(result['status'], 'needs_attention')
        self.assertIsNone(result['answer'])


if __name__ == '__main__':
    unittest.main()
