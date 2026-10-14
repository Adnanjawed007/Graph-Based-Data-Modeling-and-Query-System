# SAP Order-to-Cash Context Graph

A compact investigation prototype over the supplied SAP-style JSONL dataset. It preserves source identifiers as strings, stages every logical table in SQLite, profiles source quality, and projects verified order-to-cash links into a bounded NetworkX graph. A React dashboard supports graph browsing, entity inspection, neighbor expansion, query evidence, and graph highlighting.

## What is implemented

- Recursive JSONL inventory and reproducible profiling (`python -m backend.profile`). The generated report covers 19 logical tables, per-table files and row counts, column names, observed JSON types, null rates, sample values, candidate keys, duplicate candidate-key counts, candidate join coverage, and cancellation counts.
- Repeatable SQLAlchemy 2 ingestion (`python -m backend.ingest`) that creates a relational SQLite table for each logical source table. Source values stay text, nested JSON values are retained as JSON strings, and source file/line are composite provenance keys. A JSON staging table remains available to the API. Malformed JSON lines are retained in a diagnostic table rather than discarded silently.
- NetworkX graph projection persisted as `canonical_entities` and `document_relationships`; join audits are stored in `relationship_validation_results`. FastAPI exposes dataset statistics, entity search/detail, graph retrieval/expansion/path, document traces, data-quality diagnostics, incomplete-flow rules, top products, query validation, and safe supported queries.
- Responsive React, TypeScript, Vite, and Cytoscape dashboard.
- Optional Gemini and Groq adapters return a strict Pydantic query plan for bounded supported intents; provider output cannot supply SQL, columns, or operators. Unsupported requests are rejected, ambiguous entity queries request clarification, and explanations are generated from retrieved records.

## Data profile and source caveats

The provided archive was extracted to `data/raw/sap-o2c-data/` for this working copy. Profiling found 19 tables and 21,393 JSONL records across the supplied files. The raw directory and generated SQLite database are ignored by Git so the source dataset is not accidentally published. `data/reports/profile.json` is generated locally and can be regenerated at any time.

Numeric item identifiers can be represented in multiple forms (`10`, `000010`). For joins only, numeric fields whose names end in `Item` are canonicalized by stripping leading zeroes. Original payload values are unchanged. The report shows exact and item-normalized join coverage. Other identifiers are compared exactly. Similar-looking values alone are not treated as relationships.

Candidate join coverage is a profiling signal, not proof of business semantics. Billing preceding-document references are linked only when an existing delivery item or sales-order item matches under the item-number policy. Unmatched billing references are exposed by `/api/data-quality/report`. The supplied data includes cancellation rows and headers marked canceled; top-product billing-document counts exclude headers explicitly marked canceled.

## Architecture

```text
JSONL parts -> profiler -> data/reports/profile.json
           -> SQLite source_records + ingestion_errors
                    |                 |
                    |                 +-> FastAPI analytics / search / trace
                    +-> NetworkX verified graph -> bounded graph API
React + Cytoscape ---------------------------------------> FastAPI
```

SQLite is the source of truth. SQLAlchemy Core creates per-table relational source staging tables with text-preserved fields, provenance keys, and a JSON staging payload. Canonical graph entities and verified relationships are stored separately from source rows. NetworkX is rebuilt as a derived projection for traversal. Amounts remain source strings in staging and are parsed only for bounded analytics; ingestion does not silently rewrite source values.

## Local setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
python -m backend.profile
python -m backend.ingest
python -m backend.validate
python -m backend.build_graph
uvicorn backend.app:app --reload
```

In a second PowerShell window:

```powershell
cd frontend
npm install
npm run dev
```

The dashboard runs at `http://localhost:5173`; the API and interactive OpenAPI docs run at `http://127.0.0.1:8000` and `/docs`. Set `FRONTEND_ORIGIN` for cross-origin requests, `DATA_DIR` for source data, and either `DATABASE_PATH` or a SQLite `DATABASE_URL` for the database.

## API examples

