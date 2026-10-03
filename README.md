# 拾光 Studio · RenPy 同窗版

通过 Codex 和 RenPyBridge 制作 Galgame，在同一个游戏主窗口中插入本地 HTML 小游戏。小游戏结束后把结果交给剧情：胜利、失败、取消和异常分别连接到明确的剧情帧，也可以按分数或已声明的变量继续分支。

本仓库主版本现为这套 RenPy 制作与展示方案。原来的可视化编辑器和浏览器展示器保留在 [旧版 v0.1.1](https://github.com/senyonhu/shiguang-studio/releases/tag/v0.1.1) 中。两版的工程格式不同，迁移说明见 [MIGRATION_FROM_STUDIO](docs/MIGRATION_FROM_STUDIO.md)。

当前版本是 Windows 本机预览版：RenPyBridge / 插件 `0.1.3`，普通剧情 schema `1.0`，带小游戏的工程 schema `1.1`，同窗宿主 `0.2.0`。本机测试范围和未测环境见 [VALIDATION](docs/VALIDATION.md)。

## 玩家直接体验

从 [新版发行页](https://github.com/senyonhu/shiguang-studio/releases/tag/renpy-bridge-html-20261003) 下载 HTML 演示游戏 ZIP，完整解压后运行目录内的游戏 `.exe`。保留整个目录；原生游戏、小游戏宿主和固定浏览器运行时共同组成发行包。玩家无需安装 Python、Codex 或 RenPy SDK。

演示包含拼图、限时操作和检查点小游戏。正常游戏过程中只有一个可见主窗口；HTML 控件嵌入 RenPy 主窗口，完成后恢复剧情。开发测试可以使用浏览器诊断连接，面向玩家的正式包关闭该通道。

## 作者安装

制作端需要 Windows x64、Python **3.12** 和已安装的 Codex。首次安装需要联网下载锁定的 Python 依赖、官方 RenPy SDK、微软固定浏览器和本机宿主构建依赖。源代码 ZIP 不包含这些大型二进制文件。

在源码目录的 PowerShell 中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -X utf8 tools\prepare_html_browser.py --prepare
.\.venv\Scripts\python.exe -X utf8 tools\build_html_embedded_host.py --prepare
$bridgeDestination = Join-Path $env:LOCALAPPDATA 'RenPyBridge'
.\.venv\Scripts\python.exe -X utf8 scripts\install_local.py --destination $bridgeDestination --register
.\.venv\Scripts\python.exe -X utf8 tools\prepare_html_runtime_manifest.py
```

最后一条命令返回本次新建的 `configuration` 路径。将该实际路径填入下面命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\install_html_runtime.py --destination $bridgeDestination --configuration '<上一步返回的 configuration 路径>'
```

核心安装和同窗运行时安装分开记录并核验。运行时清单来自当前源码的实际自包含宿主构建及固定浏览器，不需要先创建私人演示工程。准备或复制运行时成功不代表具体作品已经验收。

安装器生成个人插件的真实 Python 和数据目录配置，通过官方 Codex CLI 注册。仓库中的 `.mcp.json` 是安装模板；先运行安装器，移动源码目录不影响已经受管安装的插件。注册后重新启动 Codex，让桌面会话加载新版工具，再读取 `get_environment` 和 `get_minigame_capabilities`；成功写入安装文件与当前桌面工具已经刷新是两个步骤。

升级既有受管安装时保留作品、任务、检查点和用户存档。固定 SDK 的原始文件必须匹配官方归档，具有匹配原始源码的编译缓存可以变化；完整既有 SDK 数据不会因普通插件升级而清空。安装、恢复和备份位置记录在安装报告中。

## 让 AI 插入小游戏

安装后可对 Codex 说：

> 根据我的表格和素材制作这个章节。第三段插入一个本地 HTML 拼图，在同一个游戏窗口里运行。胜利后进入庆祝剧情；分数不足进入普通剧情；取消和出错都有返回帧。小游戏可以中途保存，读档后继续，并且奖励只发一次。先检查并实测，再导出 Windows 游戏包。

AI 可以编写 HTML、CSS、JavaScript，并通过 `register_minigame` 或分块导入登记游戏包。每包包含清单、入口、结果 schema、允许的本地文件及完整内容摘要；发布后按内容摘要冻结。包中的 Bridge SDK 提供开始、检查点和结束通信，剧情核心验证结果的类型、范围、实例身份和重复提交，再应用声明过的变量变化和分支。

小游戏必须主动调用 SDK 的结束接口，单靠画面显示“胜利”不会自动推进剧情。检查点也必须由游戏序列化；不支持自动保存任意浏览器堆内存。`entry_restart` 可从入场重新开始，`checkpoint` 可恢复游戏声明的状态；完成后的读档不会重复发放已提交奖励。

本机宿主只提供受控的本地资源和消息桥，发布配置限制外部网络、下载、弹窗和文件导航。联网小游戏、任意网站、通用桌面代码执行不属于当前包契约。作者仍应检查 AI 生成的游戏逻辑、结果和素材许可。

生产示例在 `examples/html_minigames/production/v1.0.1/`；它们包含三个完整包及 SDK。具体制作约定见 [使用指南](docs/USAGE.md)、[31 个 MCP 工具](docs/TOOLS.md)、[项目契约](docs/CONTRACT.md) 和插件内的 [HTML 参考](plugin/renpy-bridge/skills/renpy-author/references/html-minigames.md)。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test examples/html_minigames/tests/*.test.cjs
.\.venv\Scripts\python.exe -X utf8 tools\prepare_html_browser.py --verify-only
```

Node 仅用于 JavaScript 测试，发行玩家端不依赖 Node。原生宿主使用固定 .NET SDK 构建，正式产物是自包含版本；不要以诊断用的 `--framework-dependent` 替代发行产物。真实 RenPy 运行、窗口归属、小游戏输入和独立包测试另由带场景的引擎任务验收。

公开源码经过文件白名单打包，不包含私人作品、存档、浏览器资料目录、凭据、原始本机证据或运行时缓存。发布范围、构建来源和校验摘要见 [PUBLIC_RELEASE](docs/PUBLIC_RELEASE.md)。

## 许可

仓库当前没有为整个 RenPyBridge 核心声明统一开源许可证。示例包分别附带自己的 MIT 许可；RenPy、WebView2、.NET 及 Python 依赖遵循各自许可。自包含宿主随包保留固定 .NET 和 WebView2 SDK 归档中的原始许可/第三方声明。公开可读取不等于可以推定全部代码使用 MIT。
