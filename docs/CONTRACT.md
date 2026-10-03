# 项目模型与操作契约 1.0 / 1.1

本契约对应 `src/renpy_bridge/models.py` 的 Pydantic 2 数据模型。普通剧情默认 `schema_version="1.0"`；使用变量和小游戏需显式迁移到 `1.1`。未声明字段严格拒绝，数字或 ID 不从错误类型自动转换。剧情模型保存结构化数据，不执行 Python、任意 RenPy 表达式或通用 Screen/ATL；独立登记的 HTML 游戏包可包含受控的 JavaScript。模型检查不代表媒体文件存在、引擎运行或游戏包验收通过。

当前契约冻结对象边界与语义。CLI/MCP 的实际工具名称和可调用状态以已实现服务为准；不得根据本文将未实现接口报告为可用。

## 1 对象与最低构造

`Project(project_id="project_demo", name="中文作品", save_namespace="project_demo")` 可创建尚未导入的空项目。默认 revision 为 0，画布为 1280×720，语言为 schinese，入口为空。acts、frames、characters、assets 是以稳定 ID 索引的字典。

一个合法的单帧项目：

```python
from renpy_bridge.models import Project, Act, Frame, Dialogue, Flow

project = Project(
    project_id="project_demo",
    name="中文作品",
    save_namespace="project_demo",
    entry_frame_id="frame_1",
    acts={"act_1": Act(act_id="act_1", frame_ids=["frame_1"])},
    frames={
        "frame_1": Frame(
            frame_id="frame_1",
            act_id="act_1",
            dialogue=Dialogue(speaker_id=None, text="你好。"),
            flow=Flow(kind="end"),
        )
    },
)
```

| 对象 | 字段与边界 |
|---|---|
| Project | project_id、name、revision、schema_version、canvas、language、entry_frame_id、save_namespace、acts、frames、characters、assets、layout、sdk_version、adapter_version、metadata、provenance |
| Act | act_id、name、order、frame_ids、provenance |
| Character | character_id、name、color、metadata；角色身份独立于表情图片 |
| Asset | 必需 asset_id、kind、path、sha256；version 默认 1，name 默认空，metadata/provenance 默认为空字典 |
| Frame | 必需 frame_id、act_id、flow；source_label、order、bg、characters、decorations、dialogue、voice、bgm、sfx、transition、provenance |
| Component | 必需 component_id、kind；asset_id/text、character_id、layout、zorder、font_size、color、provenance |
| Dialogue | text 为原始文字，speaker_id=null 表示旁白，provenance 保存来源 |
| Choice | choice_id、text、target_frame_id、provenance |
| Flow | kind=next/choices/end/minigame；普通三种与小游戏配置互斥 |
| LayoutProfile | safe_margin=.04、dialogue_height=.24、font_size=32、min_font_size=20、character_slots={} |
| Transition | kind=none/dissolve/fade；duration 为秒，默认 .3；none 不执行转场 |

metadata 与 provenance 只接受 JSON 值。Asset.metadata 可保存 width/height、透明区域、duration_seconds、格式、解码结果等。Project.metadata 可保存导入基线引用和操作来源；不把大原始文件、完整媒体字节或可执行代码塞进字段。schema 迁移必须显式进行；新 schema 不能被旧写入器静默降级。

## schema 1.1 的变量与小游戏

Project 新增 variables 和 minigames；只有 1.1 接受非空变量、小游戏和 minigame 流程。变量有稳定 variable_id、int/float/bool/str 类型、默认值、可选上下界及允许值，scope固定save。游戏身份绑定 game_id、版本、完整package_digest、清单和 minigames/<digest> 本地目录。

Flow.kind=minigame 必须含 game_id、全工程唯一instance_id、类型化inputs、result_branches，以及独立的cancel_target_frame_id、error_target_frame_id和fallback_target_frame_id。save_mode 为 entry_restart 或 checkpoint。结果分支按 outcome 和有界 Predicate 选择，并执行具有稳定effect_id的 set/add 效果；目标、结果字段和变量类型需完整验证。

