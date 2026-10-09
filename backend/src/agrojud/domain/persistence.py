"""Integrity errors surfaced by local source-cover persistence."""


class SourceIdentityConflict(ValueError):
    """A stable source identity was presented for a different CNJ process."""


class ObservationPositionConflict(ValueError):
    """A collection page position was replayed with different content."""


class PayloadHashCollisionError(ValueError):
    """One canonical JSON hash was claimed by incompatible payload content."""


class CollectionNotFoundError(LookupError):
    """A page was submitted for a collection that is not persisted."""


class QuarantinePositionConflict(ValueError):
    """A quarantined page position was replayed with different raw content."""


class QuarantineRejectionNotFoundError(LookupError):
    """An explicitly requested quarantine identifier does not exist."""


class MovementHashCollisionError(ValueError):
    """One normalized movement fingerprint was claimed by incompatible content."""
