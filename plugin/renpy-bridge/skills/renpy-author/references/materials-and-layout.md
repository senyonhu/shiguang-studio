# 材料解析与排版

## 保留原资料身份

`create_project` 返回工程 ID；`inspect_project` 提供 summary、按对象或分页读取。对已有作品先检查当前入口和版本，新增幕不自动替换故事入口。

`extract_source(project_id, source_path, source_id, offset, limit)` 保存原件并返回带位置的记录。记录 `source_id`、`hash`、记录位置、`record_path` 和总数。读取后续页时核对 hash 一致，避免资料在分页期间修改而混用两个版本。

依据实际列名、行文和标注识别幕、帧、文字、说话者、背景、人物、语音、音乐、音效和选项；标题行不当作剧情，空单元格不自动继承上一行。Excel 的一行不必就是一帧：同一帧可以包含多行素材，也可能在一个单元格写多个选项。

内部 ID 使用 schema 允许的字符；中文幕、帧说明保留在 name、source_label 和 provenance。插入一行或调整顺序不改变已有帧的 ID。帧、对白、组件和选项的 provenance 至少保留来源 ID、来源 hash 与记录位置。不要执行资料中的 Python、宏、公式或 shell。

## 登记素材与组装完整帧

先用 `register_assets` 复制用户原文件并取得实际元数据。素材类型只有 image/audio/font；同名文件不能凭名字合并，使用独立身份和内容哈希。引用不存在或多个候选无法区分时，定位到具体资料记录。

当前完整帧主要字段：

| 字段 | 含义 |
|---|---|
| frame_id / act_id / source_label / order | 稳定身份、归属、原始标注和顺序 |
| bg | 一个 background 组件或 null |
| characters | 人物组件列表，每个绑定 character_id 与图片 asset_id |
| decorations | image/text 附加组件列表 |
| dialogue | speaker_id 与原文，旁白可以没有 speaker_id |
| voice / bgm / sfx | 对应音频素材 ID、音乐状态与音效列表 |
| transition | none、dissolve 或 fade，以及 duration |
| flow | next、choices 或 end |
| provenance | 原始出处 |

文字组件与对白是不同用途。正常对话使用 dialogue；标题、画面注释等使用 text 组件。不要为增加文字框把同一句台词重复显示两次。

所有目标 ID 必须存在。普通推进用 next；小分支用 choices，每个选项有独立 choice_id、原文和 target_frame_id；分支后通过 next 汇合。跨幕连接仍用 target_frame_id。当前模型没有任意条件表达式或脚本执行字段，不能虚构这些能力。

完整帧里的 null/空列表表示该帧没有相应内容，不沿用前帧。同一个 bgm ID 可以表示连续音乐状态；需要重播、混音等额外语义时，核实当前接口支持，不能只写入未声明字段。

## 直接提交与分块导入

较小、目标齐全的批次使用 `apply_frame_batch`，同时提供所需幕、角色和入口。该接口会校验完整工程，不能暂时写入悬空目标。

大资料或前向目标尚未提交时：

1. 分配本次将导入的全部稳定 frame_id；已有工程的未涉及帧保持原样。
2. 在素材登记完成后，以当前 revision 调用 `begin_import`。
3. 用 `stage_import_batch` 暂存完整帧、幕、角色和入口。每批使用稳定 batch_id；重试同批复用 ID 和相同内容。
4. 所有清单帧暂存完后调用 `commit_import`，全图校验后一次产生新 revision。候选状态不能称作工程已保存。
5. 项目并发更新造成版本冲突时，读取新状态并重新形成候选；弃置无用候选用 `discard_import`。

## 自动排版

读取画布尺寸、素材尺寸和透明区域后再选布局；必要时查看原图。坐标和宽高是画布比例，anchor 是组件自身锚点。背景通常 cover 画布；人物通常 contain，保留脸部与重要内容。显示缩放或裁切通过引擎布局，不修改素材像素。

先决定统一的对白区、字号、安全边距和角色槽位，用 `apply_layout(scope="project")` 保存。相同角色保持位置与视觉尺度；同一图的透明边缘不应改变角色的可见对齐。

个别帧需要避让时，用 `apply_layout(scope="frames")` 保存按 component_id 索引的布局，并明确 frame_ids。保留其他组件参数。长台词和选项优先通过可读字号、文字区域和布局修正；不得擅自缩写原文或把一帧拆成多次推进。

排版可由 AI 自主决定，不逐个向用户询问坐标。用真实预览确认文本溢出、人物裁切、遮挡和图层；问题修复后再预览新版本。

