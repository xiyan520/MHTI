# MHTI 架构说明

> 这份文档是代码注释里 `ARCHITECTURE.md 第 N 节` 的落点。改动分层、模块边界或刮削主链路时，请同步更新对应小节。

## 1. 全景

```
浏览器 → Caddy(:8000) → { Vue 3 SPA(/app/static) | FastAPI(:8001) /api/*、/ws }
                                        ↓
                     api → application → domain → infrastructure
                                        ↓
                              SQLite(WAL) / TMDB / 115 / Emby
```

本地开发没有 Caddy：Vite 跑 3000 端口，把 `/api` 与 `/ws` 代理到 8000。

## 2. 后端四层

| 层 | 目录 | 职责 | 禁止 |
| --- | --- | --- | --- |
| 接入 | `server/api` | 路由、鉴权依赖、异常与请求中间件、协议转换 | 写业务分支 |
| 应用 | `server/application` | 用例编排、队列、历史记录、监控 | 依赖 `api` |
| 能力 | `server/domain` | 解析、TMDB、产物、集成、身份、系统能力 | 依赖 `api/application/bootstrap` |
| 数据 | `server/infrastructure` | 仓储、连接池、DI 容器、实时通道、安全 | 依赖 `api/application/domain` |

共享内核 `server/common` 不依赖任何 `server.*`；`server/models` 是纯数据模型。

这几条不是口头约定：`server/tests/test_architecture.py` 用 AST 扫全仓 import（含函数内导入，
跳过 `TYPE_CHECKING`），违规即测试失败；它同时断言旧 `core/`、`services/` 目录已删除。

### 组合根

`server/bootstrap.py` 负责装配：无参服务走 `_SERVICE_REGISTRY` 反射构造并缓存单例，
带依赖的服务（TMDB / 115 / Scraper / Image）走显式工厂。`server/api/deps.py` 只做转发，
router 统一从这里取依赖，方便测试覆盖。

## 3. 刮削主链路

```
解析文件名 → R18 权限预检 → 搜索 TMDB → 选剧 → 取详情 → 自动修正季号
        → 核验季/集 → 落库匹配 → Emby 冲突 → 生成 NFO → 整理搬运 → 图片 → 字幕
```

- 入口：`application/scraping/service.py` 的 `scrape_file`（自动搜索）与 `scrape_by_id`（指定 TMDB ID）。
- 协作者：`config_resolver`（配置检查）、`metadata_resolver`（NFO/元数据）、`media_pipeline`（图片/字幕/Emby）、
  `output_writer`（本地与 115 输出）、`p115_storage_provider`（115 存储适配）。
- 核验在搬运之前：TMDB 里季/集不存在就停在 `pending_action`，绝不先重命名再回滚。
- 匹配一确定就落库（`on_match_resolved`），超时或失败后「重试刮削」直接沿用，不必重搜。

### 状态机

任务：`pending → running → success | failed | timeout | pending_action`。
`pending_action` 是补偿态：多结果、季集缺失、文件冲突、Emby 冲突都收敛到这里，
用户在记录页提交后经 `PUT /api/history/{id}/resolve` 恢复上下文重跑。

进程重启后内存队列为空，`recover_pending_jobs` 会把 `pending` 重新入队、把 `running` 收尾为
`failed`（半搬运状态不自动重放）。

手动任务是「扫描 → 投递」的批次：投递完成仍保持 `running`，`refresh_progress` 按
`history_records` 重算成功 / 失败 / 跳过，等子任务全部到达终态且计数收满才收尾；
重启时已投递的批次不再判死，交给子任务回写推进。

## 4. 前端六域

```
web/src/
  config/    常量与运行期配置
  shared/    api 客户端、基础组件、composable、设计令牌（唯一跨域复用层）
  layouts/   壳层：顶栏、命令面板、移动端 TabBar
  router/    路由表、菜单元数据、守卫
  stores/    Pinia（auth / theme）
  modules/   auth · home · scrape · history · library · settings
```

每个业务域固定切片：`api.ts / types.ts / hooks/ / components/ / views/ / routes.ts / index.ts`；
跨域只用对方 `index.ts` 的白名单导出，禁止深路径导入。

### 约定

- 导航单一真源：路由 `meta`（`title/icon/order/menu/tab/keywords`）派生顶部导航、TabBar、命令面板，禁止硬编码菜单。
- 端点字符串只出现在 `api.ts` 与 `shared/api/*`。
- 样式一律引用 `shared/styles/design-tokens.css` 的令牌，禁止硬编码颜色/间距/字号。
- 页面版式统一 `PageContainer` 四段式；列表页统一「单卡框架」。

## 5. 数据与实时

14 张表：`config / auth_config / admin / sessions / login_history / login_attempts / manual_jobs /
scheduled_tasks / history_records / scrape_jobs / scraped_files / watched_folders / logs / log_config`。
SQLite 走连接池 + WAL（`infrastructure/db/connection.py`），所有 SQL 收敛在 `infrastructure/repositories`。

实时两条通道：

- WebSocket `/ws`：任务进度、待处理通知（token 走查询参数）。
- SSE `/api/history/{id}/logs/stream`：单条记录的刮削日志流。

## 6. 安全基线

- 口令：PBKDF2-HMAC-SHA256 + 随机盐，默认 60 万轮；哈希自带算法与参数（`pbkdf2_sha256$…`），旧格式登录成功时就地升级（`domain/identity/auth_service.py`）。
- 令牌：access 15 分钟；refresh 每次刷新轮换，旧值仅保留 `REFRESH_GRACE_SECONDS` 宽限。
- 敏感配置（TMDB Token、115 Cookie、Emby Key）Fernet 加密入库，密钥文件在 `DATA_DIR/.secret_key`。
- `DATA_DIR` 环境变量统一决定 SQLite、密钥、日志、115 客户端缓存的位置。

## 7. 本地运行与质量门禁

```bash
pip install -r requirements-dev.txt
ruff check server        # 静态检查
mypy                     # 渐进式类型门禁（pyproject 里锁定目录）
pytest server/tests -q   # 单元测试
cd web && npm ci && npm run build:check
```

CI 跑同一套命令；任何一步失败都会让 PR 变红（历史上有过 `|| echo 测试完成` 吞掉失败，已移除）。
