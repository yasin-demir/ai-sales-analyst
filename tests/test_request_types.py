"""Public Python entry points must reject bad request types predictably."""
import unittest
import pandas as pd

from src.questions import AnalysisFilters, run_question, segment_table


class RequestTypeTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({'sales': [100.], 'profit': [10.], 'region': ['A']})

    def test_analysis_list_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            run_question(self.frame, [])

    def test_sort_list_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            run_question(self.frame, 'segment_performance', by='region', sort_by=[])

    def test_invalid_request_type_matrix(self):
        cases = ([{'analysis': value} for value in ([], {}, None, 1, True)] +
                 [{'analysis': 'segment_performance', 'by': 'region', 'sort_by': value}
                  for value in ([], {}, 1, True)] +
                 [{'analysis': 'segment_performance', 'by': value}
                  for value in ([[]], [{}], [True], 1, {'region': 1})] +
                 [{'analysis': 'summary', 'limit': value} for value in (True, '10', [], None)] +
                 [{'analysis': 'summary', 'filters': value} for value in ([], True, 1)])
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_question(self.frame, **kwargs)

    def test_direct_grouping_rejects_non_dimensions(self):
        for by in (1, None, [[]], [{}], [True], ['region', 'region']):
            with self.subTest(by=by), self.assertRaises(ValueError):
                segment_table(self.frame, by)

    def test_date_filter_types_are_validated(self):
        for value in ([], {}, True, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AnalysisFilters(start=value)


if __name__ == '__main__':
    unittest.main()
