# QQ音乐下载器（fnOS 应用）

**当前版本 v1.3.1**　·　[下载最新安装包](https://github.com/Tianxiaodudou/qqmcxzjm/releases/latest)　·　[更新日志](CHANGELOG.md)

运行在飞牛 fnOS 上的 QQ 音乐下载器：登录后浏览推荐与私有歌单、搜索单曲/歌单，边听边看同步歌词；下载时自动选用账号可用的最高音质，把加密音频解密还原并把歌名、歌手、专辑、封面与歌词写入文件，输出可直接播放的成品到 NAS 指定目录。

- 应用名（appname）：`qqmusic-downloader`
- 桌面入口：应用中心 → QQ音乐下载器
- 访问路径：统一网关 `https://<设备地址>/app/qqmusic-downloader/`
- 许可证：GPL-3.0-or-later（本项目包含的 `app/qqmusic_api` 为 GPLv3-or-later，故整体以 GPLv3 发布）

> 本页只写**当前版本（v1.1.14）的完整功能**，不记录历史版本；历史见 [CHANGELOG.md](CHANGELOG.md) 与 [Releases](https://github.com/Tianxiaodudou/qqmcxzjm/releases)。

## 功能

| 模块 | 说明 |
| --- | --- |
| 登录 | 三种方式手动点选（不再自动出码）：QQ 扫码、微信扫码、手机号验证码；二维码可刷新重取；凭证保存在 `TRIM_PKGVAR` 下，权限 600 |
| 歌单 | 推荐歌单、新歌推荐、猜你喜欢、每日推荐（私人雷达）、自己的歌单与收藏（合并「我喜欢」与自建歌单）；支持批量勾选下载 |
| 搜索 | 单曲搜索；类型切到「歌单」后可直接输入歌单名关键词，也可把**歌单链接或歌单 ID** 粘进搜索框，跳过搜索直接打开该歌单 |
| 试听播放器 | 弹窗式播放器：封面 + 元数据 + 同步歌词（歌词可点击跳转，手动滚动即取消自动居中）+ 翻译歌词；音频走后端同源流代理，可随意拖动进度；自绘控制条（播放/暂停、进度、音量、倍速）与「⋮」菜单，其中**下载走正式下载路径**（账号最高音质 + 同名文件去重 + 任务进度），不再下载试听音质 |
| 下载 | 串行队列，四阶段（音频 → 元数据 → 解密 → 合并）每项都有进度条与百分比；自动使用账号可用的最高音质并逐级降级；支持暂停 / 继续 / 重试；**创建任务前先按成品文件名（`歌名_歌手_歌曲ID`）检查下载目录，已有同名成品就直接提示「已有该音乐文件」并跳过，不重复下载** |
| 解密与成品 | 加密音频（`.mflac` / `.mgg`）整文件解密还原（与官方 QMC2 实现逐字节一致），内嵌歌名/歌手/专辑/封面/歌词，文件名 `歌名_歌手_歌曲ID`，下载目录只保留成品 |
| 下载历史 | 成功/失败与原因；每行可「播放」已下载成品（读取文件内嵌标签与歌词）；「清除已完成」/「清空全部」点下即清，清空前自动备份 `history.json.bak` |
| 性能 | 登录后空闲逐页预取**每个页面的首屏数据**（首页推荐 / 我的歌单 / 下载任务 / 下载历史，各取第一页），切页直接命中缓存（超过 60 秒才后台静默刷新）；封面按视口懒加载，首屏不再批量拉图 |
| 设置 | 下载目录与文件夹访问权限合并为一处（点「选择文件夹…」走 fnOS 内嵌选择器，选中即授权；下拉可切换已授权目录）、歌词翻译开关、请求间隔上下限、全选上限（默认 500 首，可填 10 ~ 20000） |
| 向导 | 安装向导（存储与权限说明 + 歌词翻译 + 下载间隔）、配置向导、卸载向导（可选保留或清除应用数据） |

## 安装

1. 打开飞牛 fnOS「应用中心 → 手动安装」，选择 Release 里的安装包 `版本号-qqmusic-downloader.fpk`（例如 `1.1.15-qqmusic-downloader.fpk`）。
2. 安装向导中设置歌词翻译开关与请求间隔（安装向导没有文件夹选择器，下载目录改在应用内点选）。
3. 安装完成后进入应用，在「设置 → 下载目录」点**选择文件夹…**，在飞牛内嵌文件夹选择器中点选一个文件夹：选择即授权（trim.file.userAccess），下载目录与访问权限一次搞定。
4. 在「登录」页选择一种方式登录（未登录也可浏览推荐内容，但会受接口限流影响）。

命令行安装（设备已开启 SSH）：

```bash
appcenter-cli install-fpk 1.1.15-qqmusic-downloader.fpk   # 换成实际下载到的文件名
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

# 在项目根目录打包（产物为 qqmusic-downloader.fpk，CI 会再重命名为「版本号-应用名」）
fnpack build --directory .
```

Windows 用 `fnpack-1.2.3-windows-amd64`（重命名为 `fnpack.exe`）。

### GitHub Actions（推荐）

`.github/workflows/build-fpk.yml` 会自动完成：

1. **校验**：项目结构检查（`tools/ci/check_project.py`）、Python 语法检查、安装依赖（`app/requirements.txt`）、后端冒烟测试（`tools/ci/smoke_test.py`，依赖外部网络的用例仅告警）；打 tag 时还会校验 tag 与 `manifest` 的 `version` 一致。
2. **打包**：下载官方 `fnpack`，执行 `fnpack build --directory .`；产物按「版本号-应用名」重命名（如 `1.1.15-qqmusic-downloader.fpk`），并额外生成该版本**源码全包** `版本号-qqmusic-downloader-src.tar.gz`（`git archive` 快照）。两者都作为构建产物（artifact）上传。
3. **发布**：推送形如 `v1.1.15` 的 tag 时，自动生成**发行说明**（`tools/ci/release_notes.py`，内容取自 `manifest` 的 `changelog` + 该版本提交记录与改动统计），把重命名后的 `.fpk`、源码 `.tar.gz` 连说明一起写入 GitHub Release，并回写 `CHANGELOG.md` 与 README 的当前版本号。

触发方式：push 到 `main`、提交 PR、打 tag，或在 Actions 页面手动 `Run workflow`。产物在对应运行记录的 Artifacts 中下载。

### 本地快速自检

```bash
python tools/ci/check_project.py          # 结构与 JSON/资源引用检查
python tools/ci/release_notes.py --tag v1.1.15   # 预览该版本发行说明（不写文件）
pip install -r app/requirements.txt
python tools/ci/smoke_test.py             # 后端接口冒烟
python tools/ci/inspect_fpk.py qqmusic-downloader.fpk   # 打包产物内容/可执行位检查（CI 里传入重命名后的文件名）
```

> ⚠️ 注意：fnOS 要求 `cmd/*` 生命周期脚本带可执行位。Windows 工作树无法保存
> 可执行位，**本地（Windows）构建出的 .fpk 不可直接安装**，请使用 CI 构建的产物，
> 或用 `wsl`/Linux 环境构建（构建前 `chmod +x cmd/* wizard/*`）。

## 项目结构

```
.
├── manifest                # 应用元信息（名称/版本/changelog/入口/依赖 python312）
├── ICON.PNG ICON_256.PNG   # 应用图标
├── CHANGELOG.md            # 更新日志（由 manifest.changelog 自动生成，勿手改）
├── config/
│   ├── privilege           # 运行身份（package）
│   └── resource            # 开放 API 授权范围（trim.file.*）
├── wizard/                 # 安装 / 配置 / 卸载向导
├── cmd/                    # 生命周期脚本：main(start/stop/status) 与各 hook
├── app/
│   ├── ui/                 # 前端页面（index.html / app.js / style.css / js 宿主 SDK）
│   ├── web/                # FastAPI 后端（网关路由 + 下载服务）
│   ├── qqmusic_api/        # QQ 音乐接口 SDK
│   └── requirements.txt    # 运行时依赖（首次启动安装到 $TRIM_PKGVAR/venv）
└── tools/ci/               # CI 校验、冒烟与发行说明脚本
```

运行时目录由 fnOS 注入的环境变量决定：`TRIM_APPDEST`（程序目录）、`TRIM_PKGVAR`（数据/虚拟环境/日志）、`TRIM_PKGETC`（配置）、`TRIM_TEMP_LOGFILE`（启动日志）。

## 开发说明

- 后端：Python 3.12 + FastAPI + uvicorn，通过 Unix Socket（`app.sock`）由 fnOS 统一网关注册，前缀 `/app/qqmusic-downloader`。
- 首次启动时 `cmd/main` 会用系统自带的 python312 创建私有 venv 并安装 `app/requirements.txt`（依赖 wheel 随包预置，离线安装；失败才回退镜像/PyPI）。
- 前端为无构建步骤的原生 HTML/JS，静态资源同时挂在网关前缀与 `/static` 下，便于本地调试。
- 本地调试可绕过 fnOS：设置 `QQMUSIC_UI_DIR`、`QQMUSIC_DATA_DIR` 后直接 `uvicorn web.main:app`。

## 发版流程（维护者）

更新日志只有一个真源：**`manifest` 的 `changelog` 字段**（飞牛应用中心展示的就是它）。
`CHANGELOG.md`、GitHub Release 说明、README 的当前版本号都由它派生，不需要手写。

1. 改代码，随后把 `manifest` 的 `version` 升到新版本，并在 `changelog` 最前面加一条
   `<版本号> …`（多项用 ①②③ 分隔，末尾用 `｜` 接上一条历史）。
2. 本地预览：`python tools/ci/release_notes.py --tag v<新版本>`。
3. 提交并打 tag：`git tag v<新版本> && git push origin main --tags`。
4. CI 自动：校验 tag 与 manifest 版本一致 → 打包 → 生成发行说明 → 上传 Release 资产 →
   同步 `CHANGELOG.md` 与 README 版本号。**每次发布都会带上更新日志**，README 始终只描述当前版本的功能。

## 已知问题

- 未登录状态下频繁调用搜索/推荐接口会被 QQ 音乐限流（`RatelimitedError`），需登录或稍后重试。
- 下载的音频与元数据仅供个人合法使用，请自行确认版权与使用范围。
