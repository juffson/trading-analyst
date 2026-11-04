//! Interactive, local CLI sessions. Only the two named executables can be launched;
//! prompts are passed as arguments, never through a shell.
use anyhow::{Context, Result, bail};
use chrono::Utc;
use portable_pty::{ChildKiller, CommandBuilder, MasterPty, PtySize, native_pty_system};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tokio::sync::broadcast;

const MAX_REPLAY_BYTES: usize = 1024 * 1024;
const MAX_LOG_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Runner {
    Manual,
    Codex,
    Claude,
}

impl Default for Runner {
    fn default() -> Self {
        Self::Manual
    }
}

impl Runner {
    pub fn name(self) -> &'static str {
        match self {
            Self::Manual => "manual",
            Self::Codex => "codex",
            Self::Claude => "claude",
        }
    }
}

pub struct SessionManager {
    sessions: Mutex<HashMap<PathBuf, Arc<Session>>>,
}

impl SessionManager {
    pub fn new() -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
        }
    }

    pub fn available() -> serde_json::Value {
        json!({
            "codex": find_cli("codex").is_some(),
            "claude": find_cli("claude").is_some(),
        })
    }

    pub fn get(&self, job_dir: &Path) -> Option<Arc<Session>> {
        self.sessions.lock().ok()?.get(job_dir).cloned()
    }

    pub fn launch(
        &self,
        runner: Runner,
        job_dir: &Path,
        root: &Path,
        prompt: &str,
        report_filename: &str,
    ) -> Result<()> {
        if runner == Runner::Manual {
            bail!("手动模式不能启动内置终端");
        }
        let name = runner.name();
        let executable = find_cli(name).with_context(|| format!("未找到本地 {name} CLI"))?;
        let mut command = CommandBuilder::new(executable);
        command.cwd(root);
        command.env("TERM", "xterm-256color");
        match runner {
            Runner::Codex => {
                command.args(["--no-alt-screen", "-C"]);
                command.arg(root);
                command.args(["-s", "workspace-write", "-a", "on-request"]);
            }
            Runner::Claude => command.args(["--permission-mode", "manual"]),
            Runner::Manual => unreachable!(),
        }
        command.arg(prompt);

        let mut sessions = self
            .sessions
            .lock()
            .map_err(|_| anyhow::anyhow!("终端状态不可用"))?;
        if sessions.contains_key(job_dir) {
            bail!("任务已在运行");
        }
        let pty = native_pty_system().openpty(PtySize {
            rows: 30,
            cols: 110,
            pixel_width: 0,
            pixel_height: 0,
        })?;
        let reader = pty.master.try_clone_reader()?;
        let writer = pty.master.take_writer()?;
        let mut child = pty
            .slave
            .spawn_command(command)
            .context("启动本地 CLI 失败")?;
        drop(pty.slave);
        let killer = child.clone_killer();
        let log_path = job_dir.join("terminal.log");
        let log = match open_private_log(&log_path) {
            Ok(log) => log,
            Err(error) => {
                let _ = child.kill();
                return Err(error);
            }
        };
        if let Err(error) = write_status(job_dir, "running", runner, None) {
            let _ = child.kill();
            return Err(error);
        }
        let (output, _) = broadcast::channel(256);
        let session = Arc::new(Session {
            master: Mutex::new(pty.master),
            writer: Mutex::new(writer),
            killer: Mutex::new(killer),
            output: output.clone(),
            log_path,
            runner,
            running: Arc::new(AtomicBool::new(true)),
            stop_requested: Arc::new(AtomicBool::new(false)),
        });
        sessions.insert(job_dir.to_path_buf(), Arc::clone(&session));
        drop(sessions);

        thread::spawn(move || read_output(reader, log, output));
        let job_dir = job_dir.to_path_buf();
        let report_filename = report_filename.to_string();
        let running = Arc::clone(&session.running);
        let stop_requested = Arc::clone(&session.stop_requested);
        thread::spawn(move || {
            loop {
                match child.try_wait() {
                    Ok(Some(exit)) => {
                        running.store(false, Ordering::Release);
                        let state = if stop_requested.load(Ordering::Acquire) {
                            "cancelled"
                        } else if job_dir.join(&report_filename).exists() {
                            "done"
                        } else if exit.success() {
                            "incomplete"
                        } else {
                            "failed"
                        };
                        let _ = write_status(&job_dir, state, runner, Some(exit.exit_code()));
                        break;
                    }
                    Ok(None) => thread::sleep(Duration::from_millis(250)),
                    Err(_) => {
                        running.store(false, Ordering::Release);
                        let _ = write_status(&job_dir, "failed", runner, None);
                        break;
                    }
                }
            }
        });
        Ok(())
    }

    pub fn reconcile(&self, root: &Path) {
        let Ok(entries) = fs::read_dir(root) else {
            return;
        };
        for entry in entries.flatten() {
            let dir = entry.path();
            let path = dir.join("status.json");
            let Ok(bytes) = fs::read(&path) else { continue };
            let Ok(mut value) = serde_json::from_slice::<serde_json::Value>(&bytes) else {
                continue;
            };
            if matches!(value["status"].as_str(), Some("running" | "stopping")) {
                value["status"] = json!("interrupted");
                let _ = fs::write(path, value.to_string());
            }
        }
    }
}

