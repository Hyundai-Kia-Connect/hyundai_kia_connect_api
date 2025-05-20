# /env/bin/python

import pytest
import sys

# Add the project root to the Python path
# so absolute imports work
sys.path.append(".")

pytest.main()
