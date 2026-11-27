from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import model_validator

from vue_rag.domain.base import ContractModel, NonBlankText, RequiredMultilineText


class KnowledgeKind(StrEnum):
    OFFICIAL_DOCUMENTATION = "official_documentation"
    CURATED_FAQ = "curated_faq"
    ROUTING_EXAMPLE = "routing_example"
    QUARANTINE = "quarantine"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_VERIFIED = "not_verified"


Route = Literal[
    "rag_answer", "support_action", "kira_decision", "kira_and_support", "safety_review"
]
Risk = Literal["low", "medium", "high"]


class Provenance(ContractModel):
    document_id: NonBlankText
    version_id: NonBlankText
    chunk_id: NonBlankText
    quote: RequiredMultilineText
    source_dump: NonBlankText


class CurationCandidate(ContractModel):
    """Unpublished case record; provenance is required before promotion."""

    faq_id: NonBlankText
    question: RequiredMultilineText
    original_answer: RequiredMultilineText
    route: Route
    risk: Risk
    scope: NonBlankText
    source_case_ids: tuple[NonBlankText, ...] = ()
    answerability: str = ""
    knowledge_kind: KnowledgeKind = KnowledgeKind.QUARANTINE
    review_status: ReviewStatus = ReviewStatus.PENDING
    reusable_as_policy: bool = False


class CurationRecord(ContractModel):
    faq_id: NonBlankText
    question: RequiredMultilineText
    approved_answer: RequiredMultilineText
    knowledge_kind: KnowledgeKind
    review_status: ReviewStatus
    reusable_as_policy: bool
    route: Route
    risk: Risk
    scope: NonBlankText
    provenance: tuple[Provenance, ...]
    source_case_ids: tuple[NonBlankText, ...] = ()
    approved_by: str = ""
    approved_at: datetime | None = None
    golden_overlap_checked: bool = False
    pii_redacted: bool = False

    @model_validator(mode="after")
    def validate_publication_gate(self) -> CurationRecord:
        if not self.provenance:
            raise ValueError("at least one provenance record is required")
        if self.knowledge_kind == KnowledgeKind.CURATED_FAQ:
            if not self.reusable_as_policy or self.route != "rag_answer":
                raise ValueError("curated FAQ must be reusable policy for rag_answer")
            if self.review_status == ReviewStatus.APPROVED and (
                not self.approved_by or self.approved_at is None
            ):
                raise ValueError("approved FAQ requires reviewer and timestamp")
        if self.knowledge_kind in {
            KnowledgeKind.ROUTING_EXAMPLE,
            KnowledgeKind.QUARANTINE,
        }:
            if self.reusable_as_policy:
                raise ValueError("routing and quarantine records cannot be policy")
        if self.review_status == ReviewStatus.APPROVED and not self.pii_redacted:
            raise ValueError("approved records must be PII redacted")
        return self
