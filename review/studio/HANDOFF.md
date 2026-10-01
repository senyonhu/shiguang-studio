# 拾光 Studio 实施交接（代码修正后）

> 2026-09-30：独立复核确认的 KI-034–039 六个缺陷已修正并加入行为回归。本轮完整验证 200 项通过。历史失败报告保留在 [INDEPENDENT-REVIEW.md](INDEPENDENT-REVIEW.md)，修复说明见 [CODE-REPAIR.md](CODE-REPAIR.md)。

当前交付：`release/ShiguangStudio-Editor-0.1.0-win-x64-export.zip`（新增“导出作品”按钮的编辑器）与 `release/ShiguangStudio-Player-0.1.0-win-x64.zip`（不含样例的空白展示器）。内置 Node，解压双击启动。实际 ZIP 交付验收 **16 通过 / 0 失败**，报告与截图见 `artifacts/studio-review/portable-empty-2026-09-30-01/`。编辑器导出的整个 content 文件夹复制到展示器目录即可观看；整个导出目录也可作为独立作品包分发。旧包保留供追溯。

## 1. 身份与范围

| 项 | 当前结果 |
| --- | --- |
| 根目录 / 方案 | C:\codex\C_game；docs/editor-player-ai-plan.md v2 |
| 版本记录 | 无 .git；沿用 baseline/inventory.json 与 baseline/original/；本次修改前源码备份在 artifacts/studio-review/repair-2026-09-30-01/source-before/ |
| 当前源码指纹 | `0441c69e427d2acb179fb09defc7deda50ff5ba46a7cf630920fd8224b3822dc`（证据包 `studio-r40-2026-09-30-05`） |
| 指纹差异说明 | 相对 r39，新增 0 个文件、修改 9 个、删除 0 个；清单见 portable-empty-2026-09-30-01/source-changes.json。本轮新增界面导出与空白展示器，验证直接复制 content 即可观看。 |
| 当前阶段 | P0–P5 主体已实现；本次六个缺陷已修复，真实服务、宿主加载、安装签名等限制维持原记录。 |

## 2. 本次结果与证据

**证据包**：`artifacts/studio-review/studio-r40-2026-09-30-05/`。
本轮统计：**通过 200、失败 0、跳过 0、未执行 0**（quick 65 / core 95 / plugin 12 / release 8 / performance 4 / ui 16）；另有插件宿主清单 1 警告、规模 1 信息。requirements-check.json 声明不一致 0 处，未覆盖 **3 条**（PLUGIN-006、MEDIA-006、RELEASE-004）。证据清单 103 条，独立重算记录位于 portable-empty-2026-09-30-01/full-run-integrity.json。

前次修复新增长期回归 12 项：4 项暂停单元测试、3 项媒体 HTTP 集成、4 项迁移集成、1 项工程往返切换浏览器测试。本轮再增加 2 项：无全局 Node 的双击启动/端口冲突/停止，以及关卡嵌套依赖导出。实际 ZIP 的 16 项验收单列，不计入上述 200 项。

| 缺陷 | 修正 / 验证 |
| --- | --- |
| KI-034 / P1 | 显式工作区成功/失败均保留；成功的自动临时工作区也保留，报告给出 workspace。只清理失败时脚本自己创建的临时目录；复用已有后台时不关闭它。 |
| KI-035 / P1 | 先分配全部节点首事件 ID，再分两阶段连接 entry、next 与选项。保留背景/立绘状态；跨 200 操作、重复导入、双分支与非首节点入口均有运行内核断言。报告含旧节点/素材 ID 映射。 |
| KI-036 / P1 | 新生成在校验前和估价后检查会话可写；等待期间暂停也返回 SESSION_PAUSED，不落任务、不预留预算。已有任务的幂等读取保持可用。 |
| KI-037 / P2 | 素材 ID 按 jobId + outputId 生成稳定散列；两个候选分别入库，同一候选重试返回相同资产和修订。 |
| KI-038 / P2 | 调度先筛选可推进状态，再按创建时间分配并发槽；unknown 保留提交信息和预算但不挡正常任务。取消 unknown 后可继续确认取消。 |
| KI-039 / P2 | 工程变化关闭旧订阅并订阅当前工程；忽略旧工程返回的读取与回调，清理旧工程撤销/日志状态。真实 Edge 验证 A→B→A 后的外部更新自动显示。 |

本轮 mid：50 章 / 200 场景 / 1 万事件 / 300 素材；索引 10ms、搜索 P95 14ms、编辑 P95 **43ms**、单场景读取 10ms，均在脚本阈值内。scene-incremental-save 实际计时的是单场景读取，不另算保存延迟。core 的 harness 默认开启 STUDIO_VERIFY_PLAN=1；性能使用正常配置。

历史失败证据 independent-audit-2026-09-30-01（旧源码，1 符合 / 6 失败）与 r37（旧源码，186 通过）保持冻结。需求表保留历史 failed evidence 与 independentVerification，新增 resolution 指向本次；六条关联需求的 verificationStatus 与 lastCheckedRun 已更新。

历史规模报告：10 万事件 / 1000 素材编辑 P95 95ms 在 studio-r10-2026-10-01-17/outputs/scale-target.json；1GB 吞吐 21.61MB/s、整轮峰值 162MB 在 studio-r24-2026-10-01-33/outputs/scale-media-1gb.json。均非本轮源码重测。1 万素材索引、20GB、冷启动索引与持续切换内存释放未实测。

## 3. 复跑

Node 24.14.1、现有 node_modules、本机 Edge；测试独占临时工作区，不使用正式用户工程。

```powershell
cd C:\codex\C_game
npm run build
npm run build:player
node scripts/build-plugin.mjs
npx vite --host 127.0.0.1 --port 5173  # 另一个终端
Remove-Item Env:STUDIO_VERIFY_PLAN -ErrorAction SilentlyContinue
node scripts/studio-evidence.mjs --run-id <全新ID> --groups quick,core,plugin,release,performance --e2e --scale-profile mid
```

原独立复现脚本会写 observations；需要再次运行时先复制到同层新目录，避免改动已冻结的失败证据。当前回归无需调用旧脚本，直接计入完整验证。

## 4. 主链路与剩余范围

人工 LiveApi / 外部 MCP → transactions → EditService → journal.commitTransaction；生成任务另走 MediaJobService.createJob 并使用同一会话暂停状态；候选接受进入统一编辑事务。迁移图由 studio-v1-map.mjs 生成，再经后台 API 写入。预览与展示器共用 runtime/kernel.ts。

KI-030 changedIds 截断仍待产品判定。预览会话跨重启、草稿刷新持久化、生成退避、播放副本、快进/部分设置等维持 known-issues 记录。工程切换已验证；断线重连与客户端自动 sequence/gap 补齐不在本次覆盖范围。媒体生成仍只有 fixture-local（isTestAdapter=true），视频候选 playable=false。

## 历史需求方确认记录

沿用此前记录的环境限制：真实宿主加载无规范可核对；真实生成服务无凭据；本机无 MSI/NSIS/代码签名工具链，交付为便携包；20GB 按要求不填满磁盘，只有历史 1GB 实测。本次没有新增产品批准。

编辑器内 v1 导入按钮仍未完成。迁移脚本的两条路径已修正并新增真实运行行为断言。
