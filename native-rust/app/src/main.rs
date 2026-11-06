#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use lite_browser_native::{address, allowed_url, toolbar_navigation, ClosedTabs, Page, SavedBrowser, MAX_TABS};
use serde::Deserialize;
use serde_json::json;
use std::{
    path::PathBuf,
    time::{Duration, Instant},
};
use tao::{
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy},
    window::{Window, WindowBuilder},
};
use wry::{
    dpi::{LogicalPosition, LogicalSize},
    PageLoadEvent, Rect, WebContext, WebView, WebViewBuilder,
};

/// CI stamps the source commit, so the window itself shows which build is running.
fn window_title() -> String {
    match option_env!("APP_SOURCE_COMMIT") {
        Some(commit) if commit.len() >= 7 => format!("LiteBrowser Rust - {}", &commit[..7]),
        _ => "LiteBrowser Rust - local build".into(),
    }
}

const NOTE: &str = "Preview: original Shields, password manager, private tabs, extensions, and downloads are not ported.";
#[derive(Debug)]
enum Message {
    Command(String),
    Title(u64, String),
    Loaded(u64, String),
    Notice(String),
}
#[derive(Deserialize)]
struct Command {
    action: String,
    #[serde(default)]
    value: String,
    #[serde(default)]
    id: u64,
}
struct Tab {
    id: u64,
    url: String,
    title: String,
    view: WebView,
}
struct Browser {
    window: Window,
    toolbar: WebView,
    tabs: Vec<Tab>,
    context: WebContext,
    proxy: EventLoopProxy<Message>,
    saved: SavedBrowser,
    active: u64,
    serial: u64,
    library: bool,
    status: String,
    root: PathBuf,
    closed: ClosedTabs,
}

