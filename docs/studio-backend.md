# 拾光 Studio 后台、编辑器与工具（实施说明）

本文件说明本轮实施的后台服务、人工编辑器、MCP 工具适配器与验证入口。它是**实际代码的运行说明**，与 `docs/studio-demo.md` 描述的旧体验版工作台相互独立（旧工作台保持可用，仍使用浏览器 IndexedDB 保存体验版配置）。

审查入口：[review/studio/HANDOFF.md](../review/studio/HANDOFF.md)。

## 1. 组成

| 部分 | 入口 | 说明 |
| --- | --- | --- |
| 后台工程服务 | `npm run studio:server` → `server/src/main.ts` | 独立 Node 进程；启动脚本默认 127.0.0.1:5191，实际地址以指定工作区发现记录为准；正式工程的唯一写入者 |
| 人工编辑器 | `studio-live.html`（开发服务器或便携启动器提供地址） | 通过后台 API 新建、组织和编辑工程；`?api=` 指向实际后台 |
| MCP 工具适配器 | `npm run studio:mcp` → `server/src/mcp-stdio.ts` | stdio MCP 服务，把工具调用转发到后台 API |
| 制作插件与 Skill | `npm run studio:plugin` → `plugin/studio-author/` | 插件包（清单、MCP 连接配置、`studio-author` Skill 与 references、适配器构建产物），说明见 `plugin/studio-author/README.md` |
| 无界面预览 | `POST /v1/projects/{id}/previews`；工具 `studio_start_preview` / `studio_preview_action` / `studio_render_preview` | AI 与人工都可以在没有窗口的情况下运行工程内容 |
| 独立展示器 | `player.html`（源码）/ `npm run build:player` → `dist-player/` | 与编辑器预览共用 `src/runtime/kernel.ts` |
| 发布包导出 | `POST /v1/projects/{id}/exports`；工具 `studio_export_project` | 生成可独立运行的目录（内容 + 素材 + 展示器 + manifest + serve.mjs） |
| 共用运行内核 | `src/runtime/kernel.ts` | 剧情推进、存档、关卡消息处理；后台预览与展示器共用 |

工程与凭据默认放在工作区目录 `.studio-workspace/`（可用 `--workspace` 或 `STUDIO_WORKSPACE` 覆盖）。工作区可以放在仓库外；`.studio-workspace/` 已在 `.gitignore` 中。

## 2. 启动

```powershell
cd <仓库目录>

# 1) 启动后台（占用 127.0.0.1:5191；首次启动会生成配对密钥并写发现记录）
npm run studio:server

# 2) 需要外部 AI 制作时，由 AI 宿主启动 MCP 适配器（stdio）
npm run studio:mcp
#    或让宿主执行：node server/src/mcp-stdio.ts --workspace <工作区>

# 3) 人工编辑：另开一个终端启动前端
npm run dev
#    然后打开 http://127.0.0.1:5173/studio-live.html
#    后台不在默认端口时用 ?api=http://127.0.0.1:<端口> 指定
```

检查状态与配对信息：

```powershell
npm run studio:server:status   # 打印发现记录与健康检查结果（不含凭据）
npm run studio:pair            # 生成（或查看）配对密钥摘要；--rotate 轮换密钥
```

MCP 适配器可用的环境变量：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `STUDIO_WORKSPACE` | `<仓库>/.studio-workspace` | 工作区目录 |
| `STUDIO_AUTOSTART` | `1` | 后台未运行时是否由适配器拉起后台；设为 `0` 时后台不可用直接返回 `BACKEND_UNAVAILABLE` |
| `STUDIO_CLIENT_NAME` | `mcp-client` | 会话 actor 名称（`ai:<名称>`） |
| `STUDIO_SCOPES` | `read,edit,generate,preview,export` | 会话作用域 |
| `STUDIO_SESSION_TOKEN` | 未设置 | 直接使用既有会话令牌（跳过配对） |

## 3. 工程目录（工作区内）

