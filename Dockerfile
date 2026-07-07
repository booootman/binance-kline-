FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIAN_HOST=0.0.0.0 \
    BIAN_PORT=8000 \
    BIAN_LOG_LEVEL=INFO

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bian.py server.py ./
COPY src ./src
COPY web ./web

RUN mkdir -p /app/runtime && chown -R appuser:appuser /app/runtime

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["python", "-B", "server.py"]
