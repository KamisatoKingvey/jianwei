FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY src ./src
COPY static ./static

# 腾讯云镜像源加速云托管构建（claude-agent-sdk 体积较大）
RUN mkdir -p data \
    && pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple .

EXPOSE 80

CMD python -m uvicorn jianwei.api.main:app --host 0.0.0.0 --port 80
