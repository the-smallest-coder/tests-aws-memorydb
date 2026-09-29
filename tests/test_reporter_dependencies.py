"""Dependencies float to their newest release -- deliberately -- except memtier.

Running against the latest boto3/pandas/plotly, the latest reporter base image
and the latest Terraform providers is part of what this test project tests: a
release that breaks something is a finding to fix, not something to hold off
with a pin. These tests keep that policy from eroding (a pin added "just to be
safe" would quietly stop the project testing the newest versions), and keep
the two reporter install paths -- the ECS task's inline `pip install` in
reporter.tf and the Dockerfile's requirements.txt -- installing the same set.

The exception is the memtier image, which does the measuring: MemoryDB results
are compared against the ElastiCache repo's, so both must use the same memtier
build, pinned by digest and bumped in both repos together.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_VERSION_SPECIFIER = re.compile(r"(===|==|>=|<=|~=|!=|<|>|@)")


def _package_name(spec: str) -> str:
    return re.split(r"[<>=!~@\[;\s]", spec.strip().strip('"\''), maxsplit=1)[0].lower()


def _reporter_tf_pip_specs() -> list[str]:
    reporter_tf = (ROOT / "reporter.tf").read_text(encoding="utf-8")
    match = re.search(r"^\s*pip install (?P<args>[^\n]+)$", reporter_tf, re.MULTILINE)
    if not match:
        raise AssertionError("the reporter task's `pip install` line was not found in reporter.tf")
    return [arg.strip("\"'") for arg in match.group("args").split() if not arg.startswith("-")]


def _requirements_specs() -> list[str]:
    text = (ROOT / "reporter" / "requirements.txt").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def _variable_default(name: str) -> str:
    variables_tf = (ROOT / "variables.tf").read_text(encoding="utf-8")
    match = re.search(rf'variable "{name}"\s*\{{.*?default\s*=\s*"([^"]+)"', variables_tf, re.DOTALL)
    if not match:
        raise AssertionError(f"default for variable {name} not found in variables.tf")
    return match.group(1)


class ReporterPackageTests(unittest.TestCase):
    def test_both_install_paths_install_the_same_packages(self):
        self.assertEqual(
            sorted(_package_name(spec) for spec in _reporter_tf_pip_specs()),
            sorted(_package_name(spec) for spec in _requirements_specs()),
        )

    def test_no_reporter_package_is_version_bound(self):
        for source, specs in (
            ("reporter.tf", _reporter_tf_pip_specs()),
            ("reporter/requirements.txt", _requirements_specs()),
        ):
            for spec in specs:
                self.assertIsNone(
                    _VERSION_SPECIFIER.search(spec),
                    f"{source} binds {spec!r} to a version; install the latest instead",
                )

    def test_pip_freeze_provenance_tracks_every_installed_package(self):
        """With nothing pinned, report_status.json's parsed pip freeze is the
        only record of which versions built a report -- it must cover every
        package the task installs."""
        exporter = (ROOT / "reporter" / "exporter.py").read_text(encoding="utf-8")
        match = re.search(r"_TRACKED_PIP_PACKAGES\s*=\s*\((?P<body>[^)]*)\)", exporter)
        self.assertIsNotNone(match, "_TRACKED_PIP_PACKAGES not found in reporter/exporter.py")
        tracked = sorted(re.findall(r'"([^"]+)"', match.group("body")))

        self.assertEqual(tracked, sorted(_package_name(spec) for spec in _reporter_tf_pip_specs()))


ELASTICACHE_REPO = ROOT.parent / "tests-aws-elasticache-redis"


class ImageTests(unittest.TestCase):
    def test_reporter_image_is_a_floating_tag(self):
        self.assertNotIn("@sha256:", _variable_default("reporter_image"), "reporter_image is pinned to a digest")

    def test_loadgen_image_is_pinned_to_a_digest(self):
        """memtier does the measuring. A floating tag could hand the MemoryDB
        and ElastiCache runs different memtier builds, and the comparison
        would partly measure memtier instead of the two services."""
        self.assertRegex(_variable_default("loadgen_image"), r"@sha256:[0-9a-f]{64}$")

    def test_loadgen_image_matches_the_elasticache_repo(self):
        elasticache_variables = ELASTICACHE_REPO / "variables.tf"
        if not elasticache_variables.is_file():
            self.skipTest(f"sibling repo not checked out at {ELASTICACHE_REPO}")
        match = re.search(
            r'variable "loadgen_image"\s*\{.*?default\s*=\s*"([^"]+)"',
            elasticache_variables.read_text(encoding="utf-8"),
            re.DOTALL,
        )
        self.assertIsNotNone(match, "loadgen_image default not found in the ElastiCache repo")

        self.assertEqual(
            _variable_default("loadgen_image"),
            match.group(1),
            "memtier image differs from the ElastiCache repo; bump both together",
        )

    def test_dockerfile_uses_the_same_base_image_as_the_ecs_task(self):
        """reporter.tf's task runs var.reporter_image; the Dockerfile is the
        optional pre-built-image path. Both should start from the same base."""
        dockerfile = (ROOT / "reporter" / "Dockerfile").read_text(encoding="utf-8")
        match = re.search(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE)
        self.assertIsNotNone(match, "FROM line not found in reporter/Dockerfile")

        self.assertEqual(match.group(1), _variable_default("reporter_image"))


class TerraformProviderTests(unittest.TestCase):
    def test_provider_constraints_have_no_upper_bound(self):
        """`~> 5.0` means < 6.0, which would keep `terraform init` off the
        newest major release. Floors are fine; ceilings are not."""
        versions_tf = (ROOT / "versions.tf").read_text(encoding="utf-8")
        providers = re.search(r"required_providers\s*\{(?P<body>.*?)\n  \}", versions_tf, re.DOTALL)
        self.assertIsNotNone(providers, "required_providers block not found in versions.tf")

        constraints = re.findall(r'version\s*=\s*"([^"]+)"', providers.group("body"))
        self.assertTrue(constraints, "no provider version constraints found")
        for constraint in constraints:
            for part in constraint.split(","):
                self.assertTrue(
                    part.strip().startswith(">"),
                    f"provider constraint {constraint!r} caps the version; use a floor (>=) only",
                )


if __name__ == "__main__":
    unittest.main()
