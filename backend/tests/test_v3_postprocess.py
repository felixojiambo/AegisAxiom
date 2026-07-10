"""Unit tests for Vega v3 Stage 4 post-processing.

Tests for:
  - detect_shared_infrastructure()
  - aggregate_open_items()
  - extraction_status derivation (via _stage_4_postprocess)

Run with: docker compose exec backend pytest tests/test_v3_postprocess.py -q

All tests are pure Python — no DB, no LLM, no async.  We construct Pydantic
models directly and call the deterministic helpers.
"""

from __future__ import annotations

import pytest

from services.vega.flags import VegaFlag
from services.vega.models_v3 import (
    AcceptanceCriterion,
    App,
    AppSkeleton,
    IntegrationEntry,
    MMIDimension,
    MMIHypothesis,
    NFRs,
    OpenItem,
    PersonaRow,
    ScaleProfile,
    SessionFlags,
    SharedInfrastructureRow,
    SourceProvenance,
    Workflow,
    WorkflowSkeleton,
)
from services.vega.postprocess import (
    aggregate_open_items,
    detect_shared_infrastructure,
)
from services.vega.v3_pipeline import _stage_4_postprocess


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _source() -> SourceProvenance:
    """Minimal valid SourceProvenance."""
    return SourceProvenance()


def _mmi() -> MMIHypothesis:
    """Minimal valid MMIHypothesis (all dims value=1, low confidence)."""
    dim = MMIDimension(value=1, confidence="low", reasoning="test")
    return MMIHypothesis(
        tss=dim, ie=dim, time=dim, cost=dim, quality=dim, satisfaction=dim,
        needs_client_input=[], score=None,
    )


def _make_workflow(
    wf_id: str = "wf_test",
    partial: bool = False,
    partial_reason: str | None = None,
    acceptance_criteria: list[AcceptanceCriterion] | None = None,
    persona_overrides: list[PersonaRow] | None = None,
) -> Workflow:
    return Workflow(
        workflow_id=wf_id,
        workflow_name=f"Workflow {wf_id}",
        source=_source(),
        mmi_hypothesis=_mmi(),
        partial_extraction_failed=partial,
        partial_extraction_reason=partial_reason,
        acceptance_criteria=acceptance_criteria or [],
        persona_overrides=persona_overrides or [],
    )


def _make_app(
    app_id: str = "app_test",
    integrations: list[IntegrationEntry] | None = None,
    personas: list[PersonaRow] | None = None,
    nfrs: NFRs | None = None,
    scale_profile: ScaleProfile | None = None,
    partial: bool = False,
    partial_reason: str | None = None,
) -> App:
    return App(
        app_id=app_id,
        app_name=f"App {app_id}",
        source=_source(),
        integrations=integrations or [],
        personas=personas or [],
        nfrs=nfrs or NFRs(),
        scale_profile=scale_profile or ScaleProfile(),
        partial_extraction_failed=partial,
        partial_extraction_reason=partial_reason,
    )


def _integration(
    system_name: str,
    bucket: int = 2,
    notes: str | None = None,
    flags: list[VegaFlag] | None = None,
) -> IntegrationEntry:
    return IntegrationEntry(
        system_name=system_name,
        bucket=bucket,
        notes=notes,
        flags=flags or [],
    )


# ===========================================================================
# detect_shared_infrastructure
# ===========================================================================


