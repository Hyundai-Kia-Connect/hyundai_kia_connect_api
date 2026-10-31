"""Tests for the Service Hub push provider (mqtt_service_hub_eu module)."""

import json

import pytest

from hyundai_kia_connect_api.const import MQTT_PUSH_CONNECTION_STATE
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.mqtt_service_hub_eu import (
    POSTFIX_OTA_PROGRESS,
    POSTFIX_OTA_SCHEDULEUPDATE,
    TOPIC_PREFIX_CONNECTION,
    TOPIC_PREFIX_DEVICE,
    TOPIC_PREFIX_LOCATION,
    TOPIC_PREFIX_VSS,
    build_car_status_topics,
    build_device_remote_topics,
    build_ota_topics,
    parse_mqtt_message,
)
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class _FakeSession:
    """Records (method, url, kwargs) and returns queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


def _metadata_response(vehicle_payload: dict) -> _FakeResponse:
    return _FakeResponse(json_data={"vehicles": [vehicle_payload]})


# ---------------------------------------------------------------------------
# Topic builders
# ---------------------------------------------------------------------------


class TestBuildCarStatusTopics:
    def test_base(self):
        topics = build_car_status_topics("v1", False)
        # Res topic excluded — broker rejects rc=128
        assert topics == [
            TOPIC_PREFIX_VSS + "v1",
            TOPIC_PREFIX_CONNECTION + "v1",
        ]

    def test_location_on_true(self):
        topics = build_car_status_topics("v1", True)
        assert len(topics) == 3
        assert topics[-1] == TOPIC_PREFIX_LOCATION + "v1"


class TestBuildDeviceRemoteTopics:
    def test_basic(self):
        topics = build_device_remote_topics("dev1")
        assert len(topics) == 6
        assert all(
            t.startswith(TOPIC_PREFIX_DEVICE + "dev1") or "$/device/res/dev1" == t
            for t in topics
        )


class TestBuildOtaTopics:
    def test_unknown_capability_no_topics(self):
        vehicle = Vehicle()
        vehicle.mqtt_vehicle_id = "v1"
        vehicle.supports_ota_progress = None  # unknown → not subscribed
        assert build_ota_topics(vehicle) == []

    def test_explicit_true_topics(self):
        vehicle = Vehicle()
        vehicle.mqtt_vehicle_id = "v1"
        vehicle.supports_ota_progress = True
        vehicle.supports_schedule_update = True
        topics = build_ota_topics(vehicle)
        assert POSTFIX_OTA_PROGRESS in topics[0]
        assert POSTFIX_OTA_SCHEDULEUPDATE in topics[1]


# ---------------------------------------------------------------------------
# Message parser
# ---------------------------------------------------------------------------


class TestParseMqttMessage:
    def test_car_status_status(self):
        msg = parse_mqtt_message("service/phone/_/vss/vehicle123", b'{"x": 1}')
        assert (msg.topic_group, msg.topic_type) == ("CarStatus", "Status")
        assert msg.vehicle_id == "vehicle123"

    def test_car_status_connect(self):
        msg = parse_mqtt_message("service/phone/_/connection/v1", b"{}")
        assert (msg.topic_group, msg.topic_type) == ("CarStatus", "Connect")

    def test_car_status_location(self):
        msg = parse_mqtt_message("service/phone/_/location/v1", b"{}")
        assert (msg.topic_group, msg.topic_type) == ("CarStatus", "Location")

    def test_device_remote_command(self):
        msg = parse_mqtt_message("device/c1/remotecontroller/command", b"{}")
        assert (msg.topic_group, msg.topic_type) == ("DeviceRemote", "Command")
        assert msg.vehicle_id == "c1"

    def test_device_res_special_prefix(self):
        msg = parse_mqtt_message("$/device/res/c1", b"{}")
        assert (msg.topic_group, msg.topic_type) == ("DeviceRemote", "Res")

    def test_ota_topics(self):
        msg = parse_mqtt_message("vehicle/v1/_/ota/otaprogress", b'{"progress": 50}')
        assert (msg.topic_group, msg.topic_type) == ("CarOta", "Progress")
        msg = parse_mqtt_message("vehicle/v1/_/ota/scheduleupdate", b"{}")
        assert (msg.topic_group, msg.topic_type) == ("CarOta", "ScheduleUpdate")

    def test_invalid_json_raw_fallback(self):
        msg = parse_mqtt_message("service/phone/_/vss/v1", b"\xff\xfe invalid")
        assert msg.topic_group == "CarStatus"
        assert "raw" in msg.payload

    def test_unknown_topic(self):
        msg = parse_mqtt_message("unknown/prefix/data", b"{}")
        assert msg.topic_group == "Unknown"


# ---------------------------------------------------------------------------
# Capabilities pipeline: metadata response → Vehicle fields → topics
# ---------------------------------------------------------------------------


def _kia_api() -> KiaCciApiEU:
    api = KiaCciApiEU(region=9, brand=1, language="en")
    api._service_hub_tid = "tid-1"
    return api


def _vehicle() -> Vehicle:
    v = Vehicle()
    v.id = "careu1"
    return v


def _token() -> Token:
    token = Token()
    token.mqtt_client_id = "client-1"
    return token


def test_metadata_populates_capabilities_and_topics():
    """End-to-end: metadata protocol list → Vehicle flags → topics.

    Fixture shape = live-probed metadatalist ({"vehicles": [{unit,
    clientId, vehicleId, protocols}]}).
    """
    api = _kia_api()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [
            _metadata_response(
                {
                    "unit": "hu",
                    "clientId": "client-1",
                    "vehicleId": "mqtt-v1",
                    "protocols": [
                        "service.phone.vss",
                        "device.remotecontroller.connect",
                        "device.closeremote.precondition",
                        "device.closeremote.media.vehiclestatus",
                        "vehicle.ota.otaprogress",
                    ],
                }
            ),
            _FakeResponse(json_data={"vehicleId": "mqtt-v1"}),
        ]
    )
    api.get_push_vehicle_identity(Token(), vehicle)
    assert vehicle.mqtt_vehicle_id == "mqtt-v1"
    # protocol present → True; list read, protocol absent → False
    assert vehicle.supports_hvac_close_remote is True
    assert vehicle.supports_media_close_remote is True
    assert vehicle.supports_ota_progress is True
    assert vehicle.supports_schedule_update is False
    # protocols have no location-topic counterpart → unknown (None)
    assert vehicle.supports_speed_event is None

    topics = api.get_push_topics(_token(), vehicle)
    # location unknown → not subscribed
    assert not any(TOPIC_PREFIX_LOCATION in t for t in topics)
    assert any(POSTFIX_OTA_PROGRESS in t for t in topics)
    assert not any(POSTFIX_OTA_SCHEDULEUPDATE in t for t in topics)


def test_metadata_absent_protocol_is_false_not_none():
    """A read protocol list without the protocol = False (no masking)."""
    api = _kia_api()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [
            _metadata_response(
                {
                    "unit": "hu",
                    "clientId": "c1",
                    "vehicleId": "mqtt-v1",
                    "protocols": ["service.phone.vss"],
                }
            ),
            _FakeResponse(json_data={"vehicleId": "mqtt-v1"}),
        ]
    )
    api.get_push_vehicle_identity(Token(), vehicle)
    assert vehicle.supports_ota_progress is False
    assert vehicle.supports_schedule_update is False
    topics = api.get_push_topics(_token(), vehicle)
    assert not any("ota" in t for t in topics)


def test_metadata_unknown_shape_leaves_capabilities_unknown():
    """No protocol list in the response → flags stay None (unknown)."""
    api = _kia_api()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [
            _metadata_response({"unit": "mobile", "clientId": "c1", "other": 1}),
            _FakeResponse(json_data={"vehicleId": "mqtt-v1"}),
        ]
    )
    api.get_push_vehicle_identity(Token(), vehicle)
    assert vehicle.supports_ota_progress is None
    topics = api.get_push_topics(_token(), vehicle)
    assert not any(TOPIC_PREFIX_LOCATION in t for t in topics)
    assert not any("ota" in t for t in topics)


def test_metadata_entry_level_capabilities():
    """The hu entry's protocol list is the capability source."""
    api = _kia_api()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [
            _metadata_response(
                {
                    "unit": "hu",
                    "clientId": "hu-1",
                    "vehicleId": "mqtt-v1",
                    "protocols": ["vehicle.ota.scheduleupdate"],
                }
            ),
            _FakeResponse(json_data={"vehicleId": "mqtt-v1"}),
        ]
    )
    api.get_push_vehicle_identity(Token(), vehicle)
    assert vehicle.hu_client_id == "hu-1"
    assert vehicle.supports_schedule_update is True


