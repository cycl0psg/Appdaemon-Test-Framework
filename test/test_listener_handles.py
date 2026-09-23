from appdaemon.plugins.hass.hassapi import Hass

from appdaemontestframework import automation_fixture


class MockAutomation(Hass):
    def initialize(self):
        pass

    def _callback(self, *_args, **_kwargs):
        pass


@automation_fixture(MockAutomation)
def automation():
    pass


def test_listen_state_hands_out_a_unique_handle(automation):
    first = automation.listen_state(automation._callback, "light.kitchen")
    second = automation.listen_state(automation._callback, "light.kitchen")
    assert first is not None
    assert first != second


def test_listen_event_hands_out_a_unique_handle(automation):
    first = automation.listen_event(automation._callback, "some_event")
    second = automation.listen_event(automation._callback, "some_event")
    assert first is not None
    assert first != second


def test_cancelling_a_state_listener_reports_success(automation, hass_mocks):
    handle = automation.listen_state(automation._callback, "light.kitchen")
    assert automation.cancel_listen_state(handle) is True
    hass_mocks.hass_functions["cancel_listen_state"].assert_called_once_with(handle)


def test_cancelling_an_event_listener_reports_success(automation, hass_mocks):
    handle = automation.listen_event(automation._callback, "some_event")
    assert automation.cancel_listen_event(handle) is True
    hass_mocks.hass_functions["cancel_listen_event"].assert_called_once_with(handle)
