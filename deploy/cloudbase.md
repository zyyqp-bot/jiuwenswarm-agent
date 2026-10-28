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

## 2. 从占位镜像切换到真实镜像

`workbench-prod` 目前跑的是用来验证平台的 nginx 占位镜像（端口 80）。切换时：

1. 同一个服务、同一个 Git 分支，Dockerfile 仍是仓库根目录的 `Dockerfile`；
2. **把容器端口从 80 改成 8000**（新镜像的 `EXPOSE`/`CMD` 是 8000，网关按这个端口转发）；
3. 补齐环境变量、挂载 `/data`、实例数固定 1，再点“重新部署”。

构建时镜像会从 GitHub 下载两个固定提交的源码。若构建日志显示 `curl: (28)` 或连接重置，用构建参数把地址换成可达镜像（CloudBase 构建参数或 Dockerfile 默认值）：

```text
AGENT_CORE_ARCHIVE=<agent-core 同提交 tar.gz 的可达地址>
JIUWENSWARM_ARCHIVE=<jiuwenswarm 同提交 tar.gz 的可达地址>
```

两个 URL 必须指向 `source-lock.json` 里的同一提交；不要改成移动分支。`requirements-frozen.txt` 还包含一个 `gitcode.com` 的 `gateway-protocol` 依赖，构建机需能访问该域名。

## 3. 部署步骤

1. 云托管 → 新建服务 → 代码仓库（GitHub），仓库 `zyyqp-bot/jiuwenswarm-agent`，分支 `main`。
2. Dockerfile 路径填仓库根目录的 `Dockerfile`（多阶段：Node 构建 SPA，Python 运行 API）。
3. 端口填 8000；实例规格建议 **2 核 4 GB**（每个运行会拉起一个 Python 子进程并加载完整框架；1 核 2 GB 时把 `WORKBENCH_MAX_CONCURRENT_RUNS` 设为 1）；实例数固定 1，关闭自动扩缩容。
4. 挂载持久化存储到 `/data`（首次部署后不要换路径，否则已有运行和语料会“消失”）。
5. 环境变量按 [deploy/.env.example](.env.example) 逐项填写。密钥只在控制台保存，不写进仓库。`SESSION_SECRET` 用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成；账号密码不要保留模板里的 `replace-me`，生产会拒绝启动。
6. 部署完成后访问默认域名，应看到登录页；`/api/health` 应返回 `{"status":"ok","writable":true}`。

## 4. 导入语料（一次性，运维执行）

镜像**已包含** `corpus/` 下三个工作区的公开文件（`ai-governance` 6 份法规、`education-policy` 19 份教育政策、`medical-ai-compliance` 8 份医疗健康法规，来源见 [corpus/SOURCES.md](../corpus/SOURCES.md)），随镜像进入 `/app/corpus`。**首次启动会自动建立索引**：每个工作区在 `/data/corpora/<corpus>.db` 不存在时各自导入，互不覆盖（设置 `WORKBENCH_SEED_CORPUS=0` 可关闭）。只有新增资料或重建索引时才需要手动执行：

```bash
python scripts/import_corpus.py \
  --data-dir /data --corpus ai-governance --scope public \
  --source /app/corpus/ai-governance
```

输出 `{"documents": 6, "chunks": 92, "failures": []}`（AI 语料离线实测值；教育语料为 9 文档 / 173 块）即成功。重复执行会按内容哈希替换旧版本，不会重复计块。新增资料需要先提交 Git、重新部署，再执行一次导入（首次启动的自动导入只在对应 corpus 的数据库不存在时触发）。导入完成前，运行仍可提交，但成员没有检索工具，引用校验会让结果失败——这不是部署故障。

## 5. 部署后验证清单

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

## 6. 排障

| 现象 | 检查 |
|---|---|
| 容器反复重启 | 日志里找 `SESSION_SECRET must be at least 32`、`placeholder`、`MODEL_BASE_URL` 或 `WORKBENCH_USERS` |
| 启动即报 `another workbench instance` | 实例数不是 1，或上次进程刚被杀（锁 20 秒后自动过期）；确认只有一个实例 |
| 启动报 `PermissionError` / 写 `/data` 失败 | 卷的属主不是容器用户（uid 10001）；在 CloudBase 调整卷权限或换一个可写目录 |
| 登录 401 | 控制台环境变量是否有多余空格；改完要重新部署 |
| 运行一直 `starting` | 看子进程日志：`upstream` 下载是否成功、`PYTHONPATH` 是否两个目录都在 |
| SSE 只收到一次 | 网关是否缓冲响应；确认走的是默认域名而非静态托管 |
| 报告 404 | 运行尚未终止，或 `/data` 卷未挂载导致写到了容器临时层 |

## 8. 默认域名的访问提示中间页

CloudBase 会对 `*.run.tcloudbase.com` 等默认域名的浏览器导航请求展示“页面访问提示”，访客点“确定访问”后写入 `cloudbase_confirm_domain_access` Cookie 并进入应用；API 请求不受影响。

平台文档写明，移除中间页的唯一合规途径是绑定**已完成 ICP 备案**的自定义域名。比赛期间若没有备案域名，保留该提示，并在提交说明中告诉评委先点一次“确定访问”。
