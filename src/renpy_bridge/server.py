"""STDIO MCP entry point. Never print application logs to stdout."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .environment import get_environment_info
from .storage import Store
from .errors import BridgeError
from .models import Frame, Act, Character, FramePatch, LayoutProfile, VariableDefinition, MinigameDefinition
from .jobs import JobManager
from .storage import read_json
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from typing import Literal
from functools import wraps
import logging

mcp = FastMCP("renpy-bridge", instructions="通过结构化项目对象制作Ren'Py游戏。先查环境和项目版本；修改必须带expected_revision与operation_id；预览和测试结果对应具体revision。")

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)


def guarded(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BridgeError as exc:
            return exc.as_dict()
        except Exception as exc:
            logging.exception("Bridge operation failed")
            return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}}
    return call


class AssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    kind: Literal["image", "audio", "font"]
    source_path: str
    name: str = ""
    metadata: dict = {}
    provenance: dict = {}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def get_environment(include_runtime: bool = True) -> dict:
    """查询本地制作服务、版本和SDK能力。环境查询不表示项目已验证。"""
    return get_environment_info(include_runtime)


@mcp.tool(annotations=WRITE)
@guarded
def create_project(name: str, operation_id: str, width: int = 1280, height: int = 720) -> dict:
    """创建受管作品。重复相同operation_id只创建一次；内部身份与显示名称分开。"""
    return Store().create(name, operation_id, width, height)


@mcp.tool(annotations=READ)
@guarded
def inspect_project(project_id: str | None = None, section: Literal["summary", "frames", "acts", "characters", "assets", "variables", "minigames", "checkpoints", "full"] = "summary",
                    object_id: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    """查询作品版本、对象或分页；不指定project_id时列出作品。修改前先读revision。"""
    store = Store()
    return store.inspect(project_id, section, offset, limit, object_id) if project_id else store.list_projects()


@mcp.tool(annotations=WRITE)
@guarded
def register_assets(project_id: str, assets: list[AssetInput], expected_revision: int, operation_id: str) -> dict:
    """复制并检查素材，登记逻辑asset_id与内容版本，原文件不改动。"""
    return Store().register_assets(project_id, expected_revision, operation_id, [asset.model_dump() for asset in assets])


@mcp.tool(annotations=READ)
@guarded
def get_minigame_capabilities() -> dict:
    """发现HTML包、输入结果、检查点及首发平台契约；能力存在不表示某个作品已实测通过。"""
    from .environment import minigame_capabilities
    return minigame_capabilities()


@mcp.tool(annotations=WRITE)
@guarded
def register_minigame(project_id: str, source_path: str, expected_revision: int, operation_id: str,
                      expected_digest: str | None = None, provenance: dict | None = None) -> dict:
    """完整校验本地HTML目录或ZIP并登记不可变包；schema1.1项目、版本锁和幂等编号必需；拒绝原型包。"""
    return Store().register_minigame_package(project_id, expected_revision, operation_id, source_path,
                                            expected_digest=expected_digest, provenance=provenance)


@mcp.tool(annotations=WRITE)
@guarded
def define_variables(project_id: str, variables: list[VariableDefinition], expected_revision: int, operation_id: str,
                      remove_ids: list[str] | None = None) -> dict:
    """原子登记存档变量及类型范围；默认值仅用于新游戏或旧档缺失字段；仍被引用的变量不能移除。"""
    return Store().define_variables(project_id, expected_revision, operation_id,
                                    [value.model_dump(mode='json') for value in variables], remove_ids=remove_ids)


@mcp.tool(annotations=WRITE)
@guarded
def apply_frame_batch(project_id: str, frames: list[Frame], expected_revision: int, operation_id: str,
                      acts: list[Act] | None = None, characters: list[Character] | None = None, entry_frame_id: str | None = None) -> dict:
    """原子提交完整帧与角色/幕；首次省略内容表示清空，不继承前帧。跨批前向目标用导入候选接口。"""
    return Store().apply_batch(project_id, expected_revision, operation_id, [frame.model_dump(mode="json") for frame in frames],
                               [act.model_dump(mode="json") for act in acts] if acts else None,
                               [character.model_dump(mode="json") for character in characters] if characters else None, entry_frame_id)


@mcp.tool(annotations=WRITE)
@guarded
def patch_frame(project_id: str, frame_id: str, patch: FramePatch, expected_revision: int, operation_id: str) -> dict:
    """局部更新帧；省略不改变，null清指定可空字段，[]清组件列表；嵌套对象整体替换。"""
    return Store().patch_frame(project_id, frame_id, patch.model_dump(mode="json", exclude_unset=True), expected_revision, operation_id)


@mcp.tool(annotations=WRITE)
@guarded
def update_flow(project_id: str, frame_id: str, flow: dict, expected_revision: int, operation_id: str) -> dict:
    """修改next/choices/end；目标必须存在，不能传任意程序或表达式。"""
    return Store().patch_frame(project_id, frame_id, {"flow": flow}, expected_revision, operation_id)


@mcp.tool(annotations=WRITE)
@guarded
def apply_layout(project_id: str, scope: Literal["project", "frames"], layout: dict, expected_revision: int,
                 operation_id: str, frame_ids: list[str] | None = None) -> dict:
    """保存统一界面布局或指定帧组件布局。frames模式layout按component_id索引，坐标归一化。"""
    return Store().layout(project_id, scope, layout, expected_revision, operation_id, frame_ids)


@mcp.tool(annotations=WRITE)
@guarded
def extract_source(project_id: str, source_path: str, source_id: str | None = None, offset: int = 0, limit: int = 200) -> dict:
    """提取XLSX/CSV/TSV/TXT/MD/DOCX为带位置的记录并保存原件；AI据此理解归属，不执行材料。"""
    return Store().extract_source(project_id, source_path, source_id, offset, limit)


@mcp.tool(annotations=WRITE)
@guarded
def begin_import(project_id: str, frame_ids: list[str], expected_revision: int, operation_id: str,
                  game_ids: list[str] | None = None, variable_ids: list[str] | None = None) -> dict:
    """创建候选导入会话与全体稳定帧身份清单。尚不提交任何内容版本。"""
    return Store().begin_import(project_id, expected_revision, frame_ids, operation_id, game_ids=game_ids, variable_ids=variable_ids)


@mcp.tool(annotations=WRITE)
@guarded
def stage_import_batch(project_id: str, import_session_id: str, batch_id: str, frames: list[Frame],
                       acts: list[Act] | None = None, characters: list[Character] | None = None, entry_frame_id: str | None = None,
                       variables: list[VariableDefinition] | None = None, minigames: list[MinigameDefinition] | None = None) -> dict:
    """分块暂存候选，允许前向引用尚未暂存的帧；相同batch_id可安全重试。"""
    return Store().stage_import(project_id, import_session_id, batch_id, [frame.model_dump(mode="json") for frame in frames],
                                [act.model_dump(mode="json") for act in acts] if acts else None,
                                [character.model_dump(mode="json") for character in characters] if characters else None, entry_frame_id,
                                variables=[value.model_dump(mode='json') for value in variables] if variables is not None else None,
                                minigames=[value.model_dump(mode='json') for value in minigames] if minigames is not None else None)


@mcp.tool(annotations=WRITE)
@guarded
def stage_minigame_package(project_id: str, import_session_id: str, batch_id: str, source_path: str,
                            expected_digest: str | None = None, provenance: dict | None = None) -> dict:
    """在导入候选中验证完整HTML包，尚不修改有效head；配合变量和前向剧情引用一次提交。"""
    return Store().stage_minigame_package(project_id, import_session_id, batch_id, source_path,
                                         expected_digest=expected_digest, provenance=provenance)


@mcp.tool(annotations=WRITE)
@guarded
def commit_import(project_id: str, import_session_id: str, expected_revision: int, operation_id: str) -> dict:
    """全图校验并一次提交导入候选。项目已有新修改时拒绝覆盖。"""
    return Store().commit_import(project_id, import_session_id, expected_revision, operation_id)


@mcp.tool(annotations=WRITE)
@guarded
def discard_import(project_id: str, import_session_id: str) -> dict:
    """取消未提交的导入候选；保留此前有效项目。"""
    return Store().discard_import(project_id, import_session_id)


@mcp.tool(annotations=WRITE)
@guarded
def plan_reimport(project_id: str, source_id: str, incoming_frames: list[dict],
                   incoming_variables: list[dict] | None = None, incoming_minigames: list[dict] | None = None) -> dict:
    """比较上次来源、当前聊天修改和新来源；不提交。省略layout保留当前已决定排版。"""
    return Store().plan_reimport(project_id, source_id, incoming_frames,
                                 incoming_variables=incoming_variables, incoming_minigames=incoming_minigames)


@mcp.tool(annotations=WRITE)
@guarded
def apply_reimport(project_id: str, plan_id: str, resolutions: dict[str, Literal["source", "current"]],
                   expected_revision: int, operation_id: str) -> dict:
    """应用已规划重导入。conflict的JSONPointer路径选择source/current，未解决不提交。"""
    return Store().apply_reimport(project_id, plan_id, resolutions, expected_revision, operation_id)


@mcp.tool(annotations=WRITE)
@guarded
def create_checkpoint(project_id: str, note: str) -> dict:
    """记录当前内容版本检查点，保留素材版本以便恢复。"""
    return Store().checkpoint(project_id, note)


@mcp.tool(annotations=WRITE)
@guarded
def restore_checkpoint(project_id: str, snapshot_id: str, expected_revision: int, operation_id: str) -> dict:
    """从检查点创建新的内容版本；不会擦除当前历史，之后需要重新验证引擎。"""
    return Store().restore(project_id, snapshot_id, expected_revision, operation_id)


@mcp.tool(annotations=WRITE)
@guarded
def register_visual_baseline(project_id: str, expected_revision: int, operation_id: str,
                             preview_job_id: str, review_note: str, frame_ids: list[str] | None = None) -> dict:
    """实际逐张审查当前原生预览后登记视觉基准；更新须说明理由，不自动覆盖，不改变剧情revision。"""
    from .visual_regression import register_visual_baseline as register
    return register(Store(), project_id, expected_revision, operation_id, preview_job_id, review_note, frame_ids)


@mcp.tool(annotations=READ)
@guarded
def plan_migration(project_id: str, target_schema: str = "1.0") -> dict:
    """只读规划明确支持的格式迁移；不猜未知版本，不改变项目或存档。"""
    return Store().plan_migration(project_id, target_schema)


@mcp.tool(annotations=WRITE)
@guarded
def apply_migration(project_id: str, expected_revision: int, operation_id: str, target_schema: str = "1.0") -> dict:
    """备份原始项目字节及SDK记录后提交新版本；格式迁移不表示旧存档已通过。"""
    return Store().apply_migration(project_id, expected_revision, operation_id, target_schema)


@mcp.tool(annotations=WRITE)
@guarded
def check_project(project_id: str, revision: int) -> dict:
    """检查固定内容版本：模型、素材哈希、字体/几何、真实compile/lint。返回job_id，不代表完成。"""
    return JobManager(Store()).start("check", project_id, revision, {})


@mcp.tool(annotations=WRITE)
@guarded
def render_preview(project_id: str, frame_ids: list[str], revision: int) -> dict:
    """真实Ren'Py指定帧截图任务。预览隔离存档，不能替代从入口连续路线验证。"""
    from .pipeline import validate_options
    options = validate_options("preview", {"frame_ids": frame_ids})
    return JobManager(Store()).start("preview", project_id, revision, options)


