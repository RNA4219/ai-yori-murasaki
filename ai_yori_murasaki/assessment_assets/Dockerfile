FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/dev/shm \
    HOME=/dev/shm

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium \
      openssl \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --target /opt/requests-old \
      requests==2.31.0 \
      urllib3==2.2.3 \
 && pip install --no-cache-dir --target /opt/requests-fixed \
      requests==2.32.5 \
      urllib3==2.2.3

RUN mkdir -p /opt/aym-test-cert \
 && openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -keyout /opt/aym-test-cert/key.pem \
      -out /opt/aym-test-cert/cert.pem \
      -subj "/CN=localhost" \
      -addext "subjectAltName=IP:127.0.0.1,DNS:localhost" \
 && chmod 0644 /opt/aym-test-cert/key.pem /opt/aym-test-cert/cert.pem \
 && chmod 0755 /opt/aym-test-cert

LABEL purpose="finite-isolated-lab"

WORKDIR /app

USER 65534:65534
