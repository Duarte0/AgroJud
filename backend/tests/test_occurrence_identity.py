from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from agrojud.domain.canonical_json import JSONValue, sha256_json
from agrojud.domain.occurrence_identity import (
    NORMALIZER_VERSION,
    IdentityIntegrityError,
    NormalizedMovement,
    NormalizerVersionMismatch,
    OccurrenceHistory,
    OccurrenceIdentity,
    OccurrenceStatus,
    RepresentationRef,
    normalize_movement,
    reconcile_snapshot,
)

type FixtureMovement = dict[str, JSONValue]
type Fixture = dict[str, JSONValue]

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "occurrence_reconciliation.json"


def _fixtures() -> dict[str, Fixture]:
    parsed = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return cast(dict[str, Fixture], parsed)


def _section(name: str) -> Fixture:
    return _fixtures()[name]


def _movement(value: JSONValue) -> FixtureMovement:
    assert isinstance(value, dict)
    return cast(FixtureMovement, value)


def _movement_list(value: JSONValue) -> list[FixtureMovement]:
    assert isinstance(value, list)
    return [_movement(item) for item in value]


def _representation(value: JSONValue) -> RepresentationRef:
    assert isinstance(value, str)
    return RepresentationRef(value)


def test_reordering_movements_and_complements_does_not_create_occurrences() -> None:
    fixture = _section("reordered_snapshots")
    representation = _representation(fixture["representation"])
    first = _movement_list(fixture["first"])
    reordered = _movement_list(fixture["reordered"])

    first_result = reconcile_snapshot(representation, [normalize_movement(item) for item in first])
    second_result = reconcile_snapshot(
        representation,
        [normalize_movement(item) for item in reordered],
        first_result.history,
    )

    assert [item.status for item in first_result.observations] == [
        OccurrenceStatus.FIRST_OBSERVED,
        OccurrenceStatus.FIRST_OBSERVED,
    ]
    assert [item.status for item in second_result.observations] == [
        OccurrenceStatus.KNOWN,
        OccurrenceStatus.KNOWN,
    ]
    assert {item.identity for item in first_result.observations} == {
        item.identity for item in second_result.observations
    }
    first_content = next(item for item in first if item["codigo"] == "SYN-001")
    normalized_content = json.loads(normalize_movement(first_content).content_json)
    assert len(normalized_content["complementosTabelados"]) == 3
    assert sha256_json({"movimentos": first}) != sha256_json({"movimentos": reordered})
    assert first_result.observations[0].identity.content_sha256 in {
        item.identity.content_sha256 for item in second_result.observations
    }


def test_duplicate_multiplicity_is_stable_across_one_two_one_two_snapshots() -> None:
    fixture = _section("duplicates")
    representation = _representation(fixture["representation"])
    movement = _movement(fixture["movement"])
    multiplicities = cast(list[int], fixture["multiplicities"])
    history: OccurrenceHistory | None = None
    observed_ordinals: list[list[int]] = []
    observed_statuses: list[dict[int, OccurrenceStatus]] = []

    for multiplicity in multiplicities:
        result = reconcile_snapshot(
            representation,
            [normalize_movement(movement) for _ in range(multiplicity)],
            history,
        )
        observed_ordinals.append([item.identity.ordinal for item in result.observations])
        observed_statuses.append(
            {item.identity.ordinal: item.status for item in result.observations}
        )
        history = result.history

    assert observed_ordinals == [[1], [1, 2], [1, 2], [1, 2]]
    assert observed_statuses[0] == {1: OccurrenceStatus.FIRST_OBSERVED}
    assert observed_statuses[1] == {
        1: OccurrenceStatus.KNOWN,
        2: OccurrenceStatus.FIRST_OBSERVED,
    }
    assert observed_statuses[2] == {
        1: OccurrenceStatus.KNOWN,
        2: OccurrenceStatus.NOT_PRESENT_IN_SNAPSHOT,
    }
    assert observed_statuses[3] == {
        1: OccurrenceStatus.KNOWN,
        2: OccurrenceStatus.KNOWN,
    }
    assert history is not None
    assert len(history.occurrences) == 2


