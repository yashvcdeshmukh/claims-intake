"""Deliberate failure so CI can be observed on the pull request."""


def test_deliberate_ci_failure() -> None:
    assert False
