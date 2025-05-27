# !/env/bin/python

import pytest
import sys

DEFAULT_OPTS = ["-vv"]

# Add the project root to the Python path
# so absolute imports work
sys.path.append(".")

print("Running pytest with default options:", DEFAULT_OPTS)

pytest.main(args=DEFAULT_OPTS)
