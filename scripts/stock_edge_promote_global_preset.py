"""Emit/apply a reviewed Stock Edge global preset YAML promotion patch."""
from __future__ import annotations

import argparse
from pathlib import Path

from ifa.families.stock.backtest.promotion import apply_promotion_patch, build_promotion_patch, emit_promotion_patch


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote a reviewed Stock Edge global_preset artifact to a YAML patch/baseline.")
    parser.add_argument("--artifact", required=True, help="Path to global_preset JSON artifact.")
    parser.add_argument("--base-yaml", required=True, help="Path to stock_edge_v2.2.yaml baseline.")
    parser.add_argument("--emit-patch", action="store_true", help="Emit reviewable yaml_patch_candidate files; does not modify YAML.")
    parser.add_argument("--apply", action="store_true", help="Apply promoted params. Requires explicit flag; default is no mutation.")
    parser.add_argument("--backup", action="store_true", help="When applying in-place, write a .bak_* backup first.")
    parser.add_argument("--variant-output", help="Write promoted YAML variant instead of modifying --base-yaml.")
    parser.add_argument("--output-dir", help="Directory for emitted patch files; defaults under ifaenv manifests.")
    args = parser.parse_args()

    artifact = Path(args.artifact).expanduser()
    base_yaml = Path(args.base_yaml).expanduser()
    if not args.emit_patch and not args.apply:
        parser.error("Choose --emit-patch and/or --apply. No YAML is modified by default.")

    if args.emit_patch:
        patch = build_promotion_patch(artifact, base_yaml)
        yaml_path, md_path = emit_promotion_patch(patch, output_dir=Path(args.output_dir).expanduser() if args.output_dir else None)
        print(f"yaml patch candidate -> {yaml_path}", flush=True)
        print(f"markdown review       -> {md_path}", flush=True)

    if args.apply:
        target, backup = apply_promotion_patch(
            artifact,
            base_yaml,
            backup=bool(args.backup),
            variant_output=Path(args.variant_output).expanduser() if args.variant_output else None,
        )
        print(f"promoted yaml -> {target}", flush=True)
        if backup:
            print(f"backup        -> {backup}", flush=True)


if __name__ == "__main__":
    main()
