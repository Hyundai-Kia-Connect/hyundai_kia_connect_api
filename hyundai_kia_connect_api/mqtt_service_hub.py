"""Shared Service Hub phone-family wire contract (region-neutral).

Pure data, builders and parsers; regional flavor (headers, brand codes,
metadata semantics, the DeviceRemote/OTA family) stays in the region
classes. See hyundai_kia_connect_api#1332.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote

TOPIC_PREFIX_VSS = "service/phone/_/vss/"  # CarStatus.Status
TOPIC_PREFIX_CONNECTION = "service/phone/_/connection/"  # CarStatus.Connect
TOPIC_PREFIX_LOCATION = "service/phone/_/location/"  # CarStatus.Location

# Protocol IDs — sent as plain strings in the Service Hub body
MQTT_PROTOCOL_ID_VSS = "service.phone.vss"
MQTT_PROTOCOL_ID_CONNECTION = "service.phone.connection"
MQTT_PROTOCOL_ID_RES = "service.phone.res"
MQTT_PROTOCOL_ID_CCU_UPDATE = "statesync.vehicle.ccu.update"


@dataclass
class PushMessage:
    """Parsed Service Hub MQTT delivery — the push-message contract shape."""

    topic_group: str  # "CarStatus", "DeviceRemote", "CarOta", ...
    topic_type: str  # "Status", "Connect", "Res", ...
    vehicle_id: str  # push-level id (topic infix)
    payload: dict[str, Any]  # parsed JSON (header/body as delivered)
    is_status: bool = False


def build_car_status_topics(vehicle_id: str) -> list[str]:
    """Build CarStatus topics for a vehicle.

    The res topic is excluded — the broker rejects phone-client
    subscriptions to it with rc=128.
    """
    return [
        TOPIC_PREFIX_VSS + vehicle_id,
        TOPIC_PREFIX_CONNECTION + vehicle_id,
    ]


def classify_phone_topic(topic: str) -> tuple[str, str, str] | None:
    """Classify a phone-family topic into (group, type, infix).

    ``None`` when the topic is not phone-family (that is regional
    schema; the region classes classify their own topics).
    """
    if topic.startswith(TOPIC_PREFIX_VSS):
        return ("CarStatus", "Status", _extract_infix(topic, TOPIC_PREFIX_VSS))
    if topic.startswith(TOPIC_PREFIX_CONNECTION):
        return ("CarStatus", "Connect", _extract_infix(topic, TOPIC_PREFIX_CONNECTION))
    if topic.startswith(TOPIC_PREFIX_LOCATION):
        return ("CarStatus", "Location", _extract_infix(topic, TOPIC_PREFIX_LOCATION))
    return None


def _extract_infix(topic: str, prefix: str) -> str:
    """Extract the vehicle/client id infix from a topic after the prefix.

    The infix is the segment between the prefix and the next '/' (or the
    whole remainder for a trailing-slash topic).
    """
    after_prefix = topic[len(prefix) :]
    slash_pos = after_prefix.find("/")
    if slash_pos > 0:
        return after_prefix[:slash_pos]
    if after_prefix.endswith("/"):
        return after_prefix[:-1]
    return after_prefix


def decode_payload(payload: bytes) -> dict[str, Any]:
    """Decode a delivery payload: JSON, raw-hex fallback for non-JSON."""
    try:
        return cast(dict[str, Any], json.loads(payload.decode("utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": payload.hex()}


def parse_service_hub_message(topic: str, payload: bytes) -> PushMessage | None:
    """Parse a phone-family delivery; ``None`` for other topic schemas."""
    classification = classify_phone_topic(topic)
    if classification is None:
        return None
    topic_group, topic_type, vehicle_id = classification
    return PushMessage(
        topic_group=topic_group,
        topic_type=topic_type,
        vehicle_id=vehicle_id,
        payload=decode_payload(payload),
        is_status=topic_group == "CarStatus",
    )


def build_service_hub_url_with_params(
    base: str, path: str, params: dict[str, Any] | None
) -> str:
    """Build a Service Hub URL with query params (app-confirmed encoding).

    The server validates parameter values exactly as sent
    (live-probed): '@' is preserved, not percent-encoded.
    """
    full_url = base + path
    if not params:
        return full_url
    safe_chars = "-_.~!$'()*,;=:@/?"
    parts = [
        f"{quote(str(k), safe=safe_chars)}={quote(str(v), safe=safe_chars)}"
        for k, v in params.items()
    ]
    return f"{full_url}?{'&'.join(parts)}"
