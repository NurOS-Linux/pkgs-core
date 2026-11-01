"""Regression checks for the catalog generator (standard library only)."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bd_gen", Path(__file__).with_name("bd-gen.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


class CatalogTests(unittest.TestCase):
    def test_scalars_and_arrays(self):
        values = gen.parse_declares('declare -- pkgver="1.2.3"\ndeclare -a depends=([0]="libc" [1]="bash")\n')
        self.assertEqual(values["pkgver"], "1.2.3")
        self.assertEqual(values["depends"], ["libc", "bash"])

    def test_recipe_expansion_and_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "packages/demo/template/APGBUILD"
            path.parent.mkdir(parents=True)
            path.write_text('pkgname=demo\npkgver=1.2\npkgrel=3\nepoch=2\narch=(any)\nsource=("https://example.org/${pkgname}-${pkgver}.tar.gz" "config")\npackage() { exit 99; }\n')
            entry = gen.extract(str(path), root)
            self.assertEqual(entry["version"], "2:1.2-3")
            self.assertEqual(entry["local_sources"], ["config"])
            self.assertEqual(entry["source"][0], "https://example.org/demo-1.2.tar.gz")
            self.assertEqual(entry["recipe"], "packages/demo/template/APGBUILD")
            path.write_text('exit 7\n')
            with self.assertRaises(RuntimeError):
                gen.extract(str(path), root)


if __name__ == "__main__":
    unittest.main()
