#!/usr/bin/env python3
"""
Scheduled entry point. Called by the cloud scheduler with a run-type argument.

Usage:
    python run.py premarket    # 9:15 AM ET
    python run.py midopen      # 10:00 AM ET
    python run.py midday       # 12:00 PM ET
    python run.py afternoon    # 2:30 PM ET
    python run.py preclose     # 3:45 PM ET
"""

import logging
import sys

from engine import Engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

_DISPATCH = {
    "premarket": lambda e: e.run_premarket(),
    "midopen":   lambda e: e.run_midopen(),
    "midday":    lambda e: e.run_midday(),
    "afternoon": lambda e: e.run_midday(),
    "preclose":  lambda e: e.run_preclose(),
}

if __name__ == "__main__":
    run_type = sys.argv[1] if len(sys.argv) > 1 else "midday"
    if run_type not in _DISPATCH:
        print(f"Unknown run type '{run_type}'. Valid options: {list(_DISPATCH)}")
        sys.exit(1)

    engine = Engine()
    _DISPATCH[run_type](engine)
