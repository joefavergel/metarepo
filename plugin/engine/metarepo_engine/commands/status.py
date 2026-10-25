"""``status``: owned by spec 003. Replace COMMAND with the real implementation."""
from ._stub import stub

COMMAND = stub('status', 'Root, versions, compatibility, gates, dependencies', '003', gate=False, needs_yaml=False, needs_project=False)
