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

# --proxy-headers: the host nginx terminates TLS and proxies over plain HTTP to this
# container (deploy/nginx/fintrix.conf), so without this uvicorn reports every request as
# scheme=http regardless of what the client actually used — wrong for anything that builds
# an absolute URL from the request (e.g. the invoice-upload endpoint's pdf_url). Only
# nginx on the same host can reach this container's port at all (docker-compose.prod.yml
# binds it to 127.0.0.1), so trusting X-Forwarded-* from any peer here is safe.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src", "--proxy-headers", "--forwarded-allow-ips=*"]
