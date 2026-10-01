# 拾光 Studio 审查指引（给独立审查者）

本文件是**独立复核的入口**：按顺序做完下面 6 步，就能在本地复现全部结论，并知道哪些结论**没有**证据支撑。
更完整的阶段状态见 [HANDOFF.md](HANDOFF.md)；问题台账见 [known-issues.md](known-issues.md)。

> 2026-09-30 代码修正：独立复核发现的 **6 个缺陷已修复**（KI-034–039），新增 12 项行为回归；完整验证 200 项通过。当前说明见 [CODE-REPAIR.md](CODE-REPAIR.md)，历史失败见 [INDEPENDENT-REVIEW.md](INDEPENDENT-REVIEW.md)。需求状态以最新 lastCheckedRun 与 resolution 为准。

## 0. 先看结论的三个数字

| 项目 | 当前值 |
| --- | --- |
| 冻结证据包 | `artifacts/studio-review/studio-r40-2026-09-30-05/` |
| 源码指纹 | `0441c69e427d2acb179fb09defc7deda50ff5ba46a7cf630920fd8224b3822dc` |
| 结果 | **200 通过 / 0 失败 / 0 跳过**（quick 65 / core 95 / plugin 12 / release 8 / performance 4 / ui 16），其它 2 条为 1 警告 + 1 条规模信息 |
| 需求核对 | 运行时既有测试声明不一致 **0 处**；未覆盖 **3 条**：`PLUGIN-006`、`MEDIA-006`、`RELEASE-004` |

本轮便携包实际解压验收见 `artifacts/studio-review/portable-empty-2026-09-30-01/portable-check.json`（16 通过，0 失败；含无样例检查、空白状态提示、界面导出、复制整个 content 后观看、中文目录、保存与重启、关卡和存档）。复跑：`node scripts/studio-portable-check.mjs`。

## 1. 五分钟验证「证据与源码一致」

```powershell
cd C:\codex\C_game
node scripts/studio-digest.mjs          # 打印当前源码指纹
```

把输出与上表指纹、与 `artifacts/studio-review/studio-r40-2026-09-30-05/run.json` 的 `sourceDigest` 三者对比。
不一致时看 HANDOFF 的「指纹差异说明」及修复包 source-changes.json；本次代码与新增测试已绑定新指纹。

> 提示：`review/`、`artifacts/`、`node_modules`、`dist*` 不参与指纹；一次性脚本 `scripts/tmp-*.mjs` 不应存在。

**证据包自身的完整性**：每个证据包都带 `artifacts.sha256`（逐文件散列）。可这样核验（本轮实测：**103 条文件清单，缺失 0、散列不符 0**）：

```powershell
# 逐文件重算并与清单比对（PowerShell 版）
$run = "artifacts/studio-review/studio-r40-2026-09-30-05"
Get-Content "$run/artifacts.sha256" | ForEach-Object {
  $parts = $_ -split "\s+", 2; $expect = $parts[0]; $rel = $parts[1].Trim()
  $actual = (Get-FileHash "$run/$rel" -Algorithm SHA256).Hash.ToLower()
  if ($actual -ne $expect) { "MISMATCH: $rel" }
}
```

没有任何输出即表示证据包未被事后改动。

## 2. 复现证据包（约 10–15 分钟）

```powershell
npx vite --host 127.0.0.1 --port 5173        # 浏览器分组需要它（否则 ui 记为 not_run，不算通过）
npm run build; npm run build:editor; npm run build:player; node scripts/build-plugin.mjs
Remove-Item Env:STUDIO_VERIFY_PLAN -ErrorAction SilentlyContinue  # core 自带校验；性能用正常配置
node scripts/studio-evidence.mjs --run-id <自定义> --groups quick,core,plugin,release,performance --e2e --scale-profile mid
node scripts/studio-link-evidence.mjs --run-id <自定义>
```

产物：`run.json`（分组与逐项结果）、`requirements-check.json`（需求核对）、`logs/`（原始输出）、`artifacts.sha256`（逐文件散列）。
**验收口径**：`passed` 才算通过；`not_run`/`skipped` 不计入通过；`plugin` 的 1 条警告是宿主清单未验证（KI-001/KI-022），不是通过。

## 3. 逐项抽查「声明是否等于事实」

不要只读汇总，抽 3 条需求回到原始输出：

