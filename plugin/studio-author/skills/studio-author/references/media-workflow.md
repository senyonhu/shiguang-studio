# 素材与生成工作流（制作参考）

本文件描述素材侧**当前真实可用的能力**与**尚未接入的部分**。不要把未接入的能力当成已完成。

## 1. 现在可以做的

### 1.1 导入已有素材

1. 先核对能力响应与健康检查的工作区，再读取该工作区配置的 `importRoots`（`<工作区>/.studio-service/config.json`；`imports/` 只是默认目录）。文件必须位于实际授权根内；不要把示例包路径或旧服务配置当成当前授权，也不要扩大授权范围。
2. 调用 `studio_import_asset`，给出 `sourcePath`、`name`、`kind`：
   - `kind` 取 `background` / `character` / `cg` / `audio` / `video`。
   - 支持格式：PNG、JPEG、GIF、WebP（图片）；WAV、MP3（音频）；MP4、WebM（视频）。后台按文件头解析并校验格式与素材类型是否匹配。
3. 后台会：把文件复制到 `assets/originals/<assetId><ext>`、计算 SHA-256、登记尺寸或时长（能解析时）、写入素材清单。**无法解码的文件不会登记**（`ASSET_DECODE_FAILED`），整笔事务不提交。
4. 素材被事件引用时不能解除登记；要删除先移除引用。

### 1.2 绑定到剧情

- 插入事件时直接给 `params.assetId`，或用 `studio_bind_asset` 把素材绑定到事件的素材字段（如 `background.assetId`、`character.assetId`）。
- 绑定会做类型检查（例如 `background` 事件只接受 background/cg 素材）与整体校验。
- 若要防止覆盖别人刚改过的场景，绑定请求带上 `expectedObjectRevision`（对象级期望版本）。

### 1.3 用角色与参考保持一致性（部分可用）

- `characters/<chr>.json` 可以记录角色设定与说明；`character` 事件使用立绘素材 ID。
- 需要画面一致时，人工确认过的参考图先按 1.1 导入并命名清楚（例如 `璃_正面_暖光`），再在生成任务里作为参考素材使用。

## 2. 生成能力与真实服务边界

| 能力 | 现状 |
| --- | --- |
| 图片/视频生成任务（`media-jobs`） | **已实现**：状态机、持久队列、预算预留/释放、取消、重启恢复、候选区；提交任务用 `studio_create_media_job`（可带 `templateId` 使用生成模板） |
| 服务能力声明（`studio_list_media_providers`） | **已实现**：返回适配器能力，其中 `isTestAdapter: true` 的是测试适配器 |
| 费用估算与预算（`studio_estimate_media`） | **已实现**：返回费用状态（未知/估算/已确认）与预算余额 |
| 生成产物下载、校验、入库 | **已实现**：下载 → 解码校验 → 候选区；用 `studio_accept_media_output` 接受候选（登记素材 + 绑定剧情在同一事务里） |
| 播放副本（derived） | **未实现**：发布包直接使用 `assets/originals` 中的原始文件 |

本仓库随包提供 `fixture-local` 测试适配器（`isTestAdapter: true`，不访问网络、不产生费用）：图片候选是真实 PNG，视频候选是标注 `playable: false` 的容器头占位文件。运行服务是否配置真实云端/本地/工作流提供方须调用 `studio_list_media_providers` 核对；不能从任务 API 存在推断真实生成已接通。测试素材只用于协议验证，不报作用户要求的真实成品。

## 3. 人工与 AI 的素材交接（无 API 时的替代路径）

如果用户手上只有某个没有 API 的生成工具，正确做法是人工交接而不是假装自动接通：

1. 明确写出需求（用途、提示词、规格、参考素材、目标位置）。
2. 用户用外部工具生成后，把文件放进导入根目录。
3. 用 `studio_import_asset` 登记，必要时在 `provenance`/名称里记录来源与参数（生成服务、模型、提示词版本），供日后追溯。
4. 说明这一步是**人工/外部交接**，不是自动调用。

## 4. 音频与视频的运行时现状

- 运行内核会执行 `audio`/`video` 事件：产生宿主效果、按事件声明决定是否等待、失败时按 `onError` 继续，不会永久挂起。
- 独立展示器**已实现播放**：`bgm`/`ambience` 循环播放且不阻塞（同声道替换），`sefx`/`voice` 在 `waitForEnd: true` 时等播放结束再继续；视频带控件播放，结束或点「跳过视频」后继续。素材缺失、浏览器拦截自动播放、解码失败都会带错误码交回内核并继续（不会永久等待）。
- 关卡事件在展示器中由 iframe 承载；关卡程序通过 `studio_register_level` + `studio_install_level` 登记安装，消息协议为 `ready/init/checkpoint/complete/error`，重复与迟到消息不会重复推进。

## 5. 浏览器与导出验收

素材写入走后台；浏览器用于检查用户实际看见的名称/缩略图、资源加载、播放控制与导出作品。未经人工入口操作的 API fixture 无法证明“上传素材→选择素材→预览”可用。结构快照和像素渲染也不能证明音视频已真实播放、关卡程序已真实运行。

发布前核对素材散列、关卡入口与依赖；导出报告必须对应最终修订，missingAssets/missingLevels/sensitiveHits 为空，并检查 playerIncluded 以及承诺便携运行时的 portableIncluded。实际打开本次发布包，检查网络/控制台错误、至少一条完整剧情路径及相关媒体/关卡交互；缺检查项如实记录，保留导出目录与报告关联。
