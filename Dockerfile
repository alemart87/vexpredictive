FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "\
  if [ -d /persistent ] && [ ! -f /persistent/.initialized ]; then \
    mkdir -p /persistent && \
    touch /persistent/.initialized && \
    echo 'Persistent disk initialized'; \
  fi && \
  python migrate_v2.py && \
  python migrate_v3.py && \
  python migrate_v4.py && \
  python migrate_v5.py && \
  gunicorn --bind 0.0.0.0:10000 --timeout 120 app:app"]