pub struct Session {
    master: Mutex<Box<dyn MasterPty + Send>>,
    writer: Mutex<Box<dyn Write + Send>>,
    killer: Mutex<Box<dyn ChildKiller + Send + Sync>>,
    output: broadcast::Sender<Vec<u8>>,
    log_path: PathBuf,
    runner: Runner,
    running: Arc<AtomicBool>,
    stop_requested: Arc<AtomicBool>,
}

impl Session {
    pub fn subscribe(&self) -> broadcast::Receiver<Vec<u8>> {
        self.output.subscribe()
    }

    pub fn replay(&self) -> Vec<u8> {
        tail_log(&self.log_path)
    }

    pub fn input(&self, data: &[u8]) -> Result<()> {
        if data.len() > 64 * 1024 {
            bail!("输入过长");
        }
        if !self.running.load(Ordering::Acquire) {
            bail!("CLI 已结束");
        }
        let mut writer = self
            .writer
            .lock()
            .map_err(|_| anyhow::anyhow!("终端输入不可用"))?;
        writer.write_all(data)?;
        writer.flush()?;
        Ok(())
    }

    pub fn resize(&self, cols: u16, rows: u16) -> Result<()> {
        self.master
            .lock()
            .map_err(|_| anyhow::anyhow!("终端尺寸不可用"))?
            .resize(PtySize {
                rows: rows.clamp(10, 200),
                cols: cols.clamp(20, 400),
                pixel_width: 0,
                pixel_height: 0,
            })
    }

    pub fn stop(&self, job_dir: &Path) -> Result<()> {
        if !self.running.load(Ordering::Acquire) {
            return Ok(());
        }
        self.stop_requested.store(true, Ordering::Release);
        write_status(job_dir, "stopping", self.runner, None)?;
        self.killer
            .lock()
            .map_err(|_| anyhow::anyhow!("无法停止 CLI"))?
            .kill()?;
        Ok(())
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        if self.running.load(Ordering::Acquire) {
            if let Ok(killer) = self.killer.get_mut() {
                let _ = killer.kill();
            }
        }
    }
}

pub fn tail_log(path: &Path) -> Vec<u8> {
    let bytes = fs::read(path).unwrap_or_default();
    bytes[bytes.len().saturating_sub(MAX_REPLAY_BYTES)..].to_vec()
}

fn read_output(
    mut reader: Box<dyn Read + Send>,
    mut log: fs::File,
    output: broadcast::Sender<Vec<u8>>,
) {
    let mut written = 0usize;
    let mut buffer = [0u8; 8192];
    while let Ok(count) = reader.read(&mut buffer) {
        if count == 0 {
            break;
        }
        let chunk = buffer[..count].to_vec();
        if written < MAX_LOG_BYTES {
            let amount = count.min(MAX_LOG_BYTES - written);
            let _ = log.write_all(&chunk[..amount]);
            written += amount;
        }
        let _ = output.send(chunk);
    }
}

fn open_private_log(path: &Path) -> Result<fs::File> {
    let mut options = OpenOptions::new();
    options.create(true).truncate(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    Ok(options.open(path)?)
}

pub fn write_status(
    job_dir: &Path,
    status: &str,
    runner: Runner,
    exit_code: Option<u32>,
) -> Result<()> {
    let data = json!({
        "status": status,
        "runner": runner.name(),
        "updated_at": Utc::now().to_rfc3339(),
        "exit_code": exit_code,
    });
    fs::write(job_dir.join("status.json"), data.to_string())?;
    Ok(())
}

fn find_cli(name: &str) -> Option<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|path| std::env::split_paths(&path).collect())
        .unwrap_or_default();
    if let Some(home) = std::env::var_os("HOME") {
        dirs.push(PathBuf::from(&home).join(".local/bin"));
        dirs.push(PathBuf::from(home).join(".cargo/bin"));
    }
    dirs.extend([
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
    ]);
    dirs.into_iter()
        .map(|dir| dir.join(name))
        .find(|path| path.is_file())
        .and_then(|path| path.canonicalize().ok())
}
