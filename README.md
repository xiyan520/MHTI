# MHTI - 媒体文件刮削与整理工具

<div align="center">

![Version](https://img.shields.io/github/v/release/sfgawrgarf/MHTI?color=blue)
![Python](https://img.shields.io/badge/Python-3.12+-green.svg)
![Vue](https://img.shields.io/badge/Vue-3.5-brightgreen.svg)
![License](https://img.shields.io/badge/license-MIT-orange.svg)

**自动从 TMDB 获取剧集元数据，智能整理媒体文件，支持日文动画与 `.strm` 媒体库**

[功能特性](#-功能特性) •
[快速开始](#-快速开始) •
[系统架构](#-系统架构) •
[API 文档](#-api-端点) •
[开发指南](#-开发指南)

</div>

---

## 📖 项目简介

MHTI 是一个全栈 Web 应用，专为媒体文件管理设计。它能够自动解析视频文件名，从 TMDB 获取元数据，生成 NFO 文件，并智能整理媒体库，兼容 Emby/Jellyfin 等媒体服务器。

除常规剧集外，MHTI 也面向日文动画文件名进行了优化：可识别常见字幕组标签、制作组标签、日文集数写法与 `.strm` 文件；可选的 OpenAI 兼容 AI 服务会在实际刮削流程中辅助清洗标题、生成 TMDB 检索别名并校验候选。

## ✨ 功能特性

| 功能模块 | 说明 |
|---------|------|
| 🎬 **文件名解析** | 智能解析标准、中文、日文动画等命名格式，自动提取剧名、季号和集号 |
| 🤖 **AI 辅助识别** | 支持 OpenAI 兼容接口，辅助清洗标题、生成检索别名并在 TMDB 候选中谨慎选择 |
| 🔍 **TMDB 集成** | 自动搜索匹配，获取剧集/电影元数据，核验季集存在性 |
| 📝 **NFO 生成** | 生成 Emby/Jellyfin 兼容的 NFO 文件 |
| 📁 **文件整理** | 支持复制/移动/硬链接/软链接四种模式 |
| ☁️ **115 网盘** | 扫码登录 115 网盘，支持 115→115/115→本地 整理 |
| 🖼️ **图片下载** | 自动下载海报、背景图、剧集缩略图 |
| 📺 **字幕关联** | 自动识别并关联同名字幕文件 |
| 👁️ **文件夹监控** | 实时/兼容/事件三种模式，支持本地与 115 网盘目录 |
| 🧾 **版本与去重** | 记录已成功整理的媒体身份，监控任务自动跳过已完成的同一源文件 |
| 🔗 **Emby 集成** | 媒体库冲突检测，避免重复 |
| 🔐 **安全认证** | JWT 认证，多会话管理 |
| 🌙 **主题切换** | 支持亮色/暗色主题 |

---

## 🏗️ 系统架构

### 整体架构图

```mermaid
graph TB
    subgraph Client["🌐 客户端"]
        Browser[浏览器]
    end

    subgraph Docker["🐳 Docker 容器"]
        subgraph Gateway["网关层"]
            Caddy[Caddy<br/>反向代理<br/>端口 8000]
        end

        subgraph Frontend["前端层"]
            Vue[Vue 3 SPA<br/>静态文件]
        end

        subgraph Backend["后端层"]
            FastAPI[FastAPI<br/>REST API]
            WebSocket[WebSocket<br/>实时通信]
        end

        subgraph Services["服务层"]
            ScraperSvc[ScraperService<br/>刮削编排]
            TMDBSvc[TMDBService<br/>元数据获取]
            AISvc[AIProviderService<br/>可选 AI 辅助]
            ParserSvc[ParserService<br/>文件名解析]
            NFOSvc[NFOService<br/>NFO生成]
            ImageSvc[ImageService<br/>图片下载]
            P115Svc[P115Service<br/>115 网盘]
            WatcherSvc[WatcherService<br/>文件监控]
            RenameSvc[RenameService<br/>文件整理]
            SchedulerSvc[SchedulerService<br/>定时任务]
        end

        subgraph Core["核心层"]
            Container[DI 容器]
            Database[(SQLite)]
            Auth[JWT 认证]
        end
    end

    subgraph External["🌍 外部服务"]
        TMDB[TMDB API]
        AI[OpenAI 兼容 API]
        Emby[Emby Server]
        P115["115 网盘<br/>生活事件 API"]
    end

    Browser --> Caddy
    Caddy --> Vue
    Caddy -->|/api/*| FastAPI
    Caddy -->|/ws| WebSocket
    FastAPI --> Services
    WebSocket --> Services
    Services --> Core
    ScraperSvc --> TMDBSvc
    ScraperSvc --> AISvc
    ScraperSvc --> ParserSvc
    ScraperSvc --> NFOSvc
    ScraperSvc --> ImageSvc
    ScraperSvc --> RenameSvc
    ScraperSvc --> P115Svc
    TMDBSvc --> TMDB
    AISvc --> AI
    Services --> Emby
    P115Svc --> P115
    WatcherSvc --> P115Svc
```

### 服务层设计

```mermaid
graph LR
    subgraph Orchestration["编排层"]
        Scraper[ScraperService]
    end

    subgraph Mixins["Mixin 模式"]
        Config[ScraperConfigMixin<br/>配置管理]
        Metadata[ScraperMetadataMixin<br/>元数据处理]
        Media[ScraperMediaMixin<br/>媒体文件处理]
    end

    subgraph CoreServices["核心服务"]
        Parser[ParserService]
        TMDB[TMDBService]
        NFO[NFOService]
        Image[ImageService]
        Rename[RenameService]
        Subtitle[SubtitleService]
        P115[P115Service<br/>115 网盘适配]
    end

    Scraper --> Config
    Scraper --> Metadata
    Scraper --> Media
    Config --> Parser
    Metadata --> TMDB
    Metadata --> NFO
    Media --> Image
    Media --> Subtitle
    Media --> Rename
    Media --> P115
```

---

## 🔄 业务流程

### 刮削工作流程

```mermaid
flowchart TD
    Start([开始]) --> Scan[扫描文件夹]
    Scan --> Filter{文件过滤}
    Filter -->|通过| Parse[解析文件名]
    Filter -->|过滤| Skip[跳过文件]

    Parse --> Extract[提取剧名/季/集]
    Extract --> Search[搜索 TMDB]
    Search --> AI{已启用 AI?}
    AI -->|是| Refine[清洗标题/检索别名/候选建议]
    AI -->|否| Match{匹配结果}
    Refine --> Match

    Match -->|自动匹配| GetDetails[获取详情]
    Match -->|需要选择| Manual[手动选择]
    Match -->|无结果| Failed[标记失败]

    Manual --> GetDetails
    GetDetails --> GenNFO[生成 NFO]
    GenNFO --> Organize[文件整理]

    Organize --> Mode{整理模式}
    Mode -->|复制| Copy[复制文件]
    Mode -->|移动| Move[移动文件]
    Mode -->|硬链接| HardLink[创建硬链接]
    Mode -->|软链接| SymLink[创建软链接]

    Copy --> Download[下载图片]
    Move --> Download
    HardLink --> Download
    SymLink --> Download

    Download --> Subtitle[处理字幕]
    Subtitle --> Record[记录历史]
    Record --> Success([完成])

    Failed --> Record
    Skip --> End([结束])
```

### 文件名解析流程

```mermaid
flowchart LR
    Input[原始文件名] --> Clean[清理垃圾信息]
    Clean --> Detect{检测格式}

    Detect -->|S01E01| Standard[标准解析器]
    Detect -->|第x集| Chinese[中文解析器]
    Detect -->|第x話| Japanese[日文解析器]

    Standard --> Extract[提取信息]
    Chinese --> Extract
    Japanese --> Extract

    Extract --> Output[剧名 + 季号 + 集号]
```

### 任务队列流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant API as API 层
    participant Queue as 任务队列
    participant Worker as 工作进程
    participant WS as WebSocket

    User->>API: 创建刮削任务
    API->>Queue: 添加到队列
    API-->>User: 返回任务 ID

    Queue->>Worker: 分发任务
    Worker->>WS: 推送进度
    WS-->>User: 实时更新

    Worker->>Worker: 执行刮削
    Worker->>WS: 推送结果
    WS-->>User: 显示结果
```

---

## 📁 项目结构

```
MHTI/
├── 📂 server/                    # Python 后端
│   ├── 📂 api/                   # API 路由层
│   │   ├── auth.py               # 认证接口
│   │   ├── files.py              # 文件操作
│   │   ├── scraper.py            # 刮削接口
│   │   ├── config.py             # 配置管理
│   │   ├── tmdb.py               # TMDB 代理
│   │   ├── watcher.py            # 文件监控
│   │   └── websocket.py          # WebSocket
│   ├── 📂 core/                  # 核心层
│   │   ├── container.py          # 依赖注入容器
│   │   ├── database.py           # 数据库连接
│   │   ├── auth.py               # 认证逻辑
│   │   ├── middleware.py         # 中间件
│   │   └── 📂 db/                # 数据库模块
│   │       ├── connection.py     # 连接池
│   │       └── schema.py         # 表结构
│   ├── 📂 services/              # 业务服务层
│   │   ├── ai_provider_service.py # OpenAI 兼容 AI 服务
│   │   ├── scraper_service.py    # 刮削编排器
│   │   ├── tmdb_service.py       # TMDB 服务
│   │   ├── parser_service.py     # 解析服务
│   │   ├── nfo_service.py        # NFO 生成
│   │   ├── image_service.py      # 图片下载
│   │   ├── rename_service.py     # 文件整理
│   │   ├── p115_service.py       # 115 网盘服务
│   │   ├── watcher_service.py    # 文件监控（本地 + 115）
│   │   ├── scheduler_service.py  # 定时任务
│   │   └── 📂 parsers/           # 解析器集合
│   │       ├── cleaner.py         # 发布标签、画质与副标题清洗
│   │       ├── episode_standard.py
│   │       ├── episode_chinese.py
│   │       └── episode_japanese.py
│   ├── 📂 models/                # 数据模型
│   │   ├── scraper.py            # 刮削模型
│   │   ├── tmdb.py               # TMDB 模型
│   │   ├── file.py               # 文件模型
│   │   ├── cloud_115.py          # 115 网盘模型
│   │   ├── storage.py            # 存储定位模型
│   │   └── ...
│   └── 📂 tests/                 # 单元测试
├── 📂 web/                       # Vue.js 前端
│   ├── 📂 src/
│   │   ├── 📂 api/               # API 客户端
│   │   ├── 📂 views/             # 页面视图
│   │   │   ├── HomePage.vue      # 首页
│   │   │   ├── ScanPage.vue      # 手动任务
│   │   │   ├── HistoryPage.vue   # 刮削记录
│   │   │   ├── FilesPage.vue     # 文件管理
│   │   │   └── SettingsPage.vue  # 设置页面
│   │   ├── 📂 components/        # 组件库
│   │   │   ├── 📂 common/        # 通用组件
│   │   │   ├── 📂 layout/        # 布局组件
│   │   │   ├── 📂 scan/          # 扫描组件
│   │   │   ├── 📂 scrape/        # 刮削组件
│   │   │   └── 📂 settings/      # 设置组件
│   │   ├── 📂 stores/            # Pinia 状态
│   │   │   ├── auth.ts           # 认证状态
│   │   │   ├── scraper.ts        # 刮削状态
│   │   │   └── theme.ts          # 主题状态
│   │   ├── 📂 composables/       # 组合式函数
│   │   ├── 📂 utils/             # 工具函数
│   │   └── 📂 router/            # 路由配置
│   └── package.json
├── 📂 data/                      # 数据目录
│   └── scraper.db                # SQLite 数据库
├── docker-compose.yml            # Docker 编排
├── Dockerfile                    # 多阶段构建
├── Caddyfile                     # Caddy 配置
└── pyproject.toml                # Python 依赖
```

---

## 🚀 快速开始

### Docker 部署（推荐）

```bash
# 克隆仓库
git clone https://github.com/sfgawrgarf/MHTI.git
cd MHTI

# 创建本地持久化、源媒体和整理输出目录
mkdir -p data media output

# 默认固定为当前发布版；升级时先修改为目标版本号
export MHTI_VERSION=2.1.3

# 拉取已发布镜像并启动服务（Docker Compose v2）
docker compose pull
docker compose up -d

# 查看日志
docker compose logs -f mhti

# 访问应用
# 主页: http://localhost:8000
# API 文档: http://localhost:8000/api/docs
```

如果系统仍使用旧版 Compose 命令，将上述 `docker compose` 替换为 `docker-compose` 即可。

### Docker Compose 配置

```yaml
services:
  mhti:
    image: ghcr.io/sfgawrgarf/mhti:${MHTI_VERSION:-2.1.3}
    container_name: mhti
    restart: unless-stopped
    ports:
      - "8000:8000"    # 主入口
    volumes:
      - ./data:/app/data              # 数据持久化
      - ./media:/media:ro             # 源媒体（只读）
      - ./output:/output              # 整理输出（可写）
    environment:
      - TZ=Asia/Shanghai
      - DATA_DIR=/app/data
      - MHTI_ALLOWED_MEDIA_ROOTS=/media,/output
```

生产环境可将 `./media` 和 `./output` 替换为宿主机绝对路径，例如 `/srv/media:/media:ro` 与 `/srv/mhti-output:/output`。文件移动、重命名、字幕处理和图片写入只允许发生在 `MHTI_ALLOWED_MEDIA_ROOTS` 列出的容器内目录；使用 `/incoming`、`/library` 或其他自定义挂载时，需要把相应容器路径加入这个逗号分隔的变量。TMDB 图片默认只允许从 `image.tmdb.org` 下载，如确需其他可信图片域名，可通过 `MHTI_ALLOWED_IMAGE_HOSTS` 显式配置。不要把 API Key 写入 Compose 文件，请在网页“设置 → AI 识别”中保存。

默认配置引用 GitHub Container Registry（GHCR）的当前稳定版 `2.1.3`，不会因新的 `latest` 镜像自动升级。升级前请先查看 Release，再在项目目录的 `.env` 写入目标版本，例如 `MHTI_VERSION=2.1.3`，然后执行 `docker compose pull && docker compose up -d`。如需回滚，只需将该值改回原版本并重新拉取启动。

2.1.0 起，独立元数据目录也必须包含在 `MHTI_ALLOWED_MEDIA_ROOTS` 中。覆盖文件需要目标磁盘有足够空间暂存完整的新文件；准备或替换失败时保留原目标及源文件。字幕跟随视频整理模式，遇到同名目标不会自动覆盖。

2.1.1 起，历史记录手动处理和重试会先加入后台队列，“已加入队列”不代表文件已处理完成，请在记录页查看最终结果。超时或取消会等待文件操作停止或完成收尾；已完成写入的文件不会自动回滚，重试前请核对日志和目标文件。

2.1.2 起，手动任务和单文件刮削任务可以真正停止；手动任务页同时展示后台队列、实际运行任务、并发限制、最长等待时间及文件 I/O 状态。取消运行中任务会等待文件操作安全收尾，已经完成的文件写入不会自动回滚。

2.1.3 修复 115 网盘目录访问返回 500，并加强任务状态一致性、队列持久化性能、数据库查询索引及 watcher 生命周期管理。升级时保留现有配置、任务、历史记录和登录状态，数据库索引会在启动阶段自动补齐。

当前开发中的 AI 辅助代码尚未发布时，使用本地构建覆盖文件测试：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

推送 `v*.*.*` 发布标签后，仓库的 Release 工作流会把包含 AI 辅助功能的镜像推送至 GHCR；其他用户即可拉取该版本。

### 开发模式

```bash
# 后端开发（方案 A：虚拟环境）
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
python -m pip install -r requirements.txt
python run_server.py --host 0.0.0.0 --port 8000 --reload

# 后端开发（方案 B：不使用虚拟环境）
python -m pip install --target .local_packages -r requirements.txt
python run_server.py --host 0.0.0.0 --port 8000 --reload

# 前端开发
cd web
npm install
npm run dev
```

说明：

- 方案 A 使用当前激活的虚拟环境。
- 方案 B 会把后端依赖安装到仓库根目录 `.local_packages/`，不需要创建或激活虚拟环境。
- `run_server.py` 会优先使用当前 Python 环境；若当前环境缺依赖，再回退到 `.local_packages/`。
- 为兼容早期说明，若仓库里已经存在 `.python_packages/`，启动脚本也会继续尝试它。
- 若只需本机访问，后端可改用 `--host 127.0.0.1`。

---

## 🌐 API 端点

### 认证模块 `/api/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/login` | 用户登录 |
| POST | `/logout` | 用户登出 |
| POST | `/register` | 注册账户 |
| POST | `/refresh` | 刷新令牌 |
| GET | `/status` | 认证状态 |
| GET | `/sessions` | 会话列表 |

### 文件模块 `/api/files`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/scan` | 扫描文件夹 |
| GET | `/browse` | 浏览目录 |

### 刮削模块 `/api/scraper`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/scrape` | 执行刮削 |
| POST | `/scrape-by-id` | 按 TMDB ID 刮削 |
| GET | `/status` | 刮削状态 |

### 配置模块 `/api/config`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/PUT | `/tmdb` | TMDB 配置 |
| GET/PUT/DELETE | `/ai/config` | AI 辅助识别配置 |
| GET/PUT | `/proxy` | 代理设置 |
| GET/PUT | `/organize` | 整理配置 |
| GET/PUT | `/download` | 下载设置 |
| GET/PUT | `/nfo` | NFO 设置 |
| GET | `/115` | 115 登录状态 |
| GET | `/115/devices` | 115 登录设备列表 |
| POST | `/115/login/qrcode` | 115 扫码登录 |
| GET | `/115/login/status` | 115 登录状态轮询 |
| DELETE | `/115/login` | 115 退出登录 |

### 其他模块

| 路径 | 说明 |
|------|------|
| `/api/tmdb/*` | TMDB 代理接口 |
| `/api/ai/recognize` | 单文件 AI 辅助识别预览 |
| `/api/ai/versions/preview` | 媒体版本策略预览 |
| `/api/ai/versions/record` | 记录成功整理的媒体版本 |
| `/api/emby/*` | Emby 集成 |
| `/api/watcher/*` | 文件夹监控（本地 + 115） |
| `/api/history/*` | 历史记录 |
| `/api/manual-jobs/*` | 手动任务管理 |
| `/api/scheduler/*` | 定时任务 |
| `/ws` | WebSocket 实时通信 |
| `/health` | 健康检查 |

---

## 🎨 前端页面

| 路径 | 页面 | 功能 |
|------|------|------|
| `/` | 首页 | 统计概览、快捷入口 |
| `/login` | 登录 | 用户认证 |
| `/scan` | 手动任务 | 创建刮削任务 |
| `/history` | 刮削记录 | 查看历史记录 |
| `/files` | 文件管理 | 浏览媒体文件 |
| `/settings` | 设置 | 系统配置（含 115 网盘登录） |
| `/security` | 安全设置 | 账户管理 |

---

## 🛠️ 技术栈

### 后端

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 运行时 |
| FastAPI | 0.109+ | Web 框架 |
| Uvicorn | 0.27+ | ASGI 服务器 |
| aiosqlite | 0.19+ | 异步 SQLite |
| httpx | 0.27+ | HTTP 客户端 |
| watchdog | 4.0+ | 文件监控 |
| python-jose | 3.3+ | JWT 认证 |
| Pydantic | 2.6+ | 数据验证 |
| p115client | 0.0.9.6.5.1 | 115 网盘客户端 |

### 前端

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue | 3.5+ | 前端框架 |
| TypeScript | 5.9+ | 类型系统 |
| Vite | 7+ | 构建工具 |
| Pinia | 3.0+ | 状态管理 |
| Vue Router | 4.6+ | 路由管理 |
| Naive UI | 2.43+ | UI 组件库 |
| Axios | 1.13+ | HTTP 客户端 |

### 部署

| 技术 | 用途 |
|------|------|
| Docker | 容器化 |
| Caddy | 反向代理 |
| SQLite | 数据存储 |

---

## 📊 数据库设计

### 核心表结构

```mermaid
erDiagram
    config {
        string key PK
        text value
        datetime updated_at
    }

    admin {
        int id PK
        string username UK
        string password_hash
        datetime created_at
    }

    sessions {
        string id PK
        int user_id FK
        string token
        datetime expires_at
        datetime created_at
    }

    history_records {
        string id PK
        string file_path
        string status
        string tmdb_id
        json details
        datetime created_at
    }

    scraped_files {
        string id PK
        string source_path
        string target_path
        int file_size
        int tmdb_id
        int season
        int episode
        datetime scraped_at
    }

    manual_jobs {
        int id PK
        string name
        string source_dir
        string output_dir
        string status
        datetime created_at
    }

    scrape_jobs {
        string id PK
        string file_path
        string status
        int source_id FK
        datetime created_at
    }

    watched_folders {
        int id PK
        string path
        string output_dir
        bool enabled
        datetime created_at
    }

    admin ||--o{ sessions : has
    manual_jobs ||--o{ scrape_jobs : contains
    scrape_jobs ||--o| history_records : creates
    history_records ||--o| scraped_files : records
```

---

## ⚙️ 配置说明

### 整理模式

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `copy` | 复制文件 | 保留原文件 |
| `move` | 移动文件 | 节省空间 |
| `hardlink` | 硬链接 | 同分区节省空间（仅本地） |
| `symlink` | 软链接 | 跨分区引用（仅本地） |

### 115 网盘支持

| 功能 | 说明 |
|------|------|
| 扫码登录 | 在设置页扫码登录 115 网盘，cookies 加密存储 |
| 文件浏览 | 浏览 115 网盘目录，选择源/目标目录 |
| 115→115 整理 | 视频在 115 内整理（复制/移动+改名），NFO/图片留本地 |
| 115→本地整理 | 从 115 下载视频到本地后整理 |
| 监控模式 | 兼容模式（全量轮询）/ 事件模式（生活事件 API 增量监控） |
| TMDB 核验 | 整理前核验 TMDB 中季/集存在性，避免错误重命名 |

### 本地 `.strm` 与日文动画

将本地媒体目录挂载到容器内的 `/media`，将整理结果挂载到 `/output`。`.strm` 与常见视频文件会一并参与扫描；推荐使用 `copy` 模式，以保留原始 `.strm` 文件和原有目录结构。

日文动画文件名会先清理字幕组、制作组、日期、分辨率、语言及编码标签，再识别剧名与集数。支持的常见格式包括：

- `第N話`、`第N章`、`#N`、`Vol.N`；
- `N突き目`、`上巻`、`下巻`、前/后篇；
- `OVA日文标题`（按常见 TMDB 第一季结构处理）；
- `标题 1［副标题］` 这类全角方括号副标题格式。

解析结果仍会由 TMDB 的季/集信息核验；无法安全确定时会保留为待人工确认，而不是写入不确定的元数据。

### AI 辅助识别与多版本管理

在“设置 → AI 识别”中填写 OpenAI 兼容接口地址、模型和 API Key 后，AI 会在正常刮削中参与以下步骤：

1. 根据原始文件名和解析结果生成干净标题及 1–4 个 TMDB 检索别名；
2. 在 TMDB 候选中给出标题、季、集与候选建议；
3. 仅在达到配置的置信度阈值且通过 TMDB 季集核验时自动采用；低置信度结果保留人工选择。

AI 只提供结构化识别建议，不会自行删除、覆盖或移动源媒体。已成功整理的源文件会记录逻辑媒体身份与版本信息，监控任务会跳过同一源文件的重复投递。

若要从头重新刮削某一批文件，请先在网页删除对应记录；如果输出目录中已有同名整理结果，还需按需要清理该批输出文件，避免目标文件冲突。源目录保持只读时不会受影响。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATA_DIR` | `/app/data` | 数据目录 |
| `TZ` | `Asia/Shanghai` | 时区 |

---

## 🧪 测试

```bash
# 安装运行与测试依赖（开发环境）
python -m pip install -r requirements.txt pytest pytest-cov

# 运行所有测试
python -m pytest

# 运行覆盖率测试
python -m pytest --cov=server --cov-report=html

# 运行特定测试
python -m pytest server/tests/services/test_parser_service.py -v
```

---

## 📝 开发规范

### 代码风格

- **Python**: Ruff + Black (line-length=100)
- **TypeScript**: ESLint + Prettier
- **类型注解**: 严格模式

### 命名约定

| 语言 | 风格 |
|------|------|
| Python | snake_case |
| TypeScript | camelCase |
| Vue 组件 | PascalCase |

### 提交规范

```
<type>(<scope>): <description>

类型:
- feat: 新功能
- fix: 修复
- docs: 文档
- style: 格式
- refactor: 重构
- test: 测试
- chore: 构建/工具
```

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'feat: Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

---

<div align="center">

**Made with ❤️ for media enthusiasts**

</div>


## 赞助作者