```text
<workspace>/
  projects.json                  工程注册表
  imports/                       默认的素材导入根目录（AI 只能从这里导入文件）
  <projectId>/
    project.json                 工程信息、入口场景、格式版本
    story/chapters/<ch>.json     章节
    story/scenes/<sc>.json       场景与事件序列
    characters/<chr>.json        角色
    variables.json               变量
    assets/manifest.json         素材索引
    assets/originals/            素材原始文件（按稳定 ID 命名）
    levels/<lv>.json             关卡定义
    themes/theme.json            可编辑的对白与菜单主题
    generation/recipes/<id>.json 图片/视频生成模板
    .studio/state.json           修订、幂等记录、变更序号、对象版本
    .studio/history/             已提交事务与补偿操作
    .studio/journal/             提交备份与恢复标记
    .studio/snapshots/           保存检查点
  .studio-service/
    runtime.json                 发现记录（端口、pid、版本；不含凭据）
    config.json                  导入根、生成预算等配置
    credentials/pairing.key      本机配对密钥（权限 600）
    credentials/sessions.json    会话（只保存令牌 SHA-256 摘要）
```

## 4. 编辑路径

空工作区显示欢迎页与“新建作品”。公共 `POST /v1/projects` 会生成工程、第一章与开场，返回稳定 ID；不需要 Demo 素材就能写对白。作品列表有独立刷新入口，章节/场景可以新建、重命名、调整顺序、删除并设置开场。事件列表提供插入、编辑、排序和删除；未提交草稿阻止会丢失草稿的切换或操作，并可明确提交/放弃。

“素材与作品设置”提供素材、变量、角色、关卡、主题和生成模板。素材从文件选择入口导入，按用途/名称显示并可绑定剧情；关卡选择 HTML 文件安装程序，而非只登记路径。UI 与 level.installProgram 只安装一个 HTML，没有 CSS/JS/JSON 通用多文件依赖安装动作。脚本、样式与数据优先内嵌，图片媒体走素材导入；不要直接写正式工程补依赖。已有包内依赖仍须核对存在并真实运行。变量可新建、改初值、删除；角色可新建、重命名、删除；主题可改对白文字色、字号、对齐与菜单强调色。生成模板可新建、改提示词、删除；提交任务时带 templateId，服务端合并模板提示词/负向提示词/参数，请求的显式非空值优先。

工具栏提供“试玩当前场景 / 从头试玩”和导出入口，导出后可“验证导出作品”。这些入口运行实际播放器；后台结构快照是另外一种验证。界面与工具的写入都经过后台，不直接改正式工程文件；一次相关内容修改使用同一个事务，并可通过补偿事务撤销。

人工与 AI 共用同一条路径：`编辑器界面 / MCP 工具 → 后台 HTTP API → EditService → journal 提交`。

- 工具清单与参数规范：`GET /v1/capabilities`（或 MCP `tools/list`）。索引见 `review/studio/api-tool-map.json`。
- 事务语义：`baseRevision` 过期返回 `REVISION_CONFLICT`；同 `requestId` 同内容重试返回既有结果；同 `requestId` 不同内容返回 `REQUEST_ID_CONFLICT`。
- 撤销：`POST /v1/projects/{id}/undo` 生成补偿事务；若其后有提交改过同一对象，返回 `UNDO_DEPENDENCY_CONFLICT` 而不是覆盖。
- 暂停 AI 写入：人工编辑器右栏“协作与连接信息”中的“AI 会话写入”可以暂停/恢复某个 AI 会话；后台在提交点再次校验。

AI 制作 Skill 不使用 UI 自动化代替内容事务，但允许只读浏览器观察、真实播放及用户授权的人工入口验收。产品修复与内容制作是不同任务，用户明确授权修复编辑器/展示器/后台时可修改其代码，不应要求重复确认。服务地址、版本、工作区和 importRoots 必须从实际运行环境发现；源码修复后还需重建浏览器/便携包，复制包不会自动跟随仓库更新。

## 5. 预览、展示器与发布包

```powershell
# 预览（AI 工具或 HTTP 都可以）
#   POST /v1/projects/{id}/previews                      → previewId、状态、当前对白
#   POST /v1/projects/{id}/previews/{pid}/actions        → advance / choose / tick / effectComplete / effectFailed / levelMessage
#   POST /v1/projects/{id}/previews/{pid}/render         → 结构化舞台快照（.studio/previews/*.html|json）

# 展示器构建与发布包
npm run build:player                                   # 生成 dist-player/（相对路径产物）
#   POST /v1/projects/{id}/exports                     → exports/<exportId>/（含 manifest.json 与 serve.mjs）
node <工作区>/<工程>/exports/<exportId>/serve.mjs --port 5199
#   浏览器打开 http://127.0.0.1:5199/player.html
```

