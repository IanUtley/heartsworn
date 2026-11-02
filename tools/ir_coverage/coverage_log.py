"""Helpers for reading one isolated Unity headless coverage run."""

import os
import re


RUN_BEGIN = re.compile(r"\[HexCoverageRun\] begin id=([^ ]+)")


