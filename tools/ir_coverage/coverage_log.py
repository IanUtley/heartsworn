"""Helpers for reading one isolated Unity headless coverage run."""

import re


RUN_BEGIN = re.compile(r"\[HexCoverageRun\] begin id=([^ ]+)")