预览与展示器都用 `src/runtime/kernel.ts` 推进剧情；预览返回的是状态与结构化快照，展示器负责真实画面。
展示器已实现音频/视频真实播放与错误回执，详见第 6 节。后台预览的模拟宿主动作只能检查内核状态，不代表浏览器媒体播放成功。

发布包包含：`content/`（内容包与素材）、`content/levels/`（关卡程序）、展示器构建产物、`manifest.json`（文件散列、修订、敏感信息与素材/关卡缺失检查结果）、`serve.mjs`（本地静态服务器）。它不包含 `.studio` 运行数据、会话凭据、制作 API 或编辑入口。

保存后回读工程修订，核对 checkpoint 的 persistedRevision 与 verifiedFromDisk；导出 revision 须等于最终修订。检查 playerIncluded、missingAssets、missingLevels 与 sensitiveHits，声称双击便携交付还须 portableIncluded。缺字段表示旧运行包未提供此检查，不能当通过。关卡入口缺失会拒绝校验/导出；入口存在也需在真实播放器检查依赖请求和实际任务交互。最后打开本次导出的 player.html 验证剧情与关卡，记录该包的修订、URL 和实际检查范围。

## 5.1 渲染产物（结构化快照与像素截图）

`POST /v1/projects/{id}/previews/{previewId}/render` 默认返回**结构化舞台快照**（HTML + JSON，写入 `.studio/previews/`）；请求体带 `format: "pixel"` 时，额外用无头浏览器把同一份快照渲染成 PNG 写入 `.studio/renders/`，响应中的 `pixel` 字段给出 `renderPath`、`sha256`、`width`、`height`、`bytes`、`renderer`。

- 画布尺寸 `width`/`height` 限 160–2560（默认 1280×720）。
- 浏览器优先使用本机 Edge/Chrome，其次 Playwright 自带内核；都不可用时返回 **503 `RENDER_UNAVAILABLE`**，不会生成假图片。`STUDIO_RENDER_DISABLE=1` 可强制关闭该能力（结构化快照不受影响）。
- MCP 工具 `studio_render_preview` 暴露同样参数（`format`、`width`、`height`）。

像素渲染证明结构快照可以出图，不能证明人工编辑器入口、素材选择、音视频播放或 iframe 关卡任务可用。入口修复验收应使用独立空工作区，从新建作品、创作/导入到试玩、导出，再打开导出播放器；API fixture 和旧 Demo 截图不替代这条人工路径。

## 6. 展示器（独立播放器）

发布包根目录的 `player.html` 是独立展示器：标题页（开始新游戏 / 继续上次进度）、舞台与选项、历史对白、**3 个存档槽**、**文本速度设置**、自动播放与 HTML 关卡承载。

- 存档按作品 ID 分开：槽位 1 为 `shiguang-player-save-v1:<编码后的projectId>`，槽位 2/3 追加 `:slot2/3`；标签显示保存时间、修订与状态。它们是当前浏览器来源的本地存档，改变网址端口或浏览器不会自动迁移；当前无通用存档文件导入导出 UI。
- 设置键：`shiguang-player-settings-v1`；文本速度 慢/正常/快 对应自动播放等待 2200/1200/500ms。
- 读档校验作品身份、修订、内核与格式；缺少 projectId 的旧存档也明确拒绝。对白、选择、剩余等待与结局直接恢复，不重复追加历史。读取失败保留当前进度。
- 音频：按声道执行播放/停止/淡出、循环与音量；背景声道替换当前实例。waitForEnd 等实际结束，作者应关闭循环以避免永不结束。媒体缺失、播放被拦截或解码失败会提示或回执。
- 视频：遵守 skippable 与 onEnd/onError；不可跳过时不显示跳过按钮，onEnd=stop 结束剧情，onError=replay 最多一次重播且换 effectId，第二次失败记录错误并继续。实际播放测试使用 `tests/studio/fixtures/sample-1s.mp4`；测试生成适配器仍不能产出可播放影片。
- 关卡中途存档：定义与事件双方允许快照时才保存并在 init 下发；程序须实现 snapshot 恢复。正常进入新关卡或同关卡循环清空旧快照、结果与去重记录。存档保留等待中的音频、视频、关卡效果；音视频读档从头播放，不恢复 currentTime，已活动的背景音乐/环境音声道另行恢复。

## 7. 图片/视频生成（P4）

