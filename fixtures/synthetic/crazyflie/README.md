# Crazyflie watchdog restarts: two RECONSTRUCTED console logs (synthetic)

Not original logs. Written for the demo from public descriptions of two Crazyflie incidents, using the firmware's
standard console message formats. No text is copied from the threads.

- `cf_A_hlcommander_watchdog.log`: modelled on bitcraze/crazyflie-firmware issue #531 ("Drone fails mid-air on second
  run"): the aircraft fell about 3 s after takeoff; on reboot the console reported a watchdog timeout and a failed
  FreeRTOS assert (queue.c:1429). Maintainers traced it to a stack overflow of the high-level commander task after a
  change that moved its stack to static memory; reverting fixed it for the reporter.
  https://github.com/bitcraze/crazyflie-firmware/issues/531
- `cf_B_inflight_restart.log`: modelled on a forum report of a Crazyflie restarting in flight: the console shows a
  watchdog timeout and no assert; the engineer ruled out stack overflow; a debugger later showed a UART / DMA wait loop
  that never finished; the trigger was not found. Timestamps were not recorded.

Machine context, closure and outcome records for case A are written by `backend/app/workflows/crazyflie_demo.py`
and are labelled SYNTHETIC.
