"""A case directory: decoded recording files + a signal profile + a case descriptor (case.json), kept together.

Real customer cases live OUTSIDE the repository (default ~/Veyra-private/<case>, or VEYRA_PRIVATE_CASES_DIR) and are
never committed or published. Synthetic fixtures use the same layout inside the repository and say `"synthetic": true`.

case.json (schema veyra-case-dir-v1):
  case_id, title, synthetic, confidential
  data_dir           folder holding the decoded CSVs (relative to the case directory)
  manifest           optional CSV with columns file, sha256: every file used is verified against it before reading
  profile            signal profile JSON (see profile.py)
  windows            window -> list of decoded CSVs (do not list convenience subsets that repeat frames)
  check_parameters   check id -> parameters (e.g. a verified protection spec, a restart policy, or null when unknown)
  historical_reports earlier analyses of this case (shown as history, never re-labelled as fresh results)
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.physchecks.profile import Profile
from app.physchecks.recording import Recording, load_decoded_csv, sha256_file

SCHEMA = "veyra-case-dir-v1"


class CaseDirError(ValueError):
    pass


@dataclass
class CaseDir:
    path: Path
    meta: Dict[str, Any]
    profile: Profile
    recording: Recording
    manifest_check: Dict[str, Any]

    @property
    def case_id(self) -> str:
        return self.meta["case_id"]

    @property
    def synthetic(self) -> bool:
        return bool(self.meta.get("synthetic"))

    def check_parameters(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for cid, p in (self.meta.get("check_parameters") or {}).items():
            p = dict(p or {})
            for k, v in list(p.items()):           # a parameter may point at a JSON file next to case.json
                if isinstance(v, str) and v.endswith(".json") and (self.path / v).exists():
                    p[k] = json.loads((self.path / v).read_text(encoding="utf-8"))
            out[cid] = p
        return out


def private_root() -> Path:
    return Path(os.environ.get("VEYRA_PRIVATE_CASES_DIR") or Path.home() / "Veyra-private")


def _manifest(path: Path) -> Dict[str, str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["file"]: r["sha256"] for r in csv.DictReader(f) if r.get("file") and r.get("sha256")}


def load(path: Path, *, verify: bool = True) -> CaseDir:
    path = Path(path).expanduser()
    meta_path = path / "case.json"
    if not meta_path.exists():
        raise CaseDirError(f"{path}: no case.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("schema") != SCHEMA:
        raise CaseDirError(f"case.json schema must be {SCHEMA!r}")
    data_dir = path / meta.get("data_dir", ".")
    profile = Profile.load(path / meta["profile"])
    if profile.synthetic != bool(meta.get("synthetic")):
        raise CaseDirError("profile and case disagree on whether the data is synthetic")
    files = {w: [data_dir / f for f in fs] for w, fs in meta["windows"].items()}
    check: Dict[str, Any] = {"verified": [], "mismatched": [], "not_in_manifest": [], "manifest": None}
    if meta.get("manifest"):
        man_path = data_dir / meta["manifest"]
        man = _manifest(man_path)
        check["manifest"] = man_path.name
        if verify:
            for fs in files.values():
                for f in fs:
                    want = man.get(f.name)
                    if want is None:
                        check["not_in_manifest"].append(f.name)
                    elif sha256_file(f) != want:
                        check["mismatched"].append(f.name)
                    else:
                        check["verified"].append(f.name)
            if check["mismatched"]:
                raise CaseDirError(f"files do not match the manifest: {check['mismatched']}")
    fields = sorted({spec["field"] for spec in profile.roles.values()})
    rec = load_decoded_csv(meta["case_id"], files, fields=fields, synthetic=bool(meta.get("synthetic")), label=meta.get("title", ""))
    rec.provenance["manifest_check"] = check
    return CaseDir(path, meta, profile, rec, check)


def discover(root: Optional[Path] = None) -> List[Path]:
    root = Path(root) if root else private_root()
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if (p / "case.json").exists())