```powershell
# 能力与费用
#   GET  /v1/media/providers                           → 服务与模型能力（当前只有 fixture 测试适配器）
#   POST /v1/projects/{id}/media-jobs/estimate         → 校验 + 费用估算 + 预算状态（不创建任务）
# 任务
#   POST /v1/projects/{id}/media-jobs                  → 提交任务（返回 jobId，进入后台队列）
#   GET  /v1/jobs/{jobId}                              → 状态、阶段、候选产物、错误
#   GET  /v1/projects/{id}/media-jobs                  → 任务列表与预算
#   POST /v1/jobs/{jobId}/cancel                       → 请求取消（服务确认后才变为已取消）
#   POST /v1/projects/{id}/media-jobs/{jobId}/accept   → 接受候选：登记素材（+ 可选绑定剧情）
```

状态机：`queued → submitted → generating → downloading → verifying → available`，另有 `cancelling / cancelled / failed / unknown`。后台每 400ms 推进一次队列（不依赖编辑器窗口）；进程重启后按同一服务任务 ID 继续查询，**不会重新提交**。提交结果未知时标记 `unknown` 并停止，避免重复付费。

**当前只有测试适配器** `fixture-local`：`isTestAdapter=true`，不访问网络、不产生真实费用；图片产物是真实 PNG，视频产物只有 EBML 容器头并标注 `playable=false`（仅用于验证协议链路）。真实图片/视频服务属于未接入项，见 `review/studio/known-issues.md` 的 KI-003。

接受候选时「登记素材 + 绑定剧情」在同一个编辑事务中完成：目标对象已被改过时事务整体被拒绝，候选仍保留在 `assets/candidates/<jobId>/`，不会覆盖人工修改。生成工具共 7 个（`studio_list_media_providers`、`studio_estimate_media`、`studio_create_media_job`、`studio_get_job`、`studio_list_jobs`、`studio_cancel_job`、`studio_accept_media_output`）。

## 8. 关卡程序协议（宿主 ↔ 关卡）

关卡是内容包 levels/&lt;id&gt;.html 里的 HTML 程序，运行于仅 allow-scripts 的不透明来源 iframe。宿主通过 postMessage 核对来源窗口、协议和实例；关卡不能读取父页 DOM、localStorage 或凭据。parent.postMessage 用于协议通信。

| 方向 | 消息 | 语义 |
| --- | --- | --- |
| 关卡 → 宿主 | type=ready | 程序已就绪，等待初始化 |
| 宿主 → 关卡 | type=init（levelInstanceId、protocolVersion、payload、snapshot） | 开始或恢复；payload 是关卡事件的 initPayload，snapshot 是存档中的进度快照（无则为 null） |
| 关卡 → 宿主 | type=checkpoint（payload、requestId、messageSeq） | 记录进度点（会随存档保存） |
| 关卡 → 宿主 | type=complete（payload、requestId、messageSeq） | 关卡完成，payload 作为关卡结果交给内核 |
| 关卡 → 宿主 | type=error（payload 含 code/message） | 关卡失败：运行继续并记录错误，不会永久等待 |

宿主按 requestId 与 messageSeq 丢弃重复或迟到的 checkpoint/complete；关卡程序应在 snapshot 非空时从该快照恢复自身进度。

宿主注入的 fetch 桥只允许当前内容根的 assets/levels GET/HEAD，以及 initPayload JSON字符串或对象显式指定本机 http runner 的 GET /health、POST /run；C 执行器需独立配置启动。禁止任意其他端点、外网、直接XHR、frames/workers，不放宽CORS或allow-same-origin。内联与经典脚本属于支持范围，不透明来源下的外部ES module import未承诺。完整契约与可安装最小HTML见 [中文使用手册](USER_GUIDE.zh-CN.md)。

## 9. 插件与制作 Skill

```powershell
npm run build:plugin         # 用 esbuild 把 MCP 适配器打包进插件目录
npm run studio:plugin:check  # 结构检查：清单、Skill frontmatter、工具引用、无写文件 API、无凭据
npm run studio:plugin        # 上面两步
```

插件包（`plugin/studio-author/`）包含：

- `.codex-plugin/plugin.json`：插件清单。**字段名与必需项尚未在目标宿主中验证**（本机没有可核对的宿主插件规范）。
- `.mcp.json`：MCP 工具服务连接配置（`${PLUGIN_DIR}` 与 `${STUDIO_WORKSPACE}` 需在安装时替换；配置内不含密钥）。
- `skills/studio-author/SKILL.md` + `references/`：制作流程、工程规则与素材/生成现状说明。Skill 只引用工具名，参数以 `tools/list` 的 Schema 为准。
- `server/mcp-stdio.mjs`：适配器构建产物。结构检查会验证它**不含任何写文件 API**——工具层物理上无法绕过后台改写工程文件。

