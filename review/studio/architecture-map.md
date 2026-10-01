# 拾光 Studio 模块边界与调用链（architecture-map）

本文件描述本次实施的实际模块、依赖方向、写入边界与关键调用链。定位源码统一使用“相对路径 + 导出符号名”，行号只作辅助。

## 1. 模块职责与依赖方向

```text
src/runtime/            协议版本、事件目录、内容模型、运行内核（前端与后台共用，无平台依赖）
   ↑                                   ↑                        ↑
src/studio/live/        人工编辑器      src/player/  独立展示器   server/src/  后台服务（Node 独立进程）
   │  HTTP /v1 + SSE        │ 加载发布包内容包                              │
   └────────────────────────┴──────────────────────────────────────────────→ api（路由/认证/校验）
                                                                             ↓
                                                                       edit（领域服务：动作、事务、幂等、撤销）
                                                                             ↓
                                                                       store（journal 提交、工程文件、工作区注册表）
                                                                             ↓
                                                                       磁盘工程（唯一持久化写入点）

server/src/preview/    预览会话：调用同一运行内核，返回状态与结构化舞台快照
server/src/publisher/  发布打包：内容 + 素材 + 展示器 → 可独立运行目录 + 清单与敏感信息检查
plugin/studio-author/  制作插件包：清单 + .mcp.json + Skill/references + 适配器构建产物
server/src/tools/registry.ts   工具定义（唯一）
server/src/mcp-stdio.ts        MCP stdio 适配进程：工具调用 → 后台 HTTP API
server/src/lifecycle.ts        适配层可见的只读生命周期模块（发现/健康/启动），不含写文件能力
server/src/service-registry.ts 发现记录的写入与清理（只有后台服务进程使用）
```

写入边界补充：`server/src/store/fsread.ts` 只有读能力，`server/src/store/fsx.ts` 才有写原语。工具适配进程（`mcp-stdio.ts` → `lifecycle.ts` → `fsread.ts`）**不加载任何写文件函数**，`scripts/studio-plugin-check.mjs` 会检查打包产物中不存在写文件 API；这条静态保证是本项目“AI 不能直接改工程文件”的结构性依据之一。

依赖规则：

- `src/runtime/*` 不依赖后台与界面；被后台（`node` 直接运行 TS）与前端构建共同引用，是“共用运行内核”的物理保证。
- `server/src/api/*` 只做协议转换、认证与参数读取，不实现编辑规则。
- `server/src/edit/*` 是唯一的领域编辑实现；所有制作路径（人工界面、MCP 工具、CLI、撤销补偿）都经过它。
- `server/src/store/journal.ts` 的 `commitTransaction` 是唯一写正式工程文件的函数。

## 2. 人工编辑调用链（真实链路）

1. 浏览器 `src/studio/live/api.ts` → `LiveApi.bootstrap()` → `POST /v1/ui-session`（来源白名单校验，`server/src/api/server.ts` 的 `handle`）。
2. 人工点击“提交修改” → `LiveApi.transaction()` → `POST /v1/projects/{id}/transactions`。
3. 路由 `handle` → `authenticate`（`server/src/api/server.ts`）→ `requireScope('edit')`。
4. 领域入口 `EditService.applyTransaction`（`server/src/edit/service.ts`）：
   - 读工程快照：`loadProject`（`server/src/store/doc.ts`）
   - 读状态：`loadState`（`server/src/store/journal.ts`）
   - 幂等检查：`state.idempotency[requestId]` + `contentHash({apiVersion, projectId, operations})`
   - 基础版本检查：`request.baseRevision !== state.revision` → `conflict`
   - 会话暂停检查：`options.sessionGuard(actor)` → `SESSION_PAUSED`
   - 动作执行：`ACTIONS[action].apply`（`server/src/edit/actions.ts`）在内存快照上改写并产出 `inverse`
   - 整体校验：`validateStory`（`src/runtime/content.ts`）
   - 差异计算：`planFromContent`（`server/src/store/doc.ts`）
5. 提交与恢复：`commitTransaction`（`server/src/store/journal.ts`）写备份 → `manifest.json` → 内容文件 → 历史 → `.studio/state.json` → `committed.json` → 清理；启动时 `recoverProject` 回滚未提交事务。
6. 变更通知：`EditService.onChange` → `server/src/main.ts` 的转发 → `createStudioServer.emitChange` → SSE `GET /v1/projects/{id}/events` → `LiveApi.subscribe` → 界面刷新。