impl Browser {
    fn height(&self) -> f64 {
        if self.library {
            314.0
        } else {
            104.0
        }
    }
    fn bounds(&self, toolbar: bool) -> Rect {
        let size = self.window.inner_size().to_logical::<f64>(self.window.scale_factor());
        let height = self.height();
        Rect {
            position: LogicalPosition::new(0.0, if toolbar { 0.0 } else { height }).into(),
            size: LogicalSize::new(size.width, if toolbar { height } else { (size.height - height).max(1.0) }).into(),
        }
    }
    fn layout(&self) {
        let _ = self.toolbar.set_bounds(self.bounds(true));
        for tab in &self.tabs {
            let _ = tab.view.set_bounds(self.bounds(false));
            let _ = tab.view.set_visible(tab.id == self.active);
        }
    }
    fn render(&self) {
        let url = self.tabs.iter().find(|tab| tab.id == self.active).map(|tab| tab.url.as_str()).unwrap_or("");
        let state = json!({"tabs":self.tabs.iter().map(|tab| json!({"id":tab.id,"title":tab.title,"url":tab.url})).collect::<Vec<_>>(),
            "active":self.active,"url":url,"library":self.library,"status":self.status,
            "bookmarks":self.saved.bookmarks,"history":self.saved.history});
        // JSON serialization plus DOM textContent keeps untrusted titles/URLs out of HTML.
        let _ = self.toolbar.evaluate_script(&format!("window.render({state});"));
    }
    fn new_tab(&mut self, url: &str) -> Result<(), String> {
        if self.tabs.len() >= MAX_TABS {
            return Err(format!("This preview supports {MAX_TABS} tabs."));
        }
        if !allowed_url(url) {
            return Err("Unsupported tab URL.".into());
        }
        self.serial += 1;
        let id = self.serial;
        let title_proxy = self.proxy.clone();
        let load_proxy = self.proxy.clone();
        let popup_proxy = self.proxy.clone();
        let download_proxy = self.proxy.clone();
        let bounds = self.bounds(false);
        let view = WebViewBuilder::new_with_web_context(&mut self.context)
            .with_bounds(bounds)
            .with_url(url)
            .with_devtools(false)
            .with_navigation_handler(|url| allowed_url(&url))
            .with_document_title_changed_handler(move |title| {
                let _ = title_proxy.send_event(Message::Title(id, title.chars().take(250).collect()));
            })
            .with_on_page_load_handler(move |event, url| {
                if matches!(event, PageLoadEvent::Finished) {
                    let _ = load_proxy.send_event(Message::Loaded(id, url));
                }
            })
            .with_new_window_req_handler(move |_, _| {
                let _ = popup_proxy.send_event(Message::Notice("Pop-ups are blocked in this preview. Copy the link into a new tab.".into()));
                wry::NewWindowResponse::Deny
            })
            .with_download_started_handler(move |_, _| {
                let _ =
                    download_proxy.send_event(Message::Notice("Downloads are not enabled in this preview. Use the original LiteBrowser for downloads.".into()));
                false
            })
            .build_as_child(&self.window)
            .map_err(|error| format!("Could not open WebView2: {error}"))?;
        self.tabs.push(Tab { id, url: url.into(), title: "New tab".into(), view });
        self.active = id;
        self.layout();
        self.render();
        Ok(())
    }
    fn save(&mut self) -> Result<(), String> {
        self.saved.tabs = self.tabs.iter().map(|tab| tab.url.clone()).collect();
        self.saved.sanitize();
        transcript_store::save_snapshot(&self.root.join("sessions"), &self.saved).map(|_| ())
    }
    fn command(&mut self, command: Command) -> Result<(), String> {
        match command.action.as_str() {
            "ready" => {}
            "new" => self.new_tab("about:blank")?,
            "select" => {
                if self.tabs.iter().any(|tab| tab.id == command.id) {
                    self.active = command.id;
                    self.layout();
                }
            }
            "close" => {
                if let Some(tab) = self.tabs.iter().find(|tab| tab.id == command.id) {
                    self.closed.remember(tab.url.clone());
                }
                self.tabs.retain(|tab| tab.id != command.id);
                if self.tabs.is_empty() {
                    self.new_tab("about:blank")?;
                }
                if !self.tabs.iter().any(|tab| tab.id == self.active) {
                    self.active = self.tabs[0].id;
                }
                self.layout();
            }
            "reopen" => {
                if let Some(url) = self.closed.last().map(str::to_owned) {
                    self.new_tab(&url)?;
                    self.closed.restored();
                } else {
                    self.status = "No recently closed tabs.".into();
                }
            }
            "navigate" => {
                let url = address(&command.value)?;
                if let Some(tab) = self.tabs.iter().find(|tab| tab.id == self.active) {
                    tab.view.load_url(&url).map_err(|error| error.to_string())?;
                }
                self.status = NOTE.into();
            }
            "back" | "forward" | "reload" => {
                if let Some(tab) = self.tabs.iter().find(|tab| tab.id == self.active) {
                    if command.action == "reload" {
                        tab.view.load_url(&tab.url).map_err(|error| error.to_string())?;
                    } else {
                        tab.view
                            .evaluate_script(if command.action == "back" { "history.back()" } else { "history.forward()" })
                            .map_err(|error| error.to_string())?;
                    }
                }
            }
            "bookmark" => {
                if let Some(tab) = self.tabs.iter().find(|tab| tab.id == self.active) {
                    self.saved.toggle_bookmark(Page { url: tab.url.clone(), title: tab.title.clone() });
                    self.save()?;
                }
            }
            "library" => {
                self.library = !self.library;
                self.layout();
            }
            "save" => {
                self.save()?;
                self.status = "Tabs, bookmarks, and history saved on D:.".into();
            }
            _ => return Err("Unknown toolbar action.".into()),
        }
        self.render();
        Ok(())
    }
}

