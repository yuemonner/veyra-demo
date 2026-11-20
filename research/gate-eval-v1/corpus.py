"""Shared helpers for the gate evaluation corpora (dev.py, heldout.py)."""


def A(action, *, how=(), where="on_site", tid="none", approval=True, conditions=None, **declared):
    d = {"action": action, "how": list(how), "where": where, "template_id": tid, "needs_approval": approval}
    if conditions:
        d["conditions"] = conditions
    d.update(declared)
    return d


def item(id_, situation, label, why, action):
    return {"id": id_, "situation": situation, "label": label, "why": why, "action": action}