## 3. 工具调用链与协议版本

1. AI 宿主启动插件包内的 `plugin/studio-author/server/mcp-stdio.mjs`（由 `npm run build:plugin` 打包 `server/src/mcp-stdio.ts` 生成），transport 为 stdio + 换行分隔 JSON-RPC 2.0。
2. 适配器按 `STUDIO_WORKSPACE`（默认 `.studio-workspace`）→ `ensureService`（`server/src/lifecycle.ts`，只读模块）发现或启动后台；用本机配对密钥 `POST /v1/sessions` 建立带作用域的会话。
3. `tools/list` 直接来自 `TOOL_DEFINITIONS`；`tools/call` → `createToolCaller` → `tool.handler` → `HttpApiClient.request` → 后台 HTTP API。
4. 事务类工具缺省 `baseRevision` 时先读当前修订；缺省 `requestId` 由“工具名 + 操作内容散列”生成，保证同参数重试天然幂等。
5. 适配器不读工程内容文件（只读发现记录与配对密钥）、不写任何文件；后台不可用时返回 `BACKEND_UNAVAILABLE`。

协议版本：`API_VERSION=1`、MCP 协议 `2025-06-18`（兼容 2025-03-26 / 2024-11-05）、事件目录 `EVENT_CATALOG_VERSION=1`、运行内核 `RUNTIME_KERNEL_VERSION=1`、关卡协议 `LEVEL_PROTOCOL_VERSION=1`；均定义在 `src/runtime/version.ts`（后台入口 `server/src/version.ts` 再导出）。

## 4. 生成任务链路（P4，协议与测试适配器已实现；真实服务未接入）

1. 能力与费用：`GET /v1/media/providers` → `MediaJobService.describeProvider` → 适配器 `capabilities()`；`POST /v1/projects/{id}/media-jobs/estimate` → `validate()` + `estimate()` + 预算状态（不创建任务）。
2. 提交：`POST /v1/projects/{id}/media-jobs` → `server/src/api/server.ts#normalizeMediaRequest`（结构校验）→ `MediaJobService.createJob`（幂等按 requestId、校验前/估价后会话暂停检查、能力校验、预算预留）→ 写 `.studio/jobs/<jobId>.json` 并排队。
3. 推进：后台定时器（`server/src/main.ts#pumpTimer`，400ms）→ `MediaJobService.pump/step`：`submit → poll → resolveOutputs` → 解码校验（`decodeAsset`）→ 候选落盘 `assets/candidates/<jobId>/`（记 sha256 与 playable）→ `available`。先筛选 queued/submitted/generating/downloading/verifying/cancelling，再按创建时间调度；unknown 保留人工核对和费用预留，不占调度槽。
4. 失败与恢复：提交未知 → `unknown`（不自动重复提交）；查询未知 → `unknown` + 可重试错误；服务拒绝 → `failed` 并释放预算预留；下载失败 → `failed` 且不产生半截候选；取消 → `cancelling`，只有适配器确认后才是 `cancelled`；进程重启 → `recoverOnStart` 恢复记录，按同一 `providerJobId` 继续查询。
5. 采用候选：`POST /v1/projects/{id}/media-jobs/{jobId}/accept` → `server/src/media/accept.ts#acceptMediaOutput` → 一次编辑事务（`asset.registerGenerated` + 可选 `asset.bind`，带对象级期望版本）→ `commitTransaction`；素材 ID 按 jobId + outputId 的稳定散列生成，沿用已登记 ID 兼容重试；冲突时事务整体拒绝、候选保留，并回写 `bindingIssue`。
6. 适配器：`server/src/media/adapters/fixture.ts`（`isTestAdapter=true`，不访问网络；图片为真实 PNG，视频为 `playable=false` 的容器头占位文件）。真实云端/本地/工作流适配器尚未实现。

## 4.1 预览链路（P2，已实现）

