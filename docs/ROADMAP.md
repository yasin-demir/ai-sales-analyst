# Decisions and delivery stages

## Separate bounded rendering

Numeric questions and default JSON reports do not load Matplotlib. Opt-in HTML reports run a separate killable render process under the existing admission/memory/storage budgets. Agg initialization, process-wide render locking, owned figures, style restoration and failure cleanup also cover the existing local chart generator. Concurrent-user report requests and same-process rendering are tested separately. See [rendering](RENDERING.md).

## First API flow acceptance

The initial endpoints support upload, combined status/preview/validated fields, preparation from approved settings, structured questions and deletion. One prepared snapshot is reused for two different queries. Empty selection and insufficient period data remain HTTP 200 analytical results with existing statuses and warnings; integration cases compare complete responses to the deterministic file bridge. A real TCP smoke flow also checks summary/monthly queries and deletion. See [first API flow](FIRST_API_FLOW.md).

## Service package 4: local session ownership and lifecycle

A loopback-only FastAPI prototype now derives ownership from server-issued, validated Bearer sessions. Dataset IDs do not grant access alone. Source/query/report/delete operations are scoped; fixed expiry, revocation, durable deletion journaling, startup/periodic cleanup and in-flight operation leases are implemented. One OS-locked API process owns each store. See [ownership and lifecycle](OWNERSHIP_AND_LIFECYCLE.md). Execution/resource quotas and serialized rendering are implemented in the owned service; see [resource profile](RESOURCE_LIMITS.md) and [rendering](RENDERING.md).

## Service package 3: managed uploads and prepared snapshots

`DatasetService` separates upload, preview, mapping approval, preparation and pinned queries. Source bytes are copied read-only with SHA-256 computed during upload; atomic metadata preserves approved settings/preparation history. Prepared frames are private in-memory snapshots identified by source/config/importer version. Warm queries perform no source I/O, normalization or hashing; a restart restores a cold frame once. See [prepared data service](PREPARED_DATA_SERVICE.md). Package 4 adds the HTTP ownership/lifecycle boundary; the owned service now also enforces configurable resource quotas.

## Service package 2: separate analytical handlers

`run_question` now routes to six separate private handlers. Validation, filtering, currency separation, envelopes and overall status remain shared. Seventy-four synthetic scenarios captured before extraction match the refactored outputs, including warning order and errors; non-mutation and dispatch boundaries are tested. The full suite passes 171 tests and v1 schema checks match. See [handler responsibilities and compatibility](QUESTION_HANDLERS.md).

## Service package 1: strict contracts and request types

Direct-library unhashable-type errors are covered by regression tests and normalized to controlled ValueError. Six Pydantic v2 request/response variants and a typed file/quality envelope validate the file bridge on entry and exit. Version-one JSON Schemas are exported and checked, while existing response JSON is preserved. See [the contract guide](API_CONTRACT_V1.md). The following packages add handlers, prepared data and the local HTTP ownership boundary; full execution quotas and plotting concurrency remain later work.

## Latest learning increment: completed-month question flow

An independently tested calendar rule feeds the existing deterministic period comparison. Optional `ask-ai` interprets only a narrow, unfiltered last-two-completed-months question using OpenAI or local Ollama structured output. Numerical answers are rendered from calculation results. `compare-months` works without AI. Provider-contract tests are offline; local Qwen3 4B also passed twelve live interpretation cases and the synthetic file-to-answer check. Cloud acceptance still needs credentials/model access. The HTTP API accepts structured questions; MCP and desktop chat remain future work. See [the learning guide](AI_QUESTION_FLOW.md) and [local setup results](LOCAL_MODEL_SETUP.md).

## Implemented in this local revision

1. Existing notebooks and source files were backed up before edits. The personal exploration notebook and source CSV were preserved.
2. Loading/validation and reusable KPI, segment, discount, product-loss, calendar/time, loss-overlap, product-pool, and margin-bridge functions were added. The exact-decimal legacy summary remains available.
3. Synthetic tests cover invalid data, weighted metrics, gross/net reconciliation, no-loss/zero-denominator cases, missing periods, product keys, overlap, margin decomposition, non-mutation, and JSON command contracts.
4. The notebook calls shared functions. Exact sample-count assertions and fixed Pareto percentages were removed. Numerical findings are rendered from current calculations. Repeated summary blocks and the empty final cell were removed; nine figures remain.
5. Targeted follow-up checks connect the largest regional loss pools with product losses and reconcile the 2016–2017 margin change.
6. README, metric contract, source fingerprint, Windows environment snapshot, CI unit-test workflow, and a repeatable notebook integration command were added.

## Deliberate scope limits

The notebook remains a historical Superstore case study. Data-snapshot-specific segment names, explanatory chart layouts, and the 2016–2017 diagnostic window are explicit case-study choices. Reusable functions do not assume 9,994 rows, 48 months, or particular loss-group counts. No product-master mappings, accounting cost fields, causal effects, or recovery estimates were invented.

The original source URL and license remain unknown. The core wheel installed in a separate clean Windows/Python 3.13 environment, but Windows Application Control blocked a newly installed pandas native DLL at runtime. Clean-environment execution is therefore unverified. The package is separately checked using the existing working dependencies; the full notebook environment snapshot was not recreated. Hosted CI runs on push and pull requests; its results are reported separately from local checks.

## Next product stages

1. Acceptance of the current library and report: review diagnostic interpretations, confirm metric definitions and data provenance, then commit/push when authorized.
2. A small question-to-function interface: limited, explicit intents for KPI, region/category comparisons, loss ranking, and period selection. Unsupported questions receive a clear scope response. Completion means every number is traceable to a function result, filters, and date range.
3. A thin interface/API around that contract, with input validation and integration tests. No model-generated arithmetic or arbitrary code execution is required.
4. Optional language-model explanations after deterministic routing and acceptance cases exist. Evaluation includes unsupported requests, empty filters, denominator ambiguity, incorrect causal claims, and fabricated figures. External services and credentials require a separate authorized setup.

More charts or forecasting are not prerequisites for the next stage. Forecasting would need a defined decision, holdout period, baseline, and evaluation criteria; price recommendations would require richer cost/demand evidence.


## General-data workflow delivered locally

The later revision implements capability-specific import requirements, reviewed mapping profiles, explicit locale/date/discount/return settings, CSV and XLSX inputs, transparent invalid/duplicate handling, currency-separated HTML/JSON reports, and a local desktop workflow. The Superstore notebook remains a preserved case study. Public-use decisions and remaining release gates are in PUBLIC_RELEASE_PLAN.md. No hosted release or public upload is implied by saving these files.


### Observed distribution limit

A clean-environment installation succeeded, but execution was blocked by Windows Application Control when pandas loaded a new native DLL. No security setting was changed. Existing-environment tests pass; clean-install runtime acceptance remains a release gate. Packaging tests with approved existing dependencies do not count as a successful clean dependency installation.


## Analytical question layer delivered

The four pre-API gaps are implemented: explicit date/segment filters, baseline/current and prior-period comparisons, general segment profitability, and currency-separated scoped JSON answers. Six explicit question intents cover summary, segment ranking, period comparison, monthly trend, product losses, and discount distributions. General reports and the desktop scope dialog use this layer. Hand-calculated tests cover weighted margins, display-independent shares, nonpositive baselines, percentage-point changes, empty/missing periods, filters, currencies, non-mutation, and file/CLI integration. The next main stage remains FastAPI, not another round of unscoped EDA expansion.
