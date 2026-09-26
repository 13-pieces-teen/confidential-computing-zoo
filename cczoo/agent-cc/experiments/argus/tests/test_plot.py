import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import atomic
from plot import render


class PlotTests(unittest.TestCase):
    def test_no_measurements_never_draws_fake_performance(self):
        with tempfile.TemporaryDirectory() as directory:
            atomic(Path(directory) / "analysis.json", {"summaries": []})
            self.assertEqual(render(directory)["result"], "NOT_RUN")

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib required for scientific figures")
    def test_independent_run_figure_has_exportable_svg_and_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            atomic(Path(directory) / "analysis.json", {"summaries": [{"case": "synthetic-test", "group": "full_argus", "scale": 1,
                   "api_goodput_rps": {"mean": 10, "low": 9, "high": 11, "n_runs": 5}}]})
            result = render(directory)
            self.assertEqual(result["result"], "GENERATED")
            self.assertEqual({Path(f["path"]).suffix for f in result["files"]}, {".svg", ".pdf"})

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib required for scientific figures")
    def test_connection_and_workload_strata_have_separate_figures(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"case": "load", "group": "full_argus", "scale": 1, "connection_mode": mode,
                     "workload_kind": "memory_query", "workload_spec": "unspecified",
                     "memory_nonempty_goodput_rps": {"mean": 2, "low": None, "high": None, "n_runs": 1}}
                    for mode in ("new", "reuse")]
            atomic(Path(directory) / "analysis.json", {"summaries": rows})
            result = render(directory)
            self.assertEqual(len(result["files"]), 4)
            self.assertTrue(any("memory_query-new" in r["path"] for r in result["files"]))
            self.assertTrue(any("memory_query-reuse" in r["path"] for r in result["files"]))

    def test_nonfinite_or_zero_sample_quantities_are_not_plotted(self):
        with tempfile.TemporaryDirectory() as directory:
            data = {"summaries": [{"case": "bad", "group": "full_argus", "scale": 1,
                   "api_goodput_rps": {"mean": float("nan"), "low": None, "high": None, "n_runs": 5},
                   "locomo_coverage": {"mean": 1, "low": None, "high": None, "n_runs": 0}}]}
            with patch("plot.read", return_value=data):
                self.assertEqual(render(directory)["result"], "NOT_RUN")


if __name__ == "__main__": unittest.main()
