# RenPyBridge 个人插件

本插件提供 31 个本地 MCP 工具及 renpy-author Skill。Codex 通过结构化工程组装剧情与本地 HTML 小游戏，调用真实 RenPy 检查、预览和导出。

## 个人安装

从已配置 Python 3.12 的源码目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\install_local.py --register
```

默认受管目录为 %LOCALAPPDATA%/RenPyBridge，包含独立环境、固定依赖、核验 SDK、核心、Skill 和个人 marketplace。安装器保留既有作品、任务、检查点和 SDK 用户数据，并记录代码/配置备份与恢复日志。--dry-run 检查计划；--destination 指定目录。

HTML 同窗宿主与固定浏览器另行准备和安装，完整步骤见 [README](../README.md)。仅核心安装成功时，HTML 能力检查仍可能报告运行时缺失。

注册身份为 renpy-bridge@renpy-bridge-personal，MCP 服务为 renpy_bridge。仓库 .mcp.json 是安装模板，安装器改写为个人安装目录中的实际路径；不要直接把模板当作已经可以运行的个人插件。开发副本移动后可用 scripts/plugin_prepare.py --write 生成本机开发配置；该配置含本机路径，应留在本地。

默认注册命令不移除其他插件。可选 --replace-development-plugin 只针对同项目的旧开发身份；明确需要去重时才使用。

注册完成后重启 Codex，让新桌面会话加载插件，然后实际读取环境和小游戏能力。当前会话不会因为文件更新而自动获得新工具；独立 STDIO 或 CLI 验证不代替桌面加载验证。

制作说明见 [USAGE](../docs/USAGE.md)，接口见 [TOOLS](../docs/TOOLS.md)。用户工程位于受管目录 projects，包括原件、素材、版本和检查点。玩家使用完整导出包，不依赖 Codex 或插件。
