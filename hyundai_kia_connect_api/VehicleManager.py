"""VehicleManager.py"""

# pylint:disable=logging-fstring-interpolation,missing-class-docstring,missing-function-docstring,line-too-long,invalid-name

import datetime as dt
import logging
import re
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from .ApiImpl import (
    ApiImpl,
    ClimateRequestOptions,
    OTPRequest,
    POIInfo,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
)
from .const import (
    BRAND_GENESIS,
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    CHARGE_PORT_ACTION,
    DOMAIN,
    ORDER_STATUS,
    OTP_NOTIFY_TYPE,
    REGION_AUSTRALIA,
    REGION_BRAZIL,
    REGION_CANADA,
    REGION_CHINA,
    REGION_EUROPE,
    REGION_EUROPE_CCI,
    REGION_INDIA,
    REGION_NZ,
    REGION_USA,
    REGIONS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
)
from .exceptions import APIError, AuthenticationError, AuthenticationOTPRequired
from .HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from .HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from .HyundaiCciApiEU import HyundaiCciApiEU
from .KiaCciApiEU import KiaCciApiEU
from .KiaUvoApiAU import KiaUvoApiAU
from .KiaUvoApiCA import KiaUvoApiCA
from .KiaUvoApiCN import KiaUvoApiCN
from .KiaUvoApiEU import KiaUvoApiEU
from .KiaUvoApiIN import KiaUvoApiIN
from .KiaUvoApiUSA import KiaUvoApiUSA
from .mqtt_client import MqttTransport
from .svm import SVMDetails
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class VehicleManager:
    def __init__(
        self,
        region: int,
        brand: int,
        username: str,
        password: str,
        pin: str,
        geocode_api_enable: bool = False,
        geocode_api_use_email: bool = False,
        geocode_provider: int = 1,
        geocode_api_key: str | None = None,
        token: Token | None = None,
        language: str = "en",
    ):
        self.region: int = region
        self.brand: int = brand
        self.username: str = username
        self.password: str = password
        self.geocode_api_enable: bool = geocode_api_enable
        self.geocode_api_use_email: bool = geocode_api_use_email
        self.geocode_provider: int = geocode_provider
        self.pin: str = pin
        self.language: str = language
        self.geocode_api_key: str = geocode_api_key

        self.api: ApiImpl = self.get_implementation_by_region_brand(
            self.region, self.brand, self.language
        )

        self.token: Token = token
        self.vehicles: dict[str, Vehicle] = {}
        self.otp_request: OTPRequest = None
        # Push (MQTT) transport and action tracking. Connection-scoped
        # state lives here; all vehicle-level push identity lives on
        # Vehicle (mqtt_vehicle_id, hu_client_id).
        self._push_client: MqttTransport | None = None
        self._push_action_events: dict[str, threading.Event] = {}
        self._push_action_results: dict[str, str] = {}
        self._push_status_callback: Callable[[str, str], None] | None = None
        self._push_connection_change_callback: Callable[[bool], None] | None = None
        self._push_reconnect_cancel: threading.Event | None = None
        self._push_vehicle_ids: list[str] = []
        self._push_transport_factory: type = MqttTransport

    @DeprecationWarning
    def initialize(self) -> None:
        self.token: Token = self.api.login(
            username=self.username,
            password=self.password,
            pin=self.pin,
        )
        self.initialize_vehicles()

    def login(self) -> bool | OTPRequest:
        """Returns True if login successful, or OTPOptions if OTP is required"""
        result = self.api.login(
            username=self.username,
            password=self.password,
            pin=self.pin,
        )
        if isinstance(result, Token):
            self.token: Token = result
            self.initialize_vehicles()
            return True
        if isinstance(result, OTPRequest):
            self.otp_request = result
            return result

    def send_otp(self, notify_type: OTP_NOTIFY_TYPE) -> None:
        self.api.send_otp(self.otp_request, notify_type)

    def verify_otp_and_complete_login(self, otp_code: str) -> None:
        self.token = self.api.verify_otp_and_complete_login(
            username=self.username,
            password=self.password,
            otp_code=otp_code,
            otp_request=self.otp_request,
            pin=self.pin,
        )
        self.initialize_vehicles()

    def initialize_vehicles(self):
        if len(self.vehicles) > 0:
            _LOGGER.warning(
                "Vehicles already initialized, this will re-initialize and cause data loss mapping errors"
            )
        vehicles = self.api.get_vehicles(self.token)
        for vehicle in vehicles:
            vehicle.supports_window_control = self.api.supports_window_control
            vehicle.supports_valet_mode = self.api.supports_valet_mode
            vehicle.supports_svm = self.api.supports_svm
            self.vehicles[vehicle.id] = vehicle

    def get_vehicle(self, vehicle_id: str) -> Vehicle:
        return self.vehicles[vehicle_id]

    def update_all_vehicles_with_cached_state(self) -> None:
        for vehicle_id in self.vehicles:
            self.update_vehicle_with_cached_state(vehicle_id)

    def update_vehicle_with_cached_state(self, vehicle_id: str) -> None:
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.enabled:
            vehicle.last_scanned_at = dt.datetime.now(dt.UTC)
            self.api.update_vehicle_with_cached_state(self.token, vehicle)
            if self.geocode_api_enable is True:
                self.api.update_geocoded_location(
                    token=self.token,
                    vehicle=vehicle,
                    use_email=self.geocode_api_use_email,
                    provider=self.geocode_provider,
                    API_KEY=self.geocode_api_key,
                )
        else:
            _LOGGER.debug(f"{DOMAIN} - Vehicle Disabled, skipping.")

    def check_and_force_update_vehicles(self, force_refresh_interval: int) -> None:
        for vehicle_id in self.vehicles:
            self.check_and_force_update_vehicle(force_refresh_interval, vehicle_id)

    def check_and_force_update_vehicle(
        self, force_refresh_interval: int, vehicle_id: str
    ) -> None:
        # Force refresh only if current data is older than the value bassed in seconds.
        # Otherwise runs a cached update.
        started_at_utc: dt.datetime = dt.datetime.now(dt.UTC)
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.last_updated_at is not None:
            _LOGGER.debug(
                f"{DOMAIN} - Time differential in seconds: {(started_at_utc - vehicle.last_updated_at).total_seconds()}"
            )
            if (
                started_at_utc - vehicle.last_updated_at
            ).total_seconds() > force_refresh_interval:
                self.force_refresh_vehicle_state(vehicle_id)
            else:
                self.update_vehicle_with_cached_state(vehicle_id)
        else:
            self.update_vehicle_with_cached_state(vehicle_id)

    def force_refresh_all_vehicles_states(self) -> None:
        for vehicle_id in self.vehicles:
            self.force_refresh_vehicle_state(vehicle_id)

    def force_refresh_vehicle_state(self, vehicle_id: str) -> None:
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.enabled:
            vehicle.last_scanned_at = dt.datetime.now(dt.UTC)
            self.api.force_refresh_vehicle_state(self.token, vehicle)
        else:
            _LOGGER.debug(f"{DOMAIN} - Vehicle Disabled, skipping.")

    def get_svm_details(self, vehicle_id: str) -> SVMDetails:
        """Return the latest cached SVM composite image and metadata.

        Delegates to the region-specific API implementation. Raises
        NotImplementedError on regions without SVM support.
        """
        return self.api.get_svm_details(self.token, self.get_vehicle(vehicle_id))

    def request_svm_capture(
        self, vehicle_id: str, acknowledged_warning: bool
    ) -> SVMDetails:
        """Trigger a fresh SVM capture and return the resulting image.

        ``acknowledged_warning`` must be True; the caller must explicitly
        acknowledge the safety warning before triggering a capture.
        """
        return self.api.request_svm_capture(
            self.token, self.get_vehicle(vehicle_id), acknowledged_warning
        )

    def check_and_refresh_token(self) -> bool:
        if self.token is None:
            if self.login() is True:
                if len(self.vehicles) == 0:
                    self.initialize_vehicles()
                return True
            else:
                raise AuthenticationOTPRequired("OTP required to refresh token")
        now_utc = dt.datetime.now(dt.UTC)
        grace_period = timedelta(seconds=10)
        min_supported_datetime = dt.datetime.min.replace(tzinfo=dt.UTC)
        valid_until = self.token.valid_until
        token_expired = False
        if not isinstance(valid_until, dt.datetime):
            token_expired = True
        else:
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=dt.UTC)
            if valid_until <= min_supported_datetime + grace_period:
                token_expired = True
            else:
                token_expired = valid_until - grace_period <= now_utc
        if token_expired or self.api.test_token(self.token) is False:
            _LOGGER.debug(f"{DOMAIN} - Refresh token expired")
            try:
                result = self.api.refresh_access_token(
                    self.token,
                )
            except AuthenticationError:
                # The legacy password field may hold a 48-char refresh token;
                # a fallback login with it re-enters the refresh-token grant
                # and wedges the account (kia_uvo #1888). Retry once with the
                # credentials the manager was constructed with.
                if (
                    re.fullmatch(r"[A-Z0-9]{48}", self.token.password or "")
                    and self.password
                    and self.password != self.token.password
                    and not re.fullmatch(r"[A-Z0-9]{48}", self.password)
                ):
                    _LOGGER.warning(
                        f"{DOMAIN} - Refresh failed with a legacy "
                        "refresh-token password; retrying login with the "
                        "configured credentials"
                    )
                    result = self.api.login(self.username, self.password, self.pin)
                else:
                    raise
            if isinstance(result, Token):
                self.token: Token = result
                # Temp correction to fix bad data due to a bug.
                if self.token.pin != self.pin:
                    self.token.pin = self.pin
                if len(self.vehicles) == 0:
                    self.initialize_vehicles()
            if isinstance(result, OTPRequest):
                raise AuthenticationOTPRequired("OTP required to refresh token")
            self.api.refresh_vehicles(self.token, self.vehicles)
            return True
        if len(self.vehicles) == 0:
            self.initialize_vehicles()
        return False

    def start_climate(self, vehicle_id: str, options: ClimateRequestOptions) -> str:
        return self.api.start_climate(self.token, self.get_vehicle(vehicle_id), options)

    def stop_climate(self, vehicle_id: str) -> str:
        return self.api.stop_climate(self.token, self.get_vehicle(vehicle_id))

    def lock(self, vehicle_id: str) -> str:
        return self.api.lock_action(
            self.token, self.get_vehicle(vehicle_id), VEHICLE_LOCK_ACTION.LOCK
        )

    def unlock(self, vehicle_id: str) -> str:
        return self.api.lock_action(
            self.token,
            self.get_vehicle(vehicle_id),
            VEHICLE_LOCK_ACTION.UNLOCK,
        )

    def start_charge(self, vehicle_id: str) -> str:
        return self.api.start_charge(self.token, self.get_vehicle(vehicle_id))

    def stop_charge(self, vehicle_id: str) -> str:
        return self.api.stop_charge(self.token, self.get_vehicle(vehicle_id))

    def start_hazard_lights(self, vehicle_id: str) -> str:
        return self.api.start_hazard_lights(self.token, self.get_vehicle(vehicle_id))

    def start_hazard_lights_and_horn(self, vehicle_id: str) -> str:
        return self.api.start_hazard_lights_and_horn(
            self.token, self.get_vehicle(vehicle_id)
        )

    def set_charge_limits(self, vehicle_id: str, ac: int, dc: int) -> str:
        return self.api.set_charge_limits(
            self.token, self.get_vehicle(vehicle_id), ac, dc
        )

    def set_charging_current(self, vehicle_id: str, level: int) -> str:
        return self.api.set_charging_current(
            self.token, self.get_vehicle(vehicle_id), level
        )

    def set_windows_state(self, vehicle_id: str, options: WindowRequestOptions) -> str:
        return self.api.set_windows_state(
            self.token, self.get_vehicle(vehicle_id), options
        )

    def check_action_status(
        self,
        vehicle_id: str,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 120,
    ) -> ORDER_STATUS:
        """
        Check for the status of a sent action/command.

        Actions can have 4 states:
        - pending: request sent to vehicle, waiting for response
        - success: vehicle confirmed that the action was performed
        - fail: vehicle could not perform the action
                (most likely because a condition was not met)
        - vehicle timeout: request sent to vehicle, no response received.

        In case of timeout, the API can return "pending" for up to 2 minutes before
        it returns a final state.

        :param vehicle_id: ID of the vehicle
        :param action_id: ID of the action
        :param synchronous: Whether to wait for pending actions to reach a final
                            state (success/fail/timeout)
        :param timeout:
            Time in seconds to wait for pending actions to reach a final state.
        :return: status of the order
        """
        return self.api.check_action_status(
            self.token, self.get_vehicle(vehicle_id), action_id, synchronous, timeout
        )

    def open_charge_port(self, vehicle_id: str) -> str:
        return self.api.charge_port_action(
            self.token, self.get_vehicle(vehicle_id), CHARGE_PORT_ACTION.OPEN
        )

    def close_charge_port(self, vehicle_id: str) -> str:
        return self.api.charge_port_action(
            self.token, self.get_vehicle(vehicle_id), CHARGE_PORT_ACTION.CLOSE
        )

    def update_month_trip_info(self, vehicle_id: str, yyyymm_string: str) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.month_trip_info for the specified month.

        Default this information is None:

        month_trip_info: MonthTripInfo = None
        """
        vehicle = self.get_vehicle(vehicle_id)
        self.api.update_month_trip_info(self.token, vehicle, yyyymm_string)

    def update_day_trip_info(self, vehicle_id: str, yyyymmdd_string: str) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.day_trip_info information for the specified day.

        Default this information is None:

        day_trip_info: DayTripInfo = None
        """
        vehicle = self.get_vehicle(vehicle_id)
        self.api.update_day_trip_info(self.token, vehicle, yyyymmdd_string)

    def disable_vehicle(self, vehicle_id: str) -> None:
        self.get_vehicle(vehicle_id).enabled = False

    def enable_vehicle(self, vehicle_id: str) -> None:
        self.get_vehicle(vehicle_id).enabled = True

    def schedule_charging_and_climate(
        self, vehicle_id: str, options: ScheduleChargingClimateRequestOptions
    ) -> str:
        """Set scheduled charging and/or climate for the vehicle.

        ``None`` option fields mean "leave unchanged": the library resolves
        them from the vehicle's current settings. On ccNC/EV5-appMode EVs a
        scope whose options are all ``None`` skips its endpoint entirely;
        calling with no field set raises ``ValueError``.
        """
        return self.api.schedule_charging_and_climate(
            self.token, self.get_vehicle(vehicle_id), options
        )

    def start_valet_mode(self, vehicle_id: str) -> str:
        return self.api.valet_mode_action(
            self.token, self.get_vehicle(vehicle_id), VALET_MODE_ACTION.ACTIVATE
        )

    def stop_valet_mode(self, vehicle_id: str) -> str:
        return self.api.valet_mode_action(
            self.token, self.get_vehicle(vehicle_id), VALET_MODE_ACTION.DEACTIVATE
        )

    def set_vehicle_to_load_discharge_limit(self, vehicle_id: str, limit: int) -> str:
        return self.api.set_vehicle_to_load_discharge_limit(
            self.token, self.get_vehicle(vehicle_id), limit
        )

    def set_navigation(self, vehicle_id: str, poi_list: list[POIInfo]) -> str:
        return self.api.set_navigation(
            self.token, self.get_vehicle(vehicle_id), poi_list
        )

    # ------------------------------------------------------------------
    # Push (MQTT) — generic glue over the ApiImpl push contract.
    # The manager knows nothing about broker schemas: a region API that
    # declares supports_mqtt_push supplies broker info, registration,
    # vehicle identity, topics and the message parser. Regions without
    # support are a no-op (False / None), never an AttributeError.
    # ------------------------------------------------------------------

    @property
    def is_push_supported(self) -> bool:
        """Whether the active region API declares push support."""
        return bool(getattr(self.api, "supports_mqtt_push", False))

    def start_push(self, vehicle_id: str) -> bool:
        """Connect to the push broker and subscribe the vehicle's topics.

        Region order (live-probed, app-confirmed):
        broker → register client → vehicle identity → register vehicle →
        transport connect → subscribe topics. Returns False when the
        region API does not support push or setup failed.
        """
        if not self.is_push_supported:
            _LOGGER.debug(f"{DOMAIN} - Push not supported on {type(self.api).__name__}")
            return False

        token = self.token
        vehicle = self.get_vehicle(vehicle_id)
        if self._push_client is not None:
            self._push_vehicle_ids.append(vehicle_id)
            ok = self._register_new_vehicle(token, vehicle, vehicle_id)
            if ok:
                if self._push_client.is_connected:
                    self._subscribe_vehicle_topics(vehicle_id)
                return True
            self._push_vehicle_ids.remove(vehicle_id)
            return False

        broker = self.api.get_push_broker_info(token)
        if not broker or not broker.get("mqtt_host"):
            _LOGGER.debug(f"{DOMAIN} - Push broker info not available")
            return False
        reg = self.api.register_push_client(token)
        if not reg:
            _LOGGER.debug(f"{DOMAIN} - Push client registration failed")
            return False

        self._push_vehicle_ids.append(vehicle_id)
        if not self._register_new_vehicle(token, vehicle, vehicle_id):
            self._push_vehicle_ids.remove(vehicle_id)
            return False

        client = self._push_transport_factory(
            on_message=self._on_push_delivery_raw,
            on_connect=self._on_push_connect,
            on_disconnect=self._on_push_disconnect,
        )
        client.configure(
            broker_host=broker["mqtt_host"],
            broker_port=broker.get("mqtt_port", 8883),
            use_ssl=broker.get("use_ssl", True),
            client_id=reg.get("client_id"),
            username=reg.get("username"),
            password=reg.get("password"),
        )
        if self._push_connection_change_callback:
            client.set_on_connection_change(self._push_connection_change_callback)
        try:
            client.connect()
        except Exception as ex:
            _LOGGER.warning(f"{DOMAIN} - Push connection failed: {ex}")
            self._push_vehicle_ids.remove(vehicle_id)
            return False
        self._push_client = client
        if client.is_connected:
            self._subscribe_vehicle_topics(vehicle_id)
        return True

    def _register_new_vehicle(
        self, token: Token, vehicle: Vehicle, vehicle_id: str
    ) -> bool:
        """Run the per-vehicle registration steps of the push contract."""
        try:
            self.api.get_push_vehicle_identity(token, vehicle)
            self.api.register_push_vehicle(token, vehicle)
        except Exception as ex:
            _LOGGER.warning(
                f"{DOMAIN} - Push registration failed for {vehicle_id}: {ex}"
            )
            return False
        return True

    def _subscribe_vehicle_topics(self, vehicle_id: str) -> None:
        """Subscribe the topics the region API provides for the vehicle."""
        assert self._push_client is not None
        vehicle = self.get_vehicle(vehicle_id)
        try:
            topics = self.api.get_push_topics(self.token, vehicle)
        except Exception:
            _LOGGER.exception(f"{DOMAIN} - Push topic build failed for {vehicle_id}")
            return
        if topics:
            self._push_client.subscribe(topics)

    def _on_push_connect(self) -> None:
        """Subscribe topics for each started push vehicle on (re)connect."""
        for vehicle_id in list(self._push_vehicle_ids):
            if vehicle_id in self.vehicles:
                try:
                    self._subscribe_vehicle_topics(vehicle_id)
                except Exception:
                    _LOGGER.exception(
                        f"{DOMAIN} - Push subscribe failed for {vehicle_id}"
                    )

    def _on_push_delivery_raw(self, topic: str, payload: bytes) -> None:
        """Raw delivery from the transport; parse via the region API.

        Called from the paho background thread — must be thread-safe.
        """
        message = self.api.parse_push_message(topic, payload)
        if message is None:
            return
        self._on_push_message(message)

    def _on_push_message(self, message: Any) -> None:
        """Dispatch a parsed push message (duck-typed contract shape).

        Expected attributes: is_status (bool), vehicle_id (str, push
        level), topic_type (str), and optional action fields action_id /
        action_result (str | None), action_connected (bool).
        """
        action_id = getattr(message, "action_id", None)
        if action_id and action_id in self._push_action_events:
            result = message.action_result or (
                "connected"
                if getattr(message, "action_connected", False)
                else "success"
            )
            self._push_action_results[action_id] = result
            self._push_action_events[action_id].set()

        if not getattr(message, "is_status", False) or not self._push_status_callback:
            return
        vehicle_id = self._vehicle_id_for_push(getattr(message, "vehicle_id", ""))
        if vehicle_id is None:
            _LOGGER.debug(
                f"{DOMAIN} - Push vehicle id {message.vehicle_id} unknown, dropping"
            )
            return
        try:
            self._push_status_callback(vehicle_id, message.topic_type)
        except Exception:
            _LOGGER.exception(f"{DOMAIN} - Push status callback error")

    def _vehicle_id_for_push(self, mqtt_vehicle_id: str) -> str | None:
        """Map the push-level vehicle id to a ``self.vehicles`` key.

        ``self.vehicles`` is keyed by ``vehicle.id``; push messages carry
        the topic-level id. Multi-vehicle safe: no manager-side
        per-vehicle state — the identity lives on each Vehicle.
        """
        for vehicle_id, vehicle in self.vehicles.items():
            if vehicle.mqtt_vehicle_id and vehicle.mqtt_vehicle_id == mqtt_vehicle_id:
                return vehicle_id
        return None

    def stop_push(self) -> None:
        """Disconnect push and reset orchestration state."""
        if self._push_reconnect_cancel:
            self._push_reconnect_cancel.set()
            self._push_reconnect_cancel = None
        if self._push_client:
            try:
                self._push_client.disconnect()
            except Exception:
                _LOGGER.debug("Push disconnect failed (already disconnected)")
            self._push_client = None
        self._push_action_events.clear()
        self._push_action_results.clear()
        self._push_vehicle_ids.clear()

    @property
    def is_push_connected(self) -> bool:
        """Whether the push transport is connected."""
        return self._push_client is not None and self._push_client.is_connected

    def set_push_status_callback(
        self, callback: Callable[[str, str], None] | None = None
    ) -> None:
        """Register a callback for status pushes: (vehicle_id, topic_type)."""
        self._push_status_callback = callback

    def set_push_connection_change_callback(
        self, callback: Callable[[bool], None] | None = None
    ) -> None:
        """Register a callback for push connection changes: (connected: bool)."""
        self._push_connection_change_callback = callback
        if self._push_client:
            self._push_client.set_on_connection_change(callback)

    def get_push_connection_state(self) -> str | None:
        """Push connection state through the region API (delegator)."""
        state = self.api.get_push_connection_state(self.token)
        return str(state) if state is not None else None

    def wait_for_push_result(self, action_id: str, timeout: float = 60.0) -> str | None:
        """Wait for an action-result push (designed for asyncio.to_thread callers)."""
        event = threading.Event()
        self._push_action_events[action_id] = event
        try:
            if event.wait(timeout=timeout):
                return self._push_action_results.pop(action_id, None)
            _LOGGER.debug(f"{DOMAIN} - Push wait timed out for action {action_id}")
            return None
        finally:
            self._push_action_events.pop(action_id, None)

    # Reconnect timing (overridable in tests)
    _push_reconnect_initial_delay: float = 5.0
    _push_reconnect_max_delay: float = 300.0

    def _on_push_disconnect(self, reason: str) -> None:
        """Reconnect with capped exponential backoff on unexpected drops.

        Called from the paho background thread. Clean disconnects are not
        retried (that is stop_push()).
        """
        _LOGGER.debug(f"{DOMAIN} - Push disconnected: {reason}")
        if reason == "clean disconnect":
            return
        if not self._push_vehicle_ids:
            return
        if self._push_reconnect_cancel:
            self._push_reconnect_cancel.set()
        self._cleanup_push_client()
        cancel_event = threading.Event()
        self._push_reconnect_cancel = cancel_event

        initial_delay = self._push_reconnect_initial_delay
        max_delay = self._push_reconnect_max_delay
        vehicle_ids = list(self._push_vehicle_ids)

        def _reconnect_with_backoff() -> None:
            delay = initial_delay
            while not cancel_event.is_set():
                _LOGGER.debug(f"{DOMAIN} - Push reconnect attempt in {delay}s...")
                if cancel_event.wait(delay):
                    return
                try:
                    try:
                        self.check_and_refresh_token()
                    except Exception as token_ex:
                        _LOGGER.warning(
                            f"{DOMAIN} - Push reconnect token refresh failed: {token_ex}"
                        )
                    self._push_client = None
                    ok = all(self.start_push(vid) for vid in vehicle_ids)
                    if ok and self.is_push_connected:
                        _LOGGER.debug(f"{DOMAIN} - Push reconnected")
                        return
                except Exception as ex:
                    _LOGGER.warning(f"{DOMAIN} - Push reconnect failed: {ex}")
                delay = min(delay * 2, max_delay)
            _LOGGER.debug(f"{DOMAIN} - Push reconnect cancelled")

        threading.Thread(
            target=_reconnect_with_backoff, name="push-reconnect", daemon=True
        ).start()

    def _cleanup_push_client(self) -> None:
        old_client = self._push_client
        if old_client is not None:
            try:
                old_client.disconnect()
            except Exception:
                _LOGGER.debug("Push disconnect during cleanup failed (already down)")
            self._push_client = None

    @staticmethod
    def get_implementation_by_region_brand(
        region: int, brand: int, language: str
    ) -> ApiImpl:
        if REGIONS[region] == REGION_CANADA:
            return KiaUvoApiCA(region, brand, language)
        elif REGIONS[region] == REGION_EUROPE:
            return KiaUvoApiEU(region, brand, language)
        elif REGIONS[region] == REGION_EUROPE_CCI:
            if BRANDS[brand] == BRAND_KIA:
                return KiaCciApiEU(region, brand, language)
            if BRANDS[brand] == BRAND_GENESIS:
                raise APIError("Genesis EU CCI/GSPA is not supported yet")
            return HyundaiCciApiEU(region, brand, language)
        elif REGIONS[region] == REGION_USA and (
            BRANDS[brand] == BRAND_HYUNDAI or BRANDS[brand] == BRAND_GENESIS
        ):
            return HyundaiBlueLinkApiUSA(region, brand, language)
        elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_KIA:
            return KiaUvoApiUSA(region, brand, language)
        elif REGIONS[region] == REGION_CHINA:
            return KiaUvoApiCN(region, brand, language)
        elif REGIONS[region] == REGION_AUSTRALIA:
            return KiaUvoApiAU(region, brand, language)
        elif REGIONS[region] == REGION_NZ:
            if BRANDS[brand] == BRAND_KIA:
                return KiaUvoApiAU(region, brand, language)
            else:
                raise APIError(
                    f"Unknown brand {BRANDS[brand]} for region {REGIONS[region]}"
                )
        elif REGIONS[region] == REGION_INDIA:
            return KiaUvoApiIN(brand)
        elif REGIONS[region] == REGION_BRAZIL:
            return HyundaiBlueLinkApiBR(region, brand, language)
        else:
            raise APIError(f"Unknown region {region}")
