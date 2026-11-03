#!/usr/bin/env python3
"""Stream release files to the official Pixeldrain API."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import time
from urllib.parse import quote

import requests

API = 'https://pixeldrain.com/api/file/'


def upload_file(path, api_key, session):
    path = Path(path)
    if not api_key:
        raise ValueError('Set the GitHub Actions secret PIXELDRAIN_API_KEY')
    size = path.stat().st_size
    if not size:
        raise ValueError(f'Cannot upload empty file: {path.name}')
    for attempt in range(3):
        try:
            with path.open('rb') as body:
                with session.put(API + quote(path.name, safe=''), data=body,
                                 headers={'Content-Type': 'application/octet-stream'},
                                 auth=('', api_key), timeout=(20, 120),
                                 allow_redirects=False) as response:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(5)
                        continue
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(f'Pixeldrain HTTP {response.status_code}: check API key, account quota and file size limit')
                    result = response.json()
                    if not isinstance(result, dict):
                        raise ValueError('Pixeldrain returned a non-object response')
                    identifier = result.get('id', '')
                    if result.get('success') is not True or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
                        raise ValueError('Pixeldrain returned an invalid upload result')
                    if result.get('size') != size:
                        raise ValueError('Pixeldrain reported an unexpected uploaded size')
                    return f'https://pixeldrain.com/u/{identifier}'
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    args = parser.parse_args()
    key = os.environ.get('PIXELDRAIN_API_KEY', '')
    if not key:
        parser.error('Set the GitHub Actions secret PIXELDRAIN_API_KEY')
    with requests.Session() as session:
        for path in args.files:
            print(f'Uploading {path.name} ({path.stat().st_size} bytes)', flush=True)
            url = upload_file(path, key, session)
            with path.open('rb') as file:
                digest = hashlib.file_digest(file, 'sha256').hexdigest()
            print(f'{path.name}: {url}', flush=True)
            summary = os.environ.get('GITHUB_STEP_SUMMARY')
            if summary:
                # Escape characters used in Markdown link labels.
                name = path.name.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
                with open(summary, 'a', encoding='utf-8') as output:
                    output.write(f'- [{name}]({url}) — SHA-256: `{digest}`\n')


if __name__ == '__main__':
    main()
