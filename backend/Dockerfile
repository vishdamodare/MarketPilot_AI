FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY alembic alembic
COPY alembic.ini .
RUN useradd -r -u 10001 mp && chown -R mp /app
USER mp
ENV PORT=8010
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import os,urllib.request as u; u.urlopen(f'http://localhost:{os.environ.get(\"PORT\",\"8010\")}/api/health')"
# One worker on purpose: the in-process scheduler thread and in-memory rate limiter are per process
# (a Postgres advisory lock already keeps sync cycles from overlapping if you scale out).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
