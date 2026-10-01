label test_lab:
    if not renpy.is_in_test():
        return
    $ run_id = "test-" + uuid.uuid4().hex
    $ drafts = {}
    $ receipts = {}
    $ hints_used = {}
    $ practice = False
    $ prepare_task(3)
    jump task_interaction

testsuite tower_recovery:
    testcase paste_save_recover:
        pause until screen "main_menu"
        run Start("test_lab")
        pause until screen "task_screen"
        $ renpy.set_physical_size((1366,768))
        pause 0.5
        run Function(edit_fragment,'')
        python:
            import renpy.pygame as pg
            rt.test_fragment = 'for (int i = 1; i <= n; i++) {\r\n    printf("%d\\n", i); /* [i] */\r\n}'
            rt.test_clipboard_functions = (pg.scrap.get, pg.scrap.put)
            rt.test_clipboard_fixture = False
            try:
                rt.test_clipboard = pg.scrap.get(pg.scrap.SCRAP_TEXT)
                pg.scrap.put(pg.scrap.SCRAP_TEXT,rt.test_fragment.encode('utf-8'))
            except pg.error:
                rt.test_clipboard_fixture = True
                rt.test_clipboard_bytes = rt.test_fragment.encode('utf-8')
                pg.scrap.get = lambda kind: rt.test_clipboard_bytes
                print('CLIPBOARD FIXTURE: Windows denies clipboard access; exercising editor paste with injected bytes, not OS clipboard integration.')
        keysym "shift_K_INSERT" id "code_input"
        $ print('PASTE ACTUAL:',repr(fragment),'EXPECTED:',repr(rt.test_fragment))
        assert eval (fragment==rt.test_fragment.replace('\r\n','\n'))
        click "完整源码"
        screenshot "source-1366.png"
        click "返回编辑"
        click "验证全部回路"
        pause until eval can_continue()
        click "保存进度"
        pause 0.4
        assert eval renpy.can_load('manual')
        run Function(edit_fragment,'for (int i=1;i<n;i++){printf("%d\\n",i);}')
        pause 1.3
        assert eval (saved_draft_revision==revision)
        run Function(renpy.load,'manual')
        pause until screen "recover_draft"
        click "恢复草稿"
        assert eval (not can_continue() and 'i<n' in fragment and not running)
        screenshot "recovered-1366.png"
        click "验证全部回路"
        pause until eval (result is not None and not running)
        assert eval (result['status']=='wrong_answer')
        click "恢复初始"
        click "确认"
        assert eval (fragment==current_task()['initial_fragment'])
        click "验证全部回路"
        pause until eval can_continue()
        screenshot "passed-1366.png"
        $ print('RECOVERY PASS: paste braces/brackets/percent/quotes/backslash/newline; restore newer draft invalidates pass; 1366x768.')
    teardown:
        python:
            if hasattr(rt,'test_clipboard_functions'):
                import renpy.pygame as pg
                pg.scrap.get, pg.scrap.put = rt.test_clipboard_functions
                if not rt.test_clipboard_fixture and hasattr(rt,'test_clipboard'):
                    try:
                        pg.scrap.put(pg.scrap.SCRAP_TEXT,rt.test_clipboard or b'')
                    except pg.error:
                        print('Clipboard restore unavailable; test process will exit.')
        exit
