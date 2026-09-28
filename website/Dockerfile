FROM python:3.12-slim

# Build context is the repo root: website/ holds the app, fitment/ holds the
# committed fitment databases (fitment_live.db, fitment_2019plus.db).
WORKDIR /srv
COPY website/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY website/ .
COPY fitment/ ./fitment/

COPY website/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