class TestDetectSharedInfrastructure:

    # ── edge cases ──────────────────────────────────────────────────────────

    def test_no_apps_returns_empty(self):
        result = detect_shared_infrastructure([], [])
        assert result == []

    def test_no_workflows_no_apps_returns_empty(self):
        result = detect_shared_infrastructure([], [])
        assert result == []

    def test_one_app_five_integrations_no_sharing(self):
        """One app — no sharing is possible regardless of integration count."""
        app = _make_app("app_1", integrations=[
            _integration("QuickBooks Online"),
            _integration("Salesforce"),
            _integration("Slack"),
            _integration("Zendesk"),
            _integration("HubSpot"),
        ])
        result = detect_shared_infrastructure([], [app])
        assert result == []

    # ── shared detection ────────────────────────────────────────────────────

    def test_two_apps_sharing_one_integration(self):
        """Two apps sharing exactly one system → 1 row with both app IDs sorted."""
        app_a = _make_app("app_a", integrations=[_integration("NetSuite")])
        app_b = _make_app("app_b", integrations=[_integration("NetSuite")])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert len(result) == 1
        row = result[0]
        assert row.system_name == "NetSuite"
        assert row.used_by_app_ids == ["app_a", "app_b"]

    def test_two_apps_sharing_one_app_ids_sorted(self):
        """used_by_app_ids are always sorted for stable output."""
        app_z = _make_app("app_z", integrations=[_integration("Salesforce")])
        app_a = _make_app("app_a", integrations=[_integration("Salesforce")])
        result = detect_shared_infrastructure([], [app_z, app_a])
        assert len(result) == 1
        assert result[0].used_by_app_ids == ["app_a", "app_z"]

    def test_three_apps_only_one_pair_shares(self):
        """3 apps where only 1 pair shares an integration → 1 row with 2 app_ids."""
        app_a = _make_app("app_a", integrations=[_integration("NetSuite"), _integration("Stripe")])
        app_b = _make_app("app_b", integrations=[_integration("NetSuite"), _integration("SendGrid")])
        app_c = _make_app("app_c", integrations=[_integration("Twilio")])
        result = detect_shared_infrastructure([], [app_a, app_b, app_c])
        assert len(result) == 1
        assert result[0].system_name == "NetSuite"
        assert result[0].used_by_app_ids == ["app_a", "app_b"]

    def test_three_apps_all_share(self):
        """3 apps all sharing the same integration → 1 row with 3 app_ids."""
        apps = [
            _make_app("app_1", integrations=[_integration("PostgreSQL")]),
            _make_app("app_2", integrations=[_integration("PostgreSQL")]),
            _make_app("app_3", integrations=[_integration("PostgreSQL")]),
        ]
        result = detect_shared_infrastructure([], apps)
        assert len(result) == 1
        assert result[0].used_by_app_ids == ["app_1", "app_2", "app_3"]

    # ── case-insensitive grouping ────────────────────────────────────────────

    def test_case_insensitive_grouping_preserves_first_occurrence_casing(self):
        """'QuickBooks Online' and 'quickbooks online' → 1 row, name from first app."""
        app_a = _make_app("app_first", integrations=[_integration("QuickBooks Online")])
        app_b = _make_app("app_second", integrations=[_integration("quickbooks online")])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert len(result) == 1
        # Canonical name is from the first occurrence
        assert result[0].system_name == "QuickBooks Online"
        assert result[0].used_by_app_ids == ["app_first", "app_second"]

    def test_case_insensitive_mixed_whitespace(self):
        """'  Salesforce  ' normalises to match 'salesforce'."""
        app_a = _make_app("app_a", integrations=[_integration("  Salesforce  ")])
        app_b = _make_app("app_b", integrations=[_integration("salesforce")])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert len(result) == 1

    # ── partial extraction failure ────────────────────────────────────────────

    def test_partial_app_skipped_without_crash(self):
        """An app with partial_extraction_failed=True is silently skipped."""
        partial_app = _make_app("app_partial", partial=True)
        good_app_1 = _make_app("app_good_1", integrations=[_integration("Stripe")])
        good_app_2 = _make_app("app_good_2", integrations=[_integration("Stripe")])
        result = detect_shared_infrastructure([], [partial_app, good_app_1, good_app_2])
        assert len(result) == 1
        assert result[0].system_name == "Stripe"
        assert "app_partial" not in result[0].used_by_app_ids

    def test_all_partial_apps_returns_empty(self):
        """All apps failed → no integrations to scan → empty list."""
        apps = [_make_app(f"app_{i}", partial=True) for i in range(3)]
        result = detect_shared_infrastructure([], apps)
        assert result == []

    # ── notes deduplication ──────────────────────────────────────────────────

    def test_notes_deduplication_preserves_order(self):
        """Identical notes from different apps appear only once in the combined string."""
        app_a = _make_app("app_a", integrations=[
            _integration("NetSuite", notes="read-only export"),
        ])
        app_b = _make_app("app_b", integrations=[
            _integration("NetSuite", notes="read-only export"),
        ])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert len(result) == 1
        # "read-only export" should appear only once
        assert result[0].notes.count("read-only export") == 1

    def test_distinct_notes_combined(self):
        """Notes from different apps are joined with ' | '."""
        app_a = _make_app("app_a", integrations=[
            _integration("NetSuite", notes="billing data"),
        ])
        app_b = _make_app("app_b", integrations=[
            _integration("NetSuite", notes="inventory data"),
        ])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert len(result) == 1
        assert "billing data" in result[0].notes
        assert "inventory data" in result[0].notes

    def test_no_notes_gives_none(self):
        """When no integration entries have notes, the row's notes field is None."""
        app_a = _make_app("app_a", integrations=[_integration("Stripe", notes=None)])
        app_b = _make_app("app_b", integrations=[_integration("Stripe", notes=None)])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert len(result) == 1
        assert result[0].notes is None

    # ── read/write inference ──────────────────────────────────────────────────

    def test_read_write_inference_read(self):
        app_a = _make_app("app_a", integrations=[_integration("DB", notes="reads customer records")])
        app_b = _make_app("app_b", integrations=[_integration("DB", notes="fetch invoices")])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert result[0].read_write == "read"

    def test_read_write_inference_write(self):
        app_a = _make_app("app_a", integrations=[_integration("DB", notes="writes orders")])
        app_b = _make_app("app_b", integrations=[_integration("DB", notes="push updates")])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert result[0].read_write == "write"

    def test_read_write_inference_both(self):
        app_a = _make_app("app_a", integrations=[_integration("DB", notes="reads and writes")])
        app_b = _make_app("app_b", integrations=[_integration("DB")])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert result[0].read_write == "read_write"

    def test_read_write_unknown_when_no_hints(self):
        app_a = _make_app("app_a", integrations=[_integration("DB", notes="legacy system")])
        app_b = _make_app("app_b", integrations=[_integration("DB", notes="ERP integration")])
        result = detect_shared_infrastructure([], [app_a, app_b])
        assert result[0].read_write == "unknown"

    # ── output stability ─────────────────────────────────────────────────────

    def test_output_sorted_by_system_name(self):
        """Rows are sorted by system_name for deterministic ordering."""
        app_a = _make_app("app_a", integrations=[
            _integration("Zendesk"), _integration("Braintree"),
        ])
        app_b = _make_app("app_b", integrations=[
            _integration("Zendesk"), _integration("Braintree"),
        ])
        result = detect_shared_infrastructure([], [app_a, app_b])
        names = [r.system_name for r in result]
        assert names == sorted(names, key=str.lower)


