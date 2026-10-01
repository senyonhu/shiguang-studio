init -10 python:
    import hashlib
    import time
    import uuid
    import tower_runtime as rt
    from local_runner import build_source, source_hash, load_task

    def escaped(value):
        return str(value).replace("{", "{{")

    def current_task():
        return load_task(store.task_id)

    def source_preview():
        try:
            return build_source(store.task_id, store.fragment)
        except ValueError as error:
            return "// " + str(error) + "\n\n" + store.fragment

    def log_event(kind, **data):
        if not rt.progress or not store.run_id:
            return
        try:
            rt.progress.event(store.run_id, kind, practice=store.practice, **data)
        except OSError as error:
            store.save_notice = "记录写入失败：" + str(error)

    def flush_draft(before_reset=False):
        if not getattr(store, "run_id", "") or not rt.progress:
            return False
        try:
            rt.progress.draft(store.run_id + ("-practice" if store.practice else ""), store.task_id, store.fragment, store.revision, before_reset)
            store.saved_draft_revision = store.revision
            rt.dirty = False
            return True
        except OSError as error:
            store.save_notice = "草稿写入失败：" + str(error)
            return False

    def edit_fragment(text):
        if store.running or text == store.fragment:
            return
        store.fragment = text
        store.revision += 1
        store.drafts[store.task_id] = text
        store.passed_hash = None
        store.result = None
        store.preview_lamps = []
        store.replay_lines = []
        store.replay_cursor = 0
        store.status_message = "代码已修改，需重新验证"
        rt.dirty = True
        rt.edited_at = time.monotonic()

    class CodeInput(InputValue):
        def get_text(self):
            return store.fragment
        def set_text(self, text):
            edit_fragment(text)
        def get_editable(self):
            return not store.running
        def enter(self):
            return None

    class CodeEditor(renpy.display.behavior.Input):
        def event(self, ev, x, y, st):
            if self.editable and self.copypaste and renpy.display.behavior.map_event(ev, "input_copy"):
                import renpy.pygame as pygame
                try:
                    pygame.scrap.put(pygame.scrap.SCRAP_TEXT, self.content.encode("utf-8"))
                except pygame.error:
                    renpy.notify("剪贴板暂时不可用，请稍后重试。")
                raise renpy.IgnoreEvent()
            if self.editable and self.copypaste and renpy.display.behavior.map_event(ev, "input_paste"):
                import renpy.pygame as pygame
                try:
                    raw = pygame.scrap.get(pygame.scrap.SCRAP_TEXT)
                except pygame.error:
                    renpy.notify("剪贴板暂时不可用，请稍后重试。")
                    raise renpy.IgnoreEvent()
                if raw:
                    value = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
                    value = "".join(c for c in value if ord(c)>=32 or (c=="\n" and self.multiline))
                    if self.allow:
                        value = "".join(c for c in value if c in self.allow)
                    if self.length:
                        value = value[:max(0,self.length-len(self.content))]
                    pos = self.caret_pos
                    self.caret_pos += len(value)
                    self.update_text(self.content[:pos]+value+self.content[pos:],self.editable)
                    renpy.redraw(self,0)
                raise renpy.IgnoreEvent()
            return super(CodeEditor,self).event(ev,x,y,st)

    def prepare_task(number):
        rt.new_session()
        store.task_index = number
        store.task_id = "tower_light_%02d" % number
        if number == 3 and store.task_id not in store.drafts:
            condition = store.drafts.get("tower_light_02", "i <= n")
            store.drafts[store.task_id] = 'for (int i = 1; ' + condition + '; i++) {\n    printf("%d\\n", i);\n}'
        store.fragment = store.drafts.get(store.task_id, current_task()["initial_fragment"])
        store.revision = 0
        store.running = False
        store.result = None
        store.passed_hash = None
        store.preview_lamps = []
        store.replay_lines = []
        store.replay_cursor = 0
        store.trial_input = 1 if number == 1 else 3
        store.hint_level = store.hints_used.get(store.task_id, 0)
        store.status_message = "先运行初始术式，观察它输出了什么。"
        store.current_checkpoint = store.task_id
        flush_draft()
        log_event("task_enter", task_id=store.task_id)

    def submit(mode):
        if store.running:
            return
        flush_draft()
        try:
            source = build_source(store.task_id, store.fragment)
            request = dict(run_id=store.run_id, client_request_id=uuid.uuid4().hex, task_id=store.task_id, task_revision=current_task()["task_revision"], mode=mode, editable_fragment=store.fragment, source_hash=source_hash(source), trial_input=store.trial_input)
            sid = rt.runner.start_submission(request)
        except (ValueError, OSError) as error:
            store.status_message = str(error)
            store.passed_hash = None
            return
        rt.pending = dict(session=rt.session, task_id=store.task_id, task_revision=request["task_revision"], source_hash=request["source_hash"], revision=store.revision, sid=sid)
        store.running = True
        store.passed_hash = None
        store.result = None
        store.preview_lamps = []
        store.replay_lines = []
        store.replay_cursor = 0
        store.status_message = "术式正在运行…"
        store.attempts[store.task_id] = store.attempts.get(store.task_id, 0) + 1
        log_event("run_requested", task_id=store.task_id, submission_id=sid, mode=mode, source_hash=request["source_hash"], fragment=store.fragment)

    def cancel_run():
        if rt.pending:
            rt.runner.cancel_submission(rt.pending["sid"])
            store.status_message = "正在终止进程，请稍候…"

    def poll_run():
        pending = rt.pending
        if not pending:
            return
        value = rt.runner.poll_submission(pending["sid"])
        if value["status"] != "finished":
            store.status_message = value.get("phase", "正在运行")
            return
        result = value["result"]
        rt.pending = None
        store.running = False
        if pending["session"] != rt.session or pending["task_id"] != store.task_id or pending["revision"] != store.revision or pending["task_revision"] != current_task()["task_revision"]:
            return
        if pending["source_hash"] != source_hash(build_source(store.task_id, store.fragment)) or result["submission_id"] != pending["sid"]:
            return
        store.result = result
        statuses = {"passed": "当前输入通过" if store.task_index < 3 or result["mode"] == "trial" else "四条回路通过 / 同一份源码", "wrong_answer": "程序正常结束，但尚未完成目标", "compile_error": "这一行暂时无法编译，请查看原始错误", "timeout": "程序未在限制内结束，请检查循环条件", "output_limit": "输出过多，已终止；下方是截断结果", "runtime_error": "程序异常结束，部分输出不表示通过", "cancelled": "运行已取消，代码已保留", "unavailable": "本地运行组件无法启动，代码已保留"}
        store.status_message = statuses.get(result["status"], result["status"])
        if result["passed"] and (store.task_index < 3 or result["mode"] == "verify"):
            store.passed_hash = result["source_hash"]
        case = result["cases"][-1] if result["cases"] else None
        if case:
            store.replay_lines = case["stdout"].replace("\r\n", "\n").splitlines()[:64]
        log_event("run_finished", task_id=store.task_id, submission_id=result["submission_id"], status=result["status"], source_hash=result["source_hash"], cases=result["cases"])
        renpy.play("audio/feedback.wav" if result["passed"] else "audio/tick.wav")

    def replay_step():
        if store.replay_cursor >= len(store.replay_lines):
            return
        line = store.replay_lines[store.replay_cursor]
        store.replay_cursor += 1
        if line in ("1", "2", "3", "4", "5", "6"):
            number = int(line)
            if number not in store.preview_lamps:
                store.preview_lamps.append(number)
            renpy.play("audio/lamp.wav")

    def runtime_tick():
        now = time.monotonic()
        delta = min(0.5, max(0, now - rt.last_tick))
        rt.last_tick = now
        if store.run_id and not store.running and not renpy.context()._menu and renpy.display.interface.keyboard_focused and not renpy.get_screen("task_menu"):
            store.active_seconds += delta
        if rt.dirty and now - rt.edited_at > 1.0:
            flush_draft()
        if store.running:
            poll_run()

    def open_hint():
        store.hint_level = min(3, store.hint_level + 1)
        store.hints_used[store.task_id] = max(store.hint_level, store.hints_used.get(store.task_id, 0))
        log_event("hint_opened", task_id=store.task_id, level=store.hint_level)

    def reset_fragment():
        flush_draft(before_reset=True)
        edit_fragment(current_task()["initial_fragment"])
        flush_draft()

    def can_continue():
        if not store.passed_hash or store.running:
            return False
        try:
            return store.passed_hash == source_hash(build_source(store.task_id, store.fragment))
        except ValueError:
            return False

    def commit_task():
        if not can_continue():
            return False
        renpy.block_rollback()
        receipt = dict(task_id=store.task_id, task_revision=current_task()["task_revision"], source_hash=store.passed_hash, submission_id=store.result["submission_id"], fragment=store.fragment, passed=True, completed_at=time.time())
        if not store.practice:
            if not rt.progress:
                store.save_notice = "用户数据目录不可写，无法保存通过收据；请修复目录权限后重试。"
                return False
            try:
                rt.progress.receipt(store.run_id, store.task_id, receipt)
            except OSError as error:
                store.save_notice = "通过收据保存失败：" + str(error)
                return False
            first = store.task_id not in store.receipts
            store.receipts[store.task_id] = receipt
            if first:
                log_event("task_completed", task_id=store.task_id, source_hash=store.passed_hash, event_id=store.run_id+":"+store.task_id+":completed")
            store.story_lamps = (1, 3, 6)[store.task_index - 1]
        store.drafts[store.task_id] = store.fragment
        return True

    def save_checkpoint(name="checkpoint"):
        flush_draft()
        try:
            renpy.save(("practice-" if store.practice else "") + name, extra_info=store.current_checkpoint)
            store.save_notice = "进度已保存"
            return True
        except Exception as error:
            store.save_notice = "存档写入失败：" + str(error)
            return False

    def resume_loaded():
        rt.new_session()
        store.running = False
        store.preview_lamps = []
        store.replay_lines = []
        store.replay_cursor = 0
        if store.schema_version != 1 or store.content_version != "1.1.0":
            store.passed_hash = None
            store.status_message = "存档内容版本不兼容，请保留旧档并重新开始"
            return
        if store.passed_hash:
            valid = store.result and store.result.get("passed") and store.result.get("task_revision") == current_task()["task_revision"] and store.result.get("source_hash") == source_hash(build_source(store.task_id, store.fragment))
            if not valid:
                store.passed_hash = None
        if not store.passed_hash:
            store.status_message = "已恢复草稿；未完成的运行需要重新验证"
        try:
            latest = rt.progress.latest(store.run_id + ("-practice" if store.practice else ""), store.task_id)
            if latest and latest["fragment"] != store.fragment and latest.get("revision", -1) > store.saved_draft_revision:
                rt.draft_offer = latest
                renpy.show_screen("recover_draft")
        except (OSError, ValueError):
            pass
        log_event("save_loaded", task_id=store.task_id)

    def recover_latest():
        if rt.draft_offer:
            edit_fragment(rt.draft_offer["fragment"])
            flush_draft()
            rt.draft_offer = None

    def export_record():
        if not rt.progress:
            store.save_notice = "用户数据目录不可写，无法导出记录。"
            return
        summary = dict(completed_tasks=list(store.receipts.keys()), attempts=dict(store.attempts), hints=dict(store.hints_used), first_comprehension_answer=store.first_answer, current_comprehension_answer=store.current_answer, active_seconds=round(store.active_seconds), time_note="排除失焦、菜单和运行等待；无法可靠识别窗口内挂机时间。")
        try:
            path = rt.progress.export(store.run_id, summary, store.include_source)
            store.save_notice = "记录已导出：" + path
        except OSError as error:
            store.save_notice = "导出失败：" + str(error)

    def answer_question(answer):
        if store.first_answer is None:
            store.first_answer = answer
        store.current_answer = answer
        log_event("comprehension_answer", answer=answer, first=store.first_answer)

    def shutdown_game():
        flush_draft()
        rt.shutdown()

    def boot_runtime():
        # Ren'Py normally mirrors saves into game/saves. Use only its stable
        # user location so a read-only game installation is supported.
        target = os.path.normcase(os.path.abspath(config.savedir))
        renpy.loadsave.location.locations = [loc for loc in renpy.loadsave.location.locations if os.path.normcase(os.path.abspath(loc.directory)) == target]
        try:
            rt.setup(config.basedir, os.path.join(config.savedir, "learning"))
        except OSError as error:
            store.save_notice = "用户数据目录不可写：" + str(error)
            from local_runner import Runner
            rt.runner = Runner(config.basedir)
        rt.last_tick = time.monotonic()

    def refresh_runtime():
        rt.health = rt.runner.check_runtime()

    config.start_callbacks.append(boot_runtime)
    config.quit_callbacks.append(shutdown_game)
    config.after_load_callbacks.append(resume_loaded)
    config.overlay_screens.append("runtime_clock")

screen runtime_clock():
    timer 0.25 repeat True action Function(runtime_tick)
