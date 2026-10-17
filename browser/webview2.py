"""Loads the .NET WinForms + WebView2 bindings through pythonnet.

The WebView2 DLLs ship inside the pywebview package that is already installed,
so we borrow them from there instead of bundling a second copy.
"""
import os

try:
    import clr
except Exception:  # pythonnet could not find .NET Framework, fall back to .NET Core
    os.environ['PYTHONNET_RUNTIME'] = 'coreclr'
    import clr


def _pywebview_lib_dir() -> str:
    import webview  # pywebview; only used to locate its bundled DLLs

    return os.path.join(os.path.dirname(os.path.realpath(webview.__file__)), 'lib')


LIB_DIR = _pywebview_lib_dir()

clr.AddReference('System')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
clr.AddReference(os.path.join(LIB_DIR, 'Microsoft.Web.WebView2.Core.dll'))
clr.AddReference(os.path.join(LIB_DIR, 'Microsoft.Web.WebView2.WinForms.dll'))

# WebView2Loader.dll must be discoverable on PATH for the Core assembly.
for plat in ('win-x64', 'win-x86', 'win-arm64'):
    os.environ['Path'] += ';' + os.path.join(LIB_DIR, 'runtimes', plat, 'native')

import System  # noqa: E402
import System.Drawing as Drawing  # noqa: E402
import System.Drawing.Drawing2D as Drawing2D  # noqa: E402
import System.Windows.Forms as WinForms  # noqa: E402
from System import Action, Func, Object, String, Uri  # noqa: E402
from System.IO import MemoryStream, Stream  # noqa: E402
from System.Threading import ApartmentState, Thread, ThreadStart  # noqa: E402
from System.Threading.Tasks import Task, TaskScheduler  # noqa: E402
from System.Windows.Forms import Keys  # noqa: E402

from Microsoft.Web.WebView2.Core import (  # noqa: E402
    CoreWebView2BrowserExtension, CoreWebView2Environment, CoreWebView2EnvironmentOptions,
    CoreWebView2FaviconImageFormat, CoreWebView2MemoryUsageTargetLevel, CoreWebView2PrintDialogKind,
    CoreWebView2WebResourceContext,
)
from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2  # noqa: E402

__all__ = [
    'System', 'Drawing', 'Drawing2D', 'WinForms', 'Action', 'Func', 'Object', 'String', 'Uri', 'MemoryStream',
    'Stream', 'ApartmentState', 'Thread', 'ThreadStart', 'Task', 'TaskScheduler', 'Keys',
    'CoreWebView2BrowserExtension', 'CoreWebView2Environment', 'CoreWebView2EnvironmentOptions',
    'CoreWebView2FaviconImageFormat', 'CoreWebView2MemoryUsageTargetLevel', 'CoreWebView2PrintDialogKind',
    'CoreWebView2WebResourceContext', 'CoreWebView2CreationProperties', 'WebView2',
]
