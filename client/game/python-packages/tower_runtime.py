"""Non-saveable runtime handles stay outside Ren'Py store."""
import time
import uuid
from local_runner import Runner
from local_runner.progress import Progress

runner = None
progress = None
session = uuid.uuid4().hex
pending = None
edited_at = 0.0
dirty = False
draft_offer = None
last_tick = time.monotonic()
health = {"available": False, "message": "正在检查本地组件", "details": ""}

def setup(base, savedir):
    global runner, progress, health
    if runner:
        runner.cancel_all()
    runner = Runner(base)
    progress = Progress(savedir)
    health = runner.check_runtime()

def new_session():
    global session, pending, last_tick
    if runner:
        runner.cancel_all()
    session = uuid.uuid4().hex
    pending = None
    last_tick = time.monotonic()

def shutdown():
    if runner:
        runner.cancel_all()
