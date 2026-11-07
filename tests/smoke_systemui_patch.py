import tempfile
import subprocess
import zipfile
import pathlib

root = pathlib.Path(__file__).resolve().parents[1]


def run(args):
    r = subprocess.run(args, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode:
        raise RuntimeError(r.stdout)
    return r.stdout


with tempfile.TemporaryDirectory() as tmp:
    p = pathlib.Path(tmp)
    src_dir = p / 'src/com/android/systemui/keyguard/ui/viewmodel'
    src_dir.mkdir(parents=True)
    smali_code = """\
.class public final Lcom/android/systemui/keyguard/ui/viewmodel/DefaultSettingsPopupMenuSection;
.super Ljava/lang/Object;

.method public final addViews(Landroid/view/ViewGroup;)V
    .registers 5
    .param p1, "parent"    # Landroid/view/ViewGroup;

    const-string v0, "inflating faulty view"
    invoke-virtual {p1, v0}, Ljava/lang/Object;->equals(Ljava/lang/Object;)Z
    return-void
.end method
"""
    (src_dir / 'DefaultSettingsPopupMenuSection.smali').write_text(smali_code)

    run(['java', '-jar', 'bin/apktool/smali-3.0.5.jar', 'a', str(p / 'src'), '-o', str(p / 'classes.dex')])
    apk_path = p / 'SystemUI.apk'
    with zipfile.ZipFile(apk_path, 'w') as z:
        z.write(p / 'classes.dex', 'classes.dex')
        z.writestr('resources.arsc', b'mock oplus coui resource table')
        z.writestr('AndroidManifest.xml', b'binary manifest')
        z.writestr('META-INF/CERT.RSA', b'mock cert rsa')
        z.writestr('META-INF/CERT.SF', b'mock cert sf')
        z.writestr('META-INF/MANIFEST.MF', b'mock manifest mf')

    decoded = p / 'decoded'
    rebuilt_apk = p / 'patched_SystemUI.apk'

    # Test via functions.sh helpers (apk_decode and apk_build) exactly as port.sh runs them
    script = f"""
source functions.sh
apk_decode "{apk_path}" "{decoded}" --backend apktool --artifact SystemUI.apk -r || exit 1
targetPopupMenuSectionSmali=$(find "{decoded}" -type f -name "DefaultSettingsPopupMenuSection.smali" -print -quit)
if [[ -n "${{targetPopupMenuSectionSmali}}" ]]; then
    python3 bin/patchmethod_v2.py "${{targetPopupMenuSectionSmali}}" addViews -return void || exit 1
fi
apk_build "{decoded}" "{rebuilt_apk}" --backend apktool --artifact SystemUI.apk --copy-original || exit 1
"""
    run(['bash', '-c', script])

    # Verify smali content after patch
    target_smali = list(decoded.glob('**/DefaultSettingsPopupMenuSection.smali'))
    assert len(target_smali) == 1, f"Expected 1 target smali, found {target_smali}"
    patched_content = target_smali[0].read_text()
    assert 'return-void' in patched_content
    assert 'inflating faulty view' not in patched_content

    # Verify signature and resources preserved byte-for-byte
    with zipfile.ZipFile(apk_path) as orig_z, zipfile.ZipFile(rebuilt_apk) as patched_z:
        for name in ('resources.arsc', 'AndroidManifest.xml', 'META-INF/CERT.RSA', 'META-INF/CERT.SF', 'META-INF/MANIFEST.MF'):
            assert orig_z.read(name) == patched_z.read(name), f"Mismatch in {name}"
        assert orig_z.read('classes.dex') != patched_z.read('classes.dex')

    # Verify disassembled patched dex has nop'd addViews
    verify_dir = p / 'verify'
    run(['java', '-jar', 'bin/apktool/baksmali-3.0.5.jar', 'd', str(rebuilt_apk), '-o', str(verify_dir)])
    verified_smali = list(verify_dir.glob('**/DefaultSettingsPopupMenuSection.smali'))
    assert len(verified_smali) == 1
    assert 'inflating faulty view' not in verified_smali[0].read_text()

    print('PASS: SystemUI.apk patch preserves resources and signatures, nops addViews in DefaultSettingsPopupMenuSection')
