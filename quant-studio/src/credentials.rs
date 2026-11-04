use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

pub const DEFAULT_HTTP_URL: &str = "https://openapi.longbridge.cn";

#[derive(Clone, Deserialize, Serialize)]
pub struct Credentials {
    pub app_key: String,
    pub app_secret: String,
    pub access_token: String,
    pub http_url: String,
}

#[derive(Deserialize)]
pub struct CredentialsUpdate {
    pub app_key: Option<String>,
    pub app_secret: Option<String>,
    pub access_token: Option<String>,
    pub http_url: Option<String>,
}

impl Credentials {
    pub fn load(path: &Path) -> Result<Self> {
        if path.exists() {
            return serde_json::from_slice(&fs::read(path).context("读取应用内凭据失败")?)
                .context("解析应用内凭据失败");
        }
        let _ = dotenv::dotenv();
        Ok(Self {
            app_key: required_env("LONGPORT_APP_KEY")?,
            app_secret: required_env("LONGPORT_APP_SECRET")?,
            access_token: required_env("LONGPORT_ACCESS_TOKEN")?,
            http_url: std::env::var("LONGPORT_HTTP_URL")
                .ok()
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| DEFAULT_HTTP_URL.to_string()),
        })
    }

    pub fn status(path: &Path) -> Result<Value> {
        if !path.exists() {
            let _ = dotenv::dotenv();
            if [
                "LONGPORT_APP_KEY",
                "LONGPORT_APP_SECRET",
                "LONGPORT_ACCESS_TOKEN",
            ]
            .iter()
            .any(|name| {
                std::env::var(name)
                    .ok()
                    .is_none_or(|value| value.trim().is_empty())
            }) {
                return Ok(json!({ "configured": false, "source": "none" }));
            }
        }
        let source = if path.exists() { "app" } else { "environment" };
        let credentials = Self::load(path)?;
        Ok(json!({
            "configured": true,
            "source": source,
            "app_key_hint": mask(&credentials.app_key),
            "http_url": credentials.http_url,
        }))
    }

    pub fn update(path: &Path, update: CredentialsUpdate) -> Result<Value> {
        let previous = if path.exists() {
            Some(Self::load(path)?)
        } else {
            Self::load(path).ok()
        };
        let pick = |new: Option<String>, old: Option<&str>, name: &str| -> Result<String> {
            let value = new
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .or(old)
                .context(format!("请填写 {name}"))?;
            if value.chars().any(char::is_control) {
                bail!("{name} 不能包含换行或控制字符");
            }
            Ok(value.to_string())
        };
        let credentials = Self {
            app_key: pick(
                update.app_key,
                previous.as_ref().map(|c| c.app_key.as_str()),
                "App Key",
            )?,
            app_secret: pick(
                update.app_secret,
                previous.as_ref().map(|c| c.app_secret.as_str()),
                "App Secret",
            )?,
            access_token: pick(
                update.access_token,
                previous.as_ref().map(|c| c.access_token.as_str()),
                "Access Token",
            )?,
            http_url: pick(
                update.http_url,
                previous
                    .as_ref()
                    .map(|c| c.http_url.as_str())
                    .or(Some(DEFAULT_HTTP_URL)),
                "API 站点",
            )?,
        };
        if ![
            "https://openapi.longbridge.cn",
            "https://openapi.longbridge.com",
        ]
        .contains(&credentials.http_url.as_str())
            && previous.as_ref().map(|c| c.http_url.as_str()) != Some(credentials.http_url.as_str())
        {
            bail!("API 站点只支持 Longbridge 中国站或全球站");
        }
        save(path, &credentials)?;
        Self::status(path)
    }
}

fn required_env(name: &str) -> Result<String> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .context(format!("缺少 {name}"))
}

fn mask(value: &str) -> String {
    let tail: String = value
        .chars()
        .rev()
        .take(4)
        .collect::<String>()
        .chars()
        .rev()
        .collect();
    format!("••••{tail}")
}

fn save(path: &Path, credentials: &Credentials) -> Result<()> {
    let parent = path.parent().context("凭据目录不存在")?;
    fs::create_dir_all(parent).context("创建凭据目录失败")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    }
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let temp = path.with_extension(format!("json.tmp.{}.{nanos}", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temp).context("创建临时凭据文件失败")?;
    file.write_all(&serde_json::to_vec(credentials)?)?;
    file.sync_all()?;
    fs::rename(&temp, path).context("保存凭据失败")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_token_replaces_previous_value_without_exposing_secrets() {
        let dir =
            std::env::temp_dir().join(format!("quant-studio-credentials-{}", std::process::id()));
        let path = dir.join("longbridge.json");
        let first = CredentialsUpdate {
            app_key: Some("test-key".into()),
            app_secret: Some("test-secret".into()),
            access_token: Some("old-token".into()),
            http_url: None,
        };
        Credentials::update(&path, first).unwrap();
        let status = Credentials::update(
            &path,
            CredentialsUpdate {
                app_key: None,
                app_secret: None,
                access_token: Some("new-token".into()),
                http_url: None,
            },
        )
        .unwrap();
        assert_eq!(Credentials::load(&path).unwrap().access_token, "new-token");
        assert_eq!(Credentials::load(&path).unwrap().app_secret, "test-secret");
        assert_eq!(status["source"], "app");
        assert!(!status.to_string().contains("test-secret"));
        assert!(!status.to_string().contains("new-token"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        fs::remove_dir_all(dir).unwrap();
    }
}
