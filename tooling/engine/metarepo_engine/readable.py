"""Is a file fully readable on this machine? (004/R12; threat model rows "streamed/placeholder" and
"cloud-native").

Sync clients list files they have not downloaded ("online-only", "dataless"). Hashing or extracting such a
file either blocks on a download or sees a stub, and a hash of a stub would become a wrong source id. So
the engine classifies a file before reading it and reports ``not-local`` with guidance instead.

Statuses: ``ok``, ``empty`` (zero bytes, readable), ``not-local``, ``cloud-native`` (a pointer such as a
``.gdoc`` that holds no content; ask for an exported copy).
"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import NamedTuple

from .fsops import fs_path
from .jsonout import EXIT_PRECONDITION, EngineError

OK, EMPTY, NOT_LOCAL, CLOUD_NATIVE = "ok", "empty", "not-local", "cloud-native"

# macOS: SF_DATALESS. Windows: RECALL_ON_DATA_ACCESS, RECALL_ON_OPEN, OFFLINE.
_SF_DATALESS = 0x40000000
_WIN_PLACEHOLDER_ATTRS = 0x00400000 | 0x00040000 | 0x00001000
# Windows ERROR_CLOUD_FILE_* range (cloud files filter errors).
_WIN_CLOUD_ERRORS = range(358, 401)
_NOT_LOCAL_ERRNOS = {errno.EIO, errno.ETIMEDOUT, errno.ENODATA, errno.ENETDOWN, errno.ENOTCONN,
                      getattr(errno, "EDEADLK", -1), getattr(errno, "EREMOTEIO", -1)}

# Pointer files of cloud-native documents: they carry an id, not the content.
CLOUD_NATIVE_EXTENSIONS = frozenset({".gdoc", ".gsheet", ".gslides", ".gdraw", ".gform", ".gsite", ".gtable",
                                     ".gjam", ".gmap", ".gscript", ".gnote"})

GUIDANCE_NOT_LOCAL = ("mark the folder as available offline in your sync client (for example 'Available "
                      "offline' / 'Always keep on this device'), wait for the download, then try again")
GUIDANCE_CLOUD_NATIVE = ("this is a cloud-only document pointer with no local content; export it (for example "
                         "to .docx, .xlsx or .pdf) and add the exported copy instead")


class Readability(NamedTuple):
    status: str
    reason: str

    @property
    def usable(self) -> bool:
        return self.status in (OK, EMPTY)


def classify(path: Path) -> Readability:
    """Cheap classification: metadata first, a single 4 KiB read only for the sparse heuristic."""
    path = Path(path)
    if path.suffix.lower() in CLOUD_NATIVE_EXTENSIONS:
        return Readability(CLOUD_NATIVE, f"{path.suffix.lower()} files are cloud-document pointers")
    try:
        st = os.stat(fs_path(path))
    except OSError as exc:
        return _from_oserror(exc)
    flags = getattr(st, "st_flags", 0)
    if flags & _SF_DATALESS:
        return Readability(NOT_LOCAL, "the file is dataless (content is only in the cloud)")
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & _WIN_PLACEHOLDER_ATTRS:
        return Readability(NOT_LOCAL, "the file is an online-only placeholder")
    if not stat.S_ISREG(st.st_mode):
        return Readability(OK, "not a regular file")
    if st.st_size == 0:
        return Readability(EMPTY, "zero bytes")
    blocks = getattr(st, "st_blocks", None)
    if blocks == 0:  # size without storage: a stub, or a sparse file of zeros
        try:
            with open(fs_path(path), "rb") as fh:
                head = fh.read(4096)
        except OSError as exc:
            return _from_oserror(exc)
        if not any(head):
            return Readability(NOT_LOCAL, "the file reports a size but occupies no storage (stub or sparse)")
    return Readability(OK, "readable")


def _from_oserror(exc: OSError) -> Readability:
    win = getattr(exc, "winerror", None)
    if exc.errno in _NOT_LOCAL_ERRNOS or (win is not None and win in _WIN_CLOUD_ERRORS):
        return Readability(NOT_LOCAL, f"the file could not be read ({exc.strerror or exc})")
    raise exc


def ensure_local(path: Path) -> Readability:
    """Raise ``not_local`` / ``cloud_native`` (exit 3) unless the file can be read now."""
    verdict = classify(path)
    if verdict.status == NOT_LOCAL:
        raise EngineError("not_local", f"{Path(path).name}: not-local: {verdict.reason}; {GUIDANCE_NOT_LOCAL}",
                          EXIT_PRECONDITION, Path(path).as_posix())
    if verdict.status == CLOUD_NATIVE:
        raise EngineError("cloud_native", f"{Path(path).name}: {verdict.reason}; {GUIDANCE_CLOUD_NATIVE}",
                          EXIT_PRECONDITION, Path(path).as_posix())
    return verdict
