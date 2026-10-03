# HTML 小游戏代表原型

这里是 H0 技术验证用的三个完整离线小游戏，不表示 H1 正式包契约已经冻结。

- `puzzle`：移动九宫格数字，只有正确排序才能胜利。
- `timed`：在倒计时内按闪亮目标操作，超时真实失败；声音在实际用户操作后启用。
- `checkpoint`：完成三道装配工序，每道工序生成 JSON 检查点，并支持从检查点恢复。

每个目录自含入口、SDK、样式和资源，使用 `manifest.json` 记录原型输入、结果、保存能力及文件哈希。`sdk` 保存接入 SDK 原件，包内副本用于完整离线加载。

页面接收宿主的 `init/start/pause/resume/ack`，提交 `ready/checkpoint/finish/error`。消息固定携带 `protocol/session_id/event_id/type/payload`，完成只报告玩法结果，不指定剧情标签。不在生产页面设置假通关入口。

普通浏览器直接打开入口时进入明确标注的独立预览；这只验证玩法，不代表已经与 Galgame 通信。正式往返证据必须从受管宿主与实际 Ren’Py 流程采集。

开发自动化可使用页面的 `data-testid` 和真实控件；没有跳过玩法的 API。`tests/sdk.test.cjs` 通过 Node 运行协议行为检查。
