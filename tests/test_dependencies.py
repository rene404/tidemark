import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pins():
    result = {}
    for line in (ROOT / "constraints.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, version = line.split("==")
            result[name.lower()] = version
    return result


class ConstraintTests(unittest.TestCase):
    def test_pyobjc_packages_share_one_version(self):
        versions = {v for name, v in pins().items() if name.startswith("pyobjc")}
        self.assertEqual(len(versions), 1, pins())

    def test_every_pyobjc_dependency_is_pinned_and_in_range(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        ranges = dict(re.findall(r'"(pyobjc-[\w-]+)>=([\d.]+),<\d+;', pyproject))
        self.assertTrue(ranges)
        for name, minimum in ranges.items():
            pinned = pins()[name.lower()]
            self.assertGreaterEqual(tuple(map(int, pinned.split("."))), tuple(map(int, minimum.split("."))))
        upper = set(re.findall(r'"pyobjc-[\w-]+>=[\d.]+,<(\d+);', pyproject))
        self.assertEqual(len(upper), 1)
        for version in pins().values():
            self.assertLess(int(version.split(".")[0]), int(next(iter(upper))))

    def test_installers_use_constraints(self):
        for path in ("scripts/install-macos.sh", "scripts/managed_setup.py", ".github/workflows/ci.yml"):
            self.assertIn("constraints.txt", (ROOT / path).read_text(), path)


if __name__ == "__main__":
    unittest.main()
