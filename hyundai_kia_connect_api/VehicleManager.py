"""VehicleManager.py"""

# pylint:disable=logging-fstring-interpolation,missing-class-docstring,missing-function-docstring,line-too-long,invalid-name

import datetime as dt
import logging
import re
import threading
from collections.abc import Callable
from datetime import timedelta

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
        self.vehicles: dict = {}
        self.otp_request: OTPRequest = None
        # MQTT client and action tracking
        self._mqtt_client = None
        self._mqtt_action_events: dict[str, threading.Event] = {}
        self._mqtt_action_results: dict[str, str] = {}
        self._mqtt_status_callback: Callable | None = None
        self._mqtt_connection_change_callback: Callable[[bool], None] | None = None
        self._mqtt_reconnect_cancel: threading.Event | None = None
        self._mqtt_vehicle_id: str | None = None

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
    # MQTT Service Hub methods
    # ------------------------------------------------------------------

    def get_mqtt_host(self) -> dict | None:
        """Get MQTT broker host/port from Service Hub."""
        return self.api.get_mqtt_host(self.token)

    def register_mqtt_client(self) -> dict | None:
        """Register device for MQTT push notifications."""
        return self.api.register_mqtt_client(self.token)

    def register_mqtt_protocol(self, vehicle_id: str) -> dict | None:
        """Register MQTT protocol for a vehicle."""
        return self.api.register_mqtt_protocol(self.token, self.get_vehicle(vehicle_id))

    def get_mqtt_metadata(self, vehicle_id: str) -> dict | None:
        """Get MQTT metadata for a vehicle from Service Hub."""
        return self.api.get_mqtt_metadata(self.token, self.get_vehicle(vehicle_id))

    def get_mqtt_vehicle_id(self, vehicle_id: str) -> str | None:
        """Get MQTT vehicle ID from Service Hub."""
        return self.api.get_mqtt_vehicle_id(self.token, self.get_vehicle(vehicle_id))

    def get_mqtt_connection_state(self) -> str | None:
        """Check MQTT connection state: 'ONLINE', 'OFFLINE', or 'UNKNOWN'."""
        return self.api.get_mqtt_connection_state(self.token)

    # ------------------------------------------------------------------
    # MQTT orchestration
    # ------------------------------------------------------------------

    def start_mqtt(self, vehicle_id: str):
        """Connect to MQTT broker and subscribe to vehicle topics.

        Full flow:
        1. get_mqtt_host() → broker config (host, port, SSL)
        2. register_mqtt_client() → device registration (clientId, credentials)
        3. register_mqtt_protocol() → register protocol IDs
        4. Configure and connect HyundaiMqttClient
        5. Subscribe to topics based on vehicle capabilities

        Returns the connected HyundaiMqttClient, or None on failure.
        Gracefully handles missing paho-mqtt (ImportError).
        """
        try:
            from .mqtt_client import (
                HyundaiMqttClient,
                MqttCacheCapabilities,
            )
        except ImportError:
            _LOGGER.warning(
                f"{DOMAIN} - paho-mqtt not installed, MQTT unavailable. "
                "Install with: pip install hyundai_kia_connect_api[mqtt]"
            )
            return None

        vehicle = self.get_vehicle(vehicle_id)

        # Step 1: Get broker config
        host_info = self.get_mqtt_host()
        if not host_info or not host_info.get("mqtt_host"):
            _LOGGER.warning(f"{DOMAIN} - MQTT broker host not available")
            return None
        _LOGGER.info(
            f"{DOMAIN} - MQTT step 1 OK: broker={host_info.get('mqtt_host')}:{host_info.get('mqtt_port')}, ssl={host_info.get('use_ssl')}"
        )

        # Step 2: Register device
        reg_info = self.register_mqtt_client()
        if not reg_info:
            _LOGGER.warning(f"{DOMAIN} - MQTT device registration failed")
            return None
        _LOGGER.info(
            f"{DOMAIN} - MQTT step 2 OK: client_id={reg_info.get('client_id')}, username={'yes' if reg_info.get('username') else 'no'}, password={'yes' if reg_info.get('password') else 'no'}"
        )

        # Step 2b: Get vehicle metadata and MQTT vehicle ID
        # The app calls these before protocol registration
        meta = self.get_mqtt_metadata(vehicle_id)
        if meta:
            _LOGGER.info(f"{DOMAIN} - MQTT metadata retrieved for vehicle {vehicle_id}")
        else:
            _LOGGER.debug(f"{DOMAIN} - MQTT metadata not available, continuing")

        mqtt_vid = self.get_mqtt_vehicle_id(vehicle_id)
        if mqtt_vid:
            _LOGGER.info(f"{DOMAIN} - MQTT vehicle ID: {mqtt_vid}")
        else:
            _LOGGER.debug(f"{DOMAIN} - MQTT vehicle ID not available, using car ID")

        # Step 3: Register protocol
        self.register_mqtt_protocol(vehicle_id)
        _LOGGER.info(f"{DOMAIN} - MQTT step 3 OK: protocol registered")

        # Step 4: Configure and connect
        # Cancel any previous reconnect attempts
        if self._mqtt_reconnect_cancel:
            self._mqtt_reconnect_cancel.set()

        client = HyundaiMqttClient(
            on_message=self._on_mqtt_message,
            on_connect=lambda: _LOGGER.info(f"{DOMAIN} - MQTT connected"),
            on_disconnect=lambda reason: self._on_mqtt_disconnect(reason),
        )

        broker_host = host_info["mqtt_host"]
        # MQTT broker uses the same hostname but different port (443 or 8883)
        broker_port = host_info.get("mqtt_port", 8883)
        # Strip :31010 from REST URL — MQTT uses port 443/8883
        if broker_host and ":31010" in broker_host:
            broker_host = broker_host.replace(":31010", "")

        # MQTT credentials: registration response may include username/password.
        # If not, fall back to CCS token as password (common pattern in
        # automotive MQTT brokers) and client_id as username.
        mqtt_username = reg_info.get("username")
        mqtt_password = reg_info.get("password")
        mqtt_client_id = reg_info.get("client_id")

        if not mqtt_username:
            # Fallback: use clientId as MQTT username
            mqtt_username = mqtt_client_id
            _LOGGER.info(
                f"{DOMAIN} - MQTT username not in register response, "
                f"using clientId as username"
            )
        if not mqtt_password:
            # Fallback: use access token (stripped of "Bearer " prefix) as
            # MQTT password. The app's MQTT repository resolves it as:
            #   String accessToken = SDKAuthenticationContract.INSTANCE.getAccessToken();
            #   return accessToken.replace("Bearer ", "");
            # Previously tried ccId which caused rc=128 (broker disconnect).
            # Access token with "Bearer " stripped matches the app behavior.
            access_token = self.token.ccs_token or self.token.access_token or ""
            access_token = access_token.removeprefix("Bearer ").strip()
            if access_token:
                mqtt_password = access_token
                _LOGGER.info(
                    f"{DOMAIN} - MQTT password not in register response, "
                    f"using access token as password"
                )
            else:
                _LOGGER.warning(
                    f"{DOMAIN} - MQTT password not in register response "
                    f"and no access token available — connecting without password"
                )

        _LOGGER.info(
            f"{DOMAIN} - MQTT credentials: client_id={mqtt_client_id}, "
            f"username={'present' if mqtt_username else 'absent'}, "
            f"password={'present' if mqtt_password else 'absent'}"
        )

        client.configure(
            broker_host=broker_host,
            broker_port=broker_port,
            use_ssl=host_info.get("use_ssl", True),
            client_id=mqtt_client_id,
            username=mqtt_username,
            password=mqtt_password,
        )
        _LOGGER.info(
            f"{DOMAIN} - MQTT step 4a OK: configure() done, "
            f"broker={broker_host}:{broker_port}, "
            f"user={'yes' if mqtt_username else 'no'}:{len(mqtt_password or '') if mqtt_password else 0}chars"
        )

        # Register connection change callback before connecting so we
        # catch the initial connection event
        if self._mqtt_connection_change_callback:
            client.set_on_connection_change(self._mqtt_connection_change_callback)

        try:
            client.connect()
            _LOGGER.info(f"{DOMAIN} - MQTT step 4b OK: connect_async() called")
        except Exception as ex:
            _LOGGER.warning(f"{DOMAIN} - MQTT connection failed: {ex}")
            return None

        self._mqtt_client = client
        # Store the MQTT topic vehicle ID (mqtt_vid from get_mqtt_vehicle_id),
        # NOT the carId — MQTT topic messages (CarStatus/Status, etc.) carry
        # mqtt_vid as the topic infix, and _vehicle_id_for_mqtt compares against
        # this to resolve mqtt_vid → vehicle.id. Storing carId here caused
        # "MQTT vehicle_id unknown, skipping refresh" (mqtt_vid ≠ carId).
        self._mqtt_vehicle_id = mqtt_vid or vehicle_id
        _LOGGER.info(
            f"{DOMAIN} - MQTT client configured for vehicle {vehicle_id} (mqtt_vid={self._mqtt_vehicle_id})"
        )

        # Step 5: Subscribe (deferred — topics are subscribed after
        # on_connect callback fires, since connect_async is used)
        # We store the vehicle info for subscribe-on-connect
        mqtt_client_id = reg_info.get("client_id") or self.token.mqtt_client_id
        hu_client_id = vehicle.hu_client_id

        # CarStatus/OTA topics use the MQTT vehicle ID (from vehicleId endpoint)
        # as the topic infix, NOT the carId (app's vehicle-id cache).
        topic_vehicle_id = mqtt_vid or vehicle_id

        # Build capabilities from vehicle fields
        caps = MqttCacheCapabilities(
            has_hvac_close_remote=vehicle.has_hvac_close_remote,
            has_media_close_remote=vehicle.has_media_close_remote,
            is_support_speed_event=vehicle.is_support_speed_event,
            is_support_ota_progress=vehicle.is_support_ota_progress,
            is_support_schedule_update=vehicle.is_support_schedule_update,
            ccu_client_id=mqtt_client_id,
            hu_client_id=hu_client_id,
            vehicle_id=topic_vehicle_id,
            client_id=mqtt_client_id,
        )

        # Subscribe will be called after connection is established.
        # For now, subscribe immediately if already connected (unlikely with
        # async connect, but covers the case).
        if client.is_connected:
            client.subscribe_topics(
                vehicle_id=topic_vehicle_id,
                client_id=mqtt_client_id,
                hu_client_id=hu_client_id,
                capabilities=caps,
            )
        else:
            # Subscribe on connect — use the on_connect callback
            original_on_connect = client._on_connect

            def _subscribe_on_connect():
                client.subscribe_topics(
                    vehicle_id=topic_vehicle_id,
                    client_id=mqtt_client_id,
                    hu_client_id=hu_client_id,
                    capabilities=caps,
                )
                if original_on_connect:
                    original_on_connect()

            client._on_connect = _subscribe_on_connect

        return client

    # Reconnect timing defaults (overridable for tests)
    _mqtt_reconnect_initial_delay: float = 5.0  # seconds
    _mqtt_reconnect_max_delay: float = 300.0  # seconds (5 min cap)

    def _on_mqtt_disconnect(self, reason: str) -> None:
        """Handle MQTT disconnection with exponential backoff reconnect.

        Called from paho-mqtt background thread — must be thread-safe.
        Clean disconnects (rc=0) are not retried (e.g. explicit stop_mqtt).
        Unexpected disconnects trigger continuous reconnect with capped backoff.
        """
        _LOGGER.warning(f"{DOMAIN} - MQTT disconnected: {reason}")

        # Clean disconnect — don't reconnect (e.g. explicit stop_mqtt call)
        if reason == "clean disconnect":
            return

        vehicle_id = self._mqtt_vehicle_id
        if not vehicle_id:
            return

        # Cancel any previous reconnect attempt
        if self._mqtt_reconnect_cancel:
            self._mqtt_reconnect_cancel.set()

        # Stop and clean up the old client before reconnecting
        self._cleanup_mqtt_client()

        cancel_event = threading.Event()
        self._mqtt_reconnect_cancel = cancel_event

        initial_delay = self._mqtt_reconnect_initial_delay
        max_delay = self._mqtt_reconnect_max_delay

        def _reconnect_with_backoff():
            delay = initial_delay
            attempt = 0

            while not cancel_event.is_set():
                attempt += 1
                _LOGGER.info(
                    f"{DOMAIN} - MQTT reconnect attempt #{attempt} in {delay}s..."
                )
                if cancel_event.wait(delay):
                    return
                try:
                    # Refresh tokens before reconnect — Service Hub calls
                    # require valid CCS tokens which may have expired
                    try:
                        self.check_and_refresh_token()
                    except Exception as token_ex:
                        _LOGGER.warning(
                            f"{DOMAIN} - MQTT reconnect token refresh failed: "
                            f"{token_ex}, will retry"
                        )

                    client = self.start_mqtt(vehicle_id)
                    if client and client.is_connected:
                        _LOGGER.info(f"{DOMAIN} - MQTT reconnected successfully")
                        return
                except Exception as ex:
                    _LOGGER.warning(f"{DOMAIN} - MQTT reconnect failed: {ex}")

                # Exponential backoff: 5 → 10 → 20 → 40 → 80 → 160 → 300 → 300...
                delay = min(delay * 2, max_delay)

            _LOGGER.info(f"{DOMAIN} - MQTT reconnect cancelled by stop_mqtt")

        reconnect_thread = threading.Thread(
            target=_reconnect_with_backoff,
            name="mqtt-reconnect",
            daemon=True,
        )
        reconnect_thread.start()

    def _cleanup_mqtt_client(self) -> None:
        """Stop and clean up the current MQTT client.

        Called before reconnect attempts to ensure the old paho-mqtt
        loop is stopped and the client object is cleared.
        """
        old_client = self._mqtt_client
        if old_client is not None:
            try:
                old_client.disconnect()
            except Exception:
                _LOGGER.debug(
                    "MQTT disconnect on reconnect failed (already disconnected)"
                )
            self._mqtt_client = None

    def stop_mqtt(self):
        """Disconnect and clean up the MQTT client."""
        # Cancel any pending reconnect attempts
        if self._mqtt_reconnect_cancel:
            self._mqtt_reconnect_cancel.set()
            self._mqtt_reconnect_cancel = None

        self._cleanup_mqtt_client()
        _LOGGER.info(f"{DOMAIN} - MQTT client disconnected")
        self._mqtt_action_events.clear()
        self._mqtt_action_results.clear()
        self._mqtt_vehicle_id = None

    @property
    def mqtt_client(self):
        """The active MQTT client, or None if not connected."""
        return self._mqtt_client

    @property
    def is_mqtt_connected(self) -> bool:
        """Whether the MQTT client is connected to the broker."""
        return self._mqtt_client is not None and self._mqtt_client.is_connected

    def set_mqtt_status_callback(
        self, callback: Callable[[str, str], None] | None = None
    ):
        """Register a callback for CarStatus MQTT messages.

        Called from paho-mqtt background thread when vehicle status
        updates arrive. The callback receives (vehicle_id, topic_type).

        Set to None to unregister. Thread-safe.
        """
        self._mqtt_status_callback = callback

    def set_mqtt_connection_change_callback(
        self, callback: Callable[[bool], None] | None = None
    ):
        """Register a callback for MQTT connection state changes.

        Called from paho-mqtt background thread when the connection
        state changes. The callback receives a bool (True = connected).

        Set to None to unregister. Thread-safe.
        """
        self._mqtt_connection_change_callback = callback

    def _vehicle_id_for_mqtt(self, mqtt_vehicle_id: str) -> str | None:
        """Map MQTT vehicle id → VehicleManager.vehicles key (vehicle.id).

        MQTT CarStatus messages carry the mqttVehicleId (from the Service Hub
        vehicleId endpoint) in the topic, but ``self.vehicles`` is keyed by
        ``vehicle.id`` — a different identifier. This resolves the mapping so
        the coordinator callback receives a key it can actually look up.

        Single-vehicle case: the one registered ``self._mqtt_vehicle_id``
        maps to the single vehicle in ``self.vehicles``.
        """
        if self._mqtt_vehicle_id and mqtt_vehicle_id == self._mqtt_vehicle_id:
            for vid in self.vehicles:
                return vid
        # future: multi-vehicle reverse map via Vehicle.mqtt field if added
        return None

    def _on_mqtt_message(self, message):
        """Route MQTT messages to waiting action handlers.

        Called from paho-mqtt background thread — must be thread-safe.
        """

        _LOGGER.debug(
            f"{DOMAIN} - MQTT message received: "
            f"{message.topic_group}/{message.topic_type}"
        )

        # CarStatus messages trigger a vehicle state refresh
        if message.topic_group == "CarStatus" and self._mqtt_status_callback:
            resolved = self._vehicle_id_for_mqtt(message.vehicle_id)
            if resolved is None:
                _LOGGER.debug(
                    f"{DOMAIN} - MQTT vehicle_id {message.vehicle_id} unknown, "
                    f"skipping refresh"
                )
                return
            try:
                self._mqtt_status_callback(resolved, message.topic_type)
            except Exception:
                _LOGGER.exception(f"{DOMAIN} - MQTT status callback error")

        # DeviceRemote/CarRemote Res messages carry command results
        if message.topic_type == "Res":
            payload = message.payload
            # Extract action/result tracking info from header
            header = payload.get("header", {})
            body = payload.get("body", {})
            res_code = body.get("resCode", header.get("resCode"))
            tid = header.get("tid") or header.get("tId")

            if tid and tid in self._mqtt_action_events:
                self._mqtt_action_results[tid] = (
                    str(res_code) if res_code is not None else "success"
                )
                self._mqtt_action_events[tid].set()
                _LOGGER.debug(
                    f"{DOMAIN} - MQTT result for tid={tid}: resCode={res_code}"
                )

        # Connect/Disconnect messages for CarRemote
        if message.topic_type == "Connect":
            header = message.payload.get("header", {})
            tid = header.get("tid") or header.get("tId")
            if tid and tid in self._mqtt_action_events:
                self._mqtt_action_results[tid] = "connected"
                self._mqtt_action_events[tid].set()

    def wait_for_mqtt_result(self, action_id: str, timeout: float = 60.0) -> str | None:
        """Wait for an MQTT result message for a given action_id.

        Blocks the calling thread until the result arrives or timeout expires.
        Returns the result code string, or None on timeout.

        This is designed to be called from asyncio.to_thread() in HA.
        """
        event = threading.Event()
        self._mqtt_action_events[action_id] = event

        try:
            if event.wait(timeout=timeout):
                result = self._mqtt_action_results.pop(action_id, None)
                return result
            else:
                _LOGGER.debug(f"{DOMAIN} - MQTT wait timed out for action {action_id}")
                return None
        finally:
            self._mqtt_action_events.pop(action_id, None)

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