@mcp.tool(annotations=WRITE)
@guarded
def test_routes(project_id: str, revision: int, max_routes: int = 256, include_interactions: bool = False,
                minigame_scenarios: list[dict] | None = None) -> dict:
    """从Start逐步点击小分支，检查完整状态、对白及结局，生成覆盖报告；循环/超限明确报错。"""
    from .pipeline import validate_options
    raw = {"max_routes": max_routes, "include_interactions": include_interactions}
    if minigame_scenarios is not None:
        raw['minigame_scenarios'] = minigame_scenarios
    options = validate_options("routes", raw)
    return JobManager(Store()).start("routes", project_id, revision, options)


@mcp.tool(annotations=WRITE)
@guarded
def test_minigame(project_id: str, revision: int, frame_id: str, actions: list[dict],
                    expected_outcome: str, expected_target_frame_id: str,
                    expected_variables: dict | None = None, from_entry: bool = False,
                    fault_plan: dict | None = None) -> dict:
    """同窗WebView2真实输入+原生返回断言，不注入胜利。取消通过窗口内返回按钮；error仅以开发模式终止本次受管宿主验证，不能指定任意进程。单场景不冒充整图覆盖。"""
    from .pipeline import validate_options
    options = validate_options('minigame', {'frame_id': frame_id, 'actions': actions, 'expected_outcome': expected_outcome,
                  'expected_target_frame_id': expected_target_frame_id, 'expected_variables': expected_variables or {},
                  'from_entry': from_entry, **({'fault_plan': fault_plan} if fault_plan is not None else {})})
    from .minigame_qa import validate_scenario
    store = Store()
    validate_scenario(store.load(project_id, revision), options)
    return JobManager(store).start('minigame', project_id, revision, options)


