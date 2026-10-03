# Isolated Windows HTML host (H0 prototype)

This prototype is separate from the production RenPy Bridge, installed plugin, official demo, and Ren'Py SDK. It opens a dedicated WinForms/WebView2 window. It is not a same-window Ren'Py displayable.

Build from the workspace with `.venv/Scripts/python.exe tools/build_html_host.py --prepare`. Downloads remain under `runtimes/html-host-dependencies`. Output is `runtimes/html-minigame-host/win-x64/HtmlMinigameHost.exe`, with a self-contained .NET runtime and `build-report.json`. No system .NET SDK/runtime installation is changed.

Pinned official inputs:

| Dependency | Version | Source / integrity |
| --- | --- | --- |
| .NET Windows x64 SDK | 10.0.401 | Microsoft builds.dotnet.microsoft.com; official metadata SHA512 `24b670ad3d923bfcf47df6c3b034152398b42f6dbc388e10d783aee1cfb5e5817d399fc0ae2a12cfa822a55e61d34830ccb15c50ef6efee437ab874bb7c79430` |
| Microsoft.Web.WebView2 NuGet | 1.0.4258.31 | Official NuGet flatcontainer; SHA256 `56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9`; restored package bytes independently match |
| WebView2 browser runtime | Detected at execution | Not bundled by this H0 host. Missing or failed runtime returns `WEBVIEW_INITIALIZATION_FAILED`; no victory is synthesized. |

Required launch arguments:

```
--package-root <immutable package directory>
--entry index.html
--session-id <unique alphanumeric/underscore/hyphen ID>
--game-id <manifest game_id>
--package-version <manifest version>
--package-hash <lowercase SHA256 of package tree>
```

Optional: `--user-data-root <directory outside package>`, `--width 1100`, `--height 720`. Launch using a hidden console process (`CREATE_NO_WINDOW`/`windowsHide`); the actual game window remains the normal visible app window. User data defaults to a unique per-session directory in LocalAppData.

Development automation requires both `--development` and `--devtools-port <1024..65535>`. Production launch leaves DevTools/context menus/accelerator keys disabled and does not request a remote-debugging port. These development ports are for local validation only and are not an IPC interface or part of the game package.

The content origin is `https://minigame-{game_id}.invalid/`. WebView2 maps this virtual HTTPS hostname to the immutable package folder. All package CSS/JavaScript/media are local. External requests, navigations away from the entry, subframe navigation, new windows, downloads, host objects, and browser device permissions are blocked. It runs with normal user privileges.

Package SHA256 is `sha256(path_utf8 + NUL + file_sha256_lowercase_ascii + LF)` accumulated over every regular package file, including `manifest.json`, in relative POSIX path UTF-8 byte order. The host independently recalculates this digest, checks launch game/version/entry against the manifest, rejects reparse points/linked ancestors and case-conflicting paths, and enforces 4,096-file/256 MiB limits. The manifest cannot contain its own tree digest. WebView user data and checkpoints belong outside the package.

The parent communicates with newline-delimited UTF-8 JSON on standard input/output. Logs and real window/DPI/screen observations go only to standard error. Every envelope has `protocol:"1.0"`, `session_id`, `event_id`, `type`, and object `payload`; messages are limited to 1 MiB and JSON depth 32. HTML communicates through WebView2's `postMessage` bridge. The host validates page URI and session before forwarding messages.

Sequence:

1. The actual page sends `ready` with `session_id:""`; the host forwards it with its launch session and adds `payload.host` runtime/host/package identity. It does not emit page-ready merely because WebView2 initialized.
2. The parent sends `init` with `{game_id,package_version,package_hash,inputs,snapshot,settings,capabilities}`, then `start`.
3. Parent `pause`/`resume` update gameplay through the page SDK. The example SDK does not emit `paused`/`resumed` protocol messages; observe its UI/state to verify pause.
4. A page `checkpoint` or `finish` is forwarded unchanged. The parent must commit the checkpoint/result before acknowledging.
5. Parent `ack` payload is `{event_id:<original event ID>,accepted:true}`. Accepted checkpoint ACKs are cached so a repeated identical page event receives the same ACK. A second distinct terminal finish is rejected.
6. After a finish ACK the host emits `closed` with `reason:"finish_confirmed"` and closes. `cancel`, input EOF, initialization failure, or process failure never imply victory. Close without a finish commit emits cancellation.

This H0 host does not replace authoritative parent validation of result/snapshot schemas, durable exactly-once commits, Ren'Py checkpoint/rollback/save integration, offline runtime installation, production signing, update policy, focus/audio restoration, or the multi-DPI/multi-monitor acceptance matrix. It does not claim to sandbox arbitrary native Ren'Py/Python content.

`node tools/probe_html_host.mjs` runs isolated real WebView2 DOM mouse inputs for puzzle win, timed win/lose, two checkpoint round trips, cancellation, and forced host termination. Reports distinguish this host-only test from Ren'Py integration and real system-audio checks.
