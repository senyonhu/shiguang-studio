# HTML 小游戏接入 SDK 1.0

这份说明面向小游戏作者。玩家页面只展示玩法和自然中文状态，不展示协议、包摘要或调试入口。

三个 `production/{puzzle,timed,checkpoint}` 包保留正式清单格式、版本 `1.0.0` 和 `prototype:false`。当前 `production/v1.0.1/` 是另三个不可变新包，增加运行中与恢复后的动态音量同步。两个版本都保留完整授权文件、实际读取的本地文本资源与自然中文文案。包结构校验、SDK 测试和页面脚本测试不能证明所有窗口、存档和独立发行门槛通过；这些结论必须引用相应 revision 的真实运行记录。H0 原包仍保留在上一层目录，摘要不变。

## 页面初始化

每个包都带自己的 `bridge-sdk.js`，不引用包外文件。先加载 SDK，再加载玩法代码。

```html
<script src="bridge-sdk.js"></script>
<script src="game.js"></script>
```

```javascript
const bridge = GalgameBridge.create({
  gameId: "my_game",       // 与清单 game_id 一致
  packageVersion: "1.0.1", // 与清单 version 一致
  outcomes: ["win", "lose"],
  checkpoint: true
});

bridge.on("init", payload => {
  // payload.inputs 已由引擎按清单校验并填默认值。
  // 若非空，payload.snapshot 是 {snapshot_version, state, checkpoint_id?}。
  restoreOrInitialize(payload.inputs, payload.snapshot);
}).on("start", () => startActualPlay())
  .on("pause", () => pauseTimersAndAudio())
  .on("resume", () => resumeTimersAndAudio())
  .on("confirmed", () => showCompletion());
```

`create` 发出 `ready`。页面等待 `init` 和 `start` 后才能玩。SDK 的 `phase` 依次为 `loading`、`ready`、`running`；暂停为 `paused`，发送完成结果后为 `finishing`，收到确认后为 `confirmed`。

普通浏览器没有 WebView2 通道时，SDK 开启单独试玩。试玩事件不会写入 Galgame，也不能当作返回剧情的验证证据。生产包不含假通关按钮、直接跳剧情入口或全局修改分数接口。

## 完成与确认

完成条件由真实玩法判断。页面报告清单声明的 outcome 与完整结果字段，不指定剧情标签、不修改剧情变量。

```javascript
if (boardIsActuallySolved()) {
  disableFurtherMoves();
  try {
    await bridge.finish("win", {score: 920, moves: 8, elapsed_ms: 2100});
    showCompletion();
  } catch (error) {
    showRecoverableMessage(error.message);
  }
}
```

SDK 保留首次完成结果。同一结果的再次调用复用同一 Promise；不同结果被拒绝。等待确认时，每秒重发同一 `event_id`，默认 30 秒超时，不创造新奖赏事件。超时后页面仍不能继续得分，应使用游戏内恢复或退出路径。引擎还会按会话及结果收据防止重复应用变量效果；页面侧约束不代替引擎的校验。

条件及效果操作数中的变量绑定读取本次 finish 前的变量值。效果依保存顺序写入候选副本；后一个操作数不隐式读取前一个效果刚写入的值。全部效果与最终变量范围通过后，才整体替换变量和结果收据；保存失败时等待重试，不确认结果或前进剧情。`add` 可使用有限负数扣分，最终数值仍须满足变量声明。

`bridge.audioVolume` 是 `0..1` 的音量值。1.0.1 SDK 在 `init.payload.settings`、`settings.payload` 与 `resume.payload.settings` 中只接受有限数值，范围为 `0..1`；`0` 表示静音，字符串、布尔值、空值与越界值不会替换上次有效设置。`settings` 消息的 payload 直接为 `{audio_volume:0.25}`。当前会话收到有效值后触发 `settings` 事件；页面应立即更新已经在播放的音频元素，而不只在下次 `play()` 时设置。

```javascript
bridge.on("settings", settings => {
  tone.volume = settings.audio_volume;
});
```

页面必须响应 pause/resume，停止实际倒计时与声音；暂停时间不能算入玩法耗时。设置同步不自动开始播放，也不会把暂停状态改为运行。

## 保存进度

清单只有 `entry_restart` 的包，在重新进入时从该次入口重玩，不宣称保存每一帧 HTML 状态。声明 checkpoint 的包还需要 `snapshot_version` 和有限 `snapshot_schema`。

```javascript
await bridge.checkpoint({
  stage: 1,
  sensor: "temperature",
  wire: null,
  calibration: 34,
  mistakes: 0
}, "assembly_step_1");
```

