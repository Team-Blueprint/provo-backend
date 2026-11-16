import pytest

from app.core.enums import DayStatus, JobStatus
from app.domain.lifecycle import (
    DayEvent,
    InvalidStateTransitionError,
    JobEvent,
    transition_day_state,
    transition_job_state,
)


def test_job_valid_lifecycle_transitions():
    # DRAFT -> AWAITING_FUNDING -> FUNDED -> IN_PROGRESS -> COMPLETED
    s1 = transition_job_state(JobStatus.DRAFT, JobEvent.REQUEST_FUNDING)
    assert s1 == JobStatus.AWAITING_FUNDING

    s2 = transition_job_state(s1, JobEvent.PAYMENT_RECEIVED)
    assert s2 == JobStatus.FUNDED

    s3 = transition_job_state(s2, JobEvent.START_WORK)
    assert s3 == JobStatus.IN_PROGRESS

    s4 = transition_job_state(s3, JobEvent.ALL_DAYS_SETTLED)
    assert s4 == JobStatus.COMPLETED


def test_job_cancellation_and_dispute():
    # Can cancel in draft or funded
    s_cancel = transition_job_state(JobStatus.DRAFT, JobEvent.CANCEL)
    assert s_cancel == JobStatus.CANCELLED

    # Dispute from in_progress
    s_dispute = transition_job_state(JobStatus.IN_PROGRESS, JobEvent.DISPUTE_RAISED)
    assert s_dispute == JobStatus.DISPUTED


def test_job_invalid_transitions_raise():
    # Cannot jump DRAFT directly to COMPLETED or START_WORK
    with pytest.raises(InvalidStateTransitionError):
        transition_job_state(JobStatus.DRAFT, JobEvent.START_WORK)

    with pytest.raises(InvalidStateTransitionError):
        transition_job_state(JobStatus.COMPLETED, JobEvent.START_WORK)


def test_day_valid_lifecycle_confirmed_path():
    # PENDING -> DONE_REPORTED -> CONFIRMED -> PAID
    d1 = transition_day_state(DayStatus.PENDING, DayEvent.WORKER_DONE)
    assert d1 == DayStatus.DONE_REPORTED

    d2 = transition_day_state(d1, DayEvent.CLIENT_CONFIRM)
    assert d2 == DayStatus.CONFIRMED

    d3 = transition_day_state(d2, DayEvent.PAYOUT_SUCCESS)
    assert d3 == DayStatus.PAID


def test_day_valid_lifecycle_auto_released_path():
    # PENDING -> DONE_REPORTED -> AUTO_RELEASED -> PAID
    d1 = transition_day_state(DayStatus.PENDING, DayEvent.WORKER_DONE)
    assert d1 == DayStatus.DONE_REPORTED

    d2 = transition_day_state(d1, DayEvent.TIMER_EXPIRED)
    assert d2 == DayStatus.AUTO_RELEASED

    d3 = transition_day_state(d2, DayEvent.PAYOUT_SUCCESS)
    assert d3 == DayStatus.PAID


def test_day_dispute_and_invalid_transitions():
    # PENDING -> DONE_REPORTED -> DISPUTED
    d1 = transition_day_state(DayStatus.PENDING, DayEvent.WORKER_DONE)
    d_disp = transition_day_state(d1, DayEvent.CLIENT_DISPUTE)
    assert d_disp == DayStatus.DISPUTED

    # Disputed day cannot transition to PAID
    with pytest.raises(InvalidStateTransitionError):
        transition_day_state(d_disp, DayEvent.PAYOUT_SUCCESS)
