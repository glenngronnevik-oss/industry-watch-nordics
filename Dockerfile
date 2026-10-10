# syntax=docker/dockerfile:1
# Multi-arch: builds natively on Raspberry Pi (arm64/armv7) or via buildx for amd64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    IW_DATA_DIR=/data

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

# Run as an unprivileged user; /data is the only writable path.
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin iw \
 && mkdir -p /data && chown iw:iw /data
USER iw
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4).status==200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
