"""Advisory write leases with settle-and-verify (004/R1-R8, 004/ADR-001; P9).

A lease is the file ``_system/locks/<area>.lease.json``. It is advisory and probabilistic under sync
latency: after writing, the acquirer waits ``governance.lease_settle_seconds`` and re-reads; if the nonce
is not its own, another session won and the acquirer backs off (exit 3). Conflict detection (``conflicts``)
remains the safety net.

Public API::

    acquire(root, area, actor, operation, ...) -> Acquired       # lease + takeover info; raises LeaseHeld
    verify(root, lease) -> bool
    renew(root, lease) -> Lease                                   # verifies the nonce first
    release(root, lease) -> Lease                                 # writes released_at, keeps the file
    status(root, area) -> dict
    require_lease(root, area, lease_or_nonce) -> Lease            # call before writing a hot file
    held(root, area, actor, operation) -> context manager         # acquire ... release (also on error)
    update_hot_file(root, handle, rel, transform)                 # R8: verify + read-modify-write, one replace

Everything that touches the shared file goes through a ``LeaseStore`` so tests can substitute a store with
delayed visibility. The clock and the sleep function are injectable for the same reason.
"""
from __future__ import annotations

import json
import os
import platform
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from .events import append_event
from .fsops import atomic_write_bytes, fs_path
from .jsonout import EXIT_COMPAT, EXIT_PRECONDITION, EngineError

AREAS = ("wiki", "intake", "config")
LOCKS_REL = "_system/locks"
SETTLE_ENV = "METAREPO_SETTLE_SECONDS"
SESSION_ENV = "METAREPO_SESSION"

# 004/R8. Value: the areas whose lease authorises a write to the file. ``_system/state.json`` carries both the
# configuration gate (config) and the wiki gate (wiki), hence two areas.
HOT_FILES: dict[str, tuple[str, ...]] = {
    "30_wiki/index.md": ("wiki",),
    "30_wiki/log.md": ("wiki",),
    "_system/state.json": ("config", "wiki"),
    "project.yaml": ("config",),
}

# 004/R1: which area guards a root-relative path.
_AREA_PREFIXES = (
    ("30_wiki/", "wiki"),
    ("00_inbox/", "intake"),
    ("10_sources/", "intake"),
    ("20_extracts/", "intake"),
)
_CONFIG_FILES = ("project.yaml", "AGENTS.md", "README.md", "code/README.md", "_system/state.json")

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def area_for(rel: str) -> str | None:
    """The lease area guarding a root-relative POSIX path, or None when the path needs no lease (R1)."""
    rel = rel.replace("\\", "/").lstrip("./")
    for prefix, area in _AREA_PREFIXES:
        if rel.startswith(prefix):
            return area
    if rel in _CONFIG_FILES:
        return "config"
    return None


def lease_rel(area: str) -> str:
    return f"{LOCKS_REL}/{area}.lease.json"


def _check_area(area: str) -> None:
    if area not in AREAS:
        raise EngineError("unknown_area", f"unknown lease area {area!r}; expected one of {', '.join(AREAS)}",
                          2)


# --------------------------------------------------------------------------- time and environment
def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def fmt_ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_TS_FORMAT)


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, _TS_FORMAT).replace(tzinfo=timezone.utc)


class LeaseStore(Protocol):
    def read(self, path: Path) -> bytes | None: ...

    def write(self, path: Path, data: bytes) -> None: ...


class FsStore:
    """The real file system: atomic writes, ``None`` for a missing file."""

    def read(self, path: Path) -> bytes | None:
        try:
            with open(fs_path(path), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def write(self, path: Path, data: bytes) -> None:
        atomic_write_bytes(path, data)


@dataclass
class LeaseEnv:
    store: LeaseStore = field(default_factory=FsStore)
    clock: Callable[[], datetime] = _utcnow
    sleep: Callable[[float], None] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sleep is None:
            import time

            self.sleep = time.sleep


def _env(env: LeaseEnv | None) -> LeaseEnv:
    return env or LeaseEnv()


# --------------------------------------------------------------------------- data
@dataclass
class Lease:
    area: str
    holder: dict[str, str]
    acquired_at: str
    renewed_at: str
    expires_at: str
    operation: str
    nonce: str
    released_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "area": self.area, "holder": dict(self.holder), "acquired_at": self.acquired_at,
            "renewed_at": self.renewed_at, "expires_at": self.expires_at, "operation": self.operation,
            "nonce": self.nonce,
        }
        if self.released_at:
            out["released_at"] = self.released_at
        return out

    def to_bytes(self) -> bytes:
        return (json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lease":
        try:
            holder = data["holder"]
            return cls(area=data["area"], holder={k: str(holder[k]) for k in ("actor", "host", "session")},
                       acquired_at=data["acquired_at"], renewed_at=data["renewed_at"],
                       expires_at=data["expires_at"], operation=data["operation"], nonce=data["nonce"],
                       released_at=data.get("released_at"))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"missing or invalid lease field: {exc}") from exc

    @property
    def released(self) -> bool:
        return bool(self.released_at)

    def expired(self, now: datetime) -> bool:
        return parse_ts(self.expires_at) <= now

    def active(self, now: datetime) -> bool:
        return not self.released and not self.expired(now)

    def describe(self) -> str:
        h = self.holder
        return (f"{h.get('actor')}@{h.get('host')} (session {h.get('session')}), operation "
                f"'{self.operation}', expires {self.expires_at}")


