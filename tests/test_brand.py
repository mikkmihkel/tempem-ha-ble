"""Home Assistant serves the integration's own brand images (2026.3+)."""

from __future__ import annotations

from http import HTTPStatus
import importlib.util

import pytest
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .test_manifest import COMPONENT
from custom_components.tempem_ble.const import DOMAIN

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("homeassistant.components.brands") is None,
    reason="local brand images need Home Assistant 2026.3 or newer",
)


@pytest.mark.parametrize(
    "image", ["icon.png", "icon@2x.png", "dark_icon.png", "dark_icon@2x.png"]
)
async def test_brand_image_served(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, image: str
) -> None:
    """The icon comes from custom_components/tempem_ble/brand/, not the CDN."""
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    resp = await client.get(f"/api/brands/integration/{DOMAIN}/{image}")
    assert resp.status == HTTPStatus.OK
    assert await resp.read() == (COMPONENT / "brand" / image).read_bytes()
