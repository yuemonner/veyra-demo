# Private instance (local preview)

Built 2026-10-21. **Not deployed.** Runs on your Mac only, from the branch `private-instance-local`.

## What it adds

1. **Robot recordings read directly.** Upload a ROS 1 bag (`.bag`), an MCAP file (`.mcap`), or a ROS 2 bag folder
   zipped (`.zip`, `.db3` or `.mcap` inside). No conversion script per robot.
   - Veyra lists the topics and proposes which ones are which signals, from standard ROS message types:
     JointState (target topics become joint commands), Twist (command topics become velocity commands),
     Odometry, Imu and DiagnosticArray.
   - Unknown message types are left out, never guessed.
   - The person reviews the proposal: untick topics, or edit the mapping as JSON for custom message types.
   - Readings are kept at up to 20 Hz per signal; events are kept on every change.
   - The extracted readings go through the normal import checks. The import record keeps the recording's
     SHA-256 and the mapping that was used.
   - Readers: `rosbags` (Apache-2.0); `mcap` with `mcap-ros1-support` and `mcap-ros2-support` (MIT).
     They are listed in `backend/requirements-recordings.txt`.
2. **Sign-in and one private workspace per organisation.**
   - Every workspace has its own database file, so cases never mix between organisations.
   - Passwords are stored only as scrypt hashes. Sessions use an HttpOnly cookie, and only a hash of the
     session token is stored.
   - Five wrong passwords lock the account for 15 minutes.
   - Every record carries the signed-in person's name, whatever the browser sends. This makes "a different
     person must review" a real check.
   - Only `/auth`, `/engine`, `/share` and `/health` answer. The demo story, simulated events, webhooks and
     connectors are off.
   - The AI agent is off unless `VEYRA_PRIVATE_ALLOW_AGENT=true`, because it would send case data to an
     external model.
   - Accounts are created on the command line (`python -m app.accounts …`); there is no self sign-up.
3. **Upload → automatic debrief → shareable report.**
   - One form takes:
     - the machine id and machine type;
     - what the operator noticed: their words, a symptom from a short list, and the affected part;
     - when the problem happened;
     - which part of the recording was normal (default: the first third, stated as an assumption).
   - Veyra then imports the recording, records the operator's report and the known-good period with the
     person's name, investigates, and opens the Brief.
   - The report now starts with a **Debrief**: what was reported, where to look first, what was also
     flagged, next checks, similar earlier cases, and what it was compared against.
   - **Share** creates a read-only link to that report for 1–90 days. The link works without sign-in, is
     shown once, and can be revoked.

## Run the preview

Open `backups/private-preview.command` in Finder. It does three things:
- starts the API on 127.0.0.1:8029 and the web page on 127.0.0.1:3029 (your usual 8019/3019 preview is not touched);
- creates test accounts in `.local-private/test-accounts.txt` (test values only);
- writes SYNTHETIC sample recordings to `.local-private/samples/`.

Then open http://127.0.0.1:3029/field-case.

## Tested

- 227 backend tests pass on the Mac, and the frontend type check passes.
- The tests cover:
  - all three recording formats give the same readings;
  - unsafe zip paths are refused;
  - sign-in and lockout work;
  - records carry the signed-in person's name;
  - workspaces are isolated;
  - the risky endpoints are off;
  - share links expire and can be revoked;
  - upload → debrief works end to end over HTTP.
- In the browser on the Mac, I:
  - signed in;
  - uploaded the SYNTHETIC ROS 1 bag;
  - got a debrief that names the left front knee drive (it stops following its commands at 60 s, and the
    drive's diagnostic reports an error);
  - created a share link and opened it without signing in;
  - signed in as another organisation and saw no cases, with direct links to the first organisation's case and
    report refused.

## Not done

- Real recordings: so far only SYNTHETIC ones; ETH data is the next test.
- A database server, backups, HTTPS and a hosted deployment.
- Two-factor sign-in, password reset by email, and roles (everyone in a workspace can do everything).
- Recordings larger than about 4 GB, and very long missions (the investigation keeps all extracted readings in memory).
- A visual editor for custom message types (today: JSON).
