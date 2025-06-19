# !/env/bin/python

import pytest
import sys

# Below are some sensible defaults for running tests which
# should improve overall developer experience.
DEFAULT_OPTS = [
    # Increase verbosity (so pytest-clarity kicks in)
    "-vv",
    # --last-failed: Run only the tests that failed in the last run
    "--lf",
    # Capture stdout and stderr
    "-s",
]

# Add the project root to the Python path
# so absolute imports work
sys.path.append(".")

args = DEFAULT_OPTS + sys.argv[1:]
print("Running pytest with the following args:", args)

pytest.main(args=args)
