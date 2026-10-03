"""Generate a native Ren'Py project from the validated authoring model."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any

from .models import Project
from .sdk import SdkInfo, run_sdk
from .media import ALLOWED_EXTENSIONS, validate_media
from .errors import BridgeError

GENERATOR_VERSION = "0.1.3"
MINIGAME_GENERATOR_VERSION = "1.0"


class GenerationError(RuntimeError):
    """The authoring data cannot produce a complete native project."""


def _asset_game_paths(project: Project, sources: dict[str, Path], target: Path) -> dict[str, str]:
    """Keep native Windows file paths short without truncating asset identity."""
    paths, identities = {}, {}
    for aid, source in sources.items():
        sha256 = project.assets[aid].sha256
        relative = f"bridge_assets/{sha256[:24]}{source.suffix.lower()}"
        if relative in identities and identities[relative] != sha256:
            raise GenerationError(f"ASSET_PATH_COLLISION: truncated filename collision for {aid}")
        identities[relative] = sha256
        # Ren'Py native loaders do not inherit CPython's long-path behavior.
        if len(str(target / "game" / relative)) >= 260:
            raise GenerationError("ENGINE_PATH_TOO_LONG: choose a shorter bridge home or project output directory")
        paths[aid] = relative
    return paths


def stable_label(frame_id: str) -> str:
    """Keep labels stable across text, asset, act-order and layout edits."""
    return "rb_frame_" + hashlib.sha256(frame_id.encode("utf-8")).hexdigest()[:24]


def _identifier(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _string(value: str) -> str:
    """Quote a literal for Python/Ren'Py syntax, without shell involvement."""
    return json.dumps(value, ensure_ascii=False)


def _display_text(value: str, *, escape_percent: bool = False) -> str:
    # Pinned 8.5.3 treats the white lenticular bracket 〖 as plain text.
    # Doubling it based on newer online docs visibly duplicated the character
    # in the real 8.5.3 renderer. Keep SDK-version-specific behavior explicit.
    escaped = value.replace("[", "[[").replace("{", "{{")
    return escaped.replace("%", "%%") if escape_percent else escaped


def literal_text(value: str) -> str:
    """Escape text interpolation and tags before quoting Ren'Py display text.

    Authoring text is plain text. Formatting is never inferred as executable
    interpolation or Ren'Py text tags. Newlines, quotes and backslashes are
    subsequently encoded as part of the script string literal.
    """
    # Ren'Py's native string lexer differs from Python/JSON: e.g. \t becomes
    # the letter t, not a tab. Encode controls explicitly as Unicode escapes.
    # Its \-space escape preserves authored adjacent spaces.
    escapes = {"\\": "\\\\", '"': '\\"', " ": "\\ ", "\n": "\\n"}
    pieces = []
    for character in _display_text(value, escape_percent=True):
        if character in escapes:
            pieces.append(escapes[character])
        elif ord(character) < 32:
            pieces.append(f"\\u{ord(character):04x}")
        else:
            pieces.append(character)
    return '"' + "".join(pieces) + '"'


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _minigame_inputs(project: Project, authoring_root: Path, *, allow_prototype: bool) -> dict:
    """Revalidate registered immutable packages before generating any project."""
    from .minigame_packages import validate_directory
    games = {}
    referenced = {frame.flow.game_id for frame in project.frames.values() if frame.flow.kind == "minigame"}
    for game_id in sorted(referenced):
        definition = project.minigames[game_id]
        source = authoring_root / definition.path
        if not source.resolve().is_relative_to(authoring_root):
            raise GenerationError(f"MINIGAME_PATH_INVALID: package escapes authoring root: {game_id}")
        try:
            info = validate_directory(source, allow_prototype=allow_prototype)
        except BridgeError as error:
            raise GenerationError(f"{error.code}: invalid registered minigame {game_id}: {error}") from error
        if (info.digest != definition.package_digest or info.manifest != definition.manifest
                or info.manifest["game_id"] != game_id or info.manifest["version"] != definition.package_version):
            raise GenerationError(f"MINIGAME_PACKAGE_CHANGED: registered package identity changed: {game_id}")
        games[game_id] = info
    return games