# ===========================================================================
# aggregate_open_items
# ===========================================================================


class TestAggregateOpenItems:

    # ── empty input ──────────────────────────────────────────────────────────

    def test_empty_everything_returns_empty(self):
        result = aggregate_open_items([], [], SessionFlags())
        assert result == []

    def test_no_flags_set_returns_empty(self):
        """Workflows and apps with no flags set → no open items."""
        wf = _make_workflow("wf_1")
        app = _make_app("app_1")
        result = aggregate_open_items([wf], [app], SessionFlags())
        assert result == []

    # ── session flags ────────────────────────────────────────────────────────

    def test_tech_contact_flag_missing_produces_session_item(self):
        flags = SessionFlags(tech_contact_flag=VegaFlag.MISSING)
        result = aggregate_open_items([], [], flags)
        assert len(result) == 1
        item = result[0]
        assert item.flag == VegaFlag.MISSING
        assert item.location == "session"
        assert "tech contact" in item.label.lower()

    def test_engineering_capacity_flag_produces_session_item(self):
        flags = SessionFlags(engineering_capacity_flag=VegaFlag.MISSING)
        result = aggregate_open_items([], [], flags)
        assert len(result) == 1
        assert result[0].location == "session"
        assert result[0].flag == VegaFlag.MISSING

    def test_it_infosec_gate_flag_produces_session_item(self):
        flags = SessionFlags(it_infosec_gate_flag=VegaFlag.NOT_DISCUSSED)
        result = aggregate_open_items([], [], flags)
        assert len(result) == 1
        assert result[0].location == "session"
        assert result[0].flag == VegaFlag.NOT_DISCUSSED

    def test_all_three_session_flags_set(self):
        flags = SessionFlags(
            tech_contact_flag=VegaFlag.MISSING,
            engineering_capacity_flag=VegaFlag.MISSING,
            it_infosec_gate_flag=VegaFlag.NOT_DISCUSSED,
        )
        result = aggregate_open_items([], [], flags)
        assert len(result) == 3
        assert all(item.location == "session" for item in result)

    # ── workflow acceptance criteria ─────────────────────────────────────────

    def test_workflow_missing_acceptance_criterion(self):
        wf = _make_workflow(
            wf_id="wf_invoice",
            acceptance_criteria=[
                AcceptanceCriterion(
                    flow_name="invoice intake",
                    success_when=None,
                    flag=VegaFlag.MISSING,
                ),
            ],
        )
        result = aggregate_open_items([wf], [], SessionFlags())
        assert len(result) == 1
        item = result[0]
        assert item.flag == VegaFlag.MISSING
        assert item.location == "workflow:wf_invoice"
        assert "invoice intake" in item.label

    def test_workflow_inferred_acceptance_criterion(self):
        wf = _make_workflow(
            wf_id="wf_onboarding",
            acceptance_criteria=[
                AcceptanceCriterion(
                    flow_name="employee onboarding",
                    success_when="Employee completes setup",
                    flag=VegaFlag.INFERRED_CONFIRM,
                ),
            ],
        )
        result = aggregate_open_items([wf], [], SessionFlags())
        assert len(result) == 1
        assert result[0].flag == VegaFlag.INFERRED_CONFIRM
        assert result[0].location == "workflow:wf_onboarding"

    def test_workflow_unflagged_criterion_produces_no_item(self):
        """An AcceptanceCriterion with flag=None emits no OpenItem."""
        wf = _make_workflow(
            wf_id="wf_ok",
            acceptance_criteria=[
                AcceptanceCriterion(
                    flow_name="payment processing",
                    success_when="Payment authorised within 5s",
                    flag=None,
                ),
            ],
        )
        result = aggregate_open_items([wf], [], SessionFlags())
        assert result == []

    # ── workflow partial failure ─────────────────────────────────────────────

    def test_workflow_partial_extraction_failed_produces_missing_item(self):
        wf = _make_workflow(
            wf_id="wf_failed",
            partial=True,
            partial_reason="Stage 2a timed out after 180s + 1 retry",
        )
        result = aggregate_open_items([wf], [], SessionFlags())
        assert len(result) == 1
        item = result[0]
        assert item.flag == VegaFlag.MISSING
        assert item.location == "workflow:wf_failed"
        assert "extraction failed" in item.label.lower()
        assert "180s" in item.detail

    def test_workflow_partial_without_reason_still_produces_item(self):
        wf = _make_workflow(wf_id="wf_nreason", partial=True)
        result = aggregate_open_items([wf], [], SessionFlags())
        assert len(result) == 1
        assert result[0].flag == VegaFlag.MISSING

    # ── workflow persona overrides ────────────────────────────────────────────

    def test_workflow_persona_override_flags(self):
        persona = PersonaRow(
            role="field technician",
            flags=[VegaFlag.INFERRED_CONFIRM],
        )
        wf = _make_workflow(wf_id="wf_persona", persona_overrides=[persona])
        result = aggregate_open_items([wf], [], SessionFlags())
        assert len(result) == 1
        assert result[0].flag == VegaFlag.INFERRED_CONFIRM
        assert result[0].location == "workflow:wf_persona"
        assert "field technician" in result[0].label

    # ── app flags ────────────────────────────────────────────────────────────

    def test_app_integration_flags(self):
        """Two flagged integrations → 2 open items on the app."""
        app = _make_app("app_crm", integrations=[
            _integration("Salesforce", bucket=3, flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]),
            _integration("HubSpot", bucket=3, flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]),
        ])
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 2
        assert all(item.location == "app:app_crm" for item in result)
        assert all(item.flag == VegaFlag.NEEDS_TECHNICAL_FOLLOWUP for item in result)

    def test_app_two_flagged_integrations_one_flagged_nfr(self):
        """2 flagged integrations + 1 flagged NFR → 3 open items."""
        app = _make_app(
            app_id="app_erp",
            integrations=[
                _integration("NetSuite", bucket=3, flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]),
                _integration("SAP", bucket=3, flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]),
            ],
            nfrs=NFRs(flags=[VegaFlag.NOT_STATED]),
        )
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 3
        assert all(item.location == "app:app_erp" for item in result)
        flags_found = {item.flag for item in result}
        assert VegaFlag.NEEDS_TECHNICAL_FOLLOWUP in flags_found
        assert VegaFlag.NOT_STATED in flags_found

    def test_app_persona_flags(self):
        personas = [
            PersonaRow(role="manager", flags=[VegaFlag.INFERRED_CONFIRM]),
            PersonaRow(role="operator", flags=[VegaFlag.MISSING]),
        ]
        app = _make_app("app_people", personas=personas)
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 2
        assert all(item.location == "app:app_people" for item in result)

    def test_app_nfr_flags_bag(self):
        """NFRs.flags is a list — multiple flags emit multiple items."""
        nfrs = NFRs(flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP, VegaFlag.NOT_STATED])
        app = _make_app("app_nfr", nfrs=nfrs)
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 2
        assert all(item.location == "app:app_nfr" for item in result)

    def test_app_scale_profile_flag(self):
        sp = ScaleProfile(flag=VegaFlag.MISSING)
        app = _make_app("app_scale", scale_profile=sp)
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 1
        assert result[0].flag == VegaFlag.MISSING
        assert result[0].location == "app:app_scale"
        assert "scale" in result[0].label.lower()

    def test_app_partial_extraction_failed_produces_missing_item(self):
        app = _make_app(
            "app_partial",
            partial=True,
            partial_reason="Stage 2b extraction failed — timeout",
        )
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 1
        item = result[0]
        assert item.flag == VegaFlag.MISSING
        assert item.location == "app:app_partial"
        assert "extraction failed" in item.label.lower()
        assert "timeout" in item.detail

    def test_app_partial_without_reason_still_produces_item(self):
        app = _make_app("app_nreason", partial=True)
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 1
        assert result[0].flag == VegaFlag.MISSING

    # ── sort order ───────────────────────────────────────────────────────────

    def test_blocking_flag_sorts_before_nonblocking(self):
        """MISSING (blocking) must appear before INFERRED_CONFIRM (non-blocking)."""
        app = _make_app("app_mixed", integrations=[
            _integration("Stripe", bucket=3, flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]),
        ])
        wf = _make_workflow(
            wf_id="wf_sorted",
            acceptance_criteria=[
                AcceptanceCriterion(
                    flow_name="onboarding",
                    success_when="Done",
                    flag=VegaFlag.INFERRED_CONFIRM,
                ),
            ],
        )
        session_flags = SessionFlags(tech_contact_flag=VegaFlag.MISSING)
        result = aggregate_open_items([wf], [app], session_flags)

        # Blocking flags: MISSING (tech_contact), NEEDS_TECHNICAL_FOLLOWUP (integration)
        # Non-blocking flags: INFERRED_CONFIRM (acceptance criterion)
        assert len(result) >= 3

        blocking_indices = [
            i for i, item in enumerate(result)
            if item.flag in (VegaFlag.MISSING, VegaFlag.NEEDS_TECHNICAL_FOLLOWUP)
        ]
        non_blocking_indices = [
            i for i, item in enumerate(result)
            if item.flag == VegaFlag.INFERRED_CONFIRM
        ]
        # Every blocking item must appear before every non-blocking item
        if blocking_indices and non_blocking_indices:
            assert max(blocking_indices) < min(non_blocking_indices)

    def test_within_blocking_sorted_by_location_then_label(self):
        """Within the same blocking tier, items sort by location then label."""
        session_flags = SessionFlags(
            tech_contact_flag=VegaFlag.MISSING,
            engineering_capacity_flag=VegaFlag.MISSING,
        )
        result = aggregate_open_items([], [], session_flags)
        # Both are blocking + both at "session" location — sort by label
        labels = [item.label for item in result]
        assert labels == sorted(labels)

    def test_sort_is_deterministic_across_calls(self):
        """Calling aggregate_open_items twice on the same input returns the same order."""
        app = _make_app("app_a", integrations=[
            _integration("Stripe", bucket=3, flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]),
        ])
        wf = _make_workflow(
            wf_id="wf_b",
            acceptance_criteria=[
                AcceptanceCriterion(flow_name="core flow", flag=VegaFlag.INFERRED_CONFIRM),
            ],
        )
        flags = SessionFlags(tech_contact_flag=VegaFlag.MISSING)
        result_1 = aggregate_open_items([wf], [app], flags)
        result_2 = aggregate_open_items([wf], [app], flags)
        assert [item.label for item in result_1] == [item.label for item in result_2]

    # ── multi-flag per entity ────────────────────────────────────────────────

    def test_one_integration_multiple_flags_emits_one_item_per_flag(self):
        """An integration carrying 2 flags emits 2 separate OpenItems."""
        app = _make_app("app_multi", integrations=[
            _integration("LegacyERP", flags=[
                VegaFlag.NEEDS_TECHNICAL_FOLLOWUP,
                VegaFlag.SOURCE_UNCLEAR,
            ]),
        ])
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 2
        flag_values = {item.flag for item in result}
        assert VegaFlag.NEEDS_TECHNICAL_FOLLOWUP in flag_values
        assert VegaFlag.SOURCE_UNCLEAR in flag_values

    def test_persona_multiple_flags_emits_one_item_per_flag(self):
        """A PersonaRow with 2 flags → 2 OpenItems."""
        persona = PersonaRow(
            role="admin",
            flags=[VegaFlag.INFERRED_CONFIRM, VegaFlag.MISSING],
        )
        app = _make_app("app_p", personas=[persona])
        result = aggregate_open_items([], [app], SessionFlags())
        assert len(result) == 2


