//! Job metadata and report discovery for the local Codex/Claude terminal and
//! the existing manual-command flow.
use anyhow::Result;
use chrono::Utc;
use serde_json::{Value, json};
use std::path::Path;

/// Unicode-aware（不是 `is_ascii_alphanumeric`）——公司名常是中文（"贵州茅台"），
/// 只按 ASCII 过滤会把所有中文输入都滤成空字符串，退化成毫无信息量的 "job_<时间戳>"。
pub fn slugify(input: &str) -> String {
    let mut out = String::new();
    for c in input.trim().chars() {
        if c.is_alphanumeric() {
            out.extend(c.to_lowercase());
        } else if !out.is_empty() && !out.ends_with('-') {
            out.push('-');
        }
    }
    let out = out.trim_matches('-').to_string();
    if out.is_empty() {
        "job".to_string()
    } else {
        out
    }
}

pub fn new_job_slug(seed: &str) -> String {
    format!("{}_{}", slugify(seed), Utc::now().format("%Y%m%d%H%M%S"))
}

/// slug 是从客户端 URL path 里读回来的（GET /api/.../{slug}/status），不能直接信任——
/// 这里放行 Unicode 字母数字（公司名常是中文）+ `_`/`-`，但拒绝路径分隔符和 `..`，
/// 防止拼出 job_dir.join(slug) 时跳出预期目录。跟 `performance::is_safe_key` 分开维护，
/// 那个是给纯 ASCII 的 factor key 用的，这里的输入天然会有中文。
pub fn is_safe_slug(slug: &str) -> bool {
    !slug.is_empty()
        && slug != "."
        && slug != ".."
        && !slug.contains(['/', '\\', '\0'])
        && slug
            .chars()
            .all(|c| c.is_alphanumeric() || matches!(c, '_' | '-'))
}

/// 建好任务目录，把要跑的命令存一份 `command.txt`（方便复制），返回命令本身供页面展示。
pub fn prepare_job(job_dir: &Path, command: &str) -> Result<()> {
    std::fs::create_dir_all(job_dir)?;
    std::fs::write(job_dir.join("command.txt"), command)?;
    std::fs::write(
        job_dir.join("status.json"),
        serde_json::to_string_pretty(&json!({
            "status": "waiting_for_manual_run",
            "created_at": Utc::now().to_rfc3339(),
        }))?,
    )?;
    Ok(())
}

/// 结合终端会话写入的状态和报告文件，供页面与历史列表展示。
pub fn job_status(job_dir: &Path, report_filename: &str) -> Value {
    let command = std::fs::read_to_string(job_dir.join("command.txt")).unwrap_or_default();
    let metadata = std::fs::read(job_dir.join("status.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .unwrap_or(Value::Null);
    let stored = metadata["status"]
        .as_str()
        .unwrap_or("waiting_for_manual_run");
    let runner = metadata["runner"].as_str().unwrap_or("manual");
    if job_dir.join(report_filename).exists()
        && !matches!(
            stored,
            "running" | "stopping" | "cancelled" | "failed" | "interrupted"
        )
    {
        let summary = std::fs::read_to_string(job_dir.join("summary.json"))
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok());
        return json!({ "status": "done", "runner": runner, "command": command, "summary": summary });
    }
    if job_dir.exists() {
        return json!({ "status": stored, "runner": runner, "command": command, "exit_code": metadata["exit_code"] });
    }
    json!({ "status": "not_found" })
}

/// 列出 root 下所有 job 目录的状态摘要（每个子目录是一个 job，目录名就是 slug）。
pub fn list_jobs(root: &Path, report_filename: &str) -> Vec<Value> {
    let mut jobs = Vec::new();
    let Ok(entries) = std::fs::read_dir(root) else {
        return jobs;
    };
    for entry in entries.flatten() {
        if !entry.path().is_dir() {
            continue;
        }
        let slug = entry.file_name().to_string_lossy().to_string();
        let mut status = job_status(&entry.path(), report_filename);
        status["slug"] = json!(slug);
        jobs.push(status);
    }
    jobs.sort_by(|a, b| b["slug"].as_str().cmp(&a["slug"].as_str()));
    jobs
}

pub fn read_report_html(job_dir: &Path, filename: &str) -> Result<String> {
    Ok(std::fs::read_to_string(job_dir.join(filename))?)
}
