"""Run the managed-upload/prepared-query workflow on synthetic example data."""
import argparse
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.datasets import DatasetService
from src.importing import ImportConfig
from src.periods import last_two_complete_months


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, default=ROOT / 'work' / 'prepared-data-demo')
    args = parser.parse_args(argv)
    service = DatasetService(args.store)
    uploaded = service.upload_path(ROOT / 'examples' / 'completed_months_sales.csv')
    preview = service.preview(uploaded.dataset_id, rows=3)
    config = ImportConfig.load(ROOT / 'examples' / 'completed_months_mapping.json')
    mapping = service.approve_mapping(uploaded.dataset_id, config)
    prepared = service.prepare(uploaded.dataset_id)
    summary = service.query(prepared.preparation_id, {'analysis': 'summary'})
    comparison = service.query(prepared.preparation_id, {
        'analysis': 'period_comparison',
        'comparison': last_two_complete_months(date(2026, 11, 6)),
    })
    (args.store / 'summary.json').write_text(summary.model_dump_json(indent=2) + '\n', encoding='utf-8')
    (args.store / 'comparison.json').write_text(comparison.model_dump_json(indent=2) + '\n', encoding='utf-8')
    print(f'Dataset: {uploaded.dataset_id}')
    print(f'Preview: {preview.rows} rows, columns: {", ".join(preview.columns)}')
    print(f'Approved mapping revision: {mapping.revision}; preparation revision: {prepared.revision}')
    print(f'Source SHA-256: {uploaded.source.sha256}')
    print('Comparison: September 2026 -> October 2026')
    for row in comparison.result.answer.results[0].records:
        if row.metric in ('sales', 'profit'):
            print(f'{row.metric}: {row.baseline} -> {row.current}; change: {row.relative_change_percent}%')
    print(f'Answer files: {args.store.resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
