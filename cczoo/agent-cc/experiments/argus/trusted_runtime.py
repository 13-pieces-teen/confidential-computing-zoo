"""Load one verified installed runtime per fresh CLI process."""
import importlib
import json
from pathlib import Path
import stat
import sys

from common import require


def protected_file(value):
    path = Path(value)
    require(path.is_absolute() and path.resolve() == path,
            "installed runtime requires absolute paths without symlink traversal")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "installed runtime input must be a protected root-owned regular file")
    for parent in path.parents:
        info = parent.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                "installed runtime parent must be root-owned without group/world write")
    return path


def load(config_file, *, require_experiment=False):
    c = json.loads(protected_file(config_file).read_text())
    root = Path(c["paths"]["install_dir"])
    script = protected_file(c["server_script"])
    require(script == root / "scripts/workload.py", "server_script is not the configured installed workload runtime")
    manifest = json.loads(protected_file(root / "build-manifest.json").read_text())
    require(manifest.get("schema_version") == 1 and isinstance(manifest.get("installed_sources"), dict),
            "installed runtime build manifest is missing its source checksums")
    variant = manifest.get("experiment_variant")
    require(not require_experiment or variant is not None,
            "fault injection requires an explicitly built experiment runtime")
    required = {"workload", "deployment", "launch_state", "remote_acceptance"}
    if variant == "static_mtls":
        required.add("static_runtime")
    require({"scripts/" + name + ".py" for name in required} <= manifest["installed_sources"].keys(),
            "installed runtime dependency manifest is incomplete")
    # Full source inventory/hash checks belong to installation and preflight.
    # Sampling trusts that root-installed package; its direct executable files
    # must still be protected when this process loads them.
    for name in required:
        protected_file(script.parent / (name + ".py"))
    require(not any(name in sys.modules for name in required),
            "runtime modules are already loaded; start a fresh CLI process for this experiment arm")
    # These commands run once per process. Use Python's ordinary sibling imports
    # after checking the exact installed package; never switch arms in-process.
    sys.path.insert(0, str(script.parent))
    importlib.invalidate_caches()
    deployment = importlib.import_module("deployment")
    d = deployment.Deployment(c)
    if variant is not None:
        require(variant in {"full_argus", "native_spire_guarded", "no_watchdog", "no_close", "static_mtls"},
                "unknown installed experiment variant")
        expected = {role: c["paths"]["run_name"] + "-" + role
                    for role in ("helper", "nginx", "authz", "agent", "provider")}
        require(deployment.SERVICE_UNITS == expected,
                "installed runtime unit names do not match the isolated experiment arm")
        require(all(d.unit(role) == name + ".service" for role, name in expected.items()),
                "installed deployment resolves units outside the experiment arm")
    workload = importlib.import_module("workload")
    observer = importlib.import_module("remote_acceptance")
    require(all(Path(sys.modules[name].__file__) == script.parent / (name + ".py") for name in required),
            "runtime import resolved outside the verified installed package")
    return workload, observer, c
