"""Compile, patch, sign and inspect a real SystemUI resource fixture."""
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
VALUE = '@android:color/system_on_secondary_fixed'


def run(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(result.stdout[-7000:])
    return result.stdout


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    images = root / 'images'
    framework = images / 'system/system/framework/framework-res.apk'
    framework.parent.mkdir(parents=True)
    res = root / 'framework-res/values'
    res.mkdir(parents=True)
    (res / 'values.xml').write_text('<resources><public type="color" name="system_on_secondary_fixed"/><public type="attr" name="bad_night_color"/><color name="system_on_secondary_fixed">#ff000000</color><attr name="bad_night_color" format="color"/></resources>')
    manifest = root / 'AndroidManifest.xml'
    manifest.write_text('<manifest package="android"><application/></manifest>')
    run('otatools/bin/aapt2', 'compile', '--dir', str(res.parent), '-o', str(root / 'framework.zip'))
    run('otatools/bin/aapt2', 'link', '-x', '--manifest', str(manifest), '-o', str(framework), str(root / 'framework.zip'))
    for qualifier, value in (('values', VALUE), ('values-night', '?android:attr/bad_night_color')):
        directory = root / 'res' / qualifier
        directory.mkdir(parents=True)
        (directory / 'colors.xml').write_text(f'<resources><color name="materialColorOnSecondaryFixed">{value}</color><color name="keep">#ff123456</color></resources>')
    run('otatools/bin/aapt2', 'compile', '--dir', str(root / 'res'), '-o', str(root / 'resources.zip'))
    manifest.write_text('<manifest package="com.fixture.systemui"><application/></manifest>')
    apk = images / 'system_ext/priv-app/SystemUI/SystemUI.apk'
    apk.parent.mkdir(parents=True)
    run('otatools/bin/aapt2', 'link', '-I', str(framework), '--manifest', str(manifest), '-o', str(apk), str(root / 'resources.zip'))
    run('bash', '-c', 'source functions.sh; port_patch_systemui_night_color "$1"', 'test', str(images))
    run('otatools/bin/apksigner', 'verify', '--verbose', str(apk))
    decoded = root / 'verify'
    run('java', '-jar', 'bin/apktool/APKEditor.jar', 'd', '-f', '-t', 'xml', '-framework', str(framework), '-i', str(apk), '-o', str(decoded))
    files = list(decoded.rglob('colors.xml'))
    for qualifier in ('values', 'values-night'):
        file = next(p for p in files if p.parent.name == qualifier or (qualifier == 'values-night' and p.parent.name.startswith('values-night-v')))
        tree = ET.parse(file)
        assert tree.find("color[@name='materialColorOnSecondaryFixed']").text == VALUE
        other = tree.find("color[@name='keep']")
        if qualifier == 'values':
            assert other is not None and other.text.lower() == '#ff123456'
        elif other is not None:
            assert other.text.lower() == '#ff123456'
    first = apk.read_bytes()
    run('bash', '-c', 'source functions.sh; port_patch_systemui_night_color "$1"', 'test', str(images))
    assert apk.read_bytes() == first
    print('PASS: real SystemUI night fix, preserved day/other colors, verified signature/alignment, repeatability')