输入绑定仅允许字面量或已声明变量；结果提交后可按声明的结果字段求值。条件只有有界比较与 all/any/not 结构，不接受任意Python/JavaScript表达式。JSON模型中的字符串不执行；HTML包内的JavaScript在宿主内运行，通过受控SDK桥提交类型化数据。

小游戏清单限定文件、入口、输入、结果及检查点schema。存储层检查文件内容摘要与目录边界；宿主限制本地资源、导航和网络。通过静态清单不代表玩法正确，真实源工程和独立包仍分别验收。

## 2 稳定身份与引用不变量

ID 格式为 ASCII 字母开头，后接字母、数字、下划线或连字符，最多 64 个字符；建议按类型使用 project_/act_/frame_/character_/asset_ 前缀。显示名称、Excel 行号和用户的幕帧编号都可以是中文，与 ID 分开。

- 每个字典的 key 必须等于对象自己的 ID。不同对象类型的身份空间独立。
- 非空 frames 必须指定有效 entry_frame_id。入口为空只适用于尚未导入的空项目。
- 每帧的 act_id 必须存在，且恰好登记在那个 Act.frame_ids 中；不允许重复归属。批量修改需要同时更新帧和幕索引。
- Act.frame_ids 记录幕内显示顺序；Frame.order/source_label 是来源和展示信息。运行连接以 Flow 中的稳定 ID 为准，不按数组下标跳转。
- component_id 在单帧内唯一；同帧 PATCH 期间保持稳定。同角色跨帧依靠 character_id 识别，不依赖素材文件名。多个帧可以使用同样的本地组件 ID。
- choice_id 在同一菜单内唯一；不同选项可以有相同文字，不可据标题判断身份。
- 人物组件必须绑定存在的角色；对白 speaker_id 非空时也必须存在。角色可以说话但不显示立绘。
- 所有画面组件的素材必须存在且 kind=image；voice/bgm/sfx 必须引用 kind=audio。所有流程目标必须存在。
- 布局 character_slots 引用已存在的角色。当前引用同一路径的素材必须对应相同 hash；Windows 大小写差异不能代表两个不同文件版本。

素材 ID 代表逻辑身份；version 与 sha256 表示具体内容版本。修改同名素材内容应记录新版本并保留旧检查点引用；模型校验不读取文件内容，文件 hash 核对由导入/存储层完成。

无对白且不是选择交互的帧作为纯过场，自动隐藏原生quick_menu快捷文字；之后进入对白或选项帧时明确恢复。该状态跟随原生存档/回滚，Esc菜单仍可用。首版无需用户为纯背景帧额外编写UI代码。

首版实际接受 PNG/JPEG/WebP/BMP 图片、TTF/OpenType-CFF 字体、PCM WAV/Vorbis OGG/Layer III MP3 音频。登记时按实际内容解析，扩展名须匹配；脚本和可执行文件不能作为素材。压缩音频的完整性检查不代替 Ren’Py 实际解码播放。生成工程复验媒体内容，原件和完整 SHA256 保留；引擎文件名使用24位哈希前缀并检测碰撞，清单保存完整哈希。超过原生 Windows 路径长度时在启动前报错。历史生成版本的短名素材通过完整哈希索引连续保留，不能仅凭文件名判断内容身份。

## 3 完整帧与局部 PATCH

Frame 的 bg=None、characters=[]、decorations=[]、dialogue=None、voice=None、bgm=None、sfx=[] 明确表示该帧没有对应内容。首次完整导入省略这些字段也得到相同空状态，不能从上一帧继承。flow 必须明确指定，不能无意省略造成故事被截断。

Component.kind 只有 background、character、image、text。bg 只接受 background；characters 只接受 character；decorations 只接受 image/text。文字组件用 text，不能同时绑定图片；人物组件必须指定 character_id。图片原文件不被改写。

`FramePatch` 及 `apply_frame_patch(frame, patch)` 使用以下规则：

| PATCH 输入 | 意义 |
|---|---|
| 字段省略 | 保留当前帧字段 |
| bg/dialogue/voice/bgm/source_label=null | 清空对应可空字段 |
| characters/decorations/sfx=[] | 清空该集合 |
| 集合出现非空列表 | 用完整新列表替换，不按位置做隐式合并 |
| characters/decorations/sfx/transition/flow/provenance=null | 拒绝；这些字段必须保持结构 |
| frame_id/act_id | 不允许局部 PATCH 改身份或归属；跨幕移动用完整批量事务 |