| 抽查项 | 怎么查 |
| --- | --- |
| `MEDIA-001`（生成模板接生成请求） | 看 `logs/core.json` 中 `templateId 用模板补齐提示词与参数…`；关注点：请求显式值优先、模板不存在 404、类型不一致 422 |
| `EDIT-014`（规模与媒体负载） | 本轮看 `outputs/scale-mid.json` 与 `logs/performance.log`；历史 1GB 原始数据在 `artifacts/studio-review/studio-r24-2026-10-01-33/outputs/scale-media-1gb.json`，target 在 `studio-r10-2026-10-01-17/outputs/scale-target.json`（均非本轮源码重测）；**20GB 是推算而非实测** |
| `PLUGIN-002`（Skill 与工具一致） | `node scripts/studio-plugin-check.mjs`；看 `SKILL-TRUTHFULNESS`（双向）与 `PLUGIN-SKILL-LEVEL-CONTRACT` |
| 材料与证据是否自洽 | `node scripts/studio-materials-check.mjs`——核对材料声称的证据包：指纹、通过/失败数、未覆盖数与审查指引是否一致 |

## 4. 重点怀疑清单（这些地方最可能有问题）

1. **真实服务全部未接入**：媒体生成只有 `fixture-local`（`isTestAdapter: true`）；视频候选是标注 `playable: false` 的占位文件。任何「已生成真实视频」的说法都不成立。
2. **像素级预览**依赖本机 Chromium 系浏览器；无浏览器时返回 `RENDER_UNAVAILABLE`（明确错误，不是静默降级）。
3. **插件宿主加载未验证**（PLUGIN-006）：MCP 协议层经真实 stdio 进程验证，但「目标宿主能否加载本插件」没有宿主规范可核对。
4. **20GB 媒体未生成**（磁盘约束）：1GB 实测吞吐 21.6 MB/s、峰值内存 162MB；更大规模是线性推算。
5. **交付物是内置运行环境的 Windows x64 便携 ZIP**，MSI/NSIS 与签名仍未完成。可选 `install.cmd` 安装与 `uninstall.mjs` 卸载已验证；便携副本停止后可直接删除，需先备份作者工作区。
6. **KI-030 未判定**：变更记录/重放结果的 `changedIds` 截断为 20 个 + `changedIdCount`——是否可接受由你决定。
7. **界面内 v1 导入按钮未实现**（KI-011 剩余项）：迁移能力本身已由 `tests/studio/integration/p5-v1-into-project.test.ts` 验证。

## 5. 反证方法（怎么推翻我们的结论）

| 想验证的怀疑 | 最小反证 |
| --- | --- |
| 「事务校验其实没生效」 | 提交一个非法操作（如 `recipe.create` 用 `taskType: 'audio.text'`），应返回 `status: 'rejected'`（HTTP 200） |
| 「增量保存与全量一致」 | 用 `STUDIO_VERIFY_PLAN=1` 跑 `core` 分组，它会逐次比对计划与全量序列化 |
| 「外部 AI 有后门绕过事务」 | 检查工具注册表：内容编辑工具最终调用 `POST /v1/projects/{id}/transactions`；生成任务另走 createJob 并检查暂停；桌面自动化相关依赖为零 |
| 「播放是假的」 | 跑 `tests/e2e/p2-player.spec.ts`：真实 WAV/MP4、真实浏览器解码，断言时长与自动播放 |
| 「文档比实现漂亮」 | 跑 `node scripts/studio-plugin-check.mjs`：文档漂移会被 `SKILL-TRUTHFULNESS` 双向拦下 |

## 6. 已知未完成项（不要当成已完成）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 编辑器内 v1 导入按钮 | 未开始 | 需把 v1→事务映射搬进后台动作并加界面上传（KI-011 剩余） |
| MSI/NSIS 安装包与代码签名 | 未做 | 本机**无工具链**（实测：`makensis`/`wix`/`candle`/`light`/`signtool` 均不在 PATH，NSIS 与 WiX 安装目录不存在，Windows SDK 未装）；现为便携包 + 脚本式安装（KI-012） |
| 真实 AI 宿主加载 | 未验证 | 无宿主插件规范（PLUGIN-006） |
| 真实图片/视频生成服务 | 未验证 | 无凭据（MEDIA-006） |
| 20GB 级媒体负载 | 未实测 | 按要求不填满用户磁盘（1GB 已实测） |
| `changedIds` 截断策略 | 待判定 | 需要产品判断（KI-030） |

## 7. 复核后请回填

若你修正了任何结论，请同时更新：`HANDOFF.md`（指纹与统计）、`requirements.json`（`evidence` 与 `lastCheckedRun`）、`known-issues.md`（状态列），并重跑第 2 步生成新的冻结证据包——**不要把旧指纹的证据挂在新代码上**。

