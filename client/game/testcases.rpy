init python:
    _test.timeout = 20.0
    _test.screenshot_directory = os.path.join(config.basedir, "tests", "screenshots")
    # Ren'Py 8.5.3's test-only key map incorrectly maps K_EQUALS to ':'.
    # Repair the simulator mapping; the game's SDL input path is unaffected.
    import renpy.test.testkey as testkey
    import renpy.pygame as testpygame
    testkey.code_to_unicode[testpygame.K_EQUALS] = "="
    testkey.unicode_to_code["="] = testpygame.K_EQUALS
    testkey.unicode_to_code["{"] = testpygame.K_LEFTBRACKET
    testkey.unicode_to_code["}"] = testpygame.K_RIGHTBRACKET

testsuite tower_flow:
    testcase complete_journey:
        pause until screen "main_menu"
        screenshot "title.png"
        click "开始旅程"
        advance until screen "nickname"
        click "继续 →"
        advance until screen "choice"
        click "这是哪里"
        advance until screen "task_screen"
        screenshot "task1-initial.png"
        click "运行术式"
        pause until eval (result is not None and not running)
        assert eval (result['status']=='wrong_answer')
        keysym "K_END" id "code_input"
        keysym "K_BACKSPACE"
        type "1"
        assert eval (fragment=='1')
        click "运行术式"
        pause until eval can_continue()
        pause 0.7
        screenshot "task1-passed.png"
        click "继续 →"
        advance until screen "task_screen"
        pause until eval (result is not None and not running)
        assert eval (task_index==2 and result['cases'][0]['stdout']=='1\n2\n')
        screenshot "task2-initial.png"
        keysym "K_END" id "code_input"
        keysym "K_BACKSPACE" repeat 12
        type "i <= n"
        assert eval (fragment=='i <= n')
        click "运行术式"
        pause until eval can_continue()
        click "保存进度"
        pause 0.5
        assert eval renpy.can_load('manual')
        click "继续 →"
        advance until screen "task_screen"
        assert eval (task_index==3 and 'i <= n' in fragment)
        screenshot "task3-editor.png"
        run Function(edit_fragment,'')
        type 'for (int i = 1; i <= n; i++) {' id "code_input"
        keysym "K_RETURN"
        type 'printf("%d\\n", i);'
        keysym "K_RETURN"
        type '}'
        $ print("EDITOR ROUNDTRIP:", repr(fragment))
        screenshot "typed-code.png"
        assert eval (fragment=='for (int i = 1; i <= n; i++) {\nprintf("%d\\n", i);\n}')
        click "验证全部回路"
        pause until eval can_continue()
        assert eval (len(result['cases'])==4 and result['cases'][0]['stdout']=='')
        pause 2.2
        screenshot "four-cases.png"
        click "继续 →"
        advance until screen "world_reveal"
        pause 16.0
        screenshot "world-reveal.png"
        click "跳过演出"
        advance until screen "comprehension"
        click "B. 因为"
        click "查看旅程记录"
        pause until screen "result_screen"
        assert eval (len(receipts)==3 and first_answer=='B')
        click "导出体验记录"
        screenshot "ending.png"
    teardown:
        exit
