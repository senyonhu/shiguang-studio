"""Build new formal-manifest examples without changing the frozen H0 packages.

This only materializes and validates packages. It does not grant browser,
save compatibility, or independent-release certification.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from build_prototypes import DEFINITIONS, package_digest, sha256

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "production"
LICENSE = """MIT License

Copyright (c) 2026 RenPy Bridge contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
INSTRUCTIONS = {
    "puzzle": "星图锁检修单：只能移动空格相邻的数字。依次排成 1、2、3 / 4、5、6 / 7、8、空格，锁就会打开。",
    "timed": "信号回路操作单：按开始后才计时。每次按发亮的端子；错误操作扣半秒。时间用尽会失败，所有信号接通才成功。",
    "checkpoint": "温室检修单：先安装温度探头，再接蓝色端子，最后校准至显示的目标温度并实测。完成前两道工序会保存进度。",
}


def production_sdk() -> str:
    source = (ROOT / "sdk/bridge-sdk.js").read_text(encoding="utf-8")
    source = source.replace("原型大小限制", "大小限制")
    source = source.replace("尚未建立小游戏会话。", "小游戏还未准备好。")
    source = source.replace("宿主未确认结果；请使用宿主恢复或退出功能。", "结果暂未保存，请退出后重新进入。")
    source = source.replace("宿主拒绝该事件。", "这次操作暂时无法完成。")
    source = source.replace('package_version: "h0-1"', 'package_version: options.packageVersion || "1.0.0"')
    source = source.replace('catch (error) { reportError("HANDLER_ERROR", error.message || String(error)); }',
                            'catch (error) { if (name !== "error") reportError("HANDLER_ERROR", error.message || String(error)); }')
    return source


def main() -> None:
    PRODUCTION.mkdir(parents=True, exist_ok=True)
    sdk = production_sdk()
    (PRODUCTION / "sdk").mkdir(exist_ok=True)
    (PRODUCTION / "sdk/bridge-sdk.js").write_text(sdk, encoding="utf-8", newline="\n")
    digests = {
        "prototype": False,
        "protocol": "1.0",
        "status": "formal_manifest_examples_not_release_certification",
        "digest_algorithm": "sha256(path_utf8+nul+file_sha256_ascii+lf)-v1",
        "packages": {},
    }
    for name in DEFINITIONS:
        directory = PRODUCTION / name
        directory.mkdir(exist_ok=True)
        for filename in ("styles.css", "tap.wav"):
            if (ROOT / name / filename).is_file():
                shutil.copyfile(ROOT / name / filename, directory / filename)
        html = (ROOT / name / "index.html").read_text(encoding="utf-8")
        html = html.replace("离线玩法原型", "离线小游戏")
        html = html.replace("温室修复 · 检查点任务", "温室修复")
        html = html.replace("前两道完成后记录检查点，返回时可继续修复。", "前两道完成后保存进度，返回时可继续修复。")
        html = html.replace("正在等待游戏开始……", "正在准备……")
        html = html.replace("独立预览：这里可以实际解谜，完成结果不会返回 Galgame。", "你正在单独试玩，完成后可以关闭页面。")
        html = html.replace("独立预览：计时开始前不会扣时，结果不会返回 Galgame。", "你正在单独试玩，准备好后按开始。")
        html = html.replace("独立预览：检查点只模拟确认，不会写入 Galgame 存档。", "你正在单独试玩，进度只保留在当前页面。")
        html = html.replace("独立预览：这里可以实际操作，完成结果不会返回 Galgame。", "你正在单独试玩，完成后可以关闭页面。")
        html = html.replace("独立预览：这里可以实际修复；检查点只展示接入流程，不写入 Galgame 存档。", "你正在单独试玩，进度只保留在当前页面。")
        html = html.replace("  </main>", '    <p id="package-instructions" class="help" data-testid="package-instructions">正在读取本地操作单……</p>\n  </main>')
        (directory / "index.html").write_text(html, encoding="utf-8", newline="\n")
        game = (ROOT / name / "game.js").read_text(encoding="utf-8")
        game = game.replace('gameId: "h0_' + name + '"', 'gameId: "prod_' + name + '", packageVersion: "1.0.0"')
        game = game.replace("h0-1 / 1.0", "1.0.0 / 1.0")
        game = game.replace("正在记录检查点", "正在保存进度")
        game = game.replace("检查点版本或内容不匹配，请使用宿主恢复功能。", "这份进度无法恢复，请退出后重新进入。")
        game = game.replace("星图锁已打开，独立预览完成。", "星图锁已打开！试玩完成。")
        # This text resource is part of the actual page, not an unused packaging fixture.
        game = game.replace('  document.getElementById("preview").hidden',
            '  fetch("instructions.txt").then(response => {\n'
            '    if (!response.ok) throw new Error("本地操作单不可读");\n'
            '    return response.text();\n'
            '  }).then(text => { document.getElementById("package-instructions").textContent = text; })\n'
            '    .catch(error => bridge.reportError("LOCAL_INSTRUCTIONS_MISSING", error.message));\n'
            '  document.getElementById("preview").hidden')
        (directory / "game.js").write_text(game, encoding="utf-8", newline="\n")
        (directory / "bridge-sdk.js").write_text(sdk, encoding="utf-8", newline="\n")
        (directory / "LICENSE.txt").write_text(LICENSE, encoding="utf-8", newline="\n")
        (directory / "instructions.txt").write_text(INSTRUCTIONS[name] + "\n", encoding="utf-8", newline="\n")
        manifest = {
            "format_version": "1.0", "prototype": False, "game_id": "prod_" + name,
            "version": "1.0.0", "entry": "index.html", "protocol": "1.0", **DEFINITIONS[name],
            "required_capabilities": ["local_content", "host_messages"] + (["audio"] if name == "timed" else []),
            "license": "MIT; see LICENSE.txt. Original code, instructions, and generated tap.wav; no external dependency.",
            "files": [],
        }
        if name == "checkpoint":
            manifest["display_name"] = "温室修复"
        for path in sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix().encode("utf-8")):
            if path.is_file() and path.name != "manifest.json":
                manifest["files"].append({"path": path.relative_to(directory).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size})
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        digests["packages"][name] = {
            "game_id": manifest["game_id"], "package_version": "1.0.0", "package_hash": package_digest(directory),
            "manifest_sha256": sha256(directory / "manifest.json"), "file_count": len(manifest["files"]) + 1,
        }
    (PRODUCTION / "package-digests.json").write_text(json.dumps(digests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(digests, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
