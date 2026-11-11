# ALFA evaluation v2 — results

Measure and arms: PREREGISTRATION.md. Every flight is listed below, failures included.

| Arm | FOUND all | FOUND development | FOUND validation | FOUND-strict all | Healthy: false alarm | Healthy: clean | Median s | Runs |
|---|---|---|---|---|---|---|---|---|
| Veyra v1 (frozen baseline) | 21/36 | 11/15 | 10/21 | 18/36 | 0/10 | 1/10 | 69.1 | 47 |
| Veyra v2 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |  | 0 |
| Model alone | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |  | 0 |

| Flight | Split | Fault | Onset s | Veyra v1 (frozen baseline) | Veyra v2 | Model alone |
|---|---|---|---|---|---|---|
| flight-01 | development | unlabelled |  | No clear control fault found; roll tracks its setpoint, pitch looks si | not run | not run |
| flight-02 | validation | engine | 116.268 | missed · No control-surface fault found; one early low-voltage warning, roll la | not run | not run |
| flight-03 | development | engine | 73.411 | FOUND · No hard control fault found; roll lags setpoint about 1 s, and pitch d | not run | not run |
| flight-04 | validation | engine | 116.625 | FOUND · No control-loop fault found; setpoint-led maneuvers tracked with ~1 s  | not run | not run |
| flight-05 | development | healthy |  | no cause claimed · Roll tracked its setpoint worse from t=5-15 s; the data show | not run | not run |
| flight-06 | development | engine | 114.064 | missed · No control fault found: attitude lags setpoint steps by about 1 s; low | not run | not run |
| flight-07 | validation | engine | 123.149 | missed · No control fault shown; roll tracks setpoint with ~1 s lag; start-up b | not run | not run |
| flight-08 | development | engine | 116.791 | FOUND · Control fault likely: attitude/state estimates jump and lag setpoints  | not run | not run |
| flight-09 | validation | engine | 91.598 | FOUND · Pitch loop on UAV-09 does not track its setpoint, while roll follows w | not run | not run |
| flight-10 | development | healthy |  | no cause claimed · No clear control fault found; startup sensor and battery war | not run | not run |
| flight-11 | validation | engine | 117.172 | missed · No control-loop fault found; low-voltage and sensor warnings at 0.9 s  | not run | not run |
| flight-12 | development | engine | 87.681 | missed · No clear control fault found; pitch setpoint is inverted against attit | not run | not run |
| flight-13 | validation | engine | 133.43 | missed · Roll tracks large setpoint swings with a fixed lag; velocity and yaw e | not run | not run |
| flight-14 | development | engine | 90.36 | missed · No control fault found: roll and pitch follow commanded maneuvers with | not run | not run |
| flight-15 | validation | engine | 103.573 | missed · Pitch axis does not follow its setpoint (inverted, lagged response) wh | not run | not run |
| flight-16 | development | healthy |  | no cause claimed · Roll tracking failed at start-up and at 9.7-13.3 s; the atti | not run | not run |
| flight-17 | validation | engine | 104.854 | missed · No clear control fault: pitch and yaw mismatches look like convention  | not run | not run |
| flight-18 | validation | engine | 49.89 | missed · Roll and pitch lagged commands from t=0; low-voltage and sensor-health | not run | not run |
| flight-19 | development | healthy |  | clean · No control-loop fault found; roll lags large setpoint swings | not run | not run |
| flight-20 | validation | elevator | 117.883 | FOUND · Pitch axis did not follow its setpoint; fault area is pitch control or | not run | not run |
| flight-21 | development | aileron | 105.251 | missed · Pitch response is inverted against its setpoint from the start, while  | not run | not run |
| flight-22 | validation | elevator | 63.476 | missed · No actuator fault seen; roll tracking errors are a ~1 s delay in attit | not run | not run |
| flight-23 | development | healthy |  | no cause claimed · Pitch control stopped following its setpoint late in the fli | not run | not run |
| flight-24 | validation | rudder | 55.515 | FOUND · Pitch responded opposite to its command early on; ch[3] jumped to 1900 | not run | not run |
| flight-25 | development | rudder | 51.974 | FOUND · Control fault: output ch[3] saturated at 1900 from t=52.1 s; pitch tra | not run | not run |
| flight-26 | development | rudder | 60.115 | FOUND · Control fault at t≈60 s: actuator ch[3] output dropped to 1100 us and  | not run | not run |
| flight-27 | development | aileron,rudder | 116.321 | FOUND · Roll loop lagged its setpoint from about 125 s; actuator-side cause no | not run | not run |
| flight-28 | validation | aileron | 65.771 | FOUND · Roll control degraded from ~t=61 s; roll actuators 4/5 froze at 1500 u | not run | not run |
| flight-29 | validation | aileron | 111.922 | missed · No clear control-loop failure; start-up warnings and dead airspeed/yaw | not run | not run |
| flight-30 | validation | aileron | 49.98 | FOUND · Actuator output ch[4] froze at 1500 us from t=49.7 s while its twin ch | not run | not run |
| flight-31 | development | healthy |  | no cause claimed · No clear control fault; roll lags setpoint by about 1 s, and | not run | not run |
| flight-32 | development | aileron | 152.199 | FOUND · Longitudinal control fault: pitch moves opposite its setpoint twice, w | not run | not run |
| flight-33 | development | healthy |  | no cause claimed · Roll setpoint flips between about ±44 deg and attitude follo | not run | not run |
| flight-34 | validation | aileron | 73.379 | missed · Pitch loop diverged from setpoint at t=0.765 s, before the warnings; r | not run | not run |
| flight-35 | development | aileron | 72.401 | FOUND · No attitude-control fault found; airspeed is dead and a sensor-health  | not run | not run |
| flight-36 | development | healthy |  | no cause claimed · Roll tracking was poor in the first 18 s; no single control- | not run | not run |
| flight-37 | development | healthy |  | no cause claimed · Roll/pitch diverge from setpoint at start and near 39–43 s,  | not run | not run |
| flight-38 | validation | engine | 49.133 | FOUND · Roll and yaw tracked poorly, with roll lagging its setpoint about 1 s; | not run | not run |
| flight-39 | validation | engine | 100.133 | missed · Heading estimate disagrees with setpoint and velocity from t=38 s; rol | not run | not run |
| flight-40 | development | engine | 76.195 | FOUND · Throttle commanded to 0% at t=76.07 s, then altitude loss; roll contro | not run | not run |
| flight-41 | development | engine | 104.246 | FOUND · Throttle fell to 0 at t=104 s, then altitude dropped and pitch stopped | not run | not run |
| flight-42 | development | engine | 111.165 | FOUND · Throttle dropped to 0% late in the flight and the aircraft descended;  | not run | not run |
| flight-43 | validation | engine | 100.36 | FOUND · Sensor-health error at start, inverted pitch loop all flight, and thro | not run | not run |
| flight-44 | validation | engine | 98.274 | FOUND · Pitch diverged from setpoint at 1.197 s, 99.572 s and 111.163 s; roll  | not run | not run |
| flight-45 | development | engine | 101.302 | FOUND · Throttle commanded to 0 at t=101.254 s, then pitch moved opposite its  | not run | not run |
| flight-46 | validation | engine | 102.517 | FOUND · Throttle dropped to 0% at 102 s and the aircraft lost 30 m of altitude | not run | not run |
| flight-47 | development | healthy |  | no cause claimed · No clear control fault: pitch 'divergence' is sign/lag misma | not run | not run |