- `GET /api/health`
- `GET /api/dataset/stats`
- `GET /api/entities/search?q=740506`
- `GET /api/entities/sales_order/740506`
- `GET /api/graph?limit=150`
- `GET /api/graph/stats`
- `GET /api/graph/path?source_node_id=sales_order%3A740506&target_node_id=customer%3A310000108`
- `GET /api/graph/expand/sales_order:740506`
- `GET /api/documents/sales_order/740506/trace`
- `GET /api/analytics/top-products?limit=20`
- `GET /api/analytics/incomplete-flows`
- `GET /api/data-quality/report`
- `POST /api/query/validate` with a question JSON object
- `POST /api/query` with `{"question":"Trace order 740506"}`

Top products are ranked by distinct non-canceled billing documents. Quantity and net amount use Decimal parsing and stay grouped by currency; product units are not normalized. Incomplete flows mean an order has no referenced delivery item, or a delivery item has no matching non-canceled billing item.

### Verified example

Running `POST /api/query` with `{"question":"Billing summary by month"}` on the supplied dataset returned two INR groups:

| Billing month | Distinct documents | Quantity | Net amount |
| --- | ---: | ---: | ---: |
| 2025-04 | 76 | 118 | 28,711.58 |
| 2025-05 | 7 | 12 | 2,117.75 |

These are retrieved results from this dataset, not fixed application metrics. The response includes the validated plan, source tables, metric definition, execution status, and a warning when the deterministic planner is used because no LLM credentials are configured.

The query API accepts document traces, top-product ranking, customer order lookup, billing summaries grouped by customer/product/month, and incomplete-flow analysis. Supported filters include customer/product identifiers and inclusive date ranges. Unresolved customer or product names produce a clarification response. It does not execute model-generated SQL. Without credentials, a deterministic planner handles supported phrasings and returns a warning. To enable model-based intent interpretation, configure `LLM_PROVIDER` (`groq` or `gemini`), `LLM_API_KEY`, and `LLM_MODEL`. Model availability is provider-account specific; the model name is not hardcoded. Provider failures and invalid plans fail closed.

## Verification

Verified locally on the supplied dataset:

- `python -m backend.profile` — profiled all 19 logical tables, including join coverage and data-quality diagnostics.
- `python -m backend.ingest` — ingested 21,393 records from all 19 logical tables into JSON staging and per-source relational tables.
- `python -m backend.validate` — SQLite integrity passed; source and relational row counts matched; zero ingestion errors.
- `python -m backend.build_graph` — persisted 1,458 canonical entities and 21,949 validated relationships.
- `python -m pytest backend/tests -q` — 23 tests passed, including synthetic malformed-input and repeat-ingestion fixtures, identifier preservation, plan safety, injection resistance, verified joins, cancellation-aware analytics, and provider-plan validation.
- `cd frontend; npm run build` — production build succeeded with Cytoscape code-split. `npm audit` found zero vulnerabilities.

Tests combine the real dataset with synthetic JSONL and ingestion fixtures. They do not cover every relationship edge case or live provider behavior.

## Deployment status and limitations

`render.yaml` defines a Render API service with a persistent disk and a static frontend. The dataset must be securely uploaded to `/var/data/sap-o2c-data`, then profile, ingest, validation, and graph build commands must be run on the service; ephemeral application storage is not suitable for SQLite. No live deployment has been created or verified. Review the Render plan's persistent-disk availability and configure secrets through the host environment.

For production initialization, create the Blueprint, securely place the authorized dataset files on the mounted disk, then use the API service shell to run `python -m backend.profile`, `python -m backend.ingest`, `python -m backend.validate`, and `python -m backend.build_graph` in that order. Restart the API after initialization so its in-memory graph cache is built from the initialized database. Verify `/api/health`, `/api/dataset/stats`, and `/api/graph/stats`. The static site is built from `frontend/` and points at the API service URL through `VITE_API_URL`.

This remains an assessment prototype. Queries are bounded to the documented intents. The source table staging model and canonical graph tables are not a fully normalized domain ORM. Gemini/Groq adapters have not been verified with live credentials. Authentication, rate limiting, audit retention, full payment/invoice lifecycle semantics, and exhaustive security tests remain production work. Validate candidate keys and joins with data owners before operational use.
