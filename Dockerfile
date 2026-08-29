FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY k3ops ./k3ops
COPY tests ./tests

RUN useradd --create-home --uid 10001 k3ops
USER k3ops

EXPOSE 8890

CMD ["gunicorn", "--bind", "0.0.0.0:8890", "--workers", "2", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-", "k3ops.app:app"]
