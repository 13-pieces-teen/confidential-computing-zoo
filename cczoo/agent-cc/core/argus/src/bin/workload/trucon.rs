//! Read a fixed, fully uploaded measurement history from the root-only TruCon UDS.
use anyhow::{bail, Context, Result};
use serde::Deserialize;
use std::{collections::HashSet, path::Path};

pub const DEFAULT_SOCKET: &str = "/var/run/trucon/trucon.sock";

#[derive(Debug, Deserialize, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub chain_id: String,
    pub sequence_num: usize,
    pub mr_value: String,
    pub head_log_id: String,
    pub log_ids: Vec<String>,
}

impl Snapshot {
    fn validate(&self) -> Result<()> {
        let hex = |s: &str| {
            s.bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        };
        if self.chain_id != "default"
            || !(2..=4096).contains(&self.sequence_num)
            || self.sequence_num != self.log_ids.len()
            || self.mr_value.len() != 96
            || !hex(&self.mr_value)
            || self.log_ids.last() != Some(&self.head_log_id)
            || self.log_ids.iter().any(|id| {
                let index = (1..64).contains(&id.len()) && id.bytes().all(|c| c.is_ascii_digit());
                let uuid = matches!(id.len(), 64 | 80) && hex(id);
                !(index || uuid)
            })
            || self.log_ids.iter().collect::<HashSet<_>>().len() != self.sequence_num
        {
            bail!("invalid TruCon measurement snapshot");
        }
        Ok(())
    }
}

#[cfg(unix)]
pub fn snapshot(path: &Path) -> Result<Snapshot> {
    use std::io::{Read, Write};
    use std::os::unix::{
        fs::{FileTypeExt, MetadataExt},
        net::UnixStream,
    };
    use std::time::Duration;
    let meta = std::fs::symlink_metadata(path)?;
    if !meta.file_type().is_socket() || meta.uid() != 0 || meta.mode() & 0o077 != 0 {
        bail!("TruCon socket must be root-owned and mode 0600");
    }
    let mut stream = UnixStream::connect(path).context("connect TruCon")?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    stream.write_all(b"GET /chain-state?include_history=true HTTP/1.1\r\nHost: localhost\r\nX-TruCon-Caller-Service: argus_provider\r\nConnection: close\r\n\r\n")?;
    let mut bytes = Vec::new();
    (&mut stream).take(524289).read_to_end(&mut bytes)?;
    decode_response(&bytes)
}

#[cfg(not(unix))]
pub fn snapshot(_: &Path) -> Result<Snapshot> {
    bail!("TruCon UDS requires Linux")
}

fn decode_response(bytes: &[u8]) -> Result<Snapshot> {
    if bytes.len() > 524288 {
        bail!("TruCon response exceeds limit");
    }
    let split = bytes
        .windows(4)
        .position(|v| v == b"\r\n\r\n")
        .context("invalid HTTP response")?;
    if split > 8192 {
        bail!("TruCon headers exceed limit");
    }
    let headers = std::str::from_utf8(&bytes[..split])?;
    let mut lines = headers.split("\r\n");
    if lines.next().and_then(|l| l.split_whitespace().nth(1)) != Some("200") {
        bail!("TruCon history is not ready for attestation");
    }
    let mut content_length = None;
    for line in lines {
        let (name, value) = line.split_once(':').context("invalid HTTP header")?;
        if name.eq_ignore_ascii_case("transfer-encoding") {
            bail!("unsupported transfer encoding");
        }
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                bail!("duplicate content length");
            }
            content_length = Some(value.trim().parse::<usize>()?);
        }
    }
    let body = &bytes[split + 4..];
    if content_length != Some(body.len()) {
        bail!("incomplete TruCon response");
    }
    let snapshot: Snapshot = serde_json::from_slice(body)?;
    snapshot.validate()?;
    Ok(snapshot)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reads_existing_chain_state_with_mixed_references() {
        let body = serde_json::json!({
            "chain_id": "default", "sequence_num": 2, "mr_value": "0".repeat(96),
            "head_record_id": "record-2", "head_log_id": "b".repeat(64),
            "updated_at": "2026-09-24T00:00:00", "log_ids": ["0", "b".repeat(64)]
        })
        .to_string();
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body
        );
        let mut value = decode_response(response.as_bytes()).unwrap();
        assert_eq!(value.log_ids[0], "0");
        value.head_log_id = "other-head".into();
        assert!(value.validate().is_err());
    }

    #[test]
    fn snapshot_requires_complete_distinct_history() {
        let mut value = Snapshot {
            chain_id: "default".into(),
            sequence_num: 2,
            mr_value: "0".repeat(96),
            head_log_id: "b".repeat(80),
            log_ids: vec!["0".into(), "b".repeat(80)],
        };
        assert!(value.validate().is_ok());
        value.log_ids[1] = value.log_ids[0].clone();
        value.head_log_id = value.log_ids[1].clone();
        assert!(value.validate().is_err());
        assert!(decode_response(b"HTTP/1.1 409 Conflict\r\nContent-Length: 0\r\n\r\n").is_err());
        assert!(decode_response(b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n{}").is_err());
    }
}