# ===========================================================================
# _stage_4_postprocess shape — extraction_status itself is owned by the
# orchestrator now; smoke tests in test_v3_pipeline_smoke.py cover the
# "any partial → status=partial" contract end-to-end.
# ===========================================================================


class TestStage4Shape:

    def test_returns_two_tuple(self):
        result = _stage_4_postprocess(
            workflows=[], apps=[], session_flags=SessionFlags()
        )
        assert len(result) == 2
        shared_infra, open_items = result
        assert isinstance(shared_infra, list)
        assert isinstance(open_items, list)

    def test_does_not_inspect_partial_extraction_failed(self):
        """Stage 4 helper is a thin wrapper over the two pure functions —
        it must not consume partial_extraction_failed (the orchestrator does)."""
        wf = _make_workflow("wf_ok")
        wf_fail = _make_workflow("wf_fail", partial=True)
        # Same call shape, different inputs — both succeed and return lists.
        for inputs in (
            ([wf], []),
            ([wf, wf_fail], []),
            ([], [_make_app("a", partial=True)]),
        ):
            shared_infra, open_items = _stage_4_postprocess(
                workflows=inputs[0], apps=inputs[1], session_flags=SessionFlags()
            )
            assert isinstance(shared_infra, list)
            assert isinstance(open_items, list)