@dataclass
class Acquired:
    lease: Lease
    took_over: dict[str, Any] | None = None  # previous holder when an expired lease was taken over


class LeaseHeld(EngineError):
    """Another holder has the area (exit 3)."""

    def __init__(self, area: str, other: Lease | None, message: str):
        super().__init__("lease_held", message, EXIT_PRECONDITION)
        self.area = area
        self.other = other


class LeaseLost(EngineError):
    """The caller's lease is no longer valid (taken over, expired, released) (exit 3)."""

    def __init__(self, area: str, message: str):
        super().__init__("lease_lost", message, EXIT_PRECONDITION)
        self.area = area


# --------------------------------------------------------------------------- configuration
def governance(root: Path) -> dict[str, Any]:
    """Effective ``governance`` block (documented defaults applied) of the project at ``root``."""
    from .config import effective
    from .root import Project

    return dict(effective(Project(root).config()).get("governance") or {})


def settle_seconds(gov: dict[str, Any]) -> float:
    override = os.environ.get(SETTLE_ENV)
    if override not in (None, ""):
        return float(override)
    return float(gov.get("lease_settle_seconds", 10))


def ttl_minutes(gov: dict[str, Any]) -> float:
    return float(gov.get("lease_ttl_minutes", 30))


def new_session() -> str:
    return os.environ.get(SESSION_ENV) or uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- file access
def _path(root: Path, area: str) -> Path:
    return Path(root) / lease_rel(area)


def _read(env: LeaseEnv, root: Path, area: str) -> Lease | None:
    raw = env.store.read(_path(root, area))
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        lease = Lease.from_dict(data)
        parse_ts(lease.expires_at)
        return lease
    except (ValueError, UnicodeDecodeError) as exc:
        raise EngineError("lease_corrupt", f"{lease_rel(area)} is unreadable ({exc}); run /doctor, then remove "
                          "the file once nobody is writing", EXIT_COMPAT, lease_rel(area)) from exc


def _write(env: LeaseEnv, root: Path, lease: Lease) -> None:
    env.store.write(_path(root, lease.area), lease.to_bytes())


def _log(root: Path, command: str, area: str, actor: str, extra: dict[str, Any]) -> None:
    try:
        append_event(root, command=command, targets=[lease_rel(area)], result="ok", actor=actor,
                     extra={"area": area, **extra})
    except OSError:  # the audit log is best effort; never fail a lease over it
        pass


# --------------------------------------------------------------------------- operations
def acquire(
    root: Path,
    area: str,
    actor: str,
    operation: str,
    *,
    session: str | None = None,
    host: str | None = None,
    ttl: float | None = None,
    settle: float | None = None,
    gov: dict[str, Any] | None = None,
    env: LeaseEnv | None = None,
    log: bool = True,
) -> Acquired:
    """Acquire ``area`` (R3, R4, R5, R7). ``ttl`` is in minutes, ``settle`` in seconds."""
    _check_area(area)
    env = _env(env)
    root = Path(root)
    gov = gov if gov is not None else governance(root)
    ttl_min = ttl if ttl is not None else ttl_minutes(gov)
    settle_s = settle if settle is not None else settle_seconds(gov)
    check_owner(gov, area, actor)
    me = {"actor": actor, "host": host or platform.node() or "unknown", "session": session or new_session()}

    took_over: dict[str, Any] | None = None
    for _attempt in range(3):
        before = env.store.read(_path(root, area))
        current = _read(env, root, area)
        now = env.clock()
        if current is not None and current.active(now):
            if current.holder == me:  # same session asking again: idempotent refresh (P12)
                return Acquired(renew(root, current, ttl=ttl_min, env=env))
            raise LeaseHeld(area, current, f"'{area}' is held by {current.describe()}")
        took_over = None
        if current is not None and not current.released:
            took_over = {"holder": current.holder, "operation": current.operation,
                         "expired_at": current.expires_at, "nonce": current.nonce}
        lease = Lease(area=area, holder=me, acquired_at=fmt_ts(now), renewed_at=fmt_ts(now),
                      expires_at=fmt_ts(now + timedelta(minutes=ttl_min)), operation=operation,
                      nonce=uuid.uuid4().hex)
        if env.store.read(_path(root, area)) != before:  # changed between decision and write: decide again
            continue
        _write(env, root, lease)
        break
    else:
        raise LeaseHeld(area, None, f"'{area}' is contended; try again shortly")

    env.sleep(settle_s)  # settle: let the drive propagate competing writes (R4)
    after = _read(env, root, area)
    if after is None or after.nonce != lease.nonce:
        raise LeaseHeld(area, after, f"'{area}' was won by another session"
                        + (f": {after.describe()}" if after else "") + "; backing off")
    if log:
        _log(root, "lease acquire", area, actor, {"operation": operation, "session": me["session"]})
        if took_over:
            _log(root, "lease takeover", area, actor, {"operation": operation, "previous": took_over})
    return Acquired(lease, took_over)


