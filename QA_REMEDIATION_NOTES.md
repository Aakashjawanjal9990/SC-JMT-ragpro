# QA Remediation Notes

**Input audit:** `Kedar_Offline_Process_Line_by_Line_QA_Audit_2026-10-28-1 1.pdf`  
**Scope:** Supplied offline-ingestion source ZIP only.  
**Release status:** Improved and regression-tested locally; **not certified for unrestricted production** until cloud/database and representative-corpus validation are completed.

## Remediations applied

| Audit finding | Remediation in this revision | Validation status |
|---|---|---|
| OFF-001 — OCR not connected to orchestrator | Integrated routing/engine handling before chunking; OCR confidence is not artificially floored; required OCR failure and unclassified empty pages fail the document rather than being certified as blank. | Synthetic end-to-end OCR routing test passes. Real PaddleOCR model/runtime test still required. |
| OFF-002 — missing `check_gates` import | This supplied ZIP already uses the tracked `audit_quality.py` entry point rather than importing `check_gates`; batch startup import now smoke-tests successfully. | Local import smoke test passes. |
| OFF-003 — GCS method mismatch | The supplied ZIP already contains `list_available_pdfs`; discovery now uses the configured GCS prefix and case-insensitive `.pdf` extension matching. | Contract test passes; no live GCS access performed. |
| OFF-004 — missing PDF fixtures | Added self-contained synthetic PDF regression tests that do not depend on absent historical PDF fixtures. | Tests pass from a clean project checkout. Real historical fixture evaluation remains required. |
| OFF-005 — worker failures not accounted | Sequential and parallel ingestion paths record failures; parent-process persistence errors are accounted for; failure manifests are written atomically. | Failure paths covered in source and local tests; full batch failure-injection test against a real DB remains required. |
| OFF-006 — unsafe database target/credential fallback | Local target is isolated to SQLite; AlloyDB requires an explicit URL or complete credentials; reserved characters in credential-built URLs are encoded; TLS cannot be disabled for the AlloyDB target; example config no longer contains credential-looking values. | Configuration tests pass. Existing deployment environment variables must be checked before rollout. |
| OFF-007 — unsupported production claims | README now states that the code is not certified for unrestricted production ingestion. | Documentation scan performed. |
| OFF-008 — OCR errors represented as empty text | OCR result errors/empty outputs are handled explicitly; an unresolved page prevents document persistence. | Synthetic OCR-failure test passes. |
| OFF-009 — no end-to-end persistence evidence | Source hash and file size are computed from the exact input bytes before temporary GCS files are removed; those values are carried into persistence. Chunk/page/provenance checks are stricter. | Source-level and synthetic tests pass. Actual AlloyDB/SQLite persistence, rollback, idempotency, and read-after-write still require a configured test DB. |
| OFF-010 — gate failure can return success | Batch release decision requires a non-empty set of checks, every check explicitly `PASSED`, no failed documents, and saved count equal to queued count. Quality audit fails closed for missing DB, empty corpus, unreadable files, unexplained empty source pages, invalid page boundaries, and invalid/missing embeddings. | Quality-gate tests pass. Run the audit on a representative persisted dataset before release. |
| Additional runtime mismatch found during remediation | Fixed the pipeline's metadata call to use the implemented `LegalMetadataExtractor.extract_metadata(text)` API and removed access to a nonexistent `PageMetadata.case_title` field. Fixed the stage tracker constructor mismatch and added provenance lifecycle tracking. | Synthetic pipeline tests and module import smoke test pass. |
| GCS source identity/provenance | Document IDs are derived from the stable GCS object path rather than the randomly named temporary download; source SHA-256 and byte size are retained for DB persistence. | Code-reviewed; live GCS round-trip remains untested. |
| Partial JSON artifacts | Chunk/document JSON deliverables and failure manifests are written atomically. | Source-level review; filesystem fault injection remains recommended. |

## Local validation performed

- `python -m compileall -q .` — passed.
- `pytest -q` — **8 passed**.
- Import smoke test across 16 core modules — passed.
- Secret/certification text scan — no remaining hard-coded database password/IP or unconditional production-certification statement found in the project source/config example.

## Not validated in this environment

- Live Google Cloud Storage listing/download and permissions.
- Real PaddleOCR / PP-Structure model initialization and historical scan accuracy.
- Real Vertex AI embedding calls, quota behavior, and model compatibility.
- Actual AlloyDB connection, TLS certificate validation, production schema parity, transaction rollback, and idempotent re-ingestion.
- Full-corpus reconciliation and throughput/cost at 43,000+ judgments.
- Ruff, Bandit, and pip-audit scans; these tools were not installed in the execution environment.

## Release conditions

Do not treat these local tests as production certification. Before unrestricted ingestion, run controlled tests using the intended deployment identity and network path; verify the actual schema; test DB rollback/idempotency/read-after-write; inspect OCR on reviewer-checked historical and born-digital judgments; reconcile every source PDF as completed, explicitly skipped, or failed; and attach commit-specific CI results to the release.

## Follow-up dynamic-configuration remediation (2026-10-29)

Applied to the follow-up ZIP supplied for this review:

- Removed silent `2026` year fallbacks from batch input selection, the GCS downloader, and the pipeline. If a year cannot be inferred from the supplied year, GCS `year=YYYY` path, or filename prefix, ingestion now fails with an actionable error instead of writing fabricated metadata.
- Centralized GCS project and bucket settings through `config.py`; removed duplicated development project/bucket fallbacks from `storage/gcs_loader.py`. GCS ingestion now fails early when `GCP_PROJECT` or `GCS_BUCKET` is not explicitly configured.
- Added `PipelineConfig.validate_runtime()` to validate required cloud settings for GCS/production runs and require explicit embedding configuration plus disabled mock embeddings for production.
- Made local OCR language, version, GPU selection, PP-Structure enablement, cache path, and routing-quality thresholds configurable via environment variables; added range/order validation for OCR thresholds.
- Added a production guard against explicitly requested mock OCR/structure output when `ENVIRONMENT=production` or `prod`.
- Updated `.env.example` with the OCR configuration options.
- Added regression tests for unknown-year rejection and missing GCS/production configuration.

Validation on this follow-up ZIP: `python -m compileall -q .` passed; `pytest -q` passed with 11 tests. Live GCS, Vertex AI, PaddleOCR model inference, and AlloyDB integration were not exercised in this environment. Production readiness is therefore not certified by these local tests.
