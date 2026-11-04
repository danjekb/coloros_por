#!/usr/bin/env python3
"""Fix only the named night-mode SystemUI color in an APKEditor decode tree."""
from pathlib import Path
import sys
import re
import xml.etree.ElementTree as ET

NAME = 'materialColorOnSecondaryFixed'
VALUE = '@android:color/system_on_secondary_fixed'


def patch_color(decoded: Path) -> bool:
    files = [p for p in decoded.rglob('colors.xml') if re.fullmatch(r'values-night(?:-v[0-9]+)?', p.parent.name) and p.parent.parent.name == 'res']
    targets = []
    for path in files:
        tree = ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
        nodes = [e for e in tree.getroot() if e.tag == 'color' and e.get('name') == NAME]
        targets.extend((path, tree, node) for node in nodes)
    if not targets:
        print('SystemUI: night color not present; leaving APK unchanged')
        return False
    changed = False
    seen = set()
    for path, tree, node in targets:
        if path in seen:
            raise ValueError(f'Duplicate SystemUI night color in {path}')
        seen.add(path)
        if (node.text or '').strip() == VALUE:
            continue
        # Preserve namespace prefixes used by other resource entries.
        for _, (prefix, uri) in ET.iterparse(path, events=('start-ns',)):
            ET.register_namespace(prefix, uri)
        node.text = VALUE
        tree.write(path, encoding='utf-8', xml_declaration=True)
        print(f'SystemUI: {path}: {NAME}={VALUE}')
        changed = True
    if not changed:
        print('SystemUI: night color already correct; leaving APK unchanged')
    return changed



if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: patch_systemui_night_color.py DECODED_APK_DIRECTORY')
    raise SystemExit(0 if patch_color(Path(sys.argv[1])) else 3)
