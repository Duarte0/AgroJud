"""Deterministic, structured signal rules and local evaluation engine."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from agrojud.domain.canonical_json import JSONValue

SignalEnvironment = Literal["demo", "real"]
SignalOutcome = Literal["matched", "no_match", "not_evaluated"]


class UnknownSignalRuleError(ValueError):
    """A request refers to a rule absent from the versioned local catalog."""


class InactiveSignalRuleError(ValueError):
    """A known rule is not enabled for the selected environment."""


@dataclass(frozen=True, slots=True)
class RuleEnablement:
    enabled: bool
    state: str
    reason: str
    evidence_ids: tuple[str, ...]

    def snapshot(self) -> dict[str, JSONValue]:
        return {
            "enabled": self.enabled,
            "state": self.state,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class SignalRule:
    id: str
    version: str
    category: str
    name: str
    values: tuple[int, ...]
    explanation: str
    enablement: Mapping[SignalEnvironment, RuleEnablement]

    def snapshot(self) -> dict[str, JSONValue]:
        return {
            "id": self.id,
            "version": self.version,
            "category": self.category,
            "name": self.name,
            "predicate": {
                "scope": "movement",
                "field": "codigo",
                "operator": "equals_any",
                "values": list(self.values),
            },
            "explanation": self.explanation,
            "enablement": {
                environment: evidence.snapshot()
                for environment, evidence in self.enablement.items()
            },
        }


@dataclass(frozen=True, slots=True)
class SignalMatch:
    evidence_id: UUID
    code: int


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    outcome: SignalOutcome
    diagnostic: str | None
    matches: tuple[SignalMatch, ...]


def load_signal_rules() -> tuple[SignalRule, ...]:
    """Load and validate the static, versioned rule catalog."""

    path = Path(__file__).with_name("signal_rules.v1.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("A versão do catálogo local de regras é inválida.")
    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("O catálogo local precisa conter regras.")

    rules: list[SignalRule] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ValueError("Uma regra do catálogo precisa ser um objeto.")
        rule_id = _required_text(raw, "id")
        version = _required_text(raw, "version")
        if (rule_id, version) in seen:
            raise ValueError("O catálogo contém id/versão de regra duplicado.")
        seen.add((rule_id, version))
        predicate = raw.get("predicate")
        if not isinstance(predicate, dict) or predicate != {
            "scope": "movement",
            "field": "codigo",
            "operator": "equals_any",
            "values": predicate.get("values") if isinstance(predicate, dict) else None,
        }:
            raise ValueError(f"A regra {rule_id!r} usa um predicado não permitido.")
        raw_values = predicate.get("values")
        if not isinstance(raw_values, list) or not raw_values:
            raise ValueError(f"A regra {rule_id!r} não tem códigos estruturados.")
        values: list[int] = []
        for value in raw_values:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"A regra {rule_id!r} contém um código inválido.")
            values.append(value)

        raw_enablement = raw.get("enablement")
        if not isinstance(raw_enablement, dict):
            raise ValueError(f"A regra {rule_id!r} não registra habilitação por ambiente.")
        enablement: dict[SignalEnvironment, RuleEnablement] = {}
        for environment in ("demo", "real"):
            evidence = raw_enablement.get(environment)
            if not isinstance(evidence, dict) or not isinstance(evidence.get("enabled"), bool):
                raise ValueError(f"A regra {rule_id!r} não registra {environment}.")
            raw_ids = evidence.get("evidence_ids")
            if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
                raise ValueError(f"A evidência da regra {rule_id!r} é inválida.")
            enablement[environment] = RuleEnablement(
                enabled=evidence["enabled"],
                state=_required_text(evidence, "state"),
                reason=_required_text(evidence, "reason"),
                evidence_ids=tuple(raw_ids),
            )

        rules.append(
            SignalRule(
                id=rule_id,
                version=version,
                category=_required_text(raw, "category"),
                name=_required_text(raw, "name"),
                values=tuple(values),
                explanation=_required_text(raw, "explanation"),
                enablement=enablement,
            )
        )
    return tuple(rules)


def select_signal_rules(
    environment: SignalEnvironment,
    selected_ids: Sequence[str] | None = None,
) -> tuple[SignalRule, ...]:
    """Return enabled rules, distinguishing unknown IDs from environment gates."""

    by_id = {rule.id: rule for rule in SIGNAL_RULES}
    ids = tuple(selected_ids) if selected_ids is not None else tuple(by_id)
    selected: list[SignalRule] = []
    for rule_id in ids:
        rule = by_id.get(rule_id)
        if rule is None:
            raise UnknownSignalRuleError(f"A regra {rule_id!r} não existe.")
        if not rule.enablement[environment].enabled:
            raise InactiveSignalRuleError(
                f"A regra {rule_id!r} está inativa no ambiente {environment}: "
                f"{rule.enablement[environment].reason}"
            )
        selected.append(rule)
    if not selected:
        raise UnknownSignalRuleError("Selecione ao menos uma regra conhecida.")
    return tuple(selected)


def rule_from_snapshot(value: Mapping[str, Any]) -> SignalRule:
    """Rebuild one validated rule from the immutable enqueue snapshot."""

    predicate = value.get("predicate")
    if not isinstance(predicate, Mapping) or predicate.get("scope") != "movement":
        raise ValueError("O snapshot da regra contém predicado não suportado.")
    if predicate.get("field") != "codigo" or predicate.get("operator") != "equals_any":
        raise ValueError("O snapshot da regra contém predicado não suportado.")
    raw_values = predicate.get("values")
    if not isinstance(raw_values, list) or not raw_values:
        raise ValueError("O snapshot da regra não contém códigos estruturados.")
    values: list[int] = []
    for code in raw_values:
        if isinstance(code, bool) or not isinstance(code, int) or code <= 0:
            raise ValueError("O snapshot da regra contém código inválido.")
        values.append(code)

    raw_enablement = value.get("enablement")
    if not isinstance(raw_enablement, Mapping):
        raise ValueError("O snapshot da regra não contém evidência de habilitação.")
    enablement: dict[SignalEnvironment, RuleEnablement] = {}
    for environment in ("demo", "real"):
        raw_evidence = raw_enablement.get(environment)
        if not isinstance(raw_evidence, Mapping) or not isinstance(
            raw_evidence.get("enabled"), bool
        ):
            raise ValueError("O snapshot da regra não contém habilitação por ambiente.")
        raw_ids = raw_evidence.get("evidence_ids")
        if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
            raise ValueError("O snapshot da regra contém evidência inválida.")
        enablement[environment] = RuleEnablement(
            enabled=raw_evidence["enabled"],
            state=_required_text(raw_evidence, "state"),
            reason=_required_text(raw_evidence, "reason"),
            evidence_ids=tuple(raw_ids),
        )

    return SignalRule(
        id=_required_text(value, "id"),
        version=_required_text(value, "version"),
        category=_required_text(value, "category"),
        name=_required_text(value, "name"),
        values=tuple(values),
        explanation=_required_text(value, "explanation"),
        enablement=enablement,
    )


def evaluate_rule(
    rule: SignalRule,
    movements: Sequence[tuple[UUID, Mapping[str, Any]]],
    *,
    input_complete: bool,
) -> RuleEvaluation:
    """Evaluate exact structured movement codes; missing data is never a negative."""

    if not input_complete:
        return RuleEvaluation("not_evaluated", "A entrada normalizada está incompleta.", ())

    decoded: list[tuple[UUID, int]] = []
    for evidence_id, movement in movements:
        code_value = movement.get("codigo")
        code = _structured_code(code_value)
        if code is None:
            return RuleEvaluation(
                "not_evaluated",
                "Ao menos um movimento não possui código TPU numérico estruturado.",
                (),
            )
        decoded.append((evidence_id, code))

    matches = tuple(
        SignalMatch(evidence_id=evidence_id, code=code)
        for evidence_id, code in decoded
        if code in rule.values
    )
    return RuleEvaluation("matched" if matches else "no_match", None, matches)


def _structured_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"O campo {key!r} de uma regra precisa ser texto.")
    return result


SIGNAL_RULES = load_signal_rules()
