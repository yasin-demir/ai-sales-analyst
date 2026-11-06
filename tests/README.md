# Automated checks

`test_api_flow.py` verifies upload, inspection, reviewed preparation, two different questions on the same snapshot, semantic empty/insufficient results, and deletion. `test_resources.py` covers streaming input limits, storage/memory quotas, queue admission and real process termination. `test_rendering.py` checks concurrent user reports, serialized same-process rendering, style/data isolation, figure/file cleanup, render timeouts and numeric endpoints that never import Matplotlib. See `docs/FIRST_API_FLOW.md`, `docs/RESOURCE_LIMITS.md` and `docs/RENDERING.md`.

`test_owned_api.py` exercises local HTTP session authentication, all cross-session dataset operations, dataset/preparation/report binding, expiry, revocation, startup/periodic cleanup, in-flight deletion, storage failures and durable tombstones. `python tools/verify_local_api.py` additionally launches a temporary loopback Uvicorn process for a real TCP acceptance flow, then stops it. See `docs/OWNERSHIP_AND_LIFECYCLE.md`.

`test_datasets.py` covers the managed-upload/prepared-data service: upload-time hashing and immutable copies, separate review/preparation, exact legacy answer compatibility, zero file I/O/normalization/hash calls on warm queries, revision isolation, persistent metadata/cold restoration, concurrency, failed atomic publication and native Excel inputs. The service and limitations are documented in `docs/PREPARED_DATA_SERVICE.md`. `python tools/prepared_data_demo.py` runs the complete flow on synthetic data.

`test_question_handler_compatibility.py` compares complete results/errors against 74 synthetic pre-refactor cases stored in `tests/fixtures/question_outputs_before_handlers.json`, preserving numbers, statuses, record/warning order and error messages. It also checks non-mutation, separate handler dispatch, shared filtering/currency partitioning and early rejection. The snapshot was captured before extracting the six handlers and must not be regenerated to make a refactor pass. See `docs/QUESTION_HANDLERS.md`.

`test_request_types.py` covers direct-library wrong-type regressions, including `analysis=[]` and `sort_by=[]`. `test_contracts.py` checks six Pydantic request/response variants, exact JSON compatibility, all analytical statuses, finite strict numbers, dates, unknown fields, source/quality envelopes, validation before file I/O, internal output failures, and actual CLI files. Exported v1 schemas are compared against the models. `python tools/export_question_schemas.py --check` performs the schema check independently. These tests are offline.

The completed-month AI increment adds `test_periods.py` (calendar rules), `test_sales_assistant.py` (hand-calculated file-to-answer checks), `test_intent_ai.py` (mocked cloud contract and CLI encoding/output protection), and `test_intent_ollama.py` (key-free local contract and CLI integration). These run offline. `python tools/verify_ai_intents.py` evaluates twelve synthetic questions against a real provider: the default OpenAI mode needs `OPENAI_API_KEY`/`OPENAI_MODEL`; `--provider ollama` uses a running local model without credentials. Mocked responses do not establish real-model interpretation accuracy. Local acceptance results are in `docs/LOCAL_MODEL_SETUP.md`.

From the project root:

```powershell
python -m unittest discover -s tests -v
```

The tests use explicitly synthetic order lines, not the Superstore CSV. They check validation errors, exact-decimal summary parsing, weighted margins, product identity, gross/net reconciliation, no-loss cases, missing periods, overlap denominators, a descriptive margin bridge, non-mutation, and command JSON contracts.

The real-data notebook integration check is separate:

```powershell
python tools/verify_notebook.py
```

That check requires the local CSV. It executes a fresh kernel from both supported working directories by default and does not rewrite the notebook. `--write` saves executed outputs only after successful checks; `--html PATH` exports a standalone review. Unit tests run in CI without private data. Numerical source-snapshot values are not used as universal input requirements.

## General-data and desktop checks

`test_general.py` covers reviewed mappings, Turkish/English aliases, ambiguous names, explicit number/date/percent formats, native Excel cells and sheets, capability routing, returns, currency separation, quality-only reports, source preservation, HTML escaping, and CLI output contracts. `test_desktop.py` checks hidden-window construction and review/settings transitions on Windows; it is skipped elsewhere. Real-user usability and full cross-platform acceptance remain separate release tasks.


## Analytical question acceptance

`test_questions.py` uses hand-calculated canonical records to verify exact filtering, end-day inclusion, weighted margins, full-scope share denominators, minimum-count ranking, zero/negative baselines, percentage-point changes, missing-period handling, prior-year/leap-year boundaries, independent currencies, JSON-safe result envelopes, non-mutation, report integration and command/file preservation. No real data is required for these tests.
