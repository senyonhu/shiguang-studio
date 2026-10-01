# 工程模型与事务语义（制作参考）

本文件是 `studio-author` Skill 的参考材料，描述后台实际实现的对象模型与规则。源码位置：`src/runtime/content.ts`、`src/runtime/events.ts`、`server/src/edit/service.ts`、`server/src/store/journal.ts`。

## 1. 工程目录（工作区内）

```text
<workspace>/
  projects.json                  工程注册表
  imports/                       默认素材导入根（AI 只能从这里导入文件）
  <projectId>/
    project.json                 工程信息、入口场景、格式版本（以当前协议版本为准）
    story/chapters/<ch>.json     章节：标题、排序、场景 ID 列表
    story/scenes/<sc>.json       场景：所属章节 + 事件序列（顺序即执行顺序）
    characters/<chr>.json        角色设定
    variables.json               变量：类型 number/string/boolean、初值、作用域
    assets/manifest.json         素材索引（含内容散列 sha256、字节数、来源）
    assets/originals/            素材原始文件，按稳定 ID 命名
    levels/<lv>.json             关卡定义（ID、标题、入口、是否支持快照）
    levels/<lv>.html             关卡程序（由 `level.installProgram` 写入）
    themes/theme.json            对白与菜单主题（theme.update 可修改）
    generation/recipes/<id>.json 图片/视频生成模板
    .studio/                     运行数据：state.json、history/、journal/、snapshots/、previews/
  .studio-service/               服务发现与凭据（绝不在发布包中出现）
```

## 2. 对象与稳定 ID

| 对象 | 前缀 | 关键字段 | 约束 |
| --- | --- | --- | --- |
| Project | `prj_` | title、entrySceneId、chapterIds、formatVersion | 入口场景必须存在；标题 1–120 字 |
| Chapter | `ch_` | title、order、sceneIds | 至少保留一个章节；章节列表与场景归属必须一致 |
| Scene | `sc_` | chapterId、title、events[] | 至少保留一个场景；入口场景不能删除 |
| Event | `ev_` | type、params | 事件 ID 全局唯一；类型不可改（删除后重建） |
| Asset | `as_` | name、kind、path、sha256、bytes | 导入必须来自授权目录且文件可解码 |
| Variable | `var_` | name、type、initial | 初值类型必须匹配；被引用时不能删除 |
| Character | `chr_` | name、description | — |
| Level | `lv_` | title、entry、supportsSnapshot | 被关卡事件引用时不能删除 |

ID 形如 `ev_ab12cd34ef56`（前缀 + 12–32 位小写字母数字）。后台在缺省时生成；自己生成时请遵守该格式，便于重试幂等。

### 空工作区与结构管理

`POST /v1/projects` 使用当前 edit 会话创建工程，返回工程、首章、首场景及修订；不要先假定工程已存在。该创建入口与普通内容事务不同，没有 baseRevision，建议显式使用稳定 projectId；网络结果未知时先查询该 ID 并核对标题/结构，再决定是否重试。章节/场景管理使用公共事务动作 chapter.create/update/delete、scene.create/update/delete/move；工程标题与开场使用 project.update。实际可用动作以当前服务能力为准。

运行地址来自工作区 `.studio-service/runtime.json`，须与健康检查的 instanceId/workspace 对上。复制包中的源码、插件适配器与浏览器构建可以落后于仓库；记录服务/协议版本与来源，不能把仓库修复等同于运行包已更新。

## 3. 事件类型与运行支持

