# 拾光 Studio 独立复核报告（2026-09-30）

> 历史报告：以下结论针对旧源码 b4142ac2…，原始失败证据保持冻结。后续代码已修复 KI-034–039，完整验证 198 项通过；当前状态见 [CODE-REPAIR.md](CODE-REPAIR.md) 与 HANDOFF.md。

按 REVIEW-GUIDE.md 核验冻结材料、重建并复跑全部六组检查，再沿迁移、暂停、候选入库、任务调度与人工同步链路构造反例。**结论：既有测试全部通过，但发现 6 个可复现的实现缺陷，其中 3 个 P1；不建议将项目标为全部验收完成。** 功能代码未修改。

## 已复现问题（按修复优先级）

| 优先级 / 台账 | 问题 | 精确位置 | 独立证据 ID |
| --- | --- | --- | --- |
| P1 / KI-034 | 迁移默认删除显式传入的整个工作区，含已有数据和刚迁移的工程 | scripts/studio-migrate.mjs:280（失败分支同样删除） | MIGRATE-DELETE |
| P1 / KI-036 | 人工暂停 AI 后，同会话仍能提交新生成任务 | server/src/api/server.ts:475–479 | PAUSE-MEDIA |
| P1 / KI-035 | 合法 v1 前向选择目标被改为首节点，迁移后的分支循环 | scripts/studio-migrate.mjs:234–240 | MIGRATE-FORWARD |
| P2 / KI-037 | 同一生成任务的第二个候选无法登记入库 | server/src/media/accept.ts:38 | MEDIA-MULTI |
| P2 / KI-038 | 新的 unknown 任务挡住较早的正常任务推进 | server/src/media/jobs.ts:434–436 | MEDIA-UNKNOWN-QUEUE |
| P2 / KI-039 | 切换工程后仍订阅原工程，AI 修改当前工程不再实时显示 | src/studio/live/LiveStudio.vue:388–394 | UI-PROJECT-SUBSCRIPTION |

**KI-034：默认迁移有数据删除风险。** 在专用临时目录放入 existing-data.txt，运行迁移时指定 --workspace 且不加 --keep-workspace。有效 v1 输入经原 inspectProject 校验通过；迁移退出 0、报告校验通过，但目录和 sentinel 都不存在。workspace 没有区分调用方目录与脚本创建的临时目录，成功与失败清理均递归删除。现有新建/既有工程测试都加了 --keep-workspace，因而绿色测试并未消除默认行为。应仅清理脚本自建目录，默认保留显式目录，并验证成功/失败两路的数据保留。

**KI-036：暂停仅覆盖事务，遗漏新生成。** 人工 UI 暂停同一个 AI 会话后得到 paused=true；该会话提交事件事务返回 HTTP 423 / SESSION_PAUSED，随后 POST media-jobs 却返回 HTTP 201 / queued 并创建 jobId。generate 作用域检查不等于会话可写检查，MediaJobService.createJob 在持久化与预算预留前没有暂停守卫。当前是 fixture，未产生付费调用；接入真实适配器后同一缺口仍会允许已暂停会话发起任务。应在提交点复查会话状态，包含 await 校验/估价期间暂停的情况。

**KI-035：前向选择没有保持 v1 图。** 输入是 start 对白 → choice → finish 结局，包含真实内嵌 PNG，原 v1 校验与迁移后校验均通过。脚本边创建事件 ID 边解析选项，遇到 choice 时未来的 finish 没有 ID，于是目标被写成 ev_migstart。内核实际点击 Finish 后进入 awaitingAdvance 而非 ended，重新显示开头。此处属于合法引用被迁错；报告的一条“回退”警告不能替代保持原有去向。应先分配所有节点的首事件 ID，再生成事件/跳转，验证运行路径等价；同时核对入口与 dialogue.next 等边的映射。

**KI-037：素材 ID 未包含 outputId。** 提交 count=2 的图片请求得到两个 available 候选。接受 out_1 返回 applied/200；接受 out_2 返回 rejected/409，提示同一个 as_gen… 已存在。assetId 仅由 jobId 派生，所有候选必然共用 ID。不同候选应分别登记，同一候选的重试仍应幂等；现有测试只接受单候选。

**KI-038：unknown 永占调度槽。** 先提交 fixtureMode=slow 正常任务，再提交较新的 poll-unknown 任务。后者进入 unknown 后，超过 slow 的 1.5 秒正常完成时间（等待 2.4 秒）前者仍 generating；取消 unknown 后前者立即可推进至 available。listJobs 按创建时间倒序，pump 先截取 concurrency=1，又没有把不再自动推进的 unknown 排除，导致固定选中阻塞项。应将人工待核对任务与可推进任务分开调度，保留未知费用/服务提交状态，避免靠取消才能处理其他任务。

**KI-039：工程切换未重绑订阅。** 真实 Edge 中确认当前工程 A，再切换到另一个工程 B；通过后台在 B 插入 INDEPENDENT-SWITCH-MARKER，事务 applied/200。等待后界面 marker 数为 0，点击刷新后为 1，排除了后台写入失败。onProjectChange 只刷新内容，watchChanges 只在 connect 后调用。应在工程变化时关闭旧订阅、订阅新工程，并补断线/sequence/gap 恢复测试。

