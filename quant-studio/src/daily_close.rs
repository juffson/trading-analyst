//! Observes existing Codex schedules and public GitHub outputs; never starts jobs.
use crate::github_research::GithubResearch;
use anyhow::Result;
use chrono::{DateTime, Datelike, Duration as Days, FixedOffset, TimeZone, Utc};
use reqwest::Client;
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    path::PathBuf,
    time::{Duration, Instant},
};
use tokio::sync::Mutex;

const API: &str = "https://api.github.com/repos/juffson/trading-analyst";
const MARKETS: [(&str, &str); 3] = [
    ("CN", "A股收盘研究与飞书发布"),
    ("HK", "港股收盘研究与飞书发布"),
    ("US", "美股收盘研究与飞书发布"),
];

pub struct DailyClose {
    client: Client,
    cache: Mutex<Option<(Instant, Value)>>,
}
#[derive(Deserialize)]
struct Automation {
    id: String,
    name: String,
    status: String,
    rrule: String,
    kind: Option<String>,
    target_thread_id: Option<String>,
}

// Deliberately support only the weekly wall-clock rules used by these tasks.
// Unsupported rules produce an explicit unknown state, never a guessed time.
fn slots(rule: &str, now: DateTime<Utc>) -> Option<(String, String, String, bool)> {
    let fields: HashMap<_, _> = rule
        .trim_start_matches("RRULE:")
        .split(';')
        .filter_map(|field| field.split_once('='))
        .collect();
    if fields.get("FREQ") != Some(&"WEEKLY")
        || fields.get("INTERVAL").is_some_and(|value| *value != "1")
        || fields.get("BYSECOND").is_some_and(|value| *value != "0")
        || fields.keys().any(|key| {
            ![
                "FREQ", "INTERVAL", "BYDAY", "BYHOUR", "BYMINUTE", "BYSECOND",
            ]
            .contains(key)
        })
    {
        return None;
    }
    let hour: u32 = fields.get("BYHOUR")?.parse().ok()?;
    let minute: u32 = fields.get("BYMINUTE")?.parse().ok()?;
    let names = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
    let days: Option<Vec<usize>> = fields
        .get("BYDAY")?
        .split(',')
        .map(|day| names.iter().position(|name| *name == day))
        .collect();
    let days = days?;
    let zone = FixedOffset::east_opt(8 * 3600)?;
    let local = now.with_timezone(&zone);
    let at = |offset: i64| {
        let date = local.date_naive().checked_add_signed(Days::days(offset))?;
        if !days.contains(&(date.weekday().num_days_from_monday() as usize)) {
            return None;
        }
        zone.from_local_datetime(&date.and_hms_opt(hour, minute, 0)?)
            .single()
    };
    // Today's scheduled batch remains visible before its due time.
    let batch = (0..8).find_map(|n| at(-n))?;
    let next = (0..8).filter_map(at).find(|date| *date > local)?;
    Some((
        batch.date_naive().to_string(),
        batch.to_rfc3339(),
        next.to_rfc3339(),
        batch <= local,
    ))
}

fn automations(now: DateTime<Utc>) -> Vec<Value> {
    let home = std::env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|p| PathBuf::from(p).join(".codex")));
    let mut configs = Vec::new();
    if let Some(entries) = home.and_then(|p| std::fs::read_dir(p.join("automations")).ok()) {
        for entry in entries.flatten() {
            let path = entry.path().join("automation.toml");
            if let Ok(raw) = std::fs::read_to_string(path) {
                if let Ok(config) = toml::from_str::<Automation>(&raw) {
                    configs.push(config);
                }
            }
        }
    }
    MARKETS.iter().map(|(market, name)| {
        let found: Vec<_> = configs.iter().filter(|c| c.name == *name && c.kind.as_deref() == Some("heartbeat")).collect();
        if found.len() != 1 {
            return json!({"market":market, "name":name, "status":"UNKNOWN", "error":if found.is_empty(){"未读取到本机任务配置"}else{"发现同名任务，需要确认计划来源"}});
        }
        let c = found[0];
        let timing = slots(&c.rrule, now);
        let mut value = json!({"market":market,"name":name,"id":c.id,"status":c.status,
            "rrule":c.rrule,"thread_id":c.target_thread_id,"timezone":"Asia/Shanghai"});
        if let Some((day, due, next, reached)) = timing {
            let day = chrono::NaiveDate::parse_from_str(&day, "%Y-%m-%d").unwrap();
            let trade_date = if *market == "US" { day - Days::days(1) } else { day };
            value["expected_date"] = json!(trade_date.to_string());
            value["due_at"] = json!(due);
            value["due"] = json!(reached);
            if c.status == "ACTIVE" { value["next_run"] = json!(next); }
        } else {
            value["error"] = json!("该时间规则暂不支持解析，请在 Codex 查看原任务");
        }
        value
    }).collect()
}

