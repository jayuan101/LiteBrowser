use serde::{Deserialize, Serialize};
use url::Url;

pub const MAX_TABS: usize = 20;
#[derive(Clone, Serialize, Deserialize)]
pub struct Page { pub url: String, pub title: String }
#[derive(Default, Serialize, Deserialize)]
pub struct SavedBrowser {
    pub tabs: Vec<String>,
    pub bookmarks: Vec<Page>,
    pub history: Vec<Page>,
}

/// Only browser web navigation is accepted. Never dispatch OS protocols or scripts.
pub fn allowed_url(value: &str) -> bool {
    if value.len()>8192 || value.chars().any(char::is_control) { return false; }
    if value == "about:blank" { return true; }
    Url::parse(value).is_ok_and(|url| matches!(url.scheme(), "http" | "https")
        && url.host_str().is_some() && url.username().is_empty() && url.password().is_none())
}

pub fn address(value: &str) -> Result<String, String> {
    let text = value.trim();
    if text.len() > 8192 || text.chars().any(char::is_control) { return Err("Address is too long or contains control characters.".into()); }
    if text.is_empty() { return Ok("about:blank".into()); }
    let has_scheme = text.contains("://") || ["about:", "javascript:", "file:", "data:", "mailto:", "shell:"].iter().any(|prefix| text.to_ascii_lowercase().starts_with(prefix));
    let candidate = if has_scheme { text.to_owned() }
        else if !text.contains(char::is_whitespace) && (text.contains('.') || text.starts_with("localhost") || text.starts_with("127.0.0.1")) {
            format!("{}://{text}", if text.starts_with("localhost") || text.starts_with("127.0.0.1") { "http" } else { "https" })
        } else {
            let mut search = Url::parse("https://duckduckgo.com/").unwrap();
            search.query_pairs_mut().append_pair("q", text);
            search.into()
        };
    if !allowed_url(&candidate) { return Err("Use an HTTP/HTTPS address or a search. Scripts, local files, and external app protocols are not opened.".into()); }
    if candidate == "about:blank" { return Ok(candidate); }
    Ok(Url::parse(&candidate).map_err(|_| "Invalid address.")?.into())
}

pub fn bookmark_key(value: &str) -> Option<String> {
    if !allowed_url(value) || value == "about:blank" { return None; }
    let mut url = Url::parse(value).ok()?; url.set_fragment(None); Some(url.into())
}

impl SavedBrowser {
    pub fn sanitize(&mut self) {
        self.tabs.retain(|url| allowed_url(url)); self.tabs.truncate(MAX_TABS);
        for pages in [&mut self.bookmarks, &mut self.history] {
            pages.retain(|page| allowed_url(&page.url) && page.url != "about:blank");
            pages.truncate(1000);
            for page in pages { page.title = page.title.chars().take(250).collect(); }
        }
    }
    pub fn visit(&mut self, page: Page) {
        if !allowed_url(&page.url) || page.url == "about:blank" { return; }
        self.history.retain(|entry| entry.url != page.url);
        self.history.insert(0, page); self.history.truncate(1000);
    }
    pub fn toggle_bookmark(&mut self, page: Page) {
        let Some(key) = bookmark_key(&page.url) else { return; };
        if let Some(index) = self.bookmarks.iter().position(|entry| bookmark_key(&entry.url).as_ref() == Some(&key)) { self.bookmarks.remove(index); }
        else if self.bookmarks.len() < 1000 { self.bookmarks.push(Page { url: key, title: page.title }); }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn navigation_never_dispatches_scripts_or_credentials() {
        for url in ["javascript:alert(1)", "file:///C:/secret", "data:text/html,test", "https://user:password@example.com", "ms-settings://privacy"] { assert!(!allowed_url(url)); }
        assert!(address("JAVASCRIPT:alert(1)").is_err());
    }
    #[test] fn hostnames_and_searches_are_distinct() {
        assert_eq!(address("example.com/a").unwrap(), "https://example.com/a");
        assert_eq!(address("localhost:8080").unwrap(), "http://localhost:8080/");
        assert_eq!(Url::parse(&address("a b & c").unwrap()).unwrap().query_pairs().next().unwrap().1, "a b & c");
    }
    #[test] fn bookmarks_ignore_fragment_but_keep_query() {
        let mut saved = SavedBrowser::default();
        saved.toggle_bookmark(Page { url: "https://example.com/?a=1#one".into(), title:"One".into() });
        saved.toggle_bookmark(Page { url: "https://example.com/?a=1#two".into(), title:"Two".into() });
        assert!(saved.bookmarks.is_empty());
    }
    #[test] fn restored_state_rejects_unsafe_entries_and_bounds_tabs() {
        let mut saved = SavedBrowser { tabs: vec!["https://example.com".into(); 100], ..Default::default() };
        saved.tabs.insert(0, "javascript:alert(1)".into()); saved.sanitize();
        assert_eq!(saved.tabs.len(), MAX_TABS); assert!(saved.tabs.iter().all(|url| allowed_url(url)));
    }
}
