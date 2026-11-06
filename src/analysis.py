"""Yerel CSV dosyası üzerinde basit ve denetlenebilir hesaplamalar."""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path


def summarize_csv(path: Path) -> dict:
    """Satır sayısını, satışları ve varsa kârı hesapla."""
    try:
        return _read_summary(path, "utf-8-sig")
    except UnicodeDecodeError:
        return _read_summary(path, "cp1252")


def _read_summary(path: Path, encoding: str) -> dict:
    with path.open(newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "Sales" not in reader.fieldnames:
            raise ValueError("CSV içinde 'Sales' sütunu bulunamadı. Dosya başlıklarını kontrol edin.")
        has_profit = "Profit" in reader.fieldnames
        sales = Decimal("0")
        profit = Decimal("0")
        count = 0
        for row in reader:
            count += 1
            sales += _number(row.get("Sales"), reader.line_num, "Sales")
            if has_profit:
                profit += _number(row.get("Profit"), reader.line_num, "Profit")
    if count == 0:
        raise ValueError("CSV contains no order lines.")
    return {"rows": count, "sales": sales, "profit": profit if has_profit else None}


def _number(value: str | None, line: int, column: str) -> Decimal:
    try:
        number = Decimal((value or "").strip())
        if not number.is_finite():
            raise InvalidOperation
        return number
    except InvalidOperation as exc:
        raise ValueError(f"CSV satırı {line}: {column} sayısal değil: {value!r}") from exc


# Public analytical functions accept a DataFrame returned by load_sales/validate_sales.
# Numeric results stay numeric; rounding and currency formatting belong to presentation.
def _columns(frame, columns):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f'Missing analysis columns: {sorted(missing)}')

def _margin(profit, sales):
    """Undefined aggregate margins are NaN, rather than infinity or zero."""
    return profit.div(sales.where(sales.ne(0))).mul(100)

def overall_metrics(frame):
    _columns(frame, ['Sales', 'Profit', 'Order ID'])
    sales = float(frame['Sales'].sum())
    profit = float(frame['Profit'].sum())
    orders = int(frame['Order ID'].nunique())
    return {'rows': len(frame), 'sales': sales, 'profit': profit,
            'profit_margin': profit / sales * 100 if sales else float('nan'),
            'orders': orders, 'average_order_value': sales / orders if orders else float('nan'),
            'gross_loss': float(-frame.loc[frame['Profit'].lt(0), 'Profit'].sum()),
            'positive_profit': float(frame.loc[frame['Profit'].gt(0), 'Profit'].sum())}

def segment_performance(frame, by):
    """Sum sales/profit before calculating margin; keep missing group keys visible."""
    by = [by] if isinstance(by, str) else list(by)
    if not by:
        raise ValueError('At least one grouping column is required.')
    _columns(frame, by + ['Sales', 'Profit'])
    result = frame.groupby(by, dropna=False, observed=True).agg(
        Total_Sales=('Sales', 'sum'), Total_Profit=('Profit', 'sum'), Sales_Count=('Profit', 'size'))
    result['Profit_Margin'] = _margin(result['Total_Profit'], result['Total_Sales'])
    return result

def discount_profile(frame):
    """Unweighted means and distribution summaries by observed row-profit status."""
    import numpy as np
    _columns(frame, ['Profit', 'Discount'])
    groups = frame.assign(Profit_Status=np.select(
        [frame['Profit'].lt(0), frame['Profit'].gt(0)], ['Loss', 'Positive profit'], default='Break-even'))
    result = groups.groupby('Profit_Status', observed=True).agg(
        Order_Lines=('Profit', 'size'), Total_Profit=('Profit', 'sum'),
        Average_Discount=('Discount', 'mean'), Median_Discount=('Discount', 'median'),
        Q1_Discount=('Discount', lambda s: s.quantile(.25)), Q3_Discount=('Discount', lambda s: s.quantile(.75)))
    for column in ('Average_Discount', 'Median_Discount', 'Q1_Discount', 'Q3_Discount'):
        result[column] *= 100
    return result

PRODUCT_KEYS = ['Category', 'Sub-Category', 'Product ID', 'Product Name']

def product_gross_losses(frame):
    """Positive magnitudes from negative lines only, ranked by composite product key."""
    _columns(frame, PRODUCT_KEYS + ['Profit'])
    losses = -frame.loc[frame['Profit'].lt(0)].groupby(PRODUCT_KEYS, dropna=False, observed=True)['Profit'].sum()
    # Stable tie order makes the selected top-N groups reproducible.
    return losses.sort_index().sort_values(ascending=False, kind='stable').rename('Gross_Loss')

def loss_concentration(frame, limits=(10, 20, 50)):
    import pandas as pd
    if not limits or any(not isinstance(n, int) or isinstance(n, bool) or n <= 0 for n in limits):
        raise ValueError('Top-N limits must be positive integers.')
    losses = product_gross_losses(frame)
    total = float(losses.sum())
    rows = [{'Scope': f'Top {n} product groups', 'Groups_Included': min(n, len(losses)),
             'Gross_Loss': float(losses.head(n).sum()),
             'Loss_Share': float(losses.head(n).sum()) / total * 100 if total else 0.0} for n in limits]
    last = max(limits)
    outside = float(losses.iloc[last:].sum())
    rows += [{'Scope': f'Outside top {last}', 'Groups_Included': max(0, len(losses)-last),
              'Gross_Loss': outside, 'Loss_Share': outside / total * 100 if total else 0.0},
             {'Scope': 'All loss-making lines', 'Groups_Included': len(losses), 'Gross_Loss': total,
              'Loss_Share': 100.0 if total else 0.0}]
    return pd.DataFrame(rows).set_index('Scope')

