"""``sign``: owned by spec 010. Replace COMMAND with the real implementation."""
from ._stub import stub

COMMAND = stub('sign', 'Record a governance sign-off on a page', '010', writes_project=True)
