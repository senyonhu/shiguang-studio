---
name: studio-author
description: 通过拾光 Studio 后台 MCP/API 创建或修改小说工程，管理章节、场景、事件、素材与关卡，并核对修订、真实播放和发布完整性。适用于 AI 内容制作；界面观察和用户授权的入口验收可使用浏览器，产品代码修复按该请求的授权处理。
---

# 拾光 Studio 制作 Skill

工具参数以当前服务的 MCP `tools/list` 与 `studio_get_capabilities` 为准。先确认用户要操作的工作区、工程与运行版本；不要把示例包、旧构建或默认端口当成正在使用的版本。

## 必须遵守的规则

1. **AI 内容制作走工具/API**：不要直接编辑正式工程的 story、levels、assets 或 project.json；后台是正式工程的唯一写入者。浏览器可用于观察界面、真实播放与用户授权的人工入口验收；这些行为应与 AI 内容事务分别记录。用户明确要求修复编辑器、展示器或后台时，按该请求修改产品代码，不受内容制作范围限制。
2. **先读后写**：任何修改前先取得当前修订（`studio_open_project` 或 `studio_get_project`）。写操作要带 `baseRevision`；过期会返回冲突，此时重新读取再重试，不要盲目覆盖。
3. **同一操作的重试复用 requestId**：保留实际请求与结果。结果未知时先回读工程/历史；工具缺省 ID 的生成规则以当前实现为准。改动操作内容或解决版本冲突后属于新请求，不能拿原 ID 配新内容，也不能用不断换 ID 的方式绕过冲突。
4. **稳定 ID**：章节/场景/事件/素材/变量/关卡都用稳定 ID 引用。自己生成 ID 时使用前缀格式（`ch_`/`sc_`/`ev_`/`as_`/`var_`/`chr_`/`lv_`），这样重试不会产生第二份对象。
5. **一组相关修改放进一个事务**：用 `studio_apply_changes` 一次提交（例如“插入选择 + 插入两个分支目标事件”）。事务整体成功或整体不提交，不会留下半个分支。
6. **引用必须成立**：`dialogue.text` 不能为空；选择至少 1 项且每项要有目标；素材引用要存在且类型匹配；跳转/选项目标场景或事件必须已存在。提交前先创建被引用对象。
7. **发现授权资源范围**：从指定工作区的 `.studio-service/runtime.json` 与健康检查确认实际地址、版本与工作区；从能力/配置发现 `importRoots`，默认 `<工作区>/imports` 仅是约定。不要写死地址或扩大导入授权。复制的便携包可能包含旧代码，记录其版本与构建来源，修复源码后重新构建再验收。
8. **证据分层**：结构校验、后台预览、像素截图和真实浏览器运行证明不同事情。默认 `studio_render_preview` 是 HTML/JSON 结构快照；`format: pixel` 是快照的 PNG，仍不能证明人工入口或关卡真实可用。工具失败要按错误处理，不把 fixture 素材、模拟 complete 或旧 Demo 截图报成当前作品通过。

## 标准制作流程

1. **确认能力**：`studio_get_capabilities`。查看 `events`（事件类型与运行支持）、`actions`（可提交的事务动作）与 `features`（哪些能力还没实现，例如媒体生成）。能力不支持的不要假装做过。
2. **确认或创建工程**：`studio_list_projects`。空工作区使用当前能力支持的创建工具，或已配对客户端的公共 `POST /v1/projects`（返回工程、首章、首场景 ID）。创建结果未知时用稳定工程 ID 回读，不重复创建。已有工程用 `studio_open_project` 取得 `revision`，再用 `studio_get_project` 读取大纲；不要因列表为空退回直接写工程文件。
3. **读现有内容**：`studio_read_scene` 读取要改的场景；`studio_search` 找对白、角色或素材引用位置；`studio_list_assets` 看已有素材。
4. **搭结构**：`studio_create_chapter`、`studio_create_scene`。公共创建 API 已生成首章与开场；后续调整标题、排序、场景归属和入口用 `studio_apply_changes` 中当前支持的动作，避免重复建立首场景。
5. **写内容**：`studio_insert_event`（对白、背景、人物、选择、变量、等待、音频、视频、关卡、跳转、结束）；批量关联修改用 `studio_apply_changes`。选择的分支目标必须已经存在（先建目标场景，或先插入目标事件再插入选择）。
6. **素材**：先 `studio_import_asset` 登记（后台会校验文件可解码并计算内容散列），再 `studio_bind_asset` 绑定到事件的素材字段。
7. **校验**：`studio_validate_project`。有 `issues` 就按 `objectId`/`field` 修复后重新提交，不要带着问题保存。
8. **运行检查**：用 `studio_start_preview`、`studio_preview_action` 与 `studio_render_preview` 检查内核路径。`studio_install_level` 只安装单个 HTML，脚本/样式/数据须内嵌；图片与媒体走素材导入。当前没有通用依赖批量安装入口，不直接写正式工程补文件。登记成功只证明定义存在。模拟 `levelMessage` 只检查协议；关卡通过须在真实展示器操作、收到程序自己的 checkpoint/complete，并检查资源与恢复结果。
9. **保存、导出与汇报**：`studio_save_project` 的 `persistedRevision`、`verifiedFromDisk` 必须与回读工程一致。`studio_export_project` 后检查最终修订、`playerIncluded`、`missingAssets`、`missingLevels`、`sensitiveHits`；承诺双击便携运行时还须 `portableIncluded`。旧版本缺字段应明确未验证并升级/补验，不能把缺字段当成功。实际打开导出的播放器完成至少一条路径；修改人工入口时还要从独立空工作区验证“新建→创作→素材/关卡→预览→导出”，不能只展示已有 Demo。汇报最终工程/版本/修订及实际检查范围。
10. **改错了怎么办**：`studio_get_history` 找到那次事务 → `studio_undo` 带上 `undoId`。如果其后有人改过同一对象，后台会返回依赖冲突；这时要**停下来问清楚**，不要强行覆盖别人的修改。

## 并发与人工协作

- 人工草稿可能尚未提交，不能只凭后台没有新修订就认为无人编辑。尊重草稿提示与用户的暂停操作；不要借刷新、切换工程或暂停/恢复别人会话丢弃草稿。`baseRevision` 冲突后读取最新内容并重新规划差异，不能盲目覆盖。
- 生成或长任务绑定前，可带对象级期望版本（`expectedObjectRevision`）防止覆盖已被改过的场景。
- 用户可能暂停你的写入会话：此后提交会返回 `SESSION_PAUSED`。这不是让你绕过去，而是等你被告知恢复后再继续。

## 参考文档

- `references/project-model.md`：工程结构、对象模型、事件与引用规则、事务与冲突语义。
- `references/media-workflow.md`：素材导入、真实/测试生成服务边界、关卡安装与发布验收。
