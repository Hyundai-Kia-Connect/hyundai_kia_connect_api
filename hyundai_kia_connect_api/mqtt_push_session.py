"""MqttPushSession — push lifecycle over the ApiImpl contract (region-neutral).

Holds the receive-only MQTT transport, the started vehicle ids, callbacks and
the reconnect loop; speaks to the region API only through the push contract.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from . import const
from .const import DOMAIN
from .mqtt_client import MqttTransport
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class MqttPushSession:
    """Transport lifecycle for one VehicleManager's push fleet.

    Holds the receive-only MQTT transport, the started vehicle ids, the
    registered callbacks and the reconnect loop. ``VehicleManager`` stays
    the pass-through surface; all lifecycle logic lives here. Dependencies
    arrive from the manager: the region API (this module's mixin), the
    token accessor, the per-vehicle lookups and the fleet map.
    """

    def __init__(
        self,
        api: Any,
        token_fn: Callable[[], Token],
        refresh_fn: Callable[[], object],
        get_vehicle: Callable[[str], Vehicle],
        vehicles: Callable[[], dict[str, Vehicle]],
    ) -> None:
        self._api = api
        self._get_token = token_fn
        self._refresh_fn = refresh_fn
        self._get_vehicle = get_vehicle
        self._vehicles = vehicles
        self._client: Any = None
        self._vehicle_ids: set[str] = set()
        self._status_callback: Callable[[str, str], None] | None = None
        self._connection_change_callback: Callable[[bool], None] | None = None
        self._reconnect_cancel: threading.Event | None = None
        self._reconnect_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """Whether the push transport is connected."""
        return self._client is not None and self._client.is_connected

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        self._status_callback = callback

    def set_connection_change_callback(
        self, callback: Callable[[bool], None] | None
    ) -> None:
        self._connection_change_callback = callback
        if self._client is not None:
            self._client.set_on_connection_change(callback)

    def ensure_started(self, vehicle_id: str) -> bool:
        """Connect to the push broker and subscribe the vehicle's topics.

        Setup order (app-confirmed): broker → register client → vehicle
        identity → register vehicle → transport connect → subscribe.
        Returns False when push is unavailable (no broker info, rejected
        registration); ``APIError`` propagates and transport failures
        surface through the callbacks.
        """
        token = self._get_token()
        vehicle = self._get_vehicle(vehicle_id)
        if self._client is not None:
            if not self._register_new_vehicle(vehicle, token):
                return False
            self._vehicle_ids.add(vehicle_id)
            if self._client.is_connected:
                self._subscribe_vehicle_topics(vehicle_id)
            return True

        broker = self._api.get_push_broker_info(token)
        if not broker or not broker.get("mqtt_host"):
            _LOGGER.debug(f"{DOMAIN} - Push broker info not available")
            return False
        reg = self._api.register_push_client(token)
        if not reg:
            _LOGGER.debug(f"{DOMAIN} - Push client registration failed")
            return False
        creds = self._api.get_push_credentials(token)

        if not self._register_new_vehicle(vehicle, token):
            return False
        self._vehicle_ids.add(vehicle_id)

        client = MqttTransport(
            on_message=self._on_push_delivery_raw,
            on_connect=self._on_push_connect,
            on_disconnect=self._on_push_disconnect,
        )
        client.configure(
            broker_host=broker["mqtt_host"],
            broker_port=broker.get("mqtt_port", 8883),
            client_id=reg.get("client_id"),
            broker_username=creds[0] if creds else None,
            broker_password=creds[1] if creds else None,
        )
        if self._connection_change_callback:
            client.set_on_connection_change(self._connection_change_callback)
        # Set before connect(): the CONNACK callback can arrive first (1332).
        self._client = client
        try:
            client.connect()
        except (ValueError, OSError):
            # Programming-level failure — propagate, do not retry;
            # roll the vehicle id and the transport back.
            self._client = None
            self._vehicle_ids.discard(vehicle_id)
            raise
        if client.is_connected:
            self._subscribe_vehicle_topics(vehicle_id)
        return True

    def _register_new_vehicle(self, vehicle: Vehicle, token: Token) -> bool:
        """Run the per-vehicle registration steps of the push contract."""
        self._api.get_push_vehicle_identity(self._get_token(), vehicle)
        if not vehicle.mqtt_vehicle_id:
            _LOGGER.warning(
                f"{DOMAIN} - Push identity missing vehicle id for {vehicle.id}"
            )
            return False
        registered = self._api.register_push_vehicle(self._get_token(), vehicle)
        if registered is None:
            _LOGGER.warning(
                f"{DOMAIN} - Push protocol registration failed for {vehicle.id}"
            )
            return False
        return True

    def _subscribe_vehicle_topics(self, vehicle_id: str) -> None:
        """Subscribe the topics the region API provides for the vehicle.

        Local computation only — no HTTP; a failure here is a bug.
        """
        assert self._client is not None
        vehicle = self._get_vehicle(vehicle_id)
        topics = self._api.get_push_topics(self._get_token(), vehicle)
        if topics:
            self._client.subscribe(topics)

    def _on_push_connect(self) -> None:
        """Subscribe topics for each started push vehicle on (re)connect."""
        for vehicle_id in list(self._vehicle_ids):
            if vehicle_id in self._vehicles():
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
        message = self._api.parse_push_message(topic, payload)
        if message is None:
            return
        self._on_push_message(message)

    def _on_push_message(self, message: Any) -> None:
        """Dispatch a parsed push message (duck-typed contract shape).

        Expected attributes: is_status (bool), vehicle_id (str, push
        level), topic_type (str). Messages other than status deliveries
        are recorded by the provider's parser and ignored here.
        """
        if not getattr(message, "is_status", False) or not self._status_callback:
            return
        vehicle_id = self._vehicle_id_for_push(getattr(message, "vehicle_id", ""))
        if vehicle_id is None:
            _LOGGER.debug(
                f"{DOMAIN} - Push vehicle id {message.vehicle_id} unknown, dropping"
            )
            return
        try:
            self._status_callback(vehicle_id, message.topic_type)
        except Exception:
            _LOGGER.exception(f"{DOMAIN} - Push status callback error")

    def _vehicle_id_for_push(self, mqtt_vehicle_id: str) -> str | None:
        """Map the push-level vehicle id to a fleet key.

        The fleet map is keyed by ``vehicle.id``; push messages carry the
        topic-level id. Multi-vehicle safe: no per-vehicle state — the
        identity lives on each Vehicle.
        """
        for vehicle_id, vehicle in self._vehicles().items():
            if vehicle.mqtt_vehicle_id and vehicle.mqtt_vehicle_id == mqtt_vehicle_id:
                return vehicle_id
        return None

    def stop(self) -> None:
        """Disconnect push and reset orchestration state."""
        if self._reconnect_cancel:
            self._reconnect_cancel.set()
            self._reconnect_cancel = None
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                _LOGGER.debug("Push disconnect failed (already disconnected)")
            self._client = None
        self._vehicle_ids.clear()

    def _on_push_disconnect(self, reason: str) -> None:
        """Reconnect with capped exponential backoff on unexpected drops.

        Called from the paho background thread: no cleanup here (loop_stop
        inside paho's own thread deadlocks); the reconnect thread owns it.
        Clean disconnects are not retried (that is stop()).
        """
        _LOGGER.debug(f"{DOMAIN} - Push disconnected: {reason}")
        if reason == "clean disconnect":
            return
        if not self._vehicle_ids:
            return
        if self._reconnect_cancel:
            self._reconnect_cancel.set()
        cancel_event = threading.Event()
        self._reconnect_cancel = cancel_event

        vehicle_ids = list(self._vehicle_ids)

        def _reconnect_with_backoff() -> None:
            delay = const.MQTT_PUSH_RECONNECT_INITIAL_DELAY
            while not cancel_event.is_set():
                _LOGGER.debug(f"{DOMAIN} - Push reconnect attempt in {delay}s...")
                if cancel_event.wait(delay):
                    return
                if cancel_event.is_set():
                    return
                # One owner per rebuild: the lock keeps a superseded
                # thread from dropping a client the newer thread built,
                # and a dead transport is disconnected here, not leaked.
                with self._reconnect_lock:
                    if cancel_event.is_set():
                        return
                    try:
                        try:
                            self._refresh_fn()
                        except Exception as token_ex:
                            _LOGGER.warning(
                                f"{DOMAIN} - Push reconnect token refresh failed: "
                                f"{token_ex}"
                            )
                        self._cleanup_client()
                        ok = all(self.ensure_started(vid) for vid in vehicle_ids)
                        if ok and self.is_connected:
                            _LOGGER.debug(f"{DOMAIN} - Push reconnected")
                            return
                    except Exception as ex:
                        _LOGGER.warning(f"{DOMAIN} - Push reconnect failed: {ex}")
                delay = min(delay * 2, const.MQTT_PUSH_RECONNECT_MAX_DELAY)
            _LOGGER.debug(f"{DOMAIN} - Push reconnect cancelled")

        threading.Thread(
            target=_reconnect_with_backoff, name="push-reconnect", daemon=True
        ).start()

    def _cleanup_client(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                _LOGGER.debug("Push disconnect during cleanup failed (already down)")
            self._client = None