`apply_frame_patch` 返回新 Frame，不修改传入 Frame；随后存储层必须把它放回候选项目并重新验证完整 Project，才能发现缺失素材、角色或流程目标。原始数据不能使用未经重新验证的 `model_copy(update=...)` 直接持久化。组件级局部修改同样应按 component_id 合并完整组件，再验证整帧和整项目。

生成器每次进入帧重设工具管理的故事图层；汇合帧不能只比较 Excel 上一行。与剧情无关的标准 GUI 单独管理。无对白帧提供一次推进等待；菜单帧的对白和选项在同一次交互出现。语音按帧播放；同 BGM 的连续有效状态保持，不重新开始；sfx 是本帧事件。

## 4 布局坐标

Layout 只有一套归一化坐标：x/y 是锚点在画布中的位置，anchor_x/anchor_y 是组件自身锚点；width/height 是画布宽高比例，None 保留固有大小或按另一维和比例计算。fit=contain 保持比例完整显示，fit=cover 保持比例填满目标区域并裁切。两维都指定时适配器必须明确按该目标区域拟合；不能无意拉伸人物。

默认 Layout 为底部居中（x=.5,y=1,anchor_x=.5,anchor_y=1）。Component 未提供 layout 时根据 kind 设定：background 居中、1×1、cover；character 底部居中且 height=.85；image 底部居中采用原尺寸；text 顶部居中、y=.1、anchor_y=0、width=.9。显式 layout 是用户/AI 已确定的布局记录，不被生成器再次随机安排。

LayoutProfile.character_slots 是布局工作流的参考；应用布局时写回 Component.layout。生成器只消费已保存组件布局，不在生成期隐式覆盖为槽位值；JSON 往返后也不靠 model_fields_set 推断继承。

坐标允许有限的画外位置，以表达有意裁切；布局可见性、文字溢出、遮挡和安全区域另作几何诊断及真实截图检查。所有浮点值必须有限，尺寸比例必须为正。画布和字号都有明确上下界。

文字、角色名称和选项均为字面原文。引号、反斜杠、方括号、花括号和类似代码内容由生成器按 Ren’Py 规则转义，不在模型中执行或擅自重写；原始换行及空白保留。

## 5 流程及诊断

`Flow(kind="next", target_frame_id="frame_2")` 只有单一后继；`Flow(kind="choices", choices=[...])` 至少有一个选择；`Flow(kind="end")` 无后继或选择。

`flow_targets(frame)` 返回有效后继 ID。`analyze_flow(project)` 以非递归算法返回 reachable、unreachable、ending_frames、without_exit、cycles。without_exit 表示从入口可达却无法到达任意 end 的帧。循环路径包含重复首尾 ID，以便向用户展示具体位置。

模型允许保存结构合法的不可达草稿或循环，但检查/发行 gate 必须报告这些诊断并执行明确验收策略。不得因模型校验通过而声称路线正确，也不得自动增加结局修复图。规模测试区分边/结局覆盖和全路径组合穷举。

## 6 事务、来源与重新导入边界

所有写操作需要 expected_revision 与 operation_id。过期 revision 返回冲突，不能覆盖较新内容。相同 operation_id、相同请求重试返回既有结果；相同 ID 携带不同请求必须拒绝。事务验证对象、所有引用及素材后才提交，一次批量应完整旧版本或完整新版本。

大导入在候选会话中先分配全部 ID、分块暂存，候选可以暂有未解析的前向引用；候选不作为有效 Project revision。全部组装后执行 Project.model_validate 与全图检查，再一次提交。模型本身不提供绕过引用检查的草稿标记。

来源定位保存原文件 hash、source_id、工作表/单元格或段落、原始幕帧标签。布局记录应说明来自 layout_decision，不能冒充 Excel 原文。原始资料、上次导入的规范化基线、当前模型、新资料都保留，重新导入逐字段做三方合并；两边不同地修改同一字段时返回具体冲突。幕帧排序变化不换 ID，无法唯一匹配身份时提供候选，不按模糊相似度静默覆盖。

