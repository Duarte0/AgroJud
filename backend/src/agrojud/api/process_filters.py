"""Shared process-list filters for process, overview, and news reads."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from fastapi import Query
from sqlalchemy import and_, exists, false, or_, select
from sqlalchemy.sql.elements import ColumnElement

from agrojud.db.models import (
    CollectionResult,
    Job,
    Process,
    ProcessNews,
    ProcessSignal,
    ProcessTriage,
    ProcessWatchlistEntry,
    Representation,
    RepresentationSubject,
)
from agrojud.domain.signals import SIGNAL_RULES
from agrojud.sources.contracts import build_query_by_case_number

SignalEnvironment = Literal["demo", "real"]
TriageDecision = Literal["pending", "relevant", "discarded"]
RuralLink = Literal["unconfirmed", "confirmed"]


@dataclass(frozen=True, slots=True)
class ProcessFilters:
    process_number: str | None = None
    subject: str | None = None
    subject_code: str | None = None
    subject_name_exact: str | None = None
    class_filter: str | None = None
    court_unit: str | None = None
    collection_id: UUID | None = None
    preset_id: str | None = None
    decision: TriageDecision | None = None
    rural_link: RuralLink | None = None
    followed: bool | None = None
    pending_news: bool | None = None
    signal_category: str | None = None


def resolve_process_filters(filters: ProcessFilters) -> ProcessFilters:
    """Normalize user-facing values once so counts, links, and lists share a cut."""

    text_fields = (
        "subject",
        "subject_code",
        "subject_name_exact",
        "class_filter",
        "court_unit",
        "preset_id",
        "signal_category",
    )
    resolved = {
        field: value.strip() if (value := getattr(filters, field)) else None
        for field in text_fields
    }
    process_number = (
        build_query_by_case_number(filters.process_number).process_number
        if filters.process_number is not None
        else None
    )
    return replace(filters, process_number=process_number, **resolved)


def process_filters_dependency(
    process_number: str | None = Query(default=None, min_length=1, max_length=30),
    subject: str | None = Query(default=None, min_length=1, max_length=160),
    subject_code: str | None = Query(default=None, min_length=1, max_length=80),
    subject_name_exact: str | None = Query(default=None, min_length=1, max_length=160),
    class_filter: str | None = Query(default=None, alias="class", min_length=1, max_length=160),
    court_unit: str | None = Query(default=None, min_length=1, max_length=160),
    collection_id: UUID | None = None,
    preset_id: str | None = Query(default=None, min_length=1, max_length=120),
    decision: TriageDecision | None = None,
    rural_link: RuralLink | None = None,
    followed: bool | None = None,
    pending_news: bool | None = None,
    signal_category: str | None = Query(default=None, min_length=1, max_length=80),
) -> ProcessFilters:
    """Expose the same query contract to every read that slices by process."""

    return resolve_process_filters(
        ProcessFilters(
            process_number=process_number,
            subject=subject,
            subject_code=subject_code,
            subject_name_exact=subject_name_exact,
            class_filter=class_filter,
            court_unit=court_unit,
            collection_id=collection_id,
            preset_id=preset_id,
            decision=decision,
            rural_link=rural_link,
            followed=followed,
            pending_news=pending_news,
            signal_category=signal_category,
        )
    )


def process_predicates(
    filters: ProcessFilters,
    *,
    environment: SignalEnvironment,
) -> list[ColumnElement[bool]]:
    """Build correlated predicates without joining child rows into process counts."""

    predicates: list[ColumnElement[bool]] = []
    if filters.process_number is not None:
        predicates.append(Process.numero_cnj == filters.process_number)

    if filters.decision is not None:
        matching = exists(
            select(ProcessTriage.process_id).where(
                ProcessTriage.process_id == Process.id,
                ProcessTriage.decision == filters.decision,
            )
        )
        if filters.decision == "pending":
            predicates.append(
                or_(
                    matching,
                    ~exists(
                        select(ProcessTriage.process_id).where(
                            ProcessTriage.process_id == Process.id
                        )
                    ),
                )
            )
        else:
            predicates.append(matching)

    if filters.rural_link is not None:
        matching = exists(
            select(ProcessTriage.process_id).where(
                ProcessTriage.process_id == Process.id,
                ProcessTriage.rural_link == filters.rural_link,
            )
        )
        if filters.rural_link == "unconfirmed":
            predicates.append(
                or_(
                    matching,
                    ~exists(
                        select(ProcessTriage.process_id).where(
                            ProcessTriage.process_id == Process.id
                        )
                    ),
                )
            )
        else:
            predicates.append(matching)

    representation_filters: list[ColumnElement[bool]] = []
    if filters.subject is not None:
        subject_pattern = _contains_pattern(filters.subject)
        representation_filters.append(
            exists(
                select(RepresentationSubject.id).where(
                    RepresentationSubject.representation_id == Representation.id,
                    or_(
                        RepresentationSubject.subject_name.ilike(subject_pattern, escape="\\"),
                        RepresentationSubject.subject_code.ilike(subject_pattern, escape="\\"),
                    ),
                )
            )
        )
    if filters.subject_code is not None or filters.subject_name_exact is not None:
        exact_subject = [RepresentationSubject.representation_id == Representation.id]
        if filters.subject_code is not None:
            exact_subject.append(RepresentationSubject.subject_code == filters.subject_code)
        if filters.subject_name_exact is not None:
            exact_subject.append(RepresentationSubject.subject_name == filters.subject_name_exact)
        representation_filters.append(
            exists(select(RepresentationSubject.id).where(*exact_subject))
        )
    if filters.class_filter is not None:
        class_pattern = _contains_pattern(filters.class_filter)
        representation_filters.append(
            or_(
                Representation.class_name.ilike(class_pattern, escape="\\"),
                Representation.class_code.ilike(class_pattern, escape="\\"),
            )
        )
    if filters.court_unit is not None:
        court_pattern = _contains_pattern(filters.court_unit)
        representation_filters.append(
            or_(
                Representation.court_unit_name.ilike(court_pattern, escape="\\"),
                Representation.court_unit_code.ilike(court_pattern, escape="\\"),
            )
        )
    if filters.collection_id is not None or filters.preset_id is not None:
        collection_filters: list[ColumnElement[bool]] = [
            CollectionResult.representation_id == Representation.id
        ]
        if filters.collection_id is not None:
            collection_filters.append(CollectionResult.collection_id == filters.collection_id)
        if filters.preset_id is not None:
            collection_filters.append(
                Job.parameters_snapshot["query"]["preset_id"].astext == filters.preset_id
            )
        representation_filters.append(
            exists(
                select(CollectionResult.id)
                .join(Job, Job.collection_id == CollectionResult.collection_id)
                .where(*collection_filters)
            )
        )
    if representation_filters:
        predicates.append(
            exists(
                select(Representation.id).where(
                    Representation.process_id == Process.id,
                    *representation_filters,
                )
            )
        )

    if filters.followed is not None:
        active_watch = exists(
            select(ProcessWatchlistEntry.process_id).where(
                ProcessWatchlistEntry.process_id == Process.id,
                ProcessWatchlistEntry.active.is_(True),
            )
        )
        predicates.append(active_watch if filters.followed else ~active_watch)

    if filters.pending_news is not None:
        has_pending_news = exists(
            select(ProcessNews.id).where(
                ProcessNews.process_id == Process.id,
                ProcessNews.status == "pending",
            )
        )
        predicates.append(has_pending_news if filters.pending_news else ~has_pending_news)

    if filters.signal_category is not None:
        predicates.append(_current_signal_exists(environment, filters.signal_category))

    return predicates


def current_signal_rule_predicate(
    environment: SignalEnvironment,
) -> ColumnElement[bool]:
    """Match only published, current signals from rules enabled at their current version."""

    active_rules = [rule for rule in SIGNAL_RULES if rule.enablement[environment].enabled]
    if not active_rules:
        return false()
    return and_(
        ProcessSignal.is_published.is_(True),
        ProcessSignal.is_current.is_(True),
        ProcessSignal.environment == environment,
        or_(
            *(
                and_(
                    ProcessSignal.rule_id == rule.id,
                    ProcessSignal.rule_version == rule.version,
                )
                for rule in active_rules
            )
        ),
    )


def current_signal_category_predicate(
    environment: SignalEnvironment,
) -> ColumnElement[bool]:
    """Match the active version of each enabled rule for category distributions."""

    active_rules = [rule for rule in SIGNAL_RULES if rule.enablement[environment].enabled]
    if not active_rules:
        return false()
    return and_(
        current_signal_rule_predicate(environment),
        or_(
            *(
                and_(
                    ProcessSignal.rule_id == rule.id,
                    ProcessSignal.rule_version == rule.version,
                )
                for rule in active_rules
            )
        ),
    )


def _current_signal_exists(
    environment: SignalEnvironment,
    category: str,
) -> ColumnElement[bool]:
    rules = [
        rule
        for rule in SIGNAL_RULES
        if rule.category == category and rule.enablement[environment].enabled
    ]
    if not rules:
        return false()
    return exists(
        select(ProcessSignal.id).where(
            ProcessSignal.process_id == Process.id,
            current_signal_rule_predicate(environment),
            ProcessSignal.category == category,
            or_(
                *(
                    and_(
                        ProcessSignal.rule_id == rule.id,
                        ProcessSignal.rule_version == rule.version,
                    )
                    for rule in rules
                )
            ),
        )
    )


def _contains_pattern(value: str) -> str:
    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
