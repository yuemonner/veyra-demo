"""Local preview only: create test workspaces and people for a private instance, with generated passwords, and
SYNTHETIC sample recordings to upload. Writes VEYRA_DATA_DIR/test-accounts.txt (readable by you only).
Test values: never use them for anything else."""
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.accounts import AccountError, Accounts, data_dir  # noqa: E402

PEOPLE = [("veyra-test", "Veyra Test", "engineer@veyra.test", "Test Engineer"),
          ("veyra-test", "Veyra Test", "reviewer@veyra.test", "Test Reviewer"),
          ("second-org", "Second Org", "outsider@second.test", "Other Org")]

acc = Accounts()
out = data_dir() / "test-accounts.txt"
known = {l.split("\t")[0]: l for l in (out.read_text().splitlines()[1:] if out.exists() else []) if "\t" in l}
lines = []
for wid, wname, email, name in PEOPLE:
    try:
        acc.add_workspace(wname)
    except AccountError:
        pass
    try:
        user = acc.add_user(workspace_id=wid, email=email, name=name, password=secrets.token_urlsafe(12))
        lines.append(f"{email}\t{user['password']}\t{wname}")
    except AccountError:  # already there: keep its password (and its sessions) unless it was never written down
        lines.append(known.get(email) or f"{email}\t{acc.set_password(email)}\t{wname}  (password reset)")
acc.close()
out.write_text("Local preview test accounts (test values only)\n" + "\n".join(lines) + "\n")
os.chmod(out, 0o600)
samples = data_dir() / "samples"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_synthetic_recordings as gen  # noqa: E402
made = gen.write_all(samples)
print(json.dumps({"accounts_file": str(out), "samples": {k: str(v) for k, v in made.items()}}, indent=1))