# ---------------------------------------------------------------------------
# Push provider implementation (mixed into both brand classes)
# ---------------------------------------------------------------------------


def test_parse_push_message_status():
    msg = _kia_api().parse_push_message("service/phone/_/vss/mqtt-v1/", b'{"test": 1}')
    assert msg is not None
    assert (msg.topic_group, msg.topic_type) == ("CarStatus", "Status")
    assert msg.is_status is True


def test_parse_push_message_res_shape():
    """Res/Connect parse as plain messages; result extraction is not
    part of the contract (no consumer)."""
    body = {"header": {"tid": "tid-1"}, "body": {"resCode": "success"}}
    msg = _kia_api().parse_push_message(
        "$/device/res/testclient1", json.dumps(body).encode()
    )
    assert (msg.topic_group, msg.topic_type) == ("DeviceRemote", "Res")
    assert msg.is_status is False

    msg = _kia_api().parse_push_message(
        "device/c1/remotecontroller/connect", json.dumps(body).encode()
    )
    assert (msg.topic_group, msg.topic_type) == ("DeviceRemote", "Connect")


def test_parse_push_message_unknown_group_none():
    assert _kia_api().parse_push_message("garbage/topic/xyz/", b"{}") is None


def test_get_push_topics_without_identity_empty():
    """No mqtt_vehicle_id / client id → no topics (registry not done)."""
    topics = _kia_api().get_push_topics(Token(), _vehicle())
    assert topics == []


