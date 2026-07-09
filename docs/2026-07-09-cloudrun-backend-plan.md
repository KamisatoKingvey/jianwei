# 见微云托管后端替换计划

## 实施步骤

1. 克隆微信云托管绑定仓库。
2. 将 Flask 模板替换为见微 FastAPI 后端代码。
3. 保留云托管 80 端口配置，复用云托管内置 MySQL 连接变量。
4. 添加 `.dockerignore` 与 `.gitignore`，排除 `.env`、缓存、测试构建内容和运行期 JSONL 数据。
5. 添加规格文档与实施计划文档。
6. 运行后端测试，确认接口、算法、存储和部署入口可用。
7. 检查提交内容中没有真实密码。
8. 提交并推送到远端 `master`，触发微信云托管流水线。

## 验证

- `python3 -m pytest -q`
- `rg "<known-secret-patterns>" . --glob '!.git/**'`
- 部署后验证：
  - `GET https://flask-akcx-279513-7-1451771121.sh.run.tcloudbase.com/health`
  - `GET https://flask-akcx-279513-7-1451771121.sh.run.tcloudbase.com/api/reports/demo`

## 回滚

如果云托管构建失败，可以在 GitHub 仓库中回退本次提交，或将服务重新绑定到上一版模板提交。