@pytest.mark.parametrize("changed_field", ["name_changed", "extra_changed"])
def test_descriptive_or_extra_change_is_observed_without_losing_old_identity(
    changed_field: str,
) -> None:
    fixture = _section("descriptive_changes")
    representation = _representation(fixture["representation"])
    original = normalize_movement(_movement(fixture["original"]))
    changed = normalize_movement(_movement(fixture[changed_field]))
    first = reconcile_snapshot(representation, [original])

    second = reconcile_snapshot(representation, [changed], first.history)

    changed_observation = next(
        item
        for item in second.observations
        if item.identity.content_sha256 == changed.content_sha256
    )
    old_observation = next(
        item
        for item in second.observations
        if item.identity.content_sha256 == original.content_sha256
    )
    assert changed_observation.status is OccurrenceStatus.ALTERATION_OBSERVED
    assert old_observation.status is OccurrenceStatus.NOT_PRESENT_IN_SNAPSHOT
    assert len(second.alterations) == 1
    assert second.alterations[0].previous == (old_observation.identity,)
    assert second.alterations[0].current == (changed_observation.identity,)
    assert second.alterations[0].ambiguous is False

    returned = reconcile_snapshot(representation, [original], second.history)
    assert (
        next(
            item
            for item in returned.observations
            if item.identity.content_sha256 == original.content_sha256
        ).status
        is OccurrenceStatus.KNOWN
    )
    assert returned.alterations == ()


def test_complement_change_is_not_matched_by_auxiliary_key() -> None:
    fixture = _section("descriptive_changes")
    representation = _representation(fixture["representation"])
    original = normalize_movement(_movement(fixture["original"]))
    changed = normalize_movement(_movement(fixture["complement_changed"]))

    result = reconcile_snapshot(
        representation,
        [changed],
        reconcile_snapshot(representation, [original]).history,
    )

    assert original.auxiliary_key != changed.auxiliary_key
    assert len(result.alterations) == 0
    changed_status = next(
        item.status
        for item in result.observations
        if item.identity.content_sha256 == changed.content_sha256
    )
    assert changed_status is OccurrenceStatus.FIRST_OBSERVED


def test_multiple_prior_matches_remain_an_ambiguous_alteration_group() -> None:
    fixture = _section("descriptive_changes")
    representation = _representation(fixture["representation"])
    original = normalize_movement(_movement(fixture["original"]))
    second_version = normalize_movement(_movement(fixture["name_changed"]))
    third_version = normalize_movement(_movement(fixture["ambiguous_changed"]))

    history = reconcile_snapshot(representation, [original]).history
    history = reconcile_snapshot(representation, [second_version], history).history
    result = reconcile_snapshot(representation, [third_version], history)

    assert len(result.alterations) == 1
    alteration = result.alterations[0]
    assert set(alteration.previous) == {
        OccurrenceIdentity(representation, original.content_sha256, 1),
        OccurrenceIdentity(representation, second_version.content_sha256, 1),
    }
    assert len(alteration.current) == 1
    assert alteration.ambiguous is True
    assert (
        next(
            item
            for item in result.observations
            if item.identity.content_sha256 == third_version.content_sha256
        ).status
        is OccurrenceStatus.ALTERATION_OBSERVED
    )


def test_same_case_reference_in_distinct_degrees_and_courts_stays_separate() -> None:
    fixture = _section("distinct_representations")
    references = cast(list[str], fixture["representations"])
    common_movement: FixtureMovement = {
        "codigo": "SYN-SAME",
        "dataHora": "2026-10-05T12:00:00-03:00",
        "nome": "Movimento sintético compartilhado",
        "orgaoJulgador": {"codigo": "ORG-SYN", "nome": "Órgão sintético"},
        "complementosTabelados": [],
    }
    movement = normalize_movement(common_movement)
    identities: set[OccurrenceIdentity] = set()

    for reference in references:
        representation = RepresentationRef(reference)
        result = reconcile_snapshot(representation, [movement])
        assert result.observations[0].status is OccurrenceStatus.FIRST_OBSERVED
        identities.add(result.observations[0].identity)

    assert len(references) == 4
    assert len(identities) == 4


