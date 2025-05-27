# !/env/bin/python

import pytest
import sys

DEFAULT_OPTS = ["-vv"]

# Add the project root to the Python path
# so absolute imports work
sys.path.append(".")

args = DEFAULT_OPTS + sys.argv[1:]
print("Running pytest with the following args:", args)

pytest.main(args=args)
