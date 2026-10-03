# HTML 小游戏制作与连续工作

本参考用于通过已连接的制作接口组装作品。首次读取 `get_environment` 与 `get_minigame_capabilities`，再读取实际 `list_tools` schema。不能仅凭文档假定服务已支持小游戏，也不能把单独浏览器试玩写成返回剧情通过。产品要求剧情和小游戏在同一个可见游戏窗口切换；若服务仍报告专用小游戏窗口能力，它没有满足同窗要求。此前专用窗口证据不覆盖同窗容器、焦点、缩放与恢复，须另做实证。当前验证环境为 Windows 11 x64，离线本地内容；其他平台保留为未验证。

## 工程身份与迁移

纯剧情 1.0 工程继续使用原接口及原序列化。要加入小游戏，先读当前 revision，调用 `plan_migration(target_schema:"1.1")`，检查原稳定 ID、存档命名空间和内容保持，再用 `apply_migration` 明确迁移。迁移备份原字节，不代表旧玩家存档已兼容。不要暗改 schema、不要给旧项目重生成身份。

`variables` 为存档变量的有限声明：`variable_id`、`type:int|float|bool|str`、`default`，可含范围或 `allowed_values`。默认值只用于新游戏或缺失字段，不在每帧完整状态恢复时覆盖已经获得的剧情数值。变量名、分支 `branch_id`、效果 `effect_id`、小游戏 `game_id` 和入口 `instance_id` 都是稳定身份。

登记包时使用 `register_minigame(source_path, expected_digest, expected_revision, operation_id)`。目录或 ZIP 都须自包含，含完整文件清单、输入默认值、有限结果和保存能力。默认拒绝原型包。只展示 `prototype:false` 不足以宣称发行通过；查看真实运行报告。修改包须重新导入为新摘要，不修改 `minigames/<digest>` 中的已登记文件。

制作新的 HTML 游戏时，从 `get_environment.home` 对应的受管目录读取 `examples/html_minigames/production/SDK.md`，以及 `examples/html_minigames/production/v1.0.1/` 内的正式模板和 `bridge-sdk.js`。安装器会保留这些作者资源；SDK 文件不是仅存在于开发工程中的依赖。先复制到可编辑的新源目录，再改玩法、game_id、版本与输入/结果清单，重新计算所有资源摘要。页面的 `GalgameBridge.create` 身份须与新清单一致。保留资源授权；源模板、已登记不可变包和生成工程分别管理。

## 一次提交前向引用

大型新章节用 `begin_import` 声明全体 `frame_ids`、`game_ids` 和 `variable_ids`。先后调用 `stage_import_batch` 暂存完整帧、幕、角色、变量；调用 `stage_minigame_package` 暂存包。允许帧引用尚未暂存的包、变量与目标帧，最后 `commit_import` 一次检查全图和所有实际文件，再提交一个 head。

所有候选批次使用稳定 `batch_id`；重试同意图复用相同参数和编号。未完成、缺对象、类型不一致或新 revision 已出现时不得提交。失败保留原 head，候选可修复或取消；成功导入的不可变 blob 可以供历史版本使用，不把 blob 存在当成 live head 已提交。

变量独立小改可用 `define_variables`；修改具体帧的小游戏流程可用当前 `update_flow` 或完整帧/局部 PATCH 接口，遵守它们各自的省略规则。不要通过服务器旁路脚本修改项目 JSON。

## Flow 的有限结构

```json
{
  "kind": "minigame", "game_id": "prod_puzzle", "instance_id": "instance_star_lock",
  "inputs": {"seed": {"kind": "literal", "value": 1742}, "scramble_moves": {"kind": "variable", "variable_id": "puzzle_scramble"}},
  "result_branches": [{
    "branch_id": "branch_star_win", "outcome": "win",
    "predicate": {"op": "gte", "left": {"kind": "result", "field": "score"}, "right": {"kind": "literal", "value": 800}},
    "effects": [{"effect_id": "effect_star_score", "op": "set", "variable_id": "puzzle_score", "value": {"kind": "result", "field": "score"}}],
    "target_frame_id": "frame_star_great"
  }],
  "cancel_target_frame_id": "frame_star_cancel",
  "error_target_frame_id": "frame_star_error",
  "fallback_target_frame_id": "frame_star_other",
  "save_mode": "entry_restart"
}
```

