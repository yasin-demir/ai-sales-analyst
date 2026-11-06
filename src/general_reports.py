"""Capability-driven, local sales reports. No inferred profit or currency conversion."""
from pathlib import Path
from dataclasses import asdict
import base64
import hashlib
import html
import json
import numpy as np
import pandas as pd
from .importing import ImportConfig, read_input, prepare, capabilities
from .questions import AnalysisFilters, apply_filters, summary_metrics, segment_table, run_question, validate_comparison

def records(frame):
    return json.loads(frame.to_json(orient='records',date_format='iso'))

def _finite(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None

def _groups(frame,keys):
    # Calendar grouping is a presentation helper; segment metrics share the query engine.
    if all(key in ('category','region','product_id','product') for key in keys):
        return segment_table(frame,keys).sort_values('sales',ascending=False,kind='stable')
    result=frame.groupby(keys,dropna=False,observed=True).agg(sales=('sales','sum'),records=('sales','size'))
    if 'profit' in frame:
        result['profit']=frame.groupby(keys,dropna=False,observed=True).profit.sum()
        result['margin_percent']=result.profit.div(result.sales.where(result.sales.gt(0))).mul(100)
    return result.reset_index().sort_values('sales',ascending=False,kind='stable')

def analyze_partition(frame,capabilities,blocked,unit):
    frame=frame.copy()
    usable=set(frame.columns)-set(blocked)
    product_keys=[c for c in ('product_id','product') if c in usable]
    metrics={**summary_metrics(frame,blocked),'unit':unit}
    tables={};notes=[]
    notes.append(f'All amounts in this section use {unit}. No currency conversion or profit derivation was performed.')
    if metrics['sales'] is not None and metrics['sales']<=0:
        notes.append('Net sales are zero or negative; profit margins are undefined and are not presented as percentages.')
    if 'order_id' not in usable:notes.append('Order identifiers are unavailable; row count is not described as order count and average order value is not calculated.')
    if 'profit' not in usable:notes.append('Profit is unavailable. Profit margins and loss claims cannot be calculated from sales alone.')
    for name,column in [('category_sales','category'),('region_sales','region')]:
        if capabilities[name]['available']:tables[name]=records(_groups(frame,[column]))
    if capabilities['product_sales']['available']:
        tables['product_sales']=records(_groups(frame,product_keys))
        notes.append('Products use the explicitly mapped ID/name composite when both fields exist; labels are not automatically merged.')
    if capabilities['time_sales']['available']:
        frame['month']=frame.date.dt.to_period('M').astype(str)
        monthly=_groups(frame,['month']).sort_values('month')
        tables['monthly_sales']=records(monthly)
        counts=monthly.assign(calendar_month=pd.to_datetime(monthly.month).dt.month).groupby('calendar_month').agg(
            years_observed=('sales','size'),mean_sales=('sales','mean'),min_sales=('sales','min'),max_sales=('sales','max')).reset_index()
        tables['calendar_month_sales']=records(counts)
        notes.append('Time summaries use observed year-month totals. Calendar-month means are not average transaction values; unobserved months remain absent.')
    if capabilities['product_losses']['available']:
        negative=frame.loc[frame.profit.lt(0)]
        loss=(-negative.groupby(product_keys,dropna=False,observed=True).profit.sum()).rename('gross_loss').reset_index()
        loss=loss.sort_values(product_keys).sort_values('gross_loss',ascending=False,kind='stable')
        total=loss.gross_loss.sum()
        loss['share_percent']=loss.gross_loss/total*100 if total else 0.0
        loss['cumulative_share_percent']=loss.share_percent.cumsum()
        tables['product_losses']=records(loss)
        notes.append('Gross losses use only negative-profit lines. Cumulative product shares are nested and are not additive savings opportunities.')
    if capabilities['discount_comparison']['available']:
        frame['profit_status']=np.select([frame.profit.lt(0),frame.profit.gt(0)],['Loss','Positive profit'],default='Break-even')
        grouped=frame.groupby('profit_status',observed=True).agg(records=('profit','size'),
            mean_discount=('discount','mean'),median_discount=('discount','median'),
            q1_discount=('discount',lambda s:s.quantile(.25)),q3_discount=('discount',lambda s:s.quantile(.75)))
        grouped.loc[:,['mean_discount','median_discount','q1_discount','q3_discount']]*=100
        tables['discount_comparison']=records(grouped.reset_index())
        notes.append('Discount statistics are unweighted record percentages. Group differences do not establish causation or the impact of changing discounts.')
    question_results={}
    question_results['summary']=run_question(frame.drop(columns=list(blocked),errors='ignore'),'summary',unit=unit)
    for label,column in [('category','category'),('region','region')]:
        if column in usable:
            answer=run_question(frame.drop(columns=list(blocked),errors='ignore'),'segment_performance',by=column,
                                sort_by='gross_loss' if 'profit' in usable else 'sales',limit=max(1,frame[column].nunique()),unit=unit)
            question_results[label+'_performance']=answer
            if 'profit' in usable:tables[label+'_profitability']=answer['results'][0]['records']
    if product_keys and 'profit' in usable:
        answer=run_question(frame.drop(columns=list(blocked),errors='ignore'),'segment_performance',by=product_keys,sort_by='gross_loss',limit=max(1,frame[product_keys].drop_duplicates().shape[0]),unit=unit)
        question_results['product_performance']=answer
        tables['product_profitability']=answer['results'][0]['records']
    findings=[]
    for name,label in [('category_sales','category'),('region_sales','region')]:
        if name in tables:
            top=tables[name][0]
            findings.append(f'Highest net sales by {label}: {top[label]} ({top["sales"]:,.2f} {unit}). Ranking describes revenue size, not automatically profitability.')
    if 'monthly_sales' in tables:
        peak=max(tables['monthly_sales'],key=lambda r:r['sales'])
        findings.append(f'Highest observed year-month sales: {peak["month"]} ({peak["sales"]:,.2f} {unit}). Historical peaks are not a forecast.')
    if tables.get('product_losses'):
        losses=tables['product_losses'];n=min(10,len(losses));share=sum(row['share_percent'] for row in losses[:n])
        findings.append(f'The first {n} highest-loss product groups account for {share:.2f}% of this section’s gross loss. Profitable lines may offset these losses within the same product.')
    return {'unit':unit,'metrics':metrics,'tables':tables,'findings':findings,'notes':notes,'question_results':question_results,'_frame':frame}

def _chart(fig):
    from .plotting import png
    return base64.b64encode(png(fig)).decode('ascii')

def make_charts(partition):
    from .plotting import render_session
    with render_session():
        return _make_charts(partition)


def _make_charts(partition):
    from .plotting import plt, subplots
    from matplotlib.ticker import StrMethodFormatter,PercentFormatter
    charts=[];tables=partition['tables'];unit=partition['unit'];frame=partition['_frame']
    with plt.rc_context({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'text.parse_math':False}):
        for key,label in [('category_sales','category'),('region_sales','region'),('product_sales','product')]:
            if not tables.get(key):continue
            table=pd.DataFrame(tables[key]).head(10).iloc[::-1]
            if label=='product':
                keys=[c for c in ('product_id','product') if c in table]
                names=table[keys].astype(str).agg(' | '.join,axis=1)
            else:names=table[label].astype(str)
            fig,ax=subplots(figsize=(9,max(3,len(table)*.4+1.3)))
            bars=ax.barh(np.arange(len(table)),table.sales,color=np.where(table.sales.lt(0),'#D97706','#167D9A'))
            ax.bar_label(bars,labels=[f'{value:,.2f}' for value in table.sales],padding=5,fontsize=9)
            ax.margins(x=.22)
            ax.set_yticks(np.arange(len(table)),labels=[name[:70] for name in names])
            ax.axvline(0,color='#475569',linewidth=.8);ax.set(xlabel=f'Net sales ({unit})',title=f'Highest-sales {label} groups | up to 10')
            ax.xaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'));ax.set_axisbelow(True);ax.grid(axis='x',alpha=.2)
            fig.tight_layout();charts.append({'title':f'Sales by {label}','image':_chart(fig),
                'explanation':'Bars compare signed sales totals. Only the first 10 ranked groups are displayed; the complete table follows. Negative bars can represent configured returns. Profitability is a separate measure.'})
        for key,label in [('category_profitability','category'),('region_profitability','region')]:
            if not tables.get(key):continue
            table=pd.DataFrame(tables[key]).sort_values('gross_loss',ascending=False,kind='stable').head(10).iloc[::-1]
            y=np.arange(len(table));fig,axes=subplots(1,2,figsize=(12,max(3.5,len(table)*.4+1.5)),sharey=True)
            bars=axes[0].barh(y,table.gross_loss,color='#D97706')
            axes[0].bar_label(bars,labels=[f'{v:,.2f}' for v in table.gross_loss],padding=4,fontsize=9)
            axes[0].set(title='Gross loss exposure',xlabel=f'Gross loss ({unit})');axes[0].margins(x=.25)
            valid=table.margin_percent.notna()
            axes[1].barh(y[valid],table.loc[valid,'margin_percent'],color=np.where(table.loc[valid,'margin_percent'].lt(0),'#D97706','#167D9A'))
            for position,row in enumerate(table.itertuples()):
                if pd.isna(row.margin_percent):axes[1].text(0,position,'Undefined (nonpositive sales)',va='center',fontsize=8)
            axes[1].axvline(0,color='#475569',linewidth=.8);axes[1].set(title='Weighted profit margin',xlabel='Net profit / net sales (%)')
            axes[1].xaxis.set_major_formatter(PercentFormatter(100));axes[0].set_yticks(y,labels=table[label].astype(str))
            for ax in axes:ax.set_axisbelow(True);ax.grid(axis='x',alpha=.2)
            fig.suptitle(f'{label.title()} profitability | up to 10 highest gross-loss groups');fig.tight_layout()
            charts.append({'title':f'Profitability by {label}','image':_chart(fig),'explanation':'The aligned panels separate loss dollars from net margin. A group can remain net profitable while contributing substantial gross loss. Margin is undefined for nonpositive net sales. The complete table includes net profit, counts, and scope-wide gross-loss shares.'})
        if tables.get('monthly_sales'):
            monthly=pd.DataFrame(tables['monthly_sales']).set_index('month')
            # Gaps stay NaN on a complete plotting axis; no line bridges an unobserved month.
            monthly.index=pd.PeriodIndex(monthly.index,freq='M')
            full=monthly.reindex(pd.period_range(monthly.index.min(),monthly.index.max(),freq='M'))
            x=full.index.to_timestamp();fig,ax=subplots(figsize=(10,4.4))
            ax.plot(x,full.sales,marker='o',color='#167D9A',label='Sales')
            if 'profit' in full:ax.plot(x,full.profit,marker='o',color='#D97706',label='Profit')
            step=max(1,int(np.ceil(len(full)/12)))
            ax.set_xticks(x[::step],labels=full.index.astype(str)[::step])
            ax.axhline(0,color='#475569',linewidth=.8);ax.set(ylabel=f'Amount ({unit})',xlabel='Observed order period',title='Monthly sales and available profit')
            ax.legend(frameon=False);ax.grid(axis='y',alpha=.2);fig.autofmt_xdate();fig.tight_layout()
            charts.append({'title':'Monthly performance','image':_chart(fig),'explanation':'Points are observed monthly totals. Missing calendar months create gaps rather than invented zero values. A single observation does not establish a trend; sales and profit use the same amount axis.'})
        if tables.get('product_losses'):
            loss=pd.DataFrame(tables['product_losses']);rank=np.arange(1,len(loss)+1)
            fig,ax=subplots(figsize=(9,4.3));ax.plot(np.r_[0,rank],np.r_[0,loss.cumulative_share_percent],color='#167D9A',linewidth=2)
            ax.set(xlabel='Highest-loss product groups included',ylabel='Share of gross loss (%)',ylim=(0,105),xlim=(0,max(1,len(loss))),title='Product gross-loss concentration | full population')
            ax.yaxis.set_major_formatter(PercentFormatter(100));ax.grid(alpha=.2);fig.tight_layout()
            charts.append({'title':'Loss concentration','image':_chart(fig),'explanation':'The curve covers every product group with negative-profit lines and ends at 100%. Profitable lines are excluded from gross loss. A steep beginning identifies concentrated exposure, not guaranteed savings.'})
        if tables.get('discount_comparison'):
            names=[];groups=[]
            for name in ['Loss','Positive profit','Break-even']:
                values=frame.loc[frame.profit_status.eq(name),'discount'].mul(100)
                if len(values):names.append(f'{name} (n={len(values):,})');groups.append(values)
            fig,ax=subplots(figsize=(9,4.5));ax.boxplot(groups,showmeans=True,positions=np.arange(1,len(groups)+1))
            ax.set_xticks(np.arange(1,len(groups)+1),labels=names);ax.set(ylabel='Discount (%)',title='Discount distribution by observed profit status')
            ax.set_ylim(0,max(5,min(105,float(frame.discount.max()*100)*1.15)))
            ax.yaxis.set_major_formatter(PercentFormatter(100));ax.grid(axis='y',alpha=.2);fig.tight_layout()
            charts.append({'title':'Discount distribution','image':_chart(fig),'explanation':'Boxes show the middle 50%, lines the median, triangles the mean, and whiskers extend within 1.5 interquartile ranges; remaining points are fliers. Constant groups may collapse to lines. Counts describe group size. This observational comparison does not estimate causal effects.'})
    return charts

def build_analysis(path,config,filters=None,comparison=None):
    filters=AnalysisFilters.from_dict(filters)
    if comparison is not None:comparison=validate_comparison(comparison)
    if comparison is not None and (filters.start or filters.end):raise ValueError('Comparison uses its own date windows; additional report date bounds are not allowed.')
    path=Path(path);raw=read_input(path,config);quality=prepare(raw,config)
    frame=quality.pop('frame');blocked=quality['blocked_fields']
    result={'format_version':1,'source':{'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
            'config':asdict(config),'quality':quality,'partitions':[],
            'limitations':['Results depend on the reviewed mapping, units, row grain, and definitions supplied by the user.',
              'Profit is never inferred from sales; its accounting cost coverage is not independently verified.',
              'The source file is not modified. No external service receives the data.',
              'Descriptive patterns do not establish causes, forecasts, or recoverable savings.']}
    if quality['status']!='ready':return result
    original_frame=frame.copy()
    frame=apply_filters(frame,filters)
    blocked=[name for name in blocked if name in frame and frame[name].isna().any()]
    selected_capabilities=capabilities(frame,blocked)
    result['selection']={'status':'empty' if frame.empty else 'ok','validated_input_records':len(original_frame),'selected_records':len(frame),'filters':asdict(filters),'capabilities':selected_capabilities}
    if frame.empty:return result
    if 'currency' in frame:
        iterator=frame.groupby('currency',sort=True,dropna=False)
    else:iterator=[(config.currency or 'source units (currency unspecified)',frame)]
    for unit,subset in iterator:
        part=analyze_partition(subset,selected_capabilities,blocked,str(unit))
        clean_original=original_frame.drop(columns=blocked,errors='ignore')
        if 'currency' in clean_original:clean_original=clean_original.loc[clean_original.currency.eq(unit)]
        part['question_results']['summary']=run_question(clean_original,'summary',filters=filters,unit=str(unit))
        for name,answer in list(part['question_results'].items()):
            if name.endswith('_performance'):
                keys=['product_id','product'] if name=='product_performance' else [name.removesuffix('_performance')]
                keys=[key for key in keys if key in clean_original]
                part['question_results'][name]=run_question(clean_original,'segment_performance',filters=filters,by=keys,sort_by='gross_loss' if 'profit' in clean_original else 'sales',limit=max(1,len(subset)),unit=str(unit))
        if comparison is not None:
            answer=run_question(clean_original,'period_comparison',filters=filters,comparison=comparison,unit=str(unit))
            part['question_results']['period_comparison']=answer
            part['tables']['period_comparison']=answer['results'][0]['records']
            part['notes'].extend(answer['results'][0]['warnings'])
        part['charts']=make_charts(part);part.pop('_frame')
        result['partitions'].append(part)
    return result

def render_html(result):
    e=lambda text:html.escape(str(text))
    chunks=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
      '<title>Sales analysis report</title><style>body{font:16px/1.6 system-ui,sans-serif;color:#172033;background:#f3f6fa;margin:0}main{max-width:1100px;margin:auto;padding:30px}section{background:white;padding:24px;margin:20px 0;border-radius:12px}h1,h2,h3{line-height:1.25}img{max-width:100%;height:auto}table{border-collapse:collapse;font-size:14px;width:100%}td,th{border-bottom:1px solid #dde5ef;padding:8px;text-align:left}.scroll{overflow:auto}.warning{background:#fff5dc;padding:12px}code{overflow-wrap:anywhere}.muted{color:#526176}</style><main>',
      '<h1>Sales analysis report</h1>',f'<p>Source: <strong>{e(result["source"]["file"])}</strong></p>',
      f'<p class="muted">Source fingerprint: <code>{e(result["source"]["sha256"])}</code></p>']
    q=result['quality'];chunks.append(f'<section><h2>Data readiness: {e(q["status"])}</h2><p>Input records: {q["input_rows"]:,}; analyzed: {q["analyzed_rows"]:,}; explicitly excluded: {len(q["excluded_records"]):,}.</p>')
    for issue in q['issues']:
        chunks.append(f'<p class="warning">{e(issue["severity"])} · {e(issue["field"])}'+(f' · data record {issue["data_record"]}' if issue['data_record'] else '')+f': {e(issue["message"])}</p>')
    if q['excluded_records']:chunks.append(f'<p>Excluded logical data-record numbers (header excluded): {e(q["excluded_records"])}</p>')
    rows=[{'analysis':name,'available':item['available'],'missing or invalid':', '.join(item['missing_or_invalid_fields'])} for name,item in q['capabilities'].items()]
    chunks.append(pd.DataFrame(rows).to_html(index=False,escape=True));chunks.append('</section>')
    if 'selection' in result:
        selection=result['selection']
        chunks.append(f'<section><h2>Analysis scope</h2><p>Validated input records: {selection["validated_input_records"]:,}; selected: {selection["selected_records"]:,}; status: {e(selection["status"])}.</p><pre>'+e(json.dumps(selection['filters'],ensure_ascii=False,indent=2))+'</pre></section>')
    if len(result['partitions'])>1:chunks.append('<p class="warning">Multiple currencies are reported separately. There is no combined monetary total or exchange-rate conversion.</p>')
    for part in result['partitions']:
        chunks.append(f'<section><h2>Results — {e(part["unit"])}</h2>')
        chunks.append(pd.DataFrame([part['metrics']]).round(3).to_html(index=False,escape=True,na_rep='Undefined'))
        for note in part['notes']:chunks.append(f'<p class="muted">{e(note)}</p>')
        for item in part['findings']:chunks.append(f'<p><strong>Finding:</strong> {e(item)}</p>')
        for chart in part['charts']:
            chunks.append(f'<h3>{e(chart["title"])}</h3><img alt="{e(chart["title"])}" src="data:image/png;base64,{chart["image"]}"><p>{e(chart["explanation"])}</p>')
        for name,table in part['tables'].items():
            if name=='period_comparison':
                comparison_result=part['question_results']['period_comparison']['results'][0]
                periods=comparison_result.get('periods')
                if periods:
                    a,b=periods['baseline'],periods['current']
                    chunks.append(f'<p><strong>Comparison windows:</strong> baseline {e(a["start"])} to {e(a["end"])} ({a["records"]} records); current {e(b["start"])} to {e(b["end"])} ({b["records"]} records). Margin changes use percentage points.</p>')
            chunks.append(f'<h3>{e(name.replace("_"," ").title())}</h3><div class="scroll">'+pd.DataFrame(table).round(4).to_html(index=False,escape=True,na_rep='Undefined')+'</div>')
        chunks.append('<details><summary>Structured analysis scope, filters, units and warnings</summary><pre>'+e(json.dumps(part.get('question_results',{}),ensure_ascii=False,indent=2,allow_nan=False))+'</pre></details>')
        chunks.append('</section>')
    chunks.append('<section><h2>Definitions and limitations</h2><p>Sales are summed signed source amounts. Profit is the supplied profit field. Margin is summed profit / summed sales × 100 and is undefined for nonpositive net sales. Gross loss is the magnitude of negative row-level profit before profitable lines offset it. Rows are order lines only when the source has that grain; they are not automatically distinct orders.</p>')
    for text in result['limitations']:chunks.append(f'<p>{e(text)}</p>')
    chunks.append('<details><summary>Reviewed import settings</summary><pre>'+e(json.dumps(result['config'],ensure_ascii=False,indent=2))+'</pre></details></section></main></html>')
    return ''.join(chunks)

def write_report(source,config,html_path,json_path=None,*,filters=None,comparison=None):
    source=Path(source);html_path=Path(html_path)
    targets=[html_path]+([Path(json_path)] if json_path else [])
    if len({t.resolve() for t in targets})!=len(targets) or any(t.resolve()==source.resolve() for t in targets):
        raise ValueError('Report outputs must be distinct and may not overwrite the input file.')
    result=build_analysis(source,config,filters=filters,comparison=comparison)
    content=render_html(result)
    for target in targets:target.parent.mkdir(parents=True,exist_ok=True)
    html_path.write_text(content,encoding='utf-8')
    if json_path:
        light={**result,'partitions':[{**part,'charts':[{k:v for k,v in c.items() if k!='image'} for c in part['charts']]} for part in result['partitions']]}
        Path(json_path).write_text(json.dumps(light,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return result
