# 局部修改、重导入与恢复

## 局部修改保持完整对象

先 `inspect_project(section="frames", object_id=frame_id)` 读取目标对象，并取得当前 revision。人物移动、替换表情、改字等请求使用 `patch_frame` 或 `apply_layout`。

PATCH 规则：

- 未出现字段保持原值。
- bg、dialogue、voice、bgm 等可空字段用 null 清空。
- characters、decorations、sfx 用 [] 清空整个列表，不能传 null。
- 嵌套对象与列表整体替换。修改一位人物时，复制原 characters 列表，调整目标组件，提交保留其他人物的完整列表。不能只传 component_id 与局部 x 值当成完整人物组件。
- frame_id、act_id 等稳定身份不通过局部 patch 改写。流程改变使用 `update_flow`，目标必须存在。

`apply_layout` 可只调整组件布局；提交前保留当前 Layout 的其他字段，避免位置修改改变缩放或锚点。保存后读取目标对象确认实际结果，检查未涉及内容保持一致。

## 版本与重试

工具返回的 revision 是下一次内容修改的依据。遇到 VERSION_CONFLICT，不强行覆盖；读取目标的新值，判断本次修改是否仍适用，形成新请求。IDEMPOTENCY_CONFLICT 表明同一个操作或批次 ID 被用于不同内容，应修正调用记录。

新素材通过登记建立内容版本，不直接覆盖已登记文件。素材登记也会改变工程 revision；之后的帧写入使用新版本。

## 再次导入同一来源

使用稳定 source_id 提取更新后的资料，保持帧身份。用户只改台词/素材时，保留既有布局；incoming_frames 可省略布局，让 `plan_reimport` 按其声明保留当前排版。

调用 `plan_reimport` 比较原来源、当前工程和新资料。检查新增、删除、普通变更和冲突。对 conflict 的 JSONPointer 路径，按已经确定的用户意图选择 source 或 current；不能把所有差异一律当作冲突，也不能无依据全选 source。

`apply_reimport` 带 plan_id、明确 resolutions、当前 expected_revision 与新 operation_id。未解决的冲突不得声称已应用。来源修改与聊天修改真正矛盾而无法推断时，只询问该具体内容，同时继续能够独立确定的工作。

## 检查点与会话恢复

较大重导入、批量布局或结构修改前，可用 `create_checkpoint` 留下说明和当前版本。恢复时用 `restore_checkpoint` 创建新 revision，历史不擦除；恢复后的版本仍需新的引擎验证。

下一会话从项目 ID、当前 revision、候选导入会话、未完成 job_id 和最近有效报告继续。用 `get_job` 核实后台任务：interrupted 是任务中断，不能自动当作成功或无限等待 running。再次执行时以明确版本新建任务；旧任务不复用为新内容的证据。

正在运行的任务固定在其 revision 上。当前工程已更新时，旧任务完成仍是旧 revision 的结果；无需为旧版本报告错误去覆盖新内容。`cancel_job` 负责取消受管进程，不通过搜索系统进程名手动结束其他游戏。

