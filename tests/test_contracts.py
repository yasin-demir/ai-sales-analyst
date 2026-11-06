"""Strict v1 request/response acceptance against real deterministic outputs."""
from copy import deepcopy
import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from pydantic import ValidationError

from src.contracts import (REQUEST_ADAPTER, RESPONSE_ADAPTER, FileQuestionResponse,
                           ContractViolation, parse_question_request, parse_question_response)
from src.importing import ImportConfig
from src.question_io import answer_file_question
from src.questions import run_question
from src.cli import main


WINDOWS = {'baseline_start': '2026-01-01', 'baseline_end': '2026-01-31',
           'current_start': '2026-02-01', 'current_end': '2026-02-28'}
REQUESTS = [
    {'analysis': 'summary'},
    {'analysis': 'segment_performance', 'by': ['region', 'category'], 'sort_by': 'profit', 'limit': 2},
    {'analysis': 'period_comparison', 'comparison': WINDOWS},
    {'analysis': 'monthly_trend'},
    {'analysis': 'loss_concentration', 'limit': 2},
    {'analysis': 'discount_comparison'},
]


def fixture():
    return pd.DataFrame({
        'date': pd.to_datetime(['2026-01-01', '2026-01-31', '2026-02-01', '2026-02-28', '2026-04-01']),
        'sales': [100., 200., 600., 0., -100.], 'profit': [20., -40., 60., -10., -5.],
        'category': ['A', 'B', 'A', 'B', 'B'], 'region': ['East', 'East', 'West', 'West', 'West'],
        'product_id': ['P1', 'P2', 'P1', 'P3', 'P3'], 'product': ['One', 'Two', 'One', 'Three', 'Three'],
        'order_id': ['O1', 'O2', 'O3', 'O4', 'O5'], 'discount': [.1, .4, .1, .5, .5],
    })


class RequestContractTests(unittest.TestCase):
    def test_all_six_request_variants_python_and_json(self):
        for request in REQUESTS:
            with self.subTest(analysis=request['analysis']):
                model = parse_question_request(request)
                self.assertEqual(model.analysis, request['analysis'])
                self.assertEqual(REQUEST_ADAPTER.validate_json(json.dumps(request)), model)
                self.assertEqual(parse_question_request(model), model)

    def test_bad_analysis_and_top_level_types(self):
        for request in ([], None, True, 'summary', {}, {'analysis': []}, {'analysis': {}},
                        {'analysis': None}, {'analysis': 'predict'}):
            with self.subTest(request=request), self.assertRaises(ValidationError):
                parse_question_request(request)

    def test_strict_ranking_scalars(self):
        for field, values in {'limit': [True, '10', 1.0, 0, -1, [], None],
                              'min_records': [False, '2', 1.5, 0, {}],
                              'ascending': [1, 0, 'true', [], {}],
                              'sort_by': [[], {}, True, 'quantity']}.items():
            for value in values:
                request = {'analysis': 'segment_performance', 'by': 'region', field: value}
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    parse_question_request(request)

    def test_grouping_dimensions_are_explicit_unique_and_nonempty(self):
        for by in ([], [[]], [{}], [True], ['region', 'region'], 'currency', 'customer', 1, ('region',)):
            with self.subTest(by=by), self.assertRaises(ValidationError):
                parse_question_request({'analysis': 'segment_performance', 'by': by})

    def test_unknown_and_irrelevant_parameters_are_rejected(self):
        for request in ({'analysis': 'summary', 'limit': 3},
                        {'analysis': 'summary', 'by': 'region'},
                        {'analysis': 'monthly_trend', 'comparison': WINDOWS},
                        {'analysis': 'loss_concentration', 'min_records': 2},
                        {'analysis': 'discount_comparison', 'sort_by': 'sales'},
                        {'analysis': 'summary', 'path': 'secret.csv'}):
            with self.subTest(request=request), self.assertRaises(ValidationError):
                parse_question_request(request)

    def test_dates_and_filter_types(self):
        filters = [{'start': x} for x in ('2026-02-30', '20260101', '01/01/2026', '', [], True)]
        filters += [{'start': '2026-02-01', 'end': '2026-01-01'}, {'extra': 1},
                    {'segments': {'region': []}}, {'segments': {'region': [1]}},
                    {'segments': {'region': ' '}}, {'segments': {'customer': 'A'}}]
        for value in filters:
            with self.subTest(filters=value), self.assertRaises(ValidationError):
                parse_question_request({'analysis': 'summary', 'filters': value})
        good = {'analysis': 'summary', 'filters': {'segments': {'region': ['East'], 'category': 'A'}}}
        self.assertEqual(parse_question_request(good).filters.segments['category'], 'A')

    def test_comparison_requires_nonoverlapping_complete_windows(self):
        for comparison in ({}, [], {**WINDOWS, 'baseline_end': '2026-02-01'},
                           {**WINDOWS, 'current_end': '2026-01-01'},
                           {**WINDOWS, 'current_start': '2026-02-30'}, {**WINDOWS, 'extra': 1}):
            with self.subTest(comparison=comparison), self.assertRaises(ValidationError):
                parse_question_request({'analysis': 'period_comparison', 'comparison': comparison})
        with self.assertRaises(ValidationError):
            parse_question_request({'analysis': 'period_comparison', 'comparison': WINDOWS,
                                    'filters': {'start': '2026-01-01'}})
        self.assertEqual(parse_question_request({'analysis': 'period_comparison', 'comparison': WINDOWS,
                                                 'filters': {'segments': {'region': 'East'}}}).analysis,
                         'period_comparison')

    def test_models_and_raw_input_are_not_mutated(self):
        request = deepcopy(REQUESTS[1])
        original = deepcopy(request)
        model = parse_question_request(request)
        run_question(fixture(), **model.model_dump(exclude_none=True))
        self.assertEqual(request, original)
        self.assertEqual(model.by, ['region', 'category'])

    def test_schema_has_six_discriminated_closed_variants(self):
        for adapter in (REQUEST_ADAPTER, RESPONSE_ADAPTER):
            schema = adapter.json_schema()
            self.assertEqual(schema['discriminator']['propertyName'], 'analysis')
            self.assertEqual(len(schema['oneOf']), 6)
            for variant in schema['oneOf']:
                definition = schema['$defs'][variant['$ref'].split('/')[-1]]
                self.assertFalse(definition['additionalProperties'])

    def test_published_version_one_schemas_match_models(self):
        directory = Path(__file__).resolve().parents[1] / 'docs' / 'schemas'
        for name, schema in (
            ('question-request-v1.schema.json', REQUEST_ADAPTER.json_schema()),
            ('question-response-v1.schema.json', RESPONSE_ADAPTER.json_schema()),
            ('file-question-response-v1.schema.json', FileQuestionResponse.model_json_schema()),
        ):
            with self.subTest(schema=name):
                self.assertEqual(json.loads((directory / name).read_text(encoding='utf-8')), schema)


