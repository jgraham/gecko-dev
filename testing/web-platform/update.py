#!/usr/bin/env python
import sys
import os

here = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(here, "harness"))

from wptrunner import update, wptcommandline

if __name__ == "__main__":
    success = update.main()
    sys.exit(0 if success else 1)
