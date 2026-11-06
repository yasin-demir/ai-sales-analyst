"""HTML artifact job over a numeric snapshot. No Matplotlib imports in the API."""
import base64
import html
import json

from .resources import ResourceLimit


class RenderFailure(RuntimeError):
    pass


def render_report(answer, theme, limits):
    """Spawn entry point: initialize Agg in the child, render serially, return bytes."""
    try:
        return _render_report(answer, theme, limits)
    except ResourceLimit:
        raise
    except Exception as exc:
        raise RenderFailure('Report rendering failed; no artifact was published.') from exc


def _series(entry):
    records = entry['records']
    analysis = entry['analysis']
    if analysis == 'summary':
        metrics = entry['metrics']
        return [(k, metrics[k]) for k in ('sales', 'profit', 'gross_loss') if metrics.get(k) is not None], 'Amount'
    if analysis == 'period_comparison':
        sales = next((r for r in records if r['metric'] == 'sales'), None)
        return ([(k, sales[k]) for k in ('baseline', 'current')] if sales else []), 'Sales'
    if analysis == 'discount_comparison':
        return [(r['profit_status'], r['mean_discount_percent']) for r in records], 'Mean discount (%)'
    metric = 'gross_loss' if analysis == 'loss_concentration' else 'sales'
    keys = ('month',) if analysis == 'monthly_trend' else ('category', 'region', 'product_id', 'product', 'order_id')
    return [(' | '.join(str(r[k]) for k in keys if k in r), r[metric]) for r in records], metric


def _render_report(answer, theme, limits):
    # Lazy import: /questions and JSON reports never initialize Matplotlib.
    from .plotting import render_session, subplots, png
    if theme not in ('light', 'dark'):
        raise ValueError('Unknown report theme.')
    foreground, background = ('#172033', '#ffffff') if theme == 'light' else ('#f1f5f9', '#172033')
    style = {'font.size': 10, 'figure.facecolor': background, 'axes.facecolor': background,
             'text.color': foreground, 'axes.labelcolor': foreground, 'axes.edgecolor': foreground,
             'xtick.color': foreground, 'ytick.color': foreground, 'savefig.facecolor': background}
    escape = lambda value: html.escape(str(value))
    result = answer['result']
    chunks = ['<!doctype html><html lang="en"><meta charset="utf-8">',
              '<title>Analysis report</title>',
              f'<body style="color:{foreground};background:{background};font:16px system-ui">',
              '<h1>Analysis report</h1>',
              f'<p>Source: {escape(result["source"]["file"])}</p>',
              f'<p>SHA-256: {escape(result["source"]["sha256"])}</p>',
              f'<p>Preparation: {escape(answer["preparation_id"])}</p>',
              f'<p>Status: {escape(result["status"])}</p>']
    figures = 0
    with render_session(style):
        for entry in (result.get('answer') or {}).get('results', []):
            unit = entry['units']['amount']
            chunks.append(f'<section><h2>{escape(unit)} · {escape(entry["analysis"])}</h2>')
            if entry['status'] in ('ok', 'empty_ranking'):
                series, metric = _series(entry)
                if series:
                    figures += 1
                    if figures > limits.render_figures:
                        raise ResourceLimit('Report figure count exceeded; narrow the query.')
                    shown = series[:50]
                    fig, ax = subplots(figsize=(9, max(3, min(14, len(shown) * .28 + 1.5))))
                    ax.barh(range(len(shown)), [value for _, value in shown], color='#167d9a')
                    ax.set_yticks(range(len(shown)), labels=[label[:100] for label, _ in shown])
                    ax.set(xlabel=f'{metric} ({unit})' if metric != 'Mean discount (%)' else metric,
                           title=entry['analysis'])
                    fig.tight_layout()
                    encoded = base64.b64encode(png(fig)).decode('ascii')
                    chunks.append(f'<img alt="{escape(entry["analysis"])}" style="max-width:100%" src="data:image/png;base64,{encoded}">')
                    chunks.append(f'<p>Showing {len(shown)} of {len(series)} chart items. Complete numeric results follow.</p>')
            for warning in entry['warnings']:
                chunks.append(f'<p>{escape(warning)}</p>')
            chunks.append('</section>')
        # The unchanged numeric snapshot contains exact scope, units and definitions.
        chunks.append('<h2>Numeric results and definitions</h2><pre>' +
                      escape(json.dumps(answer, ensure_ascii=False, allow_nan=False, indent=2)) + '</pre></body></html>')
        content = ''.join(chunks).encode('utf-8')
        if len(content) > limits.response_bytes:
            raise ResourceLimit('Rendered report byte limit exceeded.')
        return content
