"""Frozen pre-refactor answers: compare complete results and error behavior."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.questions import run_question
from src import questions


SNAPSHOT = Path(__file__).with_name('fixtures') / 'question_outputs_before_handlers.json'
WINDOWS = {'baseline_start': '2026-01-01', 'baseline_end': '2026-01-31',
           'current_start': '2026-02-01', 'current_end': '2026-02-28'}


def fixture():
    return pd.DataFrame({
        'date': pd.to_datetime(['2025-01-01', '2025-01-31', '2026-01-01',
                               '2026-01-31 23:59:59', '2026-02-01', '2026-02-28', '2026-04-01'], format='mixed'),
        'sales': [100., 100., 100., 200., 600., 0., -100.],
        'profit': [10., 10., 20., -40., 60., -10., -5.],
        'category': ['A', 'B', 'A', 'B', 'A', 'B', 'B'],
        'region': ['East', 'West', 'East', 'East', 'West', 'West', 'West'],
        'product_id': ['P1', 'P2', 'P1', 'P2', 'P1', 'P3', 'P3'],
        'product': ['One', 'Two', 'One', 'Two', 'One', 'Three', 'Three'],
        'order_id': ['O1', 'O2', 'O3', 'O4', 'O5', 'O6', 'O7'],
        'discount': [0., 0., .1, .4, .1, .5, .5],
    })


def compatibility_cases():
    """Fixed synthetic scenarios; expected answers live in the frozen JSON file."""
    frame = fixture()
    requests = [
        {'analysis': 'summary'},
        {'analysis': 'segment_performance', 'by': 'region'},
        {'analysis': 'period_comparison', 'comparison': WINDOWS},
        {'analysis': 'monthly_trend'},
        {'analysis': 'loss_concentration'},
        {'analysis': 'discount_comparison'},
    ]
    cases = []
    for request in requests:
        analysis = request['analysis']
        scoped = {'segments': {'category': ['A', 'B']}}
        if analysis != 'period_comparison':
            scoped.update(start='2026-01-01', end='2026-02-28')
        currencies = frame.assign(currency=['USD', 'USD', 'TRY', 'TRY', 'TRY', 'TRY', 'TRY'])
        variants = [
            ('standard', frame, request),
            ('scoped', frame, {**request, 'filters': scoped}),
            ('empty_selection', frame, {**request, 'filters': {'segments': {'region': 'Absent'}}}),
            ('unavailable_sales', frame.drop(columns='sales'), request),
            ('no_profit', frame.drop(columns='profit'), request),
            ('currencies', currencies, request),
            ('empty_input', frame.iloc[:0], request),
        ]
        cases.extend((analysis + '/' + name, data.copy(deep=True), deepcopy(spec))
                     for name, data, spec in variants)

    rank = {'analysis': 'segment_performance', 'by': 'region'}
    compare = {'analysis': 'period_comparison', 'comparison': WINDOWS}
    missing_labels = frame.copy()
    missing_labels.loc[0, 'category'] = None
    incomplete_orders = frame.copy()
    incomplete_orders.loc[2, 'order_id'] = None
    incomplete_products = frame.copy()
    incomplete_products.loc[0, 'product_id'] = None
    incomplete_products.loc[1, 'product'] = None
    zero_baseline = frame.copy()
    zero_baseline.loc[zero_baseline.date.dt.year.eq(2026) & zero_baseline.date.dt.month.eq(1), ['sales', 'profit']] = 0
    negative_baseline = frame.copy()
    negative_baseline.loc[negative_baseline.date.dt.year.eq(2026) & negative_baseline.date.dt.month.eq(1), 'sales'] *= -1
    ties = frame.copy()
    ties['profit'] = -10.
    cases.extend([
        ('ranking/lowest_margin', frame, {**rank, 'sort_by': 'margin_percent', 'limit': 1}),
        ('ranking/explicit_descending', frame, {**rank, 'sort_by': 'margin_percent', 'ascending': False}),
        ('ranking/empty_minimum', frame, {**rank, 'min_records': 99}),
        ('ranking/minimum_full_denominator', frame, {**rank, 'min_records': 3, 'sort_by': 'gross_loss'}),
        ('ranking/composite_key', frame, {**rank, 'by': ['category', 'region'], 'sort_by': 'profit', 'limit': 2}),
        ('ranking/tuple_key', frame, {**rank, 'by': ('region', 'category')}),
        ('ranking/undefined_margin', frame, {**rank, 'by': 'product', 'sort_by': 'margin_percent'}),
        ('ranking/stable_ties', ties, {**rank, 'by': 'product_id', 'sort_by': 'gross_loss', 'limit': 2}),
        ('ranking/labels_missing_outside_filter', missing_labels, {**rank, 'by': 'category', 'filters': {'start': '2026-01-01'}}),
        ('ranking/labels_missing_inside_scope', missing_labels, {**rank, 'by': 'category'}),
        ('comparison/zero_baseline', zero_baseline, compare),
        ('comparison/negative_baseline', negative_baseline, compare),
        ('comparison/missing_period', frame, {**compare, 'comparison': {**WINDOWS, 'current_start': '2026-03-01', 'current_end': '2026-03-31'}}),
        ('comparison/segment_filter', frame, {**compare, 'filters': {'segments': {'region': 'East'}}}),
        ('loss/no_negative_rows', frame.assign(profit=frame.profit.abs()), {'analysis': 'loss_concentration'}),
        ('loss/top_limit', frame, {'analysis': 'loss_concentration', 'limit': 1}),
        ('loss/id_only', frame.drop(columns='product'), {'analysis': 'loss_concentration'}),
        ('loss/name_only', frame.drop(columns='product_id'), {'analysis': 'loss_concentration'}),
        ('loss/no_complete_product_key', incomplete_products, {'analysis': 'loss_concentration'}),
        ('discount/constant_groups', frame.assign(discount=.2), {'analysis': 'discount_comparison'}),
        ('summary/incomplete_orders', incomplete_orders, {'analysis': 'summary'}),
        ('summary/known_unit', frame, {'analysis': 'summary', 'unit': 'TRY'}),
        ('summary/nonpositive_sales', frame.assign(sales=-frame.sales.abs()), {'analysis': 'summary'}),
        ('summary/sales_only', frame[['sales']], {'analysis': 'summary'}),
        ('invalid/analysis_list', frame, {'analysis': []}),
        ('invalid/sort_list', frame, {**rank, 'sort_by': []}),
        ('invalid/metric', frame, {**rank, 'sort_by': 'quantity'}),
        ('invalid/group_key', frame, {**rank, 'by': [['region']]}),
        ('invalid/limit_bool', frame, {**rank, 'limit': True}),
        ('invalid/overlapping_periods', frame, {**compare, 'comparison': {**WINDOWS, 'baseline_end': '2026-02-01'}}),
        ('invalid/date_filter_with_comparison', frame, {**compare, 'filters': {'start': '2026-01-01'}}),
        ('invalid/currency_conflict', frame.assign(currency='TRY'), {'analysis': 'summary', 'unit': 'USD'}),
    ])
    return cases


def capture(frame, request):
    try:
        return {'result': run_question(frame, **request)}
    except (ValueError, TypeError) as exc:
        return {'error_type': type(exc).__name__, 'message': str(exc)}


class HandlerCompatibilityTests(unittest.TestCase):
    def test_complete_outputs_equal_pre_refactor_snapshot(self):
        expected = json.loads(SNAPSHOT.read_text(encoding='utf-8'))['cases']
        cases = compatibility_cases()
        self.assertEqual(set(expected), {name for name, _, _ in cases})
        self.assertEqual(len(cases), len(expected))
        for name, frame, request in cases:
            with self.subTest(case=name):
                actual = capture(frame, request)
                # JSON comparison also distinguishes bool/int/float and preserves warning order.
                self.assertEqual(json.dumps(actual, sort_keys=True, allow_nan=False),
                                 json.dumps(expected[name], sort_keys=True, allow_nan=False))

    def test_all_scenarios_preserve_input_frames_and_requests(self):
        for name, frame, request in compatibility_cases():
            with self.subTest(case=name):
                original_frame = frame.copy(deep=True)
                original_request = deepcopy(request)
                capture(frame, request)
                assert_frame_equal(frame, original_frame)
                self.assertEqual(request, original_request)


class HandlerRoutingTests(unittest.TestCase):
    def test_each_analysis_has_its_own_handler(self):
        self.assertEqual(set(questions._ANALYSIS_HANDLERS), set(questions.QUESTION_CATALOG))
        self.assertEqual(len(set(questions._ANALYSIS_HANDLERS.values())), 6)

    def test_shared_pipeline_filters_and_splits_currencies_before_handlers(self):
        frame = fixture().assign(currency=['USD', 'USD', 'TRY', 'TRY', 'TRY', 'TRY', 'TRY'])
        original = frame.copy(deep=True)
        for name, _, request in compatibility_cases():
            if not name.endswith('/standard'):
                continue
            analysis = request['analysis']
            request['filters'] = {'segments': {'category': 'A'}}
            handler = Mock(wraps=questions._ANALYSIS_HANDLERS[analysis])
            with self.subTest(analysis=analysis), patch.dict(questions._ANALYSIS_HANDLERS, {analysis: handler}):
                answer = run_question(frame, **request)
                self.assertEqual(handler.call_count, 2)
                self.assertEqual(len(answer['results']), 2)
                for call in handler.call_args_list:
                    selected, entry, _ = call.args
                    self.assertEqual(selected.category.unique().tolist(), ['A'])
                    self.assertEqual(selected.currency.nunique(), 1)
                    currency = selected.currency.iloc[0]
                    self.assertEqual(entry['units']['amount'], currency)
                    self.assertEqual(entry['scope']['input_records'], int(frame.currency.eq(currency).sum()))
                    self.assertEqual(entry['scope']['selected_records'], len(selected))
                    self.assertEqual(entry['filters']['segments'], {'category': ['A']})
        assert_frame_equal(frame, original)

    def test_empty_and_unavailable_inputs_do_not_reach_handlers(self):
        for name, frame, request in compatibility_cases():
            if not name.endswith(('/empty_selection', '/unavailable_sales', '/empty_input')):
                continue
            handler = Mock(side_effect=AssertionError('A handler must not run for empty/unavailable input.'))
            with self.subTest(case=name), patch.dict(questions._ANALYSIS_HANDLERS, {request['analysis']: handler}):
                answer = run_question(frame, **request)
                self.assertIn(answer['status'], ('empty', 'unavailable'))
                handler.assert_not_called()

    def test_invalid_requests_are_rejected_before_dispatch(self):
        handlers = {name: Mock(side_effect=AssertionError('Invalid request reached a handler.'))
                    for name in questions._ANALYSIS_HANDLERS}
        with patch.dict(questions._ANALYSIS_HANDLERS, handlers):
            for name, frame, request in compatibility_cases():
                if not name.startswith('invalid/'):
                    continue
                with self.subTest(case=name), self.assertRaises(ValueError):
                    run_question(frame, **request)
        for handler in handlers.values():
            handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
