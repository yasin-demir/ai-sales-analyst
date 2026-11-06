"""Deterministic analytical questions over normalized, validated sales records.

No natural-language guessing or model is involved. Results carry explicit scope,
filters, units, denominators, missing-field status, and cautions for future APIs.
"""
from dataclasses import dataclass, field, asdict
import re
import numpy as np
import pandas as pd

DIMENSIONS=('category','region','product_id','product','order_id','currency')
QUESTION_CATALOG={
 'summary':{'question':'What are sales, profit, margin, and gross loss in this scope?','required':['sales']},
 'segment_performance':{'question':'Which segments lead sales or profit, and which have low margins or high gross losses?','required':['sales']},
 'period_comparison':{'question':'How did sales, profit, and margin change between two explicit periods?','required':['sales','date']},
 'monthly_trend':{'question':'How do sales, profit, and gross loss vary across observed months?','required':['sales','date']},
 'loss_concentration':{'question':'Which product groups contribute most to gross loss?','required':['sales','profit']},
 'discount_comparison':{'question':'How do discount distributions differ by observed profit status?','required':['sales','discount','profit']},
}

def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('Date bounds must use YYYY-MM-DD, without a time or timezone.')
    try:return pd.Timestamp(value)
    except ValueError as exc:raise ValueError('Invalid calendar date.') from exc

@dataclass
class AnalysisFilters:
    start: str | None = None
    end: str | None = None
    segments: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.start is not None and not isinstance(self.start,str):raise ValueError('Invalid start date.')
        if self.end is not None and not isinstance(self.end,str):raise ValueError('Invalid end date.')
        if self.start:_date(self.start)
        if self.end:_date(self.end)
        if self.start is not None and not self.start:raise ValueError('Empty start date.')
        if self.end is not None and not self.end:raise ValueError('Empty end date.')
        if self.start and self.end and _date(self.start)>_date(self.end):raise ValueError('Start date must not exceed end date.')
        if not isinstance(self.segments,dict) or set(self.segments)-set(DIMENSIONS):
            raise ValueError('Unsupported segment filter; only explicit dimension fields are allowed.')
        self.segments={key:([values] if isinstance(values,str) else values) for key,values in self.segments.items()}
        if any(not isinstance(v,list) or not v or any(not isinstance(x,str) or not x.strip() for x in v) for v in self.segments.values()):
            raise ValueError('Segment values must be nonempty strings or nonempty lists of strings.')

    @classmethod
    def from_dict(cls,value=None):
        if value is None:return cls()
        if isinstance(value,cls):return cls(**asdict(value))
        if not isinstance(value,dict) or set(value)-{'start','end','segments'}:raise ValueError('Invalid filter specification.')
        return cls(**value)

def apply_filters(frame,filters=None):
    """Inclusive calendar-day bounds; exact segment membership; never mutate input."""
    filters=AnalysisFilters.from_dict(filters)
    required=list(filters.segments)+(['date'] if filters.start or filters.end else [])
    absent=set(required)-set(frame.columns)
    if absent:raise ValueError(f'Filter fields are unavailable: {sorted(absent)}')
    selected=pd.Series(True,index=frame.index)
    if filters.start or filters.end:
        if not pd.api.types.is_datetime64_any_dtype(frame.date):raise ValueError('Date filters require normalized datetime values.')
        if frame.date.isna().any():raise ValueError('Date filters require valid nonmissing dates.')
        if frame.date.dt.tz is not None:raise ValueError('A business timezone must be normalized before calendar-day filtering.')
        dates=frame.date.dt.normalize()
        if filters.start:selected &= dates.ge(_date(filters.start))
        if filters.end:selected &= dates.le(_date(filters.end))
    for key,values in filters.segments.items():selected &= frame[key].isin(values)
    return frame.loc[selected].copy()

