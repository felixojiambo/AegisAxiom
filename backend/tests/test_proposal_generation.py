"""Locks the deterministic proposal-content generator.

``services/proposal_generation.py`` is a tools-compute helper called
from the route layer. The tests cover the three guarantees that matter
for the V1 workflow:

  1. Output keys match the contract the route relies on.
  2. The output is deterministic — same input → same hash → no spurious
     regenerate refresh.
  3. The helper never mutates its inputs (route calls it with SOW dicts
     pulled from Mongo).

Synthetic fixtures only — no real customer / tenant / financial data.
"""
from __future__ import annotations

import copy

import pytest


def _opp(**overrides):
    base = {
        "opportunity_id": "opp_synth_gen_001",
        "name": "Synth Opp",
        "account_name": "Synth Account",
    }
    base.update(overrides)
    return base


def _sow(**overrides):
    base = {
        "sow_id": "sow_synth_gen_pro_svcs",
        "opportunity_id": "opp_synth_gen_001",
        "line_of_business": "professional_services",
        "status": "ready_for_review",
        "executive_summary": "Stand up the synthetic platform.",
        "smart_specific": "Deliver platform skeleton",
        "smart_measurable": "Three milestones",
        "smart_achievable": "Within current team capacity",
        "smart_relevant": "Aligned to Q3 plan",
        "smart_time_bound": "8 weeks",
        "contract_type": "fixed_fee",
        "billing_terms": "monthly",
        "payment_terms": "net_30",
        "start_date": "2099-01-01",
        "end_date": "2099-03-01",
        "total_amount": 12345.67,
        "milestones": [
            {
                "milestone_id": "m1",
                "milestone_name": "Discovery",
                "order": 1,
                "bullets": [{"text": "Stakeholder interviews"}],
            },
            {
                "milestone_id": "m2",
                "milestone_name": "Build",
                "order": 2,
                "bullets": [{"text": "Stand up infra"}, {"text": "Wire integrations"}],
            },
        ],
    }
    base.update(overrides)
    return base


def test_generate_proposal_content_returns_required_keys():
    from services.proposal_generation import generate_proposal_content

    result = generate_proposal_content(_opp(), [_sow()])
    expected_keys = {
        "title",
        "generated_summary",
        "generated_content",
        "generation_source",
        "generation_source_hash",
        "artifact_type",
    }
    assert expected_keys.issubset(result.keys())
    assert result["generation_source"] == "sow"
    assert result["artifact_type"] == "inline_content"
    assert result["title"].startswith("Synth Opp")
    # SOW content makes it into the Markdown body.
    assert "Stand up the synthetic platform." in result["generated_content"]
    assert "Discovery" in result["generated_content"]


def test_generate_proposal_content_is_deterministic():
    """Same inputs must produce byte-identical content + hash so the
    regenerate path can detect a no-op refresh."""
    from services.proposal_generation import generate_proposal_content

    a = generate_proposal_content(_opp(), [_sow()])
    b = generate_proposal_content(_opp(), [_sow()])
    assert a == b


def test_generate_proposal_content_hash_changes_when_sow_changes():
    from services.proposal_generation import generate_proposal_content

    baseline = generate_proposal_content(_opp(), [_sow()])
    edited = generate_proposal_content(
        _opp(), [_sow(executive_summary="Pivoted scope")]
    )
    assert baseline["generation_source_hash"] != edited["generation_source_hash"]
    assert "Pivoted scope" in edited["generated_content"]


def test_generate_proposal_content_does_not_mutate_inputs():
    from services.proposal_generation import generate_proposal_content

    opp = _opp()
    sows = [_sow()]
    snapshot = copy.deepcopy((opp, sows))
    generate_proposal_content(opp, sows)
    assert (opp, sows) == snapshot, "generator must not mutate its inputs"


def test_generate_proposal_content_handles_multiple_sows_sorted_by_lob():
    """Multi-SOW proposals get sections in stable (alphabetical by LOB)
    order so two consecutive renders produce identical output even if
    the caller's list is in different order."""
    from services.proposal_generation import generate_proposal_content

    fs = _sow(sow_id="sow_synth_gen_fs", line_of_business="field_services")
    ps = _sow(sow_id="sow_synth_gen_ps", line_of_business="professional_services")
    a = generate_proposal_content(_opp(), [fs, ps])
    b = generate_proposal_content(_opp(), [ps, fs])
    assert a["generation_source_hash"] == b["generation_source_hash"]
    assert "Field Services" in a["generated_content"]
    assert "Professional Services" in a["generated_content"]


def test_generate_proposal_content_rejects_empty_sows():
    from services.proposal_generation import generate_proposal_content

    with pytest.raises(ValueError):
        generate_proposal_content(_opp(), [])


def test_content_signature_is_order_invariant_for_identical_milestones():
    """J6. Two milestone lists with identical content in different
    scan order must hash the same. The render path sorts by the order
    field at format time and the signature path must use the same key
    so a Mongo scan reordering does not flip the digest and trigger
    a spurious regenerate.
    """
    from services.proposal_generation import generate_proposal_content

    forward = _sow(milestones=[
        {"milestone_id": "m1", "milestone_name": "Discovery", "order": 1,
         "bullets": [{"text": "Stakeholder interviews"}]},
        {"milestone_id": "m2", "milestone_name": "Build", "order": 2,
         "bullets": [{"text": "Stand up infra"}]},
    ])
    reversed_order = _sow(milestones=[
        {"milestone_id": "m2", "milestone_name": "Build", "order": 2,
         "bullets": [{"text": "Stand up infra"}]},
        {"milestone_id": "m1", "milestone_name": "Discovery", "order": 1,
         "bullets": [{"text": "Stakeholder interviews"}]},
    ])
    a = generate_proposal_content(_opp(), [forward])
    b = generate_proposal_content(_opp(), [reversed_order])
    assert a["generation_source_hash"] == b["generation_source_hash"]
    assert a["generated_content"] == b["generated_content"]


def test_content_signature_changes_when_milestone_content_changes():
    """J6. The signature still discriminates genuine content changes
    even after the order normalisation. A renamed milestone or a
    changed bullet must produce a different hash.
    """
    from services.proposal_generation import generate_proposal_content

    baseline = _sow()
    edited = _sow(milestones=[
        {"milestone_id": "m1", "milestone_name": "Discovery Rev",
         "order": 1, "bullets": [{"text": "Stakeholder interviews"}]},
        {"milestone_id": "m2", "milestone_name": "Build",
         "order": 2, "bullets": [{"text": "Stand up infra"},
                                 {"text": "Wire integrations"}]},
    ])
    a = generate_proposal_content(_opp(), [baseline])
    b = generate_proposal_content(_opp(), [edited])
    assert a["generation_source_hash"] != b["generation_source_hash"]