def test_register_push_vehicle_ota_protocols_conditional():
    """OTA protocol ids in the registration body only on explicit True."""
    api = _kia_api()
    vehicle = _vehicle()
    vehicle.mqtt_vehicle_id = "mqtt-v1"
    session = _FakeSession([_FakeResponse(text="", status_code=200)])
    api._service_hub_session = session

    vehicle.supports_ota_progress = None
    api.register_push_vehicle(Token(), vehicle)
    assert len(session.calls) == 1
    body = session.calls[0][2]["json"]
    assert "vehicle.ota.otaprogress" not in body["protocols"]
    assert body["protocolId"] == "statesync.vehicle.ccu.update"
    assert body["carId"] == "careu1"
    assert body["brand"] == "K"

    vehicle.supports_ota_progress = True
    vehicle.supports_schedule_update = True
    api._service_hub_session = _FakeSession([_FakeResponse(text="", status_code=200)])
    api.register_push_vehicle(Token(), vehicle)
    body = api._service_hub_session.calls[0][2]["json"]
    assert "vehicle.ota.otaprogress" in body["protocols"]
    assert "vehicle.ota.scheduleupdate" in body["protocols"]


def test_register_vehicle_failure_returns_none():
    api = _kia_api()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [_FakeResponse(json_data={}, status_code=400, text='{"err": "nope"}')]
    )
    assert api.register_push_vehicle(Token(), vehicle) is None


def test_connection_state_enum_and_graceful_unknown():
    api = _kia_api()
    token = Token()
    token.mqtt_client_id = "client-1"
    api._service_hub_session = _FakeSession(
        [_FakeResponse(json_data={"connState": "ONLINE"})]
    )
    assert api.get_push_connection_state(token) is MQTT_PUSH_CONNECTION_STATE.ONLINE

    api._service_hub_session = _FakeSession(
        [_FakeResponse(json_data={"connState": "weird"})]
    )
    assert api.get_push_connection_state(token) is None


def test_hyundai_and_kia_service_hub_bases_differ():
    """Per-brand SERVICE_HUB_BASE resolves the live-probed H_EU / K_EU hosts."""
    from urllib.parse import urlparse

    hyundai = HyundaiCciApiEU(region=9, brand=2, language="en")
    kia = _kia_api()
    assert hyundai.SERVICE_HUB_BASE != kia.SERVICE_HUB_BASE
    assert urlparse(hyundai._get_service_hub_url()).netloc == (
        "egw-svchub-ccs-h-eu.eu-central.hmgmobility.com:31010"
    )
    assert urlparse(kia._get_service_hub_url()).netloc == (
        "egw-svchub-ccs-k-eu.eu-central.hmgmobility.com:31010"
    )


