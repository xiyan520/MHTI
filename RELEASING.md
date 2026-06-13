# 版本发布指南

本项目使用 [语义化版本](https://semver.org/lang/zh-CN/) 进行版本控制。

## 版本格式

```
主版本号.次版本号.修订号[-预发布标识]

示例：
- 1.0.0      - 正式版本
- 1.1.0      - 新功能版本
- 1.1.1      - 修复版本
- 2.0.0-beta - 预发布版本
```

## 发布流程

### 1. 创建版本标签

```bash
# 确保在 main 分支
git checkout main
git pull origin main

# 创建标签
git tag -a v1.0.0 -m "Release v1.0.0: 初始发布"

# 推送标签
git push origin v1.0.0
```

### 2. 自动触发 Actions

推送标签后，GitHub Actions 会自动：

1. ✅ 构建前端 (Vue.js)
2. ✅ 构建 Docker 多架构镜像 (amd64, arm64)
3. ✅ 推送到 Docker Hub (`xiyan520/mhti:latest` 和版本标签)
4. ✅ 创建 GitHub Release

### 3. 手动触发发布

也可以在 GitHub Actions 页面手动触发：

1. 进入 Actions → Build and Release
2. 点击 "Run workflow"
3. 输入版本号（不带 v 前缀）
4. 选择是否为预发布版本

## 版本号规则

| 变更类型 | 版本变化 | 示例 |
|---------|---------|------|
| 重大不兼容变更 | 主版本号 +1 | 1.0.0 → 2.0.0 |
| 新功能（向后兼容） | 次版本号 +1 | 1.0.0 → 1.1.0 |
| Bug 修复 | 修订号 +1 | 1.0.0 → 1.0.1 |
| 预发布版本 | 添加后缀 | 1.1.0-alpha, 1.1.0-beta, 1.1.0-rc.1 |

## Docker 标签策略

发布 `v1.2.3` 时，会自动创建以下标签：

| 标签 | 说明 |
|------|------|
| `xiyan520/mhti:1.2.3` | 完整版本号 |
| `xiyan520/mhti:1.2` | 主次版本号 |
| `xiyan520/mhti:1` | 主版本号 |
| `xiyan520/mhti:latest` | 最新稳定版（非预发布） |

## 必需的 Secrets 配置

在 GitHub 仓库设置中配置以下 Secrets：

| Secret 名称 | 说明 |
|------------|------|
| `DOCKERHUB_USERNAME` | Docker Hub 用户名 |
| `DOCKERHUB_TOKEN` | Docker Hub 访问令牌 |

### 获取 Docker Hub Token

1. 登录 [Docker Hub](https://hub.docker.com/)
2. 进入 Account Settings → Security
3. 创建 Access Token（选择 Read & Write 权限）
4. 复制 Token 到 GitHub Secrets

## 发布检查清单

发布前请确认：

- [ ] 所有测试通过
- [ ] 文档已更新
- [ ] CHANGELOG 已更新（如有）
- [ ] 版本号符合语义化规范
- [ ] Secrets 已正确配置

## 赞助作者
![](https://i.imgs.ovh/2026/06/13/099f7aaed235aa63f1e1a3398f87c27d.jpg)
