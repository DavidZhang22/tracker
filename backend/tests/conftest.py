import pytest


@pytest.fixture(autouse=True)
def thread_analysis_for_unit_tests(monkeypatch):
    # Process startup is exercised explicitly in test_analysis_pool and the
    # runtime verifier. Ordinary API tests can retain their patched parsers.
    monkeypatch.setenv("TRACKER_ANALYSIS_WORKERS", "0")
