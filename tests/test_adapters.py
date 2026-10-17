import pytest
from src.adapters.registry import adapter_registry
from src.adapters.robu import RobuAdapter
from src.adapters.thinkrobotics import ThinkRoboticsAdapter
from src.adapters.zbotic import ZboticAdapter
from src.adapters.evelta import EveltaAdapter
from src.adapters.generic import GenericAdapter


def test_adapter_matching():
    robu_url = "https://robu.in/product/raspberry-pi-5-8gb/"
    adapter = adapter_registry.get_adapter_for_url(robu_url)
    assert isinstance(adapter, RobuAdapter)

    think_url = "https://thinkrobotics.com/products/holybro-pixhawk-6x"
    adapter = adapter_registry.get_adapter_for_url(think_url)
    assert isinstance(adapter, ThinkRoboticsAdapter)

    zbotic_url = "https://zbotic.in/product/cube-orange-plus/"
    adapter = adapter_registry.get_adapter_for_url(zbotic_url)
    assert isinstance(adapter, ZboticAdapter)

    evelta_url = "https://evelta.com/raspberry-pi-5-m-2-hat/"
    adapter = adapter_registry.get_adapter_for_url(evelta_url)
    assert isinstance(adapter, EveltaAdapter)

    unknown_url = "https://random-droneshop-india.com/item/123"
    adapter = adapter_registry.get_adapter_for_url(unknown_url)
    assert isinstance(adapter, GenericAdapter)
