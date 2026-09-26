"""Fresh processes load real generated arms; no Docker or systemd is invoked."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import trusted_runtime
import variants


@pytest.fixture
def installed_root():
    if sys.platform != "linux" or os.geteuid() != 0:
        pytest.skip("real protected installed paths require Linux root")
    path = Path(tempfile.mkdtemp(prefix="argus-runtime-test-", dir="/root"))
    try:
        yield path
    finally:
        assert path.parent == Path("/root") and path.name.startswith("argus-runtime-test-")
        shutil.rmtree(path)


def installed_arm(parent, name):
    config = json.loads((variants.WORKLOAD / "config/environment.example.json").read_text())
    config["approved_policy_artifact"] = {"path": "/etc/approved-existing.rego", "sha256": "a" * 64}
    output = parent / name
    kwargs = {"static_credentials": {"cert": "/etc/fixture/cert.pem", "key": "/etc/fixture/key.pem", "bundle": "/etc/fixture/bundle.pem"}} if name == "static_mtls" else {}
    arm = variants.render_variant(config, name, output, experiment_id="testarm", **kwargs)
    package = output / "package"
    # The real installer uses mode 0755; copytree from a Windows checkout can
    # otherwise preserve 0777 source-directory bits in this Linux fixture.
    for path in (package, *package.rglob("*")):
        if path.is_dir():
            path.chmod(0o755)
    c = arm["config"]
    # Only installation paths and the installed manifest are fixtures. Source
    # bytes come from the production variant renderer without import stubs.
    c["paths"]["install_dir"] = str(package)
    c["server_script"] = str(package / "scripts/workload.py")
    config_path = output / "installed-environment.json"
    config_path.write_text(json.dumps(c))
    provenance = variants.load_module("fixture_provenance", variants.WORKLOAD / "scripts/build_manifest.py")
    manifest = {"schema_version": 1, "experiment_variant": name,
                "installed_sources": {p.relative_to(package).as_posix(): provenance.sha(p)
                                      for p in provenance.installed_sources(package)}}
    (package / "build-manifest.json").write_text(json.dumps(manifest))
    return config_path, package, arm


PROBE = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from trusted_runtime import load
mode, configs = sys.argv[2], sys.argv[3:]
if mode == "cached_production":
    sys.path.insert(0, str(Path(sys.argv[1]).parents[1] / "core/spire/workload/scripts"))
    import workload
if mode == "readonly":
    from lifecycle_evidence import runtime
    workload, c = runtime(configs[0])
    print(json.dumps({"units": workload.SERVICE_UNITS}))
else:
    from fault_fixture import load_runtime
    for config in configs:
        workload, observer, c = load_runtime(config)
        print(json.dumps({"units": workload.SERVICE_UNITS, "file": workload.__file__,
                          "status_module": workload.status.__module__,
                          "hold": str(observer.hold_path(workload.Deployment(c)))}))
'''


def probe(mode, *configs):
    return subprocess.run([sys.executable, "-I", "-B", "-c", PROBE, str(HERE), mode, *map(str, configs)],
                          capture_output=True, text=True, timeout=30)


def test_generated_arms_use_own_units_in_fresh_processes(installed_root):
    first = installed_arm(installed_root, "full_argus")
    second = installed_arm(installed_root, "no_close")
    untouched = {p: p.read_bytes() for item in (first, second) for p in item[1].rglob("*") if p.is_file()}
    production = variants.WORKLOAD / "scripts/deployment.py"
    original = production.read_bytes()
    for config, package, arm in (first, second):
        process = probe("fresh", config)
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert result["units"] == arm["units"]
        assert result["file"] == str(package / "scripts/workload.py")
        assert result["hold"] == "/run/systemd/system/" + arm["units"]["helper"] + ".service.d/90-argus-acceptance-no-restart.conf"
        readonly = probe("readonly", config)
        assert readonly.returncode == 0, readonly.stderr
        assert json.loads(readonly.stdout)["units"] == arm["units"]
    assert first[2]["units"] != second[2]["units"]
    assert production.read_bytes() == original
    assert all(p.read_bytes() == raw for p, raw in untouched.items())
    assert not list(installed_root.rglob("*.pyc"))


def test_cached_production_and_second_arm_are_rejected(installed_root):
    first = installed_arm(installed_root, "full_argus")
    second = installed_arm(installed_root, "no_close")
    cached = probe("cached_production", first[0])
    assert cached.returncode != 0 and "fresh CLI process" in cached.stderr
    switched = probe("switch", first[0], second[0])
    assert switched.returncode != 0 and "fresh CLI process" in switched.stderr
    assert json.loads(switched.stdout)["units"] == first[2]["units"]


def test_static_arm_uses_normal_installed_module_import(installed_root):
    config, _, arm = installed_arm(installed_root, "static_mtls")
    process = probe("fresh", config)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["units"] == arm["units"]
    assert result["status_module"] == "static_runtime"


def test_writable_or_symlinked_executable_is_rejected_before_import(installed_root):
    config, package, _ = installed_arm(installed_root, "full_argus")
    source = package / "scripts/deployment.py"
    source.chmod(0o666)
    process = probe("fresh", config)
    assert process.returncode != 0 and "protected root-owned regular file" in process.stderr
    source.chmod(0o600)
    source.rename(source.with_name("deployment-original.py"))
    source.symlink_to(source.with_name("deployment-original.py"))
    process = probe("fresh", config)
    assert process.returncode != 0 and "symlink traversal" in process.stderr


def test_wrong_server_script_and_wrong_arm_mapping_are_rejected(installed_root):
    config, package, _ = installed_arm(installed_root, "full_argus")
    c = json.loads(config.read_text())
    c["server_script"] = str(package / "scripts/remote_acceptance.py")
    config.write_text(json.dumps(c))
    assert "server_script" in probe("fresh", config).stderr
    c["server_script"] = str(package / "scripts/workload.py")
    c["paths"]["run_name"] = "ax-another-arm"
    config.write_text(json.dumps(c))
    assert "unit names" in probe("fresh", config).stderr


def test_fault_refuses_manifest_without_explicit_experiment_build(installed_root):
    config, package, _ = installed_arm(installed_root, "full_argus")
    path = package / "build-manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["experiment_variant"]
    path.write_text(json.dumps(manifest))
    assert "explicitly built experiment" in probe("fresh", config).stderr


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0, reason="root file-owner and parent protections")
def test_root_file_in_writable_parent_and_symlink_are_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="parent"):
        trusted_runtime.protected_file(path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        trusted_runtime.protected_file(link)
    path.chmod(0o666)
    with pytest.raises(ValueError, match="regular file"):
        trusted_runtime.protected_file(path)
