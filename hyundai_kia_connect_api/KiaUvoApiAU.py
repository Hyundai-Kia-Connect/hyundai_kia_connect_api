"""KiaUvoApiAU"""

# pylint:disable=missing-class-docstring,missing-function-docstring,wildcard-import,unused-wildcard-import,invalid-name,logging-fstring-interpolation,broad-except,bare-except,unused-argument,line-too-long,too-many-lines

import base64
import datetime as dt
import logging
import typing as ty
from zoneinfo import ZoneInfo

from .ApiImpl import ApiImplSession
from .ApiImplType1 import ApiImplType1, _check_response_for_errors
from .const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    CHARGE_PORT_ACTION,
    DOMAIN,
    ENGINE_TYPES,
    LOGIN_TOKEN_LIFETIME,
    REGION_AUSTRALIA,
    REGION_NZ,
    REGIONS,
)
from .exceptions import AuthenticationError
from .Token import Token
from .utils import (
    get_child_value,
    parse_datetime,
)
from .Vehicle import (
    Vehicle,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"
USER_AGENT_MOZILLA: str = "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.166 Mobile Safari/535.19"


class KiaUvoApiAU(ApiImplType1):
    data_timezone = ZoneInfo("Australia/Sydney")
    temperature_range = tuple(x * 0.5 for x in range(34, 54))

    DRIVING_INFO_ALLTIME_KEY: str = "drivingInfoDetail"

    def __init__(self, region: int, brand: int, language: str) -> None:
        super().__init__()
        self.brand = brand
        if BRANDS[brand] == BRAND_KIA and REGIONS[region] == REGION_AUSTRALIA:
            self.BASE_URL: str = "au-apigw.ccs.kia.com.au:8082"
            self.CCSP_SERVICE_ID: str = "8acb778a-b918-4a8d-8624-73a0beb64289"
            self.APP_ID: str = "4ad4dcde-be23-48a8-bc1c-91b94f5c06f8"  # Android app ID
            self.BASIC_AUTHORIZATION: str = "Basic OGFjYjc3OGEtYjkxOC00YThkLTg2MjQtNzNhMGJlYjY0Mjg5OjdTY01NbTZmRVlYZGlFUEN4YVBhUW1nZVlkbFVyZndvaDRBZlhHT3pZSVMyQ3U5VA=="
            self.CFB = base64.b64decode(
                "SGGCDRvrzmRa2WTNFQPUaNfSFdtPklZ48xUuVckigYasxmeOQqVgCAC++YNrI1vVabI="
            )
        elif BRANDS[brand] == BRAND_HYUNDAI:
            self.BASE_URL: str = "au-apigw.ccs.hyundai.com.au:8080"
            self.CCSP_SERVICE_ID: str = "855c72df-dfd7-4230-ab03-67cbf902bb1c"
            self.APP_ID: str = "f9ccfdac-a48d-4c57-bd32-9116963c24ed"  # Android app ID
            self.BASIC_AUTHORIZATION: str = "Basic ODU1YzcyZGYtZGZkNy00MjMwLWFiMDMtNjdjYmY5MDJiYjFjOmU2ZmJ3SE0zMllOYmhRbDBwdmlhUHAzcmY0dDNTNms5MWVjZUEzTUpMZGJkVGhDTw=="
            self.CFB = base64.b64decode(
                "nGDHng3k4Cg9gWV+C+A6Yk/ecDopUNTkGmDpr2qVKAQXx9bvY2/YLoHPfObliK32mZQ="
            )
        elif BRANDS[brand] == BRAND_KIA and REGIONS[region] == REGION_NZ:
            self.BASE_URL: str = "au-apigw.ccs.kia.com.au:8082"
            self.CCSP_SERVICE_ID: str = "4ab606a7-cea4-48a0-a216-ed9c14a4a38c"
            self.APP_ID: str = "97745337-cac6-4a5b-afc3-e65ace81c994"  # Android app ID
            self.BASIC_AUTHORIZATION: str = "Basic NGFiNjA2YTctY2VhNC00OGEwLWEyMTYtZWQ5YzE0YTRhMzhjOjBoYUZxWFRrS2t0Tktmemt4aFowYWt1MzFpNzRnMHlRRm01b2QybXo0TGRJNW1MWQ=="
            self.CFB = base64.b64decode(
                "SGGCDRvrzmRa2WTNFQPUaC1OsnAhQgPgcQETEfbY8abEjR/ICXK0p+Rayw5tHCGyiUA="
            )

        self.USER_API_URL: str = "https://" + self.BASE_URL + "/api/v1/user/"
        self.SPA_API_URL: str = "https://" + self.BASE_URL + "/api/v1/spa/"
        self.SPA_API_URL_V2: str = "https://" + self.BASE_URL + "/api/v2/spa/"
        self.CLIENT_ID: str = self.CCSP_SERVICE_ID
        self.PUSH_TYPE: str = "GCM"

    def _get_vehicle_headers(self, token: Token, vehicle: Vehicle) -> dict:
        return self._get_authenticated_headers(token)

    def login(
        self,
        username: str,
        password: str,
        otp_handler: ty.Callable[[dict], dict] | None = None,
        pin: str | None = None,
    ) -> Token:
        stamp = self._get_stamp()
        device_id = self._get_device_id(stamp)
        cookies = self._get_cookies()
        # self._set_session_language(cookies)
        authorization_code = None
        try:
            authorization_code = self._get_authorization_code_with_redirect_url(
                username, password, cookies
            )
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - get_authorization_code_with_redirect_url failed")

        if authorization_code is None:
            raise AuthenticationError("Login Failed")

        _, access_token, refresh_token, expires_in = self._get_access_token(
            authorization_code, stamp
        )
        if expires_in is not None:
            valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(
                seconds=int(expires_in)
            )
        else:
            valid_until = dt.datetime.now(dt.UTC) + LOGIN_TOKEN_LIFETIME

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            device_id=device_id,
            valid_until=valid_until,
            pin=pin,
        )

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id
        is_ccs2 = vehicle.ccu_ccs2_protocol_support != 0
        if is_ccs2:
            url += "/ccs2/carstatus/latest"
        else:
            url += "/status/latest"

        response = self.session.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()

        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response: {response}")
        _check_response_for_errors(response)

        if is_ccs2:
            state = response["resMsg"]["state"]["Vehicle"]
            self._update_vehicle_properties_ccs2(vehicle, state)
            # The CCS2 status response embeds a stale cached location.
            # Override it with the more current /location/park endpoint.
            location = self._get_location(token, vehicle)
            if location and get_child_value(location, "coord.lat"):
                vehicle.location = (
                    get_child_value(location, "coord.lat"),
                    get_child_value(location, "coord.lon"),
                    parse_datetime(
                        get_child_value(location, "time"), self.data_timezone
                    ),
                )
        else:
            location = self._get_location(token, vehicle)
            self._update_vehicle_properties(
                vehicle,
                {
                    "status": response["resMsg"],
                    "vehicleLocation": location,
                },
            )

        if (
            vehicle.engine_type == ENGINE_TYPES.EV
            or vehicle.engine_type == ENGINE_TYPES.PHEV
        ):
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                # we don't know if all car types (ex: ICE cars) provide this
                # information. We also don't know what the API returns if
                # the info is unavailable. So, catch any exception and move on.
                _LOGGER.exception(
                    """Failed to parse driving info. Possible reasons:
                                    - incompatible vehicle (ICE)
                                    - new API format
                                    - API outage
                            """,
                    exc_info=e,
                )
            else:
                self._update_vehicle_drive_info(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        is_ccs2 = vehicle.ccu_ccs2_protocol_support != 0
        if is_ccs2:
            self._force_refresh_vehicle_state_ccs2(token, vehicle)
        else:
            status = self._get_forced_vehicle_state(token, vehicle)
            location = self._get_location(token, vehicle)
            self._update_vehicle_properties(
                vehicle,
                {
                    "status": status,
                    "vehicleLocation": location,
                },
            )
        # Only call for driving info on cars we know have a chance of supporting it.
        # Could be expanded if other types do support it.
        if (
            vehicle.engine_type == ENGINE_TYPES.EV
            or vehicle.engine_type == ENGINE_TYPES.PHEV
        ):
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                # we don't know if all car types (ex: ICE cars) provide this
                # information. We also don't know what the API returns if
                # the info is unavailable. So, catch any exception and move on.
                _LOGGER.exception(
                    """Failed to parse driving info. Possible reasons:
                                    - incompatible vehicle (ICE)
                                    - new API format
                                    - API outage
                            """,
                    exc_info=e,
                )
            else:
                self._update_vehicle_drive_info(vehicle, state)

    def _force_refresh_vehicle_state_ccs2(self, token: Token, vehicle: Vehicle) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/ccs2/carstatus"
        response = self.session.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(
            f"{DOMAIN} - Force refresh CCS2 vehicle status response: {response}"
        )
        _check_response_for_errors(response)
        state = response["resMsg"]["state"]["Vehicle"]
        self._update_vehicle_properties_ccs2(vehicle, state)
        location = self._get_location(token, vehicle)
        if location and get_child_value(location, "coord.lat"):
            vehicle.location = (
                get_child_value(location, "coord.lat"),
                get_child_value(location, "coord.lon"),
                parse_datetime(get_child_value(location, "time"), self.data_timezone),
            )

    def _get_location(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/location/park"

        try:
            response = self.session.get(
                url, headers=self._get_authenticated_headers(token)
            ).json()
            _LOGGER.debug(f"{DOMAIN} - _get_location response: {response}")
            _check_response_for_errors(response)
            return response["resMsg"]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - _get_location failed")
            return None

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        # TODO: needs verification
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/portdoor"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Request: {payload}")
        response = self.session.post(
            url, json=payload, headers=self._get_control_headers(token, vehicle)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def _get_drv_seat_loc(self, vehicle: Vehicle) -> str:
        """Australia uses RHD vehicles regardless of the odometer unit."""
        return "R"

    def _get_cookies(self) -> dict:
        # Get Cookies #
        url = (
            self.USER_API_URL
            + "oauth2/authorize?response_type=code&client_id="
            + self.CLIENT_ID
            + "&redirect_uri="
            + "https://"
            + self.BASE_URL
            + "/api/v1/user/oauth2/redirect&lang=en"
        )

        _LOGGER.debug(f"{DOMAIN} - Get cookies request: {url}")
        session = ApiImplSession()
        _ = session.get(url)
        return session.cookies.get_dict()

    def _get_access_token(self, authorization_code, stamp):
        # Get Access Token #
        url = self.USER_API_URL + "oauth2/token"
        headers = {
            "Authorization": self.BASIC_AUTHORIZATION,
            "Stamp": stamp,
            "Content-type": "application/x-www-form-urlencoded",
            "Host": self.BASE_URL,
            "Connection": "close",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": USER_AGENT_OK_HTTP,
        }

        data = (
            "grant_type=authorization_code&redirect_uri=https%3A%2F%2F"
            + self.BASE_URL
            + "%2Fapi%2Fv1%2Fuser%2Foauth2%2Fredirect&code="
            + authorization_code
        )
        response = self.session.post(url, data=data, headers=headers)
        response = response.json()

        token_type = response["token_type"]
        access_token = token_type + " " + response["access_token"]
        # Keep the refresh token verbatim: the oauth2/token refresh_token grant
        # rejects "Bearer "-prefixed values and access tokens in the
        # refresh_token slot with 4002 (kia_uvo #1778).
        refresh_token = response["refresh_token"]
        expires_in = response.get("expires_in")
        return token_type, access_token, refresh_token, expires_in

    def _refresh_access_token_headers(self) -> dict[str, str]:
        """AU requires the Stamp header on the refresh_token grant."""
        return {"Stamp": self._get_stamp()}
