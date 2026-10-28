# Build from the repository root so the retrieval tokenizer can be copied in:
#   docker build -f bm25_builder/Dockerfile -t bm25-builder .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /job

COPY bm25_builder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bm25_builder/main.py .
# Single source of truth: index terms must be tokenized exactly like queries.
COPY app/retrieval/tokenizer.py ./legal_tokenizer.py

USER nobody
CMD ["python", "main.py"]
