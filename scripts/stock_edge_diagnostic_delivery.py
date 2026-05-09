#!/usr/bin/env python3
"""Build a no-send Telegram delivery payload for Stock Edge diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ifa.families.stock.diagnostic.delivery import load_diagnostic_manifest, write_delivery_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Diagnostic manifest JSON from `ifa stock diagnose --output ...`.")
    parser.add_argument("--output-dir", type=Path, help="Directory for the dry-run delivery payload; defaults to manifest directory.")
    parser.add_argument("--recipient-placeholder", default="telegram:<chat_id>", help="Placeholder recipient for later direct-send integration.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Keep payload marked dry-run; this script never sends externally.")
    parser.add_argument("--json", action="store_true", help="Print the payload JSON after writing it.")
    args = parser.parse_args()

    path = write_delivery_payload(
        args.manifest,
        output_dir=args.output_dir,
        recipient_placeholder=args.recipient_placeholder,
        dry_run=True,
    )
    if args.json:
        print(path.read_text(encoding="utf-8").rstrip())
    else:
        manifest = load_diagnostic_manifest(args.manifest)
        print(f"delivery_manifest={path}")
        print(f"title=Stock Edge 单股诊断 · {manifest.get('name') or manifest.get('ts_code')} ({manifest.get('ts_code')})")
        print("dry_run=true external_send_performed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
