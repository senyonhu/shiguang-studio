# 新版 Ren'Py Demo

独立项目在 `client/`。依据用户提供的 2026-09-29 v1.1 方案制作，保留原网页 Demo 便于对照。附件内“首次只做阶段 1 / 使用占位素材”是方案交接文字；此次按用户直接要求实现 Demo 并使用图片生成功能制作最终分层图片。

## 已实现

- 标题、昵称、轻分支、三项任务、成功后手动继续、残响、理解题、结算及独立练习。
- Ren'Py 1920×1080 逻辑画布；实际检查 1366×768。中文与代码字体随包分发。
- 第 1 关数字片段、第 2 关条件表达式、第 3 关多行循环；完整源码预览、三级提示、源码恢复前备份。
- 自定义输入控件修正 Ren'Py 8.5.3 原生粘贴丢失换行的问题。Ctrl+C 复制整个片段，Ctrl+V / Shift+Insert 插入多行。不是支持文本选区的完整 IDE。
- 真实 C11 → WASI → Wasmtime。四用例只编译一个不可变快照。按完整 stdout、正常退出状态和源码 hash 判分。
- 编译 15 秒、每例 3 秒、fuel 2,000,000、来宾内存 64 MiB、Job 总内存 512 MiB；输出 16 KiB / 编译诊断 64 KiB。
- Windows 进程先挂入 Job 后恢复执行；取消及关闭释放进程树。受控标准输入输出，无主机目录预开放、环境变量或网络能力传给来宾。
- 草稿原子 JSON、完成收据、稳定检查点、旧档恢复时提示较新草稿、JSONL 事件及可选源码导出。
- 13 张内置 image_gen 原创图片，环境与人物始终分离，alpha 原样保留。提示词见 `asset-manifest.json`。
- 灯具暗/亮/光晕分层；门与锁纹分离；出口远景、浮岛城市、门框三层视差及可跳过演出。

## 文件入口

`client/game/story.rpy` 剧情；`screens.rpy` 界面；`controller.rpy` 主线程控制；`python-packages/local_runner/` 判题与进程管理；`tasks/` 任务数据。

开发启动：`tools/renpy-8.5.3-sdk/renpy.exe client`。
正式构建：`powershell -File packaging/build-windows.ps1`。

## 实现边界

片段词法限制是教学功能范围，不能声称为任意恶意 C 源码的通用编译沙箱。WASI 限制程序访问能力，Windows Job 管理资源与进程；两者职责不同。当前没有账号、网络服务、自动 AI 导师或防作弊成绩。

开场对白较精炼；15—20 分钟是学生阅读、尝试与复盘的目标时长，尚未做学生用时实测。音乐为旧版原创程序旋律转离线 WAV。所有视觉图片均使用内置生成工具，文字与交互由引擎绘制。

测试存档使用项目 `.runtime/renpy-test-saves/` 隔离。正式版使用稳定的用户 AppData 路径，取消游戏目录的存档镜像。缺少用户数据目录写权限时显示错误。

## 官方依据

- [Ren'Py 8.5.3](https://www.renpy.org/release/8.5.3)
- [Ren'Py 自动化测试](https://www.renpy.org/doc/html/testcases.html)
- [WASI SDK 22](https://github.com/WebAssembly/wasi-sdk/releases/tag/wasi-sdk-22)：采用官方 MinGW64 版，Clang 18.1.2；PE 导入仅依赖 Windows 10/11 自带的系统/UCRT DLL。
- [Wasmtime 49.0.1](https://github.com/bytecodealliance/wasmtime/releases/tag/v49.0.1)
- [Wasmtime CLI 选项](https://docs.wasmtime.dev/cli-options.html)

实际选项以随包工具 `--version`、`run -W help` 的输出及本机运行结果为准。
