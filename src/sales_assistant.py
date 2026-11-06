"""Validated intent -> calendar bounds -> existing analytics -> factual Turkish text."""
from datetime import date

from .periods import last_two_complete_months
from .question_io import answer_file_question


READY = {'status': 'ready', 'analysis': 'period_comparison',
         'period_rule': 'last_two_complete_months'}


def validate_decision(decision):
    """Reject unknown fields, arbitrary dates, filters and executable model output."""
    if not isinstance(decision, dict):
        raise ValueError('Model decision must be an object.')
    status = decision.get('status')
    if status == 'ready':
        if decision != READY:
            raise ValueError('Only an unfiltered last-two-complete-months comparison is supported.')
    elif status in ('needs_clarification', 'unsupported'):
        field = 'question' if status == 'needs_clarification' else 'message'
        if set(decision) != {'status', field} or not isinstance(decision[field], str) or not 1 <= len(decision[field].strip()) <= 1000:
            raise ValueError('A nonempty clarification question or unsupported message is required.')
    else:
        raise ValueError('Unknown decision status.')
    return dict(decision)


def resolve_request(decision, reference_date: date):
    decision = validate_decision(decision)
    if decision['status'] != 'ready':
        raise ValueError('An unresolved decision cannot be executed.')
    return {'analysis': 'period_comparison',
            'comparison': last_two_complete_months(reference_date)}


def _number(value):
    return f'{value:,.2f}'.translate(str.maketrans({',': '.', '.': ','}))


def render_answer(payload):
    """All numerical claims come from the deterministic result, never an LLM."""
    decision = payload['decision']
    if decision['status'] != 'ready':
        return decision.get('question', decision.get('message'))
    bounds = payload['request']['comparison']
    lines = [f"Önceki dönem: {bounds['baseline_start']} – {bounds['baseline_end']}",
             f"Son tamamlanmış dönem: {bounds['current_start']} – {bounds['current_end']}"]
    result = payload['analysis_result']
    if result['answer'] is None:
        lines.append('Veri doğrulaması tamamlanamadı; analiz yapılmadı.')
        lines.extend(issue['message'] for issue in result['quality']['issues'])
        return '\n'.join(lines)
    for entry in result['answer']['results']:
        unit = entry['units']['amount']
        lines.append(f'Birim: {unit}')
        if entry['status'] != 'ok':
            lines.append('Karşılaştırma üretilemedi; durum: ' + entry['status'])
        else:
            periods = entry['periods']
            lines.append(f"Kayıt sayısı: önceki {periods['baseline']['records']}, son {periods['current']['records']}.")
            records = {row['metric']: row for row in entry['records']}
            for metric, label in [('sales', 'Satış'), ('profit', 'Kâr')]:
                if metric not in records:
                    lines.append(f'{label} alanı yok; karşılaştırması hesaplanamadı.')
                    continue
                row = records[metric]
                change = row['relative_change_percent']
                rate = (f'%{_number(change)}' if change is not None else
                        'Yüzde değişim tanımsız: önceki değer sıfır, negatif veya eksik.')
                lines.append(f"{label}: {_number(row['baseline'])} -> {_number(row['current'])} {unit}. "
                             f"Fark: {_number(row['absolute_change'])} {unit}; {rate}")
        lines.extend('Uyarı: ' + warning for warning in entry['warnings'])
    return '\n'.join(lines)


def execute_decision(path, config, decision, reference_date: date):
    """Only validated ready decisions can reach the existing file-level engine."""
    decision = validate_decision(decision)
    if type(reference_date) is not date:
        raise ValueError('reference_date must be a datetime.date.')
    payload = {'schema_version': 1, 'reference_date': reference_date.isoformat(),
               'decision': decision, 'status': decision['status'],
               'request': None, 'analysis_result': None}
    if decision['status'] == 'ready':
        payload['request'] = resolve_request(decision, reference_date)
        payload['analysis_result'] = answer_file_question(path, config, payload['request'])
        payload['status'] = payload['analysis_result']['status']
    payload['text'] = render_answer(payload)
    return payload