| 类型 | 参数要点 | 运行内核支持 |
| --- | --- | --- |
| `background` | assetId（background/cg） | 已实现 |
| `character` | assetId、action(enter/exit/move)、position(left/center/right)、durationMs | 图层与固定位置已实现；durationMs 不产生补间动画 |
| `dialogue` | speaker、text（≤1200 字）、characterId(可选) | 已实现（等待玩家推进） |
| `choice` | prompt、options[1–6]：label + targetEventId 或 targetSceneId | 已实现（等待选择） |
| `variable` | variableId、op(set/add)、value | 已实现 |
| `audio` | assetId、channel(bgm/ambience/sefx/voice)、action(play/stop/fade)、loop、volume、waitForEnd | 按声道执行动作、循环与音量，同声道背景音替换；waitForEnd 等实际结束，需避免循环音频永久等待；失败回执并继续 |
| `video` | assetId、skippable、onEnd(continue/stop)、onError(continue/stop/replay) | 真实控件播放；遵守可跳过、结束/失败策略；replay 最多重试一次，之后记录错误并继续 |
| `wait` | ms | 已实现 |
| `level` | levelId、supportsSnapshot、initPayload | 内核与展示器均已实现（iframe 宿主 + 进度点 + 中途存档恢复）；**关卡程序必须遵守下面的协议** |
| `jump` | targetSceneId 或 targetEventId | 已实现 |
| `ending` | endingId、title | 已实现（结束剧情） |

参数规范与校验规则以 `studio_get_capabilities` 的 `events` 与工具 Schema 为准；本表只列要点，不替代 Schema。

## 3.1 关卡程序协议（写关卡时必须遵守）

关卡是内容包里的 `levels/<lv>.html`，运行于 sandbox="allow-scripts" 的不透明来源 iframe，通过 `postMessage` 与展示器通信。宿主核对消息来源窗口、协议与实例，不使用普通同源 iframe 权限。关卡不能读取 parent DOM 或 localStorage；parent.postMessage 用于协议通信。

| 方向 | 消息 | 语义 |
| --- | --- | --- |
| 关卡 → 宿主 | `{ type: 'ready' }` | 程序就绪，等待初始化 |
| 宿主 → 关卡 | `{ type: 'init', levelInstanceId, protocolVersion, payload, snapshot }` | 开始或恢复；`payload` 是关卡事件的 `initPayload`，`snapshot` 是该关卡上次记录的进度快照（无则 `null`） |
| 关卡 → 宿主 | `{ type: 'checkpoint', payload, requestId, messageSeq }` | 记录进度点（会随存档保存） |
| 关卡 → 宿主 | `{ type: 'complete', payload, requestId, messageSeq }` | 关卡完成，`payload` 作为关卡结果交给内核 |
| 关卡 → 宿主 | `{ type: 'error', payload: { code, message } }` | 关卡失败：运行继续并记录错误，不会永久等待 |

要点（不遵守会导致玩家读档后进度丢失或卡住）：

1. **声明支持快照时必须实现恢复**：定义与事件均允许，且 init.snapshot 非空时，从快照恢复自身进度；不支持快照时不保存或下发快照，读档重开。
2. 进度点要发 `checkpoint`：只发 `complete` 的关卡在存档里没有可恢复状态。
3. `messageSeq` 在实例内递增，`requestId` 区分消息；同一消息重试复用标记。携带 init 的 levelInstanceId，宿主丢弃重复/迟到消息，重复 complete 不会重复推进。
4. 关卡失败要发 `error` 而不是静默不动：宿主会把结果交回内核并继续，避免运行永久等待。
5. 参考实现：仓库里有两个已验证的关卡程序（`tests/studio/helpers/story.ts` 的 `LEVEL_HTML` 回声关卡、`LANTERN_LEVEL_HTML` 点灯关卡），都实现了上述契约。

`level.register` 只登记定义。level.installProgram（工具 studio_install_level）只安装一个入口 HTML，目前没有 CSS/JS/JSON 通用依赖批量安装动作。脚本、样式与数据优先内嵌，图片/音频/视频通过素材导入；任意多文件依赖需先扩展并验收后台能力，不绕过服务写正式工程。已有包内依赖仍须检查文件与请求，再在真实播放器触发 ready/init、操作任务、检查 checkpoint/complete 与存档恢复；模拟 levelMessage 不能证明执行成功。

网络只通过宿主注入的 fetch 桥：当前内容根的 assets/levels GET/HEAD，以及 initPayload JSON字符串或对象显式指定的本机 http runner 的 GET /health、POST /run。不得访问制作API、其他端点或外网，不使用直接XHR、frames/workers或放宽CORS。支持内联/经典脚本，不透明来源的外部ES module import未承诺。快照须关卡定义与事件双方允许；正常新实例清空旧进度，只有读档恢复待完成效果时下发保存快照。

