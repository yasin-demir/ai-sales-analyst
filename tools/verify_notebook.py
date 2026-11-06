"""Reproduce the real-data case study and reconcile it with library outputs."""
import argparse
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from src.analysis import summarize_csv, overall_metrics
from src.data import load_sales

class Anchors(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=set()
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.add(attrs['id'])

CHECKS = """
from pandas.testing import assert_frame_equal
assert kpis == overall_metrics(df)
assert_frame_equal(category_performance, segment_performance(df, 'Category'))
assert_frame_equal(regional_performance, segment_performance(df, 'Region'))
assert_frame_equal(monthly_performance, time_performance(df))
assert_frame_equal(calendar_month_summary, calendar_month_performance(df))
assert np.isclose(product_losses.sum(), kpis['gross_loss'])
assert np.isclose(kpis['positive_profit']-kpis['gross_loss'], kpis['profit'])
assert np.isclose(pool_overlap.Top_Product_Loss.sum(), product_losses.head(50).sum())
assert np.isclose(mix_change+within_change, observed_delta)
"""

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='Save verified executed outputs')
    parser.add_argument('--html',type=Path,help='Optional standalone HTML output')
    args=parser.parse_args()
    path=ROOT/'notebooks/01_sales_eda.ipynb'
    csv=ROOT/'data/Sample - Superstore.csv'
    df=load_sales(csv);exact=summarize_csv(csv);metrics=overall_metrics(df)
    assert exact['rows']==metrics['rows']
    assert abs(exact['sales']-Decimal(str(metrics['sales'])))<Decimal('0.000001')
    assert abs(exact['profit']-Decimal(str(metrics['profit'])))<Decimal('0.000001')
    original=path.read_bytes()
    final=None
    for directory in (ROOT,ROOT/'notebooks'):
        nb=nbformat.read(path,as_version=4)
        nb.cells.append(nbformat.v4.new_code_cell(CHECKS))
        NotebookClient(nb,timeout=120,kernel_name='python3',resources={'metadata':{'path':str(directory)}}).execute()
        nb.cells.pop()
        assert not any(o.output_type=='error' for c in nb.cells if c.cell_type=='code' for o in c.outputs)
        images=sum('image/png' in o.get('data',{}) for c in nb.cells if c.cell_type=='code' for o in c.outputs)
        assert images==9, f'Expected the nine case-study figures, got {images}'
        nbformat.validate(nb)
        print(f'PASS: fresh kernel from {directory.name}; {images} figures; metric reconciliation.')
        final=nb
    html,_=HTMLExporter().from_notebook_node(final)
    anchors=Anchors();anchors.feed(html)
    assert all(target in anchors.ids for target in re.findall(r'\]\(#([^)]+)\)',final.cells[0].source))
    for cell in final.cells:
        texts=[cell.source] if cell.cell_type=='markdown' else [o.get('data',{}).get('text/markdown','') for o in cell.outputs]
        for text in texts:
            assert not re.search(r'\$\s*\d',re.sub(r'`[^`]*`','',text)), 'Unescaped currency in Markdown'
    assert path.read_bytes()==original, 'Notebook was changed externally during verification.'
    if args.write:nbformat.write(final,path)
    if args.html:
        args.html.parent.mkdir(parents=True,exist_ok=True)
        # Generated fixed-width tables can contain trailing spaces; keep exports
        # suitable for version control without changing displayed values.
        args.html.write_text('\n'.join(line.rstrip() for line in html.splitlines())+'\n',encoding='utf-8')
    print('PASS: source/CLI totals, shared functions, nine figures, navigation, and Markdown currency formatting.')
    return 0

if __name__=='__main__':raise SystemExit(main())
