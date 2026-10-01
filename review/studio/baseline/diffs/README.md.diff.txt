--- baseline/README.md
+++ current/README.md
--- baseline lines: 96
+++ current lines: 110

- # 旧塔的最后一盏灯
- 
- **新版 Ren’Py 单机 Demo 已加入。** 双击 `启动Demo.cmd` 或 `启动RenPy新版.cmd`；独立源码在 `client/`，Windows 离线包在 `release/TowerLastLight-1.1.0-win.zip`。新版使用真实 C11 / WASI 本地执行，全部图片由 image_gen 生成，背景、人物、灯、门均独立分层。详见 [新版实现说明](docs/renpy/IMPLEMENTATION.md)。以下保留旧网页版说明。
- 
- 《术式纪元》序章技术验证 Demo。Vue 3 + TypeScript + Vite / Ink + inkjs / Monaco / FastAPI / Judge0 / IndexedDB。
- 
- ## 旧网页版启动
- 
- 在 Windows 上双击 **启动网页版Demo.cmd**，然后打开 **http://127.0.0.1:5173**。
- 
- 已有依赖的本机再次启动通常只需几秒；首次安装和 Docker 镜像构建需要网络。需 Node.js 22+、Python 3.12、Docker Desktop 的 Linux 容器引擎。不要直接双击 index.html。
- 
- ```powershell
- cd C:\codex\C_game
- .\start.ps1
- ```
- 
- 启动器自动准备项目内依赖、启动四个 Judge0 容器、后台启动 API 和前端。日志在 `.runtime/`。网页端口为 5173，业务 API 为 8000，Judge0 为 2358，均只监听本机。Judge0 启动需要额外等待；游戏中的连接检查会真正编译并运行一段 C，而不仅检查 HTTP 存活。
- 
- 试玩时可最小化 Docker 窗口，让它留在系统托盘；请勿使用 Pause 或 Quit，这会暂停/关闭真实编译服务。如果显示 manually paused，在 Docker 菜单中选择 Resume 恢复即可。
- 
- 只启动界面与业务服务可运行 `powershell -ExecutionPolicy Bypass -File start.ps1 -SkipJudge`。此模式如果没有可用执行器，运行会如实显示“执行服务暂不可用”，无法通关，不会模拟成功。
- 
- ## 操作
- 
- - 开始旅程，输入昵称，阅读剧情并选择回应。空格或 Enter 可推进普通对话。
- - 第一关可直接在 Monaco 中增删、粘贴或修改完整 C 代码；也可在代码下方“输出内容”中将 `0` 改成 `1`。删除原有 printf 后快捷输入会隐藏，仍可直接编辑代码。
- - 第二、三关均可直接增删和替换完整 C 代码。第二关先运行默认代码，再把 `i < n` 改为 `i <= n`，也可使用“循环条件”快捷输入。
- - 代码上方的“标准输入 n”可手动填写 0—6 的整数，点击“运行术式”时会自动传给 `scanf`，无需在输出区域输入。第二关提交固定验证 n=3；第三关继承第二关代码，最后“验证全部回路”固定检查 0、1、3、6。蓝色行仅为提示，不限制编辑范围。
- - “运行术式”只试运行；“提交验证”成功后先保存，再允许推进。已提交关卡锁定草稿，结算后可进入完整代码自由实验。
- - 提示分三级，使用不扣分。恢复初始代码需要确认。设置中可调整代码字体、静音、减少动态效果、导入导出存档。
- - 26 秒出口演出可跳过；两条路径抵达相同的未来残响。结算有学习记录、奖励和自由实验。
- 
- ## 实现边界
- 
- 两个主场景、一位原创 SVG 角色的三种表情、六盏独立灯、原创程序生成的音乐与音效。场景为 SVG/CSS 三层视差，不依赖外部图片、音频或字体。美术采用可迭代的矢量风格，未使用商业插画或配音。
- 
- 每次请求后端保留不可变源代码字符串与 SHA-256。第三关提交固定跑 0、1、3、6。按完整 stdout 判定；只统一 CRLF、容许一个末尾换行，不过滤重复、空行、空格或额外文字。n=0 必须是完全空 stdout。
- 
- 灯光每步约 300ms，只是 stdout 回放。试运行/提交、编译失败/错误答案/运行失败/超时/输出超限/服务不可用分别呈现。编辑期间返回的旧结果按请求世代、任务、源代码和散列共同丢弃。
- 
- Ink 编译器与运行时均使用锁定的 inkjs 2.4.0；构建时从 `story/tower.ink` 生成 JSON，避免字节码版本错配。IndexedDB 一个快照保存 Ink 状态、当前显示行/选择、草稿、任务和奖励，`save_revision` 串行写入。奖励 ID `tower-exit-v1` 去重。导入先校验类型、版本、任务一致性和可加载的 Ink 状态，校验失败不覆盖当前存档。
- 
- 日志含每次提交源码、散列、任务进入退出、错误类别、失败输入、最高提示和理解题首次答案。只保留最近 2,000 条；页面隐藏时暂停计时。IndexedDB 存档可修改，仅作 Demo 进度，不能用作可信考试成绩。
- 
- ## 执行部署
- 
- `infra/compose.yaml` 部署 Judge0、worker、PostgreSQL、Redis；后两者只服务 Judge0，并非游戏业务数据库。浏览器只能访问 FastAPI。可用后端环境变量 `JUDGE0_URL`、`JUDGE0_TOKEN` 连接自有 Judge0；C 语言 ID 通过实例 `/languages` 查找，优先最高版本 GCC，编译固定 `-std=c11 -O0`。
- 
- 此机使用 cgroup v2。Judge0 1.13.1 原镜像的旧 Isolate 不兼容，因此 Dockerfile 从官方固定提交构建 **Isolate 2.7** 静态二进制。入口脚本只在容器私有 cgroup 命名空间内创建管理/执行子组，不改宿主全局 cgroup。学生进程保留命名空间、seccomp、网络隔离、CPU/内存/文件限制。没有挂载应用目录、Docker socket 或宿主凭据。
- 
- 每个运行用例 CPU 1 秒、墙钟 3 秒、内存 64MiB、文件输出 16KiB。达到 stdout/stderr 上限或 SIGXFSZ 时标为输出超限，不能拿截断输出判成功。编译资源另设 5 秒 CPU / 10 秒墙钟 / 256MiB / 4MiB 文件上限，以容纳编译器和可执行文件。后端最多两个评测请求并发、12 个活动请求、100 秒整体超时、500 条临时结果上限。
- 
- 这是本机教学技术验证部署；Judge0 需要 privileged 容器，数据库密码是明确的本地示例值，不应原样暴露到公网。正式多用户发布应在独立评测主机部署，增加鉴权、配额、服务监控与隔离审计。
- 
- ## 验证命令
- 
- ```powershell
- npm ci
- npm run build
- npm test
- .\.venv\Scripts\python.exe -m pytest tests/test_backend.py -q
- # 需前后端及 Judge0 启动
- .\.venv\Scripts\python.exe scripts/verify-live.py
- npm run test:e2e
- ```
- 
- 浏览器自动化使用本机 Microsoft Edge。`test-results/report/index.html` 是浏览器测试报告；`.runtime/live-verification.json` 是真实执行详情；`.runtime/` 内另有实际界面截图。完整流程测试在 Judge0 健康检查失败时会明确跳过，不能把跳过算作验证通过。
- 
- ## 开发入口与目录
- 
- 开发服务器 URL 加 `?debug=1` 才显示调试入口；生产构建关闭。调试状态写入独立 `debug` 存档，不覆盖正式 `main` 存档，不计入完成或奖励。
- 
- | 路径 | 内容 |
- | --- | --- |
- | `src/useGame.ts` | 剧情协调、提交版本控制、保存与回放 |
- | `src/save.ts` | IndexedDB 与导入校验 |
- | `src/components/CodeEditor.vue` | Monaco、完整代码编辑与错误定位 |
- | `src/components/TowerScene.vue` | 石室、独立灯、三层出口 |
- | `src/components/Character.vue` | 璃的三种矢量表情 |
- | `src/audio.ts` | Web Audio 原创乐段与音效 |
- | `story/tower.ink` | 可直接编辑的剧情源文件 |
- | `backend/tasks.py` | 后端任务、输入与严格输出协议 |
- | `backend/judge.py` | Judge0 真实 C11 执行适配 |
- | `backend/app.py` | 队列、任务与轮询接口 |
- | `infra/` | 隔离执行器的可重复构建配置 |
- | `tests/` | 单元与浏览器测试 |
- 
- 停止当前 Demo 的网页/API 可结束 `.runtime/api.pid` 与 `.runtime/web.pid` 对应进程；执行器用 `docker compose -f infra/compose.yaml stop` 停止，数据卷保留。
- 
- 本机 Docker Desktop 4.60.1 曾在退出后遗留无法访问的 `dockerInference` / `engine.sock` 临时重解析点，导致下次启动报错。如果再次出现相同错误，先退出 Docker，然后执行 `powershell -ExecutionPolicy Bypass -File repair-docker-runtime.ps1`。脚本只在目录仅含已知临时套接字时备份并重建运行目录，不删除镜像、容器、数据卷或凭据。正常运行时无需执行此修复。
- 
- 后续仍需由教师与 3—5 名学生真人试玩，记录卡点并调整 15—20 分钟体验目标；自动化测试不代表教学效果验证。
- 
- 技术依据：[inkjs 官方仓库](https://github.com/y-lohse/inkjs)、[Judge0 提交文档](https://github.com/judge0/judge0/blob/v1.13.1/docs/api/submissions/submissions.md)、[Isolate 官方手册](https://github.com/ioi/isolate/blob/v2.7/isolate.1.txt)。
- 

+ # 旧塔的最后一盏灯
+ 
+ **新版 Ren’Py 单机 Demo 已加入。** 双击 `启动Demo.cmd` 或 `启动RenPy新版.cmd`；独立源码在 `client/`，Windows 离线包在 `release/TowerLastLight-1.1.0-win.zip`。新版使用真实 C11 / WASI 本地执行，全部图片由 image_gen 生成，背景、人物、灯、门均独立分层。详见 [新版实现说明](docs/renpy/IMPLEMENTATION.md)。以下保留旧网页版说明。
+ 
+ **拾光 Studio（可视化编辑器 + 后台 API + MCP 制作工具 + 独立展示器）已加入实施。** 后台工程服务、人工编辑器、MCP 工具适配器、制作插件与 Skill、独立展示器的启动方式、目录结构和验证命令见 [后台与编辑器实施说明](docs/studio-backend.md)；插件包说明见 [studio-author 插件](plugin/studio-author/README.md)；本次实施的审查入口在 [review/studio/HANDOFF.md](review/studio/HANDOFF.md)。它不替代下面的旧网页版 Demo。
+ 
+ 《术式纪元》序章技术验证 Demo。Vue 3 + TypeScript + Vite / Ink + inkjs / Monaco / FastAPI / Judge0 / IndexedDB。
+ 
+ ## 旧网页版启动
+ 
+ 在 Windows 上双击 **启动网页版Demo.cmd**，然后打开 **http://127.0.0.1:5173**。
+ 
+ 已有依赖的本机再次启动通常只需几秒；首次安装和 Docker 镜像构建需要网络。需 Node.js 22+、Python 3.12、Docker Desktop 的 Linux 容器引擎。不要直接双击 index.html。
+ 
+ ```powershell
+ cd C:\codex\C_game
+ .\start.ps1
+ ```
+ 
+ 启动器自动准备项目内依赖、启动四个 Judge0 容器、后台启动 API 和前端。日志在 `.runtime/`。网页端口为 5173，业务 API 为 8000，Judge0 为 2358，均只监听本机。Judge0 启动需要额外等待；游戏中的连接检查会真正编译并运行一段 C，而不仅检查 HTTP 存活。
+ 
+ 试玩时可最小化 Docker 窗口，让它留在系统托盘；请勿使用 Pause 或 Quit，这会暂停/关闭真实编译服务。如果显示 manually paused，在 Docker 菜单中选择 Resume 恢复即可。
+ 
+ 只启动界面与业务服务可运行 `powershell -ExecutionPolicy Bypass -File start.ps1 -SkipJudge`。此模式如果没有可用执行器，运行会如实显示“执行服务暂不可用”，无法通关，不会模拟成功。
+ 
+ ## 操作
+ 
+ - 开始旅程，输入昵称，阅读剧情并选择回应。空格或 Enter 可推进普通对话。
+ - 第一关可直接在 Monaco 中增删、粘贴或修改完整 C 代码；也可在代码下方“输出内容”中将 `0` 改成 `1`。删除原有 printf 后快捷输入会隐藏，仍可直接编辑代码。
+ - 第二、三关均可直接增删和替换完整 C 代码。第二关先运行默认代码，再把 `i < n` 改为 `i <= n`，也可使用“循环条件”快捷输入。
+ - 代码上方的“标准输入 n”可手动填写 0—6 的整数，点击“运行术式”时会自动传给 `scanf`，无需在输出区域输入。第二关提交固定验证 n=3；第三关继承第二关代码，最后“验证全部回路”固定检查 0、1、3、6。蓝色行仅为提示，不限制编辑范围。
+ - “运行术式”只试运行；“提交验证”成功后先保存，再允许推进。已提交关卡锁定草稿，结算后可进入完整代码自由实验。
+ - 提示分三级，使用不扣分。恢复初始代码需要确认。设置中可调整代码字体、静音、减少动态效果、导入导出存档。
+ - 26 秒出口演出可跳过；两条路径抵达相同的未来残响。结算有学习记录、奖励和自由实验。
+ 
+ ## 实现边界
+ 
+ 两个主场景、一位原创 SVG 角色的三种表情、六盏独立灯、原创程序生成的音乐与音效。场景为 SVG/CSS 三层视差，不依赖外部图片、音频或字体。美术采用可迭代的矢量风格，未使用商业插画或配音。
+ 
+ 每次请求后端保留不可变源代码字符串与 SHA-256。第三关提交固定跑 0、1、3、6。按完整 stdout 判定；只统一 CRLF、容许一个末尾换行，不过滤重复、空行、空格或额外文字。n=0 必须是完全空 stdout。
+ 
+ 灯光每步约 300ms，只是 stdout 回放。试运行/提交、编译失败/错误答案/运行失败/超时/输出超限/服务不可用分别呈现。编辑期间返回的旧结果按请求世代、任务、源代码和散列共同丢弃。
+ 
+ Ink 编译器与运行时均使用锁定的 inkjs 2.4.0；构建时从 `story/tower.ink` 生成 JSON，避免字节码版本错配。IndexedDB 一个快照保存 Ink 状态、当前显示行/选择、草稿、任务和奖励，`save_revision` 串行写入。奖励 ID `tower-exit-v1` 去重。导入先校验类型、版本、任务一致性和可加载的 Ink 状态，校验失败不覆盖当前存档。
+ 
+ 日志含每次提交源码、散列、任务进入退出、错误类别、失败输入、最高提示和理解题首次答案。只保留最近 2,000 条；页面隐藏时暂停计时。IndexedDB 存档可修改，仅作 Demo 进度，不能用作可信考试成绩。
+ 
+ ## 执行部署
+ 
+ `infra/compose.yaml` 部署 Judge0、worker、PostgreSQL、Redis；后两者只服务 Judge0，并非游戏业务数据库。浏览器只能访问 FastAPI。可用后端环境变量 `JUDGE0_URL`、`JUDGE0_TOKEN` 连接自有 Judge0；C 语言 ID 通过实例 `/languages` 查找，优先最高版本 GCC，编译固定 `-std=c11 -O0`。
+ 
+ 此机使用 cgroup v2。Judge0 1.13.1 原镜像的旧 Isolate 不兼容，因此 Dockerfile 从官方固定提交构建 **Isolate 2.7** 静态二进制。入口脚本只在容器私有 cgroup 命名空间内创建管理/执行子组，不改宿主全局 cgroup。学生进程保留命名空间、seccomp、网络隔离、CPU/内存/文件限制。没有挂载应用目录、Docker socket 或宿主凭据。
+ 
+ 每个运行用例 CPU 1 秒、墙钟 3 秒、内存 64MiB、文件输出 16KiB。达到 stdout/stderr 上限或 SIGXFSZ 时标为输出超限，不能拿截断输出判成功。编译资源另设 5 秒 CPU / 10 秒墙钟 / 256MiB / 4MiB 文件上限，以容纳编译器和可执行文件。后端最多两个评测请求并发、12 个活动请求、100 秒整体超时、500 条临时结果上限。
+ 
+ 这是本机教学技术验证部署；Judge0 需要 privileged 容器，数据库密码是明确的本地示例值，不应原样暴露到公网。正式多用户发布应在独立评测主机部署，增加鉴权、配额、服务监控与隔离审计。
+ 
+ ## 验证命令
+ 
+ ```powershell
+ npm ci
+ npm run build
+ npm test
+ npm run studio:test          # 后台/工具/内核/预览/插件（真实进程）
+ npm run studio:plugin        # 插件打包与结构检查
+ npm run studio:evidence -- --groups quick,core --e2e   # 生成证据包
+ .\.venv\Scripts\python.exe -m pytest tests/test_backend.py -q
+ # 需前后端及 Judge0 启动
+ .\.venv\Scripts\python.exe scripts/verify-live.py
+ npm run test:e2e
+ ```
+ 
+ 浏览器自动化使用本机 Microsoft Edge。`test-results/report/index.html` 是浏览器测试报告；`.runtime/live-verification.json` 是真实执行详情；`.runtime/` 内另有实际界面截图。完整流程测试在 Judge0 健康检查失败时会明确跳过，不能把跳过算作验证通过。
+ 
+ ## 开发入口与目录
+ 
+ 开发服务器 URL 加 `?debug=1` 才显示调试入口；生产构建关闭。调试状态写入独立 `debug` 存档，不覆盖正式 `main` 存档，不计入完成或奖励。
+ 
+ | 路径 | 内容 |
+ | --- | --- |
+ | `src/useGame.ts` | 剧情协调、提交版本控制、保存与回放 |
+ | `src/runtime/` | 共用运行内核：事件目录、内容模型、剧情推进与存档 |
+ | `src/studio/live/` | 后台工程视图的人工编辑器（`studio-live.html`） |
+ | `server/src/` | 后台服务：API、编辑服务、journal 存储、认证会话、MCP 工具适配 |
+ | `src/player/` | 独立展示器（发布包入口 `player.html`，共用 `src/runtime/` 内核） |
+ | `plugin/studio-author/` | 制作插件包：清单、MCP 连接配置、Skill 与适配器构建产物 |
+ | `docs/studio-backend.md` | 后台/编辑器/插件/展示器的运行与验证说明 |
+ | `review/studio/` | 审查材料：HANDOFF、需求映射、接口索引、改动清单、已知问题、基线 |
+ | `artifacts/studio-review/` | 每轮验证证据包（run.json、原始日志、脱敏轨迹、产物散列） |
+ | `tests/studio/` | 后台/工具/恢复/预览/插件的单元与真实进程集成测试 |
+ | `src/save.ts` | IndexedDB 与导入校验 |
+ | `src/components/CodeEditor.vue` | Monaco、完整代码编辑与错误定位 |
+ | `src/components/TowerScene.vue` | 石室、独立灯、三层出口 |
+ | `src/components/Character.vue` | 璃的三种矢量表情 |
+ | `src/audio.ts` | Web Audio 原创乐段与音效 |
+ | `story/tower.ink` | 可直接编辑的剧情源文件 |
+ | `backend/tasks.py` | 后端任务、输入与严格输出协议 |
+ | `backend/judge.py` | Judge0 真实 C11 执行适配 |
+ | `backend/app.py` | 队列、任务与轮询接口 |
+ | `infra/` | 隔离执行器的可重复构建配置 |
+ | `tests/` | 单元与浏览器测试 |
+ 
+ 停止当前 Demo 的网页/API 可结束 `.runtime/api.pid` 与 `.runtime/web.pid` 对应进程；执行器用 `docker compose -f infra/compose.yaml stop` 停止，数据卷保留。
+ 
+ 本机 Docker Desktop 4.60.1 曾在退出后遗留无法访问的 `dockerInference` / `engine.sock` 临时重解析点，导致下次启动报错。如果再次出现相同错误，先退出 Docker，然后执行 `powershell -ExecutionPolicy Bypass -File repair-docker-runtime.ps1`。脚本只在目录仅含已知临时套接字时备份并重建运行目录，不删除镜像、容器、数据卷或凭据。正常运行时无需执行此修复。
+ 
+ 后续仍需由教师与 3—5 名学生真人试玩，记录卡点并调整 15—20 分钟体验目标；自动化测试不代表教学效果验证。
+ 
+ 技术依据：[inkjs 官方仓库](https://github.com/y-lohse/inkjs)、[Judge0 提交文档](https://github.com/judge0/judge0/blob/v1.13.1/docs/api/submissions/submissions.md)、[Isolate 官方手册](https://github.com/ioi/isolate/blob/v2.7/isolate.1.txt)。
+ 
