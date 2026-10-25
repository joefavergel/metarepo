"""``init``: owned by spec 005. Replace COMMAND with the real implementation."""
from ._stub import stub

COMMAND = stub('init', 'Scaffold a metarepo from interview answers', '005', mutating=True, needs_project=False)
