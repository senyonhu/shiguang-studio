using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace RenPyBridge.HtmlEmbeddedHost;

internal static class NativeParent
{
    [DllImport("user32", SetLastError = true)] internal static extern bool IsWindow(nint hwnd);
    [DllImport("user32")] private static extern bool IsWindowVisible(nint hwnd);
    [DllImport("user32")] private static extern uint GetWindowThreadProcessId(nint hwnd, out uint pid);
    [DllImport("user32")] private static extern nint GetAncestor(nint hwnd, uint flags);
    [DllImport("user32")] private static extern nint GetWindowDpiAwarenessContext(nint hwnd);
    [DllImport("user32")] private static extern int GetAwarenessFromDpiAwarenessContext(nint context);
    [DllImport("user32", SetLastError = true)] private static extern nint SetThreadDpiAwarenessContext(nint context);
    [DllImport("user32")] private static extern bool AreDpiAwarenessContextsEqual(nint left, nint right);
    [DllImport("user32")] private static extern uint GetDpiForWindow(nint hwnd);
    [DllImport("user32", CharSet = CharSet.Unicode)] private static extern int GetClassName(nint hwnd, StringBuilder name, int maximum);
    [DllImport("user32")] private static extern bool GetClientRect(nint hwnd, out Rect rect);
    [DllImport("user32", EntryPoint = "GetWindowLongPtrW")] private static extern nint GetWindowLongPtr(nint hwnd, int index);
    [DllImport("user32")] private static extern bool EnumWindows(EnumCallback callback, nint parameter);
    [DllImport("user32")] private static extern bool EnumChildWindows(nint parent, EnumCallback callback, nint parameter);
    private delegate bool EnumCallback(nint hwnd, nint parameter);
    [StructLayout(LayoutKind.Sequential)] private struct Rect { internal int Left, Top, Right, Bottom; }

    internal static void Validate(nint parent, int expectedPid)
    {
        if (!HasIdentity(parent, expectedPid)) throw new ArgumentException("Parent HWND must be the live declared Ren'Py top-level window.");
        if (!IsWindowVisible(parent)) throw new ArgumentException("Ren'Py parent window must be visible.");
    }

    internal static bool HasIdentity(nint parent, int expectedPid)
    {
        if (parent == 0 || expectedPid <= 0 || !IsWindow(parent)) return false;
        GetWindowThreadProcessId(parent, out var pid);
        return pid == expectedPid && GetAncestor(parent, 2) == parent;
    }

    private static string ClassName(nint hwnd)
    {
        var value = new StringBuilder(256);
        GetClassName(hwnd, value, value.Capacity);
        return value.ToString();
    }

    internal static bool HasVisibleWebRenderer(nint parent)
    {
        var found = false;
        EnumChildWindows(parent, (handle, _) => {
            if (ClassName(handle) == "Chrome_RenderWidgetHostHWND" && IsWindowVisible(handle) && GetAncestor(handle, 2) == parent)
                found = true;
            return true;
        }, 0);
        return found;
    }

    internal static void MatchDpi(nint parent)
    {
        var parentContext = GetWindowDpiAwarenessContext(parent);
        if (parentContext == 0 || SetThreadDpiAwarenessContext(parentContext) == 0)
            throw new InvalidOperationException("Could not match the Ren'Py window DPI awareness context.");
    }

    internal static Size ClientBounds(nint parent)
    {
        if (!GetClientRect(parent, out var bounds)) throw new InvalidOperationException("Parent client area is unavailable.");
        return new Size(bounds.Right - bounds.Left, bounds.Bottom - bounds.Top);
    }

    internal static string Describe(string kind, nint child, nint parent, int parentPid, nint webview, Rectangle childBounds, bool childVisible, bool paused, bool waitingLayout)
    {
        var root = GetAncestor(child, 2);
        var parentContext = GetWindowDpiAwarenessContext(parent);
        var childContext = GetWindowDpiAwarenessContext(child);
        var currentPid = Environment.ProcessId;
        var visible = new List<object>();
        EnumWindows((handle, _) => {
            GetWindowThreadProcessId(handle, out var pid);
            if (IsWindowVisible(handle) && (pid == currentPid || pid == parentPid))
                visible.Add(new { hwnd = handle.ToInt64(), pid, class_name = ClassName(handle) });
            return true;
        }, 0);
        var descendants = new List<object>();
        EnumChildWindows(parent, (handle, _) => {
            GetWindowThreadProcessId(handle, out var pid);
            descendants.Add(new { hwnd = handle.ToInt64(), pid, class_name = ClassName(handle), visible = IsWindowVisible(handle),
                                  root = GetAncestor(handle, 2).ToInt64(), style = GetWindowLongPtr(handle, -16).ToInt64() });
            return true;
        }, 0);
        return JsonSerializer.Serialize(new {
            kind, at_utc = DateTimeOffset.UtcNow.ToString("O"), parent_hwnd = parent.ToInt64(), parent_pid = parentPid,
            host_pid = currentPid, child_hwnd = child.ToInt64(), webview_control_hwnd = webview.ToInt64(), root_hwnd = root.ToInt64(),
            webview_root_hwnd = webview == 0 ? 0 : GetAncestor(webview, 2).ToInt64(),
            same_root = root == parent, child_style = GetWindowLongPtr(child, -16).ToInt64(),
            child_visible = childVisible, child_bounds = childBounds, paused, waiting_layout = waitingLayout,
            ws_child = (GetWindowLongPtr(child, -16).ToInt64() & 0x40000000) != 0,
            parent_dpi = GetDpiForWindow(parent), child_dpi = GetDpiForWindow(child),
            parent_dpi_awareness = GetAwarenessFromDpiAwarenessContext(parentContext), child_dpi_awareness = GetAwarenessFromDpiAwarenessContext(childContext),
            dpi_context_equal = AreDpiAwarenessContextsEqual(parentContext, childContext),
            parent_client = IsWindow(parent) ? ClientBounds(parent) : Size.Empty, child_client = ClientBounds(child), visible_owned_top_levels = visible,
            native_descendants = descendants
        });
    }
}
