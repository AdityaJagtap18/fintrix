FROM python:3.11-slim

# tesseract-ocr + poppler-utils: the Tesseract OCR adapter's only runtime dependency
# (core/extraction/tesseract_ocr.py lazily imports pytesseract/pdf2image, which need these
# system binaries present — see pyproject.toml's `ocr` extras group). `apt-get upgrade`
# pulls in any Debian security patches already released for packages already on the image.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

# pip/setuptools/wheel ship vendored inside the base image and lag behind their own CVE
# fixes (e.g. wheel's malicious-wheel-file CVE) independent of our actual dependencies.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir ".[ocr]"

RUN useradd --create-home --shell /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src"]
