init python:
    style.default.font = "fonts/SourceHanSansLite.ttf"
    style.default.size = 28
    style.default.color = "#e7edf0"
    style.default.language = "unicode"
    style.default.font_features = {"liga": False, "calt": False}
    style.button.background = Frame(Transform("images/ui_panel.png", xysize=(320,320)), 12, 12)
    style.button.hover_background = Solid("#375663")
    style.button.selected_background = Solid("#43616a")
    style.button.insensitive_background = Solid("#14212b")
    style.button.padding = (24, 14)
    style.button_text.color = "#dce7ea"
    style.button_text.hover_color = "#ffe0a4"
    style.button_text.insensitive_color = "#78858d"
    style.button_text.size = 27
    style.button_text.text_align = 0.5
    style.button_text.xalign = 0.5
    style.frame.background = Frame(Transform("images/ui_panel.png", xysize=(640,640)), 24, 24)
    style.frame.padding = (30, 24)
    style.input.color = "#e0f4ed"
    style.input.font = "fonts/JetBrainsMono-Regular.ttf"
    style.input.size = 26
    style.vscrollbar.base_bar = Solid("#172831")
    style.vscrollbar.thumb = Solid("#69818b")
    style.vscrollbar.xsize = 8
    style.bar.left_bar = Solid("#c7a875")
    style.bar.right_bar = Solid("#293d49")
    style.bar.ysize = 12

screen lamp(number, lit=False, width=125):
    fixed:
        xysize (width, int(width * 1.2))
        if lit:
            add "images/lamp_glow.png" xysize (width * 2, width * 2) align (0.5, 0.43)
        add ("images/lamp_on.png" if lit else "images/lamp_off.png") xysize (width, width) align (0.5, 0.0)
        text str(number) xalign 0.5 yalign 1.0 color ("#ffe1a1" if lit else "#879da7") size 25

screen story_lights():
    zorder 1
    for index, point in enumerate([(220,270),(490,240),(740,210),(1160,210),(1410,240),(1680,270)]):
        fixed:
            pos point
            use lamp(index+1, index < story_lamps, 90)

screen main_menu():
    tag menu
    on "show" action Function(rt.new_session)
    add "tower"
    add Solid("#06101c99")
    add "images/door.png" xpos 903 ypos 243 xysize (218, 415)
    add "images/li_worried.png" xpos 1110 ypos 40 xysize (672, 1008)
    vbox:
        xpos 146
        ypos 150
        spacing 18
        text "A CHRONICLE OF SPELLS" size 22 color "#97b9c5" kerning 5
        text "术式纪元" size 110 color "#f1e2c5" outlines [(1,"#1d2a36",0,3)]
        text "旧塔的最后一盏灯" size 42 color "#c9d6dc"
        null height 24
        text "在黑暗里，写下第一束光。" size 28 color "#a6bbc8"
        null height 40
        textbutton "开始旅程    →" action Start() xsize 400 text_size 32
        textbutton "继续旅程" action ShowMenu("load") xsize 400
        hbox:
            spacing 16
            textbutton "设置" action ShowMenu("preferences") xsize 192
            textbutton "退出" action Quit(confirm=True) xsize 192
    text "序章 / 离线 C 语言冒险 / 约 15—20 分钟" xpos 148 ypos 993 size 23 color "#8da6b5"
    if rt.runner:
        text rt.health["message"] xpos 1240 ypos 1003 size 22 color "#acc6c8"
        if not rt.health["available"]:
            textbutton "重试组件检查" action Function(refresh_runtime) xpos 1230 ypos 927 text_size 23
            text rt.health["details"] xpos 1120 ypos 820 xsize 700 size 20 color "#ddafa5"

