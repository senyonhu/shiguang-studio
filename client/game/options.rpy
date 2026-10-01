define config.save_directory = "TowerLastLight-RenPy-1"

init -100 python:
    import os
    gui.init(1920, 1080)
    config.screen_width = 1920
    config.screen_height = 1080
    config.window_title = "术式纪元 / 旧塔的最后一盏灯"
    config.name = "旧塔的最后一盏灯"
    config.version = "1.1.0"
    config.default_text_cps = 32
    config.default_music_volume = 0.5
    config.default_sfx_volume = 0.6
    config.has_voice = False
    config.developer = False
    config.rollback_enabled = False
    config.allow_skipping = False
    config.keymap['skip'] = []
    config.keymap['toggle_skip'] = []
    config.keymap['hide_windows'] = []
    config.keymap['self_voicing'] = []
    config.keymap['clipboard_voicing'] = []
    config.keymap['accessibility'] = []
    config.keymap['toggle_fullscreen'] = ['alt_K_RETURN', 'K_F11']
    config.keymap['screenshot'] = ['K_F12']
    config.keymap['help'] = []
    config.keymap['input_next_line'] = ['K_RETURN', 'shift_K_RETURN', 'K_KP_ENTER']
    config.keymap['input_enter'] = ['ctrl_K_RETURN']
    config.keymap['game_menu'] = ['K_ESCAPE', 'mouseup_3']
    config.main_menu_music = "audio/tower.wav"

define build.name = "TowerLastLight"
define build.directory_name = "TowerLastLight-1.1.0"
define build.include_update = False
init python:
    build.classify('toolchain/bin/', 'windows')
    build.classify('toolchain/bin/clang.exe', 'windows')
    build.classify('toolchain/bin/clang.cfg', 'windows')
    build.classify('toolchain/bin/wasm-ld.exe', 'windows')
    build.classify('toolchain/bin/*.dll', 'windows')
    build.classify('toolchain/lib/**', 'windows')
    build.classify('toolchain/share/**', 'windows')
    build.classify('toolchain/VERSION', 'windows')
    build.classify('toolchain/**', None)
    build.classify('runtime/wasmtime.exe', 'windows')
    build.classify('runtime/**', None)
    build.classify('tests/**', None)
    build.classify('game/test*.rpy', None)
    build.classify('game/test*.rpyc', None)
    build.classify('**/__pycache__/**', None)
    build.classify('traceback.txt', None)
    build.classify('errors.txt', None)
    build.classify('**.pyc', None)
    build.classify('**.log', None)
    build.classify('**.tmp', None)
    build.classify('game/saves/**', None)
    build.documentation('README*')

default player_name = "旅人"
default run_id = ""
default schema_version = 1
default content_version = "1.1.0"
default task_index = 1
default task_id = "tower_light_01"
default fragment = "0"
default revision = 0
default drafts = {}
default receipts = {}
default hints_used = {}
default attempts = {}
default story_lamps = 0
default result = None
default passed_hash = None
default running = False
default status_message = "等待运行"
default trial_input = 3
default hint_level = 0
default preview_lamps = []
default replay_lines = []
default replay_cursor = 0
default current_checkpoint = "opening"
default saved_draft_revision = -1
default practice = False
default code_size = 26
default reduce_motion = False
default first_answer = None
default current_answer = None
default active_seconds = 0.0
default save_notice = ""
default include_source = True

define li = Character("璃", color="#d9bb83")
define player = Character("[player_name]", color="#a2d6de")
define echo = Character("未知残响", color="#d3c7ef")

image tower = Transform("images/tower.png", xysize=(1920, 1080))
image world_far = Transform("images/world_far.png", xysize=(1920, 1080))
image li worried = "images/li_worried.png"
image li thinking = "images/li_thinking.png"
image li relieved = "images/li_relieved.png"

transform li_story:
    xalign 0.30
    yalign 1.0
    yoffset 210
    zoom 0.77

transform li_shadow:
    xalign 0.30
    yalign 1.0
    yoffset 210
    zoom 0.77
    matrixcolor BrightnessMatrix(-0.38)

transform soft_arrive:
    alpha 0.0
    linear 0.8 alpha 1.0

transform world_drift:
    zoom 1.14
    align (0.5, 0.5)
    ease 24.0 zoom 1.0

transform city_drift:
    xalign 0.72
    yalign 0.55
    zoom 0.75
    alpha 0.0
    pause 5.0
    ease 9.0 alpha 1.0 zoom 0.66

transform foreground_drift:
    xysize (2112, 1188)
    align (0.5, 0.5)
    ease 12.0 zoom 1.3 alpha 0.0

transform reveal_title:
    alpha 0.0
    pause 20.0
    linear 2.0 alpha 1.0
