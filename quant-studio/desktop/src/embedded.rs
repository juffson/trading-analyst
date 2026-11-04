use anyhow::{Context, Result, bail};
use gpui_kit::Window;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, UNIX_EPOCH};
use wry::dpi::{LogicalPosition, LogicalSize};
use wry::{PageLoadEvent, Rect, WebView, WebViewBuilder};

const SERVER: &str = "127.0.0.1:4870";
const RAIL_WIDTH: f64 = 204.0;

pub struct EmbeddedPages {
    webview: Option<WebView>,
    backend: Option<Child>,
    bounds: Option<(f64, f64)>,
    page_ready: Arc<AtomicBool>,
    asset_version: u128,
}

impl EmbeddedPages {
    pub fn new() -> Self {
        Self {
            webview: None,
            backend: None,
            bounds: None,
            page_ready: Arc::new(AtomicBool::new(false)),
            asset_version: 0,
        }
    }

    pub fn open(&mut self, page: &str, factor: Option<&str>, window: &mut Window, on_route: impl Fn(&str) + 'static) -> Result<()> {
        self.ensure_backend()?;
        let asset_version = static_version();
        let url = format!(
            "http://{SERVER}/?embedded=1&v={asset_version}{}#{page}",
            factor
                .map(|id| format!("&factor={}", encode_query(id)))
                .unwrap_or_default()
        );
        if let Some(webview) = &self.webview {
            if factor.is_some()
                || asset_version != self.asset_version
                || !self.page_ready.load(Ordering::Acquire)
            {
                webview.load_url(&url).context("打开回测页面失败")?;
            } else {
                webview
                    .evaluate_script(&format!("showPage('{page}')"))
                    .context("切换内置页面失败")?;
            }
            webview.set_visible(true).context("显示内置页面失败")?;
        } else {
            let page_ready = Arc::clone(&self.page_ready);
            self.webview = Some(
                WebViewBuilder::new()
                    .with_url(&url)
                    .with_ipc_handler(move |request| {
                        if request.uri().authority().map(|a| a.as_str()) == Some(SERVER) {
                            if let Some(page) = request.body().strip_prefix("route:") {
                                on_route(page);
                            }
                        }
                    })
                    .with_bounds(Self::rect(window))
                    .with_on_page_load_handler(move |event, _| {
                        page_ready
                            .store(matches!(event, PageLoadEvent::Finished), Ordering::Release);
                    })
                    .build_as_child(window)
                    .context("创建内置页面失败")?,
            );
        }
        self.asset_version = asset_version;
        self.resize(window)?;
        Ok(())
    }

    pub fn hide(&self) -> Result<()> {
        if let Some(webview) = &self.webview {
            webview.set_visible(false).context("隐藏内置页面失败")?;
        }
        Ok(())
    }

    pub fn resize(&mut self, window: &Window) -> Result<()> {
        let size = window.bounds().size;
        let current = (size.width.as_f32() as f64, size.height.as_f32() as f64);
        if self.bounds != Some(current) {
            if let Some(webview) = &self.webview {
                webview
                    .set_bounds(Self::rect(window))
                    .context("调整内置页面尺寸失败")?;
            }
            self.bounds = Some(current);
        }
        Ok(())
    }

    fn rect(window: &Window) -> Rect {
        let size = window.bounds().size;
        Rect {
            position: LogicalPosition::new(RAIL_WIDTH, 0.0).into(),
            size: LogicalSize::new(
                (size.width.as_f32() as f64 - RAIL_WIDTH).max(1.0),
                (size.height.as_f32() as f64).max(1.0),
            )
            .into(),
        }
    }

    fn ensure_backend(&mut self) -> Result<()> {
        if server_ready() {
            return Ok(());
        }
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .canonicalize()
            .context("定位 quant-studio 目录失败")?;
        let binary = root.join("target/debug/quant-studio");
        let mut command = if backend_is_current(&root, &binary) {
            Command::new(binary)
        } else {
            let mut cargo = Command::new(option_env!("CARGO").unwrap_or("cargo"));
            cargo.arg("run").arg("--locked").arg("--quiet");
            cargo
        };
        let child = command
            .current_dir(root)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .context("启动 Quant Lab 服务失败")?;
        self.backend = Some(child);
        for _ in 0..1200 {
            if server_ready() {
                return Ok(());
            }
            if let Some(status) = self.backend.as_mut().unwrap().try_wait()? {
                bail!("Quant Lab 服务启动失败：{status}");
            }
            thread::sleep(Duration::from_millis(100));
        }
        bail!("等待 Quant Lab 服务启动超时")
    }
}

impl Drop for EmbeddedPages {
    fn drop(&mut self) {
        if let Some(mut child) = self.backend.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn server_ready() -> bool {
    SERVER
        .to_socket_addrs()
        .ok()
        .and_then(|mut addresses| addresses.next())
        .and_then(|address| TcpStream::connect_timeout(&address, Duration::from_millis(150)).ok())
        .is_some()
}

fn encode_query(value: &str) -> String {
    value
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
                (byte as char).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}

fn static_version() -> u128 {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../static");
    [
        "index.html",
        "app-theme.css",
        "app-shell.js",
        "research-dashboard.css",
        "research-dashboard.js",
        "daily-close.js",
        "daily-close.css",
    ]
    .iter()
    .filter_map(|name| root.join(name).metadata().ok()?.modified().ok())
    .filter_map(|modified| modified.duration_since(UNIX_EPOCH).ok())
    .map(|duration| duration.as_nanos())
    .max()
    .unwrap_or_default()
}

fn backend_is_current(root: &std::path::Path, binary: &std::path::Path) -> bool {
    let Ok(built_at) = binary.metadata().and_then(|metadata| metadata.modified()) else {
        return false;
    };
    let mut paths = vec![
        root.join("Cargo.toml"),
        root.join("Cargo.lock"),
        root.join("src"),
    ];
    while let Some(path) = paths.pop() {
        if path.is_dir() {
            let Ok(entries) = std::fs::read_dir(path) else {
                return false;
            };
            paths.extend(entries.filter_map(|entry| entry.ok().map(|entry| entry.path())));
        } else if (path.extension().is_some_and(|extension| extension == "rs")
            || path
                .file_name()
                .is_some_and(|name| name == "Cargo.toml" || name == "Cargo.lock"))
            && path
                .metadata()
                .and_then(|metadata| metadata.modified())
                .is_ok_and(|modified| modified > built_at)
        {
            return false;
        }
    }
    true
}
