"""Shared pytest configuration — no fixtures needed for live-server tests."""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (use -m 'not slow' to skip)",
    )