def test_get_service_hub_url_missing_base_empty():
    class _Baseless(KiaCciApiEU):
        SERVICE_HUB_BASE = ""

    api = _Baseless(region=9, brand=1, language="en")
    assert api._get_service_hub_url() == ""


def test_service_hub_headers_app_confirmed_set():
    token = Token()
    token.access_token = "tok-1"
    token.exchangeable_token = "ex-1"
    api = _kia_api()
    headers = api._get_service_hub_headers(token)
    assert headers["Authorization"] == "Bearer tok-1"
    assert headers["X-Service-Id"] == api.CCSP_CLIENT_SERVICE_ID
    assert headers["X-Application-Id"] == api.PUSH_PROVIDER_ID
    assert headers["exchangeable-token"] == "ex-1"
    assert "X-Request-Id" in headers


def test_query_encoding_preserves_at_sign():
    """tid/@-bearing query values are sent un-encoded (server validates as sent)."""

    api = _kia_api()
    url = api._service_hub_get_url_with_params(
        "/api/v3/servicehub/vehicleId", {"carId": "a@b"}
    )
    assert "a@b" in url


def test_cci_classes_implement_contract():
    for api in (HyundaiCciApiEU(region=9, brand=2, language="en"), _kia_api()):
        for name in (
            "get_push_broker_info",
            "register_push_client",
            "register_push_vehicle",
            "get_push_vehicle_identity",
            "get_push_connection_state",
            "get_push_topics",
            "parse_push_message",
        ):
            assert callable(getattr(api, name)), name


def test_vehicle_push_identity_fields():
    v = Vehicle()
    assert v.mqtt_vehicle_id is None
    assert v.hu_client_id is None
    assert not hasattr(v, "mqtt_client_id")  # renamed → mqtt_vehicle_id


@pytest.mark.parametrize("brand_cls,brand", [(HyundaiCciApiEU, 2), (KiaCciApiEU, 1)])
def test_gspa_base_has_no_service_hub_state_leak(brand_cls, brand):
    """SERVICE_HUB_BASE is the only push URL config (no region maps, no staging)."""
    import hyundai_kia_connect_api.mqtt_service_hub_eu as mod

    assert not hasattr(mod, "SERVICE_HUB_PRODUCTION_BASES")
    assert not hasattr(mod, "SERVICE_HUB_STAGING_BASES")
    assert not hasattr(brand_cls, "staging")


def test_metadata_prefers_hu_entry_over_tmu():
    """Multi-unit responses (live-probed Hyundai: hu, tmu, ccu) read the
    hu protocol list; the tmu list must not shadow it. A response
    without a hu entry falls back to the first entry with protocols
    (Kia: tmu only)."""
    api = _kia_api()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [
            _FakeResponse(
                json_data={
                    "vehicles": [
                        {
                            "unit": "tmu",
                            "clientId": "c-t",
                            "vehicleId": "mqtt-v1",
                            "protocols": ["statesync.vehicle.ccu.update"],
                        },
                        {
                            "unit": "hu",
                            "clientId": "client-1",
                            "vehicleId": "mqtt-v1",
                            "protocols": [
                                "vehicle.ota.scheduleupdate",
                                "device.closeremote.precondition",
                            ],
                        },
                        {"unit": "ccu", "clientId": "c-x", "vehicleId": "mqtt-v1"},
                    ]
                }
            ),
            _FakeResponse(json_data={"vehicleId": "mqtt-v1"}),
        ]
    )
    api.get_push_vehicle_identity(Token(), vehicle)
    assert vehicle.supports_hvac_close_remote is True  # from hu, not tmu
    assert vehicle.supports_schedule_update is True  # from hu

    api._service_hub_session = _FakeSession(
        [
            _FakeResponse(
                json_data={
                    "vehicles": [
                        {
                            "unit": "tmu",
                            "clientId": "c-t",
                            "vehicleId": "mqtt-v1",
                            "protocols": ["statesync.vehicle.ccu.update"],
                        }
                    ]
                }
            ),
            _FakeResponse(json_data={"vehicleId": "mqtt-v1"}),
        ]
    )
    vehicle2 = _vehicle()
    api.get_push_vehicle_identity(Token(), vehicle2)
    assert vehicle2.supports_hvac_close_remote is False  # tmu fallback


