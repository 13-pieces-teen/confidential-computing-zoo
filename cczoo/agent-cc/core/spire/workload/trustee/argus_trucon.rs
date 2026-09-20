//! Runs only after the TDX verifier has authenticated Quote and REPORTDATA.
use crate::{HashAlgorithm, Tee};
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::Command,
};

static SLOTS: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(4);

pub fn prepare(
    tee: &Tee,
    evidence: &Value,
    runtime: &Value,
    algorithm: &HashAlgorithm,
) -> Result<Option<Value>> {
    let references = evidence.get("rekor_entry_uuids");
    let workload = runtime.get("protocol").and_then(Value::as_str) == Some("argus.workload.tdx.v1");
    if !workload && references.is_none() {
        return Ok(None);
    }
    if *tee != Tee::Tdx || !workload || algorithm.digest(b"").len() != 48 {
        bail!("TruCon evidence requires TDX and SHA-384 bound workload runtime_data");
    }
    let refs = references
        .and_then(Value::as_array)
        .context("missing Rekor UUID list")?;
    if !(2..=4096).contains(&refs.len()) {
        bail!("invalid Rekor reference count");
    }
    let mut seen = std::collections::HashSet::new();
    for reference in refs {
        let s = reference.as_str().context("Rekor UUID must be a string")?;
        if !matches!(s.len(), 64 | 80)
            || !s
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
            || !seen.insert(s)
        {
            bail!("invalid or duplicate Rekor UUID");
        }
    }
    Ok(Some(
        json!({"rekor_entry_uuids": refs, "runtime_data": runtime}),
    ))
}

fn protected(path: &Path) -> Result<()> {
    let meta = std::fs::symlink_metadata(path)?;
    if !meta.is_file() {
        bail!("verifier/config must be regular files");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.uid() != 0 || meta.mode() & 0o022 != 0 {
            bail!("verifier/config must be root-owned without group/other write");
        }
    }
    Ok(())
}

async fn bounded(mut stream: impl AsyncRead + Unpin, limit: u64) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    (&mut stream)
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .await?;
    if bytes.len() as u64 > limit {
        bail!("TruCon verifier output exceeds limit");
    }
    Ok(bytes)
}

pub async fn appraise(request: Option<&Value>, claims: &mut Value, tee_class: &str) -> Result<()> {
    // Never retain an input or preexisting verdict under our reserved namespace.
    claims
        .as_object_mut()
        .context("TEE claims must be an object")?
        .remove("trucon");
    let Some(request) = request else {
        return Ok(());
    };
    if tee_class != "cpu" {
        bail!("TruCon requires TDX CPU claims");
    }
    let mut request = request.clone();
    request["rtmr2"] = claims
        .pointer("/quote/body/rtmr_2")
        .context("authenticated RTMR2 is missing")?
        .clone();
    let bytes = serde_json::to_vec(&request)?;
    if bytes.len() > 524288 {
        bail!("TruCon request exceeds size limit");
    }
    let executable =
        std::env::var("ARGUS_TRUCON_VERIFIER").context("TruCon verifier is not configured")?;
    let config =
        std::env::var("ARGUS_TRUCON_CONFIG").context("TruCon trust configuration is missing")?;
    if !Path::new(&executable).is_absolute() || !Path::new(&config).is_absolute() {
        bail!("verifier paths must be absolute");
    }
    protected(Path::new(&executable))?;
    protected(Path::new(&config))?;
    let result = tokio::time::timeout(Duration::from_secs(50), async {
        let _permit = SLOTS.acquire().await?;
        let mut child = Command::new(&executable)
            .arg(&config)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .context("start TruCon verifier")?;
        let mut stdin = child.stdin.take().context("verifier stdin")?;
        let stdout = child.stdout.take().context("verifier stdout")?;
        let stderr = child.stderr.take().context("verifier stderr")?;
        let write = async {
            stdin.write_all(&bytes).await?;
            drop(stdin);
            Ok::<(), anyhow::Error>(())
        };
        let (_, output, errors) =
            tokio::try_join!(write, bounded(stdout, 16384), bounded(stderr, 4096))?;
        let status = child.wait().await?;
        if !status.success() {
            bail!(
                "TruCon verifier rejected evidence: {}",
                String::from_utf8_lossy(&errors)
            );
        }
        let value: Value = serde_json::from_slice(&output).context("invalid verifier result")?;
        if value.get("verified") != Some(&Value::Bool(true)) {
            bail!("TruCon verifier did not affirm evidence");
        }
        Ok::<Value, anyhow::Error>(value)
    })
    .await
    .context("TruCon verifier timeout")??;
    claims["trucon"] = result;
    Ok(())
}
