#!/usr/bin/env python
import sys

from wptrunner import update, wptcommandline

if __name__ == "__main__":
    parser = wptcommandline.create_parser_update()
    args = parser.parse_args()
    success = update.main(**vars(args))
    sys.exit(0 if success else 1)
