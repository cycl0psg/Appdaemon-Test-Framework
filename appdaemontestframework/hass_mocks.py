import logging
import threading
import uuid
import warnings

import appdaemon.utils
import mock
from appdaemon.plugins.hass.hassapi import Hass
from packaging.version import Version

from appdaemontestframework.appdaemon_mock.appdaemon import MockAppDaemon

CURRENT_APPDAEMON_VERSION = Version(appdaemon.utils.__version__)


def is_appdaemon_version_at_least(version_as_string):
    expected_appdaemon_version = Version(version_as_string)
    return CURRENT_APPDAEMON_VERSION >= expected_appdaemon_version


def _new_callback_handle(*_args, **_kwargs):
    """Side effect for mocked `run_*` registrations: return a fresh, unique handle."""
    return uuid.uuid4().hex


def _make_config_model(name, automation_class):
    """Build a minimal AppDaemon `AppConfig` so the inherited `name` property works.

    AppDaemon >=4.5 backs `ADAPI.name` with a pydantic `AppConfig` model instead of a
    plain attribute set in `__init__` (which we mock out).
    """
    from appdaemon.app_management import AppConfig  # pylint: disable=import-outside-toplevel

    return AppConfig(
        name=name,
        module=getattr(automation_class, "__module__", "appdaemontestframework"),
        **{"class": getattr(automation_class, "__name__", str(name))},
    )


class _DeprecatedAndUnsupportedAppdaemonCheck:
    already_warned_during_this_test_session = False
    min_supported_appdaemon_version = '4.5.0'
    min_deprecated_appdaemon_version = '4.5.0'

    @classmethod
    def show_warning_only_once(cls):
        if cls.already_warned_during_this_test_session is True:
            return
        cls.already_warned_during_this_test_session = True

        appdaemon_version_unsupported = not is_appdaemon_version_at_least(
                cls.min_supported_appdaemon_version
        )
        appdaemon_version_deprecated = not is_appdaemon_version_at_least(
                cls.min_deprecated_appdaemon_version
        )

        if appdaemon_version_unsupported:
            raise Exception("Appdaemon-Test-Framework only support Appdemon >={} "
                            "Your current Appdemon version is {}".format(
                                cls.min_supported_appdaemon_version,
                                CURRENT_APPDAEMON_VERSION))

        if appdaemon_version_deprecated:
            warnings.warn(
                    "Appdaemon-Test-Framework will only support Appdaemon >={} "
                    "until the next major release. "
                    "Your current Appdemon version is {}".format(
                            cls.min_deprecated_appdaemon_version,
                            CURRENT_APPDAEMON_VERSION
                    ),
                    DeprecationWarning)