Skill 的 frontmatter 格式与本机实际存在的 Skill（`C:\deepseek_harness\resources\runtime\office-skills\*\SKILL.md`）一致。宿主加载验证属于未验证项（见 `review/studio/known-issues.md` KI-001 与 KI-022）。

## 10. 验证入口

```powershell
npm run typecheck          # vue-tsc：src/ + server/ + tests/ 全量类型检查
npm run studio:test:quick  # 协议、内核、编辑服务单元测试
npm run studio:plugin      # 插件打包与结构检查
npm run studio:test:core   # 真实后台进程 + MCP stdio 进程 + 崩溃恢复 + 权限
npm run studio:test        # 以上全部
npm test                   # 仓库全部 vitest（含旧协议/工作台用例）

# 人工界面与展示器浏览器验证（需要先 npm run dev）
npx playwright test tests/e2e/studio-live.spec.ts --reporter=list    # 人工编辑器
npx playwright test tests/e2e/p2-player.spec.ts --reporter=list      # 独立展示器（自动导出并启动发布包）

# 生成完整证据包（含源码指纹、原始日志、脱敏轨迹、需求交叉核对、artifacts.sha256）
npm run studio:evidence -- --groups quick,core --e2e --label "说明"
```

`npm run studio:evidence` 只在无法解析到用例时才会把分组写成失败；它不会把跳过写成通过。`requirements-check.json` 会把 `requirements.json` 里声明为“已验证”但本轮没有通过用例的需求标成 `declarationMismatch`。

## 11. 发布验收、规模测量与旧工程迁移

```powershell
# 发布组：建样板工程 → 导出发布包 → 验收检查（含实际启动）
npm run studio:release                  # 报告写到 .runtime/release-run/release-check.json
# 单独检查已有发布包
node scripts/studio-release-check.mjs --package <发布包目录> --run

# 性能组：按规模经 API 生成测试工程并测量方案 11 的指标
npm run studio:scale                    # mid 规模（50 章 / 200 场景 / 1 万事件 / 300 素材）
node scripts/studio-scale.mjs --profile target --json .runtime/scale-target.json   # 目标规模（耗时长）

# 旧 Studio v1 配置迁移（先预览再执行）
npm run studio:migrate -- --input <v1 工程.json> --dry-run
npm run studio:migrate -- --input <v1 工程.json> --workspace <工作区> --json <报告>
```

发布验收检查项：清单与文件一一对应、全部文件散列、素材齐全且内容散列一致、无凭据/会话/运行数据、无本地绝对路径依赖、发布包可实际启动（`serve.mjs` + 抓取播放器与内容包）。篡改包内文件会被如实判为失败。

**规模实测**（本机，同一脚本）：

| 规模 | 暖启动索引（目标 3000ms） | 索引搜索 P95（目标 300ms） | 单场景读取（目标 1000ms） | 文本编辑 P95（目标 100ms） |
| --- | --- | --- | --- | --- |
| mid：50 章 / 200 场景 / **1 万事件** / 300 素材 | 10ms ✅ | 17ms ✅ | 10ms ✅ | **73ms ✅**（优化前 138–164ms ❌） |
| target：100 章 / 500 场景 / **10 万事件** / 1000 素材（工程 91.14 MB / 5883 文件） | 17ms ✅ | 57ms ✅ | 17ms ✅ | **95ms ✅** |

事务阶段计时（`STUDIO_TIMING_FILE=<file>`，1638 个单操作事务）：clone p95 4.3ms、apply 0.9ms、validate 1.1ms、plan 0.9ms、commit 37.5ms、cache 4.2ms，服务内合计 p95 47.3ms；因此 200ms 中的主要部分在服务之外，需要给 API 层加计时定位。