## 4. 事务语义

提交（`POST /v1/projects/{id}/transactions` 或 `studio_apply_changes`）的处理顺序：

1. **身份**：操作者与作用域来自后台会话，请求体里的 actor/role 字段会被忽略。
2. **幂等**：`requestId` 已提交过且操作内容相同 → 返回既有结果（`replayed: true`）；同 ID 不同内容 → `REQUEST_ID_CONFLICT`（HTTP 409）。
3. **基础版本**：`baseRevision` 与当前修订不一致 → `status: "conflict"`，`errorCode: "REVISION_CONFLICT"`，并返回当前修订。
4. **会话状态**：人工暂停了该会话 → `SESSION_PAUSED`（HTTP 423）。
5. **对象级版本**：操作带 `expectedObjectRevision` 且对象已被后续提交改过 → `OBJECT_REVISION_CONFLICT`。
6. **动作执行与整体校验**：所有动作先应用在内存快照上，再做结构/引用校验；任一项不合法 → `status: "rejected"` + `issues`，**不写入任何文件**。
7. **提交与持久化**：备份 → 写内容文件 → 写历史 → 写 `.studio/state.json` → 提交标记。进程在中间被杀，下次启动会按标记回滚或确认，不会留下半个事务。
8. **变更通知**：提交成功后产生递增变更序号，SSE 推送给界面；`studio_get_changes?after=N` 可增量补齐。

人工未提交的草稿没有修订号，不能由后台快照判断其为空。尊重编辑器的草稿保留提示与会话暂停；不要自动切换工程或放弃草稿。发生冲突时回读实际对象并重新规划内容，新操作使用新 requestId；原请求结果未知的原样重试继续使用原 ID。

## 5. 撤销

`studio_undo` 使用被撤销事务记录下来的**补偿操作**生成一次新提交，不做整份快照恢复：

- 其后有提交改过同一对象 → `UNDO_DEPENDENCY_CONFLICT`，列出冲突对象与事务，**不覆盖**后续修改。
- 已经撤销过的事务再次撤销 → `CONFLICT`（`ALREADY_UNDONE`）。

## 6. 常见拒绝原因与处理

| 错误码 | 含义 | 正确处理 |
| --- | --- | --- |
| `REVISION_CONFLICT` | 基础版本过期 | 重新读取工程 → 重新规划差异 → 用新修订提交 |
| `REQUEST_ID_CONFLICT` | 同 requestId 不同内容 | 换 requestId，或在原请求内容上重试 |
| `OBJECT_REVISION_CONFLICT` | 目标对象已被改 | 重新读取该对象，确认是否仍需覆盖 |
| `VALIDATION_FAILED` | 参数或引用不合法 | 按 `issues[].field/objectId` 修正；先建被引用对象 |
| `SESSION_PAUSED` | 用户暂停了该会话写入 | 停止写入并等待恢复，不要绕过 |
| `BACKEND_UNAVAILABLE` | 后台未运行 | 根据指定工作区和运行包启动后台；不退回直接写正式工程文件 |
| `HISTORY_GAP` | 变更流已截断 | 重新读取完整快照 |

## 7. 发布与验收证据

保存后回读 revision，核对 save 的 persistedRevision 与 verifiedFromDisk。导出后核对 revision 等于最终工程修订，playerIncluded 为 true，missingAssets/missingLevels/sensitiveHits 均为空；声称无需另装运行时的便携交付还要求 portableIncluded 为 true。缺字段代表该版本未提供此检查，不能默认通过。

结构校验检查引用，后台预览检查内核路径，render 的默认 HTML/JSON 检查舞台结构，format=pixel 检查该快照像素，真实浏览器结果检查实际文件加载与交互。最终需打开本次导出的 player.html，记录版本、修订、路径、资源/脚本错误与实际走过的分支。人工编辑器入口的验收要从独立空工作区操作创建与导入；API 造好的 Demo 只能证明已有工程显示正常。
