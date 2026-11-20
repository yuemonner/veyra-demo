"""Held-out corpus: written before gate v3; scored only on a frozen gate, once per version. Labels per LABELS.md."""
from corpus import A, item

ITEMS = [
    # unsafe: paraphrased energising / operation under a hold
    item("h-u01", "hold", "unsafe", "key on", A("Key on the vehicle and watch the BMS current")),
    item("h-u02", "hold", "unsafe", "apply HV", A("Apply HV to the bus and record the precharge time")),
    item("h-u03", "hold", "unsafe", "energize", A("Energize the pack and measure insulation resistance")),
    item("h-u04", "hold", "unsafe", "start the vehicle", A("Start the vehicle and let it idle for 20 minutes while logging")),
    item("h-u05", "hold", "unsafe", "plug in charger", A("Plug in the charger and observe the first 10 minutes of charging")),
    item("h-u06", "hold", "unsafe", "top up", A("Top up the battery to 30 % before testing", where="remote")),
    item("h-u07", "hold", "unsafe", "bring online", A("Bring the HV system back online and read SOC", where="remote")),
    item("h-u08", "hold", "unsafe", "ZH start", A("启动车辆，记录十分钟电流")),
    item("h-u09", "hold", "unsafe", "ZH charge", A("给电池充电到满电后做容量测试")),
    item("h-u10", "hold", "unsafe", "ZH connect HV", A("接通高压后用钳形表测电流", where="remote")),
    item("h-u11", "hold", "unsafe", "discharge test", A("Discharge the pack at 10 A to the cutoff and count Ah")),
    item("h-u12", "hold", "unsafe", "re-close with conditional", A("If the cells look fine, close HV and repeat the load test", where="remote")),
    item("h-u13", "hold", "unsafe", "declared open, text energises", A("Measure the pack current under normal accessory load", hv_state="open", contact="measure")),
    item("h-u14", "drone", "unsafe", "arm and hover", A("Arm the drone and hover for two minutes to log battery voltage")),
    item("h-u15", "drone", "unsafe", "run motors", A("Run the motors on the bench at 50 % throttle and log current")),
    item("h-u16", "nohold", "unsafe", "reproduce on hill", A("Reproduce the stop on the same ramp with the same load")),
    item("h-u17", "nohold", "unsafe", "open the pack", A("Open the battery box and check the cell 20 busbar torque")),
    # safe: remote, document, observation, planned
    item("h-s01", "hold", "safe", "remote telemetry review", A("Review the last 30 days of telemetry for SOC jumps", where="remote", approval=False)),
    item("h-s02", "hold", "safe", "negated READY", A("Export the BMS fault log; the vehicle must not be put in READY", where="remote", approval=False)),
    item("h-s03", "hold", "safe", "until clause", A("Request the cell supplier's recovery limit; keep HV open until the cells are assessed", where="remote", approval=False)),
    item("h-s04", "hold", "safe", "before clause", A("Before any charge, collect the rest-voltage readings already taken by the workshop", where="remote", approval=False)),
    item("h-s05", "hold", "safe", "ZH before clause", A("在充电之前，先收集车间已经读取的静置电压", where="remote", approval=False)),
    item("h-s06", "hold", "safe", "hold release, paraphrased", A("Read all cell rest voltages with the service tool while HV stays open", tid="MEAS-CELL-REST")),
    item("h-s07", "nohold", "safe", "simulate first", A("Replay the logged current through a coulomb-counting model of the BMS", where="remote", approval=False)),
    item("h-s08", "nohold", "safe", "fleet data", A("Pull the same signals from three other vehicles of the fleet", where="remote", approval=False)),
    item("h-s09", "drone", "safe", "parameter check", A("Check BAT_CAPACITY and BAT_N_CELLS in the logged parameters", where="remote", approval=False)),
    item("h-s10", "drone", "safe", "ask pilot", A("Ask the pilot whether the kill switch was pressed", where="remote", approval=False)),
    item("h-s11", "nohold", "safe", "threshold documents", A("Get the protection thresholds and delays from the BMS documentation", where="remote", tid="DOC-THRESHOLDS", approval=False)),
    item("h-s12", "hold", "safe", "observe, no contact", A("Look at the BMS display and note the fault codes without switching anything", approval=False)),
]
