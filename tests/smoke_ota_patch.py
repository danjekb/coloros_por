import tempfile,subprocess,zipfile, pathlib
root=pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
 p=pathlib.Path(tmp); (p/'src').mkdir()
 (p/'src/Test.smali').write_text('''.class public LTest;
.super Ljava/lang/Object;
.method public static check()Z
.locals 1
const-string v0, "ro.boot.vbmeta.device_state"
const-string v0, "locked"
const/4 v0, 0x1
return v0
.end method
''')
 def run(args):
  r=subprocess.run(args,cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  if r.returncode:raise RuntimeError(r.stdout)
 run(['java','-jar','bin/apktool/smali-3.0.5.jar','a',str(p/'src'),'-o',str(p/'classes.dex')])
 with zipfile.ZipFile(p/'OTA.apk','w') as z:
  z.write(p/'classes.dex','classes.dex');z.writestr('resources.arsc',b'opaque OEM resources');z.writestr('AndroidManifest.xml',b'opaque binary manifest')
 run(['java','-jar','bin/apktool/apktool.jar','d','-f','-r',str(p/'OTA.apk'),'-o',str(p/'decoded')])
 run(['python3','bin/patchmethod_v2.py','-d',str(p/'decoded'),'-k','ro.boot.vbmeta.device_state','locked','-return','false'])
 run(['java','-jar','bin/apktool/apktool.jar','b','-f','--copy-original',str(p/'decoded'),'-o',str(p/'patched.apk')])
 with zipfile.ZipFile(p/'OTA.apk') as a,zipfile.ZipFile(p/'patched.apk') as b:
  for name in ('resources.arsc','AndroidManifest.xml'): assert a.read(name)==b.read(name),name
  assert a.read('classes.dex')!=b.read('classes.dex')
 print('PASS: real apktool/smali OTA patch preserves resources and binary manifest byte-for-byte, changes dex')
