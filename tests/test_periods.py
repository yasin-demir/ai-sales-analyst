"""Independent date-rule acceptance checks, before analytical integration."""
from datetime import date, datetime
import calendar
import unittest

from src.periods import last_two_complete_months


class PeriodTests(unittest.TestCase):
    def test_november_example(self):
        self.assertEqual(last_two_complete_months(date(2026, 11, 6)), {
            'baseline_start': '2026-09-01', 'baseline_end': '2026-09-30',
            'current_start': '2026-10-01', 'current_end': '2026-10-31',
        })

    def test_year_boundary(self):
        self.assertEqual(last_two_complete_months(date(2026, 1, 1)), {
            'baseline_start': '2025-11-01', 'baseline_end': '2025-11-30',
            'current_start': '2025-12-01', 'current_end': '2025-12-31',
        })

    def test_february_leap_and_common_years(self):
        for year, end in [(2024, 29), (2025, 28), (2000, 29), (2100, 28)]:
            with self.subTest(year=year):
                bounds = last_two_complete_months(date(year, 3, 15))
                self.assertEqual(bounds['baseline_start'], f'{year}-01-01')
                self.assertEqual(bounds['baseline_end'], f'{year}-01-31')
                self.assertEqual(bounds['current_start'], f'{year}-02-01')
                self.assertEqual(bounds['current_end'], f'{year}-02-{end}')

    def test_all_month_lengths_against_calendar(self):
        for year in (2024, 2025, 2026):
            for month in range(1, 13):
                bounds = last_two_complete_months(date(year, month, 15))
                for prefix, offset in [('baseline', 2), ('current', 1)]:
                    expected_year, zero_month = divmod(year * 12 + month - 1 - offset, 12)
                    expected_month = zero_month + 1
                    self.assertEqual(bounds[f'{prefix}_start'], date(expected_year, expected_month, 1).isoformat())
                    self.assertEqual(bounds[f'{prefix}_end'], date(expected_year, expected_month, calendar.monthrange(expected_year, expected_month)[1]).isoformat())

    def test_current_day_does_not_change_completed_months(self):
        self.assertEqual(last_two_complete_months(date(2026, 11, 1)), last_two_complete_months(date(2026, 11, 30)))

    def test_invalid_types_and_out_of_range_dates(self):
        for value in (None, '2026-11-06', datetime(2026, 11, 6), date(1, 1, 1), date(1, 2, 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                last_two_complete_months(value)


if __name__ == '__main__':
    unittest.main()
