"""Install compact framework resources, then decode/edit/build a Wi-Fi-like APK."""
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(result.stdout)
    return result.stdout


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    resources = root / 'res/values'
    resources.mkdir(parents=True)
    (resources / 'values.xml').write_text('<resources><bool name="config_wifiChannelUtilizationOverrideEnabled">false</bool><string name="compact_string">fixture</string></resources>')
    compiled = root / 'compiled.zip'
    run('otatools/bin/aapt2', 'compile', '--dir', str(root / 'res'), '-o', str(compiled))
    manifest = root / 'AndroidManifest.xml'
    manifest.write_text('<manifest package="android"><application/></manifest>')
    framework = root / 'framework-res.apk'
    run('otatools/bin/aapt2', 'link', '--manifest', str(manifest), '-x', '--enable-compact-entries', '-o', str(framework), str(compiled))
    frames = root / 'framework'
    frames.mkdir()
    run('java', '-jar', 'bin/apktool/apktool.jar', 'if', '-p', str(frames), str(framework))
    assert (frames / '1.apk').is_file()
    manifest.write_text('<manifest package="com.fixture.wifi"><application/></manifest>')
    apk = root / 'wifi.apk'
    run('otatools/bin/aapt2', 'link', '--manifest', str(manifest), '--enable-compact-entries', '-I', str(framework), '-o', str(apk), str(compiled))
    decoded = root / 'decoded'
    run('java', '-jar', 'bin/apktool/apktool.jar', 'd', '-f', '-p', str(frames), '-o', str(decoded), str(apk))
    values = decoded / 'res/values/bools.xml'
    tree = ET.parse(values)
    for node in tree.getroot():
        if node.get('name') == 'config_wifiChannelUtilizationOverrideEnabled':
            node.text = 'true'
    tree.write(values, encoding='utf-8', xml_declaration=True)
    rebuilt = root / 'patched.apk'
    run('java', '-jar', 'bin/apktool/apktool.jar', 'b', '-f', '-p', str(frames), '-o', str(rebuilt), str(decoded))
    verified = root / 'verified'
    run('java', '-jar', 'bin/apktool/apktool.jar', 'd', '-f', '-p', str(frames), '-o', str(verified), str(rebuilt))
    assert ET.parse(verified / 'res/values/bools.xml').find("bool[@name='config_wifiChannelUtilizationOverrideEnabled']").text == 'true'
    print('PASS: compact framework install and Wi-Fi resource decode/edit/build/verification')
