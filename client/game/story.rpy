label start:
    $ rt.new_session()
    $ run_id = uuid.uuid4().hex
    $ receipts = {}
    $ drafts = {}
    $ hints_used = {}
    $ attempts = {}
    $ story_lamps = 0
    $ practice = False
    $ first_answer = None
    $ current_answer = None
    $ active_seconds = 0.0
    $ save_notice = ""
    $ current_checkpoint = "opening"
    scene black
    play music "audio/tower.wav" fadein 2.0
    play sound "audio/drip.wav"
    "你醒来时，掌心压着冰冷的石板。"
    scene tower with Dissolve(2.0)
    show screen closed_door
    show screen story_lights
    "头顶没有熟悉的天花板，只有一圈已经熄灭的灯。"
    show li worried at li_shadow with dissolve
    li "听得见吗？先别动，前面是断掉的台阶。"
    call screen nickname
    $ player_name = player_name.strip() or "旅人"
    menu:
        "这是哪里？":
            li "旧塔的下层。等有了光，我带你找出口。"
        "你是谁？":
            li "我叫璃。至于你为什么在这里……我也想知道。"
    li "你手边的术式板还在响应。试着把第一盏灯的编号发给它。"
    "石板显出一段文字。你竟然认得它——这是 C 语言。"
    "C 程序输出的是文字；这块术式板按约定把文字变成点灯指令。"
    $ save_checkpoint()
    hide li
    hide screen closed_door
    hide screen story_lights
    call play_task(1)
    scene tower
    show screen closed_door
    show screen story_lights
    show li relieved at li_story with dissolve
    li "亮了。它读懂了你写的东西。"
    player "我只是改了一个数字。"
    li "在这里，一个数字就能决定我们看见什么。"
    show li thinking at li_story
    li "这段旧术式应该依次点亮三盏灯。先运行一次，看看它停在哪里。"
    hide li
    hide screen closed_door
    hide screen story_lights
    call play_task(2)
    scene tower
    show screen closed_door
    show screen story_lights
    show li relieved at li_story with dissolve
    li "第三盏也亮了。但出口接的是另一条回路。"
    li "先别急着复制。只有一盏灯的时候呢？一盏也没有的时候呢？"
    "你保留了修好的代码。术式板等待着不同规模的回路。"
    hide li
    hide screen closed_door
    hide screen story_lights
    call play_task(3)
    $ current_checkpoint = "before_door"
    $ save_checkpoint()
    scene tower
    show screen closed_door
    show screen story_lights
    show li relieved at li_story with dissolve
    li "现在，这几条回路都能正常工作了。"
    li "走吧。你总得先看看自己来到了什么地方。"
    hide li with dissolve
    $ story_lamps = 0
    while story_lamps < 6:
        $ story_lamps += 1
        play sound "audio/lamp.wav"
        pause 0.32
    hide screen closed_door
    show screen opening_door
    play sound "audio/door.wav"
    pause 3.0
    hide screen opening_door
    hide screen story_lights
    play music "audio/world.wav" fadeout 1.5 fadein 3.0
    if not reduce_motion:
        call screen world_reveal
    scene world_far
    show screen world_city
    with Dissolve(1.0)
    "山谷与浮岛，在晨光里展开。遥远的城市悬在云海之上。"
    play sound "audio/echo.wav"
    echo "如果你已经走出了那座塔……这一次，别把重要的话留到明天。"
    player "谁在说话？"
    show li worried at li_story with dissolve
    li "怎么了？"
    player "……没什么。"
    hide li with dissolve
    "第一章 / 初识术式，待续。"
    call screen comprehension
    $ current_checkpoint = "completed"
    $ save_checkpoint()
    hide screen world_city
    jump settlement

label play_task(number):
    $ prepare_task(number)
    $ save_checkpoint()
    if number == 2:
        $ submit("verify")
    jump task_interaction

label task_interaction:
    call screen task_screen
    if _return == "save":
        $ save_checkpoint("manual")
        jump task_interaction
    elif _return == "menu":
        $ flush_draft()
        $ rt.new_session()
        $ running = False
        call screen task_menu
        jump task_interaction
    elif _return == "continue":
        if commit_task():
            $ save_checkpoint()
            return
        jump task_interaction
    jump task_interaction

label settlement:
    $ practice = False
    call screen result_screen
    if _return == "practice":
        $ practice = True
        $ practice_snapshot = (dict(drafts), dict(attempts), dict(hints_used), active_seconds)
        call play_task(3)
        $ drafts, attempts, hints_used, active_seconds = practice_snapshot
        $ practice = False
        $ current_checkpoint = "completed"
        jump settlement
    $ rt.new_session()
    return

screen nickname():
    modal True
    frame:
        align (0.5,0.48) xsize 860
        padding (50,40)
        vbox:
            spacing 27
            text "璃该怎样称呼你？" size 36 color "#e7cc99"
            input value VariableInputValue("player_name") length 16 font "fonts/SourceHanSansLite.ttf" size 38 exclude "{}[]"
            text "可以保留“旅人”，直接继续。" size 25 color "#9ab4c1"
            textbutton "继续 →" action Return() xalign 1.0

screen closed_door():
    zorder 0
    add "images/door.png" xpos 903 ypos 243 xysize (218,415)

screen opening_door():
    add "images/door.png":
        xpos 903 ypos 243 xysize (218,415)
        at transform:
            linear 3.0 xoffset -235 alpha 0.0
    add "images/lock.png":
        xpos 956 ypos 398 xysize (110,110)
        at transform:
            alpha 0.0
            linear 0.5 alpha 1.0
            linear 2.5 alpha 0.0

screen world_city():
    add "images/world_mid.png" xalign 0.80 yalign 0.60 zoom 0.66

screen task_menu():
    modal True
    add Solid("#04101ae8")
    frame:
        align (0.5,0.5) xsize 760
        vbox:
            spacing 24
            text "旅程暂停" size 44 color "#e4c997"
            text "代码草稿已尝试备份；未完成的运行已终止。" size 25 xsize 670
            textbutton "返回术式板" action Return() xfill True
            textbutton "读取存档" action ShowMenu("load") xfill True
            textbutton "设置" action ShowMenu("preferences") xfill True
            textbutton "返回标题" action MainMenu(confirm=True) xfill True
            textbutton "退出游戏" action Quit(confirm=True) xfill True
