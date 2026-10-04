"""Render a filled result file without modifying the distributed template."""
import argparse
from pathlib import Path
import subprocess
import sys

def main():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=base / "results.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--table-python", default=sys.executable)
    args = parser.parse_args()
    data = args.data.resolve()
    out = args.out.resolve()
    if out == base or base in out.parents:
        parser.error("--out must be outside the distributed figures directory")
    subprocess.run([sys.executable, str(base / "validate_results.py"), str(data)], check=True)
    build = out / "_build"
    subprocess.run([sys.executable, str(base / "plot_figures.py"), "--data", str(data), "--out", str(build)], check=True)
    subprocess.run([args.table_python, str(base / "build_tables.py"), "--data", str(data), "--plots", str(build), "--out", str(out)], check=True)

if __name__ == "__main__":
    main()