screen say(who, what):
    window:
        id "window"
        background Frame("images/ui_panel.png", 32, 32)
        xalign 0.5
        ypos 800
        xsize 1720
        ysize 240
        padding (55, 30)
        if who is not None:
            text who id "who" ypos 0 size 32 color "#e8c58b"
        text what id "what" ypos 54 xsize 1590 size 33 line_spacing 10
    text "点击 / 空格 继续" xpos 1560 ypos 1004 size 20 color "#8da5ad"
    use quick_menu

screen quick_menu():
    hbox:
        xpos 1380
        ypos 28
        spacing 10
        textbutton "历史" action ShowMenu("history") text_size 22
        textbutton "存档" action ShowMenu("save") text_size 22
        textbutton "设置" action ShowMenu("preferences") text_size 22

screen choice(items):
    modal True
    vbox:
        align (0.5,0.52)
        spacing 22
        for item in items:
            textbutton item.caption action item.action xsize 780

screen input(prompt):
    modal True
    frame:
        align (0.5,0.5)
        xsize 900
        padding (54,46)
        vbox:
            spacing 32
            text prompt size 32
            input id "input" font "fonts/SourceHanSansLite.ttf" size 34 xsize 750
            textbutton "确认名字" action Return() xalign 1.0

screen task_screen():
    tag task
    modal True
    default view_source = False
    default details = False
    default knowledge = False
    key "rollback" action NullAction()
    key "rollforward" action NullAction()
    key "game_menu" action Return("menu")
    add "tower"
    add Solid("#07111d66")
    add "images/door.png" xpos 891 ypos 232 xysize (235,430)
    text "旧塔 / 术式板" xpos 56 ypos 40 size 23 color "#a4bbc8"
    text ("自由实验" if practice else "第 %d / 3 项修复" % task_index) xpos 56 ypos 83 size 38 color "#e9d1a4"
    add ("images/li_relieved.png" if can_continue() else "images/li_thinking.png" if task_index > 1 else "images/li_worried.png") xpos 115 ypos 342 xysize (480,720)
    frame:
        xpos 45 ypos 147 xsize 648 ysize 187
        padding (22,18)
        hbox:
            spacing 4
            for number in range(1,7):
                use lamp(number, number in preview_lamps, 95)
    frame:
        xpos 46 ypos 807 xsize 647 ysize 225
        vbox:
            spacing 12
            text "璃 / 守塔学徒" size 25 color "#dfc18e"
            text ("现在，这几条回路都能正常工作了。" if can_continue() and task_index==3 else "亮了。它读懂了你写的东西。" if can_continue() else "不是完全失效。看一看，它停在了哪一盏。" if result else "术式板还在响应。试着运行，再观察灯的变化。") size 27 xsize 572
            text "灯光是输出回放，并非实时调试。" size 21 color "#8daab6"
    frame:
        xpos 737 ypos 32 xsize 1140 ysize 1010
        padding (34,28)
        vbox:
            spacing 14
            hbox:
                xfill True
                text current_task()["title"] size 42 color "#f2d9a8" xsize 755
                textbutton "保存进度" action Return("save") sensitive not running text_size 24
            text current_task()["goal"] size 27 color "#c1d8df"
            hbox:
                spacing 10
                text ("无标准输入" if task_index==1 else "标准输入 n =") yalign 0.5 size 24 color "#9eb8c7"
                if task_index==3:
                    for n in range(7):
                        textbutton str(n) action SetVariable("trial_input",n) selected trial_input==n sensitive not running xsize 60 padding (12,8)
                elif task_index==2:
                    text "3" yalign 0.5 size 28 color "#e6d2a8"
                null width 14
                textbutton ("返回编辑" if view_source else "完整源码") action ToggleScreenVariable("view_source") text_size 23 padding (18,8)
                textbutton "说明" action ToggleScreenVariable("knowledge") text_size 23 padding (18,8)
            frame:
                background Solid("#07121af2")
                xsize 1072 ysize 303
                padding (24,18)
                vbox:
                    spacing 12
                    text ("main.c / 最终提交源码" if view_source else current_task()["editable_region"] + " / 可编辑") size 22 color "#7bb3c3"
                    viewport:
                        ysize 237 xsize 1024
                        scrollbars "vertical"
                        mousewheel True
                        draggable False
                        if view_source:
                            text escaped(source_preview()) font "fonts/JetBrainsMono-Regular.ttf" size code_size color "#dae9e9" substitute False
                        else:
                            add CodeEditor(value=CodeInput(), length=(12 if task_index==1 else 4096), allow=("0123456789" if task_index==1 else None), exclude="", multiline=task_index==3, copypaste=True, size=code_size, xmaximum=990) id "code_input"
            text ("Enter 换行 / 方向键移动 / Ctrl+C 复制整个片段 / Ctrl+V 粘贴 / Esc 菜单" if task_index==3 else "点击代码编辑 / 方向键移动 / Ctrl+C 复制片段 / Ctrl+V 粘贴") size 21 color "#8ca6b4"
            hbox:
                spacing 12
                textbutton ("试运行" if task_index==3 else "运行术式") action Function(submit,"trial" if task_index==3 else "verify") sensitive not running text_color "#ffe0a3"
                if task_index==3:
                    textbutton "验证全部回路" action Function(submit,"verify") sensitive not running text_color "#ffe0a3"
                textbutton ("取消" if running else "提示 %d/3" % hint_level) action (Function(cancel_run) if running else Function(open_hint))
                textbutton "恢复初始" action Confirm("恢复初始代码？当前片段会备份一份。",Function(reset_fragment)) sensitive not running text_size 24
                if can_continue():
                    textbutton ("结束实验" if practice else "继续 →") action Return("continue") text_color "#ffe0a3"
            text status_message size 25 color ("#e6c88e" if can_continue() else "#bad2dc") xsize 1055
            viewport:
                xsize 1070 ysize 225
                scrollbars "vertical"
                mousewheel True
                vbox:
                    spacing 10
                    if hint_level:
                        text ("提示 %d：" % hint_level + current_task()["hints"][hint_level-1]) size 24 color "#dfc593" xsize 1010
                    if knowledge:
                        text current_task()["knowledge"] size 23 color "#a6cbd2" xsize 1010
                        if task_index==2:
                            text "初始程序示例推演：i=1 → 真；i=2 → 真；i=3 → 假，循环结束。" size 23 xsize 1010
                        text "程序骨架：stdio.h 声明输入输出函数；main 是入口；scanf 读取 n；return 0 正常结束。" size 23 xsize 1010
                    if result:
                        for c in result["cases"]:
                            hbox:
                                spacing 18
                                text (("通过" if c["passed"] else "未通过") + " / n=%s" % c["input"]) size 24 color ("#c6ddbb" if c["passed"] else "#e0b2a5") xsize 182
                                text c["comparison"]["message"] size 23 xsize 805
                        if result["cases"]:
                            $ lastcase = result["cases"][-1]
                            hbox:
                                spacing 25
                                vbox:
                                    text "期望输出" color "#789baa" size 21
                                    text escaped(repr(lastcase["comparison"]["expected"])) substitute False font "fonts/JetBrainsMono-Regular.ttf" size 23 xsize 485
                                vbox:
                                    text ("实际输出（部分结果，运行失败）" if lastcase["status"]!="ok" else "实际输出") color "#789baa" size 21
                                    text escaped(repr(lastcase["stdout"][:400])) substitute False font "fonts/JetBrainsMono-Regular.ttf" size 23 xsize 485
                            if replay_cursor:
                                text ("正在回放输出行 %d：" % replay_cursor + escaped(replay_lines[replay_cursor-1])) size 23 color "#e8c98e" substitute False
                        textbutton ("收起原始详情" if details else "查看原始详情") action ToggleScreenVariable("details") text_size 22 padding (14,6)
                        if details:
                            text escaped(result["compile_output"] or "编译成功") substitute False size 22 xsize 1010
                            for c in result["cases"]:
                                text ("n=%s / %s / %d ms / exit=%s" % (c["input"],c["status"],c["elapsed_ms"],c["exit_code"])) size 21 color "#91adb8"
                                text escaped(c["stderr"][:2500]) substitute False size 21 xsize 1010
                    else:
                        text "输出将显示在这里。每行一个灯编号，术式板按顺序回放。" size 24 color "#809ca9"
            if save_notice:
                text save_notice size 20 color "#9fb8bc" xsize 1050
    timer 0.32 repeat True action Function(replay_step)

