# Hardened Non-Root Container for GCP Cloud Run Jobs / GKE Batch Workers
FROM python:3.10-slim-bookworm

# Environment flags for performance and security
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies for PyMuPDF & Tesseract OCR fallback
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libmupdf-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create a secure non-root application user
RUN groupadd -r -g 10001 appuser && useradd -r -u 10001 -g appuser appuser

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Default Entrypoint for Cloud Run Jobs
ENTRYPOINT ["python", "gcp_production_pipeline.py"]
CMD ["run-gcs", "--prefix", "raw_pdfs/", "--workers", "4", "--embed"]