Frame 模型不自动执行三方合并、版本迁移、检查点恢复或文件复制；这些属于事务和导入服务。生成脚本是派生产物，不提供任意人工 `.rpy` 双向合并。

## 7 路径与检查边界

Asset.path 必须是项目内、使用 `/` 的可迁移相对路径，可含中文及空格。绝对路径、驱动器、反斜杠、空路径段、`.`/`..`、Windows 设备名/保留字符、末尾点/空格均拒绝。

语法校验不能替代真实路径安全：存储层必须解析允许根目录和目标，检查符号链接/reparse point，防止实际越界。导入时核对文件存在、hash、媒体类型及解码；打包时确认字体、图片和音频均在包内。metadata/provenance 中的原始来源路径只是数据，不直接成为输出目标或引擎文件路径。

固定资料位于 `tests/fixtures/models/`；合法与无效样例、独立路线/人物期望及测试说明均已保存。`tests/test_models.py` 验证身份、引用、完整状态、PATCH、文本原样保存、路径、分支与 5000 帧非递归图检查；真实 SDK/声音/视觉/客户端验收由后续阶段执行并独立记录。

## 8 文件提取与三方导入纯函数

`sources.extract_file(path: Path)` 返回 format、source_path、hash、size_bytes、records、warnings；文本文件另外返回 encoding。调用者在读取前检查路径授权与根目录，提取器不登记素材、不猜幕帧归属、不执行材料指令。

- XLSX 记录工作表、坐标、行列、值、公式及所属合并区域，另外列出合并区域与锚点。公式仅读取缓存；缺缓存返回 FORMULA_WITHOUT_CACHE，不调用 Excel 或执行表达式。
- CSV/TSV 保存行值和实际源行范围；先用 utf-8-sig，失败后尝试 gb18030 并告警。CSV 允许逗号/分号/制表符探测；TSV 固定制表符。TXT/MD 保存原文段落、段落号与行范围，不把类似代码的文本当作代码。
- DOCX 提取普通正文段落和表格并保持 block 顺序；嵌入媒体、文本框、嵌套表格分别告警，不伪称已经完整读取这些内容。
- 不支持 XLS/PDF/OCR；SourceError(ValueError) 带 code 和 source_path，区分 UNSUPPORTED_FORMAT、SOURCE_UNREADABLE、SOURCE_INVALID、SOURCE_TOO_LARGE、EXTRACTOR_UNAVAILABLE。
- 初版提取边界为单文件 50 MiB、打包文档解压 250 MiB、表格范围 250,000 cells；超限报实际位置/原因。用于哈希与提取的字节来自同一次读取，避免文件并发变化导致来源不一致。

`reimport.compare_import(baseline, current, incoming)` 返回 `{merged, changes, conflicts}`。这是未提交计划；无布局字段的新来源保留已决定 layout，明确给出的 layout 则照常三方比较。字典按稳定 key，组件/选项等有稳定 ID 的列表按 ID 合并；新旧源重排不改变身份。两边不同地调整同一列表的相对顺序，生成 `$order` 冲突。非身份列表按完整字段比较。

冲突包含 path、baseline/current/incoming 及各自的 `*_exists`。删除与 null 分开；冲突默认保留 current，不能静默覆盖。路径对字典 key 使用 JSON Pointer 转义，身份列表使用稳定 ID 路径段，`$order` 表示列表顺序，因此不把该扩展路径当作通用 JSON Patch 索引。

`resolve_import(plan, {path: "source" | "current"})` 返回新的计划，可部分解决冲突；未知路径或其他选择拒绝。只有 conflicts 为空且候选完整 Project、素材和引用均验证通过，Store 才可提交。删除仍被引用的素材/目标不会被比较函数自动修复，引用安全交由完整 Project 验证。

比较和解决不会修改输入对象、读取文件、调用模型服务或提交项目。`tests/test_sources.py`、`tests/test_reimport.py` 以实际小文件和独立期望验证提取、编码、损坏文件、公式缓存、重排、局部聊天修改、冲突、删除及稳定 ID 列表。
