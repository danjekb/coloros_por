Bundled apktool: **3.0.3** (official stable release).

Source: https://github.com/iBotPeaches/Apktool/releases/tag/v3.0.3

Asset: `apktool_3.0.3.jar`, installed here as `apktool.jar`.

SHA-256 (verified against the GitHub release asset digest):
`dbf930b076c6b9be08d57c449cacefc3bdd6b71ebd59b3066fc0e1f5b14f9423`

The Actions runner uses Java 21. The dex-only Wallpapers patch decodes with
`-r` and builds with `--copy-original` to preserve the original binary manifest.
Do not use `--copy-original` for resource/manifest modifications such as the
Android 17 Wi-Fi RRO patch.
