"""MqttPushSession — push lifecycle over the ApiImpl contract (region-neutral).

Holds the receive-only MQTT transport, the started vehicle ids, callbacks and
the reconnect loop; speaks to the region API only through the push contract.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import ExitStack
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
    arrive from the manager: the region API, the
    token accessor, the per-vehicle lookups and the fleet map.
    """

    def __init__(
        self,
        api: Any,
        token_fn: Callable[[], Token],
        refresh_fn: Callable[[], None],
        get_vehicle: Callable[[str], Vehicle],
        vehicles: Callable[[], dict[str, Vehicle]],
        after_close_fn: Callable[[], None] | None = None,
    ) -> None:
        self._api = api
        self._get_token = token_fn
        self._refresh_fn = refresh_fn
        self._get_vehicle = get_vehicle
        self._vehicles = vehicles
        self._after_close_fn = after_close_fn
        self._client: Any = None
        self._vehicle_ids: set[str] = set()
        self._status_callback: Callable[[str, str], None] | None = None
        self._connection_change_callback: Callable[[bool], None] | None = None
        self._reconnect_cancel: threading.Event | None = None
        self._reconnect_lock = threading.Lock()
        self._provider_lock = threading.RLock()
        self._providers: dict[str, Any] = {}
        self._provider_generation = 0
        self._closed = False
        self._subscribed_vehicle_ids: set[str] = set()

    @property
    def is_connected(self) -> bool:
        """Whether the push transport is connected."""
        with self._provider_lock:
            providers = list(self._providers.values())
        return any(provider.is_connected for provider in providers) or (
            self._client is not None and self._client.is_connected
        )

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        self._status_callback = callback

    def set_connection_change_callback(
        self, callback: Callable[[bool], None] | None
    ) -> None:
        self._connection_change_callback = callback
        with self._provider_lock:
            generation, client = self._provider_generation, self._client
        if client is not None:
            client.set_on_connection_change(
                lambda connected: self._report_connection_change(connected, generation)
            )

    def _report_connection_change(self, connected: bool, generation: int) -> None:
        callback = self._connection_change_callback
        if self._generation_active(generation) and callback is not None:
            callback(connected)

    def _generation_active(self, generation: int) -> bool:
        with self._provider_lock:
            return not self._closed and self._provider_generation == generation

    def ensure_started(self, vehicle_id: str) -> bool:
        """Register and connect unless stop or close invalidates this attempt."""
        with self._provider_lock:
            generation = self._provider_generation
            if self._closed:
                return False
        try:
            return self._ensure_started(vehicle_id, generation)
        finally:
            self._cleanup_closed_work()

    def _cleanup_closed_work(self) -> None:
        if self._closed and self._after_close_fn is not None:
            try:
                self._after_close_fn()
            except Exception as err:
                _LOGGER.warning(
                    "%s - Push late shutdown failed (%s)",
                    DOMAIN,
                    type(err).__name__[:64],
                )

    def _attempt_active(self, generation: int, cancel: threading.Event | None) -> bool:
        return self._generation_active(generation) and (
            cancel is None or not cancel.is_set()
        )

    def _ensure_started(
        self, vehicle_id: str, generation: int, cancel: threading.Event | None = None
    ) -> bool:
        token = self._get_token()
        vehicle = self._get_vehicle(vehicle_id)
        factory = getattr(self._api, "get_push_provider", None)
        provider = factory(token, vehicle) if callable(factory) else self._api
        if not self._attempt_active(generation, cancel):
            return False
        starter = getattr(provider, "start_push", None)
        if callable(starter):
            return self._start_provider(vehicle_id, provider, generation, cancel)
        if self._client is not None:
            if not self._register_new_vehicle(vehicle, token, generation, cancel):
                return False
            with self._provider_lock:
                if not self._attempt_active(generation, cancel):
                    return False
                self._vehicle_ids.add(vehicle_id)
            if self._client is not None and self._client.is_connected:
                self._subscribe_vehicle_topics(vehicle_id)
            return self._attempt_active(generation, cancel)

        broker = self._api.get_push_broker_info(token)
        if not self._attempt_active(generation, cancel):
            return False
        if not broker or not broker.get("mqtt_host"):
            _LOGGER.debug(f"{DOMAIN} - Push broker info not available")
            return False
        reg = self._api.register_push_client(token)
        if not self._attempt_active(generation, cancel):
            return False
        if not reg:
            _LOGGER.debug(f"{DOMAIN} - Push client registration failed")
            return False
        creds = self._api.get_push_credentials(token)
        if not self._attempt_active(generation, cancel):
            return False
        if not self._register_new_vehicle(vehicle, token, generation, cancel):
            return False

        def on_message(topic: str, payload: bytes) -> None:
            if self._attempt_active(generation, cancel):
                self._on_push_delivery_raw(topic, payload)

        def on_connect() -> None:
            if self._attempt_active(generation, cancel):
                self._on_push_connect()

        def on_disconnect(reason: str) -> None:
            if self._attempt_active(generation, cancel):
                self._on_push_disconnect(reason, generation)

        client = MqttTransport(
            on_message=on_message, on_connect=on_connect, on_disconnect=on_disconnect
        )
        client.configure(
            broker_host=broker["mqtt_host"],
            broker_port=broker.get("mqtt_port", 8883),
            client_id=reg.get("client_id"),
            broker_username=creds[0] if creds else None,
            broker_password=creds[1] if creds else None,
        )
        if self._connection_change_callback:
            client.set_on_connection_change(
                lambda connected: (
                    self._connection_change_callback(connected)
                    if self._attempt_active(generation, cancel)
                    and self._connection_change_callback
                    else None
                )
            )
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                return False
            self._vehicle_ids.add(vehicle_id)
            self._client = client
        try:
            client.connect()
        except (ValueError, OSError):
            with self._provider_lock:
                if self._client is client:
                    self._vehicle_ids.discard(vehicle_id)
                    self._client = None
            raise
        finally:
            if not self._attempt_active(generation, cancel):
                client.disconnect()
                with self._provider_lock:
                    if self._client is client:
                        self._client = None
        if not self._attempt_active(generation, cancel):
            return False
        if client.is_connected:
            self._subscribe_vehicle_topics(vehicle_id)
        return self._attempt_active(generation, cancel)

    def _start_provider(
        self,
        vehicle_id: str,
        provider: Any,
        generation: int,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Attach a provider-owned session instead of creating another client."""
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                return False
            previous = self._providers.get(vehicle_id)
            self._providers[vehicle_id] = provider
            self._vehicle_ids.add(vehicle_id)
        if previous is not None and previous is not provider:
            previous.stop_push()

        def status(changed_vehicle: str, kind: str) -> None:
            with self._provider_lock:
                current = (
                    self._attempt_active(generation, cancel)
                    and self._providers.get(vehicle_id) is provider
                )
            if current and changed_vehicle == vehicle_id and self._status_callback:
                self._status_callback(vehicle_id, kind)

        def connection(connected: bool) -> None:
            with self._provider_lock:
                current = (
                    self._attempt_active(generation, cancel)
                    and self._providers.get(vehicle_id) is provider
                )
            if not current:
                return
            if self._connection_change_callback:
                self._connection_change_callback(connected)
            if not connected:
                self._on_push_disconnect("provider disconnected", generation)

        try:
            started = bool(provider.start_push(status, connection))
        except Exception:
            with self._provider_lock:
                stopped = not self._attempt_active(generation, cancel)
                if self._providers.get(vehicle_id) is provider:
                    self._providers.pop(vehicle_id, None)
                    self._vehicle_ids.discard(vehicle_id)
            provider.stop_push()
            if stopped:
                return False
            raise
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                if self._providers.get(vehicle_id) is provider:
                    self._providers.pop(vehicle_id, None)
                    self._vehicle_ids.discard(vehicle_id)
                stopped = True
            else:
                stopped = False
        if stopped:
            provider.stop_push()
            return False
        return started

    def _register_new_vehicle(
        self,
        vehicle: Vehicle,
        token: Token,
        generation: int,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Run the per-vehicle registration steps of the push contract."""
        self._api.get_push_vehicle_identity(self._get_token(), vehicle)
        if not self._attempt_active(generation, cancel):
            return False
        if not vehicle.mqtt_vehicle_id:
            _LOGGER.warning(
                f"{DOMAIN} - Push identity missing vehicle id for {vehicle.id}"
            )
            return False
        registered = self._api.register_push_vehicle(self._get_token(), vehicle)
        if not self._attempt_active(generation, cancel):
            return False
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
        with self._provider_lock:
            if (
                self._closed
                or self._client is None
                or vehicle_id in self._subscribed_vehicle_ids
            ):
                return
            assert self._client is not None
            vehicle = self._get_vehicle(vehicle_id)
            topics = self._api.get_push_topics(self._get_token(), vehicle)
            if topics:
                self._client.subscribe(topics)
                self._subscribed_vehicle_ids.add(vehicle_id)

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

    def close(self) -> None:
        """Permanently stop delivery and reject future startup attempts."""
        with self._provider_lock:
            if self._closed:
                return
            self._closed = True
        self.stop()

    def stop(self) -> None:
        """Disconnect push while allowing a later explicit restart."""
        with self._provider_lock:
            self._provider_generation += 1
            providers = list(self._providers.values())
            self._providers.clear()
            self._vehicle_ids.clear()
            self._subscribed_vehicle_ids.clear()
            cancel, self._reconnect_cancel = self._reconnect_cancel, None
            client, self._client = self._client, None
        if cancel is not None:
            cancel.set()
        try:
            with ExitStack() as cleanup:
                for provider in providers:
                    cleanup.callback(provider.stop_push)
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    _LOGGER.debug("Push disconnect failed (already disconnected)")

    def _on_push_disconnect(
        self, reason: str, expected_generation: int | None = None
    ) -> None:
        """Reconnect after unexpected drops unless stop or close invalidates it."""
        _LOGGER.debug(f"{DOMAIN} - Push disconnected: {reason}")
        with self._provider_lock:
            if (
                self._closed
                or reason == "clean disconnect"
                or not self._vehicle_ids
                or (
                    expected_generation is not None
                    and expected_generation != self._provider_generation
                )
            ):
                return
            generation = self._provider_generation
            self._subscribed_vehicle_ids.clear()
            previous_cancel = self._reconnect_cancel
            cancel_event = threading.Event()
            self._reconnect_cancel = cancel_event
            vehicle_ids = list(self._vehicle_ids)
        if previous_cancel is not None:
            previous_cancel.set()

        def _reconnect_with_backoff() -> None:
            with ExitStack() as cleanup:
                cleanup.callback(self._cleanup_closed_work)
                delay = const.MQTT_PUSH_RECONNECT_INITIAL_DELAY
                while not cancel_event.is_set():
                    _LOGGER.debug(f"{DOMAIN} - Push reconnect attempt in {delay}s...")
                    if cancel_event.wait(delay):
                        return
                    with self._reconnect_lock:
                        if not self._attempt_active(generation, cancel_event):
                            return
                        try:
                            try:
                                self._refresh_fn()
                            except Exception as token_ex:
                                _LOGGER.warning(
                                    "%s - Push reconnect token refresh failed (%s)",
                                    DOMAIN,
                                    type(token_ex).__name__[:64],
                                )
                            if cancel_event.is_set() or not self._generation_active(
                                generation
                            ):
                                return
                            if not self._cleanup_client(generation):
                                return
                            ok = all(
                                not cancel_event.is_set()
                                and self._generation_active(generation)
                                and self._ensure_started(vid, generation, cancel_event)
                                for vid in vehicle_ids
                            )
                            if ok and self.is_connected:
                                _LOGGER.debug(f"{DOMAIN} - Push reconnected")
                                return
                        except Exception as ex:
                            _LOGGER.warning(
                                "%s - Push reconnect failed (%s)",
                                DOMAIN,
                                type(ex).__name__[:64],
                            )
                    delay = min(delay * 2, const.MQTT_PUSH_RECONNECT_MAX_DELAY)
                _LOGGER.debug(f"{DOMAIN} - Push reconnect cancelled")

        threading.Thread(
            target=_reconnect_with_backoff, name="push-reconnect", daemon=True
        ).start()

    def _cleanup_client(self, generation: int) -> bool:
        with self._provider_lock:
            if not self._generation_active(generation):
                return False
            client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                _LOGGER.debug("Push disconnect during cleanup failed (already down)")
        return True
