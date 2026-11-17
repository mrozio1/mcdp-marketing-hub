import os

# Tests written before per-person access expect viewers to open every tab (as they could then). The access tests
# (tests/test_access.py) switch this off to check the real defaults: viewers start with nothing.
os.environ.setdefault("ADS_VIEWER_DEFAULTS", "legacy")
