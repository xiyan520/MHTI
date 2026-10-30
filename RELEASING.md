# 版本发布指南

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)，通过 GitHub Actions 工作流 `Build and Release` 构建并发布 Docker 镜像。

## 版本格式

```text
主版本号.次版本号.修订号[-预发布标识]
```

| 示例 | 含义 |
| --- | --- |
| `2.0.0` | 正式版本 |
| `2.1.0` | 新增功能，向后兼容 |
| `2.1.1` | 缺陷修复 |
| `3.0.0` | 存在不兼容变更 |
| `2.1.0-beta.1` | 预发布版本 |

## 发布流程

### 1. 准备版本

1. 将 `CHANGELOG.md` 的 `[Unreleased]` 段落整理为待发布版本，并补上发布日期。
2. 同步版本号，以下位置必须一致：

   | 位置 | 字段 |
   | --- | --- |
   | `server/main.py` | `FastAPI(version=...)` 与根路由返回的 `version` |
   | `server/api/v1/frontend_config.py` | `APP_VERSION` 的默认值 |
   | `web/package.json` | `version` |
   | `pyproject.toml` | `[tool.poetry] version` |

3. 本地跑通「质量门禁」：`ruff check server`、`mypy`、`pytest server/tests -q`、`cd web && npm run build:check`。

### 2. 打标签

```bash
git switch main
git pull origin main

git tag -a v2.1.0 -m "Release v2.1.0"
git push origin v2.1.0
```

标签必须形如 `v<主>.<次>.<修订>`，工作流靠 `v*.*.*` 触发。

### 3. 自动发布

推送标签后，`Build and Release` 工作流会：

1. 构建前端并打包进 Docker 镜像（多架构：`amd64` / `arm64`）。
2. 推送到 Docker Hub，并按标签策略打多个 tag。
3. 创建 GitHub Release，附带变更说明。

### 4. 手动触发

在 GitHub 的 Actions → `Build and Release` → Run workflow，填写：

| 输入 | 说明 |
| --- | --- |
| `version` | 版本号，不带 `v` 前缀，如 `2.1.0` |
| `prerelease` | 是否为预发布版本 |

## Docker 标签策略

发布 `v2.1.3` 时生成：

| 标签 | 说明 |
| --- | --- |
| `xiyan520/mhti:2.1.3` | 完整版本号 |
| `xiyan520/mhti:2.1` | 主次版本号 |
| `xiyan520/mhti:2` | 主版本号 |
| `xiyan520/mhti:latest` | 最新稳定版（预发布不覆盖） |

## 必需的 Secrets

在仓库 Settings → Secrets and variables → Actions 中配置：

| Secret | 说明 |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub 用户名 |
| `DOCKERHUB_TOKEN` | Docker Hub 访问令牌（Read & Write） |

获取令牌：Docker Hub → Account Settings → Security → New Access Token。

## 发布检查清单

- [ ] 质量门禁全部通过
- [ ] 版本号四处一致
- [ ] `CHANGELOG.md` 已整理并标注日期
- [ ] 文档中失效的版本号、截图与链接已更新
- [ ] Secrets 配置就绪
- [ ] 预发布版本已确认 `prerelease` 开关
