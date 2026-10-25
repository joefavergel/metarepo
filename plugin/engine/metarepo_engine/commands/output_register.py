"""``output register``: owned by spec 014. Replace COMMAND with the real implementation."""
from ._stub import stub

COMMAND = stub('output register', "Stamp an output's provenance", '014', writes_project=True)
