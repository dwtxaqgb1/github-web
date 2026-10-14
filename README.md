# GitHub 发布工具 (gh-web)
一个自托管的 GitHub 
管理面板。在浏览器里上传源码包、管理仓库、发布 
Release、部署 Pages，不需要命令行。
## ✨ 功能
### 发布
- 上传 `.zip` / `.tar.gz` 
源码包，自动建仓库、推代码 - 
一键创建 
Release，附发布说明和资产文件 - 部署 
GitHub Pages 静态站点 - 
发布成功后自动给出加速下载链接
### 项目管理
- 项目列表 / 搜索 / 排序 - 
网页浏览仓库文件树、下载文件、预览内容 
- 在线编辑文件（直接改、直接提交） - 
文件历史查看（commit 列表 + diff） - 
单文件回滚到历史版本 - 重命名 / 归档 
/ 删除仓库 - 克隆仓库到 NAS 本地 - 
一键发新版本（自动带出上次配置）
### Fork
- Fork 别人的仓库到自己的账号 - 
手动同步上游更新 - 
自动同步（每个仓库独立设置频率：每小时 
/ 每天 / 每周等）
### 安全
- 登录密码 + 
失败限速（连续失败锁定） - Token 
加密存储 - Token 权限自检 - 支持 SSH 
over 443（绕过 github.com:443 丢包）
### 体验
- 中文界面 - 上传进度条 - 
发布前预检（发现潜在问题提前拦截） - 
步骤进度指示 - README Markdown 
实时预览 - README 
自动生成（扫描项目文件结构） - 
任务日志弹窗
## 📸 截图
> 待补充（可以截几个界面放到 
> `docs/screenshots/` 
> 里，然后在这里引用）
## 🚀 快速开始
### 环境要求
- Docker 20.10+ - Docker Compose v2 
- 能访问 GitHub（见下方 [SSH 
部署](#-ssh-部署推荐)）
### 一键安装（推荐）
```bash
git clone https://github.com/dwtxaqgb1/github-web.git && cd github-web && chmod +x install.sh && ./install.sh
```
