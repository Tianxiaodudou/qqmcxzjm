# QQ音乐下载器（fnOS 应用）

运行在飞牛 fnOS 上的 QQ 音乐下载器：扫码/验证码登录后，浏览推荐与私有歌单、搜索单曲，边听边看同步歌词；下载时自动选用账号可用的最高音质，把加密音频解密还原并把元数据写入文件，输出可直接播放的成品到 NAS 指定目录。

- 应用名（appname）：`qqmusic-downloader`
- 桌面入口：应用中心 → QQ音乐下载器
- 访问路径：统一网关 `https://<设备地址>/app/qqmusic-downloader/`
- 许可证：GPL-3.0-or-later（本项目包含的 `app/qqmusic_api` 为 GPLv3-or-later，故整体以 GPLv3 发布）

## 功能

| 模块 | 说明 |
| --- | --- |
| 登录 | QQ 扫码、微信扫码、QQ音乐 App 扫码、手机号验证码；凭证保存在 `TRIM_PKGVAR` 下，权限 600 |
| 搜索 | 单曲搜索，支持一键加入下载队列 |
| 歌单 | 推荐歌单、新歌推荐、自己的歌单与收藏；支持批量勾选下载 |
| 预览 | 浏览器内播放试听，同步歌词 + 翻译歌词 |
| 下载 | 串行队列，四阶段进度（音频 → 元数据 → 解密 → 合并）；自动选最高可用音质并逐级降级；支持暂停/继续/重试/清空 |
| 元数据 | 歌名、歌手、专辑、封面（内嵌）、歌词（内嵌歌词 + 同名 .lrc）、翻译歌词写入成品文件 |
| 解密 | 加密音频（`.mflac` / `.mgg`）解密还原后写入元数据，成品文件名为 `歌名_歌手_歌曲ID`，下载目录只保留成品 |
| 历史 | 下载历史（成功/失败原因），仅显示可访问的记录 |
| 设置 | 下载目录 / 文件夹访问权限（合并为一处：点「选择文件夹…」走 fnOS 内嵌选择器，选中即授权；下拉可切换已授权目录）、歌词翻译开关、请求间隔 |

## 安装

1. 打开飞牛 fnOS「应用中心 → 手动安装」，选择 `qqmusic-downloader.fpk`。
2. 安装向导中填写歌词翻译开关（安装向导没有文件夹选择器，下载目录改在应用内点选）。
3. 安装完成后进入应用，在「设置 → 下载目录」点**选择文件夹…**，在飞牛内嵌文件夹选择器中点选一个文件夹：选择即授权（trim.file.userAccess），下载目录与访问权限一次搞定。
4. 在「登录」页选择一种方式登录（未登录也可浏览推荐内容，但会受接口限流影响）。

命令行安装（设备已开启 SSH）：

```bash
appcenter-cli install-fpk qqmusic-downloader.fpk
appcenter-cli start qqmusic-downloader
```

## 构建

### 官方工具

`fnpack` 是 fnOS 官方打包工具，从官方文档获取对应平台版本：
<https://developer.fnnas.com/docs/cli/fnpack/>

```bash
# Linux / macOS
curl -fL -o fnpack https://static2.fnnas.com/fnpack/fnpack-1.2.3-linux-amd64
chmod +x fnpack && sudo mv fnpack /usr/local/bin/fnpack

# 在项目根目录打包（产物为 qqmusic-downloader.fpk）
fnpack build --directory .
```

Windows 用 `fnpack-1.2.3-windows-amd64`（重命名为 `fnpack.exe`）。

### GitHub Actions（推荐）

`.github/workflows/build-fpk.yml` 会自动完成：

1. **校验**：项目结构检查（`tools/ci/check_project.py`）、Python 语法检查、安装依赖（`app/requirements.txt`）、后端冒烟测试（`tools/ci/smoke_test.py`，共 14 项，依赖外部网络的用例仅告警）。
2. **打包**：下载官方 `fnpack`，执行 `fnpack build --directory .`，上传 `qqmusic-downloader.fpk` 为构建产物（artifact）。
3. **发布**：推送形如 `v1.0.0` 的 tag 时，自动把 `.fpk` 附加到 GitHub Release。

触发方式：push 到 `main`、提交 PR、打 tag，或在 Actions 页面手动 `Run workflow`。产物在对应运行记录的 Artifacts 中下载。

### 本地快速自检

```bash
python tools/ci/check_project.py          # 结构与 JSON/资源引用检查
pip install -r app/requirements.txt
python tools/ci/smoke_test.py             # 后端接口冒烟
python tools/ci/inspect_fpk.py qqmusic-downloader.fpk   # 打包产物内容/可执行位检查
```

> ⚠️ 注意：fnOS 要求 `cmd/*` 生命周期脚本带可执行位。Windows 工作树无法保存
> 可执行位，**本地（Windows）构建出的 .fpk 不可直接安装**，请使用 CI 构建的产物，
> 或用 `wsl`/Linux 环境构建（构建前 `chmod +x cmd/* wizard/*`）。

## 项目结构

```
.
├── manifest                # 应用元信息（名称/版本/入口/依赖 python312）
├── ICON.PNG ICON_256.PNG   # 应用图标
├── config/
│   ├── privilege           # 运行身份（package）
│   └── resource            # 开放 API 授权范围（trim.file.*）
├── wizard/                 # 安装向导（install/config 步骤）
├── cmd/                    # 生命周期脚本：main(start/stop/status) 与各 hook
├── app/
│   ├── ui/                 # 前端页面（index.html / app.js / style.css / js 宿主 SDK）
│   ├── web/                # FastAPI 后端（网关路由 + 下载服务）
│   ├── qqmusic_api/        # QQ 音乐接口 SDK
│   └── requirements.txt    # 运行时依赖（首次启动安装到 $TRIM_PKGVAR/venv）
└── tools/ci/               # CI 校验与冒烟脚本
```

运行时目录由 fnOS 注入的环境变量决定：`TRIM_APPDEST`（程序目录）、`TRIM_PKGVAR`（数据/虚拟环境/日志）、`TRIM_PKGETC`（配置）、`TRIM_TEMP_LOGFILE`（启动日志）。

## 开发说明

- 后端：Python 3.12 + FastAPI + uvicorn，通过 Unix Socket（`app.sock`）由 fnOS 统一网关注册，前缀 `/app/qqmusic-downloader`。
- 首次启动时 `cmd/main` 会用系统自带的 python312 创建私有 venv 并安装 `app/requirements.txt`。
- 前端为无构建步骤的原生 HTML/JS，静态资源同时挂在网关前缀与 `/static` 下，便于本地调试。
- 本地调试可绕过 fnOS：设置 `QQMUSIC_UI_DIR`、`QQMUSIC_DATA_DIR` 后直接 `uvicorn web.main:app`。

## 已知问题

- 未登录状态下频繁调用搜索/推荐接口会被 QQ 音乐限流（`RatelimitedError`），需登录或稍后重试。
- 下载的音频与元数据仅供个人合法使用，请自行确认版权与使用范围。
