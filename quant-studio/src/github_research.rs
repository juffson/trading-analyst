//! Read-only view of the public research archive in juffson/trading-analyst.
use anyhow::{Context, Result, bail};
use chrono::{NaiveDate, Utc};
use reqwest::Client;
use serde_json::{Value, json};
use std::collections::HashSet;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

const TREE_URL: &str =
    "https://api.github.com/repos/juffson/trading-analyst/git/trees/main?recursive=1";
const RAW_BASE: &str = "https://raw.githubusercontent.com/juffson/trading-analyst/main/research";
const CACHE_TTL: Duration = Duration::from_secs(600);

struct CachedIndex {
    value: Value,
    fetched_at: Instant,
}

pub struct GithubResearch {
    client: Client,
    cache: Mutex<Option<CachedIndex>>,
}

impl GithubResearch {
    pub fn new() -> Result<Self> {
        Ok(Self {
            client: Client::builder()
                .timeout(Duration::from_secs(15))
                .user_agent("Quant-Lab-Research-Archive/1.0")
                .build()?,
            cache: Mutex::new(None),
        })
    }

    async fn get_json(&self, url: &str) -> Result<Value> {
        let response = self.client.get(url).send().await?.error_for_status()?;
        let bytes = response.bytes().await?;
        if bytes.len() > 2_000_000 {
            bail!("GitHub 研究数据超过读取上限");
        }
        serde_json::from_slice(&bytes).context("GitHub 研究数据不是有效 JSON")
    }

    async fn fetch_index(&self) -> Result<Value> {
        let symbols_url = format!("{RAW_BASE}/symbols.json");
        let watchlist_url = format!("{RAW_BASE}/watchlist.json");
        let (tree, symbols, watchlist) = tokio::try_join!(
            self.get_json(TREE_URL),
            self.get_json(&symbols_url),
            self.get_json(&watchlist_url),
        )?;
        if tree["truncated"].as_bool() != Some(false) {
            bail!("GitHub 文件索引不完整");
        }
        let tracked: HashSet<&str> = watchlist["symbols"]
            .as_array()
            .context("跟踪名单格式错误")?
            .iter()
            .filter_map(Value::as_str)
            .collect();
        let mut catalog = Vec::new();
        let mut codes = HashSet::new();
        for symbol in symbols["symbols"].as_array().context("标的索引格式错误")? {
            let code = symbol["symbol"].as_str().context("标的代码缺失")?;
            codes.insert(code.to_string());
            catalog.push(json!({
                "symbol": code,
                "name": symbol["name"],
                "market": symbol["market"],
                "currency": symbol["currency"],
                "tracked": tracked.contains(code),
            }));
        }
        let mut reports = Vec::new();
        for item in tree["tree"].as_array().context("GitHub 文件索引格式错误")? {
            if item["type"].as_str() != Some("blob") {
                continue;
            }
            let Some(path) = item["path"].as_str() else {
                continue;
            };
            let parts: Vec<_> = path.split('/').collect();
            if parts.len() != 4
                || parts[0] != "research"
                || parts[1] != "reports"
                || !codes.contains(parts[2])
            {
                continue;
            }
            let Some((date, revision)) = parse_filename(parts[3]) else {
                continue;
            };
            reports.push(json!({
                "symbol": parts[2], "date": date, "revision": revision,
                "filename": parts[3],
            }));
        }
        reports.sort_by(|a, b| {
            let ka = (
                a["date"].as_str().unwrap_or(""),
                a["revision"].as_u64().unwrap_or(0),
            );
            let kb = (
                b["date"].as_str().unwrap_or(""),
                b["revision"].as_u64().unwrap_or(0),
            );
            kb.cmp(&ka)
        });
        Ok(json!({
            "source": "https://github.com/juffson/trading-analyst/tree/main/research",
            "fetched_at": Utc::now().to_rfc3339(),
            "stale": false,
            "symbols": catalog,
            "reports": reports,
        }))
    }

    pub async fn index(&self, refresh: bool) -> Result<Value> {
        let mut cache = self.cache.lock().await;
        if !refresh {
            if let Some(saved) = cache
                .as_ref()
                .filter(|saved| saved.fetched_at.elapsed() < CACHE_TTL)
            {
                return Ok(saved.value.clone());
            }
        }
        match self.fetch_index().await {
            Ok(value) => {
                *cache = Some(CachedIndex {
                    value: value.clone(),
                    fetched_at: Instant::now(),
                });
                Ok(value)
            }
            Err(error) => {
                if let Some(saved) = cache.as_ref() {
                    let mut value = saved.value.clone();
                    value["stale"] = json!(true);
                    Ok(value)
                } else {
                    Err(error).context("读取 GitHub 研究档案失败")
                }
            }
        }
    }

    pub async fn report(&self, symbol: &str, filename: &str) -> Result<Value> {
        let index = self.index(false).await?;
        let exists = index["reports"].as_array().is_some_and(|reports| {
            reports.iter().any(|item| {
                item["symbol"].as_str() == Some(symbol)
                    && item["filename"].as_str() == Some(filename)
            })
        });
        if !exists {
            bail!("研究报告不存在于 GitHub 索引中");
        }
        let url = format!("{RAW_BASE}/reports/{symbol}/{filename}");
        let report = self.get_json(&url).await?;
        let (date, revision) = parse_filename(filename).context("研究报告路径无效")?;
        if report["symbol"].as_str() != Some(symbol)
            || report["trade_date"].as_str() != Some(date.as_str())
            || report["revision"].as_u64() != Some(revision)
        {
            bail!("GitHub 报告内容与索引不一致");
        }
        Ok(report)
    }
}

fn parse_filename(filename: &str) -> Option<(String, u64)> {
    let stem = filename.strip_suffix(".json")?;
    let (date, revision) = if let Some((date, suffix)) = stem.split_once(".v") {
        let revision = suffix.parse::<u64>().ok()?;
        if revision < 2 {
            return None;
        }
        (date, revision)
    } else {
        (stem, 1)
    };
    if date.len() != 10
        || NaiveDate::parse_from_str(date, "%Y-%m-%d")
            .ok()?
            .to_string()
            != date
    {
        return None;
    }
    Some((date.to_string(), revision))
}

#[cfg(test)]
mod tests {
    use super::parse_filename;

    #[test]
    fn report_revisions_have_stable_identity() {
        assert_eq!(
            parse_filename("2026-11-04.json"),
            Some(("2026-11-04".into(), 1))
        );
        assert_eq!(
            parse_filename("2026-10-23.v2.json"),
            Some(("2026-10-23".into(), 2))
        );
        assert_eq!(parse_filename("2026-10-23.v1.json"), None);
        assert_eq!(parse_filename("../../token.json"), None);
    }
}
