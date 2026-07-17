//! 把 `../company-deep-dive` / `../trading-analyst` 这两个 Skill 接到 quant-studio 页面里——
//! **不**在后台无人值守跑 headless claude。跑完整个 skill 流程需要 Bash/网络/子 agent 权限，
//! 没有 TTY 就没法逐条确认，唯一能不卡住的办法是 `--permission-mode bypassPermissions`，
//! 那等于让一个点了页面按钮就能触发的进程完全绕过审批——这个口子太大，用户明确选了不开。
//!
//! 所以这里只做"生成命令，人工跑"：quant-studio 拼好完整的 `claude -p "..."` 命令（跟
//! `../auto-trader/pipeline/README.md` 里手动调用 skill 的方式一致），用户自己复制到终端跑，
//! 交互式确认每一步；跑完回来点"检查完成"，页面从磁盘上找生成的报告文件展示。
//! 不依赖 skill 自动发现/匹配（用户也没把这两个 skill 装进 ~/.claude/skills/）——命令里直接把
//! SKILL.md 的绝对路径写清楚，让 claude 自己读文件按流程做。
use anyhow::Result;
use chrono::Utc;
use serde_json::{json, Value};
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
        && slug.chars().all(|c| c.is_alphanumeric() || matches!(c, '_' | '-'))
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

/// 状态纯靠磁盘上有没有目标报告文件判断——没有后台进程，没有"running"这个中间态。
pub fn job_status(job_dir: &Path, report_filename: &str) -> Value {
    let command = std::fs::read_to_string(job_dir.join("command.txt")).unwrap_or_default();
    if job_dir.join(report_filename).exists() {
        let summary = std::fs::read_to_string(job_dir.join("summary.json"))
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok());
        return json!({ "status": "done", "command": command, "summary": summary });
    }
    if job_dir.exists() {
        return json!({ "status": "waiting_for_manual_run", "command": command });
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
