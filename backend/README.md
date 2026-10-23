# Query backend starter

This is the first implementation slice for Nilesh's ownership: the versioned query endpoint and orchestration boundary. It validates a request, calls retrieval, prepares bounded evidence, asks a generation adapter for an answer, and validates proposed evidence IDs against persisted source records before returning citations.

The repository had no application code or selected runtime when this slice was started. Python 3.11+ and FastAPI are a practical starting point for the RAG backend ecosystem. This is a local backend choice only; it does not choose a database, vector store, embedding model, language model provider, or deployment platform.

## Run locally

From this `backend` directory, install the package in editable mode and run:

```powershell
python -m pip install -e .
uvicorn app.main:app --reload
```

`GET /health` is a liveness check. `GET /ready` returns HTTP 503 until retrieval, source-record, and answer-generation adapters are supplied; it checks configuration only and does not probe their external services. `POST /v1/query` validates requests, but returns HTTP 503 until the retrieval, source-record, and generation adapters are supplied. This avoids pretending that a database or model is already connected.

Set `APP_ENV` and `LOG_LEVEL` to configure the runtime (defaults: `development` and `INFO`). HTTP logs are JSON and include a request ID, method, path, status, and duration. Request and response bodies, query strings, prompts, and judgment text are not logged. A single valid incoming `X-Request-ID` is propagated; otherwise the service generates one. The ID is returned in the `X-Request-ID` response header and in query responses. Responses include `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`. Invalid-request responses do not echo submitted values.

The API bounds question length, filter count, identifier length, source count, passage size, and total evidence context size. Retrieved candidates with duplicate chunk IDs are discarded as ambiguous; duplicate or mismatched persisted source records are not cited.

Authentication and deployment security are not implemented in this slice; they require the team's agreed auth boundary and Prathmesh's security configuration. The default validation response should be aligned with Kusuma's shared error envelope when that contract is agreed.

## Integration points

- Pratik supplies `RetrievalPort` with ranked passages and their stable identities/page provenance.
- Anuradha supplies `SourceRecordPort` to resolve passage/document/source records and canonical metadata.
- The selected model integration supplies `AnswerGenerationPort` and returns answer text plus evidence IDs from the supplied context.
- Kusuma's shared validation/error utilities can be adopted after their contract is agreed; this slice keeps query-specific validation and orchestration local to the query backend.

The in-memory interfaces in `app/ports.py` describe contracts only; they do not implement search, persistence, OCR, embeddings, authentication, or model generation.