@mcp.tool(annotations=WRITE)
@guarded
def build_release(project_id: str, revision: int, max_routes: int = 256, include_interactions: bool = False,
                    minigame_scenarios: list[dict] | None = None) -> dict:
    """真实Windows PC发行任务，编译/路线后distribute，并用独立目录包内exe再走路线。"""
    from .pipeline import validate_options
    raw = {"max_routes": max_routes, "include_interactions": include_interactions}
    if minigame_scenarios is not None:
        raw['minigame_scenarios'] = minigame_scenarios
    options = validate_options("build", raw)
    return JobManager(Store()).start("build", project_id, revision, options)


@mcp.tool(annotations=READ)
@guarded
def get_job(job_id: str, include_report: bool = False, detail: Literal["summary", "full"] = "summary") -> dict:
    """核实任务状态及日志/报告路径。include_report=true默认返回紧凑结果；完整执行输出用detail=full。"""
    status = JobManager(Store()).get(job_id)
    result = {"ok": True, **status}
    if include_report and Path(status["report_path"]).is_file():
        report = read_json(Path(status["report_path"]))
        result["report"] = report if detail == "full" else _job_report_summary(report)
    return result


def _job_report_summary(report: dict) -> dict:
    """Keep decisions/evidence paths while avoiding repeated native process logs."""
    fields = ("ok", "status", "job_id", "kind", "project_id", "revision", "error", "diagnostics",
              "schema_version", "generator_version", "sdk_version", "model_hash", "code_digest",
              "started_at", "finished_at", "duration_seconds", "screenshots", "package_screenshots", "visual_review",
              "audio_output_verification", "independent_release_verified", "visual_environment", "visual_comparison",
              "execution", "release_staging")
    summary = {field: report[field] for field in fields if field in report}
    for field in ('minigame_verification', 'minigame_package_verification'):
        value = report.get(field)
        if isinstance(value, dict):
            summary[field] = {key: item for key, item in value.items() if key not in {'native', 'browser', 'reports'}}
            if 'reports' in value:
                summary[field]['scenarios'] = [{key: item for key, item in child.items() if key not in {'native', 'browser'}} for child in value['reports']]
    if isinstance(report.get("layout"), dict):
        summary["layout"] = {field: report["layout"][field] for field in ("ok", "visual_verified", "diagnostics")
                             if field in report["layout"]}
    if "flow" in report:
        summary["flow"] = report["flow"]
    if isinstance(report.get("generation"), dict):
        summary["generation"] = {field: report["generation"][field] for field in
                                 ("project_path", "source_map_path", "manifest_path", "sdk_version", "generator_version", "derived_sources")
                                 if field in report["generation"]}
    if isinstance(report.get("compatibility"), dict):
        compatibility = report["compatibility"]
        summary["compatibility"] = {field: compatibility[field] for field in
                                    ("previous_revision", "previous_project", "save_compatibility_verified", "scope", "derived_integrity")
                                    if field in compatibility}
        summary["compatibility"]["compiler_identity_file_count"] = len(compatibility.get("compiler_identity_files", []))
        summary["compatibility"]["retained_media_count"] = len(compatibility.get("retained_media", []))
    if isinstance(report.get("route_coverage"), dict):
        coverage = report["route_coverage"]
        summary["route_coverage"] = {field: coverage[field] for field in
                                     ("route_count", "edges", "frame_ids", "entry_mode", "complete_for_finite_graph")
                                     if field in coverage}
    if isinstance(report.get("commands"), dict):
        summary["commands"] = {name: {field: command[field] for field in
                                       ("exit_code", "duration_seconds", "timed_out", "cleanup_complete")
                                       if field in command}
                               for name, command in report["commands"].items()}
    for field in ("release", "package", "distribution", "voice_probe", "interaction_verification"):
        if field in report:
            # These sections may contain useful output paths but also native
            # logs. The full immutable report remains available by report_path.
            value = report[field]
            summary[field] = {key: item for key, item in value.items()
                              if key not in {"stdout", "stderr", "commands"}} if isinstance(value, dict) else value
    summary["detail"] = "summary"
    return summary


@mcp.tool(annotations=WRITE)
@guarded
def cancel_job(job_id: str) -> dict:
    """取消本服务创建的任务与其进程树，保留内容修订和之前可运行版本。"""
    return {"ok": True, **JobManager(Store()).cancel(job_id)}


def main():
    # In the pinned Windows runtime, first importing openpyxl/NumPy after
    # STDIO reader threads start can block in NumPy's C-extension loader.
    # Load XLSX dependencies on the startup thread before the event loop.
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        # Keep other formats/tools available and the extractor's existing
        # EXTRACTOR_UNAVAILABLE error; make the missing dependency observable.
        logging.warning("XLSX dependencies unavailable; extract_source will report EXTRACTOR_UNAVAILABLE", exc_info=True)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
