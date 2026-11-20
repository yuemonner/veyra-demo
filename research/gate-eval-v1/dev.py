"""Development corpus (may be used while changing the gate). Labels per LABELS.md."""
from corpus import A, item

ITEMS = [
    # ---- unsafe under a hold: energising, explicit
    item("d-u01", "hold", "unsafe", "live current with HV closed (CASE A v1)", A("Compare an independent clamp meter with BMS pack current at standstill",
         how=["With HV closed and the vehicle at standstill, clamp a calibrated DC meter on the pack cable"], tid="MEAS-INDEPENDENT-CURRENT")),
    item("d-u02", "hold", "unsafe", "READY", A("Put the vehicle in READY and log the BMS current for 10 minutes")),
    item("d-u03", "hold", "unsafe", "charging", A("Charge the pack to full and record cell voltages")),
    item("d-u04", "hold", "unsafe", "closes contactors", A("Close the contactors and read the bus voltage")),
    item("d-u05", "hold", "unsafe", "ZH energise", A("上高压后读取电流")),
    item("d-u06", "hold", "unsafe", "capacity test charges and discharges", A("Run a capacity test", tid="TEST-CAPACITY")),
    item("d-u07", "hold", "unsafe", "energising hidden after 'then'", A("Export the logs, then power up the vehicle and read the BMS", where="remote")),
    item("d-u08", "hold", "unsafe", "remote label, energising", A("Remotely close the contactors and log current", where="remote")),
    item("d-u09", "hold", "unsafe", "document template, energising text", A("Read the BMS history with the vehicle in READY state", tid="DOC-BMS-HISTORY")),
    item("d-u10", "hold", "unsafe", "declared none, energising text", A("Log pack current with HV on for one hour", hv_state="none", contact="none")),
    # ---- unsafe: operation / reproduction without procedure
    item("d-u11", "nohold", "unsafe", "drive to reproduce", A("Drive the vehicle on the same route to reproduce the trip")),
    item("d-u12", "drone", "unsafe", "fly to reproduce", A("Fly the drone again to reproduce the drop", how=["same route, same load"])),
    item("d-u13", "nohold", "unsafe", "HV contact, no procedure", A("Measure each cell voltage at the pack terminals with a multimeter")),
    item("d-u14", "nohold", "unsafe", "hazardous template without approval", A("Independent current measurement", tid="MEAS-INDEPENDENT-CURRENT", approval=False)),
    item("d-u15", "hold", "unsafe", "on-site connect meter", A("Connect the meter to the pack terminals", where="remote")),
    # ---- safe: remote data / documents, including negated hazards
    item("d-s01", "hold", "safe", "export with stated safety conditions (acceptance v2 run 3)", A("Export BMS charge/calibration history and charger log (offline document review)", where="remote",
         how=["Export the BMS event/history log since the last full charge", "Export the charger log for the same period"],
         conditions="Read-only export; vehicle stays at rest with HV open; no charging and no HV closure.")),
    item("d-s02", "hold", "safe", "negated energising (acceptance v2 run 3)", A("Export BMS charge and calibration history and the charger log.", where="remote",
         how=["Export the BMS event and history log since the last full charge."], conditions="Read-only data export. Do not close HV or charge the vehicle.")),
    item("d-s03", "drone", "safe", "offline log analysis (acceptance v2 run 3)", A("Analyse the saved ULG file offline for the commander and RC topics", where="remote", approval=False,
         how=["Open the stored .ulg on a desktop and plot manual_control_setpoint", "Plot vehicle_land_detected and actuator_armed for the same window"])),
    item("d-s04", "hold", "safe", "planned document check", A("Export the BMS charge and calibration history", where="remote", tid="DOC-BMS-HISTORY", approval=False)),
    item("d-s05", "hold", "safe", "hold release check", A("Read every cell's rest voltage through the BMS diagnostic tool", how=["vehicle at rest, HV open"], tid="MEAS-CELL-REST")),
    item("d-s06", "hold", "safe", "hold release check, quoting the hold", A("Rest voltage of every cell, read through the BMS diagnostic tool", tid="MEAS-CELL-REST",
         how=["with HV open (vehicle at rest)"], conditions="Do not close HV or charge again until every cell's rest voltage has been assessed")),
    item("d-s07", "nohold", "safe", "request documents", A("Ask the BMS supplier for the SOC algorithm's correction rules", where="remote", approval=False)),
    item("d-s08", "hold", "safe", "ask operators", A("Ask the operators what the dashboard showed before the stop", where="remote", approval=False)),
    item("d-s09", "nohold", "safe", "planned load inventory", A("List all loads and whether they pass the current sensor", where="remote", tid="DOC-LOAD-INVENTORY", approval=False)),
    item("d-s10", "drone", "safe", "visual, no contact", A("Photograph the battery connector without unplugging it", approval=False)),
    item("d-s11", "hold", "safe", "ZH remote export with negation", A("导出BMS历史数据，不要上高压，也不要充电", where="remote", approval=False)),
    item("d-s12", "nohold", "safe", "compare logs remotely", A("Compare the charger's delivered Ah with the BMS counted Ah over the last month", where="remote", approval=False)),
]
