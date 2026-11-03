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
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
            time.sleep(5)
            continue
        break

    # A successful PUT may return only file info or {"id": "..."}, without
    # a success flag. Validate the ID and verify size instead of requiring
    # an optional field. Never repeat PUT because result verification failed.
    if not isinstance(result, dict):
        raise ValueError('Pixeldrain returned a non-object response')
    identifier = result.get('id', '')
    if ('success' in result and result['success'] is not True) or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
        raise ValueError('Pixeldrain returned an invalid upload result')
    url = f'https://pixeldrain.com/u/{identifier}'
    print(f'Pixeldrain accepted upload: {url}', flush=True)
    reported_size = result.get('size')
    if reported_size is None:
        try:
            with session.get(f'https://pixeldrain.com/api/file/{identifier}/info',
                             timeout=(20, 30), allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError(f'File info HTTP {response.status_code}')
                info = response.json()
                if not isinstance(info, dict) or info.get('success') is False:
                    raise ValueError('Invalid file info response')
                if 'id' in info and info['id'] != identifier:
                    raise ValueError('File info ID does not match upload')
                reported_size = info.get('size')
        except (requests.RequestException, ValueError) as error:
            raise RuntimeError(f'File already uploaded to {url}; could not verify file info ({type(error).__name__}). Do not re-upload blindly.') from error
    if type(reported_size) is not int or reported_size != size:
        raise ValueError(f'File already uploaded to {url}, but Pixeldrain reported an unexpected size')
    return url


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
