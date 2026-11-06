# General CSV/XLSX workflow

This workflow analyzes sales records with reviewed field meanings. It does not automatically understand arbitrary accounting, survey, medical, or unrelated datasets. Every input record is assumed to be one observation at the user's stated grain; raw rows are not automatically called orders.

## Desktop use

The local desktop app starts with `python -m src.app` or the repository's `start_app.cmd` after environment setup. An installed package also provides `sales-analyst-desktop`.

1. A CSV or XLSX file is selected. The source stays local and is not edited.
2. Delimiter, encoding, number format, date format, Excel sheet, discount units, and return handling are reviewed. Dates have explicit examples rather than guessed day/month order.
3. Suggested mappings are reviewed. Blank fields remain out of scope; ambiguous names such as Kazanç, Amount, or Price are not automatically assigned a financial meaning. One input column cannot serve as both sales and profit.
4. The review checkbox confirms meanings and formats. Changing settings invalidates that confirmation. Settings can be saved as JSON and reused with matching files.
5. A standalone HTML report is saved and opened. It contains readiness, issues, applicable/unavailable analyses, metrics, relevant charts, complete result tables, definitions, and limitations. If records are invalid, a quality-only report is generated unless exclusion was explicitly configured.

No account, external language model, server upload, or API key is needed. The HTML and JSON outputs themselves contain business results and should be shared intentionally.

## Command workflow

```powershell
python -m src.cli inspect examples/minimal_sales.csv --template work/draft.json
python -m src.cli analyze examples/minimal_sales.csv --config work/draft.json --confirm-mapping --save-config work/reviewed.json --output work/report.html --json-output work/report.json
python -m src.cli analyze examples/minimal_sales.csv --config work/reviewed.json --output work/next_report.html
```

The inspect command creates an unreviewed draft. The first analyze command is appropriate only after the draft's field meanings and formats have been reviewed. Saved reviewed settings avoid repeating that step for the same schema. CLI exit codes: 0 ready, 1 invalid request/configuration or I/O failure, 2 quality report only because the data need attention.

## Analysis requirements

| Analysis | Required mapped fields |
|---|---|
| Sales summary | sales |
| Monthly and calendar-month sales | sales, date |
| Product sales | sales, product_id and/or product |
| Category or region sales | sales, category or region |
| Profit and margin | sales, profit |
| Product gross-loss concentration | profit, product_id and/or product; sales remains the import minimum |
| Discount distributions | discount, profit; sales remains the import minimum |

Order IDs are optional. Average order value is calculated only when usable order IDs exist. Empty dimension labels disable the relevant grouping rather than being assigned invented categories. No profit is inferred from sales or an ambiguously labelled cost column.

## Formatting and policy examples

- `1.234,50`: decimal comma, thousands dot. `1,234.50`: decimal dot, thousands comma. Grouping positions are validated; malformed grouping is rejected.
- CSV delimiters and encodings are explicit. A non-UTF-8 Turkish file may need cp1254; the system does not silently guess between incompatible encodings.
- Default date format is ISO year-month-day. `03/04/2026` requires a reviewed day/month/year or month/day/year format. Native Excel date/numeric cells are preserved independently of text display format. Uncalculated formula cells without cached values may appear missing and must be resolved in the source workbook.
- Percent discounts such as `%20` or `20%` use `percent`; `0.20` uses `fraction`. Excel percentage cells often store a fraction even when displayed as 20%; the preview and selected units determine interpretation.
- Negative sales are rejected unless `allow_returns` is enabled. Enabled returns remain signed, and no original-sale linkage is inferred. Zero/nonpositive net sales yield an undefined profit margin.
- Multiple currencies are partitioned into separate sections and totals. No exchange rate is invented. A configured single currency conflicting with the source is an error. Without any currency definition, labels say source units rather than dollars.
- Invalid-row policy defaults to stop. Explicit exclusion records every excluded logical record number, and the result scope changes accordingly. Fully repeated records are warned about and retained; there is no automatic deduplication.
- Inputs and configuration files cannot be overwritten by report outputs through the CLI. Output HTML and JSON paths must be distinct. User-provided labels are HTML-escaped.

## Limitations

This is a local early release for small and medium tabular sales extracts, not a tested enterprise-scale ingestion system. No memory/row-count guarantee or independent usability study exists yet. Desktop support requires Python with Tk; some Linux installations need an OS Tk package. The Windows construction/settings smoke tests do not replace full cross-platform user testing.

Reports currently use English, while the desktop workflow is Turkish. XLSX is supported; legacy XLS, multiple header rows, merged business tables, accounting exports requiring row-grain transformation, and other file types are not automatically normalized. The configured mapping remains a human business-definition decision.