## 8. 状态总表（已实现 / 已验证 / 模拟验证 / 未验证 / 未完成）

口径：**已验证** = 有可复跑的用例通过且计入证据包；**模拟验证** = 走完整协议但对手方是测试适配器/占位产物；**未验证** = 功能存在但没有自动化用例；**未完成** = 还没做。

| 能力 | 状态 | 证据 / 说明 |
| --- | --- | --- |
| 后台单一写入口（事务、校验、撤销、幂等、日志） | 已验证（含暂停期间的新生成及估价竞态） | core 分组；非法操作返回 `status: rejected`（HTTP 200） |
| 崩溃恢复与日志提交协议 | 已验证 | `p1-recovery`；含故障注入（`STUDIO_FAULT_ENABLE`/`STUDIO_FAULT_POINT`） |
| 增量保存与全量一致 | 已验证 | `STUDIO_VERIFY_PLAN=1` 下逐次比对（core） |
| 编辑器五个面板（变量/角色/关卡/主题/生成模板） | 已验证（含工程往返切换订阅） | 浏览器用例 16 项；改完即与后台、磁盘一致 |
| 主题贯穿模型→动作→面板→展示器 | 已验证 | 面板改主题 + 展示器对白样式断言 |
| 生成模板接到生成请求（`templateId`） | 已验证 | 模板补缺省、请求优先；404/422 路径 |
| MCP 插件工具（36 个） | 已验证 | 真实 stdio JSON-RPC 进程；打包产物与注册表一致 |
| 制作 Skill（含关卡协议与快照契约） | 已验证 | 结构检查 12 项，含双向文档漂移守卫 |
| 独立展示器（对白/选择/关卡/结局/存档/设置） | 已验证 | 浏览器 16 项；真实 WAV/MP4 解码 |
| 像素级预览 | 已验证（依赖本机浏览器） | 无浏览器时返回 `RENDER_UNAVAILABLE`，不静默降级 |
| 发布包完整性与离线运行 | 已验证 | release 8 项；便携包内直接跑通制作+导出 |
| 脚本式安装/卸载（便携包） | 已验证 | 安装到临时前缀→用安装副本真实启动→卸载后目录消失 |
| v1 迁移：新建工程 | **已修复并验证** | 保留工作区，验证入口、双分支、next、舞台状态及默认输出目录（KI-034、KI-035） |
| v1 迁移：导入既有工程（`--project`） | **已修复并验证** | 跨 200 操作引用、连续重复导入、已有后台与工作区保留 |
| 生成服务对接（图片/视频任务全流程） | **模拟验证，已修复暂停/候选/调度缺陷**（KI-036–038） | 仅 `fixture-local`（`isTestAdapter: true`，不访问网络）；视频候选 `playable: false` |
| 规模与性能 | 本轮 mid 已验证；target 为历史测量 | 本轮 1 万事件 / 300 素材编辑 P95 43ms；历史 10 万事件 / 1000 素材 P95 95ms，位置见第 3 节；1 万素材索引未实测 |
| 媒体负载 | 已验证（1GB）/ 20GB 未实测（需求方已接受为已记录的限制：不填满用户磁盘） | 1GB：吞吐 21.6MB/s、峰值 162MB；20GB 为线性推算 |
| 真实 AI 宿主加载插件 | **未验证（需求方已接受为已记录的限制）** | 无宿主插件规范可核对（PLUGIN-006）；MCP 协议层已用真实 stdio 进程验证 |
| 真实图片/视频生成服务 | **未验证（需求方已接受为已记录的限制）** | 无凭据（MEDIA-006）；现为明确标注的测试适配器 |
| 编辑器内 v1 导入按钮 | **未完成（超出方案要求的界面扩展）** | 剩余工作量比原先估计小：全部所需动作都是公开动作（`scene.create`/`event.insert`/`asset.registerFromFile`），编辑器读入 JSON 后按同一套映射调 `submitOperations()` 即可，**无需改后台**；内嵌 data URI 图片需另行导入（详见 KI-011） |
| MSI/NSIS 安装包与代码签名 | **未完成（需求方已接受为已记录的限制）** | 本机无 WiX/NSIS/SDK 工具链（已实测）；现为便携包 + 脚本式安装（KI-012） |
| `changedIds` 截断策略（20 + `changedIdCount`） | **待产品判定** | 语义已记录在 KI-030，尚未定论 |

> 若你发现任何一行的状态与事实不符，请按第 7 节回填并重跑证据包——**这张表本身就是待审查对象**。