def _finite(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None

def _records(frame):
    import json
    return json.loads(frame.to_json(orient='records',date_format='iso'))

def _require(frame,columns):
    problems=[]
    for column in columns:
        if column not in frame:problems.append(f'Missing field: {column}')
        elif frame[column].isna().any():problems.append(f'Missing values in required field: {column}')
        elif column in ('sales','profit','discount','quantity') and (not pd.api.types.is_numeric_dtype(frame[column]) or not np.isfinite(frame[column]).all()):
            problems.append(f'Field must contain normalized finite numbers: {column}')
        elif column=='date' and not pd.api.types.is_datetime64_any_dtype(frame[column]):
            problems.append('Date field must contain normalized datetimes.')
        elif column=='date' and frame[column].dt.tz is not None:
            problems.append('A business timezone must be normalized before calendar-day analysis.')
    return problems

def summary_metrics(frame,blocked=()):
    usable={c for c in frame if c not in blocked and not frame[c].isna().any()}
    sales=frame.sales.sum()
    result={'records':len(frame),'sales':_finite(sales)}
    if 'profit' in usable:
        profit=frame.profit.sum()
        result.update(profit=_finite(profit),gross_loss=_finite(-frame.loc[frame.profit.lt(0),'profit'].sum()),
                      positive_profit=_finite(frame.loc[frame.profit.gt(0),'profit'].sum()),
                      loss_records=int(frame.profit.lt(0).sum()),
                      loss_record_rate_percent=float(frame.profit.lt(0).mean()*100) if len(frame) else None,
                      margin_percent=_finite(profit/sales*100) if sales>0 else None)
    if 'order_id' in usable:
        count=int(frame.order_id.nunique());result['unique_order_ids']=count
        result['average_order_value']=_finite(sales/count) if count else None
    return result

def segment_table(frame,by):
    """Full-scope metrics; displayed ranking limits never change share denominators."""
    if not isinstance(by,(str,list,tuple)):raise ValueError('Grouping must be a dimension or a list of dimensions.')
    keys=[by] if isinstance(by,str) else list(by)
    if not keys or any(not isinstance(key,str) or key not in DIMENSIONS for key in keys) or len(set(keys))!=len(keys):
        raise ValueError('Unsupported or duplicate grouping dimensions.')
    required=keys+['sales']
    problems=_require(frame,required)
    if problems:raise ValueError('; '.join(problems))
    work=frame.copy()
    grouped=work.groupby(keys,dropna=False,observed=True)
    result=grouped.agg(sales=('sales','sum'),records=('sales','size'))
    sales=work.sales.sum()
    result['sales_share_percent']=result.sales/sales*100 if sales>0 else np.nan
    if 'profit' in work:
        problems=_require(work,['profit'])
        if problems:raise ValueError('; '.join(problems))
        work['gross_loss']=(-work.profit).clip(lower=0);work['positive_profit']=work.profit.clip(lower=0)
        work['loss_record']=work.profit.lt(0).astype(int)
        grouped=work.groupby(keys,dropna=False,observed=True)
        for name in ['profit','gross_loss','positive_profit','loss_record']:result[name]=grouped[name].sum()
        result=result.rename(columns={'loss_record':'loss_records'})
        result['margin_percent']=result.profit.div(result.sales.where(result.sales.gt(0))).mul(100)
        result['loss_record_rate_percent']=result.loss_records/result.records*100
        total_loss=work.gross_loss.sum()
        result['gross_loss_share_percent']=result.gross_loss/total_loss*100 if total_loss else 0.0
    return result.reset_index()

def _scope(frame,selected,filters):
    def observed(df):
        if 'date' not in df or df.empty or not pd.api.types.is_datetime64_any_dtype(df.date):return {'start':None,'end':None}
        return {'start':str(df.date.min().date()),'end':str(df.date.max().date())}
    return {'input_records':len(frame),'selected_records':len(selected),
            'input_observed_dates':observed(frame),'selected_observed_dates':observed(selected),
            'requested_dates':{'start':filters.start,'end':filters.end}}

def _envelope(analysis,frame,selected,filters,unit,status='ok'):
    return {'schema_version':1,'analysis':analysis,'status':status,'scope':_scope(frame,selected,filters),
            'filters':asdict(filters),'units':{'amount':unit,'rates':'percent','margin_change':'percentage_points','counts':'records'},
            'metrics':{},'records':[],'warnings':[],
            'definitions':{'margin_percent':'sum(profit) / sum(sales) * 100; undefined for nonpositive sales',
                           'gross_loss':'magnitude of negative row profit before positive lines offset losses',
                           'share_denominator':'all selected records in this currency partition, before ranking/min-record display restrictions'}}

def _period_bounds(spec):
    if not isinstance(spec,dict) or set(spec)!={'baseline_start','baseline_end','current_start','current_end'}:
        raise ValueError('Comparison requires exactly baseline_start, baseline_end, current_start, current_end.')
    a,b,c,d=(_date(spec[key]) for key in ('baseline_start','baseline_end','current_start','current_end'))
    if a>b or c>d:raise ValueError('Each period must have start <= end.')
    if b>=c:raise ValueError('Baseline must end before the current period starts; periods may not overlap.')
    return a,b,c,d

def validate_comparison(spec):
    _period_bounds(spec)
    return dict(spec)

def previous_period_bounds(period,frequency='M',mode='previous'):
    """Explicit month/year labels; no invented current date or implicit MTD comparison."""
    if frequency not in ('M','Y') or mode not in ('previous','year_over_year'):
        raise ValueError('Frequency must be M/Y; mode must be previous/year_over_year.')
    pattern=r'\d{4}-\d{2}' if frequency=='M' else r'\d{4}'
    if not isinstance(period,str) or not re.fullmatch(pattern,period):raise ValueError('Period label must be YYYY-MM for M or YYYY for Y.')
    try:current=pd.Period(period,freq=frequency)
    except ValueError as exc:raise ValueError('Invalid period label.') from exc
    offset=12 if frequency=='M' and mode=='year_over_year' else 1
    baseline=current-offset
    return {'baseline_start':str(baseline.start_time.date()),'baseline_end':str(baseline.end_time.date()),
            'current_start':str(current.start_time.date()),'current_end':str(current.end_time.date())}

def _comparison(frame,envelope,comparison):
    a,b,c,d=_period_bounds(comparison)
    dates=frame.date.dt.normalize()
    baseline=frame.loc[dates.between(a,b)];current=frame.loc[dates.between(c,d)]
    envelope['periods']={'baseline':{'start':str(a.date()),'end':str(b.date()),'calendar_days':(b-a).days+1,'records':len(baseline)},
                         'current':{'start':str(c.date()),'end':str(d.date()),'calendar_days':(d-c).days+1,'records':len(current)}}
    envelope['scope']['comparison_window_records']=len(baseline)+len(current)
    if (b-a).days!=(d-c).days:envelope['warnings'].append('Period lengths differ; raw totals do not adjust for days, working days, or exposure.')
    for name,part,start,end in [('baseline',baseline,a,b),('current',current,c,d)]:
        if part.empty:envelope['warnings'].append(f'{name} has no observed records; absence is not assumed to be zero activity.')
        elif part.date.min().normalize()>start or part.date.max().normalize()<end:
            envelope['warnings'].append(f'{name} observations do not reach both requested boundaries; full-period coverage cannot be established from transaction dates.')
    if baseline.empty or current.empty:
        envelope['status']='insufficient_data';return envelope
    metrics_a,metrics_b=summary_metrics(baseline),summary_metrics(current)
    if 'margin_percent' in metrics_a and (metrics_a['margin_percent'] is None or metrics_b['margin_percent'] is None):
        envelope['warnings'].append('Margin change is undefined because at least one period has nonpositive net sales.')
    for key in ['sales','profit','gross_loss','records','unique_order_ids','average_order_value','margin_percent']:
        if key not in metrics_a or key not in metrics_b:continue
        old,new=metrics_a[key],metrics_b[key]
        delta=new-old if old is not None and new is not None else None
        if key=='margin_percent':
            change=None;pp=delta;reason='Margin changes are expressed in percentage points, not relative percent.'
        else:
            change=delta/old*100 if delta is not None and old>0 else None;pp=None
            reason=None if change is not None else 'Relative change is undefined when the baseline is zero, negative, or missing.'
        envelope['records'].append({'metric':key,'baseline':old,'current':new,'absolute_change':_finite(delta) if delta is not None else None,
                                    'relative_change_percent':_finite(change) if change is not None else None,
                                    'margin_change_pp':_finite(pp) if pp is not None else None,'relative_change_note':reason})
    envelope['metrics']={'baseline':metrics_a,'current':metrics_b}
    envelope['warnings'].append('Comparisons are descriptive; timing, product mix, pricing, and costs may differ. Transaction dates do not prove source completeness.')
    return envelope

@dataclass(frozen=True)
class _HandlerOptions:
    """Parameters already validated by the shared question entry point."""
    keys: list[str]
    sort_by: str | None
    ascending: bool | None
    limit: int
    min_records: int
    comparison: dict | None


def _handle_summary(selected,entry,options):
    """Compute summary metrics for one nonempty, currency-separated selection."""
    entry['metrics']=summary_metrics(selected)
    if 'profit' not in selected:entry['warnings'].append('Profit is unavailable; no profit or loss is inferred.')
    if selected.sales.sum()<=0:entry['warnings'].append('Net sales are nonpositive; margin and revenue shares are undefined.')
    return entry


def _handle_segment_performance(selected,entry,options):
    """Rank segments without changing the full-selection denominators."""
    keys=options.keys
    table=segment_table(selected,keys)
    metric=options.sort_by or 'sales'
    metrics=[c for c in table if c not in keys]
    if metric not in metrics:raise ValueError(f'Sort metric unavailable. Available: {metrics}')
    eligible=table.loc[table.records.ge(options.min_records)]
    asc=options.ascending if options.ascending is not None else metric=='margin_percent'
    if eligible[metric].isna().any():
        entry['warnings'].append(f'Groups with undefined {metric} are not ranked; full-scope denominators remain unchanged.')
        eligible=eligible.loc[eligible[metric].notna()]
    ranked=eligible.sort_values(keys).sort_values(metric,ascending=asc,kind='stable',na_position='last').head(options.limit)
    entry['records']=_records(ranked)
    entry['ranking']={'by':keys,'sort_by':metric,'ascending':asc,'limit':options.limit,'min_records':options.min_records,
                      'total_groups':len(table),'eligible_groups':len(eligible),'returned_groups':len(ranked)}
    entry['metrics']=summary_metrics(selected)
    if len(eligible)<len(table):entry['warnings'].append('Some groups are hidden by min_records; share denominators still include the entire selected scope.')
    if ranked.empty:entry['status']='empty_ranking';entry['warnings'].append('No groups meet the minimum record count.')
    entry['warnings'].append('Margins aggregate profit and sales; low-volume groups may have unstable rates. Gross loss and net profit answer different questions.')
    return entry


def _handle_period_comparison(selected,entry,options):
    """Use the existing period calculations and coverage warnings unchanged."""
    return _comparison(selected,entry,options.comparison)


def _handle_monthly_trend(selected,entry,options):
    """Aggregate observed calendar months; retain missing-month warnings."""
    work=selected.assign(month=selected.date.dt.to_period('M').astype(str))
    rows=[]
    for month,group in work.groupby('month',sort=True):rows.append({'month':month,**summary_metrics(group)})
    entry['records']=rows;entry['metrics']=summary_metrics(selected)
    periods=pd.PeriodIndex([r['month'] for r in rows],freq='M')
    missing=len(pd.period_range(periods.min(),periods.max(),freq='M'))-len(periods)
    if missing:entry['warnings'].append(f'{missing} calendar months have no observed records and are not filled with zero values.')
    entry['warnings'].append('Observed monthly aggregates are descriptive and are not a validated seasonal forecast.')
    return entry


def _handle_loss_concentration(selected,entry,options):
    """Rank product gross losses using the existing ID/name key policy."""
    product_keys=[c for c in ('product_id','product') if c in selected and not selected[c].isna().any()]
    if not product_keys:
        entry['status']='unavailable';entry['warnings'].append('No complete product key in the selected scope.');return entry
    negative=selected.loc[selected.profit.lt(0)]
    table=(-negative.groupby(product_keys,dropna=False,observed=True).profit.sum()).rename('gross_loss').reset_index()
    table=table.sort_values(product_keys).sort_values('gross_loss',ascending=False,kind='stable')
    total=float(table.gross_loss.sum());top=table.head(options.limit).copy()
    top['share_percent']=top.gross_loss/total*100 if total else 0.0
    top['cumulative_share_percent']=top.share_percent.cumsum()
    entry['records']=_records(top)
    entry['metrics']={'total_gross_loss':total,'selected_product_groups':len(table),'top_groups_returned':len(top),
                      'top_gross_loss':float(top.gross_loss.sum()),'top_share_percent':float(top.gross_loss.sum()/total*100) if total else 0.0,
                      'outside_top_gross_loss':float(table.iloc[options.limit:].gross_loss.sum())}
    if total==0:entry['warnings'].append('No negative-profit records exist in this scope; gross loss is zero.')
    entry['warnings'].append('Product ID/name groups remain distinct. Gross losses are not necessarily net product losses or avoidable savings.')
    return entry


def _handle_discount_comparison(selected,entry,options):
    """Compare the existing unweighted discount distributions by profit status."""
    work=selected.assign(profit_status=np.select([selected.profit.lt(0),selected.profit.gt(0)],['Loss','Positive profit'],default='Break-even'))
    table=work.groupby('profit_status',observed=True).agg(records=('sales','size'),mean_discount_percent=('discount','mean'),median_discount_percent=('discount','median'),
        q1_discount_percent=('discount',lambda s:s.quantile(.25)),q3_discount_percent=('discount',lambda s:s.quantile(.75)))
    table.loc[:,[c for c in table if c.endswith('_percent')]]*=100
    entry['records']=_records(table.reset_index());entry['warnings'].append('Unweighted record distributions; profit-status differences do not establish a causal effect of discounts.')
    return entry


_ANALYSIS_HANDLERS={
    'summary':_handle_summary,
    'segment_performance':_handle_segment_performance,
    'period_comparison':_handle_period_comparison,
    'monthly_trend':_handle_monthly_trend,
    'loss_concentration':_handle_loss_concentration,
    'discount_comparison':_handle_discount_comparison,
}


def run_question(frame,analysis,*,filters=None,unit=None,by=None,sort_by=None,ascending=None,limit=10,min_records=1,comparison=None):
    """Return currency-separated, JSON-safe answers for an explicit supported intent."""
    if not isinstance(analysis,str) or analysis not in QUESTION_CATALOG:raise ValueError(f'Unsupported analysis. Available: {list(QUESTION_CATALOG)}')
    if sort_by is not None and not isinstance(sort_by,str):raise ValueError('sort_by must be a supported metric name or null.')
    filters=AnalysisFilters.from_dict(filters)
    if unit is not None and (not isinstance(unit,str) or not unit.strip()):raise ValueError('unit must be a nonempty label or null.')
    if analysis!='segment_performance' and any(value is not None for value in (by,sort_by,ascending)):
        raise ValueError('Grouping and ranking parameters apply only to segment_performance.')
    if analysis!='period_comparison' and comparison is not None:raise ValueError('Comparison windows apply only to period_comparison.')
    if not isinstance(limit,int) or isinstance(limit,bool) or limit<1:raise ValueError('limit must be a positive integer.')
    if not isinstance(min_records,int) or isinstance(min_records,bool) or min_records<1:raise ValueError('min_records must be a positive integer.')
    if ascending is not None and not isinstance(ascending,bool):raise ValueError('ascending must be true, false, or null.')
    keys=[by] if isinstance(by,str) else list(by) if isinstance(by,(list,tuple)) else []
    if analysis=='segment_performance' and (not keys or any(not isinstance(k,str) or k not in DIMENSIONS or k=='currency' for k in keys) or len(set(keys))!=len(keys)):
        raise ValueError('An explicit supported segment grouping is required; currencies are partitioned automatically.')
    if analysis=='period_comparison':
        _period_bounds(comparison)
        if filters.start or filters.end:raise ValueError('Period comparison accepts segment filters, not an additional date filter that could truncate its windows.')
    known_metrics={'sales','records','sales_share_percent','profit','gross_loss','positive_profit','loss_records','margin_percent','loss_record_rate_percent','gross_loss_share_percent'}
    if analysis=='segment_performance' and sort_by is not None and sort_by not in known_metrics:raise ValueError('Unsupported ranking metric.')
    requested=QUESTION_CATALOG[analysis]['required']+list(filters.segments)+(['date'] if filters.start or filters.end else [])+keys
    if analysis=='segment_performance' and sort_by in known_metrics-{'sales','records','sales_share_percent'}:requested += ['profit']
    product_keys=[c for c in ('product_id','product') if c in frame]
    if analysis in ('summary','segment_performance','monthly_trend','period_comparison'):
        requested += [c for c in ('profit',) if c in frame]
    if 'date' in frame:requested += ['date']
    problems=_require(frame,[c for c in requested if c not in DIMENSIONS])
    problems += [f'Missing field: {c}' for c in requested if c in DIMENSIONS and c not in frame]
    if analysis=='loss_concentration' and not product_keys:problems.append('A nonmissing product ID or product label is required.')
    if 'currency' in frame and frame.currency.isna().any():problems.append('Currency labels must not be missing.')
    if unit and 'currency' in frame and not frame.currency.eq(unit).all():raise ValueError('Explicit unit conflicts with source currency labels.')
    result={'schema_version':1,'analysis':analysis,'question':QUESTION_CATALOG[analysis]['question'],'status':'ok','results':[]}
    if problems:
        entry=_envelope(analysis,frame,frame.iloc[:0],filters,unit or 'source units','unavailable')
        entry['warnings']=problems;result.update(status='unavailable',results=[entry]);return result
    parts=list(frame.groupby('currency',sort=True,dropna=False)) if 'currency' in frame else [(unit or 'source units (currency unspecified)',frame)]
    if not parts:parts=[(unit or 'source units (currency unspecified)',frame)]
    options=_HandlerOptions(keys,sort_by,ascending,limit,min_records,comparison)
    handler=_ANALYSIS_HANDLERS[analysis]
    for currency,part in parts:
        selected=apply_filters(part,filters)
        entry=_envelope(analysis,part,selected,filters,str(currency),'empty' if selected.empty else 'ok')
        if 'currency' not in frame and not unit:entry['warnings'].append('Currency is unspecified; amounts retain source units.')
        selected_problems=_require(selected,keys) if not selected.empty else []
        if selected_problems:
            entry['status']='unavailable';entry['warnings'].extend(selected_problems);result['results'].append(entry);continue
        if 'order_id' in selected and selected.order_id.isna().any():entry['warnings'].append('Incomplete order identifiers; order count and average order value are omitted.')
        if 'order_id' in selected and (filters.start or filters.end or filters.segments):
            entry['warnings'].append('Order counts and average order value describe the selected records; filters may include only part of an order.')
        if selected.empty:
            entry['warnings'].append('No records match the requested scope. Empty selection is not a zero-sales observation.')
            result['results'].append(entry);continue
        entry=handler(selected,entry,options)
        result['results'].append(entry)
    statuses={r['status'] for r in result['results']}
    result['status']='ok' if statuses=={'ok'} else ('partial' if 'ok' in statuses else next(iter(statuses)) if len(statuses)==1 else 'insufficient_data')
    return result

def summarize_sales(frame,**kwargs):return run_question(frame,'summary',**kwargs)
def rank_segments(frame,by,**kwargs):return run_question(frame,'segment_performance',by=by,**kwargs)
def compare_periods(frame,comparison,**kwargs):return run_question(frame,'period_comparison',comparison=comparison,**kwargs)
def compare_previous_period(frame,period,frequency='M',mode='previous',**kwargs):
    return compare_periods(frame,previous_period_bounds(period,frequency,mode),**kwargs)
def monthly_trend(frame,**kwargs):return run_question(frame,'monthly_trend',**kwargs)
def rank_product_losses(frame,**kwargs):return run_question(frame,'loss_concentration',**kwargs)
def compare_discounts(frame,**kwargs):return run_question(frame,'discount_comparison',**kwargs)