## 核验结果与口径

- 编辑器生产构建（含类型检查）、展示器构建和插件构建均成功。
- 当前源码指纹：b4142ac2b221ee56aa33a369a313e4f5ab4fcdab1f11fd03debb2eafa11c4121。相对指南原指纹 a97d1e…，163 个源码文件中只有 TESTING.md 散列不同，无增删；原实现/测试/配置相同。
- 原冻结包 studio-r35-2026-10-01-41 的 92 个文件、92 条清单均一致，无缺失/不符/多余。当前复跑包 studio-r37-2026-09-30-02：**186 passed / 0 failed / 0 skipped**（quick 61、core 86、plugin 12、release 8、performance 4、ui 15），另有 1 警告和 1 规模信息。
- 独立行为预期共 7 项：**1 项符合（非法 recipe 以 HTTP 200 / rejected 拒绝），6 项失败**。这些未进入既有 186 个断言，不能把 186 当成完整需求正确性的证明。
- 既有 requirements-check 的“0 声明不一致、3 未覆盖”是在运行时对现有声明的匹配；复核回填后 EDIT-009、API-004、MEDIA-002、MEDIA-004、MEDIA-005、RELEASE-003 最终 verificationStatus 已改为 failed，独立 evidence 与 lastCheckedRun 已关联。原 passing 用例保留为局部证据。
- 正常 mid 配置（1 万事件、300 素材）索引 9ms / 搜索 P95 13ms / 文本编辑 P95 **44ms** / 单场景读取 9ms，均在脚本阈值内。scene-incremental-save 实际计时的是 GET 单场景，不应另算为独立保存延迟验证。
- 首轮 studio-r36-2026-09-30-01 误把 STUDIO_VERIFY_PLAN=1 施加到性能组，测得编辑 P95 112ms；该轮完整保留。最终重新运行全部分组，性能使用正常配置，core 的 harness 仍默认全量比对。

指南指定抽查的 MEDIA-001 模板补缺省/显式值优先与 404/422、PLUGIN-002 双向 SKILL-TRUTHFULNESS 和关卡契约守卫均在当前原始结果中 passed。未接入真实服务的声明属实：只有 isTestAdapter=true 的 fixture-local，视频候选 playable=false。真实 WAV/MP4 播放用例通过，并不证明视频生成服务接通。像素预览不可用路径仍明确报 RENDER_UNAVAILABLE。

## 材料纠正与未覆盖范围

旧 HANDOFF 有过期阶段/能力描述，architecture-map 仍称音视频、关卡恢复、存档等未实现；guide 第 1 步指向另一轮包，第 3 步引用不存在的 logs/performance.json 和当前包内的 1GB 报告。这些在 materials-check 通过时仍然存在，说明当前守卫主要核对数字/指纹，未证明整篇陈述和每个文件引用正确。本次已修正 HANDOFF、REVIEW-GUIDE、known-issues、requirements 及 architecture-map（KI-040）；原材料保存在独立包 materials-before/。

历史目标规模报告的真实位置是 studio-r10-2026-10-01-17/outputs/scale-target.json（10 万事件、1000 素材，编辑 P95 95ms）；历史 1GB 报告在 studio-r24-2026-10-01-33/outputs/scale-media-1gb.json（21.61MB/s，整轮峰值 162MB）。它们都进入对应包的散列清单，但源码指纹与本次不同，属于历史测量，未在本次重跑。**1 万素材索引未实测**，20GB、冷启动索引与连续切换的内存释放也没有本轮测量；20GB 推算不能替代实测。

真实宿主加载、真实生成服务、MSI/签名和 20GB 沿用原交接记录的限制，不计入这 6 个实现缺陷。编辑器内 v1 导入按钮仍未完成；后台预览不跨重启、草稿刷新持久化、生成退避、播放副本、快进与部分设置保持台账记录。changedIds 截断（KI-030）仍待产品判定：若允许摘要，消费者必须检查 changedIdCount 并重取完整快照/历史，不能承诺完整增量列表。此次未新增产品批准。

## 证据与复跑

- 既有分组冻结包：artifacts/studio-review/studio-r37-2026-09-30-02/run.json、requirements-check.json、logs/、outputs/、artifacts.sha256。
- 独立冻结包：artifacts/studio-review/independent-audit-2026-09-30-01/run.json、observations.json、pause-observation.json、integrity.json、脚本、材料前后快照与 artifacts.sha256。
- 独立脚本均只用新建的 studio-independent-* 临时工作区，不接触正式用户工程；浏览器只读界面并切换/刷新，制作操作仍通过后台 API。结束时清理本次后台与浏览器，交付前关闭本轮 Vite。
- 独立脚本退出 0 表示复现流程完成；行为判断见 failureConfirmed 与独立 run.json 的 status。再次执行会写 observations，保留冻结结果时应先复制整个独立目录到同层新 runId，再运行并重新计算散列。

建议先修三个 P1，补充对应行为断言，再处理三个 P2。修复前保留当前失败证据；修复后按同一套复核步骤生成新源码指纹和新证据，不能把本次结果挂在后续代码上。
