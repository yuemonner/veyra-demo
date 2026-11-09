"""Development / validation split, fixed before the baseline (see PREREGISTRATION.md)."""
import hashlib

PRIOR_EXPOSURE = {
    "carbonZ_2018-10-05-14-37-22_3_left_aileron_failure",   # the AI evidence tools were developed while looking at it
    "carbonZ_2018-09-11-15-06-34_2_rudder_right_failure",   # hosted demo seed case
}


def split(name: str) -> str:
    if name in PRIOR_EXPOSURE or "no_failure" in name:      # the no-failure flights are hosted-demo known-good runs
        return "development"
    return "development" if hashlib.sha256(name.encode()).hexdigest()[0] in "01234567" else "validation"


if __name__ == "__main__":
    import sys
    for line in sys.stdin:
        n = line.strip()
        if n:
            print(f"{split(n)}\t{n}")
