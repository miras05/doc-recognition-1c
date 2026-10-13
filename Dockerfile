FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY docbot ./docbot
COPY mock_1c ./mock_1c

RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app:app /app
USER app

CMD ["python", "-m", "docbot.bot"]
