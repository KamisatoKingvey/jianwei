FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY src ./src
COPY static ./static

RUN mkdir -p data \
    && pip install --no-cache-dir .

EXPOSE 80

CMD python -m uvicorn jianwei.api.main:app --host 0.0.0.0 --port 80
