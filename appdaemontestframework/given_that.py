import re
from collections import defaultdict
from datetime import datetime

from appdaemontestframework.common import AppdaemonTestFrameworkError
from appdaemontestframework.hass_mocks import HassMocks


class StateNotSetError(AppdaemonTestFrameworkError):

    def __init__(self, entity_id, namespace):
        if namespace != "default":
            super().__init__(
                f"""
            State for entity: '{entity_id}' in '{namespace}' namespace was never set!
            Please make sure to set the state with `given_that.state_of({entity_id}, NAMESPACE).is_set_to(STATE)`
            before trying to access the mocked state
            """
            )
        else:
            super().__init__(
                f"""
            State for entity: '{entity_id}' was never set!
            Please make sure to set the state with `given_that.state_of({entity_id}).is_set_to(STATE)`
            before trying to access the mocked state
            """
            )


class AttributeNotSetError(AppdaemonTestFrameworkError):
    pass


class AttrDict(dict):
    """A dict whose keys are also reachable as attributes.

    AppDaemon >=4.5 exposes `self.app_config[app]` as a pydantic `AppConfig` model
    (with `extra="allow"`), so app code legitimately probes it with
    `hasattr(self.app_config[room], "motion_app")`. Plain dicts have no such
    attributes, so mocking the config with them makes every `hasattr` check false.
    """

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    @classmethod
    def convert(cls, value):
        if isinstance(value, dict):
            return cls({key: cls.convert(val) for key, val in value.items()})
        if isinstance(value, list):
            return [cls.convert(val) for val in value]
        return value


