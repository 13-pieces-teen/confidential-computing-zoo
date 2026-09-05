//! Independent Linux observation and the Workload v1 JCS/SHA-384 contract.
use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use serde_json::Value;
use sha2::{Digest, Sha256, Sha384};
use std::{collections::BTreeMap, fs, io::Read, path::Path};
use tdx_quote::ReportData;

pub type Target = BTreeMap<String, String>;
pub const PROTOCOL: &str = "argus.workload.tdx.v1";
const AGENT: &str = "spiffe://argus.local/spire/agent/argus_tdx/openviking-node";
const FIELDS: &[&str] = &[
    "agent_id",
    "boot_id",
    "config_digest",
    "config_path",
    "container_id",
    "executable",
    "image_config_digest",
    "launch_id",
    "listen_port",
    "net_namespace",
    "pid",
    "pid_namespace",
    "policy_id",
    "rootfs_read_only",
    "start_time",
    "workload_id",
];

fn decimal(s: &str) -> bool {
    !s.starts_with('0') && !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}
fn hex(s: &str, n: usize) -> bool {
    s.len() == n
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn digest(s: &str) -> bool {
    s.strip_prefix("sha256:").is_some_and(|v| hex(v, 64))
}
pub fn validate(t: &Target) -> Result<()> {
    if t.len() != FIELDS.len() || FIELDS.iter().any(|f| !t.contains_key(*f)) {
        bail!("invalid target schema");
    }
    if t.values()
        .any(|v| v.bytes().any(|b| !(32..=126).contains(&b)))
    {
        bail!("target values must be printable ASCII");
    }
    if t["agent_id"] != AGENT || t["rootfs_read_only"] != "true" {
        bail!("invalid node or filesystem binding");
    }
    let boot = &t["boot_id"];
    if boot.len() != 36
        || [8, 13, 18, 23].iter().any(|i| boot.as_bytes()[*i] != b'-')
        || !hex(&boot.replace('-', ""), 32)
    {
        bail!("invalid boot ID");
    }
    if !hex(&t["container_id"], 64)
        || !digest(&t["image_config_digest"])
        || !digest(&t["config_digest"])
    {
        bail!("actual image/config digests required");
    }
    for f in ["pid", "start_time", "listen_port"] {
        if !decimal(&t[f]) {
            bail!("invalid decimal {f}");
        }
    }
    let pid = t["pid"].parse::<i32>()?;
    let port = t["listen_port"].parse::<u16>()?;
    if pid <= 0 || port == 0 {
        bail!("invalid PID or port");
    }
    for f in ["workload_id", "policy_id", "launch_id"] {
        let v = &t[f];
        if v.is_empty()
            || v.len() > 128
            || !v
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        {
            bail!("invalid {f}");
        }
    }
    for (f, prefix) in [("pid_namespace", "pid:["), ("net_namespace", "net:[")] {
        if !t[f]
            .strip_prefix(prefix)
            .and_then(|s| s.strip_suffix(']'))
            .is_some_and(decimal)
        {
            bail!("invalid namespace");
        }
    }
    for f in ["config_path", "executable"] {
        let p = &t[f];
        if !p.starts_with('/')
            || p == "/"
            || p.ends_with('/')
            || p.split('/')
                .skip(1)
                .any(|s| s.is_empty() || s == "." || s == "..")
        {
            bail!("invalid target path");
        }
    }
    Ok(())
}
pub fn runtime_data(t: &Target, nonce: &str) -> Result<Target> {
    validate(t)?;
    let decoded = URL_SAFE_NO_PAD.decode(nonce)?;
    if decoded.len() != 32 || URL_SAFE_NO_PAD.encode(decoded) != nonce {
        bail!("invalid nonce");
    }
    let mut data = t.clone();
    data.insert("nonce".into(), nonce.into());
    data.insert("protocol".into(), PROTOCOL.into());
    Ok(data)
}
pub fn report_data(data: &Target) -> Result<ReportData> {
    let mut target = data.clone();
    let nonce = target.remove("nonce").context("missing nonce")?;
    if target.remove("protocol").as_deref() != Some(PROTOCOL) {
        bail!("invalid protocol");
    }
    runtime_data(&target, &nonce)?;
    // Sorted keys + ASCII string values give identical RFC 8785 JSON in Rust/Go/Trustee.
    Ok(ReportData::from_digest(&Sha384::digest(
        serde_json::to_vec(data)?,
    ))?)
}
fn bounded(path: impl AsRef<Path>, max: u64) -> Result<Vec<u8>> {
    let mut v = Vec::new();
    fs::File::open(path)?.take(max + 1).read_to_end(&mut v)?;
    if v.len() as u64 > max {
        bail!("input exceeds limit");
    }
    Ok(v)
}
#[cfg(not(target_os = "linux"))]
pub fn load_and_check(_: &Path) -> Result<Target> {
    bail!("Workload evidence requires Linux");
}
#[cfg(target_os = "linux")]
pub fn load_and_check(path: &Path) -> Result<Target> {
    use std::{os::unix::fs::MetadataExt, process::Command};
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || meta.uid() != 0 || meta.mode() & 0o022 != 0 {
        bail!("target registration must be root-owned and protected");
    }
    let parent = fs::metadata(path.parent().context("registration parent required")?)?;
    if parent.uid() != 0 || parent.mode() & 0o022 != 0 {
        bail!("registration directory must be root-owned and protected");
    }
    let t: Target = serde_json::from_slice(&bounded(path, 32768)?)?;
    validate(&t)?;
    // Inspect independently. TC API/Helper cannot substitute an image-name hash.
    let out = Command::new("timeout")
        .args(["10s", "docker", "inspect", &t["container_id"]])
        .output()?;
    if !out.status.success() || out.stdout.len() > 1 << 20 {
        bail!("docker inspect failed");
    }
    let values: Vec<Value> = serde_json::from_slice(&out.stdout)?;
    if values.len() != 1 {
        bail!("expected one container");
    }
    let c = &values[0];
    if c["Id"].as_str() != Some(&t["container_id"])
        || c["Image"].as_str() != Some(&t["image_config_digest"])
        || c["State"]["Running"] != true
        || c["HostConfig"]["ReadonlyRootfs"] != true
        || c["HostConfig"]["Privileged"] != false
        || c["Config"]["Labels"]["io.trucon.launch-id"].as_str() != Some(&t["launch_id"])
        || c["Config"]["Labels"]["io.trucon.workload-id"].as_str() != Some(&t["workload_id"])
    {
        bail!("container differs from registration");
    }
    let init = c["State"]["Pid"]
        .as_u64()
        .context("missing container PID")?;
    let env = c["Config"]["Env"]
        .as_array()
        .context("missing workload environment")?;
    if !env
        .iter()
        .any(|e| e.as_str() == Some(&format!("OPENVIKING_CONFIG_FILE={}", t["config_path"])))
    {
        bail!("workload configuration path is not active");
    }
    if c["HostConfig"]["NetworkMode"] == "host" || c["HostConfig"]["PidMode"] == "host" {
        bail!("isolated container namespaces required");
    }
    let mounts = c["Mounts"].as_array().context("missing mounts")?;
    for mount in mounts {
        let dest = mount["Destination"].as_str().context("invalid mount")?;
        if dest != t["config_path"]
            && dest != "/var/lib/openviking"
            && !(dest == "/tmp" && mount["Type"] == "tmpfs")
        {
            bail!("unapproved workload mount");
        }
        if dest == t["config_path"] && mount["RW"] != false {
            bail!("configuration mount must be read-only");
        }
    }
    if !mounts
        .iter()
        .any(|m| m["Destination"].as_str() == Some(&t["config_path"]) && m["RW"] == false)
    {
        bail!("missing read-only configuration mount");
    }
    let pid = &t["pid"];
    let root = format!("/proc/{pid}");
    if fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim() != t["boot_id"] {
        bail!("boot changed");
    }
    let stat = fs::read_to_string(format!("{root}/stat"))?;
    let fields: Vec<_> = stat
        .rsplit_once(") ")
        .context("invalid process stat")?
        .1
        .split_whitespace()
        .collect();
    if fields.len() < 20 || fields[0] == "Z" || fields[0] == "X" || fields[19] != t["start_time"] {
        bail!("process incarnation changed");
    }
    for (f, n) in [("pid_namespace", "pid"), ("net_namespace", "net")] {
        if fs::read_link(format!("{root}/ns/{n}"))?.to_str() != Some(&t[f])
            || fs::read_link(format!("/proc/{init}/ns/{n}"))?
                != fs::read_link(format!("{root}/ns/{n}"))?
        {
            bail!("namespace changed");
        }
    }
    if fs::read_link(format!("{root}/exe"))?.to_str() != Some(&t["executable"])
        || !fs::read_to_string(format!("{root}/cgroup"))?.contains(&t["container_id"])
    {
        bail!("process/container binding changed");
    }
    let config = bounded(format!("{root}/root{}", t["config_path"]), 4 << 20)?;
    if format!("sha256:{}", hex::encode(Sha256::digest(config))) != t["config_digest"] {
        bail!("configuration changed");
    }
    if listener_pid(pid, &t["listen_port"], &t["pid_namespace"])? != *pid {
        bail!("target is not the unique loopback listener");
    }
    Ok(t)
}
#[cfg(target_os = "linux")]
fn listener_pid(anchor: &str, port: &str, namespace: &str) -> Result<String> {
    let expected = port.parse::<u16>()?;
    let mut sockets = Vec::new();
    for proto in ["tcp", "tcp6"] {
        for line in fs::read_to_string(format!("/proc/{anchor}/net/{proto}"))?
            .lines()
            .skip(1)
        {
            let f: Vec<_> = line.split_whitespace().collect();
            if f.len() < 10 || f[3] != "0A" {
                continue;
            }
            let (addr, p) = f[1].split_once(':').context("invalid tcp row")?;
            if u16::from_str_radix(p, 16)? != expected {
                continue;
            }
            if proto != "tcp" || addr != "0100007F" {
                bail!("workload must listen only on IPv4 loopback");
            }
            sockets.push(format!("socket:[{}]", f[9]));
        }
    }
    if sockets.len() != 1 {
        bail!("expected exactly one service socket");
    }
    let mut owners = Vec::new();
    for e in fs::read_dir("/proc")?.flatten() {
        let pid = e.file_name().to_string_lossy().into_owned();
        if !decimal(&pid) {
            continue;
        }
        if fs::read_link(e.path().join("ns/pid"))
            .ok()
            .and_then(|p| p.to_str().map(str::to_owned))
            .as_deref()
            != Some(namespace)
        {
            continue;
        }
        let Ok(fds) = fs::read_dir(e.path().join("fd")) else {
            continue;
        };
        if fds.flatten().any(|fd| {
            fs::read_link(fd.path())
                .ok()
                .and_then(|p| p.to_str().map(str::to_owned))
                .as_deref()
                == Some(&sockets[0])
        }) {
            owners.push(pid);
        }
    }
    if owners.len() != 1 {
        bail!("listener is shared or absent");
    }
    Ok(owners.remove(0))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_go_trustee_vector() {
        let v: Value = serde_json::from_str(include_str!(
            "../../../../spire/workload/testdata/runtime-data.json"
        ))
        .unwrap();
        let data: Target = serde_json::from_value(v["runtime_data"].clone()).unwrap();
        assert_eq!(
            serde_json::to_string(&data).unwrap(),
            v["canonical"].as_str().unwrap()
        );
        assert_eq!(
            hex::encode(report_data(&data).unwrap().as_aligned_bytes()),
            v["report_data_hex"].as_str().unwrap()
        );
        for f in [
            "pid",
            "nonce",
            "launch_id",
            "image_config_digest",
            "config_digest",
            "policy_id",
        ] {
            let mut changed = data.clone();
            changed.insert(f.into(), format!("{}x", changed[f]));
            assert!(
                report_data(&changed).is_err()
                    || report_data(&changed).unwrap().as_aligned_bytes()
                        != report_data(&data).unwrap().as_aligned_bytes()
            );
        }
    }
    #[test]
    fn rejects_extra_fields_and_image_names() {
        let v: Value = serde_json::from_str(include_str!(
            "../../../../spire/workload/testdata/runtime-data.json"
        ))
        .unwrap();
        let mut t: Target = serde_json::from_value(v["runtime_data"].clone()).unwrap();
        t.remove("protocol");
        t.remove("nonce");
        t.insert("image_config_digest".into(), "openviking:latest".into());
        assert!(validate(&t).is_err());
        t.insert("extra".into(), "true".into());
        assert!(validate(&t).is_err());
    }
}
