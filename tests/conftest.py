import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest_plugins = "pytest_homeassistant_custom_component"

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