# ---------------------------------------------------------------------------
# Service Hub HTTP endpoints — failure paths and response shaping
# ---------------------------------------------------------------------------


def test_get_push_broker_info_success_creates_session(monkeypatch):
    """First call creates the shared Service Hub session and stores the
    response tid header for the subsequent calls."""
    import hyundai_kia_connect_api.mqtt_service_hub_eu as mod

    api = _kia_api()
    session = _FakeSession(
        [
            _FakeResponse(
                json_data={
                    "mqtt": {"host": "b.example", "port": 1884},
                    "http": {"host": "h.example", "port": 31010},
                },
                headers={"tid": "tid-x"},
            )
        ]
    )
    created = []
    monkeypatch.setattr(
        mod.requests, "Session", lambda: (created.append(1), session)[1]
    )
    info = api.get_push_broker_info(_token())
    assert created == [1]  # session created on first use
    assert info == {
        "mqtt_host": "b.example",
        "mqtt_port": 1884,
        "http_host": "h.example",
        "http_port": 31010,
    }
    assert api._service_hub_tid == "tid-x"
    assert api._service_hub_session is session


def test_get_push_broker_info_failures():
    api = _kia_api()
    token = _token()
    # non-200 → None
    api._service_hub_session = _FakeSession(
        [_FakeResponse(status_code=503, text="down")]
    )
    assert api.get_push_broker_info(token) is None
    # no mqtt host in response → None
    api._service_hub_session = _FakeSession([_FakeResponse(json_data={})])
    assert api.get_push_broker_info(token) is None

    # transport error → None
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("socket gone")

    api._service_hub_session = _Boom()
    assert api.get_push_broker_info(token) is None
    # no tid header → warning, call still succeeds with the port default
    api._service_hub_session = _FakeSession(
        [_FakeResponse(json_data={"mqtt": {"host": "b.example"}})]
    )
    info = api.get_push_broker_info(token)
    assert info == {
        "mqtt_host": "b.example",
        "mqtt_port": 8883,
        "http_host": None,
        "http_port": None,
    }


def test_register_push_client_success_and_side_effects():
    api = _kia_api()
    token = Token()
    api._service_hub_session = _FakeSession(
        [
            _FakeResponse(
                json_data={
                    "clientId": "cid1",
                    "deviceId": "d1",
                    "username": "u1",
                    "password": "p1",
                }
            )
        ]
    )
    result = api.register_push_client(token)
    assert result == {
        "client_id": "cid1",
        "device_id": "d1",
        "username": "u1",
        "password": "p1",
    }
    assert token.mqtt_client_id == "cid1"
    _, _, kwargs = api._service_hub_session.calls[0]
    assert kwargs["json"] == {"unit": "mobile", "uuid": ""}
    assert kwargs["headers"]["tid"] == "tid-1"
    # snake_case clientId fallback in the response
    api._service_hub_session = _FakeSession(
        [_FakeResponse(json_data={"client_id": "c2"})]
    )
    assert api.register_push_client(token)["client_id"] == "c2"


def test_register_push_client_failures():
    api = _kia_api()
    token = _token()
    api._service_hub_session = _FakeSession(
        [_FakeResponse(status_code=422, text="tid required")]
    )
    assert api.register_push_client(token) is None
    api._service_hub_session = _FakeSession(
        [_FakeResponse(json_data={"username": "u1"})]
    )
    token_fresh = Token()  # mqtt_client_id unset — must stay unset on failure
    assert api.register_push_client(token_fresh) is None  # no clientId
    assert token_fresh.mqtt_client_id is None  # side effect not applied

    class _Boom:
        def post(self, *a, **k):
            raise RuntimeError("socket gone")

    api._service_hub_session = _Boom()
    assert api.register_push_client(token) is None


def test_register_push_vehicle_transport_error_returns_none():
    api = _kia_api()

    class _Boom:
        def post(self, *a, **k):
            raise RuntimeError("socket gone")

    api._service_hub_session = _Boom()
    # tid unused → falls back to a uuid; must not raise
    api._service_hub_tid = None
    assert api.register_push_vehicle(_token(), _vehicle()) is None


def test_connection_state_failures():
    api = _kia_api()
    token = _token()
    api._service_hub_session = _FakeSession(
        [_FakeResponse(status_code=503, text="down")]
    )
    assert api.get_push_connection_state(token) is None

    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("socket gone")

    api._service_hub_session = _Boom()
    assert api.get_push_connection_state(token) is None


