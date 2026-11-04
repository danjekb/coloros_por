import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from patch_systemui_night_color import NAME, VALUE, patch_color


class SystemUIColorTests(unittest.TestCase):
    def test_night_variants_only_and_repeatability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for qualifier in ('values', 'values-night', 'values-night-v31'):
                path = root / 'package_1/res' / qualifier / 'colors.xml'
                path.parent.mkdir(parents=True)
                path.write_text(f'<resources><!--keep--><color name="{NAME}">?android:attr/test</color><color name="other">#ff123456</color></resources>')
                paths.append(path)
            day = paths[0].read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(patch_color(root))
                first = [p.read_bytes() for p in paths]
                self.assertFalse(patch_color(root))
            self.assertEqual([p.read_bytes() for p in paths], first)
            self.assertEqual(paths[0].read_bytes(), day)
            for path in paths[1:]:
                tree = ET.parse(path)
                self.assertEqual(tree.find(f"color[@name='{NAME}']").text, VALUE)
                self.assertEqual(tree.find("color[@name='other']").text, '#ff123456')
                self.assertIn('<!--keep-->', path.read_text())

    def test_missing_color_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'res/values-night/colors.xml'
            path.parent.mkdir(parents=True)
            path.write_text('<resources><color name="other">#ff123456</color></resources>')
            original = path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(patch_color(root))
            self.assertEqual(path.read_bytes(), original)
