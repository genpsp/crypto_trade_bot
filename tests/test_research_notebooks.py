from __future__ import annotations

import json
from pathlib import Path
import unittest


class ResearchNotebookLayoutTest(unittest.TestCase):
    def test_new_notebooks_are_valid_nbformat_json(self) -> None:
        for path in [
            Path("research/notebooks/run_overview.ipynb"),
            Path("research/notebooks/run_diff.ipynb"),
            Path("research/notebooks/trial_drilldown.ipynb"),
        ]:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(4, payload["nbformat"])
            self.assertGreaterEqual(len(payload["cells"]), 2)

    def test_notebook_code_cells_compile(self) -> None:
        for path in [
            Path("research/notebooks/run_overview.ipynb"),
            Path("research/notebooks/run_diff.ipynb"),
            Path("research/notebooks/trial_drilldown.ipynb"),
        ]:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for index, cell in enumerate(payload["cells"]):
                if cell.get("cell_type") != "code":
                    continue
                source = "".join(cell.get("source", []))
                compile(source, f"{path}:cell{index}", "exec")


if __name__ == "__main__":
    unittest.main()
