"""``lease release``: owned by spec 004. Replace COMMAND with the real implementation."""
from ._stub import stub

COMMAND = stub('lease release', 'Release a write lease', '004', writes_project=True)
