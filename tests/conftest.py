"""Pytest fixtures for the integration suite.

Every test starts from a clean database so tests are isolated and repeatable
(no shared module state, no cross-test coupling).
"""

import pytest_asyncio

from tests.helpers import DEVICES, register_device, truncate_all


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Truncate all data tables before each test."""
    await truncate_all()
    yield


@pytest_asyncio.fixture
async def registered(clean_db):
    """Register all standard devices and return {device_id: api_key}."""
    keys = {}
    for device_id, cfg in DEVICES.items():
        keys[device_id] = await register_device(device_id, cfg["patient_id"], cfg["device_type"])
    return keys