class HassMocks:
    def __init__(self):
        _DeprecatedAndUnsupportedAppdaemonCheck.show_warning_only_once()
        # Mocked out init for Hass class.
        self._hass_instances = []  # list of all hass instances
        self._apps_mocks = {}

        hass_mocks = self
        AD = MockAppDaemon()
        self.AD = AD

        def _hass_init_mock(self, _ad, name, *_args):
            hass_mocks._hass_instances.append(self)
            if "app_name" in getattr(self, "args", {}):
                hass_mocks.apps_mocks[self.args["app_name"]] = self
            else:
                hass_mocks.apps_mocks[self.__module__] = self
            # `name` is a read-only property on ADAPI backed by `config_model`. Set the
            # backing model directly (bypassing the `config_model` setter, which would
            # also clobber the mocked `args` dict).
            self._config_model = _make_config_model(name, type(self))
            self.AD = AD
            self.logger = logging.getLogger(__name__)
            self.lock = threading.RLock()
            self._namespace = "default"

        # This is a list of all mocked out functions.
        self._mock_handlers = [
            # Meta
            # Patch the __init__ method to skip Hass initialization.
            # Use autospec so we can access the `self` object
            MockHandler(Hass, "__init__", side_effect=_hass_init_mock, autospec=True),
            # logging
            MockHandler(Hass, "log", side_effect=self._log_log),
            MockHandler(Hass, "error", side_effect=self._log_error),
            # Scheduler callback registrations functions
            # `run_in` is spied so it reaches the real AppDaemon code and actually
            # schedules the callback into `MockScheduler` (this is what makes
            # `time_travel` work). The other `run_*` registrations are plain mocks that
            # just record the call and hand back a unique handle - going through the real
            # AppDaemon code for those would drag in far more scheduler internals than is
            # worth mocking, and tests only assert on *how* they were called.
            SpyMockHandler(Hass, "run_in"),
            MockHandler(Hass, "run_once", side_effect=_new_callback_handle),
            MockHandler(Hass, "run_at", side_effect=_new_callback_handle),
            MockHandler(Hass, "run_daily", side_effect=_new_callback_handle),
            MockHandler(Hass, "run_hourly", side_effect=_new_callback_handle),
            MockHandler(Hass, "run_minutely", side_effect=_new_callback_handle),
            MockHandler(Hass, "run_every", side_effect=_new_callback_handle),
            SpyMockHandler(Hass, "timer_running"),
            SpyMockHandler(Hass, "cancel_timer"),
            # Sunrise and sunset functions
            MockHandler(Hass, "run_at_sunrise", side_effect=_new_callback_handle),
            MockHandler(Hass, "run_at_sunset", side_effect=_new_callback_handle),
            # Listener callback registrations functions
            MockHandler(Hass, "listen_event"),
            MockHandler(Hass, "cancel_listen_event"),
            MockHandler(Hass, "listen_state"),
            MockHandler(Hass, "cancel_listen_state"),
            # State functions / attr
            MockHandler(Hass, "set_state"),
            MockHandler(Hass, "get_state"),
            SpyMockHandler(Hass, "time"),
            DictMockHandler(Hass, "args"),
            DictMockHandler(Hass, "app_config"),
            # Interactions functions
            MockHandler(Hass, "call_service"),
            MockHandler(Hass, "turn_on"),
            MockHandler(Hass, "turn_off"),
            MockHandler(Hass, "fire_event"),
            MockHandler(Hass, "select_option"),
            # Custom callback functions
            MockHandler(Hass, "register_constraint"),
            MockHandler(Hass, "now_is_between"),
            MockHandler(Hass, "notify"),
            # Miscellaneous Helper Functions
            MockHandler(Hass, "entity_exists"),
            MockHandler(Hass, "get_app"),
            MockHandler(Hass, "friendly_name"),
            MockHandler(Hass, "set_log_level"),
            # AppDaemon >=4.5 routes these through `self.AD.plugins.get_plugin_object()`,
            # which the mock AppDaemon does not provide. Mock the leaf methods instead.
            # `render_template` backs the HA template helpers (`area_entities()` etc.).
            MockHandler(Hass, "check_for_entity"),
            MockHandler(Hass, "render_template"),
            MockHandler(Hass, "get_history"),
            # `persistent_notification` internally does `await self.call_service(...)`,
            # and the plain `call_service` mock is not awaitable - mock the leaf.
            MockHandler(Hass, "persistent_notification"),
            # `@sync_decorator async def` in AppDaemon that internally await a (mocked,
            # non-awaitable) `get_state` - spying the real impl breaks. Mock them and
            # compute the answer from the mocked person / device_tracker states.
            MockHandler(Hass, "anyone_home"),
            MockHandler(Hass, "everyone_home"),
            MockHandler(Hass, "noone_home"),
        ]

        # Generate a dictionary of mocked Hass functions for use by older code
        # Note: This interface is considered deprecated and should be replaced
        # with calls to public methods in the HassMocks object going forward.
        self._hass_functions = {}
        for mock_handler in self._mock_handlers:
            self._hass_functions[
                mock_handler.function_or_field_name] = mock_handler.mock

    # Mock handling
    def unpatch_mocks(self):
        """Stops all mocks this class handles."""
        for mock_handler in self._mock_handlers:
            mock_handler.patch.stop()
        # Release the frozen clock installed by the scheduler's time simulation so it
        # does not leak into subsequent tests, then shut down the background event loop.
        self.AD.sched.sim_stop()
        self.AD.stop()

    # Access to the deprecated hass_functions dict.
    @property
    def hass_functions(self):
        return self._hass_functions

    @property
    def apps_mocks(self):
        return self._apps_mocks

    # Logging mocks
    @staticmethod
    def _log_error(msg, level="ERROR", stack_info=False):
        HassMocks._log_log(msg, level)

    @staticmethod
    def _log_log(msg, level="INFO", stack_info=False):
        # Renamed the function to remove confusion
        get_logging_level_from_name = logging.getLevelName
        logging.log(get_logging_level_from_name(level), msg)

    @staticmethod
    def _uuid4(*args, **kwargs):
        # return uuid4 for handle identification
        return uuid.uuid4()


class MockHandler:
    """
    A class for generating a mock in an object and holding on to info about it.
    :param object_to_patch: The object to patch
    :param function_or_field_name: the name of the function to patch in the
    object
    :param side_effect: side effect method to call. If not set, it will just
    return `None`
    :param autospec: If `True` will autospec the Mock signature. Useful for
    getting `self` in side effects.
    """

    def __init__(self,
                 object_to_patch,
                 function_or_field_name,
                 side_effect=None,
                 autospec=False):
        self.function_or_field_name = function_or_field_name
        patch_kwargs = self._patch_kwargs(side_effect, autospec)
        self.patch = mock.patch.object(
                object_to_patch,
                function_or_field_name,
                **patch_kwargs
        )
        self.mock = self.patch.start()

    def _patch_kwargs(self, side_effect, autospec):
        return {
            'create': True,
            'side_effect': side_effect,
            'return_value': None,
            'autospec': autospec
        }


class DictMockHandler(MockHandler):
    class MockDict(dict):
        def reset_mock(self):
            pass

    def __init__(self, object_to_patch, field_name):
        super().__init__(object_to_patch, field_name)

    def _patch_kwargs(self, _side_effect, _autospec):
        return {
            'create': True,
            'new': self.MockDict()
        }


class SpyMockHandler(MockHandler):
    """
    Mock Handler that provides a Spy. That is, when invoke it will call the
    original function but still provide all Mock-related functionality
    """

    def __init__(self, object_to_patch, function_name):
        original_function = getattr(object_to_patch, function_name)
        super().__init__(
                object_to_patch,
                function_name,
                side_effect=original_function,
                autospec=True
        )
