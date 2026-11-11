# ALFA evaluation v2 — results

Measure and arms: PREREGISTRATION.md. Every flight is listed below, failures included.

| Arm | FOUND all | FOUND development | FOUND validation | FOUND-strict all | Healthy: false alarm | Healthy: clean | Median s | Runs |
|---|---|---|---|---|---|---|---|---|
| Veyra v1 (frozen baseline) | 12/36 | 6/15 | 6/21 | 11/36 | 0/10 | 1/10 | 69.1 | 47 |
| Veyra v2 | 10/12 | 3/5 | 7/7 | 10/12 | 0/2 | 2/2 | 71.8 | 15 |
| Model alone | 23/23 | 9/9 | 14/14 | 21/23 | 1/5 | 3/5 | 22.8 | 29 |

| Flight | Split | Fault | Onset s | Veyra v1 (frozen baseline) | Veyra v2 | Model alone |
|---|---|---|---|---|---|---|
| flight-01 | development | unlabelled |  | No clear control fault found; roll tracks its setpoint, pitch looks si | No control fault shown; attitude loops track setpoints through aggress | Airspeed reads 0 all flight and yaw rate is stuck at 0; throttle and p |
| flight-02 | validation | engine | 116.268 | missed · No control-surface fault found; one early low-voltage warning, roll la | FOUND · Throttle command dropped to zero at about 116 s with no recorded event | FOUND · Throttle dropped to 0% at t=116 s; motor channel ch2 went to minimum a |
| flight-03 | development | engine | 73.411 | missed · No hard control fault found; roll lags setpoint about 1 s, and pitch d | FOUND · Throttle command dropped to 0 at 73.3 s and stayed there; motor channe | FOUND · Throttle/motor output ch[2] dropped to minimum at 74 s and stayed ther |
| flight-04 | validation | engine | 116.625 | FOUND · No control-loop fault found; setpoint-led maneuvers tracked with ~1 s  | FOUND · Throttle commanded to 0% at 115.637 s and stayed there; aircraft lost  | FOUND · No clear control fault; throttle channel 2 pinned at idle from 117 s w |
| flight-05 | development | healthy |  | no cause claimed · Roll tracked its setpoint worse from t=5-15 s; the data show | clean · No control fault shown; roll, throttle and airspeed shifts f | clean · No control fault found; attitude tracks setpoints, though ya |
| flight-06 | development | engine | 114.064 | missed · No control fault found: attitude lags setpoint steps by about 1 s; low | FOUND · Throttle command dropped to zero at 113.146 s with no recorded event;  | FOUND · No attitude-control fault; throttle cut to 0% at t=115 s with channel  |
| flight-07 | validation | engine | 123.149 | missed · No control fault shown; roll tracks setpoint with ~1 s lag; start-up b | FOUND · Throttle command dropped to 0 at about 122-123 s and stayed there; ch[ | FOUND · Throttle cut to 0% at t=123s; motor output ch[2] followed to 1100us, a |
| flight-08 | development | engine | 116.791 | missed · Control fault likely: attitude/state estimates jump and lag setpoints  | missed · Throttle commanded to zero with no logged event; aircraft lost altitud | FOUND · Throttle channel 2 dropped to idle at 117 s; pitch then failed to trac |
| flight-09 | validation | engine | 91.598 | missed · Pitch loop on UAV-09 does not track its setpoint, while roll follows w | FOUND · Throttle commanded to 0 at 91.9 s without any event; roll and pitch tr | FOUND · Throttle/motor channel 2 dropped to minimum for 14 s (t=92–105) in fli |
| flight-10 | development | healthy |  | no cause claimed · No clear control fault found; startup sensor and battery war | clean · No control fault shown; roll divergence is a commanded manoe | clean · No control fault found; setpoints tracked, but yaw-rate read |
| flight-11 | validation | engine | 117.172 | missed · No control-loop fault found; low-voltage and sensor warnings at 0.9 s  | FOUND · Throttle command dropped to zero at ~117.2 s; motor channel ch[2] held | FOUND · Propulsion output ch[2] and throttle dropped to minimum twice, at t=2- |
| flight-12 | development | engine | 87.681 | missed · No clear control fault found; pitch setpoint is inverted against attit | missed · Throttle commanded to 0 and held; ch2 followed to minimum, altitude fe | FOUND · Throttle channel ch[2] pinned at minimum from t=88 s with throttle 0%, |
| flight-13 | validation | engine | 133.43 | missed · Roll tracks large setpoint swings with a fixed lag; velocity and yaw e | FOUND · Throttle commanded to 0 at about 131.8 s and stayed there; aircraft lo | FOUND · Motor/throttle channel ch[2] dropped to minimum at t=134 s and stayed  |
| flight-14 | development | engine | 90.36 | missed · No control fault found: roll and pitch follow commanded maneuvers with | FOUND · Throttle commanded to 0% at 89.527 s and never recovered; altitude fel | FOUND · Throttle channel ch[2] dropped to minimum 1100 us at t=91 s and stayed |
| flight-15 | validation | engine | 103.573 | missed · Pitch axis does not follow its setpoint (inverted, lagged response) wh | FOUND · Throttle commanded to zero at 103.3 s; the aircraft lost attitude cont | FOUND · Throttle output ch[2] cut to minimum at t=103-104 s and stayed there;  |
| flight-16 | development | healthy |  | no cause claimed · Roll tracking failed at start-up and at 9.7-13.3 s; the atti | not run | no cause claimed · No actuator control fault seen; airspeed and yaw-rate read z |
| flight-17 | validation | engine | 104.854 | missed · No clear control fault: pitch and yaw mismatches look like convention  | not run | FOUND · Throttle collapsed to 0% at t=105 s; propulsion channel ch[2] stuck at |
| flight-18 | validation | engine | 49.89 | missed · Roll and pitch lagged commands from t=0; low-voltage and sensor-health | not run | FOUND · Throttle drops to zero at t=50 s with motor channel ch[2] at minimum;  |
| flight-19 | development | healthy |  | clean · No control-loop fault found; roll lags large setpoint swings | not run | clean · No control fault found; attitude tracked setpoints, but airs |
| flight-20 | validation | elevator | 117.883 | missed · Pitch axis did not follow its setpoint; fault area is pitch control or | not run | FOUND · Pitch-axis control loss from t≈119 s; actuator ch[2] and pitch diverge |
| flight-21 | development | aileron | 105.251 | missed · Pitch response is inverted against its setpoint from the start, while  | not run | FOUND · Actuator ch[4]/ch[5] pair desynchronised: ch[4] pinned at 1500 us from |
| flight-22 | validation | elevator | 63.476 | missed · No actuator fault seen; roll tracking errors are a ~1 s delay in attit | not run | FOUND · Pitch control lost from about t=73 s: nose-down divergence and altitud |
| flight-23 | development | healthy |  | no cause claimed · Pitch control stopped following its setpoint late in the fli | not run | FALSE ALARM · Pitch control lost authority from about t=58 s: ch1 and ch2  |
| flight-24 | validation | rudder | 55.515 | FOUND · Pitch responded opposite to its command early on; ch[3] jumped to 1900 | not run | FOUND · Output channel 3 jumped to 1900 us at t=56 s and stayed there; the oth |
| flight-25 | development | rudder | 51.974 | FOUND · Control fault: output ch[3] saturated at 1900 from t=52.1 s; pitch tra | not run | FOUND · Actuator output channel 3 stuck at 1900 us from t=52 s; attitude contr |
| flight-26 | development | rudder | 60.115 | FOUND · Control fault at t≈60 s: actuator ch[3] output dropped to 1100 us and  | not run | FOUND · Actuator output ch[3] dropped to 1100 us at t=61 s and stayed there; t |
| flight-27 | development | aileron,rudder | 116.321 | FOUND · Roll loop lagged its setpoint from about 125 s; actuator-side cause no | not run | FOUND · From about t=117 s, actuator ch[5] diverged from ch[4], which froze at |
| flight-28 | validation | aileron | 65.771 | FOUND · Roll control degraded from ~t=61 s; roll actuators 4/5 froze at 1500 u | not run | FOUND · Outputs ch4 and ch5 froze at exactly 1500 us from about t=66 s, then r |
| flight-29 | validation | aileron | 111.922 | missed · No clear control-loop failure; start-up warnings and dead airspeed/yaw | not run | not run |
| flight-30 | validation | aileron | 49.98 | FOUND · Actuator output ch[4] froze at 1500 us from t=49.7 s while its twin ch | not run | FOUND · Output channel 4 froze at 1500 us from about t=50 s while paired chann |
| flight-31 | development | healthy |  | no cause claimed · No clear control fault; roll lags setpoint by about 1 s, and | not run | not run |
| flight-32 | development | aileron | 152.199 | missed · Longitudinal control fault: pitch moves opposite its setpoint twice, w | not run | not run |
| flight-33 | development | healthy |  | no cause claimed · Roll setpoint flips between about ±44 deg and attitude follo | not run | not run |
| flight-34 | validation | aileron | 73.379 | missed · Pitch loop diverged from setpoint at t=0.765 s, before the warnings; r | not run | not run |
| flight-35 | development | aileron | 72.401 | missed · No attitude-control fault found; airspeed is dead and a sensor-health  | not run | not run |
| flight-36 | development | healthy |  | no cause claimed · Roll tracking was poor in the first 18 s; no single control- | not run | not run |
| flight-37 | development | healthy |  | no cause claimed · Roll/pitch diverge from setpoint at start and near 39–43 s,  | not run | not run |
| flight-38 | validation | engine | 49.133 | missed · Roll and yaw tracked poorly, with roll lagging its setpoint about 1 s; | not run | not run |
| flight-39 | validation | engine | 100.133 | missed · Heading estimate disagrees with setpoint and velocity from t=38 s; rol | not run | not run |
| flight-40 | development | engine | 76.195 | FOUND · Throttle commanded to 0% at t=76.07 s, then altitude loss; roll contro | not run | not run |
| flight-41 | development | engine | 104.246 | FOUND · Throttle fell to 0 at t=104 s, then altitude dropped and pitch stopped | not run | not run |
| flight-42 | development | engine | 111.165 | missed · Throttle dropped to 0% late in the flight and the aircraft descended;  | not run | not run |
| flight-43 | validation | engine | 100.36 | FOUND · Sensor-health error at start, inverted pitch loop all flight, and thro | not run | not run |
| flight-44 | validation | engine | 98.274 | missed · Pitch diverged from setpoint at 1.197 s, 99.572 s and 111.163 s; roll  | not run | not run |
| flight-45 | development | engine | 101.302 | FOUND · Throttle commanded to 0 at t=101.254 s, then pitch moved opposite its  | not run | not run |
| flight-46 | validation | engine | 102.517 | FOUND · Throttle dropped to 0% at 102 s and the aircraft lost 30 m of altitude | not run | not run |
| flight-47 | development | healthy |  | no cause claimed · No clear control fault: pitch 'divergence' is sign/lag misma | not run | not run |