class GivenThatWrapper:
    def __init__(self, hass_mocks: HassMocks):
        self._hass_mocks = hass_mocks
        self._init_mocked_states()
        self._init_mocked_passed_args()
        self._init_mocked_app_config()

    def _init_mocked_states(self):
        self.mocked_states = defaultdict(dict)
        # Area registry: structural (non-state) metadata about entities.
        # `area_entities` maps an HA area name -> list of entity ids.
        # `entity_meta` maps an entity id -> {"area", "device_class", "entity_category"}.
        # Kept separate from `mocked_states` so that re-setting an entity's state with
        # `given_that.state_of(x).is_set_to(...)` (which replaces the attributes dict)
        # does not lose its structural metadata.
        self.area_entities = {}
        self.entity_meta = {}

        def get_app_mock(name):
            if name in self._hass_mocks.apps_mocks:
                return self._hass_mocks.apps_mocks[name]
            else:
                return None

        self._hass_mocks.hass_functions["get_app"].side_effect = get_app_mock

        def get_state_mock(entity_id=None, attribute=None, default=None, namespace=None, **_kwargs):
            namespace = namespace or "default"
            # `entity_id` None -> every entity; a bare domain ("person") -> every entity
            # in that domain; "domain.name" -> that single entity.
            if entity_id is None or "." not in entity_id:
                resdict = dict()
                for entityid in self.mocked_states[namespace]:
                    if entity_id is not None and entityid.split(".")[0] != entity_id:
                        continue
                    state = self.mocked_states[namespace][entityid]
                    resdict[entityid] = {
                        "state": state['main'],
                        "attributes": state['attributes'],
                    }
                return resdict
            else:
                if entity_id not in self.mocked_states[namespace]:
                    # Entity known only to the area registry (no dynamic state set):
                    # serve its structural attributes.
                    if attribute not in (None, "state", "all") and entity_id in self.entity_meta:
                        return self.entity_meta[entity_id].get(attribute)
                    # Real `ADAPI.get_state` returns the default (None) for an unknown
                    # entity rather than raising - apps legitimately query entities they
                    # are about to create (e.g. MQTT discovery). Match that.
                    return {} if attribute == "all" else default

                state = self.mocked_states[namespace][entity_id]

                if attribute is None or attribute == "state":
                    return state['main']
                if attribute == 'all':
                    def format_time(timestamp: datetime):
                        if not timestamp:
                            return None
                        return timestamp.isoformat()

                    return {
                        "last_updated": format_time(state['last_updated']),
                        "last_changed": format_time(state['last_changed']),
                        "state": state["main"],
                        "attributes": state['attributes'],
                        "entity_id": entity_id,
                    }
                value = state['attributes'].get(attribute)
                if value is None and attribute in ('last_changed', 'last_updated'):
                    # These live alongside the state rather than in the attributes dict.
                    timestamp = state[attribute]
                    value = timestamp.isoformat() if timestamp else None
                if value is None:
                    # Fall back to structural metadata from the area registry.
                    value = self.entity_meta.get(entity_id, {}).get(attribute)
                return value

        self._hass_mocks.hass_functions['get_state'].side_effect = get_state_mock

        def entity_exists_mock(entity_id, namespace=None):
            namespace = namespace or "default"
            return entity_id in self.mocked_states[namespace] or entity_id in self.entity_meta

        self._hass_mocks.hass_functions['entity_exists'].side_effect = entity_exists_mock

        self._hass_mocks.hass_functions['check_for_entity'].side_effect = entity_exists_mock

        def render_template_mock(template, namespace=None, **kwargs):
            # Minimal HA-template evaluation covering the helpers the apps use:
            # `{{ area_entities('kitchen') }}`, `{{ area_name('x') }}`, ...
            match = re.match(r"\s*\{\{\s*(\w+)\((.*?)\)\s*\}\}\s*$", template or "")
            if not match:
                return None
            func = match.group(1)
            args = [a.strip().strip("'\"") for a in match.group(2).split(",") if a.strip()]
            if func == "area_entities":
                return list(self.area_entities.get(args[0], [])) if args else []
            if func in ("area_name", "area_id"):
                # Resolve the area an entity belongs to. An area name passed straight
                # through resolves to itself, and an entity that was never assigned to
                # an area gives None - as Home Assistant does.
                if not args:
                    return None
                if args[0] in self.area_entities:
                    return args[0]
                return self.entity_meta.get(args[0], {}).get("area")
            if func == "device_attr":
                # Device registry attributes (`manufacturer`, `model`, ...). Served from
                # the entity's own mocked attributes, falling back to the area registry.
                if len(args) < 2:
                    return None
                entity_id, attr_name = args[0], args[1]
                entity_state = self.mocked_states["default"].get(entity_id)
                if entity_state and attr_name in entity_state["attributes"]:
                    return entity_state["attributes"][attr_name]
                return self.entity_meta.get(entity_id, {}).get(attr_name)
            return None

        self._hass_mocks.hass_functions['render_template'].side_effect = render_template_mock

        def _home_states(person):
            domain = "person" if person else "device_tracker"
            return [
                data['main']
                for entity_id, data in self.mocked_states["default"].items()
                if entity_id.split(".")[0] == domain
            ]

        def anyone_home_mock(person=False, namespace=None):
            return any(state == "home" for state in _home_states(person))

        def everyone_home_mock(person=False, namespace=None):
            states = _home_states(person)
            return bool(states) and all(state == "home" for state in states)

        def noone_home_mock(person=False, namespace=None):
            return not any(state == "home" for state in _home_states(person))

        self._hass_mocks.hass_functions['anyone_home'].side_effect = anyone_home_mock
        self._hass_mocks.hass_functions['everyone_home'].side_effect = everyone_home_mock
        self._hass_mocks.hass_functions['noone_home'].side_effect = noone_home_mock

        def friendly_name_mock(entity_id, namespace=None):
            namespace = namespace or "default"
            if entity_id not in self.mocked_states[namespace]:
                # Real `friendly_name` falls back to the entity id rather than raising.
                return entity_id
            return self.mocked_states[namespace][entity_id]["attributes"].get("friendly_name")

        self._hass_mocks.hass_functions["friendly_name"].side_effect = friendly_name_mock

    def _init_mocked_passed_args(self):
        self.mocked_passed_args = self._hass_mocks.hass_functions['args']
        self.mocked_passed_args.clear()

    def _init_mocked_app_config(self):
        self.mocked_app_config = self._hass_mocks.hass_functions["app_config"]
        self.mocked_app_config.clear()

    def state_of(self, entity_id, namespace=None):
        namespace = namespace or "default"
        given_that_wrapper = self

        class IsWrapper:
            def is_set_to(self,
                          state,
                          attributes=None,
                          last_updated: datetime = None,
                          last_changed: datetime = None):
                if not attributes:
                    attributes = {}
                # In Home Assistant an entity that exists always carries these, so
                # default them to the simulated 'now' rather than leaving them unset.
                now = given_that_wrapper._hass_mocks.AD.sched.get_now_sync()
                if last_updated is None:
                    last_updated = now
                if last_changed is None:
                    last_changed = now
                given_that_wrapper.mocked_states[namespace][entity_id] = {
                    "main": state,
                    "attributes": attributes,
                    "last_updated": last_updated,
                    "last_changed": last_changed,
                }

        return IsWrapper()

    def area(self, area_name):
        """Register the entities that belong to a Home Assistant area.

        Backs the `area_entities()` template helper (via the mocked `render_template`)
        and provides structural attributes (`device_class`, `entity_category`, `area`)
        that `get_state(entity, attribute=...)` falls back to.

            given_that.area("kitchen").contains({
                "binary_sensor.stove_eye_motion_detection": "motion",
                "sensor.kitchen_temperature": "temperature",
            })

        `contains` accepts a `{entity_id: device_class}` dict or a plain iterable of
        entity ids. Each entity that has no state yet gets a sensible default so
        downstream `get_state` calls do not raise.
        """
        given_that_wrapper = self

        class ContainsWrapper:
            @staticmethod
            def contains(entities):
                if isinstance(entities, dict):
                    items = list(entities.items())
                else:
                    items = [(entity_id, None) for entity_id in entities]

                registered = given_that_wrapper.area_entities.setdefault(area_name, [])
                for entity_id, device_class in items:
                    if entity_id not in registered:
                        registered.append(entity_id)

                    meta = given_that_wrapper.entity_meta.setdefault(entity_id, {})
                    meta["area"] = area_name
                    if device_class is not None:
                        meta["device_class"] = device_class

                    if entity_id not in given_that_wrapper.mocked_states["default"]:
                        domain = entity_id.split(".")[0]
                        default_state = "off" if domain in (
                            "binary_sensor", "switch", "input_boolean") else 0
                        given_that_wrapper.mocked_states["default"][entity_id] = {
                            "main": default_state,
                            "attributes": {},
                            "last_updated": None,
                            "last_changed": None,
                        }

        return ContainsWrapper()

    def passed_arg(self, argument_key):
        given_that_wrapper = self

        class IsWrapper:
            @staticmethod
            def is_set_to(argument_value):
                given_that_wrapper.mocked_passed_args[argument_key] = \
                    argument_value

        return IsWrapper()

    def app_config(self, argument_key):
        given_that_wrapper = self

        class IsWrapper:
            @staticmethod
            def is_set_to(argument_value):
                # Stored as an AttrDict so `hasattr(app_config[app], "key")` behaves
                # like it does against AppDaemon's pydantic AppConfig model.
                given_that_wrapper.mocked_app_config[argument_key] = AttrDict.convert(argument_value)

        return IsWrapper()

    def time_is(self, time_as_datetime):
        self._hass_mocks.AD.sched.sim_set_start_time(time_as_datetime)

    def mock_functions_are_cleared(self, clear_mock_states=False, clear_mock_passed_args=False, clear_mock_app_config=False):
        for mocked_function in self._hass_mocks.hass_functions.values():
            mocked_function.reset_mock()
        if clear_mock_states:
            self._init_mocked_states()
        if clear_mock_passed_args:
            self._init_mocked_passed_args()
        if clear_mock_app_config:
            self._init_mocked_app_config()