def category_region_losses(frame):
    _columns(frame, ['Category', 'Region', 'Sales', 'Profit'])
    result = frame.assign(Gross_Loss=(-frame['Profit']).clip(lower=0)).groupby(
        ['Category', 'Region'], dropna=False, observed=True).agg(
            Total_Sales=('Sales', 'sum'), Net_Profit=('Profit', 'sum'), Gross_Loss=('Gross_Loss', 'sum'))
    total = result['Gross_Loss'].sum()
    result['Loss_Share'] = result['Gross_Loss'] / total * 100 if total else 0.0
    return result.sort_values('Gross_Loss', ascending=False, kind='stable')

def time_performance(frame, frequency='M'):
    """Observed periods only. Missing months are not silently interpreted as zero sales."""
    if frequency not in ('M', 'Y'):
        raise ValueError('Frequency must be M or Y.')
    _columns(frame, ['Order Date', 'Sales', 'Profit'])
    result = frame.groupby(frame['Order Date'].dt.to_period(frequency)).agg(
        Total_Sales=('Sales', 'sum'), Net_Profit=('Profit', 'sum'))
    result['Profit_Margin'] = _margin(result['Net_Profit'], result['Total_Sales'])
    result.index.name = 'Order year' if frequency == 'Y' else 'Order month (YYYY-MM)'
    return result

def calendar_month_performance(frame):
    """Statistics across observed year-month totals, not across individual order lines."""
    import calendar
    monthly = time_performance(frame)
    result = monthly.assign(Month_Number=monthly.index.month).groupby('Month_Number').agg(
        Years_Observed=('Total_Sales', 'size'), Mean_Monthly_Sales=('Total_Sales', 'mean'),
        Min_Monthly_Sales=('Total_Sales', 'min'), Max_Monthly_Sales=('Total_Sales', 'max'))
    result.index = result.index.map(lambda month: calendar.month_name[month])
    result.index.name = 'Calendar month'
    return result

def loss_pool_overlap(frame, top_n=50):
    """Overlap, not added totals: top product groups inside each category-region pool."""
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n <= 0:
        raise ValueError('top_n must be a positive integer.')
    _columns(frame, PRODUCT_KEYS + ['Region', 'Profit'])
    leaders = product_gross_losses(frame).head(top_n).index
    negative = frame.loc[frame['Profit'].lt(0)].copy()
    included = negative.set_index(PRODUCT_KEYS).index.isin(leaders)
    negative['Gross_Loss'] = -negative['Profit']
    negative['Top_Product_Loss'] = negative['Gross_Loss'].where(included, 0)
    result = negative.groupby(['Category', 'Region'], dropna=False, observed=True).agg(
        Gross_Loss=('Gross_Loss', 'sum'), Top_Product_Loss=('Top_Product_Loss', 'sum'))
    result['Within_Pool_Share'] = _margin(result['Top_Product_Loss'], result['Gross_Loss'])
    return result.sort_values('Gross_Loss', ascending=False, kind='stable')

def loss_pool_products(frame, pools=2, top_per_pool=5):
    """Highest gross-loss product groups within the largest category-region loss pools."""
    import pandas as pd
    if pools <= 0 or top_per_pool <= 0:
        raise ValueError('Pool and product counts must be positive.')
    leaders = category_region_losses(frame).head(pools).index
    pieces = []
    for category, region in leaders:
        subset = frame.loc[frame['Category'].eq(category) & frame['Region'].eq(region)]
        detail = product_gross_losses(subset).head(top_per_pool).reset_index()
        detail['Region'] = region
        pieces.append(detail)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=PRODUCT_KEYS + ['Gross_Loss', 'Region'])

def margin_bridge(frame, by='Category', start=2016, end=2017):
    """Exact symmetric decomposition in percentage points; descriptive, not causal.

    For each segment: change(weight*margin) = change(weight)*mean(margin)
    + mean(weight)*change(margin). Both years need positive sales in every segment.
    """
    import pandas as pd
    if start == end:
        raise ValueError('Two distinct years are required.')
    _columns(frame, [by, 'Order Date', 'Sales', 'Profit'])
    yearly = {}
    for year in (start, end):
        subset = frame.loc[frame['Order Date'].dt.year.eq(year)]
        if subset.empty:
            raise ValueError(f'No observations for year {year}.')
        yearly[year] = segment_performance(subset, by)
    a, b = yearly[start], yearly[end]
    if set(a.index) != set(b.index) or a['Total_Sales'].le(0).any() or b['Total_Sales'].le(0).any():
        raise ValueError('Margin bridge requires matching segments with positive sales in both years.')
    a, b = a.sort_index(), b.sort_index()
    w0, w1 = a['Total_Sales']/a['Total_Sales'].sum(), b['Total_Sales']/b['Total_Sales'].sum()
    m0, m1 = a['Profit_Margin'], b['Profit_Margin']
    return pd.DataFrame({'Start_Sales_Share': w0*100, 'End_Sales_Share': w1*100,
                         'Start_Margin': m0, 'End_Margin': m1,
                         'Mix_Contribution_pp': (w1-w0)*(m0+m1)/2,
                         'Within_Contribution_pp': (w0+w1)/2*(m1-m0)})
