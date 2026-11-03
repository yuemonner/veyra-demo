"""SYNTHETIC demo case: a PIX-style wheeled shuttle (72 V pack, four wheel motors) stops on a hill start.

Writes fixtures/synthetic/demo/shuttle-hill-start-stop/{log.txt, source.json, truth.json}. Deterministic (seeded).
Vehicle parameters follow PIX Moving's published Hooke 2.0 chassis specs where they exist (72 V 150 Ah ternary pack,
4 x wheel motors, 126 N·m rated / 180 N·m peak, CAN 2.0B 500 kbps, 20 % gradeability); everything else is made up.

The log is a CAN logger export decoded with the chassis DBC: three windows of the same vehicle on the same 8 % slope.
  A 2026-10-28 07:42  cold (2 °C), SOC 38 %, VCU 2.4.1 (hill-start feed-forward)  -> emergency stop, remote code 7
  B 2026-10-28 14:10  warm (18 °C), SOC 61 %, VCU 2.4.1                            -> normal launch
  C 2026-10-09 07:38  cold (3 °C),  SOC 35 %, VCU 2.3.8 (no feed-forward)          -> normal launch
Ground truth (truth.json, never shown to the investigator): VCU 2.4.1 adds the hill-start feed-forward torque AFTER the
BMS torque clamp, so on a cold low-SOC morning the request exceeds the discharge current limit; cell 13 (high internal
resistance) and a resistive FR high-voltage branch deepen the sag at the FR inverter, which trips on DC-bus undervoltage
first and is reported as an FR motor fault. The FR inverter itself (replaced on 10-21) is not the cause.
"""
import json
import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "demo" / "shuttle-hill-start-stop"
rng = random.Random(20261028)

MASS, G, R_WHEEL, GRADE = 960.0, 9.81, 0.19, 0.08
UV_THRESHOLD, UV_DEBOUNCE = 55.0, 0.05
A_PER_NM = 0.46            # DC current per N·m of total wheel torque at launch speeds (calibrated, incl. losses)
R_BRANCH = {"FL": 0.008, "FR": 0.026, "RL": 0.009, "RR": 0.008}


def n(x, s):  # noise
    return x + rng.gauss(0, s)