1 万事件规模已达标（内容缓存 + 增量保存：`ProjectCache` 用文件大小/修改时间指纹判断磁盘变化，提交只序列化与 `changedIds` 相关的文件，`verifyPlan` 在测试中比对增量与全量计划）；**10 万事件下编辑延迟仍超标**，剩余成本是每次提交克隆整份内容 + 全量校验，需要事件级增量校验。媒体负载（`--media-mb`）实测：1.07 GB（512 个约 2MB 的 PNG，合计 1,074,294,784 字节）导入耗时 47.4s，**吞吐 21.6 MB/s**，单次导入 p50 36ms / p95 46ms，导入期间后台峰值内存 130MB（整轮 162MB），工程体积 1027 MB / 2159 文件；20GB 级按要求未生成（按吞吐推算约 15–16 分钟，属推算）。原始数据见证据包 `outputs/scale-mid.json` 与 `outputs/scale-target.json`。

迁移工具会保留原始配置（复制进工程 `imports/`），逐条列出支持与不支持的字段：关卡节点不自动迁移（需先安装关卡程序），引用工程外路径的素材需要重新导入；旧试玩存档不承诺兼容。

## 12. 离线便携交付物（P5）

```powershell
npm run studio:package            # 组装 dist-studio/ 并压缩为 dist-studio.zip
node scripts/studio-package.mjs --out <目录> [--zip]
```

便携包内容：`app/server`（后台源码，只用 Node 内置模块）、`app/src/runtime`（共用内核）、`app/dist`（编辑器）、`app/dist-player`（展示器）、`app/plugin`（制作插件包与 MCP 适配器）、`app/scripts`（发布检查、迁移、静态托管）、`runtime/`（Node与许可）、`docs/`（使用手册与截图）、`manifest.json`（逐文件散列与入口）、`studio.cmd` 和 `README.md`。

0.1.1 Windows x64 便携包内置经官方散列核对的 Node 24.14.1，无需另装 Node、Python、npm 或 Docker。默认 editor/player 均空白；--with-demo 才两包加入样例。浏览器由系统提供，像素级渲染另需可用 Chromium 系浏览器。源码开发和其他未附带runtime的导出目录仍需本机Node环境。

验收（`tests/studio/integration/p5-package-offline.test.ts`）：清单散列逐文件核对、入口存在、无 node_modules、第一方代码零外部服务地址（第三方包内的地址仅为 Monaco/Vue 许可证横幅）、包内后台完成真实制作流程并导出包含展示器的发布包、包内 MCP 适配器列出 ≥36 个工具。包内附带**脚本式安装/卸载**：`install.cmd`/`install.mjs` 把便携包复制到 `%LOCALAPPDATA%\ShiguangStudio`（可用 `--prefix` 指定、可创建快捷方式、不写注册表、不需要管理员权限），`uninstall.cmd`/`uninstall.mjs` 彻底删除。验收测试会安装到临时前缀、用安装副本真实启动后台并通过健康检查、再卸载校验目录消失。**它仍然不是 MSI/NSIS 安装包，也没有代码签名**（KI-012）。

## 12.1 诊断与测试开关

| 环境变量 | 作用 | 说明 |
| --- | --- | --- |
| `STUDIO_CHANGE_LIMIT` | 变更流保留条数（默认 500） | 调小后可验证 `gap=true` 的截断路径（KI-004 用例即用 3） |
| `STUDIO_VERIFY_PLAN=1` | 每次提交用事务前的原始内容与全量计划双重比对 | 真实进程集成测试默认打开；生产不开（有额外开销） |
| `STUDIO_TIMING_FILE=<file>` | 事务阶段计时 + 请求阶段计时写入 JSONL | 仅测量时启用，正常运行零开销 |
| `STUDIO_RENDER_DISABLE=1` | 关闭像素级渲染 | 结构化快照不受影响；像素请求返回 503 `RENDER_UNAVAILABLE` |

`GET /v1/health` 额外返回 `rssMB` 与 `peakRssMB`（服务启动以来的 RSS 峰值，由提交时与每 5 秒的采样取最大），用于规模测量的内存占用核对。

## 13. 当前边界

历史未实现项与验证记录集中在 `review/studio/known-issues.md`，应结合当前代码、服务能力和本次证据读取，不能沿用旧版本限制。已提供人工创作入口、音视频播放、关卡快照、多存档槽、插件/Skill、规模测量、旧工程迁移与脚本式安装/卸载。

仍需明确区分：随包 fixture-local 为测试生成适配器，真实图片/视频提供方以当前配置为准；插件在真实 AI 宿主的加载需该宿主实测；脚本式安装不等于 MSI/NSIS 安装包或代码签名；复杂关卡外部依赖与 C 执行适配器必须在交付环境实际运行。某次 Demo 内容/浏览器检查不自动覆盖当前空工作区人工入口或另一份导出作品。
