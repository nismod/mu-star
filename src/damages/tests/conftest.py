from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "example"


@pytest.fixture
def example_config_path():
    return EXAMPLE / "config" / "asset-type-cost-curves.xlsx"
