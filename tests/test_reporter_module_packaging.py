"""reporter.tf ships the reporter to its ECS task one module at a time.

There is no image build: Terraform uploads each file named in
local.reporter_modules to S3, and the task's inline Python downloads each file
named in its own `modules` list before running exporter.py. A module missing
from either list is simply absent in the container, and the first import of it
kills the task before anything is exported -- no metrics, no report, no email.
Unit tests cannot see that, because they import straight from the checkout.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTER_DIR = ROOT / "reporter"
REPORTER_TF = ROOT / "reporter.tf"

# Column-0 imports only: these run at module load, which is exactly when a
# missing file kills the task. Imports inside functions are left out on
# purpose -- aggregator.py, for one, is only imported by a local CLI command.
_MODULE_LEVEL_IMPORT = re.compile(r"^(?:from\s+(\w+)\s+import\b|import\s+(\w+))", re.MULTILINE)


def _uploaded_modules() -> set[str]:
    text = REPORTER_TF.read_text(encoding="utf-8")
    match = re.search(r"reporter_modules\s*=\s*toset\(\[(?P<body>.*?)\]\)", text, flags=re.DOTALL)
    if not match:
        raise AssertionError("local.reporter_modules not found in reporter.tf")
    return set(re.findall(r'"([\w.]+\.py)"', match.group("body")))


def _downloaded_modules() -> set[str]:
    text = REPORTER_TF.read_text(encoding="utf-8")
    match = re.search(r"^modules\s*=\s*\[(?P<body>.*?)^\]", text, flags=re.DOTALL | re.MULTILINE)
    if not match:
        raise AssertionError("the reporter task's download list was not found in reporter.tf")
    return set(re.findall(r'"([\w.]+\.py)"', match.group("body")))


def _sibling_imports(module: str) -> set[str]:
    text = (REPORTER_DIR / module).read_text(encoding="utf-8")
    names = {first or second for first, second in _MODULE_LEVEL_IMPORT.findall(text)}
    return {f"{name}.py" for name in names if (REPORTER_DIR / f"{name}.py").is_file()}


def _load_time_closure(entry: str) -> set[str]:
    """Every reporter module that importing *entry* pulls in at load time."""
    seen: set[str] = set()
    pending = [entry]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        pending.extend(_sibling_imports(module) - seen)
    return seen


class ReporterModulePackagingTests(unittest.TestCase):
    def test_upload_and_download_lists_name_the_same_modules(self):
        self.assertEqual(_uploaded_modules(), _downloaded_modules())

    def test_every_listed_module_exists(self):
        for module in sorted(_uploaded_modules() | _downloaded_modules()):
            self.assertTrue((REPORTER_DIR / module).is_file(), f"reporter.tf lists missing file {module}")

    def test_everything_exporter_imports_at_load_time_is_shipped(self):
        """The task runs `python exporter.py`, so its load-time import closure
        is the minimum that must reach the container."""
        needed = _load_time_closure("exporter.py")
        self.assertIn("metric_source.py", needed, "closure walk no longer sees metric_source")

        for listed_name, listed in (
            ("local.reporter_modules", _uploaded_modules()),
            ("the task's download list", _downloaded_modules()),
        ):
            self.assertEqual(
                sorted(needed - listed),
                [],
                f"imported by exporter.py at load time but missing from {listed_name}",
            )


if __name__ == "__main__":
    unittest.main()