def _host_inventory(root: Path, *, required_file: str = "HtmlMinigameEmbeddedHost.exe") -> dict[str, dict]:
    """Inventory an owned runtime tree without following links or junctions."""
    root = Path(root).absolute()
    for ancestor in reversed((root, *root.parents)):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or not stat.S_ISDIR(info.st_mode):
            raise GenerationError(f"MINIGAME_RUNTIME_PATH_INVALID: runtime contains unsafe ancestor: {ancestor}")
    inventory, folded = {}, set()
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(current) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise GenerationError(f"MINIGAME_RUNTIME_PATH_INVALID: runtime contains link or special file: {relative}")
            if relative.casefold() in folded:
                raise GenerationError(f"MINIGAME_RUNTIME_PATH_INVALID: runtime case collision: {relative}")
            folded.add(relative.casefold())
            if stat.S_ISREG(info.st_mode):
                inventory[relative] = {"sha256": _hash_file(path), "bytes": info.st_size}
    if required_file not in inventory:
        raise GenerationError(f"MINIGAME_RUNTIME_MISSING: {required_file} is absent from the selected runtime")
    return dict(sorted(inventory.items()))


def _install_minigames(project: Project, target: Path, games: dict, host_root: Path,
                       host_files: dict, *, allow_prototype: bool, fixed_runtime_root: Path | None = None,
                       fixed_runtime_files: dict | None = None, fixed_runtime_version: str | None = None,
                       development: bool = False) -> dict:
    """Copy every declared package/runtime file; verify both source and copy."""
    from .minigame_packages import validate_directory
    from .minigame_runtime import RPY_RUNTIME_SOURCE, runtime_source_files
    installed = {}
    for game_id, info in games.items():
        relative_root = f"game/bridge_minigames/{info.digest}"
        destination = target / relative_root
        for item in info.files:
            output = destination / item.path
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(info.root / item.path, output)
        copied = validate_directory(destination, allow_prototype=allow_prototype)
        current = validate_directory(info.root, allow_prototype=allow_prototype)
        if copied.digest != info.digest or current.digest != info.digest:
            raise GenerationError(f"MINIGAME_PACKAGE_CHANGED: package changed during generation: {game_id}")
        installed[game_id] = {"game_id": game_id, "package_digest": info.digest,
            "package_version": info.manifest["version"], "package_root": relative_root,
            "manifest": info.manifest,
            "files": {item.path: {"sha256": item.sha256, "bytes": item.bytes} for item in info.files}}
    host_relative = "html-runtime/win-x64"
    for relative, expected in host_files.items():
        source = host_root / relative
        output = target / host_relative / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        if _hash_file(output) != expected["sha256"] or output.stat().st_size != expected["bytes"]:
            raise GenerationError(f"MINIGAME_RUNTIME_CHANGED: runtime changed during copy: {relative}")
    if _host_inventory(host_root) != host_files or _host_inventory(target / host_relative) != host_files:
        raise GenerationError("MINIGAME_RUNTIME_CHANGED: runtime tree changed during generation")
    fixed_runtime = None
    if fixed_runtime_root is not None:
        browser_relative = "html-runtime/webview2-fixed"
        for relative, expected in fixed_runtime_files.items():
            output = target / browser_relative / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixed_runtime_root / relative, output)
            if _hash_file(output) != expected["sha256"] or output.stat().st_size != expected["bytes"]:
                raise GenerationError(f"MINIGAME_RUNTIME_CHANGED: fixed browser changed during copy: {relative}")
        if (_host_inventory(fixed_runtime_root, required_file="msedgewebview2.exe") != fixed_runtime_files
                or _host_inventory(target / browser_relative, required_file="msedgewebview2.exe") != fixed_runtime_files):
            raise GenerationError("MINIGAME_RUNTIME_CHANGED: fixed browser tree changed during generation")
        fixed_runtime = {"relative_root": browser_relative, "version": fixed_runtime_version, "files": fixed_runtime_files}
    for relative, source in runtime_source_files().items():
        relative = relative if relative.startswith("game/") else "game/" + relative
        output = target / relative
        if not output.resolve().is_relative_to(target) or not relative.startswith("game/python-packages/renpy_bridge_minigames/"):
            raise GenerationError("MINIGAME_RUNTIME_PATH_INVALID: runtime source escaped the agreed module directory")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(source, encoding="utf-8")
    defaults = {key: value.default for key, value in sorted(project.variables.items())}
    (target / "game/bridge_minigame_runtime.rpy").write_text(
        "default rb_story_variables = " + repr(defaults) + "\n\n" + RPY_RUNTIME_SOURCE, encoding="utf-8")
    instances = {}
    for frame_id, frame in sorted(project.frames.items()):
        if frame.flow.kind == "minigame":
            instances[frame.flow.instance_id] = {**frame.flow.model_dump(mode="json", exclude_none=True), "frame_id": frame_id}
    configuration = {"schema_version": "1.0", "project_id": project.project_id, "revision": project.revision,
        "save_namespace": project.save_namespace, "developer": bool(development),
        "host": {"relative_executable": host_relative + "/HtmlMinigameEmbeddedHost.exe",
                 "sha256": host_files["HtmlMinigameEmbeddedHost.exe"]["sha256"], "files": host_files,
                 "window_mode": "embedded_same_top_level_window", "footer_fraction": 0.1},
        "minigames": installed,
        "variables": {key: value.model_dump(mode="json") for key, value in sorted(project.variables.items())},
        "instances": instances, "frame_labels": {key: stable_label(key) for key in project.frames}}
    if fixed_runtime is not None:
        configuration["fixed_runtime"] = fixed_runtime
    (target / "game/bridge-minigames.json").write_text(json.dumps(configuration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"configuration_path": "game/bridge-minigames.json", "minigames": installed,
        "html_runtime": {"path": host_relative, "files": host_files}, "fixed_runtime": fixed_runtime,
        "managed_roots": ["game/bridge_minigames", "html-runtime", "game/python-packages/renpy_bridge_minigames", "game/bridge-minigames.json"]}


def _set_define(path: Path, variable: str, expression: str) -> None:
    source = path.read_text(encoding="utf-8-sig")
    updated, count = re.subn(rf"(?m)^define {re.escape(variable)}\s*=.*$",
                             lambda match: f"define {variable} = {expression}", source)
    if count != 1:
        raise GenerationError(f"SDK template must define {variable} exactly once; found {count}")
    path.write_text(updated, encoding="utf-8")


def complete_native_gui(sdk: SdkInfo, game: Path) -> list[str]:
    """Supply static GUI files that generate_gui does not itself copy."""
    template_gui = sdk.root / "gui" / "game" / "gui"
    copied = []
    for source in sorted(template_gui.rglob("*")):
        if source.is_file():
            relative = source.relative_to(template_gui)
            destination = game / "gui" / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                copied.append(relative.as_posix())
    return copied


def _displayable(component: Any, project: Project, paths: dict[str, str]) -> str:
    layout = component.layout
    width = round(project.canvas.width * layout.width) if layout.width is not None else None
    height = round(project.canvas.height * layout.height) if layout.height is not None else None
    properties = [f"xpos={float(layout.x)!r}", f"ypos={float(layout.y)!r}",
                  f"xanchor={float(layout.anchor_x)!r}", f"yanchor={float(layout.anchor_y)!r}"]
    if component.kind == "text":
        text_props = [f"font=gui.text_font", f"size={component.font_size}", f"color={_string(component.color)}"]
        if width is not None:
            text_props.append(f"xmaximum={width}")
        if height is not None:
            text_props.append(f"ymaximum={height}")
        # Python expression strings preserve whitespace without the script
        # lexer's \-space escape. Make interpolation explicitly active so its
        # doubled brackets are converted back to literal brackets exactly once.
        child = f"Text({_string(_display_text(component.text or ''))}, substitute=True, {', '.join(text_props)})"
    else:
        if component.asset_id not in paths:
            raise GenerationError(f"Image component has no copied asset: {component.component_id}")
        child = f"Image({_string(paths[component.asset_id])})"
        if width is not None:
            properties.append(f"xsize={width}")
        if height is not None:
            properties.append(f"ysize={height}")
        if width is not None or height is not None:
            properties.append(f"fit={_string(layout.fit)}")
    return f"Transform({child}, {', '.join(properties)})"


def _build_story(project: Project, paths: dict[str, str]) -> tuple[str, dict]:
    lines: list[str] = ["# Generated by renpy-bridge. Edit the authoring project, not this file."]
    frame_map: dict[str, dict] = {}
    char_names = {cid: _identifier("rb_character_", cid) for cid in project.characters}
    for cid, character in sorted(project.characters.items()):
        lines.append(f"define {char_names[cid]} = Character({_string(_display_text(character.name))}, who_color={_string(character.color)})")
    lines.extend(["", "default rb_current_frame_id = None", "", "label start:"])
    if project.entry_frame_id is None:
        lines.append("    return")
    else:
        lines.append(f"    jump {stable_label(project.entry_frame_id)}")
    max_sfx = max((len(frame.sfx) for frame in project.frames.values()), default=0)
    for fid, frame in sorted(project.frames.items()):
        lines.extend(["", f"label {stable_label(fid)}:"])
        start_line = len(lines)
        lines.append(f"    $ rb_current_frame_id = {_string(fid)}")
        # A silent, non-choice interlude is a clean composition. Restore the
        # native controls explicitly on the next dialogue/menu interaction.
        lines.append(f"    $ quick_menu = {frame.dialogue is not None or frame.flow.kind == 'choices'}")
        # Applying each complete frame after clearing our layers eliminates
        # predecessor-dependent visual state at joins, load and rollback.
        for layer in ["rb_background", "rb_characters", "rb_decorations"]:
            lines.append(f"    scene onlayer {layer}")
        groups = [("rb_background", [frame.bg] if frame.bg else []),
                  ("rb_characters", frame.characters), ("rb_decorations", frame.decorations)]
        for layer, components in groups:
            for component in sorted(components, key=lambda item: item.zorder):
                expression = _displayable(component, project, paths)
                tag = _identifier("rb_component_", component.component_id)
                lines.append(f"    show expression {expression} as {tag} onlayer {layer} zorder {component.zorder}")
        if frame.bgm is None:
            lines.append("    stop music")
        else:
            lines.append(f"    play music {_string(paths[frame.bgm])} if_changed")
        for index in range(max_sfx):
            lines.append(f"    stop rb_sfx_{index}")
        for index, asset_id in enumerate(frame.sfx):
            lines.append(f"    play rb_sfx_{index} {_string(paths[asset_id])}")
        if frame.transition.kind == "dissolve":
            lines.append(f"    with Dissolve({frame.transition.duration!r})")
        elif frame.transition.kind == "fade":
            half = frame.transition.duration / 2
            lines.append(f"    with Fade({half!r}, 0.0, {half!r})")
        if frame.voice is None:
            lines.append("    voice None")
        else:
            lines.append(f"    voice {_string(paths[frame.voice])}")
        if frame.dialogue is None:
            dialogue = None
            lines.append("    window hide")
        else:
            speaker = char_names.get(frame.dialogue.speaker_id, "narrator")
            dialogue = f"{speaker} {literal_text(frame.dialogue.text)}"
            lines.append("    window auto")
        choices = []
        if frame.flow.kind == "choices":
            lines.append("    menu:")
            interaction_line = len(lines)
            dialogue_line = None
            if frame.dialogue is not None and frame.dialogue.text:
                # A native menu caption keeps dialogue and all choices in one
                # interaction, without an extra click to reveal the choices.
                dialogue_line = len(lines) + 1
                lines.append(f"        {dialogue}")
            for choice in frame.flow.choices:
                choice_line = len(lines) + 1
                lines.append(f"        {literal_text(choice.text)}:")
                lines.append(f"            jump {stable_label(choice.target_frame_id)}")
                choices.append({"choice_id": choice.choice_id, "line": choice_line,
                                "target_frame_id": choice.target_frame_id})
        elif frame.flow.kind == "minigame":
            dialogue_line = len(lines) + 1 if dialogue is not None else None
            if dialogue is not None:
                lines.append(f"    {dialogue}")
            phase_label = _identifier("rb_minigame_", fid)
            lines.append(f"label {phase_label}_prepare:")
            interaction_line = len(lines) + 1
            lines.append(f"    call rb_minigame_run({_string(frame.flow.instance_id)}) from {phase_label}_return")
            lines.append("    jump expression _return")
        else:
            interaction_line = len(lines) + 1
            dialogue_line = interaction_line if dialogue is not None else None
            # A pure visual frame waits through the engine's native pause
            # interaction. Explicit hiding prevents inherited say text/window.
            lines.append(f"    {dialogue}" if dialogue is not None else "    pause")
            if frame.flow.kind == "next":
                lines.append(f"    jump {stable_label(frame.flow.target_frame_id)}")
            elif frame.flow.kind == "end":
                lines.append("    return")
            else:
                raise GenerationError(f"Unsupported native flow: {frame.flow.kind}")
        frame_map[fid] = {"frame_id": fid, "act_id": frame.act_id,
                          "source_label": frame.source_label, "label": stable_label(fid),
                          "file": "game/script.rpy", "start_line": start_line,
                          "end_line": len(lines), "dialogue_line": dialogue_line,
                          "interaction_line": interaction_line,
                          "choices": choices}
        if frame.flow.kind == "minigame":
            frame_map[fid]["minigame"] = {"game_id": frame.flow.game_id, "instance_id": frame.flow.instance_id,
                "prepare_label": phase_label + "_prepare", "return_label": phase_label + "_return",
                "runtime_phases": ["rb_minigame_run", "rb_minigame_entry_save", "rb_minigame_wait",
                    "rb_minigame_checkpoint", "rb_minigame_checkpoint_save", "rb_minigame_commit",
                    "rb_minigame_finish_save", "rb_minigame_menu_phase", "rb_minigame_done"]}
    return "\n".join(lines) + "\n", frame_map


def generate_project(project: Project, authoring_root: Path, target: Path, sdk: SdkInfo, *,
                     minigame_host_root: Path | None = None, minigame_fixed_runtime_root: Path | None = None,
                     allow_prototype_minigames: bool = False, minigame_development: bool = False,
                     defer_minigame_integrity: bool = False) -> dict:
    """Generate into a new directory, returning paths and version evidence.

    Compilation, lint, execution and release packaging are separate job stages.
    Asset hashes and containment are checked before any project files are made.
    """
    project = Project.model_validate(project.model_dump())
    if sdk.version != "8.5.3" and not sdk.version.startswith("8.5.3."):
        raise GenerationError(f"This generator is verified for Ren'Py 8.5.3, not {sdk.version}; probe the new SDK before enabling it")
    authoring_root = Path(authoring_root).resolve()
    target = Path(target).resolve()
    if target.exists():
        raise FileExistsError(f"Generation target must be a new directory: {target}")
    uses_minigames = any(frame.flow.kind == "minigame" for frame in project.frames.values())
    games = _minigame_inputs(project, authoring_root, allow_prototype=allow_prototype_minigames) if uses_minigames else {}
    host_root = None
    host_files = {}
    fixed_root, fixed_files, fixed_version = None, {}, None
    if uses_minigames:
        host_root = Path(minigame_host_root) if minigame_host_root is not None else sdk.root.parent / "html-minigame-embedded-host/win-x64"
        try:
            host_files = _host_inventory(host_root)
        except OSError as error:
            raise GenerationError(f"MINIGAME_RUNTIME_MISSING: selected HTML host runtime is unavailable: {host_root}") from error
        if minigame_fixed_runtime_root is None:
            candidates = list(sdk.root.parent.glob("webview2-fixed-*/Microsoft.WebView2.FixedVersionRuntime.*.x64"))
            if len(candidates) != 1:
                raise GenerationError("MINIGAME_RUNTIME_MISSING: select exactly one fixed WebView2 directory with minigame_fixed_runtime_root")
            fixed_root = candidates[0]
        else:
            fixed_root = Path(minigame_fixed_runtime_root)
        try:
            fixed_files = _host_inventory(fixed_root, required_file="msedgewebview2.exe")
            lock = fixed_root.parent / "runtime-lock.json"
            locked = json.loads(lock.read_text(encoding="utf-8-sig")) if lock.is_file() else {}
            fixed_version = locked.get("version")
            if not isinstance(fixed_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", fixed_version):
                match = re.search(r"FixedVersionRuntime\.(\d+\.\d+\.\d+\.\d+)\.x64", fixed_root.name)
                fixed_version = match.group(1) if match else None
            if fixed_version is None or locked.get("architecture", "x64") != "x64":
                raise GenerationError("MINIGAME_RUNTIME_MISSING: fixed browser version/architecture is not declared")
            if "exe_sha256" in locked and locked["exe_sha256"] != fixed_files["msedgewebview2.exe"]["sha256"]:
                raise GenerationError("MINIGAME_RUNTIME_CHANGED: fixed browser executable differs from its lock")
        except OSError as error:
            raise GenerationError(f"MINIGAME_RUNTIME_MISSING: fixed browser directory is unavailable: {fixed_root}") from error
    sources: dict[str, Path] = {}
    paths: dict[str, str] = {}
    for aid, asset in project.assets.items():
        source = (authoring_root / asset.path).resolve()
        if not source.is_relative_to(authoring_root) or not source.is_file():
            raise GenerationError(f"Asset is missing or outside the authoring root: {aid}")
        if source.suffix.lower() not in ALLOWED_EXTENSIONS.get(asset.kind, set()):
            raise GenerationError(f"Unsupported {asset.kind} file extension for {aid}: {source.suffix}")
        if _hash_file(source) != asset.sha256:
            raise GenerationError(f"Asset SHA256 mismatch: {aid}")
        try:
            validate_media(asset.kind, source)
        except BridgeError as exc:
            raise GenerationError(f"{exc.code}: invalid media {aid}: {exc}") from exc
        sources[aid] = source
    paths = _asset_game_paths(project, sources, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    state = target.parent / (".rb-sdk-state-" + target.name)
    private_saves = state / "saves"
    private_multi = state / "multipersistent"
    private_saves.mkdir(parents=True, exist_ok=True)
    private_multi.mkdir(parents=True, exist_ok=True)
    isolated = {"RENPY_MULTIPERSISTENT": str(private_multi)}
    generation = run_sdk(sdk, "launcher", "generate_gui", [str(target), "--start",
                         "--width", str(project.canvas.width), "--height", str(project.canvas.height),
                         "--language", "schinese", "--template", str(sdk.root / "gui"),
                         "--savedir", str(private_saves)],
                         timeout_seconds=120, env=isolated)
    if generation.exit_code != 0 or generation.timed_out:
        raise GenerationError(f"Native GUI generation failed: {generation.stderr or generation.stdout}")
    game = target / "game"
    copied_gui = complete_native_gui(sdk, game)
    font = game / "SourceHanSansLite.ttf"
    if not font.is_file():
        raise GenerationError("Chinese SDK font was not copied into the native project")
    for aid, source in sources.items():
        destination = game / paths[aid]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source, destination)
        if _hash_file(destination) != project.assets[aid].sha256:
            raise GenerationError(f"Asset changed during generation: {aid}")
    _set_define(game / "options.rpy", "config.name", f"_({_string(_display_text(project.name))})")
    _set_define(game / "options.rpy", "config.save_directory", _string(project.save_namespace))
    build_name = _identifier("rbgame_", project.project_id)
    _set_define(game / "options.rpy", "build.name", _string(build_name))
    _set_define(game / "gui.rpy", "gui.text_size", str(project.layout.font_size))
    _set_define(game / "gui.rpy", "gui.textbox_height", str(round(project.canvas.height * project.layout.dialogue_height)))
    _set_define(game / "gui.rpy", "gui.dialogue_xpos", str(round(project.canvas.width * project.layout.safe_margin)))
    _set_define(game / "gui.rpy", "gui.dialogue_width", str(round(project.canvas.width * (1 - 2 * project.layout.safe_margin))))
    max_sfx = max((len(frame.sfx) for frame in project.frames.values()), default=0)
    runtime = ["# Native Ren'Py integration; no simulated say/choice controls.",
               "init -20 python:",
               "    for rb_layer in ('rb_background', 'rb_characters', 'rb_decorations'):",
               "        if rb_layer not in config.layers:",
               "            config.layers.insert(config.layers.index('transient'), rb_layer)"]
    for index in range(max_sfx):
        runtime.append(f"    renpy.music.register_channel('rb_sfx_{index}', 'sfx', loop=False)")
    runtime.extend(["", "define config.extra_savedirs = []", "", "init python:",
                    "    build.classify('bridge-*.json', None)",
                    "    build.classify('bridge-*.log', None)"])
    if uses_minigames:
        runtime.extend(["    build.classify('game/bridge-minigames.json', 'all')",
                        "    build.classify('game/bridge_minigames/**', 'all')",
                        "    build.classify('html-runtime/**', 'all')",
                        "    build.classify('game/python-packages/renpy_bridge_minigames/**', 'all')"])
    (game / "bridge_runtime.rpy").write_text("\n".join(runtime) + "\n", encoding="utf-8")
    story, frames = _build_story(project, paths)
    (game / "script.rpy").write_text(story, encoding="utf-8")
    minigame_installation = _install_minigames(project, target, games, host_root, host_files,
        allow_prototype=allow_prototype_minigames, fixed_runtime_root=fixed_root, fixed_runtime_files=fixed_files,
        fixed_runtime_version=fixed_version, development=minigame_development) if uses_minigames else None
    # This second native command is part of the SDK launcher's New Project
    # workflow. It materializes button/bar/slider images used by native
    # DynamicImage styles; generate_gui alone leaves those paths incomplete.
    gui_images = run_sdk(sdk, target, "gui_images", ["--savedir", str(private_saves)],
                         timeout_seconds=120, env=isolated)
    if gui_images.exit_code != 0 or gui_images.timed_out:
        raise GenerationError(f"Native GUI image generation failed: {gui_images.stderr or gui_images.stdout}")
    source_map = {"schema_version": project.schema_version, "generator_version": GENERATOR_VERSION,
                  "sdk_version": sdk.version, "project_id": project.project_id,
                  "revision": project.revision, "frames": frames}
    source_map_path = target / "bridge-source-map.json"
    source_map_path.write_text(json.dumps(source_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"project_id": project.project_id, "revision": project.revision,
                "build_name": build_name,
                "sdk_version": sdk.version, "generator_version": GENERATOR_VERSION,
                "literal_serializer": "renpy-8.5.3-native-v1",
                "asset_filename_scheme": "sha256-prefix-24-with-full-digest-manifest-v1",
                "chinese_font_sha256": _hash_file(font), "static_gui_copied": copied_gui,
                "assets": {aid: {"game_path": path, "sha256": project.assets[aid].sha256,
                                  "version": project.assets[aid].version} for aid, path in paths.items()},
                "native_gui_process": asdict(generation), "gui_images_process": asdict(gui_images)}
    if minigame_installation is not None:
        manifest.update({"minigame_generator_version": MINIGAME_GENERATOR_VERSION,
                         "minigame_development": bool(minigame_development),
                         "minigames": minigame_installation["minigames"],
                         "html_runtime": minigame_installation["html_runtime"],
                         "fixed_runtime": minigame_installation["fixed_runtime"],
                         "minigame_configuration": minigame_installation["configuration_path"]})
    manifest_path = target / "bridge-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from .derived_integrity import record_derived_sources
    if uses_minigames and defer_minigame_integrity:
        # Compatibility preparation installs retained immutable packages and
        # extends the static configuration before the first authoritative
        # registry is written. Never replace an existing integrity baseline.
        derived_sources = {"pending": True, "managed_roots": minigame_installation["managed_roots"]}
    elif uses_minigames:
        derived_sources = record_derived_sources(target, managed_roots=minigame_installation["managed_roots"])
    else:
        derived_sources = record_derived_sources(target)
    return {"project_path": str(target), "source_map_path": str(source_map_path),
            "build_name": build_name,
            "manifest_path": str(manifest_path), "sdk_version": sdk.version,
            "generator_version": GENERATOR_VERSION,
            "derived_sources": derived_sources,
            "frame_labels": {fid: entry["label"] for fid, entry in frames.items()}}
