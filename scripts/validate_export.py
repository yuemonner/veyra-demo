"""Validate an authorized export against an import profile, locally. Nothing is stored or transmitted.

Usage:
  python3 scripts/validate_export.py EXPORT_FILE PROFILE_JSON --asset EX1 [--json] [--rows N]

Exit codes: 0 accepted / accepted_with_warnings, 1 needs_review, 2 rejected / unsupported_format,
3 invalid profile or arguments. Requires only the Python standard library (3.9+).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.ingest.pipeline import ACCEPTED, ACCEPTED_WITH_WARNINGS, NEEDS_REVIEW, ingest  # noqa: E402
from app.ingest.profile import ProfileError, parse_profile  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate an export against a Veyra import profile (local only).")
    parser.add_argument("export_file", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--asset", required=True, help="Machine ID this file must describe, e.g. EX1")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON")
    parser.add_argument("--rows", type=int, default=0, help="Include the first N normalized rows in JSON output")
    args = parser.parse_args(argv)
    try:
        profile = parse_profile(json.loads(args.profile.read_text(encoding="utf-8")))
    except (OSError, ValueError, ProfileError) as error:
        print(f"Profile problem: {error}", file=sys.stderr)
        return 3
    try:
        raw = args.export_file.read_bytes()
    except OSError as error:
        print(f"Cannot read export: {error}", file=sys.stderr)
        return 3
    result = ingest(raw, args.export_file.name, profile, args.asset)
    report = result.to_dict(include_rows=False)
    if args.json:
        if args.rows:
            report["rows"] = [{k: v for k, v in r.items() if k != "raw"} for r in result.rows[: args.rows]]
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        label = " (SYNTHETIC profile)" if profile.synthetic else ""
        print(f"{args.export_file.name}: {result.status}{label}")
        print(f"  sha256 {result.input['sha256']}  ·  {len(result.rows)} accepted, {result.rejected_row_count} rejected, {result.duplicate_row_count} duplicate")
        for error in result.file_errors:
            print(f"  error: {error}")
        for row in result.rejected_rows[:10]:
            print(f"  row {row['row']}: {'; '.join(row['reasons'])}")
        if result.rejected_row_count > 10:
            print(f"  … {result.rejected_row_count - 10} more rejected rows (use --json)")
        for warning in result.warnings:
            print(f"  warning: {warning}")
        if result.signals:
            print("  signals:")
            for s in result.signals:
                bounds = f" {s['min']:g}–{s['max']:g} {s['unit']}" if s["min"] is not None else ""
                endpoint = f" [{s['scope']}]" if s["scope"] else (" [endpoint not stated]" if s["kind"] == "measurement" else "")
                print(f"    {s['signal']}{endpoint} {s['count']} rows {s['start_utc']} → {s['end_utc']}{bounds}")
        for note in result.missing_information:
            print(f"  missing: {note}")
    if result.status in (ACCEPTED, ACCEPTED_WITH_WARNINGS):
        return 0
    return 1 if result.status == NEEDS_REVIEW else 2


if __name__ == "__main__":
    sys.exit(main())
