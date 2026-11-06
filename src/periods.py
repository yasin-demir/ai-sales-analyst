"""Calendar rules. The application supplies the business-local reference date."""
from datetime import date, timedelta


def last_two_complete_months(reference_date: date) -> dict[str, str]:
    """Return inclusive ISO bounds for the two months before reference_date's month."""
    if type(reference_date) is not date:
        raise ValueError('reference_date must be a datetime.date, without a time.')
    try:
        current_end = reference_date.replace(day=1) - timedelta(days=1)
        current_start = current_end.replace(day=1)
        baseline_end = current_start - timedelta(days=1)
        baseline_start = baseline_end.replace(day=1)
    except OverflowError as exc:
        raise ValueError('Reference date must allow two preceding calendar months.') from exc
    return {
        'baseline_start': baseline_start.isoformat(),
        'baseline_end': baseline_end.isoformat(),
        'current_start': current_start.isoformat(),
        'current_end': current_end.isoformat(),
    }