screen recover_draft():
    modal True
    zorder 200
    add Solid("#000a")
    frame:
        align (0.5,0.5) xsize 1000
        vbox:
            spacing 24
            text "发现较新的草稿" size 36 color "#e4c68f"
            text "要恢复独立备份中的代码吗？恢复后需要重新运行验证。" size 28
            hbox:
                spacing 22
                textbutton "恢复草稿" action [Function(recover_latest),Hide("recover_draft")]
                textbutton "保留此存档" action [SetField(rt,"draft_offer",None),Hide("recover_draft")]

screen menu_shell(title):
    add "tower"
    add Solid("#06101de8")
    text title xpos 120 ypos 85 size 56 color "#e6c994"
    textbutton "返回" action Return() xpos 1660 ypos 80
    transclude

screen save():
    tag menu
    use menu_shell("保存旅程"):
        use slots("save")

screen load():
    tag menu
    use menu_shell("继续旅程"):
        use slots("load")

screen slots(mode):
    vbox:
        xpos 120 ypos 220 spacing 26
        hbox:
            spacing 22
            if mode=="load":
                textbutton "最近检查点" action Function(renpy.load,"checkpoint") sensitive renpy.can_load("checkpoint")
                textbutton "快速存档" action Function(renpy.load,"manual") sensitive renpy.can_load("manual")
            text "点击一个存档位" size 26 color "#94afbc"
        grid 3 2:
            spacing 26
            for slot in range(1,7):
                button:
                    xsize 530 ysize 245
                    action (FileSave(slot) if mode=="save" else FileLoad(slot))
                    vbox:
                        spacing 20
                        text ("旅程 %d" % slot) size 29 color "#dec492"
                        text FileTime(slot,format="%Y-%m-%d %H:%M",empty="空存档") size 26
                        text FileSaveName(slot) size 22
        text "F11 全屏 / F12 截图。代码任务中请先用“保存进度”落盘。" size 24 color "#8ca8b8"

