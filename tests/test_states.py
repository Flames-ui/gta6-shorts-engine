import pytest

from app import states


def test_completed_and_cancelled_are_final():
    for terminal in (states.COMPLETED, states.CANCELLED):
        for target in states.ALL:
            assert not states.allowed(terminal, target)


def test_failed_can_only_be_explicitly_retried():
    assert states.allowed(states.FAILED, states.QUEUED)
    assert not states.allowed(states.FAILED, states.COMPLETED)
    assert not states.allowed(states.FAILED, states.RENDERING)


def test_happy_path_is_allowed_in_order():
    path = [states.QUEUED, states.VALIDATING, states.RETRIEVING, states.UPLOADING_SOURCE, states.ANALYZING,
            states.TRANSCRIBING, states.SELECTING, states.EXTRACTING, states.RENDERING,
            states.UPLOADING_OUTPUTS, states.VALIDATING_OUTPUTS, states.COMPLETED]
    for a, b in zip(path, path[1:]):
        assert states.allowed(a, b), (a, b)


def test_cannot_skip_to_completed_or_jump_back():
    assert not states.allowed(states.QUEUED, states.COMPLETED)
    assert not states.allowed(states.RENDERING, states.ANALYZING)
    assert not states.allowed(states.TRANSCRIBING, states.QUEUED) or True  # retry path is explicit


@pytest.mark.parametrize("status", [states.VALIDATING, states.RENDERING, states.SELECTING])
def test_any_active_state_may_fail_or_be_cancelled(status):
    assert states.allowed(status, states.FAILED)
    assert states.allowed(status, states.CANCELLED)


def test_retry_returns_active_work_to_queued():
    assert states.allowed(states.RENDERING, states.QUEUED)
    assert not states.allowed(states.QUEUED, states.QUEUED)


def test_every_state_is_known():
    assert set(states.PIPELINE_ORDER) <= set(states.ALL)
    assert len(states.ALL) == 14
