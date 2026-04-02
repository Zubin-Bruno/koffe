FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY minimal_app.py .

EXPOSE 10000

CMD uvicorn minimal_app:app --host 0.0.0.0 --port ${PORT:-10000}