fn run() -> Result<(), String> {
    let root = PathBuf::from(r"D:\UserData\Apps\LiteBrowser-Rust");
    std::fs::create_dir_all(root.join("webview-profile")).map_err(|error| error.to_string())?;
    let mut saved = transcript_store::latest_snapshot::<SavedBrowser>(&root.join("sessions"))?.unwrap_or_default();
    saved.sanitize();
    let urls = if saved.tabs.is_empty() { vec!["about:blank".into()] } else { saved.tabs.clone() };
    let event_loop = EventLoopBuilder::<Message>::with_user_event().build();
    let window = WindowBuilder::new()
        .with_title(window_title())
        .with_inner_size(tao::dpi::LogicalSize::new(1200, 820))
        .with_min_inner_size(tao::dpi::LogicalSize::new(800, 500))
        .build(&event_loop)
        .map_err(|error| error.to_string())?;
    let mut toolbar_context = WebContext::new(Some(root.join("toolbar-profile")));
    let proxy = event_loop.create_proxy();
    let toolbar_proxy = proxy.clone();
    let toolbar = WebViewBuilder::new_with_web_context(&mut toolbar_context)
        .with_html(include_str!("toolbar.html"))
        .with_devtools(false)
        .with_navigation_handler({
            let loaded = std::cell::Cell::new(false);
            move |url| toolbar_navigation(&url, !loaded.replace(true))
        })
        .with_new_window_req_handler(|_, _| wry::NewWindowResponse::Deny)
        .with_download_started_handler(|_, _| false)
        .with_ipc_handler(move |request| {
            if request.body().len() <= 16_384 {
                let _ = toolbar_proxy.send_event(Message::Command(request.body().clone()));
            }
        })
        .build_as_child(&window)
        .map_err(|error| error.to_string())?;
    let mut browser = Browser {
        window,
        toolbar,
        tabs: vec![],
        context: WebContext::new(Some(root.join("webview-profile"))),
        proxy,
        saved,
        active: 0,
        serial: 0,
        library: false,
        status: NOTE.into(),
        root,
        closed: ClosedTabs::default(),
    };
    for url in urls {
        browser.new_tab(&url)?;
    }
    let mut tick = Instant::now();
    event_loop.run(move |event, _, control| {
        // Retain both profile contexts for the life of their WebViews.
        let _ = &toolbar_context;
        *control = ControlFlow::WaitUntil(tick + Duration::from_millis(500));
        match event {
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => match browser.save() {
                Ok(()) => *control = ControlFlow::Exit,
                Err(error) => {
                    browser.status = error;
                    browser.render();
                }
            },
            Event::WindowEvent { event: WindowEvent::Resized(_), .. } | Event::WindowEvent { event: WindowEvent::ScaleFactorChanged { .. }, .. } => {
                browser.layout()
            }
            Event::UserEvent(Message::Command(json)) => {
                match serde_json::from_str::<Command>(&json).map_err(|error| error.to_string()).and_then(|command| browser.command(command)) {
                    Ok(()) => {}
                    Err(error) => {
                        browser.status = error;
                        browser.render();
                    }
                }
            }
            Event::UserEvent(Message::Title(id, title)) => {
                if let Some(tab) = browser.tabs.iter_mut().find(|tab| tab.id == id) {
                    tab.title = title;
                    if let Some(page) = browser.saved.history.iter_mut().find(|page| page.url == tab.url) {
                        page.title = tab.title.clone();
                    }
                }
                browser.render();
            }
            Event::UserEvent(Message::Loaded(id, url)) => {
                if allowed_url(&url) {
                    if let Some(tab) = browser.tabs.iter_mut().find(|tab| tab.id == id) {
                        tab.url = url;
                        browser.saved.visit(Page { url: tab.url.clone(), title: tab.title.clone() });
                    }
                }
                browser.render();
            }
            Event::UserEvent(Message::Notice(message)) => {
                browser.status = message;
                browser.render();
            }
            Event::MainEventsCleared if tick.elapsed() >= Duration::from_millis(500) => {
                tick = Instant::now();
                let mut changed = false;
                for tab in &mut browser.tabs {
                    if let Ok(url) = tab.view.url() {
                        if allowed_url(&url) && tab.url != url {
                            tab.url = url;
                            changed = true;
                        }
                    }
                }
                if changed {
                    browser.render();
                }
            }
            _ => {}
        }
    });
}

fn main() {
    if let Err(error) = run() {
        rfd::MessageDialog::new()
            .set_title("LiteBrowser Rust")
            .set_description(format!("{error}\n\nThis preview requires the Microsoft Edge WebView2 runtime and a writable D: drive."))
            .show();
    }
}
