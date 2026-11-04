"""Stand-in for a text editor in tests: records which file it was asked to
open (one line per start in the log file given first) and exits. The tests
then "save" by writing the file themselves, in place or by renaming."""

import sys

with open(sys.argv[1], "a", encoding="utf-8") as log:
    log.write(sys.argv[-1] + "\n")
