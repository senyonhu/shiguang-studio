# 引擎验证、证据与交付

## 运行工具和异步任务

| 工具 | 参数重点 | 证据用途 |
|---|---|---|
| check_project | project_id、revision | 结构/素材、生成、真实编译与 lint |
| render_preview | project_id、frame_ids、revision | 指定帧真实渲染截图 |
| test_routes | project_id、revision、max_routes | 原生入口与分支路线运行 |
| build_release | project_id、revision | Windows 发行和包检查 |
| get_job | job_id | 状态、报告路径和日志路径 |
| cancel_job | job_id | 取消本插件受管任务 |

测试材料入口前两帧为直接相连的普通对白帧时，test_routes 和 build_release 可传 `include_interactions=true`，追加存读档、回滚、历史、设置、自动播放与跳过实测。默认false；条件不符返回 INTERACTION_FIXTURE_REQUIRED，需要单独安排交互夹具。报告 interaction_verification 对应这次实际执行，不能用默认未执行报告宣称通过。

以上工具按当前宿主实际清单调用；不存在的工具保留为未连接。任务开始返回 job_id 不表示完成；queued/running 继续查询，succeeded/failed/cancelled/interrupted 都是终态。读取报告和日志后再判断测试范围。

错误应结合工程对象与来源映射定位。修复相关帧、素材或流程后重验新 revision。素材缺失或流程目标错误不通过临时虚构素材、篡改原文或删掉选项消除。

## 逐层结论

- **编译检查：** check_project 的有效报告证明该 revision 被生成并由实际 SDK 检查。不能据此声明视觉、音频或玩家操作通过。
- **单帧预览：** 查看实际 PNG，检查中文缺字、对白/选项溢出、人物裁切、遮挡、层级和一致性。指定帧预览可能采用隔离 fixture，不代表普通故事入口与前后衔接通过。
- **连续路线：** test_routes 从原生入口验证每条选项和汇合。核对报告覆盖的实际节点/选项和预期结果；max_routes 限制可能导致未覆盖，不能把截断的遍历称作全路线通过。
- **声音与交互：** 分别确认每句原生 voice、BGM/音效、完整帧切换、转场、点击/自动播放/跳过/历史/存读档/回滚的实际测试记录。若当前报告没有覆盖某项，注明未验证。中文 TTS 文件生成或系统 loopback 探测通过不能代替 Ren’Py 的配音绑定验证。
- **独立发行：** build_release 成功或 ZIP 完整只证明所报告的构建/包检查。独立运行还需要从解包后的 Windows 可执行文件启动，在无 MCP、无工程目录和无 SDK 依赖的条件下检查实际游戏。不得把包存在当作独立运行通过。

`runnable_revision` 仅由真实路线通过设置。工程更新后，旧 runnable_revision 不能表明新 revision 可运行；未测试的版本保留为草稿。

当前原生预览逐张审查后，可调用 `register_visual_baseline(project_id, expected_revision, operation_id, preview_job_id, review_note, frame_ids)` 登记已审基准。review_note说明实际看过的画面及更新理由。此操作不改剧情revision，相同operation_id/参数可重试；不能因为比较失败就自动覆盖。之后render_preview默认比较已有基准，记录visual_comparison和差异PNG。无基准not_run、环境不一致blocked都不能称为比较通过；原生job succeeded时仍须检查visual_review是否visual_failed/visual_blocked。正常修改造成预期画面变化时，先实际查看，再以明确理由登记新基准。登记信息与审查字节不可变保存。

受管生成工程登记.rpy的原始字节和哈希。后续兼容使用前发现外部修改、新增或缺失，会保留实际与原始基准、提示具体文件并返回DERIVED_SOURCES_CHANGED；不要通过重写基准来绕过。早期工程缺记录时明确unverified，保留原目录，不能声称已检测过旧版本外改。

无对白且非选择帧会隐藏原生quick_menu，之后的对白/选项帧恢复。纯背景过场应实际检查画面无快捷文字。跨会话恢复检查点时，用 inspect_project(section="checkpoints") 分页读取记录与snapshot_id，再调用restore_checkpoint；恢复仍需重新检查，不要求用户记程序ID。

## Demo 与持续验收

用户要求测试工具时，由 Codex 生成一套小型中文剧情、背景、人物图和真实中文语音，形成标注完整帧与小分支的 Excel。素材生成完成后冻结文件、记录来源和哈希；重复回归使用相同材料，避免每次换图导致结果不可比。

经真实插件工具完成解析、登记、组装和布局；保存实际 MCP 调用以及 SDK 日志。再请求一次目标帧移位、换图或改字，核对仅目标内容变化；关闭并重开工程，分别走所有选项，再导出并独立启动。

真实产品证据链是 Codex → 已加载 MCP → 执行核心 → SDK → 运行产物。固定假 pipeline、协议客户端或诊断 CLI 可以验证局部实现，但不能代替这条用户验收链，也不能把 CLI 重连结果称作当前桌面聊天已刷新。

记录 project_id、revision、job_id、SDK/生成器/插件/schema 版本、输入哈希、截图、日志、报告和发行包。交付说明按实际证据报告 passed/failed/unverified；不把计划中的检查写成已完成结果。