impl DailyClose {
    pub fn new() -> Result<Self> {
        Ok(Self {
            client: Client::builder()
                .timeout(Duration::from_secs(15))
                .user_agent("Quant-Lab-Daily-Close/1.0")
                .build()?,
            cache: Mutex::new(None),
        })
    }
    async fn publications(&self) -> Result<Value> {
        let data: Value = self
            .client
            .get(format!(
                "{API}/actions/workflows/publish-feishu.yml/runs?branch=main&per_page=8"
            ))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        let runs = data["workflow_runs"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("发布记录格式错误"))?;
        let mut items = Vec::new();
        for run in runs {
            // Projection only: never forward raw API payloads or logs.
            items.push(
                json!({"id":run["id"],"status":run["status"],"conclusion":run["conclusion"],
                "created_at":run["created_at"],"updated_at":run["updated_at"],"sha":run["head_sha"],
                "url":run["html_url"],"title":run["display_title"],"event":run["event"]}),
            );
        }
        Ok(json!({"runs":items,"checked_at":Utc::now().to_rfc3339()}))
    }
    pub async fn snapshot(&self, research: &GithubResearch, refresh: bool) -> Value {
        let mut cache = self.cache.lock().await;
        if let Some((_, saved)) = cache
            .as_ref()
            .filter(|(at, _)| !refresh && at.elapsed() < Duration::from_secs(300))
        {
            let mut value = saved.clone();
            value["schedules"] = json!(automations(Utc::now()));
            return value;
        }
        let (index, publications) = tokio::join!(research.index(true), self.publications());
        let mut value =
            json!({"checked_at":Utc::now().to_rfc3339(),"schedules":automations(Utc::now())});
        match index {
            Ok(index) => value["research"] = index,
            Err(error) => value["research_error"] = json!(error.to_string()),
        }
        match publications {
            Ok(items) => value["publications"] = items,
            Err(error) => {
                value["publication_error"] = json!(format!("发布状态读取失败：{error}"));
                if let Some((_, saved)) = cache.as_ref() {
                    if !saved["publications"].is_null() {
                        value["publications"] = saved["publications"].clone();
                        value["publications"]["stale"] = json!(true);
                    }
                }
            }
        }
        *cache = Some((Instant::now(), value.clone()));
        value
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const RULE: &str = "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=15;BYMINUTE=30;BYSECOND=0";
    fn time(s: &str) -> DateTime<Utc> {
        s.parse().unwrap()
    }
    #[test]
    fn before_and_after_due_in_shanghai() {
        let (day, _, next, due) = slots(RULE, time("2026-11-04T07:00:00Z")).unwrap();
        assert_eq!(day, "2026-11-04");
        assert!(!due);
        assert_eq!(next, "2026-11-04T15:30:00+08:00");
        let (_, _, next, due) = slots(RULE, time("2026-11-04T07:30:00Z")).unwrap();
        assert!(due);
        assert_eq!(next, "2026-11-05T15:30:00+08:00");
    }
    #[test]
    fn weekends_keep_last_scheduled_batch() {
        let (day, _, next, _) = slots(RULE, time("2026-11-08T10:00:00Z")).unwrap();
        assert_eq!(day, "2026-11-06");
        assert_eq!(next, "2026-11-09T15:30:00+08:00");
    }
    #[test]
    fn us_schedule_includes_saturday_and_rolls_year() {
        let rule = "FREQ=WEEKLY;BYDAY=TU,WE,TH,FR,SA;BYHOUR=7;BYMINUTE=30";
        let (day, _, next, _) = slots(rule, time("2027-01-02T00:00:00Z")).unwrap();
        assert_eq!(day, "2027-01-02");
        assert_eq!(next, "2027-01-05T07:30:00+08:00");
    }
    #[test]
    fn unsupported_rules_are_not_guessed() {
        for rule in [
            "FREQ=DAILY;BYHOUR=15;BYMINUTE=30",
            "FREQ=WEEKLY;BYDAY=XX;BYHOUR=15;BYMINUTE=30",
            "FREQ=WEEKLY;BYDAY=MO;BYHOUR=25;BYMINUTE=30",
        ] {
            assert!(slots(rule, Utc::now()).is_none());
        }
        assert!(slots(&format!("{RULE};COUNT=1"), Utc::now()).is_none());
    }
}