def test_history_from_another_representation_is_rejected() -> None:
    movement = normalize_movement({"codigo": "SYN-X", "dataHora": "20261005120000"})
    first_representation = RepresentationRef("fixture:degree-1:organ-a")
    second_representation = RepresentationRef("fixture:degree-2:organ-b")
    history = reconcile_snapshot(first_representation, [movement]).history

    with pytest.raises(ValueError, match="outra representação"):
        reconcile_snapshot(second_representation, [movement], history)


def test_naive_timestamp_keeps_original_for_comparison_without_assumed_timezone() -> None:
    with_seconds = normalize_movement({"codigo": "SYN-TIME", "dataHora": "2026-10-06T09:30:00"})
    without_seconds = normalize_movement({"codigo": "SYN-TIME", "dataHora": "2026-10-06T09:30"})
    content = json.loads(with_seconds.content_json)

    assert content["dataHoraStatus"] == "timezone_ambiguous"
    assert content["dataHoraNormalizada"] == "2026-10-06T09:30:00.000000"
    assert "Z" not in content["dataHoraNormalizada"]
    assert with_seconds.auxiliary_key != without_seconds.auxiliary_key


def test_aware_timestamp_preserves_original_and_normalizes_to_utc() -> None:
    movement = normalize_movement({"codigo": "SYN-TIME", "dataHora": "2026-10-06T09:30:00-03:00"})
    content = json.loads(movement.content_json)

    assert content["dataHora"] == "2026-10-06T09:30:00-03:00"
    assert content["dataHoraNormalizada"] == "2026-10-06T12:30:00.000000Z"
    assert content["dataHoraStatus"] == "timezone_aware"


def test_absent_code_or_timestamp_keeps_content_but_has_no_auxiliary_match_key() -> None:
    missing_fields = normalize_movement({"nome": "Movimento sintético sem chave"})
    null_fields = normalize_movement({"codigo": None, "dataHora": None, "nome": None})

    assert missing_fields.auxiliary_key is None
    assert null_fields.auxiliary_key is None
    assert missing_fields.content_sha256 != null_fields.content_sha256
    assert sha256_json({"movimentos": [{"nome": "Movimento sintético sem chave"}]})


def test_normalizer_version_is_explicit_on_every_result() -> None:
    movement: NormalizedMovement = normalize_movement({"codigo": "SYN-V", "dataHora": None})
    assert movement.algorithm_version == NORMALIZER_VERSION


def test_comparing_algorithm_versions_requires_explicit_reprocessing() -> None:
    representation = RepresentationRef("fixture:normalizer-version")
    movement = normalize_movement({"codigo": "SYN-V", "dataHora": None})
    history = reconcile_snapshot(representation, [movement]).history
    next_version = NormalizedMovement(
        algorithm_version="movement-normalizer-v2",
        content_sha256=movement.content_sha256,
        canonical_content=movement.canonical_content,
        auxiliary_key=movement.auxiliary_key,
    )

    with pytest.raises(NormalizerVersionMismatch, match="reprocessamento explícito"):
        reconcile_snapshot(representation, [next_version], history)


def test_movement_hash_must_match_its_canonical_content() -> None:
    movement = normalize_movement({"codigo": "SYN-HASH", "dataHora": None})
    forged = NormalizedMovement(
        algorithm_version=movement.algorithm_version,
        content_sha256="0" * 64,
        canonical_content=movement.canonical_content,
        auxiliary_key=movement.auxiliary_key,
    )

    with pytest.raises(ValueError, match="não corresponde ao hash registrado"):
        reconcile_snapshot(RepresentationRef("fixture:invalid-hash"), [forged])


def test_content_hash_collision_with_different_content_is_an_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agrojud.domain.occurrence_identity as identity_module

    class ConstantDigest:
        def hexdigest(self) -> str:
            return "0" * 64

    monkeypatch.setattr(identity_module.hashlib, "sha256", lambda _value: ConstantDigest())
    first = normalize_movement({"codigo": "SYN-A", "dataHora": None})
    second = normalize_movement({"codigo": "SYN-B", "dataHora": None})

    with pytest.raises(IdentityIntegrityError, match="Colisão de hash"):
        reconcile_snapshot(RepresentationRef("fixture:collision"), [first, second])
