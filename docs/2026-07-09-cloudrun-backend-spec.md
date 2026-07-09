# 见微云托管后端替换规格

## 背景

微信云托管服务 `flask-akcx` 初始仓库来自 Flask 计数器模板。小程序侧已经配置为优先通过 `wx.cloud.callContainer` 访问该服务，并以公网域名作为调试与兜底通道。为了让云端真正返回见微睡眠监测报告，需要将模板应用替换为见微 FastAPI 后端。

## 目标

- 使用见微后端替换 Flask 模板代码。
- 保持云托管服务端口为 `80`。
- 提供小程序需要的报告接口 `GET /api/reports/demo`。
- 提供健康检查接口 `GET /health`，便于公网域名验证。
- 保留模板探活接口 `POST /api/count`，避免云托管示例调用直接失败。
- 数据库存储使用微信云托管内置 MySQL，数据库密码只通过云托管环境变量配置，不进入 Git 仓库。

## 接口

- `GET /health`：返回服务状态与存储状态。
- `GET /api/reports/demo`：返回演示睡眠报告。
- `GET /api/reports/{device_id}/{session_id}`：从事件存储读取指定会话并生成报告。
- `POST /api/radar/ingest-hex`：接收 R60ABD1 十六进制日志并入库。
- `POST /api/count`：兼容微信云托管模板的自增/清零/读取计数接口。

## 部署配置

云托管构建使用仓库根目录 `Dockerfile`：

- 基础镜像：`python:3.12-slim`
- 启动命令：`python -m uvicorn jianwei.api.main:app --host 0.0.0.0 --port 80`
- 暴露端口：`80`

数据库使用微信云托管 MySQL 环境变量：

- `MYSQL_ADDRESS`
- `MYSQL_USERNAME`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

`MYSQL_DATABASE` 默认使用模板数据库 `flask_demo`。未配置云托管 MySQL 时，服务会回退到本地 JSONL 存储，主要用于开发调试。

## 非目标

- 不在本次提交中配置或暴露真实数据库密码。
- 不改动小程序 UI。
- 不接入真实雷达硬件上报链路之外的新协议。
- 不保留额外的外部数据库适配。
