"""Unit tests for change detection domain enums, status transitions, and invariants."""

import pytest
from apps.spatial.domain import (
    BuildingAssociationType,
    ChangeCandidateStatus,
    ChangeCategory,
    ChangeProjectStatus,
    ChangeRunStatus,
    ReviewDecision,
)


def test_change_detection_enums_integrity():
    """Verify completeness and stability of M4.4 change detection enums."""
    # ChangeCategory
    assert ChangeCategory.BUILDING_APPEARANCE_CANDIDATE == "BUILDING_APPEARANCE_CANDIDATE"
    assert ChangeCategory.BUILDING_REMOVAL_CANDIDATE == "BUILDING_REMOVAL_CANDIDATE"
    assert ChangeCategory.CONSTRUCTION_ACTIVITY_CANDIDATE == "CONSTRUCTION_ACTIVITY_CANDIDATE"
    assert ChangeCategory.VEGETATION_GAIN == "VEGETATION_GAIN"
    assert ChangeCategory.VEGETATION_LOSS == "VEGETATION_LOSS"
    assert ChangeCategory.WATER_BODY_CHANGE == "WATER_BODY_CHANGE"
    assert ChangeCategory.BARE_SOIL_CHANGE == "BARE_SOIL_CHANGE"
    assert ChangeCategory.LAND_COVER_CHANGE == "LAND_COVER_CHANGE"
    assert ChangeCategory.ROAD_OR_INFRASTRUCTURE_CANDIDATE == "ROAD_OR_INFRASTRUCTURE_CANDIDATE"
    assert ChangeCategory.UNKNOWN_CHANGE == "UNKNOWN_CHANGE"
    assert len(ChangeCategory.ALL) == 10

    # ChangeCandidateStatus
    assert ChangeCandidateStatus.OBSERVED == "OBSERVED"
    assert ChangeCandidateStatus.POTENTIAL == "POTENTIAL"
    assert ChangeCandidateStatus.MODEL_INFERRED == "MODEL_INFERRED"
    assert ChangeCandidateStatus.HUMAN_REVIEWED == "HUMAN_REVIEWED"
    assert ChangeCandidateStatus.VERIFIED == "VERIFIED"
    assert ChangeCandidateStatus.REJECTED == "REJECTED"
    assert ChangeCandidateStatus.SUPERSEDED == "SUPERSEDED"
    assert len(ChangeCandidateStatus.ALL) == 7

    # ChangeProjectStatus
    assert ChangeProjectStatus.ACTIVE == "ACTIVE"
    assert ChangeProjectStatus.ARCHIVED == "ARCHIVED"
    assert ChangeProjectStatus.COMPLETED == "COMPLETED"
    assert len(ChangeProjectStatus.ALL) == 3

    # ChangeRunStatus
    assert ChangeRunStatus.PENDING == "PENDING"
    assert ChangeRunStatus.VALIDATING == "VALIDATING"
    assert ChangeRunStatus.PROCESSING == "PROCESSING"
    assert ChangeRunStatus.COMPLETED == "COMPLETED"
    assert ChangeRunStatus.FAILED == "FAILED"
    assert ChangeRunStatus.CANCELLED == "CANCELLED"
    assert len(ChangeRunStatus.ALL) == 6

    # ReviewDecision
    assert ReviewDecision.CONFIRM_OBSERVATION == "CONFIRM_OBSERVATION"
    assert ReviewDecision.CONFIRM_FIELD_VERIFICATION == "CONFIRM_FIELD_VERIFICATION"
    assert ReviewDecision.REJECT_CANDIDATE == "REJECT_CANDIDATE"
    assert ReviewDecision.MARK_INCONCLUSIVE == "MARK_INCONCLUSIVE"
    assert ReviewDecision.REQUEST_FIELD_INSPECTION == "REQUEST_FIELD_INSPECTION"
    assert len(ReviewDecision.ALL) == 5

    # BuildingAssociationType
    assert BuildingAssociationType.OVERLAPS_EXISTING == "OVERLAPS_EXISTING"
    assert BuildingAssociationType.ADJACENT_EXISTING == "ADJACENT_EXISTING"
    assert BuildingAssociationType.ISOLATED_UNMAPPED == "ISOLATED_UNMAPPED"
    assert BuildingAssociationType.PREVIOUSLY_OBSERVED == "PREVIOUSLY_OBSERVED"
    assert BuildingAssociationType.UNASSOCIATED == "UNASSOCIATED"
    assert len(BuildingAssociationType.ALL) == 5


def test_review_decision_validations():
    """Test decision validation rules."""
    valid_decisions = [
        ReviewDecision.CONFIRM_OBSERVATION,
        ReviewDecision.CONFIRM_FIELD_VERIFICATION,
        ReviewDecision.REJECT_CANDIDATE,
        ReviewDecision.MARK_INCONCLUSIVE,
        ReviewDecision.REQUEST_FIELD_INSPECTION,
    ]
    for d in valid_decisions:
        assert d in ReviewDecision.ALL

    invalid_decision = "AUTO_APPROVE_WITHOUT_REVIEW"
    assert invalid_decision not in ReviewDecision.ALL
