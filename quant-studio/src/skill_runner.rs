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
    std::fs::create_dir_all(job_dir.join("workflow"))?;
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
        return json!({ "status": "done", "runner": runner, "command": command, "summary": summary,
            "workflow": workflow_status(job_dir, report_filename, "done") });
    }
    if job_dir.exists() {
        return json!({ "status": stored, "runner": runner, "command": command, "exit_code": metadata["exit_code"],
            "workflow": workflow_status(job_dir, report_filename, stored) });
    }
    json!({ "status": "not_found" })
}

struct StageSpec {
    title: &'static str,
    detail: &'static str,
    files: &'static [(&'static str, &'static str)],
}

const COMPANY_STAGES: &[StageSpec] = &[
    StageSpec {
        title: "任务准备",
        detail: "确认研究对象与执行命令",
        files: &[("分析任务", "command.txt")],
    },
    StageSpec {
        title: "财务数据",
        detail: "行情与财报并行采集",
        files: &[
            ("实时行情", "workflow/stock_quote.json"),
            ("财务报表", "workflow/financial_statements.json"),
        ],
    },
    StageSpec {
        title: "多维分析",
        detail: "四个独立分析视角",
        files: &[
            ("经济模型", "workflow/economic_model.json"),
            ("PESTEL", "workflow/pestel.json"),
            ("波特五力", "workflow/porter.json"),
            ("账面资产", "workflow/asset_valuation.json"),
        ],
    },
    StageSpec {
        title: "估值综合",
        detail: "DCF 与 11 维度结论",
        files: &[
            ("DCF 估值", "workflow/dcf_valuation.json"),
            ("分析汇总", "workflow/analysis_summary.json"),
        ],
    },
    StageSpec {
        title: "研究报告",
        detail: "生成可查看的 HTML 决策仪表盘",
        files: &[("HTML 报告", "deep_dive.html"), ("摘要", "summary.json")],
    },
];

const REVIEW_STAGES: &[StageSpec] = &[
    StageSpec {
        title: "任务准备",
        detail: "汇总模拟与实盘记录",
        files: &[("交易记录", "records.json")],
    },
    StageSpec {
        title: "行情与资讯",
        detail: "获取最新行情和事件",
        files: &[
            ("行情快照", "workflow/market_snapshot.json"),
            ("资讯与公告", "workflow/news_context.json"),
        ],
    },
    StageSpec {
        title: "执行复盘",
        detail: "对比操作与市场变化",
        files: &[("复盘分析", "workflow/review_analysis.json")],
    },
    StageSpec {
        title: "复盘报告",
        detail: "形成结论与下一步行动",
        files: &[("HTML 报告", "review.html"), ("摘要", "summary.json")],
    },
];

/// 看板只认实际存在且可解析的产物；进行中的节点是下一个尚未完成的步骤。
fn workflow_status(job_dir: &Path, report_filename: &str, job_state: &str) -> Value {
    if !job_dir.join("workflow").is_dir() {
        return Value::Null; // 旧任务没有阶段记录
    }
    let specs = match report_filename {
        "deep_dive.html" => COMPANY_STAGES,
        "review.html" => REVIEW_STAGES,
        _ => return Value::Null,
    };
    let mut first_open_seen = false;
    let mut ready_count = 0;
    let mut total_count = 0;
    let stages: Vec<Value> = specs
        .iter()
        .enumerate()
        .map(|(index, spec)| {
            let items: Vec<Value> = spec
                .files
                .iter()
                .map(|(label, path)| {
                    let ready = artifact_ready(&job_dir.join(path));
                    ready_count += usize::from(ready);
                    total_count += 1;
                    json!({ "label": label, "ready": ready })
                })
                .collect();
            let complete = items.iter().all(|item| item["ready"] == true);
            let state = if complete {
                "done"
            } else if !first_open_seen {
                first_open_seen = true;
                match job_state {
                    "running" => "active",
                    "stopping" => "stopping",
                    "waiting_for_manual_run" => "waiting",
                    "done" => "untracked",
                    "failed" | "incomplete" | "interrupted" | "cancelled" => "blocked",
                    _ => "pending",
                }
            } else if job_state == "done" {
                "untracked"
            } else {
                "pending"
            };
            json!({ "index": index + 1, "title": spec.title, "detail": spec.detail,
            "state": state, "items": items })
        })
        .collect();
    json!({ "stages": stages, "ready_count": ready_count, "total_count": total_count })
}

fn artifact_ready(path: &Path) -> bool {
    let Ok(bytes) = std::fs::read(path) else {
        return false;
    };
    if bytes.is_empty() {
        return false;
    }
    if path
        .extension()
        .is_some_and(|extension| extension == "json")
    {
        serde_json::from_slice::<Value>(&bytes).is_ok()
    } else {
        true
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workflow_advances_only_when_artifacts_are_valid() {
        let dir = std::env::temp_dir().join(format!(
            "quant-studio-workflow-{}-{}",
            std::process::id(),
            Utc::now().timestamp_micros()
        ));
        prepare_job(&dir, "test command").unwrap();
        let initial = job_status(&dir, "deep_dive.html");
        assert_eq!(initial["workflow"]["ready_count"], 1);
        assert_eq!(initial["workflow"]["stages"][1]["state"], "waiting");

        std::fs::write(dir.join("workflow/stock_quote.json"), "{").unwrap();
        let invalid = job_status(&dir, "deep_dive.html");
        assert_eq!(invalid["workflow"]["ready_count"], 1);

        std::fs::write(dir.join("workflow/stock_quote.json"), "{}").unwrap();
        std::fs::write(dir.join("workflow/financial_statements.json"), "{}").unwrap();
        let ready = job_status(&dir, "deep_dive.html");
        assert_eq!(ready["workflow"]["ready_count"], 3);
        assert_eq!(ready["workflow"]["stages"][1]["state"], "done");
        assert_eq!(ready["workflow"]["stages"][2]["state"], "waiting");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn review_workflow_uses_record_and_report_files() {
        let dir = std::env::temp_dir().join(format!(
            "quant-studio-review-workflow-{}-{}",
            std::process::id(),
            Utc::now().timestamp_micros()
        ));
        prepare_job(&dir, "test command").unwrap();
        std::fs::write(dir.join("records.json"), "{}").unwrap();
        let initial = job_status(&dir, "review.html");
        assert_eq!(initial["workflow"]["ready_count"], 1);
        assert_eq!(initial["workflow"]["stages"][0]["state"], "done");

        std::fs::write(dir.join("review.html"), "<h1>review</h1>").unwrap();
        std::fs::write(dir.join("summary.json"), "{}").unwrap();
        let complete = job_status(&dir, "review.html");
        assert_eq!(complete["status"], "done");
        assert_eq!(complete["workflow"]["stages"][3]["state"], "done");
        assert_eq!(complete["workflow"]["stages"][1]["state"], "untracked");
        std::fs::remove_dir_all(dir).unwrap();
    }
}
