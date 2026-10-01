# studio-author 插件（拾光 Studio 制作插件）

这个目录是**制作插件包**：把拾光 Studio 后台能力包装成 MCP 工具，并附带 `studio-author` 制作 Skill。AI 内容写入通过后台 HTTP API，插件自身不写正式工程文件；浏览器可用于界面观察、真实播放及用户授权的人工入口验收。产品代码修复遵循该请求的授权范围。

## 包内结构

```text
plugin/studio-author/
  .codex-plugin/plugin.json     插件清单（安装信息）
  .mcp.json                     MCP 工具服务连接配置（含占位符，安装时替换）
  skills/studio-author/
    SKILL.md                    制作流程与规则（何时使用、必须遵守的规则、标准步骤）
    references/
      project-model.md          工程结构、对象模型、事件与引用规则、事务与错误语义
      media-workflow.md         素材导入/绑定、生成任务（测试适配器）与真实服务未接入的边界说明
  server/mcp-stdio.mjs          MCP 工具适配器构建产物（npm run build:plugin 生成）
  manifest.json                 包内文件清单与散列（含生成时间与构建输入数）
```

## 构建与检查

```powershell
npm run build:plugin        # 用 esbuild 打包适配器到 plugin/studio-author/server/
npm run studio:plugin:check # 结构检查（清单、Skill frontmatter、工具引用、无写文件 API、无凭据）
npm run studio:plugin       # 两步一起执行
```

`studio-plugin-check` 覆盖本机可以验证的部分：必需文件、`SKILL.md` frontmatter（与 `C:\deepseek_harness\resources\runtime\office-skills\*\SKILL.md` 的实际格式一致）、Skill 只引用工具名而不复制参数清单、适配器构建产物**不含任何写文件 API**、包内没有凭据或 `.studio` 运行数据。报告可用 `--json <file>` 输出，最终证据包会把它写入 `outputs/plugin-check.json`。

## 安装（需要按目标宿主规范核对）

1. 运行 `npm run build:plugin` 生成适配器构建产物。
2. 把 `plugin/studio-author/` 复制到宿主的插件目录（或按宿主方式指向该目录）。
3. 编辑 `.mcp.json`：把 `${PLUGIN_DIR}` 替换为插件目录绝对路径，`${STUDIO_WORKSPACE}` 替换为工作区绝对路径。
4. 确认工作区后台可用：`npm run studio:server`（或保留 `STUDIO_AUTOSTART=1` 让适配器自行拉起）。
5. 首次运行会使用工作区内的配对密钥建立带作用域的会话（`<工作区>/.studio-service/credentials/pairing.key`）；令牌只存摘要，不出现在配置、网址或日志中。

运行地址与版本应从该工作区发现记录和健康检查核对；不要假定默认端口或包内源码已是最新。空工作区可由当前创建工具或公共 `POST /v1/projects` 创建首章/开场；已有工程先读取修订，内容事务使用稳定 requestId，同一次重试复用，发生冲突先回读并重新规划。人工草稿与暂停会话必须被保留和尊重。

保存/导出证据须关联最终修订，并核对 playerIncluded、portableIncluded（便携交付）、missingAssets、missingLevels 和 sensitiveHits。登记关卡、模拟 complete、结构快照或已有 Demo 截图不能替代真实关卡/播放器验收；修改人工入口时还须从独立空工作区走一次创作流程。

## 明确未验证的部分

- **宿主能否加载本插件与 Skill**：本机没有目标宿主的插件规范（未找到 `plugin.json` 示例或插件生成工具），因此 `.codex-plugin/plugin.json` 的字段名与必需项、`.mcp.json` 的字段结构都**尚未在宿主中验证**。见 `review/studio/known-issues.md` 的 KI-001 与 KI-022。
- 本机已验证的部分只有：MCP 协议层（真实 stdio JSON-RPC 进程的 `initialize`/`tools/list`/`tools/call`）、工具行为、Skill 引用的工具都存在于真实 `tools/list`、以及上面的静态结构检查。
