"""Local analysis entry point: python -m src.main --help."""
import argparse
import json
from pathlib import Path
from .analysis import summarize_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = PROJECT_ROOT / 'data' / 'Sample - Superstore.csv'

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Local Superstore sales analysis; no external service calls.')
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA, help='CSV file path')
    parser.add_argument('--report', choices=['summary', 'category', 'region', 'losses', 'time', 'diagnostics'], default='summary')
    parser.add_argument('--json', action='store_true', help='Machine-readable result including units and scope')
    args = parser.parse_args(argv)
    if not args.data.is_file():
        print(f'Data file not found: {args.data}')
        return 1
    try:
        if args.report == 'summary':
            summary = summarize_csv(args.data)
            if args.json:
                print(json.dumps({'report': 'summary', 'units': 'dataset dollars',
                                  **{k: str(v) if k in ('sales', 'profit') and v is not None else v for k,v in summary.items()}}, allow_nan=False))
            else:
                print(f"Order lines: {summary['rows']:,}")
                print(f"Sales: ${summary['sales']:,.2f}")
                if summary['profit'] is not None:
                    print(f"Net profit: ${summary['profit']:,.2f}")
            return 0
        from .data import load_sales
        from .analysis import segment_performance, loss_concentration, time_performance, loss_pool_overlap
        frame = load_sales(args.data)
        reports = {'category': lambda: segment_performance(frame, 'Category'),
                   'region': lambda: segment_performance(frame, 'Region'),
                   'losses': lambda: loss_concentration(frame),
                   'time': lambda: time_performance(frame),
                   'diagnostics': lambda: loss_pool_overlap(frame)}
        table = reports[args.report]()
        table = table.reset_index()
        for col in table:
            if str(table[col].dtype).startswith('period'):
                table[col] = table[col].astype(str)
        if args.json:
            print(json.dumps({'report': args.report, 'units': 'sales/profit/loss: dataset dollars; margins/shares: percent',
                              'scope': {'rows': len(frame), 'start': str(frame['Order Date'].min().date()),
                                        'end': str(frame['Order Date'].max().date())},
                              'records': json.loads(table.to_json(orient='records'))}, allow_nan=False))
        else:
            print('Amounts: dollars | margins/shares: percent | Top-N loss shares are nested, not additive.')
            print(table.round(2).to_string(index=False))
        return 0
    except (OSError, ValueError, UnicodeError) as exc:
        print(f'Analysis could not be completed: {exc}')
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
