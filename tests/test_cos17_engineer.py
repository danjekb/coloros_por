import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from patch_cos17_engineer import patch_engineer

SOURCE = '''.class public Lcom/android/server/engineer/OplusEngineerFwService;
.super Ljava/lang/Object;
.method public onBootPhase(I)V
    .locals 1
    const-string v0, "side-effect"
    return-void
.end method
.method public keep()V
    .locals 0
    return-void
.end method
'''


class EngineerTests(unittest.TestCase):
    def test_exact_method_and_repeatability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'OplusEngineerFwService.smali'
            path.write_text(SOURCE)
            with contextlib.redirect_stdout(io.StringIO()):
                patch_engineer(root)
                first = path.read_text()
                patch_engineer(root)
            self.assertEqual(path.read_text(), first)
            self.assertNotIn('side-effect', first)
            self.assertIn('onBootPhase(I)V\n    .locals 0\n    return-void\n.end method', first)
            self.assertEqual(first.split('.method public keep')[1], SOURCE.split('.method public keep')[1])

    def test_missing_or_wrong_signature_does_not_silently_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                patch_engineer(root)
            path = root / 'OplusEngineerFwService.smali'
            path.write_text(SOURCE.replace('onBootPhase(I)V', 'onBootPhase()Z'))
            with self.assertRaises(ValueError):
                patch_engineer(root)
            self.assertIn('side-effect', path.read_text())