1. `POST /v1/projects/{id}/previews` → `server/src/api/server.ts#handle` → `PreviewService.start`（`server/src/preview/service.ts`）→ `startRuntime`（`src/runtime/kernel.ts`）。
2. 动作：`POST /v1/projects/{id}/previews/{pid}/actions` → `normalizePreviewAction`（结构校验）→ `PreviewService.action` → `applyRuntimeAction`。修订不一致返回 `CONFLICT`。
3. 渲染：`POST /v1/projects/{id}/previews/{pid}/render` → `PreviewService.render` → 写 `.studio/previews/<pid>-NNN.html|json`（结构化舞台快照）。
4. 工具：`studio_start_preview` / `studio_preview_action` / `studio_get_preview` / `studio_render_preview`。
5. 预览会话保存在后台内存中，后台重启后失效（已知限制）。

## 4.2 展示器与发布链路（P2，已实现）

1. 构建：`npm run build:player`（`vite.player.config.ts`，`base: './'`、`publicDir: false`）→ `dist-player/player.html` + 相对路径资源块。
2. 导出：`POST /v1/projects/{id}/exports` → `server/src/publisher/service.ts#exportProject`：复制内容（含素材、关卡程序）→ 复制展示器产物 → 写 `manifest.json`（散列/修订/敏感信息/素材缺失）与 `serve.mjs`。
3. 运行：`node <导出目录>/serve.mjs --port <端口>` → 浏览器打开 `/player.html`；展示器加载 `content/content.json`，素材与关卡入口都相对于内容包目录解析。
4. 关卡宿主：`src/player/main.ts#hostLevel` 校验同源与实例 ID，`ready` 后下发 `init`，把 `checkpoint/complete/error` 转成内核消息；重复与迟到消息由内核按 `requestId`/`messageSeq` 丢弃，关卡容器放在独立顶层节点，避免重新渲染时重载 iframe。
5. 音频与视频由 src/player/main.ts 的 drainHostEffects / playAudio / playVideo 承载；真实 WAV 与 MP4 浏览器用例已通过，失败回执与跳过按内核约定继续。

## 5. 预览与展示器共享的源码入口（P2，已实现）

共用：`src/runtime/kernel.ts`（`startRuntime` / `applyRuntimeAction` / `saveRuntime` / `restoreRuntime`）、`src/runtime/content.ts`（内容模型与校验）、`src/runtime/events.ts`（事件目录）、`src/runtime/version.ts`（协议版本）。
环境适配部分：后台侧是 `server/src/preview/service.ts`（会话与渲染产物）；展示器侧是 `src/player/main.ts`（DOM 渲染、素材 URL 解析、iframe 关卡宿主、localStorage 存档）。
已验证：真实音视频播放、关卡快照恢复、3 个存档槽与文本速度设置，以及可选像素预览（依赖本机浏览器）。预览会话不跨后台重启；人工工程切换会重绑 SSE 并丢弃旧工程读取/回调（KI-039 已修复）；断线重连与客户端自动 gap 补齐仍未覆盖。

## 6. 存储位置

| 内容 | 位置 | 写入者 |
| --- | --- | --- |
| 工作区注册表 | `<workspace>/projects.json` | `server/src/store/workspace.ts` `writeRegistry` |
| 工程内容 | `<workspace>/<projectId>/project.json`、`story/{chapters,scenes}/*.json`、`characters/*.json`、`variables.json`、`assets/manifest.json`、`levels/*.json`、`themes/theme.json` | 只由 `commitTransaction` 写 |
| 素材原始文件 | `<workspace>/<projectId>/assets/originals/` | `commitTransaction`（二进制随事务写入，回滚时删除） |
| 运行状态/幂等/变更/对象版本 | `<workspace>/<projectId>/.studio/state.json` | `commitTransaction` |
| 事务历史 | `<workspace>/<projectId>/.studio/history/*.json` | `commitTransaction` |
| 提交日志与恢复 | `<workspace>/<projectId>/.studio/journal/<txnId>/` | `commitTransaction` / `recoverProject` |
| 保存检查点 | `<workspace>/<projectId>/.studio/snapshots/` | `EditService.checkpoint` |
| 服务发现（无凭据） | `<workspace>/.studio-service/runtime.json`、`service.lock` | `server/src/main.ts` |
| 凭据（配对密钥摘要/明文、会话令牌摘要） | `<workspace>/.studio-service/credentials/`（chmod 600） | `server/src/auth/sessions.ts` |
| 工作区配置（导入根等） | `<workspace>/.studio-service/config.json` | `server/src/store/config.ts` |
| 证据包 | `artifacts/studio-review/<runId>/` | `scripts/studio-evidence.mjs` 与集成测试 |
| 玩家存档 | 浏览器 localStorage 的 shiguang 存档键，3 个槽位 | src/player/main.ts |