结果分支按保存的顺序匹配：先 outcome，再可选 predicate，首次命中后应用该分支的效果并前进；没有匹配时走 fallback。取消和异常必须有各自显式目标。页面只报结果，不能传剧情标签或变量表达式。

绑定为 `literal`、`variable` 或 `result`；输入不能读尚未产生的 result。predicate 为 `eq/ne/lt/lte/gt/gte/all/any/not` 的有限树；效果为 `set/add`，禁止 eval 和任意代码。结果字段、变量及输入类型必须一致，数值比较和 add 只用于数值。checkpoint 入口还必须引用确实声明快照版本和 schema 的包。

条件和每个效果的变量操作数读取该次 finish 前的变量值；效果按保存顺序写候选副本，不通过前一个效果的隐含改值传参。`add` 的负数可用于扣分，最终候选值必须符合范围和枚举。任何效果失败都不提交部分奖励；结果、变量和保存收据共同完成后才确认并前进。

## 新版本、重导入与恢复

保留 source provenance。`plan_reimport` 对帧、变量与小游戏做三方比较；`branch_id`、`effect_id` 保留身份，顺序变化可单独冲突。原来源和聊天都改了同一项时明确选择 `source/current`，未解决不提交。新包可以先复制为不可变 blob；只有 `apply_reimport` 的原子提交才能更新工程引用。

制作检查点包括变量、包清单和旧摘要。恢复前重验引用的完整包；文件缺失或篡改时拒绝恢复，不能返回部分工程。1.1 工程恢复旧纯剧情检查点时保留显式 1.1 格式，不隐式降级。

玩家保存与制作检查点不同。入口重玩不承诺任意运行时 HTML 状态；checkpoint 只恢复已确认的 JSON 工序进度。必须另测入口前、中途、完成后与读档后的返回目标，以及结果收据避免重复加分。无法兼容的包/快照版本应显示恢复选项，不能静默重玩并宣称恢复成功。

窗口最小化、客户区为零或渲染器仍在调整尺寸时，宿主保留窗口归属和本局身份，等待有效布局恢复。声明 `pause` 能力的页面必须正确处理暂停与恢复消息并停止自己的倒计时；未声明该能力的页面不能承诺隐藏期间停表。恢复窗口不能擅自解除原生菜单或待确认检查点造成的暂停。接入新包时实际验证这些行为，不能把示例包的结果推广到任意 HTML 页面。

## 验证与交付

继续使用 `check_project`、`render_preview`、`test_routes`、`build_release` 和 `get_job`。服务实际提供 `test_minigame` 时再按其 schema 启动真实宿主：实际完成拼图；实际操作限时玩法成功与真实等到超时失败；真实关闭/取消；真实工序 checkpoint 后恢复并完成。不注入 finish、假结果或成功按钮来冒充玩法实测。

分开记录：清单与类型验证、实际本地页面、音频、返回剧情、变量写入、玩家存档、窗口/焦点、有限路线、独立发行。每份报告对应固定工程 revision、包摘要与宿主版本；旧报告不证明新摘要。剧情预览中的占位屏和结构路线测试不证明 HTML 玩法通过。

发行须复制完整已验证包，包括 `LICENSE.txt`、实际 `instructions.txt`、字体、WAV/JSON/WASM 等声明文件，不能按普通素材后缀漏掉小游戏资源。保留授权与来源，宿主、SDK 和包资产纳入产物完整性；浏览器用户数据不纳入不可变资源摘要。

玩家不需要接触这些结构。面向用户说明玩法、何时返回剧情、进度是否保存，以及已验证平台即可；bridge、协议、摘要和调试选项留在制作文档。
