"""Retrieve a received attachment through JARVIS's existing verified downloader."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from jarvis.workflow_report import signed_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cert', type=Path)
    parser.add_argument('--as', dest='session', default='')
    parser.add_argument('--message', type=UUID, required=True)
    parser.add_argument('--attachment', type=UUID, required=True)
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args(argv)
    base = f'/api/team/messages/{args.message}'
    with signed_client(args.cert, args.session, 'packages') as client:
        result = client.post(f'{base}/files/{args.attachment}/location', {})
        if result.status_code != 200:
            print(f'Retrieval refused ({result.status_code}): {result.text}')
            return 1
        location = result.json()
        output = {'location': location}
        if location.get('state') != 'verified' or not location.get('local_path'):
            print(json.dumps(output, indent=2))
            return 1
        if args.inspect:
            result = client.post(f'{base}/attachments/{args.attachment}/bundle', {})
            if result.status_code != 200:
                output['inspection_error'] = {'status': result.status_code, 'detail': result.text}
                print(json.dumps(output, indent=2))
                return 1
            output['inspection'] = result.json()
        print(json.dumps(output, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