class ResponseContractTests(unittest.TestCase):
    def check_round_trip(self, answer):
        model = parse_question_response(answer)
        self.assertEqual(model.model_dump(mode='json', exclude_unset=True), answer)
        restored = RESPONSE_ADAPTER.validate_json(json.dumps(answer, allow_nan=False))
        self.assertEqual(restored.model_dump(mode='json', exclude_unset=True), answer)
        return model

    def test_all_six_success_outputs_keep_existing_json_exactly(self):
        for request in REQUESTS:
            with self.subTest(analysis=request['analysis']):
                self.check_round_trip(run_question(fixture(), **request))

    def test_all_six_empty_outputs(self):
        for request in REQUESTS:
            with self.subTest(analysis=request['analysis']):
                answer = run_question(fixture(), **{**request, 'filters': {'segments': {'region': 'Absent'}}})
                self.assertEqual(answer['status'], 'empty')
                self.check_round_trip(answer)

    def test_all_six_unavailable_outputs(self):
        for request in REQUESTS:
            with self.subTest(analysis=request['analysis']):
                answer = run_question(fixture().drop(columns='sales'), **request)
                self.assertEqual(answer['status'], 'unavailable')
                self.check_round_trip(answer)

    def test_empty_ranking_and_insufficient_period(self):
        answer = run_question(fixture(), 'segment_performance', by='region', min_records=99)
        self.assertEqual(answer['status'], 'empty_ranking')
        self.check_round_trip(answer)
        comparison = {**WINDOWS, 'current_start': '2026-03-01', 'current_end': '2026-03-31'}
        answer = run_question(fixture(), 'period_comparison', comparison=comparison)
        self.assertEqual(answer['status'], 'insufficient_data')
        self.check_round_trip(answer)

    def test_partial_currency_availability_and_no_combined_totals(self):
        frame = fixture()
        frame['currency'] = ['USD', 'TRY', 'TRY', 'TRY', 'TRY']
        answer = run_question(frame, 'period_comparison', comparison=WINDOWS)
        self.assertEqual(answer['status'], 'partial')
        self.check_round_trip(answer)
        answer['metrics'] = {'sales': 800}
        with self.assertRaises(ValidationError):
            parse_question_response(answer)

    def test_optional_profit_order_fields_and_no_loss(self):
        self.check_round_trip(run_question(fixture()[['sales']], 'summary'))
        for key in ('product', 'product_id'):
            self.check_round_trip(run_question(fixture().drop(columns=key), 'loss_concentration'))
        frame = fixture()
        frame['profit'] = frame.profit.abs()
        answer = run_question(frame, 'loss_concentration')
        self.assertEqual(answer['results'][0]['metrics']['total_gross_loss'], 0)
        self.check_round_trip(answer)

    def test_zero_negative_baselines_and_undefined_margins(self):
        frame = fixture()
        frame.loc[frame.date.dt.month.eq(1), ['sales', 'profit']] = 0
        self.check_round_trip(run_question(frame, 'period_comparison', comparison=WINDOWS))
        self.check_round_trip(run_question(fixture(), 'segment_performance', by='product', sort_by='sales'))

    def test_nonfinite_and_wrong_numeric_types_are_rejected(self):
        for value in (float('nan'), float('inf'), -float('inf'), '100', True, [], {}):
            answer = run_question(fixture(), 'summary')
            answer['results'][0]['metrics']['sales'] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_question_response(answer)
        answer = run_question(fixture(), 'summary')
        answer['results'][0]['metrics']['records'] = 5.0
        with self.assertRaises(ValidationError):
            parse_question_response(answer)

    def test_unknown_fields_at_all_levels_are_rejected(self):
        for path in ((), ('results', 0), ('results', 0, 'metrics'),
                     ('results', 0, 'scope'), ('results', 0, 'units'), ('results', 0, 'definitions')):
            answer = run_question(fixture(), 'summary')
            target = answer
            for key in path:
                target = target[key]
            target['unexpected'] = 1
            with self.subTest(path=path), self.assertRaises(ValidationError):
                parse_question_response(answer)

    def test_status_version_and_analysis_mismatches_are_rejected(self):
        for path, value in [(('schema_version',), 2), (('schema_version',), True),
                            (('results', 0, 'schema_version'), '1'), (('status',), 'partial'),
                            (('analysis',), 'monthly_trend'), (('results', 0, 'analysis'), 'monthly_trend'),
                            (('results', 0, 'status'), 'empty')]:
            answer = run_question(fixture(), 'summary')
            target = answer
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(ValidationError):
                parse_question_response(answer)

    def test_response_cannot_claim_values_for_empty_selection(self):
        answer = run_question(fixture(), 'summary', filters={'segments': {'region': 'Absent'}})
        answer['results'][0]['metrics'] = {'sales': 0, 'records': 0}
        with self.assertRaises(ValidationError):
            parse_question_response(answer)

    def test_ranking_dimensions_counts_and_loss_identity_are_validated(self):
        for change in ('count', 'dimension', 'unknown_metric'):
            answer = run_question(fixture(), 'segment_performance', by='region')
            entry = answer['results'][0]
            if change == 'count':
                entry['ranking']['returned_groups'] += 1
            elif change == 'dimension':
                del entry['records'][0]['region']
            else:
                entry['records'][0]['invented_metric'] = 1
            with self.subTest(change=change), self.assertRaises(ValidationError):
                parse_question_response(answer)
        answer = run_question(fixture(), 'loss_concentration')
        del answer['results'][0]['records'][0]['product']
        del answer['results'][0]['records'][0]['product_id']
        with self.assertRaises(ValidationError):
            parse_question_response(answer)

    def test_percentages_and_period_metadata_cannot_be_mislabelled(self):
        for change in ('margin_units', 'nonpositive_growth', 'days'):
            answer = run_question(fixture(), 'period_comparison', comparison=WINDOWS)
            entry = answer['results'][0]
            if change == 'days':
                entry['periods']['baseline']['calendar_days'] = 30
            else:
                metric = 'margin_percent' if change == 'margin_units' else 'profit'
                next(r for r in entry['records'] if r['metric'] == metric)['relative_change_percent'] = 50
            with self.subTest(change=change), self.assertRaises(ValidationError):
                parse_question_response(answer)

    def test_month_order_and_duplicate_currency_units_are_rejected(self):
        answer = run_question(fixture(), 'monthly_trend')
        answer['results'][0]['records'].reverse()
        with self.assertRaises(ValidationError):
            parse_question_response(answer)
        answer = run_question(fixture(), 'summary')
        answer['results'].append(deepcopy(answer['results'][0]))
        with self.assertRaises(ValidationError):
            parse_question_response(answer)


class FileContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'sales.csv'
        frame = fixture()
        frame.to_csv(self.path, index=False)
        self.original = self.path.read_bytes()
        self.config = ImportConfig(mapping={key: key for key in frame}, mapping_reviewed=True,
                                   allow_returns=True, currency='TRY')

    def test_all_six_file_envelopes_validate_without_shape_changes(self):
        for request in REQUESTS:
            with self.subTest(analysis=request['analysis']):
                payload = answer_file_question(self.path, self.config, request)
                model = FileQuestionResponse.model_validate(payload)
                self.assertEqual(model.model_dump(mode='json', exclude_unset=True), payload)
                self.assertEqual(FileQuestionResponse.model_validate_json(json.dumps(payload)), model)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_bad_request_is_rejected_before_any_file_read(self):
        requests = [{'analysis': []}, {'analysis': 'segment_performance', 'by': 'region', 'sort_by': []},
                    {'analysis': 'summary', 'filters': {'segments': {'region': []}}}]
        for request in requests:
            with self.subTest(request=request), patch('src.question_io.read_input') as read:
                with self.assertRaises(ValidationError):
                    answer_file_question('not-present.csv', self.config, request)
                read.assert_not_called()

    def test_typed_request_can_be_executed(self):
        payload = answer_file_question(self.path, self.config, parse_question_request(REQUESTS[0]))
        self.assertEqual(payload['status'], 'ok')

    def test_quality_only_and_explicit_exclusion_are_typed(self):
        self.path.write_text('sales,profit\n100,10\nbad,2\n', encoding='utf-8')
        config = ImportConfig(mapping={'sales': 'sales', 'profit': 'profit'}, mapping_reviewed=True)
        payload = answer_file_question(self.path, config, {'analysis': 'summary'})
        self.assertIsNone(FileQuestionResponse.model_validate(payload).answer)
        config.invalid_rows = 'exclude'
        payload = answer_file_question(self.path, config, {'analysis': 'summary'})
        self.assertEqual(FileQuestionResponse.model_validate(payload).quality.excluded_records, [2])

    def test_internal_bad_output_is_not_reported_as_bad_user_input(self):
        answer = run_question(fixture(), 'summary')
        answer['results'][0]['metrics']['sales'] = float('nan')
        with patch('src.question_io.run_question', return_value=answer):
            with self.assertRaises(ContractViolation) as caught:
                answer_file_question(self.path, self.config, {'analysis': 'summary'})
        self.assertIsInstance(caught.exception.__cause__, ValidationError)
        self.assertNotIsInstance(caught.exception, ValueError)

    def test_file_status_hash_and_quality_counts_are_validated(self):
        for change in ('status', 'hash', 'count', 'extra', 'capability', 'date_boundary'):
            payload = answer_file_question(self.path, self.config, {'analysis': 'summary'})
            if change == 'status':
                payload['status'] = 'unavailable'
            elif change == 'hash':
                payload['source']['sha256'] = 'not-a-sha256'
            elif change == 'count':
                payload['quality']['analyzed_rows'] = 999
            elif change == 'capability':
                del payload['quality']['capabilities']['product_losses']
            elif change == 'date_boundary':
                del payload['answer']['results'][0]['scope']['input_observed_dates']['start']
            else:
                payload['import_config']['secret'] = 'unexpected'
            with self.subTest(change=change), self.assertRaises(ValidationError):
                FileQuestionResponse.model_validate(payload)

    def test_all_six_cli_requests_produce_typed_files(self):
        config_path = self.path.with_name('mapping.json')
        self.config.save(config_path)
        for index, request in enumerate(REQUESTS):
            request_path = self.path.with_name(f'request-{index}.json')
            output = self.path.with_name(f'answer-{index}.json')
            request_path.write_text(json.dumps(request), encoding='utf-8')
            with self.subTest(analysis=request['analysis']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['ask', str(self.path), '--config', str(config_path),
                                       '--request', str(request_path), '--output', str(output)]), 0)
                payload = json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(FileQuestionResponse.model_validate(payload).answer.analysis, request['analysis'])

    def test_cli_bad_requests_exit_cleanly_without_creating_output(self):
        config_path = self.path.with_name('mapping.json')
        self.config.save(config_path)
        for index, request in enumerate(({'analysis': []},
            {'analysis': 'segment_performance', 'by': 'region', 'sort_by': []},
            {'analysis': 'segment_performance', 'by': 'region', 'limit': True})):
            request_path = self.path.with_name(f'bad-{index}.json')
            output = self.path.with_name(f'bad-output-{index}.json')
            request_path.write_text(json.dumps(request), encoding='utf-8')
            with self.subTest(request=request), contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(['ask', str(self.path), '--config', str(config_path),
                                       '--request', str(request_path), '--output', str(output)]), 1)
                self.assertIn('Could not complete', stdout.getvalue())
                self.assertFalse(output.exists())
        self.assertEqual(self.path.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
