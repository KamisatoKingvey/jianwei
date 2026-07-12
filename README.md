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

ESP32 采样上报（与 Java 原型的 RadarData JSON 完全兼容）：

- `POST /api/radar/data` — 单条上报
- `POST /api/radar/batch` — 批量上报（固件每 30 秒推一批）
- 时间戳为 epoch 毫秒时直接使用；为开机毫秒（未 NTP 同步）时用服务端接收时间锚定，并按批内毫秒差还原相对间隔
- 设备首次上报自动注册并生成 6 位绑定码；一旦设备登记 secret，上报必须带 `X-Device-Secret` 头

设备绑定（openid 由云托管 `callContainer` 自动注入 `X-WX-OPENID`）：

- `POST /api/devices/register` — 登记设备/设置 secret（配置 `JIANWEI_ADMIN_KEY` 后需带 `X-Admin-Key`）
- `POST /api/devices/bind` — 用绑定码把设备绑到当前微信账号
- `GET /api/devices/mine` — 我绑定的设备及最新状态
- `GET /api/devices/{device_id}/status` — 设备在线状态与最新采样

睡眠报告与告警：

- `GET /api/reports/device/{device_id}/latest` — 最近一次监测会话的报告（含 CO2/温湿度环境汇总）
- `GET /api/reports/device/{device_id}/nights?days=7` — 按会话切分的历史报告
- `GET /api/alerts/device/{device_id}`、`GET /api/alerts/mine` — 告警记录
- 入库时实时检测：疑似呼吸中断、呼吸过低、夜间离床（同类型告警 30 分钟冷却）

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

其他可选环境变量：

```text
JIANWEI_ADMIN_KEY            # 设置后 /api/devices/register 需带 X-Admin-Key
WX_SUBSCRIBE_TEMPLATE_ID     # 订阅消息模板 ID，不配则告警只入库不推送
WX_SUBSCRIBE_MESSAGE_KEY     # 模板内容字段名，默认 thing1
WX_SUBSCRIBE_TIME_KEY        # 模板时间字段名，默认 time2
WX_SUBSCRIBE_PAGE            # 点击消息跳转页，默认 pages/dashboard/dashboard
```

## 见微睡眠助手（agent）

基于 claude-agent-sdk（官方 harness，内置系统提示词原样保留、仅 append 见微约束），
模型走 Anthropic 兼容协议，默认指向 MiniMax：

```text
LLM_API_KEY                  # 必填，不配则助手整体禁用（503），核心功能不受影响
LLM_BASE_URL                 # 默认 https://api.minimaxi.com/anthropic
LLM_MODEL                    # 默认 MiniMax-M3
JIANWEI_AGENT_DAILY_LIMIT    # 每用户每日对话上限，默认 30
JIANWEI_AGENT_MAX_TURNS      # agent 单次最大轮数，默认 8
```

接口：

- `POST /api/agent/chat` — 多轮对话（openid 走 callContainer 注入；回复末尾强制拼接免责声明）
- `GET /api/agent/conversations/{id}` — 拉取对话历史（仅本人）
- `GET /api/agent/report-insights/{device_id}` — 晨报 AI 解读，按会话缓存；agent 不可用时回落规则摘要

agent 只有 5 个只读数据工具（设备列表/最新报告/多晚趋势/实时状态/告警），
全部按 openid 校验设备归属；内置 Bash/文件读写等工具全部禁用。

本地未配置 MySQL 时，采样/设备/告警分别落在 `data/samples.jsonl`、`data/devices.json`、`data/alerts.jsonl`；配置 MySQL 后自动建表 `radar_samples`、`devices`、`user_devices`、`alerts`。

## 测试

```bash
python3 -m pytest -q
```
