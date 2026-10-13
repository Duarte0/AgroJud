"""SPEC-027 search definitions compile only validated, structured TPU criteria."""

from __future__ import annotations

import pytest

from agrojud.sources.catalog import (
    compile_custom_search,
    compile_preset,
    list_custom_search_codes,
    list_official_themes,
)
from agrojud.sources.contracts import SourceError, build_datajud_payload


def test_official_themes_use_only_the_approved_current_catalog_codes() -> None:
    themes = {item.id: item for item in list_official_themes()}
    assert set(themes) == {
        "tema.credito_contratos_rurais",
        "tema.recuperacao_judicial",
        "tema.execucoes_relacionadas_ao_agro",
    }

    credit = compile_preset("tema.credito_contratos_rurais", environment="demo")
    assert credit.query.class_codes == ()
    assert credit.query.subject_codes == (4964, 4976, 10501)

    recovery = compile_preset("tema.recuperacao_judicial", environment="demo")
    assert recovery.query.class_codes == (129,)
    assert recovery.query.subject_codes == (13277,)
    assert recovery.query.dimension_combination == "OR"

    executions = compile_preset("tema.execucoes_relacionadas_ao_agro", environment="demo")
    assert set(executions.query.class_codes) == {155, 156, 1116, 159, 12078, 12079, 12154}
    assert executions.query.subject_codes == (4964, 4976, 10501)
    assert executions.query.dimension_combination == "AND"
    assert "rural" in themes["tema.execucoes_relacionadas_ao_agro"].justification.casefold()


def test_custom_search_picker_contains_only_active_catalog_approved_codes() -> None:
    classes = {item.code for item in list_custom_search_codes("class")}
    subjects = {item.code for item in list_custom_search_codes("subject")}
    assert classes == {129, 159, 155, 156, 1116, 12078, 12079, 12154}
    assert subjects == {4964, 4976, 10501, 13277}
    assert not ({108, 4993, 4968} & classes | {108, 4993, 4968} & subjects)


def test_recovery_theme_compiles_class_or_subject_as_one_allowlisted_query() -> None:
    compiled = compile_preset("tema.recuperacao_judicial", environment="demo")
    filters = build_datajud_payload(compiled.query, None, 100)["query"]["bool"]["filter"]
    dimension_clause = next(item for item in filters if isinstance(item, dict) and "bool" in item)
    assert dimension_clause == {
        "bool": {
            "should": [
                {"terms": {"classe.codigo": [129]}},
                {"terms": {"assuntos.codigo": [13277]}},
            ],
            "minimum_should_match": 1,
        }
    }


def test_agro_execution_query_requires_execution_class_and_rural_subject() -> None:
    compiled = compile_preset("tema.execucoes_relacionadas_ao_agro", environment="demo")
    filters = build_datajud_payload(compiled.query, None, 100)["query"]["bool"]["filter"]
    assert {"terms": {"classe.codigo": list(compiled.query.class_codes)}} in filters
    assert {"terms": {"assuntos.codigo": [4964, 4976, 10501]}} in filters
    assert all(not (isinstance(item, dict) and "bool" in item) for item in filters)


@pytest.mark.parametrize("codes", [(108,), (4993,), (4968,), (999999,)])
def test_custom_search_rejects_legacy_inactive_unknown_or_unapproved_subject_codes(
    codes: tuple[int, ...],
) -> None:
    with pytest.raises(SourceError):
        compile_custom_search(
            custom_search_id="test",
            version=1,
            name="Busca",
            description=None,
            class_codes=(),
            subject_codes=codes,
            dimension_combination="OR",
            environment="demo",
        )


def test_custom_search_compiles_and_and_or_without_accepting_query_dsl() -> None:
    common = {
        "custom_search_id": "test",
        "version": 1,
        "name": "Busca estruturada",
        "description": None,
        "class_codes": [129],
        "subject_codes": [13277],
        "environment": "demo",
    }
    and_query = compile_custom_search(**common, dimension_combination="AND").query
    or_query = compile_custom_search(**common, dimension_combination="OR").query
    and_filters = build_datajud_payload(and_query, None, 100)["query"]["bool"]["filter"]
    or_filters = build_datajud_payload(or_query, None, 100)["query"]["bool"]["filter"]
    assert {"terms": {"classe.codigo": [129]}} in and_filters
    assert {"terms": {"assuntos.codigo": [13277]}} in and_filters
    assert any(isinstance(item, dict) and "bool" in item for item in or_filters)
