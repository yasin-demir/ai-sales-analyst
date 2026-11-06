# Public usefulness and release plan

## Ready locally

- Capability-based CSV/XLSX import, reusable reviewed mappings, locale/return policies, currency partitioning, and transparent quality reports.
- A local desktop file/mapping workflow, HTML/JSON results, installable Python package entry points, and clearly synthetic demo files.
- Automated analytical/import/report tests, source preservation, and the existing Superstore case-study notebook.

## Before a public release

1. The owner needs to select an explicit code license; no intellectual-property license has been assigned on their behalf. Superstore redistribution rights and download provenance remain separate and unverified. Synthetic examples can demonstrate the workflow without including the real extract.
2. A short pilot with 3–5 intended users and their non-sensitive sample exports should validate mapping terminology, date/discount settings, and report comprehension. Success means users complete the flow without code edits and can explain the denominator and limitations of results.
3. Turkish and English report/UI language options, accessible colors/text, keyboard navigation, small-screen layout, and actionable error wording need user acceptance checks. Human use is broader than a programmatic smoke test.
4. A signed or otherwise verified Windows executable/installer would remove the Python setup barrier. It requires separate packaging, update, integrity, and antivirus checks; the current wheel is an installable Python package, not a standalone executable.
5. Documentation should include a 2-minute demonstration, three distinct synthetic source schemas, sample report screenshots, support/issue templates, and a compatibility/known-limits matrix.
6. Versioned releases remain a separate step. The configured hosted test workflow runs on push/pull requests; its results are reported separately from local checks. No hosted application deployment or dataset redistribution is included.

## Product positioning

The initial audience is people with tabular sales exports who need an explainable local report: small businesses, students, and analysts. It should not claim to analyze every kind of data. A focused sales use case with trustworthy field mapping is more useful than universal but unreliable interpretations.

Next capabilities can follow pilot evidence: date/segment filters, repeat-run comparisons using saved profiles, connector-specific import templates, and exports of selected tables. A language-model interface is optional and should explain deterministic results rather than inventing business definitions, arithmetic, or causal explanations.


### Observed distribution limit

A clean-environment installation succeeded, but execution was blocked by Windows Application Control when pandas loaded a new native DLL. No security setting was changed. Existing-environment tests pass; clean-install runtime acceptance remains a release gate. Packaging tests with approved existing dependencies do not count as a successful clean dependency installation.
