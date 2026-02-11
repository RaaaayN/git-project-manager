#!/usr/bin/env python3
from __future__ import annotations

import sys

from project_os_agent import main


if __name__ == "__main__":
    raise SystemExit(main(["process-event", *sys.argv[1:]]))
