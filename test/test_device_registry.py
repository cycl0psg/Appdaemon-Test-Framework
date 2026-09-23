from appdaemon.plugins.hass.hassapi import Hass

from appdaemontestframework import automation_fixture

DEVICE = "87eac77bbc2c96a718cdf24bb0fb5b67"


class MockAutomation(Hass):
    def initialize(self):
        pass


@automation_fixture(MockAutomation)
def automation():
    pass


def test_device_entities_lists_what_the_device_contains(automation, given_that):
    given_that.device(DEVICE).contains(["fan.dyson", "switch.dyson_night_mode"])
    assert automation.device_entities(DEVICE) == ["fan.dyson", "switch.dyson_night_mode"]


def test_device_id_finds_the_device_of_an_entity(automation, given_that):
    given_that.device(DEVICE).contains(["fan.dyson", "switch.dyson_night_mode"])
    assert automation.device_id("switch.dyson_night_mode") == DEVICE


def test_an_entity_on_no_device_has_no_device_id(automation, given_that):
    given_that.device(DEVICE).contains(["fan.dyson"])
    assert automation.device_id("fan.xiaomi") is None


def test_an_unknown_device_has_no_entities(automation):
    assert automation.device_entities("unknown") == []


def test_registering_twice_does_not_duplicate(automation, given_that):
    given_that.device(DEVICE).contains(["fan.dyson"])
    given_that.device(DEVICE).contains(["fan.dyson", "climate.dyson"])
    assert automation.device_entities(DEVICE) == ["fan.dyson", "climate.dyson"]
