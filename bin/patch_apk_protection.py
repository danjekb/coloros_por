#!/usr/bin/env python3
"""APK Protection Patch for services.jar.

Based on Apk-Protection-Patch (Mods-Center):
- In com/android/server/pm/pkg/parsing/ParsingPackageUtils.smali:
  remakes getSigningDetails(...) to bypass signature verification restrictions and accept signatures.
- In com/android/server/pm/ScanPackageUtils.smali:
  remakes assertMinSignatureSchemeIsValid to return void.
- In com/android/server/pm/PackageManagerServiceUtils.smali:
  remakes matchSignaturesCompat and matchSignaturesRecover to return false.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys

GET_SIGNING_DETAILS_REMAKE = '''    .locals 4
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Landroid/content/pm/parsing/result/ParseInput;",
            "Ljava/lang/String;",
            "ZZ",
            "Landroid/content/pm/SigningDetails;",
            "I)",
            "Landroid/content/pm/parsing/result/ParseResult<",
            "Landroid/content/pm/SigningDetails;",
            ">;"
        }
    .end annotation
    invoke-static {p5}, Landroid/util/apk/ApkSignatureVerifier;->getMinimumSignatureSchemeVersionForTargetSdk(I)I
    move-result v0
    if-eqz p3, :cond_1
    const/4 v0, 0x2
    :cond_1
    if-eqz p2, :cond_2
    const/4 v0, 0x1
    invoke-static {p0, p1, v0}, Landroid/util/apk/ApkSignatureVerifier;->unsafeGetCertsWithoutVerification(Landroid/content/pm/parsing/result/ParseInput;Ljava/lang/String;I)Landroid/content/pm/parsing/result/ParseResult;
    move-result-object v1
    goto :goto_1
    :cond_2
    invoke-static {p0, p1, v0}, Landroid/util/apk/ApkSignatureVerifier;->verify(Landroid/content/pm/parsing/result/ParseInput;Ljava/lang/String;I)Landroid/content/pm/parsing/result/ParseResult;
    move-result-object v1
    :goto_1
    invoke-interface {v1}, Landroid/content/pm/parsing/result/ParseResult;->isError()Z
    move-result v2
    if-eqz v2, :cond_3
    invoke-interface {p0, v1}, Landroid/content/pm/parsing/result/ParseInput;->error(Landroid/content/pm/parsing/result/ParseResult;)Landroid/content/pm/parsing/result/ParseResult;
    move-result-object v2
    return-object v2
    :cond_3
    sget-object v2, Landroid/content/pm/SigningDetails;->UNKNOWN:Landroid/content/pm/SigningDetails;
    if-ne p4, v2, :cond_4
    return-object v1
    :cond_4
    invoke-virtual {p4}, Landroid/content/pm/SigningDetails;->getSignatures()[Landroid/content/pm/Signature;
    move-result-object v2
    invoke-interface {v1}, Landroid/content/pm/parsing/result/ParseResult;->getResult()Ljava/lang/Object;
    move-result-object v3
    check-cast v3, Landroid/content/pm/SigningDetails;
    invoke-virtual {v3}, Landroid/content/pm/SigningDetails;->getSignatures()[Landroid/content/pm/Signature;
    move-result-object v3
    invoke-static {v2, v3}, Landroid/content/pm/Signature;->areExactMatch([Landroid/content/pm/Signature;[Landroid/content/pm/Signature;)Z
    move-result v2
    invoke-interface {p0, p4}, Landroid/content/pm/parsing/result/ParseInput;->success(Ljava/lang/Object;)Landroid/content/pm/parsing/result/ParseResult;
    move-result-object v2
    return-object v2'''

RETURN_VOID = '''    .locals 0
    return-void'''

RETURN_FALSE = '''    .locals 1
    const/4 v0, 0x0
    return v0'''


def replace_method(content: str, method_name: str, new_body: str) -> tuple[str, bool]:
    pattern = re.compile(
        r"(?P<header>^\s*\.method[^\n\r]*?" + re.escape(method_name) + r"[^\n\r]*)"
        r"(?P<body>.*?)"
        r"(?P<footer>^\s*\.end method)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return content, False

    header = match.group("header")
    footer = match.group("footer")
    formatted_body = "\n" + new_body.strip("\n") + "\n"
    modified_block = f"{header}{formatted_body}{footer}"
    new_content = content[:match.start()] + modified_block + content[match.end():]
    return new_content, True


def patch_apk_protection(decompiled_dir: Path | str) -> dict[str, list[str]]:
    root = Path(decompiled_dir)
    patched_records: dict[str, list[str]] = {}

    if not root.is_dir():
        print(f"[!] Directory not found: {root}")
        return patched_records

    # 1. ParsingPackageUtils: getSigningDetails
    for smali_file in root.rglob("ParsingPackageUtils.smali"):
        text = smali_file.read_text(encoding="utf-8", errors="ignore")
        new_text, changed = replace_method(text, "getSigningDetails", GET_SIGNING_DETAILS_REMAKE)
        if changed:
            smali_file.write_text(new_text, encoding="utf-8")
            patched_records.setdefault(str(smali_file), []).append("getSigningDetails")
            print(f"[+] Patched getSigningDetails in {smali_file}")

    # 2. ScanPackageUtils: assertMinSignatureSchemeIsValid
    for smali_file in root.rglob("ScanPackageUtils.smali"):
        text = smali_file.read_text(encoding="utf-8", errors="ignore")
        new_text, changed = replace_method(text, "assertMinSignatureSchemeIsValid", RETURN_VOID)
        if changed:
            smali_file.write_text(new_text, encoding="utf-8")
            patched_records.setdefault(str(smali_file), []).append("assertMinSignatureSchemeIsValid")
            print(f"[+] Patched assertMinSignatureSchemeIsValid in {smali_file}")

    # 3. PackageManagerServiceUtils: matchSignaturesCompat, matchSignaturesRecover
    for smali_file in root.rglob("PackageManagerServiceUtils.smali"):
        text = smali_file.read_text(encoding="utf-8", errors="ignore")
        file_changed = False
        for method_name in ("matchSignaturesCompat", "matchSignaturesRecover"):
            new_text, changed = replace_method(text, method_name, RETURN_FALSE)
            if changed:
                text = new_text
                file_changed = True
                patched_records.setdefault(str(smali_file), []).append(method_name)
                print(f"[+] Patched {method_name} in {smali_file}")
        if file_changed:
            smali_file.write_text(text, encoding="utf-8")

    return patched_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply APK Protection patch to services.jar smali tree")
    parser.add_argument("decompiled_dir", help="Path to decompiled services.jar directory")
    args = parser.parse_args()

    target = Path(args.decompiled_dir)
    if not target.is_dir():
        print(f"Error: {target} is not a directory", file=sys.stderr)
        return 1

    records = patch_apk_protection(target)
    total_patched = sum(len(methods) for methods in records.values())
    print(f"[*] APK Protection patch finished. Patched {total_patched} methods across {len(records)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
