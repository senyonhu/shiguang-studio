"""Native Ren'Py test support for logical text and player interactions.

Append the returned source to a dedicated QA .rpy file. These helpers inspect
the engine's actual Text objects; font coverage and screenshot review remain
separate checks. The caller supplies one global teardown containing ``exit``.
"""
from __future__ import annotations

import re


NATIVE_QA_SOURCE = '''init -1 python:
    import itertools as rb_qa_itertools

    def _rb_qa_text_tokens(displayable):
        if displayable is None:
            return None
        if displayable.dirty or displayable.displayables is None:
            displayable.update()
        return "".join(value if kind == renpy.text.textsupport.TEXT else "\\n" if kind == renpy.text.textsupport.PARAGRAPH else "" for kind, value in displayable.tokens)

    def _rb_qa_visible_text(screen="say", identifier="what"):
        return _rb_qa_text_tokens(renpy.get_displayable(screen, identifier))

    def _rb_qa_layer_text(layer="rb_decorations"):
        # Pin this diagnostic traversal to the tested SDK. Public showing tags
        # do not expose Text children wrapped in generated Transforms.
        values = []
        visited = set()
        def walk(displayable):
            if id(displayable) in visited:
                return
            visited.add(id(displayable))
            if isinstance(displayable, renpy.text.text.Text):
                values.append(_rb_qa_text_tokens(displayable))
                return
            for child in displayable.visit():
                if child is not None:
                    walk(child)
        for entry in renpy.game.context().scene_lists.layers[layer]:
            walk(entry.displayable)
        return values

    def _rb_qa_assert_layer_text(expected, layer="rb_decorations"):
        actual = _rb_qa_layer_text(layer)
        print("NATIVE_LAYER_TEXT_EXPECTED=" + repr(expected))
        print("NATIVE_LAYER_TEXT_ACTUAL=" + repr(actual))
        if actual != expected:
            raise AssertionError("Native decorative logical text differs from authoring text")
        return True

    def _rb_qa_codepoint_diff(expected, actual, limit=20):
        differences = []
        for index, (wanted, found) in enumerate(rb_qa_itertools.zip_longest(expected, actual or "")):
            if wanted != found:
                differences.append({"index": index, "expected": None if wanted is None else "U+%04X" % ord(wanted), "actual": None if found is None else "U+%04X" % ord(found)})
                if len(differences) >= limit:
                    break
        return differences

    def _rb_qa_assert_text(expected, screen="say", identifier="what"):
        actual = _rb_qa_visible_text(screen, identifier)
        print("NATIVE_TEXT_EXPECTED=" + repr(expected))
        print("NATIVE_TEXT_ACTUAL=" + repr(actual))
        if actual != expected:
            print("NATIVE_TEXT_CODEPOINT_DIFF=" + repr(_rb_qa_codepoint_diff(expected, actual)))
            raise AssertionError("Native displayed logical text differs from the authoring text")
        return True

    def _rb_qa_layer_state():
        # Include native placement and rendered text as well as showing tags,
        # so save/load/rollback cannot pass while restoring the wrong layout.
        return {layer: {
            "tags": sorted(renpy.get_showing_tags(layer=layer)),
            "entries": [(entry.tag, entry.zorder, tuple(entry.displayable.get_placement())) for entry in renpy.game.context().scene_lists.layers[layer]],
            "text": _rb_qa_layer_text(layer),
        } for layer in ("rb_background", "rb_characters", "rb_decorations")}
'''