screen preferences():
    tag menu
    use menu_shell("设置"):
        vbox:
            xpos 130 ypos 230 spacing 35
            hbox:
                spacing 25
                text "显示" xsize 260
                textbutton "窗口" action Preference("display","window")
                textbutton "全屏" action Preference("display","fullscreen")
            hbox:
                spacing 25
                text "代码字号" xsize 260
                textbutton "标准" action SetVariable("code_size",26) selected code_size==26
                textbutton "大字" action SetVariable("code_size",30) selected code_size==30
            hbox:
                spacing 25
                text "演出" xsize 260
                textbutton "完整演出" action SetVariable("reduce_motion",False) selected not reduce_motion
                textbutton "减少动态" action SetVariable("reduce_motion",True) selected reduce_motion
            hbox:
                spacing 25
                text "音乐音量" xsize 260
                bar value Preference("music volume") xsize 700 yalign 0.5
            hbox:
                spacing 25
                text "音效音量" xsize 260
                bar value Preference("sound volume") xsize 700 yalign 0.5
            hbox:
                spacing 25
                text "文字速度" xsize 260
                bar value Preference("text speed") xsize 700 yalign 0.5
            textbutton "静音 / 恢复声音" action Preference("all mute","toggle")
            text "静音也能完成全部任务。提示不扣分；演出随时可跳过。" color "#9ab7c4" size 26
            textbutton "返回标题" action [Function(shutdown_game),MainMenu(confirm=True)]