## 7. 读写正式工程、启动外部进程、调用生成服务的入口清单

- 写正式工程：`server/src/store/journal.ts` `commitTransaction`（唯一）。`recoverProject` 会回滚或清理，属于恢复路径。
- 生成素材文件：`asset.registerFromFile` / `asset.registerGenerated` 返回 `binaryFiles`，仍由 `commitTransaction` 落盘。
- 读正式工程：`server/src/store/doc.ts` `loadProject`（后台）、`server/src/store/workspace.ts` `summarizeProject`。
- 启动外部进程：`server/src/lifecycle.ts` `ensureService`（按 `STUDIO_AUTOSTART` 启动 `server/src/main.ts`）；测试用 `tests/studio/helpers/harness.ts` `startBackend` 与 `McpClient`。
- 调用生成服务：无（P4 未实现）。`server/src/media/decode.ts` 只做本地文件头解析，不联网。
- 网络访问：仅 `127.0.0.1` HTTP（后台 API）与 SSE。后台不访问互联网。

## 8. 重要设计选择与偏离

1. **运行内核放在 `src/runtime/`，由后台直接以相对路径引用**：后台用 Node 24 的 TypeScript 类型擦除直接运行 `.ts`，因此不需要为共享内核做额外打包；代价是导入必须带 `.ts` 扩展名（已在 `tsconfig.json` 打开 `allowImportingTsExtensions`）。
2. **提交前在内存快照上完成全部校验**：事务冲突与校验失败不会产生任何文件写入；代价是每次事务重新读取工程文件（规模性能未验证，见 `EDIT-014`）。
3. **journal 备份回滚策略**：可保证多文件一致，但每次提交会复制被修改文件到 `.studio/journal/<txnId>/before/`，大文件（视频素材）提交会产生额外 IO；这是为“崩溃后半事务”付出的成本。
4. **人工界面会话由 `/v1/ui-session` 发放**：界面无法读取本机配对密钥文件，因此改为“来源白名单 + 仅监听本机 + 不含 admin 作用域”。偏离方案中“配对建立会话”的严格描述，残余风险记入 `review/studio/known-issues.md`。
5. **幂等指纹不含 baseRevision**：方案未规定指纹字段；这样冲突后按同一 requestId 重试不会被判为“同 ID 不同请求”。已记录在 `requirements.json` 的 `EDIT-005`。
6. **发现记录不含凭据**：`runtime.json` 只有地址、pid、版本、工作区路径；配对密钥在 `credentials/pairing.key`（600），会话令牌只存 SHA-256 摘要。
7. **旧工作台未改动**：`src/studio/Studio.vue` 仍使用浏览器 IndexedDB 保存体验版配置，保持原 Demo 可用；它不是正式工程写入通道。
8. **后台源码必须保持“可类型擦除”语法**：后台由 Node 24 直接运行 `.ts`（strip-only），因此不能用参数属性、枚举、命名空间等需要转译的语法。`PreviewService` 最初使用 `constructor(private options)`，在 vitest 里能通过、真实后台进程启动即失败；这类问题只能由“启动真实进程”的集成测试发现，这也是 core 分组必须存在的原因。

## 9. 未确定事项

- 目标 AI 宿主的插件清单字段与 Skill 加载规范（P3 需要按实际宿主规范确定，不能凭印象编造）。
- 展示器发行形态（Electron 外壳还是本地静态包）与关卡宿主的安全边界细节。
- 规模目标的实现策略（增量保存、内存缓存）与性能指标口径。

## 迁移图与目录所有权（本次修正）

scripts/studio-v1-map.mjs 先分配全部节点首事件 ID，保留入口、next 和 choices。引用先指向已登记的终止哨兵，分批插入所有事件后再补最终引用，最后删除哨兵。成功的工作区始终保留；失败只清理脚本自建临时目录，显式目录与已有后台不被清理。报告含 workspace、migrationId、nodeEventIds、assetIds 以追溯旧数据。
