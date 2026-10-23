"""Compute layer for the investigation agent: query any recorded signal, across cases, and run new analysis.

data.py     read-only access to stored readings (one case or several), with units, clocks and source rows
methods.py  built-in analysis methods (onset, onset order, lag, run comparison) and the team's saved methods
sandbox.py  runs Python written by the agent or the team in a separate process: no network, resource limits,
            writes only inside its own temporary folder

Every result carries its provenance: which case, which readings (with a fingerprint and the first and last source
row), which code or method version, which isolation was used, and how long it took.
"""