screen history():
    tag menu
    use menu_shell("旅程回声"):
        viewport:
            xpos 140 ypos 220 xsize 1620 ysize 745
            scrollbars "vertical"
            mousewheel True
            vbox:
                spacing 28
                for h in _history_list:
                    if h.who:
                        text h.who color "#dfbf87" size 27
                    text h.what size 29 xsize 1520

screen confirm(message, yes_action, no_action):
    modal True
    zorder 200
    add Solid("#000b")
    frame:
        align (0.5,0.5) xsize 950
        vbox:
            spacing 32
            text message size 32 xsize 850
            hbox:
                spacing 25
                textbutton "确认" action yes_action
                textbutton "取消" action no_action

screen notify(message):
    zorder 200
    frame:
        xpos 40 ypos 40
        text message size 24
    timer 3.0 action Hide("notify")

screen world_reveal():
    modal True
    add "world_far" at world_drift
    add "images/world_mid.png" at city_drift
    add "images/world_fore.png" at foreground_drift
    vbox:
        align (0.5,0.36)
        spacing 20
        at reveal_title
        text "术式纪元" size 104 color "#fff3cf" outlines [(2,"#354d67aa",0,3)] xalign 0.5
        text "世界，比你想象的更辽阔。" size 32 color "#fff4d7" outlines [(1,"#354d67",0,1)] xalign 0.5
    textbutton "跳过演出 →" action Return() xpos 1620 ypos 977 text_size 24
    timer 27.0 action Return()

screen comprehension():
    modal True
    frame:
        align (0.5,0.5) xsize 1370
        vbox:
            spacing 25
            text "离开旧塔之前" size 48 color "#e6c895"
            text "输入 0 时，没有灯亮，为什么仍然通过？" size 34
            textbutton "A. 因为程序自动忽略了这个测试。" action Function(answer_question,"A") xfill True
            textbutton "B. 因为没有灯需要点亮，循环第一次判断就不成立。" action Function(answer_question,"B") xfill True
            textbutton "C. 因为运行时间足够短。" action Function(answer_question,"C") xfill True
            if current_answer:
                text ("是的：需要零条指令，实际也没有输出。" if current_answer=="B" else "再想想：输入 0 也真正运行了。把 i=1、n=0 代入条件看看。") size 28 color "#dec999" xsize 1230
                textbutton "查看旅程记录 →" action Return() xalign 1.0

screen result_screen():
    modal True
    add "world_far"
    add "images/world_mid.png" xalign 0.93 yalign 0.62 zoom 0.56
    add Solid("#08182977")
    frame:
        xpos 120 ypos 115 xsize 1040 ysize 850
        padding (55,43)
        vbox:
            spacing 22
            text "旧塔修复记录" size 56 color "#f0d8a6"
            text "第一章 / 初识术式，待续。" size 31 color "#b9ced7"
            null height 15
            for n in range(1,4):
                $ tid = "tower_light_%02d" % n
                text ("%02d  %s    %s" % (n,load_task(tid)["title"],"已完成" if tid in receipts else "未完成")) size 29
            text ("运行 %d 次 / 最高提示 %d 级" % (sum(attempts.values()),max(list(hints_used.values())+[0]))) size 28 color "#aac6cd"
            text ("理解题首次选择：%s" % (first_answer or "未作答")) size 28 color "#aac6cd"
            text ("有效体验约 %d 分钟（不含失焦、菜单与运行等待）" % max(1,round(active_seconds/60))) size 24 color "#8eaeba"
            textbutton ("导出附带源码：是" if include_source else "导出附带源码：否") action ToggleVariable("include_source") text_size 23
            hbox:
                spacing 16
                textbutton "导出体验记录" action Function(export_record)
                textbutton "重试灯阵" action Return("practice")
                textbutton "返回标题" action Return("title")
            if save_notice:
                text save_notice size 21 xsize 914 color "#9fbdc8"
    add "images/li_relieved.png" xpos 1210 ypos 90 xysize (640,960)