def check_owner(gov: dict[str, Any], area: str, actor: str) -> None:
    """Owner mode (R7): only ``wiki_owners`` may acquire ``wiki``."""
    if area != "wiki" or gov.get("wiki_write_mode", "lease") != "owner":
        return
    owners = [str(o) for o in (gov.get("wiki_owners") or [])]
    if actor not in owners:
        raise EngineError(
            "not_wiki_owner",
            f"'{actor}' may not write the wiki: governance.wiki_write_mode is 'owner'. Owners: "
            f"{', '.join(owners) or '(none)'}. Prepare a proposal file in personal/ for an owner to review.",
            EXIT_PRECONDITION)


def verify(root: Path, lease: Lease, *, env: LeaseEnv | None = None) -> bool:
    env = _env(env)
    current = _read(env, Path(root), lease.area)
    return bool(current and current.nonce == lease.nonce and current.active(env.clock()))


def _require_current(env: LeaseEnv, root: Path, lease: Lease) -> Lease:
    current = _read(env, Path(root), lease.area)
    if current is None or current.nonce != lease.nonce:
        raise LeaseLost(lease.area, f"the '{lease.area}' lease is no longer yours"
                        + (f"; now held by {current.describe()}" if current and current.active(env.clock()) else ""))
    if current.released:
        raise LeaseLost(lease.area, f"the '{lease.area}' lease was already released")
    if current.expired(env.clock()):
        raise LeaseLost(lease.area, f"the '{lease.area}' lease expired at {current.expires_at}; acquire it again")
    return current


def renew(root: Path, lease: Lease, *, ttl: float | None = None, env: LeaseEnv | None = None) -> Lease:
    env = _env(env)
    current = _require_current(env, Path(root), lease)
    now = env.clock()
    if ttl is None:
        ttl_min = (parse_ts(current.expires_at) - parse_ts(current.renewed_at)).total_seconds() / 60.0
    else:
        ttl_min = ttl
    renewed = Lease(**{**current.__dict__, "renewed_at": fmt_ts(now),
                       "expires_at": fmt_ts(now + timedelta(minutes=ttl_min))})
    _write(env, Path(root), renewed)
    return renewed


def needs_renewal(lease: Lease, now: datetime) -> bool:
    """True once half of the TTL has elapsed since the last renewal (R5, 'renew at half-life')."""
    renewed = parse_ts(lease.renewed_at)
    half = (parse_ts(lease.expires_at) - renewed) / 2
    return now >= renewed + half


def release(root: Path, lease: Lease, *, env: LeaseEnv | None = None, log: bool = True) -> Lease:
    """Mark the lease released (R6). The file stays and is reused by the next acquire. Idempotent."""
    env = _env(env)
    current = _read(env, Path(root), lease.area)
    if current is not None and current.nonce == lease.nonce and current.released:
        return current
    current = _require_current(env, Path(root), lease)
    released = Lease(**{**current.__dict__, "released_at": fmt_ts(env.clock())})
    _write(env, Path(root), released)
    if log:
        _log(Path(root), "lease release", lease.area, lease.holder.get("actor", "unknown"),
             {"operation": lease.operation})
    return released


def status(root: Path, area: str, *, env: LeaseEnv | None = None) -> dict[str, Any]:
    """State of one area: ``free`` (never taken), ``held``, ``expired`` or ``released``. Never exposes the nonce."""
    _check_area(area)
    env = _env(env)
    current = _read(env, Path(root), area)
    out: dict[str, Any] = {"area": area, "path": lease_rel(area)}
    if current is None:
        out["state"] = "free"
        return out
    now = env.clock()
    out["state"] = "released" if current.released else ("expired" if current.expired(now) else "held")
    lease = current.to_dict()
    lease.pop("nonce", None)
    out["lease"] = lease
    out["available"] = out["state"] != "held"
    return out


