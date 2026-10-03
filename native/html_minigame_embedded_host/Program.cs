using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace RenPyBridge.HtmlEmbeddedHost;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        try
        {
            var options = HostOptions.Parse(args);
            options.ValidatePackage();
            Application.SetHighDpiMode(HighDpiMode.SystemAware);
            NativeParent.MatchDpi(options.ParentWindow);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using var embedded = new EmbeddedHost(options);
            using var context = new ApplicationContext();
            embedded.OnExit = context.ExitThread;
            _ = embedded.Handle;
            Application.Run(context);
            return embedded.ExitCode;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine($"[html-host] startup: {error.GetType().Name}: {error.Message}");
            Console.Out.WriteLine(new JsonObject {
                ["protocol"] = "1.0", ["session_id"] = "", ["event_id"] = Guid.NewGuid().ToString("N"),
                ["type"] = "error", ["payload"] = new JsonObject {
                    ["code"] = "HOST_STARTUP_FAILED", ["message"] = error.Message
                }
            }.ToJsonString());
            Console.Out.Flush();
            return 2;
        }
    }
}

internal sealed record HostOptions(
    string PackageRoot, string Entry, string SessionId, string GameId, string PackageVersion,
    string PackageHash, string UserDataRoot, bool Development, int? DevtoolsPort, int Width, int Height, nint ParentWindow, int ParentProcessId, double FooterFraction,
    Rectangle? Viewport, Size? LayoutClient)
{
    internal string HostName => $"minigame-{GameId}.invalid";
    internal string EntryUrl => $"https://{HostName}/" + string.Join("/", Entry.Split('/').Select(Uri.EscapeDataString));
    internal IReadOnlySet<string> PackageFiles { get; private set; } = new HashSet<string>(StringComparer.Ordinal);

    internal static HostOptions Parse(string[] args)
    {
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        var development = false;
        for (int index = 0; index < args.Length; index++)
        {
            if (args[index] == "--development") { development = true; continue; }
            if (!args[index].StartsWith("--", StringComparison.Ordinal) || index + 1 >= args.Length)
                throw new ArgumentException("All arguments require --name value, except --development.");
            if (!values.TryAdd(args[index], args[++index])) throw new ArgumentException("Duplicate argument.");
        }
        string Required(string key) => values.TryGetValue(key, out var value) && value.Length > 0
            ? value : throw new ArgumentException($"Missing {key}.");
        var allowed = new HashSet<string> {
            "--package-root", "--entry", "--session-id", "--game-id", "--package-version",
            "--package-hash", "--user-data-root", "--devtools-port", "--width", "--height", "--parent-window-handle", "--parent-process-id", "--footer-fraction",
            "--viewport-rect", "--parent-client-size"
        };
        if (values.Keys.Any(key => !allowed.Contains(key))) throw new ArgumentException("Unknown argument.");
        int? port = values.TryGetValue("--devtools-port", out var rawPort) ? int.Parse(rawPort) : null;
        if (port != null && (!development || port < 1024 || port > 65535))
            throw new ArgumentException("A development flag and a port between 1024 and 65535 are required for CDP.");
        var session = Required("--session-id");
        var game = Required("--game-id");
        if (!Regex.IsMatch(game, "^[a-z0-9][a-z0-9_-]{0,63}$")) throw new ArgumentException("Invalid game ID.");
        if (!Regex.IsMatch(session, "^[A-Za-z0-9_-]{1,128}$")) throw new ArgumentException("Invalid session ID.");
        var entry = values.GetValueOrDefault("--entry", "index.html").Replace('\\', '/');
        if (entry.StartsWith('/') || entry.Split('/').Any(part => part is "" or "." or "..") || entry.Contains(':'))
            throw new ArgumentException("Entry must be a safe relative POSIX path.");
        var width = int.Parse(values.GetValueOrDefault("--width", "1100"));
        var height = int.Parse(values.GetValueOrDefault("--height", "720"));
        if (width < 320 || width > 7680 || height < 240 || height > 4320) throw new ArgumentException("Invalid window dimensions.");
        var hash = Required("--package-hash").ToLowerInvariant();
        var footer = double.Parse(values.GetValueOrDefault("--footer-fraction", "0.1"), System.Globalization.CultureInfo.InvariantCulture);
        if (!double.IsFinite(footer) || footer < 0.05 || footer > 0.25) throw new ArgumentException("Footer fraction must be between 0.05 and 0.25.");
        if (!Regex.IsMatch(hash, "^[0-9a-f]{64}$")) throw new ArgumentException("Invalid package SHA256.");
        Rectangle? viewport = null;
        Size? layoutClient = null;
        if (values.ContainsKey("--viewport-rect") || values.ContainsKey("--parent-client-size")) {
            var rect = Required("--viewport-rect").Split(',').Select(int.Parse).ToArray();
            var client = Required("--parent-client-size").Split(',').Select(int.Parse).ToArray();
            if (rect.Length != 4 || client.Length != 2 || client.Any(value => value <= 0 || value > 32768)
                || rect[0] < 0 || rect[1] < 0 || rect[2] <= 0 || rect[3] <= 0
                || (long)rect[0] + rect[2] > client[0] || (long)rect[1] + rect[3] > client[1])
                throw new ArgumentException("Native viewport must fit the declared client area.");
            viewport = new Rectangle(rect[0], rect[1], rect[2], rect[3]);
            layoutClient = new Size(client[0], client[1]);
        }
        var udf = values.GetValueOrDefault("--user-data-root",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "RenPyBridge", "HtmlHost", "sessions", session));
        return new(Path.GetFullPath(Required("--package-root")), entry, session, game,
            Required("--package-version"), hash, Path.GetFullPath(udf), development, port, width, height, (nint)long.Parse(Required("--parent-window-handle")), int.Parse(Required("--parent-process-id")), footer, viewport, layoutClient);
    }

    internal void ValidatePackage()
    {
        NativeParent.Validate(ParentWindow, ParentProcessId);
        if (!Directory.Exists(PackageRoot)) throw new DirectoryNotFoundException("Package root does not exist.");
        AssertNoLinks(PackageRoot);
        var files = new List<(string Relative, string Full)>();
        void Walk(string directory)
        {
            foreach (var item in Directory.EnumerateFileSystemEntries(directory))
            {
                AssertNoLinks(item);
                if (Directory.Exists(item)) Walk(item);
                else files.Add((Path.GetRelativePath(PackageRoot, item).Replace('\\', '/'), item));
            }
        }
        Walk(PackageRoot);
        if (files.Select(file => file.Relative).Distinct(StringComparer.OrdinalIgnoreCase).Count() != files.Count)
            throw new InvalidDataException("Case-conflicting package paths are forbidden.");
        if (files.Count > 4096 || files.Sum(file => new FileInfo(file.Full).Length) > 256L * 1024 * 1024)
            throw new InvalidDataException("Package exceeds host limits.");
        using var digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        foreach (var file in files.OrderBy(file => file.Relative, Utf8PathComparer.Instance))
        {
            using var stream = File.OpenRead(file.Full);
            var fileHash = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
            digest.AppendData(Encoding.UTF8.GetBytes(file.Relative + "\0" + fileHash + "\n"));
        }
        var actual = Convert.ToHexString(digest.GetHashAndReset()).ToLowerInvariant();
        if (!CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(actual), Encoding.ASCII.GetBytes(PackageHash)))
            throw new InvalidDataException("Package tree SHA256 mismatch.");
        PackageFiles = files.Select(file => file.Relative).ToHashSet(StringComparer.Ordinal);
        var manifestFile = Path.Combine(PackageRoot, "manifest.json");
        var manifest = JsonNode.Parse(File.ReadAllText(manifestFile))?.AsObject()
            ?? throw new InvalidDataException("Invalid manifest JSON.");
        if (manifest["game_id"]?.GetValue<string>() != GameId || manifest["version"]?.GetValue<string>() != PackageVersion
            || manifest["entry"]?.GetValue<string>() != Entry)
            throw new InvalidDataException("Package manifest identity does not match launch arguments.");
        if (!File.Exists(Path.Combine(PackageRoot, Entry.Replace('/', Path.DirectorySeparatorChar))))
            throw new FileNotFoundException("Entry does not exist.");
        if (UserDataRoot.StartsWith(PackageRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            || string.Equals(UserDataRoot, PackageRoot, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("User data must be outside the immutable package.");
    }

    private static void AssertNoLinks(string path)
    {
        for (var cursor = Path.GetFullPath(path); !string.IsNullOrEmpty(cursor); cursor = Path.GetDirectoryName(cursor) ?? "")
            if ((File.GetAttributes(cursor) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("Package links and linked ancestors are forbidden.");
    }
}

internal sealed class Utf8PathComparer : IComparer<string>
{
    internal static readonly Utf8PathComparer Instance = new();
    public int Compare(string? left, string? right) => Encoding.UTF8.GetBytes(left ?? "").AsSpan()
        .SequenceCompareTo(Encoding.UTF8.GetBytes(right ?? ""));
}

// Stream the declared byte range without copying entire local media into RAM.
internal sealed class ResourceSlice : Stream
{
    private readonly FileStream source;
    private readonly long start;
    private readonly long length;
    internal ResourceSlice(string file, long start, long length)
    {
        source = File.OpenRead(file);
        this.start = start;
        this.length = length;
        source.Position = start;
    }
    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override long Length => length;
    public override long Position { get => source.Position - start; set => Seek(value, SeekOrigin.Begin); }
    public override int Read(byte[] buffer, int offset, int count) => source.Read(buffer, offset, (int)Math.Min(count, Math.Max(0, length - Position)));
    public override int Read(Span<byte> buffer) => source.Read(buffer[..(int)Math.Min(buffer.Length, Math.Max(0, length - Position))]);
    public override long Seek(long offset, SeekOrigin origin)
    {
        var position = origin switch { SeekOrigin.Begin => offset, SeekOrigin.Current => Position + offset, SeekOrigin.End => length + offset, _ => throw new ArgumentOutOfRangeException(nameof(origin)) };
        if (position < 0 || position > length) throw new IOException("Resource slice position is outside the response.");
        source.Position = start + position;
        return position;
    }
    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    protected override void Dispose(bool disposing) { if (disposing) source.Dispose(); base.Dispose(disposing); }
}

internal sealed class EmbeddedHost : Control
{
    private readonly HostOptions options;
    private readonly WebView2 view = new() { Dock = DockStyle.Fill };
    private readonly HashSet<string> forwardedEvents = new(StringComparer.Ordinal);
    private readonly Dictionary<string, JsonObject> pendingCritical = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (JsonObject Event, JsonObject Ack)> acknowledged = new(StringComparer.Ordinal);
    private bool initialized;
    private bool pageReady;
    private bool completed;
    private bool closing;
    private string runtimeVersion = "unavailable";
    private string? terminalEvent;
    internal int ExitCode { get; private set; }

    internal EmbeddedHost(HostOptions options)
    {
        this.options = options;
        desiredViewport = options.Viewport;
        layoutClient = options.LayoutClient;
        Controls.Add(view);
        HandleCreated += (_, _) => BeginInvoke(async () => await InitializeAsync());
        Enter += (_, _) => LogWindow("entered");
        Leave += (_, _) => LogWindow("left");
        DpiChangedAfterParent += (_, _) => LogWindow("dpi_changed");
        SizeChanged += (_, _) => LogWindow("size_changed");
        HandleDestroyed += (_, _) => OnExit?.Invoke();
        resizeTimer.Tick += (_, _) => UpdateParentBounds();
        resizeTimer.Start();
    }

    internal Action? OnExit;
    private readonly System.Windows.Forms.Timer resizeTimer = new() { Interval = 50 };
    private Rectangle? desiredViewport;
    private Size? layoutClient;
    private bool paused;
    private bool waitingLayout;
    private bool readyTreeLogged;
    protected override CreateParams CreateParams
    {
        get {
            var parameters = base.CreateParams;
            parameters.Parent = options.ParentWindow;
            parameters.Style = unchecked((int)0x56000000); // WS_CHILD/VISIBLE/CLIPCHILDREN/CLIPSIBLINGS
            parameters.ExStyle = 0x10000; // WS_EX_CONTROLPARENT, never WS_EX_APPWINDOW
            var bounds = NativeParent.ClientBounds(options.ParentWindow);
            var rect = desiredViewport ?? new Rectangle(0, 0, bounds.Width, (int)Math.Floor(bounds.Height * (1 - options.FooterFraction)));
            if (layoutClient != null && bounds != layoutClient.Value) parameters.Style &= unchecked((int)~0x10000000);
            parameters.X = rect.X; parameters.Y = rect.Y; parameters.Width = rect.Width; parameters.Height = rect.Height;
            return parameters;
        }
    }

    private void UpdateParentBounds()
    {
        if (closing) return;
        if (!NativeParent.HasIdentity(options.ParentWindow, options.ParentProcessId)) {
            Emit("error", new JsonObject { ["code"] = "PARENT_WINDOW_LOST", ["message"] = "The declared Ren'Py window no longer exists.", ["fatal"] = true });
            ExitCode = 3;
            closing = true; Close(); return;
        }
        var bounds = NativeParent.ClientBounds(options.ParentWindow);
        if (layoutClient != null && bounds != layoutClient.Value) {
            Visible = false;
            if (!waitingLayout) LogWindow("layout_pending_client_change");
            waitingLayout = true;
            return;
        }
        waitingLayout = false;
        var rect = desiredViewport ?? new Rectangle(0, 0, bounds.Width, (int)Math.Floor(bounds.Height * (1 - options.FooterFraction)));
        if (rect.Width > 0 && rect.Height > 0 && Bounds != rect) {
            SetBounds(rect.X, rect.Y, rect.Width, rect.Height);
            LogWindow("parent_resized");
        }
        Visible = !paused;
        LogReadyTree();
    }

    private void Close()
    {
        resizeTimer.Stop();
        LogWindow("child_dispose");
        view.Dispose();
        Dispose();
        OnExit?.Invoke();
    }

    private void LogWindow(string kind)
    {
        if (!IsHandleCreated) return;
        Console.Error.WriteLine("[html-embedded-window] " + NativeParent.Describe(kind, Handle, options.ParentWindow, options.ParentProcessId, view.IsHandleCreated ? view.Handle : 0,
            Bounds, Visible, paused, waitingLayout));
        Console.Error.Flush();
    }

    private void LogReadyTree()
    {
        if (pageReady && !readyTreeLogged && Visible && !waitingLayout && NativeParent.HasVisibleWebRenderer(options.ParentWindow)) {
            readyTreeLogged = true;
            LogWindow("page_ready");
        }
    }

    private async Task InitializeAsync()
    {
        try
        {
            Directory.CreateDirectory(options.UserDataRoot);
            var arguments = options.DevtoolsPort is int port ? $"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1" : "";
            var environment = await CoreWebView2Environment.CreateAsync(null, options.UserDataRoot,
                new CoreWebView2EnvironmentOptions(arguments));
            await view.EnsureCoreWebView2Async(environment);
            runtimeVersion = environment.BrowserVersionString;
            var core = view.CoreWebView2;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = true;
            core.Settings.AreDevToolsEnabled = options.Development;
            core.Settings.AreDefaultContextMenusEnabled = options.Development;
            core.Settings.AreBrowserAcceleratorKeysEnabled = options.Development;
            core.Settings.AreDefaultScriptDialogsEnabled = false;
            core.Settings.IsBuiltInErrorPageEnabled = false;
            // Resolve the virtual HTTPS origin here, rather than folder mapping:
            // mapped resources bypass WebResourceRequested and therefore cannot
            // receive the host's mandatory CSP headers (including worker scripts).
            core.NavigationStarting += (_, args) => { if (!IsEntry(args.Uri)) args.Cancel = true; };
            core.FrameNavigationStarting += (_, args) => args.Cancel = true;
            core.NewWindowRequested += (_, args) => args.Handled = true;
            core.DownloadStarting += (_, args) => args.Cancel = true;
            core.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All,
                CoreWebView2WebResourceRequestSourceKinds.All);
            core.WebResourceRequested += (_, args) => {
                try {
                if (!IsLocal(args.Request.Uri)) {
                    args.Response = environment.CreateWebResourceResponse(
                        new MemoryStream(Encoding.UTF8.GetBytes("Network access is disabled.")), 403, "Forbidden", "Content-Type: text/plain\r\n");
                    Console.Error.WriteLine("[html-network-denied] " + args.Request.Uri);
                    return;
                }
                // WebResourceRequested does not intercept WebSocket handshakes.
                // Apply a browser-enforced policy to both documents and worker
                // scripts; regular workers do not inherit the document policy.
                var uri = new Uri(args.Request.Uri);
                var relative = Uri.UnescapeDataString(uri.AbsolutePath).TrimStart('/');
                if (!options.PackageFiles.Contains(relative)) {
                    args.Response = environment.CreateWebResourceResponse(Stream.Null, 404, "Not Found", "Content-Type: text/plain\r\n");
                    return;
                }
                var extension = Path.GetExtension(relative).ToLowerInvariant();
                var file = Path.GetFullPath(Path.Combine(options.PackageRoot, relative.Replace('/', Path.DirectorySeparatorChar)));
                if (!file.StartsWith(options.PackageRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                    || !File.Exists(file)) {
                    args.Response = environment.CreateWebResourceResponse(Stream.Null, 404, "Not Found", "Content-Type: text/plain\r\n");
                    return;
                }
                var mime = relative == options.Entry ? "text/html" : MimeType(extension);
                var policy = $"default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
                    + "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data: blob:; "
                    + "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; "
                    + "frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'";
                var headers = $"Content-Type: {mime}\r\nContent-Security-Policy: {policy}\r\n"
                    + "X-Content-Type-Options: nosniff\r\nAccept-Ranges: bytes\r\nCache-Control: no-store\r\n";
                var length = new FileInfo(file).Length;
                long start = 0, end = length - 1;
                var partial = false;
                if (args.Request.Headers.Contains("Range")) {
                    var range = Regex.Match(args.Request.Headers.GetHeader("Range"), @"^bytes=(\d*)-(\d*)$");
                    if (range.Success && (range.Groups[1].Length > 0 || range.Groups[2].Length > 0)) {
                        try {
                            if (range.Groups[1].Length == 0) start = Math.Max(0, length - long.Parse(range.Groups[2].Value));
                            else {
                                start = long.Parse(range.Groups[1].Value);
                                if (range.Groups[2].Length > 0) end = Math.Min(end, long.Parse(range.Groups[2].Value));
                            }
                            partial = true;
                        } catch (OverflowException) { start = length; }
                    }
                    if (start >= length || end < start) {
                        args.Response = environment.CreateWebResourceResponse(Stream.Null, 416, "Range Not Satisfiable",
                            headers + $"Content-Range: bytes */{length}\r\n");
                        return;
                    }
                }
                var count = Math.Max(0, end - start + 1);
                if (partial) headers += $"Content-Range: bytes {start}-{end}/{length}\r\n";
                args.Response = environment.CreateWebResourceResponse(new ResourceSlice(file, start, count),
                    partial ? 206 : 200, partial ? "Partial Content" : "OK", headers + $"Content-Length: {count}\r\n");
                } catch (Exception error) when (error is ArgumentException or IOException or UnauthorizedAccessException) {
                    args.Response = environment.CreateWebResourceResponse(Stream.Null, 404, "Not Found", "Content-Type: text/plain\r\n");
                    Console.Error.WriteLine("[html-resource-denied] " + error.GetType().Name);
                }
            };
            core.WebMessageReceived += OnWebMessage;
            // WebRTC ICE/STUN traffic is outside WebResourceRequested and CSP
            // connect-src. Offline packages have no peer-connection capability.
            // Run before every document's scripts, including any frame realm.
            await core.AddScriptToExecuteOnDocumentCreatedAsync("(() => { for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection']) "
                + "Object.defineProperty(globalThis, name, {value: undefined, writable: false, configurable: false}); })();");
            // A service worker must not synthesize an entry response without the
            // host's CSP. This internal browser control opens no debugging port.
            await core.CallDevToolsProtocolMethodAsync("Network.enable", "{}");
            await core.CallDevToolsProtocolMethodAsync("Network.setBypassServiceWorker", "{\"bypass\":true}");
            core.ProcessFailed += (_, args) => Fail("WEBVIEW_PROCESS_FAILED", args.ProcessFailedKind.ToString());
            core.NavigationCompleted += (_, args) => {
                if (!args.IsSuccess) Fail("NAVIGATION_FAILED", args.WebErrorStatus.ToString());
            };
            Console.Error.WriteLine($"[html-host] runtime={runtimeVersion}; origin=https://{options.HostName}; development={options.Development}");
            _ = Task.Run(ReadParentAsync);
            UpdateParentBounds();
            LogWindow("webview_initialized");
            core.Navigate(options.EntryUrl);
        }
        catch (Exception error) { Fail("WEBVIEW_INITIALIZATION_FAILED", error.Message); }
    }

    private bool IsLocal(string url) => Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && uri.Host == options.HostName && uri.IsDefaultPort && uri.UserInfo.Length == 0;
    private bool IsEntry(string url) => IsLocal(url) && Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && Uri.UnescapeDataString(uri.AbsolutePath).TrimStart('/') == options.Entry;

    private static string MimeType(string extension) => extension switch {
        ".html" or ".htm" => "text/html", ".js" or ".mjs" => "application/javascript",
        ".css" => "text/css", ".json" or ".map" => "application/json", ".txt" => "text/plain",
        ".svg" => "image/svg+xml", ".png" => "image/png", ".jpg" or ".jpeg" => "image/jpeg",
        ".gif" => "image/gif", ".webp" => "image/webp", ".avif" => "image/avif", ".ico" => "image/x-icon",
        ".wav" => "audio/wav", ".mp3" => "audio/mpeg", ".ogg" => "audio/ogg", ".m4a" => "audio/mp4",
        ".mp4" => "video/mp4", ".webm" => "video/webm", ".woff" => "font/woff", ".woff2" => "font/woff2",
        ".ttf" => "font/ttf", ".otf" => "font/otf", ".wasm" => "application/wasm",
        _ => "application/octet-stream"
    };

    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            if (!IsEntry(args.Source)) throw new InvalidDataException("Unexpected web message origin.");
            var json = args.WebMessageAsJson;
            var message = ParseEnvelope(json);
            var type = message["type"]!.GetValue<string>();
            var session = message["session_id"]?.GetValue<string>() ?? "";
            if (type == "ready" && !pageReady && session.Length == 0)
            {
                pageReady = true;
                message["session_id"] = options.SessionId;
                var payload = message["payload"]!.AsObject();
                payload["host"] = new JsonObject {
                    ["version"] = "0.2.0-embedded", ["webview2_runtime"] = runtimeVersion,
                    ["game_id"] = options.GameId, ["package_version"] = options.PackageVersion,
                    ["package_hash"] = options.PackageHash, ["development"] = options.Development
                };
                LogReadyTree();
                view.Focus();
                Forward(message);
                return;
            }
            if (!initialized || session != options.SessionId) throw new InvalidDataException("Unexpected web message session.");
            if (type is not ("checkpoint" or "finish" or "error" or "paused" or "resumed"))
                throw new InvalidDataException("Unsupported web message type.");
            var eventId = message["event_id"]!.GetValue<string>();
            if (acknowledged.TryGetValue(eventId, out var confirmed))
            {
                if (!JsonNode.DeepEquals(confirmed.Event, message)) throw new InvalidDataException("Repeated event has changed payload.");
                view.CoreWebView2.PostWebMessageAsJson(confirmed.Ack.ToJsonString());
                return;
            }
            if (pendingCritical.TryGetValue(eventId, out var original))
            {
                if (!JsonNode.DeepEquals(original, message)) throw new InvalidDataException("Repeated event has changed payload.");
                return;
            }
            if (type == "finish")
            {
                if (terminalEvent != null && terminalEvent != eventId) throw new InvalidDataException("A terminal event already exists.");
                terminalEvent = eventId;
            }
            if (forwardedEvents.Count >= 8192) throw new InvalidDataException("Session event limit exceeded.");
            if (!forwardedEvents.Add(eventId)) return;
            if (type is "finish" or "checkpoint") pendingCritical[eventId] = message.DeepClone().AsObject();
            Forward(message);
        }
        catch (Exception error) { Emit("error", new JsonObject { ["code"] = "WEB_MESSAGE_REJECTED", ["message"] = error.Message }); }
    }

    private async Task ReadParentAsync()
    {
        try
        {
            while (!closing)
            {
                var line = await Console.In.ReadLineAsync();
                if (line == null)
                {
                    if (!closing && IsHandleCreated) BeginInvoke(() => {
                        if (!completed) Emit("cancel", new JsonObject { ["reason"] = "parent_disconnected" });
                        closing = true; Close();
                    });
                    return;
                }
                if (line.Length > 1024 * 1024) throw new InvalidDataException("Parent message exceeds 1 MiB.");
                var message = ParseEnvelope(line);
                if (!closing && IsHandleCreated) BeginInvoke(() => OnParentMessage(message));
            }
        }
        catch (Exception error)
        {
            if (!closing && IsHandleCreated) BeginInvoke(() => Fail("PARENT_CHANNEL_FAILED", error.Message));
        }
    }

    private void OnParentMessage(JsonObject message)
    {
        try
        {
            if (message["session_id"]!.GetValue<string>() != options.SessionId) throw new InvalidDataException("Parent session mismatch.");
            var type = message["type"]!.GetValue<string>();
            var payload = message["payload"]!.AsObject();
            if (type == "layout") {
                var client = payload["parent_client"]?.AsArray() ?? throw new InvalidDataException("Layout client is missing.");
                var rect = payload["viewport"]?.AsArray() ?? throw new InvalidDataException("Layout viewport is missing.");
                var footer = payload["footer"]?.AsArray() ?? throw new InvalidDataException("Layout footer is missing.");
                if (client.Count != 2 || rect.Count != 4 || footer.Count != 4) throw new InvalidDataException("Invalid layout dimensions.");
                var width = client[0]!.GetValue<int>(); var height = client[1]!.GetValue<int>();
                var viewport = new Rectangle(rect[0]!.GetValue<int>(), rect[1]!.GetValue<int>(), rect[2]!.GetValue<int>(), rect[3]!.GetValue<int>());
                var storyFooter = new Rectangle(footer[0]!.GetValue<int>(), footer[1]!.GetValue<int>(), footer[2]!.GetValue<int>(), footer[3]!.GetValue<int>());
                if (width <= 0 || height <= 0 || width > 32768 || height > 32768 || viewport.X < 0 || viewport.Y < 0
                    || viewport.Width <= 0 || viewport.Height <= 0 || viewport.Right > width || viewport.Bottom > height
                    || storyFooter.X != viewport.X || storyFooter.Width != viewport.Width || storyFooter.Y != viewport.Bottom
                    || storyFooter.Height <= 0 || storyFooter.Bottom > height)
                    throw new InvalidDataException("Layout viewport and toolbar must fit the client.");
                layoutClient = new Size(width, height); desiredViewport = viewport;
                UpdateParentBounds(); LogWindow("layout_applied");
                return; // native geometry is never forwarded to untrusted page code
            }
            if (type == "init")
            {
                if (!pageReady || initialized) throw new InvalidDataException("Invalid init ordering.");
                if (payload["game_id"]?.GetValue<string>() != options.GameId
                    || payload["package_version"]?.GetValue<string>() != options.PackageVersion
                    || payload["package_hash"]?.GetValue<string>() != options.PackageHash)
                    throw new InvalidDataException("Init package identity mismatch.");
                initialized = true;
            }
            else if (type == "cancel")
            {
                view.CoreWebView2.PostWebMessageAsJson(message.ToJsonString());
                Emit("cancel", new JsonObject { ["reason"] = payload["reason"]?.DeepClone() ?? JsonValue.Create("parent_cancelled") });
                closing = true; Close(); return;
            }
            else if (!initialized || type is not ("start" or "pause" or "resume" or "ack" or "settings"))
                throw new InvalidDataException("Unsupported parent message or ordering.");
            if (type == "ack")
            {
                var eventId = payload["event_id"]?.GetValue<string>() ?? throw new InvalidDataException("Ack event ID is missing.");
                if (!pendingCritical.TryGetValue(eventId, out var pending)) throw new InvalidDataException("Ack refers to unknown event.");
                if (payload["accepted"]?.GetValue<bool>() == true)
                {
                    view.CoreWebView2.PostWebMessageAsJson(message.ToJsonString());
                    acknowledged[eventId] = (pending.DeepClone().AsObject(), message.DeepClone().AsObject());
                    pendingCritical.Remove(eventId);
                    if (pending["type"]!.GetValue<string>() == "finish")
                    {
                        completed = true;
                        Emit("closed", new JsonObject { ["reason"] = "finish_confirmed", ["event_id"] = eventId });
                        closing = true; Close();
                    }
                    return;
                }
            }
            view.CoreWebView2.PostWebMessageAsJson(message.ToJsonString());
            if (type == "pause") {
                paused = true;
                Visible = false;
                LogWindow("paused_child_hidden");
            }
            else if (type == "resume") {
                paused = false;
                UpdateParentBounds();
                if (Visible) view.Focus();
                LogWindow("resumed_child_visible");
            }
        }
        catch (Exception error) { Emit("error", new JsonObject { ["code"] = "PARENT_MESSAGE_REJECTED", ["message"] = error.Message }); }
    }

    private static JsonObject ParseEnvelope(string json)
    {
        if (Encoding.UTF8.GetByteCount(json) > 1024 * 1024) throw new InvalidDataException("Message exceeds 1 MiB.");
        var message = JsonNode.Parse(json, new JsonNodeOptions(), new JsonDocumentOptions { MaxDepth = 32 })?.AsObject()
            ?? throw new InvalidDataException("Message is not an object.");
        if (message["protocol"]?.GetValue<string>() != "1.0" || message["payload"] is not JsonObject
            || string.IsNullOrEmpty(message["type"]?.GetValue<string>())
            || !Regex.IsMatch(message["event_id"]?.GetValue<string>() ?? "", "^[A-Za-z0-9_-]{1,128}$"))
            throw new InvalidDataException("Invalid protocol envelope.");
        return message;
    }

    private void Forward(JsonObject message)
    {
        Console.Out.WriteLine(message.ToJsonString());
        Console.Out.Flush();
    }

    private void Emit(string type, JsonObject payload) => Forward(new JsonObject {
        ["protocol"] = "1.0", ["session_id"] = options.SessionId,
        ["event_id"] = Guid.NewGuid().ToString("N"), ["type"] = type, ["payload"] = payload
    });

    private void Fail(string code, string message)
    {
        if (closing) return;
        Console.Error.WriteLine($"[html-host] {code}: {message}");
        Emit("error", new JsonObject { ["code"] = code, ["message"] = message });
        ExitCode = 3; closing = true; Close();
    }
}
