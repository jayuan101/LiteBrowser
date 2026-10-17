"""The one WebView2 environment every part of Lite shares, with browser extensions switched on.

WebView2 runs one browser process per profile folder, and every WebView that joins it must
ask for the same options. The WinForms control's CreationProperties cannot switch browser
extensions on, so Lite creates the environment itself and hands it to each WebView - the
toolbar, the pop-up panel and every tab - once it exists. Should this WebView2 refuse
extensions, Lite starts without them rather than not at all.
"""
from .webview2 import (
    Action, CoreWebView2CreationProperties, CoreWebView2Environment, CoreWebView2EnvironmentOptions, Task, TaskScheduler,
)


class Engine:
    def __init__(self, profile_dir: str, browser_args: str, extensions: bool = True):
        self.profile_dir = profile_dir
        self.browser_args = browser_args
        self.env = None
        self.error = None  # why the environment could not be created, if it could not
        self.extensions = False  # extensions really are on (the environment accepted the option)
        self._waiting = []  # attach() calls made before the environment existed
        self._scheduler = TaskScheduler.FromCurrentSynchronizationContext()  # the UI thread
        self._create(extensions)

    def _create(self, extensions: bool):
        options = CoreWebView2EnvironmentOptions(self.browser_args, None, None, False, None)
        options.AreBrowserExtensionsEnabled = extensions
        CoreWebView2Environment.CreateAsync(None, self.profile_dir, options).ContinueWith(
            Action[Task[CoreWebView2Environment]](lambda task: self._created(task, extensions)), self._scheduler)

    def _created(self, task, extensions: bool):
        if task.IsFaulted:
            if extensions:
                print('WebView2 refused browser extensions:', task.Exception)
                self._create(False)  # an older WebView2: Lite still works, just without extensions
                return
            self.error = task.Exception
        else:
            self.env, self.extensions = task.Result, extensions
        waiting, self._waiting = self._waiting, []
        for attach in waiting:
            attach()

    def attach(self, webview, private: bool = False):
        """Start `webview` in the shared environment (as soon as it exists). Private: InPrivate mode,
        whose cookies, history and cache go when the last private tab closes."""
        def go():
            if self.env is None:  # the environment failed: let the WebView try on its own (and report why)
                props = CoreWebView2CreationProperties()
                props.UserDataFolder = self.profile_dir
                props.AdditionalBrowserArguments = self.browser_args
                props.IsInPrivateModeEnabled = private
                webview.CreationProperties = props
                webview.EnsureCoreWebView2Async(None)
                return
            if private:
                options = self.env.CreateCoreWebView2ControllerOptions()
                options.IsInPrivateModeEnabled = True
                webview.EnsureCoreWebView2Async(self.env, options)
            else:
                webview.EnsureCoreWebView2Async(self.env)
        if self.env is not None or self.error is not None:
            go()
        else:
            self._waiting.append(go)
