import json

from app.core.schemas import Citation
from app.query.service import QueryService


def test_reciprocal_rank_fusion_deduplicates_and_fuses():
    a = {"evidence_id": "a", "content": "A"}
    b = {"evidence_id": "b", "content": "B"}
    result = QueryService._reciprocal_rank_fusion([a, b], [b])
    assert [item["evidence_id"] for item in result] == ["b", "a"]


def test_answer_parser_rejects_malformed_or_insufficient_json():
    assert QueryService._parse_answer("not-json") == {}
    assert QueryService._parse_answer(json.dumps({"insufficient_evidence": True})) == {}
    assert QueryService._parse_answer('```json\n{"answer":"ok","citations":[]}\n```')["answer"] == "ok"


def test_citations_show_each_document_page_once_and_prefer_image():
    citations = [
        Citation(document_id="doc-1", filename="report.pdf", page_number=21,
                 evidence_id="text-1", evidence_type="text"),
        Citation(document_id="doc-1", filename="report.pdf", page_number=21,
                 evidence_id="text-2", evidence_type="text"),
        Citation(document_id="doc-1", filename="report.pdf", page_number=21,
                 evidence_id="image-1", evidence_type="image"),
        Citation(document_id="doc-1", filename="report.pdf", page_number=22,
                 evidence_id="text-3", evidence_type="text"),
    ]
    unique = QueryService._deduplicate_citations(citations)
    assert [(item.page_number, item.evidence_type) for item in unique] == [
        (21, "image"), (22, "text"),
    ]


def test_dispatch_is_not_duplicated_as_query_service_heuristics():
    assert not hasattr(QueryService, "_requires_document_evidence")
    assert not hasattr(QueryService, "_has_relevant_retrieval")
    assert not hasattr(QueryService, "_is_contextual_follow_up")