def _nonce_of(lease_or_nonce: Lease | str | None) -> str | None:
    if isinstance(lease_or_nonce, Lease):
        return lease_or_nonce.nonce
    return lease_or_nonce


def require_lease(root: Path, area: str | tuple[str, ...], lease_or_nonce: "Lease | str | LeaseHandle | None",
                  *, env: LeaseEnv | None = None) -> Lease:
    """Gate for every writer of a shared file (R8, AC5): the supplied lease must be the current, unexpired
    holder of ``area`` (or of one of several acceptable areas). Raises ``lease_required`` / ``lease_lost``."""
    env = _env(env)
    if isinstance(lease_or_nonce, LeaseHandle):
        env = lease_or_nonce.env
        lease_or_nonce = lease_or_nonce.lease
    nonce = _nonce_of(lease_or_nonce)
    areas = (area,) if isinstance(area, str) else tuple(area)
    if not nonce:
        raise EngineError("lease_required",
                          f"this write needs the '{areas[0]}' lease: run `lease acquire {areas[0]}` first and "
                          "pass --lease <nonce>", EXIT_PRECONDITION)
    for a in areas:
        _check_area(a)
        current = _read(env, Path(root), a)
        if current is not None and current.nonce == nonce:
            return _require_current(env, Path(root), current)
    raise LeaseLost(areas[0], f"the supplied lease does not match the current '{areas[0]}' lease")


@dataclass
class LeaseHandle:
    """A lease held by this process. Callable: calling it releases (the shape ``migrate.LEASE_HOOK`` expects)."""

    root: Path
    lease: Lease
    env: LeaseEnv
    took_over: dict[str, Any] | None = None

    def ensure(self) -> Lease:
        """Re-verify before a batch of writes (R4). Renews when past half-life."""
        current = require_lease(self.root, self.lease.area, self.lease, env=self.env)
        if needs_renewal(current, self.env.clock()):
            current = renew(self.root, current, env=self.env)
        self.lease = current
        return current

    def release(self) -> None:
        release(self.root, self.lease, env=self.env)

    __call__ = release


def attach(root: Path, area: str, nonce: str, *, env: LeaseEnv | None = None) -> LeaseHandle:
    """Handle for a lease acquired earlier (``--lease <nonce>``), verified now."""
    env = _env(env)
    current = _require_current_by_nonce(env, Path(root), area, nonce)
    return LeaseHandle(Path(root), current, env)


def _require_current_by_nonce(env: LeaseEnv, root: Path, area: str, nonce: str) -> Lease:
    _check_area(area)
    current = _read(env, root, area)
    if current is None or current.nonce != nonce:
        raise LeaseLost(area, f"the supplied lease does not match the current '{area}' lease")
    return _require_current(env, root, current)


@contextmanager
def held(root: Path, area: str, actor: str, operation: str, **kwargs: Any) -> Iterator[LeaseHandle]:
    """Acquire ``area`` for the duration of the block; always release (also on error)."""
    env = _env(kwargs.get("env"))
    kwargs["env"] = env
    result = acquire(root, area, actor, operation, **kwargs)
    handle = LeaseHandle(Path(root), result.lease, env, result.took_over)
    try:
        yield handle
    finally:
        try:
            handle.release()
        except LeaseLost:
            pass  # taken over meanwhile; nothing of ours to release


def config_lease_hook(project: Any) -> LeaseHandle:
    """``migrate.LEASE_HOOK`` implementation: take the ``config`` lease; calling the result releases it."""
    from .events import resolve_actor

    return acquire_handle(project.root, "config", resolve_actor(None), "migrate")


def acquire_handle(root: Path, area: str, actor: str, operation: str, **kwargs: Any) -> LeaseHandle:
    env = _env(kwargs.pop("env", None))
    result = acquire(root, area, actor, operation, env=env, **kwargs)
    return LeaseHandle(Path(root), result.lease, env, result.took_over)


def update_hot_file(root: Path, handle: "LeaseHandle | Lease | str", rel: str,
                    transform: Callable[[bytes | None], bytes]) -> None:
    """Hot-file discipline (R8): re-verify the lease, read the file, apply ``transform`` and replace it
    atomically in one step. ``rel`` must be a hot file whose area the lease covers."""
    rel = rel.replace("\\", "/")
    areas = HOT_FILES.get(rel)
    if areas is None:
        raise EngineError("not_a_hot_file", f"{rel} is not a hot file (hot files: {', '.join(HOT_FILES)})", 2)
    require_lease(root, areas, handle)
    target = Path(root) / rel
    try:
        with open(fs_path(target), "rb") as fh:
            existing: bytes | None = fh.read()
    except FileNotFoundError:
        existing = None
    atomic_write_bytes(target, transform(existing))