def interaction_testcases(first_frame_id: str, second_frame_id: str,
                          namespace: str = "rb_interactions", *,
                          first_text: str | None = None,
                          second_text: str | None = None) -> str:
    """Return tests for a fixed fixture whose Start frame goes to a second say.

    Both supplied frames must have dialogue and the first must flow directly
    to the second. Run with private saves/multipersistent, a real renderer and
    a bounded process timeout. Native player actions are exercised, without
    reliance on translated button captions or unreviewed screenshot baselines.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", namespace):
        raise ValueError("Test suite namespace must be an ASCII identifier")
    first = repr(first_frame_id)
    second = repr(second_frame_id)
    first_visible = "_rb_qa_visible_text() is not None" if first_text is None else f"_rb_qa_visible_text() == {first_text!r}"
    second_visible = "_rb_qa_visible_text() is not None" if second_text is None else f"_rb_qa_visible_text() == {second_text!r}"
    first_ready = f"rb_current_frame_id == {first} and {first_visible}"
    second_ready = f"rb_current_frame_id == {second} and {second_visible}"
    return f'''testsuite {namespace}:
    before testcase:
        $ _test.timeout = 15.0
        $ _preferences.text_cps = 0
        if eval (not main_menu):
            run MainMenu(confirm=False, save=False)
        pause 0.1 until screen "main_menu"
        run Start()
        pause 0.1 until eval ({first_ready})
        pause 0.3

    testcase save_load_rollback:
        # Function attributes are QA-only state outside the game save/rollback
        # store. Test locals and store variables are both reset on FileLoad.
        $ _rb_qa_visible_text.baseline = _rb_qa_visible_text()
        $ _rb_qa_layer_state.baseline = _rb_qa_layer_state()
        run ShowMenu("save")
        pause 0.3
        assert screen "save"
        run FileSave(1, confirm=False)
        pause 0.3
        keysym "game_menu"
        pause 0.1 until not screen "save"
        keysym "dismiss"
        pause 0.1 until eval ({second_ready})
        pause 0.3
        run FileLoad(1, confirm=False)
        pause 0.1 until eval (rb_current_frame_id == {first} and _rb_qa_visible_text() == _rb_qa_visible_text.baseline)
        pause 0.3
        assert eval (_rb_qa_layer_state() == _rb_qa_layer_state.baseline)
        keysym "dismiss"
        pause 0.1 until eval ({second_ready})
        pause 0.3
        assert eval renpy.can_rollback()
        run Rollback()
        pause 0.1 until eval (rb_current_frame_id == {first} and _rb_qa_visible_text() == _rb_qa_visible_text.baseline)
        pause 0.3
        assert eval (_rb_qa_layer_state() == _rb_qa_layer_state.baseline)

    testcase history_and_preferences:
        $ rb_qa_original_text = _rb_qa_visible_text()
        keysym "dismiss"
        pause 0.1 until eval ({second_ready})
        pause 0.3
        run ShowMenu("history")
        pause 0.3
        assert screen "history"
        assert eval (len(_history_list) >= 1)
        keysym "game_menu"
        pause 0.1 until not screen "history"
        assert eval (rb_current_frame_id == {second})
        run ShowMenu("preferences")
        pause 0.3
        assert screen "preferences"
        $ rb_qa_original_music_volume = _preferences.get_volume("music")
        run Preference("music volume", 0.25)
        assert eval (abs(_preferences.get_volume("music") - 0.25) < 0.001)
        run Preference("music volume", rb_qa_original_music_volume)
        keysym "game_menu"
        pause 0.1 until not screen "preferences"
        assert eval (rb_current_frame_id == {second})

    testcase auto_forward:
        $ rb_qa_original_afm_time = _preferences.afm_time
        $ _preferences.afm_time = 0.2
        run Preference("auto-forward", "enable")
        pause 0.1 until eval (rb_current_frame_id != {first})
        run Preference("auto-forward", "disable")
        $ _preferences.afm_time = rb_qa_original_afm_time
        assert eval (rb_current_frame_id != {first})

    testcase skip:
        $ rb_qa_original_skip_unseen = _preferences.skip_unseen
        $ _preferences.skip_unseen = True
        run Skip()
        pause 0.1 until eval (rb_current_frame_id != {first})
        $ renpy.config.skipping = None
        $ _preferences.skip_unseen = rb_qa_original_skip_unseen
        assert eval (rb_current_frame_id != {first})
'''
