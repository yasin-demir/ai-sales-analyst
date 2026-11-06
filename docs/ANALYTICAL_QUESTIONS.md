# Analytical questions and result contract

Version-one Pydantic request/response models are in `src/contracts.py`. The file bridge validates requests before reading input and validates generated responses before returning them. See [the contract guide](API_CONTRACT_V1.md) and the exported JSON Schemas in `docs/schemas/`. The [local HTTP prototype](OWNERSHIP_AND_LIFECYCLE.md) uses the same contracts with prepared datasets and validated session ownership.

The functions in `src/questions.py` operate on the canonical, validated fields produced by the general importer. They do not guess natural-language intent, derive profit from sales, combine currencies, or execute generated code. The local FastAPI layer invokes them through authenticated prepared-data queries. An optional CLI natural-language adapter also uses them for the narrow last-two-completed-months scenario; see [AI question flow](AI_QUESTION_FLOW.md).

## Supported questions

| User question | Public function / intent | Important requirements |
|---|---|---|
| What are sales, profit, and margin in this date/segment scope? | `summarize_sales` / `summary` | sales; profit and order IDs only when available |
| Which region/category/product has the lowest margin or highest loss? | `rank_segments` / `segment_performance` | explicit grouping; profit for profitability/loss metrics |
| How did sales, profit, and margin change between two periods? | `compare_periods` / `period_comparison` | explicit nonoverlapping baseline/current windows and date |
| How does a month/year compare with the previous period or prior year? | `compare_previous_period`, `previous_period_bounds` | explicit YYYY-MM or YYYY label; M/Y frequency |
| What is the monthly sales/profit/gross-loss pattern? | `monthly_trend` | date and sales; profit is optional |
| Which products contribute most to gross loss? | `rank_product_losses` / `loss_concentration` | profit and a product identifier/label |
| How do discount distributions differ by profit outcome? | `compare_discounts` / `discount_comparison` | discount and profit |

`apply_filters` supports inclusive calendar-day start/end bounds in YYYY-MM-DD and exact membership in category, region, product ID/name, order ID, and currency. It copies the selected data and does not change the source. Business timezone normalization must precede calendar-day analysis of timezone-aware timestamps. Missing requested fields are explicit unavailable results, not invented values. No matching rows is an empty selection rather than a zero-sales observation.

## Shared result structure

Every public question result has `schema_version`, `analysis`, `question`, `status`, and currency-separated `results`. Each currency result has `scope`, `filters`, `units`, `metrics`, `records`, `warnings`, and `definitions`. The scope records input/selected row counts, observed dates, and requested date bounds. File-level answers additionally include source file/fingerprint, data quality, raw row count, and explicitly excluded record count.

Results are JSON-safe: undefined values are null rather than NaN/Infinity. Statuses distinguish ok, partial currency availability, empty selection, empty ranking, unavailable fields, insufficient period data, and import needs_attention. Invalid request syntax/parameters raise a clear ValueError. There is no aggregate monetary result across currencies.

## Ranking and denominator rules

Segment metrics include signed sales, net profit, positive-row profit, gross loss, record count, loss-record count/rate, weighted margin, sales share, and gross-loss share when fields permit. Supported sort metrics use these canonical names. Margin defaults to ascending order; other metrics default to descending order. `limit` and `min_records` restrict display/ranking, not the denominator. Full-scope totals remain in metrics. Undefined margins are excluded when ranking by margin; they remain visible when ranking by another defined measure.

Gross-loss shares use all selected gross loss in the same currency, including groups hidden by display limits. Sales shares and margins are undefined for nonpositive signed sales. A product can have loss-making records while remaining net profitable. Gross exposure is not a savings estimate. Order counts/average order values refer to selected records and may include only part of an order after filters.

## Period comparison rules

The baseline must finish before the current period begins. Additional general date bounds are rejected for a period comparison so they cannot silently truncate either window; segment filters are supported. Sales/profit/gross loss/counts/AOV receive absolute changes and relative percentages only when the baseline is positive. A zero or negative baseline has no relative growth rate. Margin changes are percentage points, not relative percentages.

An unobserved period produces insufficient_data, not a zero total. Different calendar lengths are flagged. Observed transaction dates not reaching requested boundaries are flagged; even reaching both boundaries does not independently establish completeness. No working-day, exposure, causal, or seasonal adjustment is implied. Monthly trend summaries retain missing months as missing.

## Command examples

```powershell
python -m src.cli questions
python -m src.cli ask examples/synthetic_sales.csv --config examples/synthetic_mapping.json --request examples/question_lowest_margin.json --output work/lowest_margin.json
python -m src.cli ask examples/synthetic_sales.csv --config examples/synthetic_mapping.json --request examples/question_month_comparison.json --output work/month_comparison.json
python -m src.cli analyze examples/synthetic_sales.csv --config examples/synthetic_mapping.json --filters examples/analysis_filters.json --comparison examples/comparison_periods.json --output work/scoped_report.html --json-output work/scoped_report.json
```

The three request examples and all example sales rows are synthetic. Request output cannot overwrite the request, configuration, filters, comparison, or source file. `ask` returns exit code 2 for unavailable/insufficient data; empty selections/rankings are valid explicit answers. The local desktop app provides an optional analysis-scope/period dialog. Import settings and analytical scope are separate concepts.

## Next boundary

This revision completes the query functions and report integration needed before the API. FastAPI endpoints can call `answer_file_question` or the pure question functions, validate the same request fields, and return the same result contract. No arbitrary natural-language interpretation or model-generated arithmetic is required for that step.