def window(name, date, t_start, temp_c, soc, vcu_sw, hsa, r_pack, ocv, cell13_extra, dcl, fail):
    """Simulate one launch. Returns log lines and a summary."""
    lines, summary = [], {"window": name}
    hh, mm, ss = (int(x) for x in t_start.split(":"))
    base_s = hh * 3600 + mm * 60 + ss
    def ts(t):
        s = base_s + t
        return f"{date}T{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"
    def add(t, msg, text, level=""):
        lines.append(f"{ts(t)} {level + ' ' if level else ''}{msg:<13} {text}")
    tq_lim_bms = min(180.0, (dcl - 2.5) / A_PER_NM / 4)   # VCU maps the BMS DCL (minus auxiliary load) to a per-wheel torque limit
    v_speed, ad_target, epb = 0.0, 0.0, "APPLIED"
    faulted, uv_since, fr_dtc_at, stop_at = False, None, None, None
    min_v_fr, min_v, max_i = 99.0, 99.0, 0.0
    t, dt = 0.0, 0.1
    end = 9.0
    while t <= end + 1e-9:
        launch = t >= 2.0
        # AD stack command
        if launch and not faulted:
            ad_target = min(1.5, (t - 2.0) * 1.2)
        elif faulted:
            ad_target = 0.0
        # VCU torque arbitration
        if launch and not faulted:
            ramp = min(1.0, (t - 2.0) / 0.4)
            base = 79.0 * ramp + (8.0 if v_speed > 0.2 else 0.0)
            ff = (45.0 if hsa and t < 2.0 + 0.9 else 0.0) * ramp
            if hsa:
                out = min(base, tq_lim_bms) + ff        # 2.4.1: feed-forward added after the BMS clamp
            else:
                out = min(base, tq_lim_bms)
            epb = "RELEASED" if t >= 2.15 else "RELEASING"
        else:
            base = ff = out = 0.0
            if faulted:
                epb = "APPLIED"
        tq_act = {w: 0.0 for w in R_BRANCH}
        for w in R_BRANCH:
            tq_act[w] = 0.0 if (faulted or not launch) else n(out, 0.8)
        if fr_dtc_at is not None:
            tq_act["FR"] = 0.0
        total_tq = sum(tq_act.values())
        i_pack = max(2.5, n(A_PER_NM * total_tq + 2.5, 1.2))   # 2.5 A: DC-DC + controllers idle
        cell_avg_drop = r_pack * i_pack
        v_pack = ocv - cell_avg_drop
        v_wheel = {w: v_pack - (i_pack / 4) * R_BRANCH[w] for w in R_BRANCH}
        cell_avg = v_pack / 20
        cell_min = cell_avg - cell13_extra * i_pack
        # vehicle motion
        f_drive = sum(tq_act.values()) / R_WHEEL
        f_res = MASS * G * (math.sin(math.atan(GRADE)) + 0.015 * math.cos(math.atan(GRADE)))
        acc = (f_drive - f_res) / MASS if epb == "RELEASED" else 0.0
        if epb != "RELEASED" and v_speed <= 0:
            acc, v_speed = 0.0, 0.0
        v_speed = max(-0.05, v_speed + acc * dt) if not faulted else max(0.0, v_speed - 1.5 * dt)
        # undervoltage at the FR inverter
        if launch and not faulted and v_wheel["FR"] < UV_THRESHOLD:
            uv_since = t if uv_since is None else uv_since
            if t - uv_since >= UV_DEBOUNCE - 1e-9 and fr_dtc_at is None and fail:
                fr_dtc_at = t
        else:
            uv_since = None
        min_v_fr, min_v, max_i = min(min_v_fr, v_wheel["FR"]), min(min_v, v_pack), max(max_i, i_pack)
        rpm = v_speed / (2 * math.pi * R_WHEEL) * 60
        # --- frames (10 Hz)
        add(t, "AD_Cmd", f"TargetSpeed_mps={ad_target:.2f} Mode=AUTO Route=B-ramp")
        add(t, "VCU_Status", f"State={'FAULT' if faulted else 'DRIVE' if launch else 'READY'} Gear={'D' if launch else 'P'} HSA_Active={1 if (hsa and launch and t < 2.9 and not faulted) else 0} "
                             f"FaultLevel={3 if faulted else 0} Remote_Code={7 if faulted else 1} VCU_SW={vcu_sw}")
        add(t, "VCU_TqDebug", f"TqReq_Base={base:.1f} TqHSA_FF={ff:.1f} TqLim_BMS={tq_lim_bms:.1f} TqCmd_Out={out:.1f} (N.m per wheel)")
        add(t, "BMS_Status", f"Pack_V={n(v_pack, 0.05):.2f} Pack_I={i_pack:.1f} SOC={soc:.0f} DCL_A={dcl:.0f} T_cell_min={temp_c:.0f} T_cell_max={temp_c + 2:.0f} SOH=91")
        add(t, "BMS_Cell", f"Cell_V_min={n(cell_min, 0.002):.3f} Cell_V_min_id=13 Cell_V_avg={n(cell_avg, 0.001):.3f} Cell_V_max={n(cell_avg + 0.012, 0.001):.3f}")
        for w in ("FL", "FR", "RL", "RR"):
            dtc = "0x2A11" if (w == "FR" and fr_dtc_at is not None) else "0x0000"
            add(t, f"MCU_{w}", f"DC_V={n(v_wheel[w], 0.04):.2f} Tq_Act={tq_act[w]:.1f} Spd_rpm={rpm:.0f} T_inv={temp_c + 4:.0f} DTC={dtc}")
        add(t, "Veh", f"Speed_mps={max(0.0, v_speed):.2f} Pitch_deg=4.57 EPB={epb} DCDC_12V={n(13.6, 0.02):.2f}")
        # events
        if launch and abs(t - 2.0) < 1e-6:
            add(t, "VCU_Event", f"INFO Launch on grade: EPB release requested, HSA {'enabled' if hsa else 'not available in this software'}")
        if i_pack > dcl * 1.05 and not summary.get("dcl_exceeded_at"):
            summary["dcl_exceeded_at"] = ts(t)
            add(t, "BMS_Event", f"WARN discharge current {i_pack:.0f} A above DCL {dcl:.0f} A")
        if fr_dtc_at is not None and abs(t - fr_dtc_at) < 1e-6:
            add(t, "MCU_FR_Event", f"ERROR DTC 0x2A11 DC bus under-voltage ({v_wheel['FR']:.1f} V < {UV_THRESHOLD:.1f} V for >= 50 ms), torque disabled")
        if fr_dtc_at is not None and not faulted and t >= fr_dtc_at + 0.05 - 1e-6:
            faulted, stop_at = True, t
            add(t, "VCU_Event", "ERROR FaultLevel=3 MotorFault_FR=1: torque cut on all wheels, EPB apply, Remote_Code=7")
            add(t + 0.05, "AD_Event", "ERROR chassis fault reported by VCU -> EMERGENCY STOP, mission paused")
        dt = 0.05 if 1.8 <= t < 3.6 else 0.1   # the logger records at 20 Hz around the launch
        t = round(t + dt, 3)
    summary.update({"min_v_pack": round(min_v, 2), "min_v_fr": round(min_v_fr, 2), "max_i": round(max_i, 1), "dcl": dcl,
                    "faulted": faulted, "fr_dtc_at": ts(fr_dtc_at) if fr_dtc_at is not None else None, "tq_lim_bms": round(tq_lim_bms, 1)})
    return lines, summary


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    head = [
        "# CAN log export, chassis bus (CAN 2.0B, 500 kbps), decoded with chassis DBC v1.6. SYNTHETIC DEMO DATA, not from a real vehicle.",
        "# Vehicle SHUTTLE-03 (park shuttle on a 4WD drive-by-wire chassis). Pack 72 V 150 Ah NCM 20S. Wheel motors 4 x 126 N.m rated / 180 N.m peak.",
        "# Software: VCU_SW 2.3.8 until 2026-10-14, then 2.4.1 (OTA, adds hill-start assist HSA). BMS_SW 1.9.2. MCU_SW 3.2.0.",
        "# Service: FR motor inverter (MCU_FR) replaced 2026-10-21 after remote code 7 on 2026-10-20. Problem returned on 2026-10-28.",
        "# Units: torque N.m per wheel, current A, voltage V, speed m/s. MCU_FR undervoltage threshold 55.0 V (debounce 50 ms).",
        "# Three snapshots of the same vehicle launching on the same 8 % ramp (route B-ramp, stop line at the top of the ramp).",
    ]
    out = list(head)
    windows = [
        dict(name="C", date="2026-10-09", t_start="07:38:20", temp_c=3, soc=35, vcu_sw="2.3.8", hsa=False, r_pack=0.068, ocv=72.0, cell13_extra=0.00048, dcl=150, fail=False),
        dict(name="A", date="2026-10-28", t_start="07:42:00", temp_c=2, soc=38, vcu_sw="2.4.1", hsa=True, r_pack=0.072, ocv=72.6, cell13_extra=0.00055, dcl=150, fail=True),
        dict(name="B", date="2026-10-28", t_start="14:10:05", temp_c=18, soc=61, vcu_sw="2.4.1", hsa=True, r_pack=0.032, ocv=74.4, cell13_extra=0.00022, dcl=300, fail=False),
    ]
    labels = {"C": "snapshot 1 of 3: 2026-10-09 morning launch on B-ramp (before the VCU 2.4.1 OTA)",
              "A": "snapshot 2 of 3: 2026-10-28 morning launch on B-ramp (field report: emergency stop, remote code 7)",
              "B": "snapshot 3 of 3: 2026-10-28 afternoon launch on B-ramp (same vehicle, after the restart)"}
    summaries = []
    for w in windows:
        out.append("")
        out.append(f"# ---- {labels[w['name']]}")
        lines, s = window(**w)
        out += lines
        summaries.append(s)
    (OUT / "log.txt").write_text("\n".join(out) + "\n")
    (OUT / "source.json").write_text(json.dumps({
        "title": "园区接驳车坡道起步急停，遥控器报 FR 电机故障（码 7）",
        "platform": "4WD drive-by-wire wheeled shuttle: 72 V NCM pack with BMS, VCU, four wheel-motor inverters (MCU), EPB, autonomous driving stack on the chassis CAN",
        "symptom": ("10/28 早上 7:42，3 号接驳车在园区 B 区 8% 坡道起步时急停，遥控器显示故障码 7（FR 电机故障），自动驾驶任务暂停。断电重启后恢复，下午同一坡道正常。"
                    "近两周同一坡道早班出现过类似停车。10/21 已更换 FR 电机控制器，问题仍在。"),
        "source_url": None, "project": "Veyra synthetic demo (parameters after PIX Moving's published Hooke 2.0 chassis specs)",
        "opened": "2026-10-28", "provenance": "SYNTHETIC: generated by scripts/demo/make_pix_shuttle_case.py for a demo. Not a real vehicle or a real fault.",
        "machine_id": "SHUTTLE-03", "synthetic": True, "lang": "zh"}, ensure_ascii=False, indent=1))
    (OUT / "truth.json").write_text(json.dumps({"summaries": summaries, "cause": __doc__.split("Ground truth")[1].strip()}, ensure_ascii=False, indent=1))
    print(len(out), "lines")
    for s in summaries:
        print(s)


if __name__ == "__main__":
    main()
