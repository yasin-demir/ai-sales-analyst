# Local data and provenance

The working extract is `Sample - Superstore.csv`; data files are excluded from Git. `source_manifest.json` records the local snapshot fingerprint, shape, and date range. A hash establishes file identity, not authenticity or permission to redistribute.

The original download URL, publisher/version, and redistribution license have not been independently verified. No source link or license is invented. These fields remain explicitly unknown in the manifest and should be documented before dataset redistribution. Raw customer/order rows are not included in the repository or synthetic tests.

Dollar presentation follows the project's Superstore convention; the CSV has no independently verified currency or cost-coverage metadata. The analysis does not represent current company-wide accounting results.

Validation retains negative and zero Profit lines, rejects missing required identifiers and invalid numeric/date values, and preserves multiple names per Product ID as composite product groups. It does not silently deduplicate or repair business records. The CLI summary intentionally has a smaller Sales/optional-Profit contract than full analytical reports.
