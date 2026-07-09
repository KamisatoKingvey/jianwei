# 见微后端

这是见微雷达睡眠监测的 FastAPI 后端，提供报告生成、雷达日志入库、健康检查和微信云托管模板探活接口。

## 本地运行

```bash
cd /Users/kingveylee/ZJU/创客项目/code/backend
PYTHONPATH=src python3 -m uvicorn jianwei.api.main:app --host 127.0.0.1 --port 8000
```

常用接口：

- `GET /health`
- `GET /api/reports/demo`
- `GET /api/reports/{device_id}/{session_id}`
- `POST /api/radar/ingest-hex`
- `POST /api/count`

## 微信云托管部署

当前云托管信息：

- 环境 ID：`prod-d5gitgy083abca035`
- 服务名：`flask-akcx`
- 公网域名：`https://flask-akcx-279513-7-1451771121.sh.run.tcloudbase.com`

部署思路：

1. 将本目录后端代码放到云托管服务绑定的 Git 仓库中，替换默认 Flask 模板代码。
2. 保留 `Dockerfile` 和 `.dockerignore`，云托管构建时会启动 `uvicorn jianwei.api.main:app --host 0.0.0.0 --port 80`。
3. 使用微信云托管内置 MySQL；如果控制台没有自动注入连接变量，就在服务环境变量里补齐 MySQL 连接信息，不要把真实密码提交到 Git。
4. 部署完成后验证公网域名：

```bash
curl https://flask-akcx-279513-7-1451771121.sh.run.tcloudbase.com/health
curl https://flask-akcx-279513-7-1451771121.sh.run.tcloudbase.com/api/reports/demo
```

## 数据库环境变量

支持以下微信云托管 MySQL 环境变量：

```text
MYSQL_ADDRESS
MYSQL_USERNAME
MYSQL_PASSWORD
MYSQL_DATABASE
```

`MYSQL_DATABASE` 默认是模板创建的 `flask_demo`，本地可参考 `.env.example` 创建 `.env`。`.env` 已被忽略，不应提交。

## 测试

```bash
python3 -m pytest -q
```
