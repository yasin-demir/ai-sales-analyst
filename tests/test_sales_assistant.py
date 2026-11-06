"""File-to-answer acceptance checks with hand-calculated amounts; no model needed."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.importing import ImportConfig
from src.sales_assistant import READY, execute_decision, resolve_request, validate_decision


class AssistantTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'sales.csv'
        self.content = ('date,sales,profit\n2026-08-31,999999,999999\n'
                        '2026-09-01,40000,5000\n2026-09-30,60000,7000\n'
                        '2026-10-01,50000,6000\n2026-10-31,70000,9000\n'
                        '2026-11-01,999999,999999\n')
        self.path.write_text(self.content, encoding='utf-8')
        self.config = ImportConfig(mapping={'date': 'date', 'sales': 'sales', 'profit': 'profit'},
                                   mapping_reviewed=True, currency='TRY')

    def run_answer(self):
        return execute_decision(self.path, self.config, READY, date(2026, 11, 6))

    def test_hand_calculated_amounts_dates_rates_and_source_preservation(self):
        answer = self.run_answer()
        self.assertEqual(answer['status'], 'ok')
        records = {r['metric']: r for r in answer['analysis_result']['answer']['results'][0]['records']}
        self.assertEqual(records['sales']['baseline'], 100000)
        self.assertEqual(records['sales']['current'], 120000)
        self.assertEqual(records['sales']['absolute_change'], 20000)
        self.assertEqual(records['sales']['relative_change_percent'], 20)
        self.assertEqual(records['profit']['baseline'], 12000)
        self.assertEqual(records['profit']['current'], 15000)
        self.assertEqual(records['profit']['absolute_change'], 3000)
        self.assertEqual(records['profit']['relative_change_percent'], 25)
        self.assertIn('2026-09-01', answer['text'])
        self.assertIn('120.000,00', answer['text'])
        self.assertIn('%25,00', answer['text'])
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.content)

    def test_missing_period_is_not_zero_sales(self):
        self.path.write_text('date,sales,profit\n2026-10-01,100,10\n', encoding='utf-8')
        answer = self.run_answer()
        self.assertEqual(answer['status'], 'insufficient_data')
        self.assertNotIn('Satış:', answer['text'])

    def test_no_profit_is_explicit(self):
        self.config.mapping.pop('profit')
        answer = self.run_answer()
        self.assertIn('Kâr alanı yok', answer['text'])
        self.assertIn('Satış:', answer['text'])

    def test_zero_and_negative_baselines_have_no_growth_rate(self):
        for profit in ('0', '-10'):
            self.path.write_text(f'date,sales,profit\n2026-09-01,100,{profit}\n2026-10-01,120,20\n', encoding='utf-8')
            answer = self.run_answer()
            self.assertIn('Yüzde değişim tanımsız', answer['text'])

    def test_quality_failure_is_not_an_answer(self):
        self.path.write_text('date,sales,profit\n2026-09-01,broken,10\n', encoding='utf-8')
        answer = self.run_answer()
        self.assertEqual(answer['status'], 'needs_attention')
        self.assertIn('analiz yapılmadı', answer['text'])

    def test_currencies_remain_separate(self):
        self.path.write_text('date,sales,profit,currency\n2026-09-01,100,10,TRY\n2026-10-01,120,12,TRY\n2026-09-01,1000,100,USD\n2026-10-01,900,90,USD\n', encoding='utf-8')
        self.config.currency = None
        self.config.mapping['currency'] = 'currency'
        answer = self.run_answer()
        self.assertEqual(len(answer['analysis_result']['answer']['results']), 2)
        self.assertIn('Birim: TRY', answer['text'])
        self.assertIn('Birim: USD', answer['text'])

    def test_unresolved_decisions_do_not_read_files(self):
        for decision in ({'status': 'needs_clarification', 'question': 'Hangi dönem?'},
                         {'status': 'unsupported', 'message': 'Bu analiz desteklenmiyor.'}):
            with patch('src.sales_assistant.answer_file_question') as engine:
                result = execute_decision('missing.csv', self.config, decision, date(2026, 11, 6))
                engine.assert_not_called()
                self.assertIsNone(result['request'])

    def test_model_cannot_inject_dates_functions_or_filters(self):
        for extra in ({'comparison': {}}, {'filters': {'region': 'East'}}, {'code': 'print(1)'}):
            with self.assertRaises(ValueError):
                validate_decision({**READY, **extra})
        for decision in ({'status': 'ready', 'analysis': 'predict'}, {'status': 'unknown'},
                         {'status': 'needs_clarification', 'question': ''}, []):
            with self.assertRaises(ValueError):
                validate_decision(decision)


if __name__ == '__main__':
    unittest.main()
