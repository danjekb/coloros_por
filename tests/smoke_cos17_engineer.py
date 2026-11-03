"""Validate the engineer patch through real APKEditor and smali tools."""
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(result.stdout)


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    source = root / 'src'
    source.mkdir()
    (source / 'OplusEngineerFwService.smali').write_text('''.class public Lcom/android/server/engineer/OplusEngineerFwService;
.super Ljava/lang/Object;
.method public onBootPhase(I)V
    .locals 1
    const-string v0, "side-effect"
    return-void
.end method
''')
    run(['java', '-jar', 'bin/apktool/smali-3.0.5.jar', 'a', str(source), '-o', str(root / 'classes.dex')])
    original = root / 'oplus-services.jar'
    with zipfile.ZipFile(original, 'w') as jar:
        jar.write(root / 'classes.dex', 'classes.dex')
        jar.writestr('assets/sentinel', b'preserve original assets')
    decoded = root / 'decoded'
    run(['java', '-jar', 'bin/apktool/APKEditor.jar', 'd', '-f', '-i', str(original), '-o', str(decoded)])
    run(['python3', 'bin/patch_cos17_engineer.py', str(decoded)])
    patched = root / 'patched.jar'
    run(['java', '-jar', 'bin/apktool/APKEditor.jar', 'b', '-f', '-i', str(decoded), '-o', str(patched)])
    with zipfile.ZipFile(patched) as jar:
        assert jar.read('assets/sentinel') == b'preserve original assets'
        (root / 'patched.dex').write_bytes(jar.read('classes.dex'))
    run(['java', '-jar', 'bin/apktool/baksmali-3.0.5.jar', 'd', str(root / 'patched.dex'), '-o', str(root / 'verify')])
    result = next((root / 'verify').rglob('OplusEngineerFwService.smali')).read_text()
    assert 'side-effect' not in result and 'return-void' in result
    print('PASS: COS17 engineer patch survives real JAR decode/build and DEX verification')
