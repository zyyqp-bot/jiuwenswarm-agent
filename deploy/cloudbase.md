# 腾讯云 CloudBase 云托管部署

本文件对应 `workbench-prod` 环境。目标：一个可直接打开的 HTTPS 链接，评委登录后提交任务、看任务图、点开证据、下载报告。

## 1. 固定约束

| 约束 | 值 | 原因 |
|---|---|---|
| 实例数 | **单实例**，关闭弹性伸缩（最小=最大=1） | 运行注册表、并发计数和 SQLite 都在进程内；两个实例会各算各的 |
| 端口 | 8000 | 镜像 `EXPOSE 8000`，`CMD` 监听 0.0.0.0:8000 |
| 持久化卷 | `/data` | `WORKBENCH_DATA_DIR=/data`；运行快照、报告、语料库都在这里 |
| 公开上传 | **不上传、不提供入口** | 只暴露预置工作区；导入语料由运维在容器内执行 |
| Worker 数 | 1 | 同上；不要用 `--workers N` 覆盖 `CMD` |

## 2. 部署步骤

1. 云托管 → 新建服务 → 代码仓库（GitHub），仓库 `zyyqp-bot/jiuwenswarm-agent`，分支 `main`。
2. Dockerfile 路径填仓库根目录的 `Dockerfile`（多阶段：Node 构建 SPA，Python 运行 API）。
3. 端口填 8000；实例规格 1 核 2 GB；实例数固定 1，关闭自动扩缩容。
4. 挂载持久化存储到 `/data`（首次部署后不要换路径，否则已有运行和语料会“消失”）。
5. 环境变量按 [deploy/.env.example](.env.example) 逐项填写。密钥只在控制台保存，不写进仓库。
6. 部署完成后访问默认域名，应看到登录页；`/api/health` 应返回 `{"status":"ok","writable":true}`。

## 3. 导入语料（一次性，运维执行）

镜像**不包含**真实语料，公开政策文件在构建前放到 `corpus/ai-governance/`，随镜像进入 `/app/corpus`。部署后在云托管控制台的容器终端执行：

```bash
python scripts/import_corpus.py \
  --data-dir /data --corpus ai-governance --scope public \
  --source /app/corpus/ai-governance
```

输出 `{"documents": n, "chunks": n, "failures": []}` 即成功。重复执行会按内容哈希替换旧版本，不会重复计块。导入完成前，运行仍可提交，但成员没有检索工具，引用校验会让结果失败——这不是部署故障。

## 4. 部署后验证清单

- [ ] `GET /api/health` 返回 `writable: true`（卷可写）
- [ ] 浏览器登录成功，刷新后仍在线（HttpOnly Cookie 通过网关）
- [ ] 提交一次任务，任务图出现两个节点并变绿
- [ ] 页面能持续收到进度（SSE 不被网关缓冲；服务端已发 `X-Accel-Buffering: no`）
- [ ] 容器能出网调用模型（运行进入 `streaming` 而不是立即 `failed`）
- [ ] 运行结束后报告面板有内容，`/api/runs/{id}/report.md` 可下载
- [ ] 关闭浏览器再打开，实例未休眠（常驻实例数 = 1，无冷启动）
- [ ] 另一个浏览器会话看不到前一个会话的运行（会话隔离）

`scripts/smoke_check.py` 把上述关键路径串起来（健康 → 登录 → 真实运行 → 首帧 → 终止 → 报告）：

```bash
python scripts/smoke_check.py --base-url https://<默认域名> \
  --user judge1 --password '<控制台里配置的密码>'
```

脚本不打印密码和 Cookie。

## 5. 排障

| 现象 | 检查 |
|---|---|
| 容器反复重启 | 日志里找 `SESSION_SECRET must be at least 32` 或 `WORKBENCH_USERS entries must be` |
| 登录 401 | 控制台环境变量是否有多余空格；改完要重新部署 |
| 运行一直 `starting` | 看子进程日志：`upstream` 下载是否成功、`PYTHONPATH` 是否两个目录都在 |
| SSE 只收到一次 | 网关是否缓冲响应；确认走的是默认域名而非静态托管 |
| 报告 404 | 运行尚未终止，或 `/data` 卷未挂载导致写到了容器临时层 |
