import tempfile,subprocess,zipfile, pathlib
root=pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
 p=pathlib.Path(tmp); (p/'src').mkdir()
 (p/'src/Test.smali').write_text('.class public Lcom/oplus/wallpapers/rely/utils/m$z;\n.super Ljava/lang/Object;\n.method public static check()Z\n.locals 3\nconst/4 v0, 0x0\nconst-string v1, "oplus_keyguard_workshop_aod_support"\nconst/4 v2, 0x0\ninvoke-static {v0, v1, v2}, Landroid/provider/Settings$Secure;->getInt(Landroid/content/ContentResolver;Ljava/lang/String;I)I\nmove-result v0\nreturn v0\n.end method\n')
 def run(args):
  r=subprocess.run(args,cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  if r.returncode:raise RuntimeError(r.stdout)
 run(['java','-jar','bin/apktool/smali-3.0.5.jar','a',str(p/'src'),'-o',str(p/'classes.dex')])
 with zipfile.ZipFile(p/'Wallpapers.apk','w') as z:
  z.write(p/'classes.dex','classes.dex');z.writestr('resources.arsc',b'opaque OEM resources');z.writestr('AndroidManifest.xml',b'opaque binary manifest')
 run(['java','-jar','bin/apktool/apktool.jar','d','-f','-r',str(p/'Wallpapers.apk'),'-o',str(p/'decoded')])
 run(['python3','bin/patch_inspiration_aod_gate.py',str(p/'decoded/smali/com/oplus/wallpapers/rely/utils/m$z.smali')])
 run(['java','-jar','bin/apktool/apktool.jar','b','-f','--copy-original',str(p/'decoded'),'-o',str(p/'patched.apk')])
 with zipfile.ZipFile(p/'Wallpapers.apk') as a,zipfile.ZipFile(p/'patched.apk') as b:
  for name in ('resources.arsc','AndroidManifest.xml'): assert a.read(name)==b.read(name),name
  assert a.read('classes.dex')!=b.read('classes.dex')
 print('PASS: real apktool/smali Wallpapers patch preserves resources and binary manifest byte-for-byte, changes dex')
