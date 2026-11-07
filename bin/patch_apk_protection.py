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


class PatchError(RuntimeError):
    """Raised when the services.jar layout does not match the expected Android code."""


REQUIRED_METHODS = {
    "ParsingPackageUtils.smali": ("getSigningDetails", GET_SIGNING_DETAILS_REMAKE),
    "ScanPackageUtils.smali": ("assertMinSignatureSchemeIsValid", RETURN_VOID),
    "PackageManagerServiceUtils.smali": (
        "matchSignaturesCompat",
        "matchSignaturesRecover",
    ),
}


def replace_method(content: str, method_name: str, new_body: str) -> tuple[str, bool]:
    """Replace exactly one method whose declaration names method_name."""
    declaration = re.compile(
        r"^[ \t]*\.method\b[^\r\n]*\b" + re.escape(method_name) + r"\s*\(",
        re.MULTILINE,
    )
    declarations = list(declaration.finditer(content))
    if len(declarations) != 1:
        raise PatchError(
            f"Expected exactly one declaration for {method_name}, found {len(declarations)}"
        )

    start = declarations[0].start()
    end_match = re.search(r"^[ \t]*\.end method[ \t]*$", content[declarations[0].end():], re.MULTILINE)
    if end_match is None:
        raise PatchError(f"Method {method_name} has no .end method")
    end = declarations[0].end() + end_match.end()
    block = content[start:end]
    header_end = block.find("\n")
    if header_end < 0:
        raise PatchError(f"Malformed declaration for {method_name}")
    header = block[:header_end]
    footer = block[block.rfind(".end method"):]
    return content[:start] + header + "\n" + new_body.strip("\n") + "\n" + footer + content[end:], True


def patch_apk_protection(decompiled_dir: Path | str) -> dict[str, list[str]]:
    """Patch all required methods, failing before writes if the layout is unexpected."""
    root = Path(decompiled_dir)
    if not root.is_dir():
        raise PatchError(f"Directory not found: {root}")

    records: dict[str, list[str]] = {}
    pending_writes: dict[Path, str] = {}

    # Resolve all required files first. Multiple matches can indicate an ambiguous
    # multidex/decompilation layout, so do not silently patch an arbitrary copy.
    resolved: dict[str, Path] = {}
    for filename in REQUIRED_METHODS:
        matches = list(root.rglob(filename))
        if len(matches) != 1:
            raise PatchError(
                f"Expected exactly one {filename} under {root}, found {len(matches)}"
            )
        resolved[filename] = matches[0]

    # Validate and patch in memory. No file is written until every target passes.
    for filename, spec in REQUIRED_METHODS.items():
        smali_file = resolved[filename]
        text = smali_file.read_text(encoding="utf-8", errors="strict")
        method_names = (spec,) if isinstance(spec, str) else spec
        for item in method_names:
            method_name, new_body = item if isinstance(item, tuple) else (item, None)
            if new_body is None:
                raise PatchError(f"Internal patch definition missing body for {method_name}")
            text, _ = replace_method(text, method_name, new_body)
            records.setdefault(str(smali_file), []).append(method_name)
            print(f"[+] Validated and patched {method_name} in {smali_file}")
        pending_writes[smali_file] = text

    for smali_file, text in pending_writes.items():
        smali_file.write_text(text, encoding="utf-8")

    return records

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
