# SYNTHETIC: AMR-07 navigation failure (ROS 2 console)

Written by hand for testing, not from a real robot. It follows ROS 2 / Nav2 console formats: a control loop that misses
its rate, a lidar driver whose queue fills and then times out, the navigation goal failing, an emergency stop, the
kernel's out-of-memory killer ending the lidar driver, and the launch system respawning it. There is no restart of the
machine. Used by `backend/tests/test_logs_lessons_mcp.py` to check that a log without a restart still gets an incident,
what warned first, and checks specific to what failed.

# SYNTHETIC: cell-3 arm protective stop (Linux syslog)

`cell3_arm_syslog.log`, also written by hand. EtherCAT working-counter warnings, a following error on axis 3, a
protective stop, then the motion planner segfaulting and systemd restarting it. The point of this one: the process that
crashed is NOT the lead (it crashed after the stop); what went wrong right before the stop is.
