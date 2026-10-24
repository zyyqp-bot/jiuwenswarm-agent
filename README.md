# JiuwenSwarm 证据工作台

面向公众的在线资料研读工作台：两名固定成员（Research / Engineering）在原生任务 DAG 上分工，每条结论都引用检索到的公开政策原文，运行结束后生成可下载的带引用 Markdown 报告。登录后才能使用；公开链接不提供上传入口。

需求、源码核对事实、架构决策与验收记录在 [PROJECT.md](PROJECT.md)；协作规则见 [AGENTS.md](AGENTS.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `app/` | 工作台应用：API、会话、运行服务、任务桥、计划器、检索工具、报告 |
| `web/` | Vite + React SPA（登录、任务图、证据抽屉、报告） |
| `deploy/` | 环境变量模板与 CloudBase 部署指南 |
| `corpus/` | 垂直语料（公开政策/法规），按工作区分目录 |
| `p0a/`…`p1/` | 离线与真实回合的验证脚本，每个脚本写一份 `*_results.json` |
| `upstream/` | 官方源码 checkout（gitignored，按 `source-lock.json` 复现） |
| `scripts/` | 运维命令：导入语料、部署后冒烟检查 |

## 本地运行

前置：Python 3.12、Node 24、两个官方仓库按 [PROJECT.md §13](PROJECT.md) 的命令拉取到 `upstream/`。

```powershell
# Windows PowerShell；Git Bash 用 `:` 分隔 PYTHONPATH
$env:PYTHONPATH = "$PWD\upstream\agent-core;$PWD\upstream\jiuwenswarm"

# 开发模式：不设 SESSION_SECRET 时会生成临时密钥
$env:WORKBENCH_ENV = "development"
$env:WORKBENCH_USERS = "judge:change-me"
$env:WORKBENCH_DATA_DIR = "$PWD\data"
$env:MODEL_BASE_URL = "https://api.deepseek.com/v1"
$env:MODEL_API_KEY = "<provider key>"
$env:MODEL_NAME = "deepseek-chat"

.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。前端已构建时由同一进程提供；开发前端时：

```bash
cd web
npm ci
npm run dev   # /api 代理到 127.0.0.1:8000
```

`npm run build` 生成 `web/dist`，API 进程自动提供（同一来源，Cookie 不需要 CORS）。

## 导入语料

仓库已内置 6 份公开法规原文（生成式 AI 暂行办法、内容标识办法、算法推荐规定、深度合成规定、个人信息保护法、数据安全法），逐份来源与条数见 [corpus/SOURCES.md](corpus/SOURCES.md)。

公开链接不接受上传。新增资料先放进 `corpus/<workspace>/`（支持 `.txt`/`.md`/`.pdf`），再由运维执行：

```bash
PYTHONPATH=upstream/agent-core:upstream/jiuwenswarm \
.venv/bin/python scripts/import_corpus.py \
  --data-dir data --corpus ai-governance --scope public \
  --source corpus/ai-governance
```

输出 `{"documents": n, "chunks": n, "failures": []}` 即成功；重复导入按内容哈希替换旧版本。没有语料时运行仍可提交，但成员没有检索工具，引用校验会让结果失败。

## 构建镜像与部署

```bash
docker build -t workbench:local .
docker run --rm -p 8000:8000 -v workbench-data:/data \
  --env-file deploy/.env.local workbench:local
```

镜像从 Git 仓库独立构建：`upstream/` 被 gitignore，构建时按 `source-lock.json` 的两个提交从 GitHub 下载，不依赖本机 checkout。单实例、`/data` 卷、无公开上传的 CloudBase 步骤与部署后清单见 [deploy/cloudbase.md](deploy/cloudbase.md)。

部署后从本机跑一次真实链路：

```bash
.venv/Scripts/python.exe scripts/smoke_check.py \
  --base-url https://<默认域名> --user judge1 --password '<密码>'
```

脚本依次检查健康、登录、真实运行、首个 SSE 帧、终止状态与报告，不打印密码或 Cookie。

## 验证

所有检查脚本自带结果文件，退出码非零即失败。全量离线套件：

```bash
export PYTHONPATH="$PWD/upstream/agent-core:$PWD/upstream/jiuwenswarm"
for f in p0a/*_check.py p0b/*_check.py p0c/*_check.py p0d/*_check.py \
         p0e/*_check.py p1/*_check.py; do
  case "$f" in *live_*) continue;; esac
  .venv/bin/python "$f" || echo "FAILED: $f"
done
```

`p1/live_*.py` 需要真实模型凭据，不在离线套件内。`p1/workbench_e2e_check.py` 用假 supervisor 串起登录→运行→SSE→任务板→检索→报告→会话隔离，结果文件里记 `live_run: "not_run"`；真实云端运行仍需部署后由 `smoke_check.py` 验证。
