"""``remove``: owned by spec 003. Replace COMMAND with the real implementation."""
from ._stub import stub

COMMAND = stub('remove', 'Delete a non-source file after explicit confirmation', '003', writes_project=True)
