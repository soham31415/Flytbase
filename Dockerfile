FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Shell form so ${PORT} expands. Exactly 1 worker: there must be only one poller.
# seed/ holds snapshots (raw/) and analyst YAML (analysis/). Copy them into data/ (the volume)
# on every start; crm.db is not in seed/, so the store survives, and snapshot import is idempotent.
CMD mkdir -p data && cp -r seed/. data/ && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
