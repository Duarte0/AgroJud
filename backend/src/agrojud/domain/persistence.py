"""Integrity errors surfaced by local source-cover persistence."""


class SourceIdentityConflict(ValueError):
    """A stable source identity was presented for a different CNJ process."""


class ObservationPositionConflict(ValueError):
    """A collection page position was replayed with different content."""


class PayloadHashCollisionError(ValueError):
    """One canonical JSON hash was claimed by incompatible payload content."""


class CollectionNotFoundError(LookupError):
    """A page was submitted for a collection that is not persisted."""
