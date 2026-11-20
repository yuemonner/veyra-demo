# Adding a new machine type (configuration only)

Veyra's investigation engine reasons over four fault-source **roles** that exist on any remotely operated
or autonomous machine. A **machine profile** says what each role is called on a given kind of machine and
who owns it. The machine's context (`machine_class`) picks the profile.

| Role key | Meaning | Excavator | Fixed-wing UAV | Agricultural robot | Legged robot |
|---|---|---|---|---|---|
| `network` | link between operator/cloud and machine | Network | Communication (telemetry & command link) | Connectivity (RTK, LTE, Wi-Fi) | Communication (operator & mission link) |
| `control_suite` | software that decides what to do | Remote-control software | Flight controller & autopilot | Autonomy & control software | Locomotion controller & autonomy |
| `vehicle_interface` | outputs that drive actuators | Vehicle interface | Actuation (servos & outputs) | Drive & implement actuation | Joint drives (actuators) |
| `vehicle_body` | the physical machine | Machine body & hydraulics | Airframe & propulsion | Vehicle, implement & hydraulics | Legs, body & terrain contact |

## To onboard a new machine type

1. **Write a profile**: one JSON file in `backend/app/machines/profiles/` (or in the folder named by
   `VEYRA_MACHINE_PROFILES_DIR`). Required: `id`, and for every role a `label` and an `owner`. Optional:
   - `signal_roles`: route a catalog signal to a different role on this machine;
   - `checks`: reword (`question`, `why`, `who`, `tools`) or switch off (`"applicable": false`) a next check;
   - `context_keys`: which machine-context attributes must be confirmed before two runs are compared.
   The profile is validated when it loads; a missing role or unknown field is refused.
2. **Map the data**: an import profile (JSON) maps the source columns and signal names to Veyra's
   catalog (`signal_map`, units, clock). See `GET /engine/profiles` for the canonical CSV/JSONL formats.
3. **Record what the machine is**: a machine-context assertion `machine_class = <profile id>`, reported
   or confirmed, with its source.
4. **Declare known-good runs**: cases a person declares known-good (`reference_declared`) become the
   machine baseline. Holds and tracking errors are judged against them, never against the case itself.

No engine code changes for any of these. `tests/test_machine_profiles.py` proves it: a profile written to a
temporary folder at test time (an orchard robot) changes domain names, owner teams and check wording.

## What still needs code

- A **new kind of evidence** (for example an energy check for engine failures, or sensor-health rules for a
  dedicated "Sensors" role) is a new analysis module, not configuration.
- Signals outside the catalog need a catalog entry (name, unit, physical range, default role).
- The four roles are the engine's fixed vocabulary; a profile names them but cannot add a fifth.

## Second real machine: ANYmal D (legged robot), October 2026

What it took to point the engine at a walking robot (GrandTour dataset, ETH RSL) is logged in
`docs/ANYMAL_ADAPTATION.md`: a profile (configuration), catalog entries for joints and body velocity, and
command tracking generalised from one attitude pair to a table of commanded/measured pairs (code).
