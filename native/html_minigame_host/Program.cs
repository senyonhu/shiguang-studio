using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace RenPyBridge.HtmlHost;

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
            Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using var form = new HostForm(options);
            Application.Run(form);
            return form.ExitCode;
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
    string PackageHash, string UserDataRoot, bool Development, int? DevtoolsPort, int Width, int Height)
{
    internal string HostName => $"minigame-{GameId}.invalid";
    internal string EntryUrl => $"https://{HostName}/" + string.Join("/", Entry.Split('/').Select(Uri.EscapeDataString));

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
            "--package-hash", "--user-data-root", "--devtools-port", "--width", "--height"
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
        if (!Regex.IsMatch(hash, "^[0-9a-f]{64}$")) throw new ArgumentException("Invalid package SHA256.");
        var udf = values.GetValueOrDefault("--user-data-root",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "RenPyBridge", "HtmlHost", "sessions", session));
        return new(Path.GetFullPath(Required("--package-root")), entry, session, game,
            Required("--package-version"), hash, Path.GetFullPath(udf), development, port, width, height);
    }

    internal void ValidatePackage()
    {
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

internal sealed class HostForm : Form
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

    internal HostForm(HostOptions options)
    {
        this.options = options;
        Text = $"小游戏 · {options.GameId}";
        ClientSize = new Size(options.Width, options.Height);
        MinimumSize = new Size(360, 280);
        StartPosition = FormStartPosition.CenterScreen;
        Controls.Add(view);
        Shown += async (_, _) => await InitializeAsync();
        Activated += (_, _) => LogWindow("activated");
        Deactivate += (_, _) => LogWindow("deactivated");
        DpiChanged += (_, _) => LogWindow("dpi_changed");
        SizeChanged += (_, _) => LogWindow("size_changed");
        Move += (_, _) => LogWindow("moved");
        FormClosing += (_, _) => {
            if (!closing && !completed) Emit("cancel", new JsonObject { ["reason"] = "window_closed" });
            closing = true;
        };
    }

    private void LogWindow(string kind)
    {
        if (!IsHandleCreated) return;
        var screen = Screen.FromControl(this);
        Console.Error.WriteLine("[html-host-window] " + JsonSerializer.Serialize(new {
            kind, at_utc = DateTimeOffset.UtcNow.ToString("O"), device_dpi = DeviceDpi,
            active = Form.ActiveForm == this, state = WindowState.ToString(),
            bounds = new { Bounds.X, Bounds.Y, Bounds.Width, Bounds.Height },
            client_size = new { ClientSize.Width, ClientSize.Height },
            screen = new { screen.DeviceName, screen.Primary,
                bounds = new { screen.Bounds.X, screen.Bounds.Y, screen.Bounds.Width, screen.Bounds.Height },
                working_area = new { screen.WorkingArea.X, screen.WorkingArea.Y, screen.WorkingArea.Width, screen.WorkingArea.Height } },
            screen_count = Screen.AllScreens.Length
        }));
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
            core.SetVirtualHostNameToFolderMapping(options.HostName, options.PackageRoot, CoreWebView2HostResourceAccessKind.DenyCors);
            core.NavigationStarting += (_, args) => { if (!IsEntry(args.Uri)) args.Cancel = true; };
            core.FrameNavigationStarting += (_, args) => args.Cancel = true;
            core.NewWindowRequested += (_, args) => args.Handled = true;
            core.DownloadStarting += (_, args) => args.Cancel = true;
            core.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += (_, args) => {
                if (!IsLocal(args.Request.Uri)) args.Response = environment.CreateWebResourceResponse(
                    new MemoryStream(Encoding.UTF8.GetBytes("Network access is disabled.")), 403, "Forbidden", "Content-Type: text/plain\r\n");
            };
            core.WebMessageReceived += OnWebMessage;
            core.ProcessFailed += (_, args) => Fail("WEBVIEW_PROCESS_FAILED", args.ProcessFailedKind.ToString());
            core.NavigationCompleted += (_, args) => {
                if (!args.IsSuccess) Fail("NAVIGATION_FAILED", args.WebErrorStatus.ToString());
            };
            Console.Error.WriteLine($"[html-host] runtime={runtimeVersion}; origin=https://{options.HostName}; development={options.Development}");
            _ = Task.Run(ReadParentAsync);
            core.Navigate(options.EntryUrl);
        }
        catch (Exception error) { Fail("WEBVIEW_INITIALIZATION_FAILED", error.Message); }
    }

    private bool IsLocal(string url) => Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && uri.Host == options.HostName && uri.IsDefaultPort && uri.UserInfo.Length == 0;
    private bool IsEntry(string url) => IsLocal(url) && Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && Uri.UnescapeDataString(uri.AbsolutePath).TrimStart('/') == options.Entry;

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
                    ["version"] = "0.1.0", ["webview2_runtime"] = runtimeVersion,
                    ["game_id"] = options.GameId, ["package_version"] = options.PackageVersion,
                    ["package_hash"] = options.PackageHash, ["development"] = options.Development
                };
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
