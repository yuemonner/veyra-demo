# Hosted demo seed

`alfa_carbonz_rudder_case.sqlite.gz` holds one investigated REAL-data case and the aircraft's 10 normal flights as known-good runs (fault_type `known_good_run`, the machine baseline). The case: a CMU AirLab ALFA flight (Carbon Z T-28) where
Veyra's output-health check flagged the rudder output, with its imports, investigation runs, the recorded AI agent
report and its hash-chained ledger, copied verbatim (`scripts/export_case_seed.py`). The ledger also holds Veyra's
evidence-discovery record for the flight's ORIGINAL recording (every topic, read by `app/discovery`), appended through the
store by `scripts/seed/alfa_discovery.py` because the hosted demo cannot read the bag at start-up; on a live upload the
same function runs automatically. Label topics (`/failure_status/*`) are excluded by the product's label guard.

Data: Keipour, Mousaei, Scherer, *ALFA: A Dataset for UAV Fault and Anomaly Detection*, IJRR 2021,
doi:10.1184/R1/12707963. CC0 per the dataset README (CC BY 4.0 on the repository page). Public research data;
no private data. The faults in ALFA are injected in flight by the researchers.

A hosted engine with `VEYRA_ENGINE_SEED=demo` loads this file, plus the SYNTHETIC field-case story, only when its
database holds no cases.

`anymal_d_grandtour.sqlite.gz` holds two ANYmal D cases built from one REAL GrandTour mission (ETH Zurich Robotic Systems
Lab, https://huggingface.co/datasets/leggedrobotics/grand_tour_dataset, MIT licence per the dataset card), mission
2024-10-01-11-29-55, by `scripts/grandtour/anymal_load_cases.py --hosted`:
- the real mission, no reported fault (false-alarm check);
- the same mission with a SYNTHETIC fault injected into the left front knee drive from 200 s (case flagged synthetic).
Lighter than the full runs in `docs/ANYMAL_ADAPTATION.md`: 60-310 s of the mission, measurements at 10 Hz, joint
torque left out. Same results: no departure on the real mission; LF_KFE departure from 09:33:30.543Z on the injected one.
