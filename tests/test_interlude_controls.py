from renpy_bridge.generator import _build_story, stable_label
from renpy_bridge.models import Project
from renpy_bridge.pipeline import native_tests


def test_clean_interlude_hides_native_controls_and_next_dialogue_restores_them():
    project = Project(project_id="interlude", name="无文字过场", save_namespace="interlude", entry_frame_id="silent",
                      acts={"act": {"act_id": "act", "frame_ids": ["silent", "dialogue"]}}, frames={
                          "silent": {"frame_id": "silent", "act_id": "act", "flow": {"kind": "next", "target_frame_id": "dialogue"}},
                          "dialogue": {"frame_id": "dialogue", "act_id": "act", "dialogue": {"text": "下一帧"}, "flow": {"kind": "end"}}})
    story, mapping = _build_story(project, {})
    for fid, expected in [("silent", False), ("dialogue", True)]:
        entry = mapping[fid]
        block = "\n".join(story.splitlines()[entry["start_line"] - 1:entry["end_line"]])
        assert f"label {stable_label(fid)}:" in block
        assert f"$ quick_menu = {expected}" in block
    qa, _ = native_tests(project, "preview", {"frame_ids": ["silent", "dialogue"]})
    assert "assert eval (quick_menu == False)" in qa
    assert "assert eval (quick_menu == True)" in qa


def test_choice_without_dialogue_keeps_native_menu_controls():
    project = Project(project_id="choice", name="无对白选择", save_namespace="choice", entry_frame_id="choose",
                      acts={"act": {"act_id": "act", "frame_ids": ["choose", "end"]}}, frames={
                          "choose": {"frame_id": "choose", "act_id": "act", "flow": {"kind": "choices", "choices": [{"choice_id": "one", "text": "继续", "target_frame_id": "end"}]}},
                          "end": {"frame_id": "end", "act_id": "act", "flow": {"kind": "end"}}})
    story, mapping = _build_story(project, {})
    entry = mapping["choose"]
    block = "\n".join(story.splitlines()[entry["start_line"] - 1:entry["end_line"]])
    assert "$ quick_menu = True" in block and "menu:" in block
