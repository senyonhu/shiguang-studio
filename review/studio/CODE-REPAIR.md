# 2026-09-30 代码修正记录

独立复核确认的 KI-034–039 已完成代码修正；编辑器、展示器和插件构建成功。本轮证据 studio-r38-2026-09-30-03：**198 通过 / 0 失败 / 0 跳过**，另有 1 条插件宿主警告及 1 条规模信息。

源码指纹：`752eeca8031a0688f29ed96fc8cdee15a4c23ee15086bf9372df4869375d5479`（167 文件）。原历史失败包与 r37 保持不变。

| 问题 | 修正 | 回归证据 |
| --- | --- | --- |
| KI-034 工作区被删除 | 成功保留输出；失败仅清理脚本自建目录；已有后台不被停止 | p5-review-migration 的成功、失败、默认目录及后台保留断言 |
| KI-035 分支映射错误 | 预分配首事件，连接 entry/next/choices；两阶段引用；唯一导入标识；节点舞台状态和 ID 映射 | 双分支实际跑到正确结局，入口非首节点、反向 next、跨 200 操作及重复导入 |
| KI-036 暂停仍可生成 | 会话守卫在校验前和估价后执行；拒绝后不落任务/预算 | before/validate/estimate 3 条竞态、幂等重放、HTTP 人工暂停/恢复、浏览器暂停生成 |
| KI-037 第二候选无法入库 | jobId+outputId 稳定散列，已登记 ID 兼容重试 | 双候选各入库、每个候选重试资产与修订不变 |
| KI-038 unknown 阻塞 | 筛选可推进状态后按创建时间调度，unknown 保留核对/预算 | 正常任务自动完成，未知任务 attempts=1、预算保留、仍可取消 |
| KI-039 切工程仍订阅旧工程 | 重绑 SSE，旧读取/回调失效，清理旧工程状态 | Edge 中 A→B→A，外部写入自动显示、工程日志不混入 |

长期新增回归 12 项（quick +4、core +7、ui +1）；原暂停浏览器测试亦补充生成拒绝。完整结果 quick 65 / core 93 / plugin 12 / release 8 / performance 4 / ui 16，所有测试退出码 0，无 flaky。mid（1 万事件 / 300 素材）编辑 P95 57ms。历史 target 与 1GB 未在本次重测。

证据：artifacts/studio-review/studio-r38-2026-09-30-03/run.json、logs/quick.json、logs/core.json、logs/e2e.json、requirements-check.json、outputs/、artifacts.sha256。修复包 repair-2026-09-30-01 保存修改前源码、材料前后快照、改动清单、工程切换截图、完整性与材料核对结果。

六条关联需求已按本轮断言更新 verificationStatus/lastCheckedRun；旧 failed evidence 保留，independentVerification.resolution 指向新源码。仍未接入真实生成服务；断线重连与客户端自动 gap 补齐、界面 v1 导入按钮等维持交接中的范围限制。
