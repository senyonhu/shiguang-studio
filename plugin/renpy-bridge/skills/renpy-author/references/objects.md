# 对象与参数实例

首次组装时先读本页，再使用当前宿主工具清单的真实 schema。下列 JSON 是 AI 向接口传递的对象，不要求用户编写。`p_example`、来源 ID、素材路径、revision 和操作 ID 都要换成实际返回值。schema 快照与可校验例子在项目 `docs/tools.schema.json`、`docs/tool-examples.json` 中。

## 素材登记

图片、音频、字体用 `kind="image"/"audio"/"font"`；原文件用 `source_path`。不要使用 `type`，不要自己填写服务管理的 `path`、hash 或 version。出处放在 `provenance` 内，不放到素材顶层。

```json
{
  "project_id": "p_example",
  "expected_revision": 0,
  "operation_id": "register_example_assets",
  "assets": [
    {"asset_id": "asset_bg", "kind": "image", "source_path": "C:\\材料\\背景.png", "provenance": {"source_id": "source_example", "sheet": "第一幕", "cell": "D2"}},
    {"asset_id": "asset_character", "kind": "image", "source_path": "C:\\材料\\小夏.png"},
    {"asset_id": "asset_voice", "kind": "audio", "source_path": "C:\\材料\\第一帧.wav"}
  ]
}
```

用 `register_assets` 提交后，使用返回的 revision 开展下一步。稳定 asset_id 表示素材身份，版本和 hash 由服务维护。

## 一批完整帧

用 `apply_frame_batch` 提交。下面包含背景、角色、声音、菜单、旁白、next 与 end；两条选择在第三帧汇合。角色定义只接受 character_id、name、color、metadata，不能加顶层 provenance。幕允许 provenance，没有 source_label 字段；原来的“第一幕 第1帧”保存到 Frame.source_label。

```json
{
  "project_id": "p_example",
  "expected_revision": 1,
  "operation_id": "import_example_frames",
  "entry_frame_id": "frame_1",
  "characters": [{"character_id": "character_xiaoxia", "name": "小夏", "color": "#ffd0e0"}],
  "acts": [{"act_id": "act_1", "name": "第一幕", "frame_ids": ["frame_1", "frame_2", "frame_3"], "provenance": {"source_id": "source_example"}}],
  "frames": [
    {
      "frame_id": "frame_1", "act_id": "act_1", "source_label": "第一幕 第1帧", "order": 0,
      "bg": {"component_id": "background", "kind": "background", "asset_id": "asset_bg"},
      "characters": [{"component_id": "xiaoxia", "kind": "character", "character_id": "character_xiaoxia", "asset_id": "asset_character"}],
      "decorations": [], "dialogue": {"speaker_id": "character_xiaoxia", "text": "我们走哪条路？", "provenance": {"source_id": "source_example", "sheet": "第一幕", "cell": "B2"}},
      "voice": "asset_voice", "bgm": null, "sfx": [], "transition": {"kind": "dissolve", "duration": 0.3},
      "flow": {"kind": "choices", "choices": [{"choice_id": "choice_river", "text": "去河边", "target_frame_id": "frame_2"}, {"choice_id": "choice_finish", "text": "直接结束", "target_frame_id": "frame_3"}]},
      "provenance": {"source_id": "source_example", "sheet": "第一幕", "row": 2}
    },
    {
      "frame_id": "frame_2", "act_id": "act_1", "source_label": "第一幕 第2帧", "order": 1,
      "bg": null, "characters": [], "decorations": [], "dialogue": {"speaker_id": null, "text": "河边很安静。"},
      "voice": null, "bgm": null, "sfx": [], "transition": {"kind": "none", "duration": 0.0},
      "flow": {"kind": "next", "target_frame_id": "frame_3"}, "provenance": {"source_id": "source_example", "sheet": "第一幕", "row": 3}
    },
    {
      "frame_id": "frame_3", "act_id": "act_1", "source_label": "第一幕 第3帧", "order": 2,
      "bg": null, "characters": [], "decorations": [], "dialogue": {"speaker_id": null, "text": "两条路在这里汇合。"},
      "voice": null, "bgm": null, "sfx": [], "transition": {"kind": "none", "duration": 0.0},
      "flow": {"kind": "end"}, "provenance": {"source_id": "source_example", "sheet": "第一幕", "row": 4}
    }
  ]
}
```

每个图片组件都必须带 kind；不能仅由它所在 bg 或 characters 列表推断。bg 用 background，人物用 character 并同时绑定 character_id 和 asset_id，附加图片用 image。文字组件放 decorations，使用 kind=text、text、font_size、color，不绑定图片或角色。

Flow 三种合法形式为 `{"kind":"next","target_frame_id":"frame_2"}`、`{"kind":"choices","choices":[...]}`、`{"kind":"end"}`，不能传 `{"next":"frame_2"}` 或 `{"end":true}`。每一帧是完整状态：示例第二、三帧明确清空背景、人物与声音，不从第一帧继承。省略时也按空状态处理。

provenance 保存实际 extract_source 返回的 source_id、hash 与工作表/行/列或文本行范围。不要复制不准确的示例定位；AI 决定的布局应另外记录为排版决策，不能冒充材料原文。

## 局部排版与 PATCH

`apply_layout(scope="frames")` 的 layout 直接按组件 ID 索引。下面只移动 frame_1 中的 xiaoxia；不包含其他帧的组件。不同帧的组件集合不同，分开调用并使用每次返回的新 revision。只有多个帧都含同一组组件 ID 时，才可以一次选多帧应用同一映射。

```json
{
  "project_id": "p_example", "scope": "frames", "frame_ids": ["frame_1"],
  "expected_revision": 2, "operation_id": "move_example_character",
  "layout": {"xiaoxia": {"x": 0.72, "y": 0.93, "anchor_x": 0.5, "anchor_y": 1.0, "height": 0.7, "fit": "contain"}}
}
```

坐标和尺寸是画布比例，0.5 表示中间，anchor 是组件自身的锚点。统一字体、文字框高度和角色槽位用 scope=project 的 LayoutProfile 字段；不要把组件映射传给 project。

只改一句对白用 `patch_frame`，不重建整帧。嵌套 dialogue 整体替换，因此要同时保留实际 speaker_id 与必要出处。

```json
{
  "project_id": "p_example", "frame_id": "frame_2", "expected_revision": 3,
  "operation_id": "patch_example_dialogue",
  "patch": {"dialogue": {"speaker_id": null, "text": "风声很轻。"}}
}
```

完整帧的省略表示空状态，PATCH 的省略表示保留当前值。null 只用于允许清空的字段，[] 清列表；不能给 flow 传 null。

## 任务结果

启动检查、预览、路线或发行工具后用返回的 job_id 查询。普通轮询保留 include_report=false；完成后用 include_report=true 得到紧凑结果，只有查原始执行输出时指定 detail=full。

```json
{
  "job_id": "job_0123456789abcdef0123456789abcdef",
  "include_report": true,
  "detail": "summary"
}
```

先检查 state，再读报告 status 与 revision。MCP 传输成功或查询的 ok=true 不能证明 job 已通过。summary 保留实际诊断、截图路径和路线覆盖，full 或 report_path 提供详细执行记录；截图仍需实际查看，语音仍需实际播放输出证据。
