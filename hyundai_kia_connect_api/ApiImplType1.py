from .ApiImpl import (
    ApiImpl,
)
from .Token import Token

USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"


class ApiImplType1(ApiImpl):
    def __init__(self) -> None:
        """Initialize."""

    def _get_authenticated_headers(self, token: Token) -> dict:
        return {
            "Authorization": token.access_token,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "Stamp": self._get_stamp(),
            "ccsp-device-id": token.device_id,
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Ccuccs2protocolsupport": self.ccu_ccs2_protocol_support,
            "User-Agent": USER_AGENT_OK_HTTP,
        }