检查点值是独立 JSON，不包含 DOM、函数、浏览器对象、文件路径或权限。当前 SDK 发 `snapshot_version:"1.0"`；引擎核对包版本、快照版本及 schema，再确认事件。最大快照 64 KiB、JSON 深度 32。页面必须校验恢复状态之间的关系；版本或内容不兼容时报告错误，不能静默重置后声称恢复成功。

前两道真实修复工序提交检查点，等待确认后才允许下一工序。最终修复只由正确探头、正确端子和实际校准值同时满足时完成。

## 消息结构

```json
{
  "protocol": "1.0",
  "session_id": "由引擎建立的本次会话身份",
  "event_id": "事件身份",
  "type": "finish",
  "payload": {"outcome": "win", "results": {"score": 920, "moves": 8, "elapsed_ms": 2100}}
}
```

页面事件为 `ready`、`checkpoint`、`finish`、`error`。宿主命令为 `init`、`start`、`pause`、`resume`、`settings`、`ack`。`ready` 的空 `session_id` 只用于初始化前来源检查；之后必须匹配已建立会话。`host_ready` 是宿主诊断，和页面 `ready` 不同。

`init.payload` 包含 `game_id`、`package_version`、`package_hash`、`inputs`、`snapshot`、`settings.audio_volume`、`capabilities.checkpoint`。`ack.payload` 的 `event_id` 引用待确认事件，并带 `accepted`。SDK 对不匹配会话、错误 game_id 的 init、重复 init 和迟到 ACK 不改变状态。引擎按实际来源和不可变包验证数据，SDK 本身不构成系统级浏览器沙箱。

`bridge.reportError(code, detail)` 报告页面故障。玩家只看到可操作的中文恢复提示；详情用于制作诊断。

## 包清单与摘要

包目录有 `manifest.json`、入口页面和所有本地依赖。`files` 列出所有文件的相对 POSIX 路径、字节数、SHA-256，排除清单自身；包摘要包含清单自身。

```json
{
  "format_version": "1.0", "prototype": false,
  "game_id": "my_game", "version": "1.0.0", "entry": "index.html", "protocol": "1.0",
  "display_name": "星图锁",
  "inputs": {"seed": {"type": "integer", "default": 1742, "minimum": 0}},
  "results": {"outcomes": ["win"], "fields": {"score": {"type": "integer", "minimum": 0}}},
  "save_capabilities": {"entry_restart": true, "checkpoint": false, "snapshot_version": null},
  "required_capabilities": ["local_content", "host_messages"],
  "license": "MIT; see LICENSE.txt",
  "files": [{"path": "index.html", "sha256": "填入实际64位小写摘要", "bytes": 1234}]
}
```

示例摘要占位符不是可导入包。完整实例见三个实际包的清单。

算法固定为：对包内所有常规文件，按相对路径 UTF-8 字节排序；外层 SHA-256 逐项接收 `path_utf8 + NUL + file_sha256_lowercase_ascii + LF`。注册复制到工程的 `minigames/<digest>`，同摘要不可变，重新修改任何文件都得到新包。清单不得写入自身包摘要来产生自引用。

默认上限为 256 MiB 和 4096 个文件。目录与 ZIP 都拒绝路径穿越、重复或大小写碰撞、链接或重解析点、额外/缺失文件、错误摘要、本机程序与引擎脚本。页面无网络依赖，不请求下载、文件、摄像头或麦克风权限。

产品要求是在同一个可见游戏窗口内切换剧情和 HTML 小游戏。此前专用小游戏窗口的记录只证明通用玩法与消息交互，不满足同窗要求；同窗容器、焦点、窗口缩放和恢复必须另做实证。当前验证环境为 Windows 11 x64，其他平台能力也必须单独验证。能力发现或 `prototype:false` 都不表示某个项目已通过同窗或发行验收。

## 重建与验证示例

原 `build_production_examples.py` 保留为 1.0.0 的制作来源。当前运行 `python examples/html_minigames/tools/build_production_upgrade.py` 创建独立 1.0.1 目录；已经存在时只核对冻结摘要，不覆写同版本。每次修改生产源必须使用新版本和新摘要，再通过真实制作接口登记新包，不篡改已经导入的不可变目录。

`node --test examples/html_minigames/tests/production.test.cjs` 执行实际页面脚本的胜利、计时失败、暂停、检查点恢复和 ACK 协议测试。这些是逻辑测试；真实浏览器、Ren'Py 返回剧情、玩家存档与独立发行仍要继续验证。

`node --test examples/html_minigames/tests/settings.test.cjs` 对 1.0.1 重复实际 SDK 与页面玩法测试，并核对初始静音、运行中音量变化、暂停/恢复同步、错误会话与非法数值。声音元素的 JS 属性测试不等于物理音频设备输出验收。