def test_service_hub_brand_codes():
    """One-letter Service Hub brand codes differ from the header brand names."""
    hyundai = HyundaiCciApiEU(region=9, brand=2, language="en")
    kia = _kia_api()
    # Genesis brand class does not exist; the code path stays 3 → "G".
    genesis = HyundaiCciApiEU(region=9, brand=3, language="en")
    assert hyundai._service_hub_brand == "H"
    assert kia._service_hub_brand == "K"
    assert genesis._service_hub_brand == "G"


def test_get_url_with_params_no_params_no_query():
    api = _kia_api()
    assert api._service_hub_get_url_with_params("/api/x", {}) == (
        f"https://{api.SERVICE_HUB_BASE}/api/x"
    )


def test_service_hub_headers_non_ccs_token():
    token = _token()
    token.non_ccs_token = "n-1"
    headers = _kia_api()._get_service_hub_headers(token)
    assert headers["non-ccs-token"] == "n-1"


def test_metadata_failures():
    api = _kia_api()
    token = _token()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [_FakeResponse(status_code=500, text="boom")]
    )
    assert api._service_hub_get_metadata(token, vehicle) is None
    # non-dict entries are skipped
    api._service_hub_session = _FakeSession(
        [
            _FakeResponse(
                json_data={"vehicles": ["junk", {"unit": "mobile", "other": 1}]}
            )
        ]
    )
    api._service_hub_get_metadata(token, vehicle)
    assert vehicle.hu_client_id is None
    assert vehicle.supports_ota_progress is None

    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("socket gone")

    api._service_hub_session = _Boom()
    assert api._service_hub_get_metadata(token, vehicle) is None


def test_vehicle_id_failures():
    api = _kia_api()
    token = _token()
    vehicle = _vehicle()
    api._service_hub_session = _FakeSession(
        [_FakeResponse(status_code=500, text="boom")]
    )
    assert api._service_hub_get_vehicle_id(token, vehicle) is None
    api._service_hub_session = _FakeSession([_FakeResponse(json_data={"nope": 1})])
    assert api._service_hub_get_vehicle_id(token, vehicle) is None
    assert vehicle.mqtt_vehicle_id is None

    class _Boom:
        def post(self, *a, **k):
            raise RuntimeError("socket gone")

    api._service_hub_session = _Boom()
    assert api._service_hub_get_vehicle_id(token, vehicle) is None


def test_capabilities_fallback_skips_non_dict_entries():
    from hyundai_kia_connect_api.mqtt_service_hub_eu import _apply_vehicle_capabilities

    vehicle = _vehicle()
    _apply_vehicle_capabilities(
        ["junk", {"unit": "tmu", "protocols": ["vehicle.ota.otaprogress"]}], vehicle
    )
    assert vehicle.supports_ota_progress is True


def test_build_ota_topics_without_vehicle_id():
    assert build_ota_topics(Vehicle()) == []


def test_classify_device_topic_suffix_variants():
    cases = {
        "device/c1/remotecontroller/connectcheck": ("DeviceRemote", "ConnectCheck"),
        "device/c1/remotecontroller/mobileclose": ("DeviceRemote", "MobileClose"),
        "device/c1/remotecontroller/vehicleclose": ("DeviceRemote", "CarClose"),
    }
    for topic, expected in cases.items():
        msg = parse_mqtt_message(topic, b"{}")
        assert (msg.topic_group, msg.topic_type, msg.vehicle_id) == (
            expected[0],
            expected[1],
            "c1",
        )
    # unrecognized device suffix → DeviceRemote/Unknown
    msg = parse_mqtt_message("device/c1/other-thing", b"{}")
    assert (msg.topic_group, msg.topic_type) == ("DeviceRemote", "Unknown")
    # unrecognized vehicle suffix → CarOta/Unknown
    msg = parse_mqtt_message("vehicle/v1/other-thing", b"{}")
    assert (msg.topic_group, msg.topic_type) == ("CarOta", "Unknown")


def test_classify_empty_infix_between_prefix_and_slash():
    """A topic ending on the infix delimiter carries an empty infix."""
    msg = parse_mqtt_message("device//", b"{}")
    assert msg.topic_group == "DeviceRemote"
    assert msg.vehicle_id == ""
