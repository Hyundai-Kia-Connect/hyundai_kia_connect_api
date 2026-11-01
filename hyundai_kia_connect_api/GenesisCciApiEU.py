"""GenesisCciApiEU.py — Genesis EU CCI/GSPA API.

Genesis-specific EU implementation inheriting the OneApp (CCI) login flow,
the GSPA secure-request layer, and the shared GSPA remote-control layer
from ``GspaApiEU``. This module keeps Genesis brand constants. The auth
path (CCI login, signed token-exchange, signed token-refresh) and the
X-Stamp cipher are live-verified; the GSPA read/remote layer awaits
live verification with a vehicle attached to the account.
"""

from .GspaApiEU import GspaApiEU
from .mqtt_service_hub_eu import MqttServiceHubMixin
from .Token import Token


class GenesisCciApiEU(MqttServiceHubMixin, GspaApiEU):
    """Genesis EU CCI/GSPA API.

    Uses the CCI login flow (OneApp client_id 50e3b8b0) confirmed on
    production endpoints and the shared GSPA layer from ``GspaApiEU``.
    The Genesis X-Stamp cipher is validated bit-exact against the brand's
    native library. GSPA reads/controls are inherited brand-neutral and
    stay gated until live verification with a real vehicle.
    """

    # Brand constants (Genesis OneApp EU, confirmed on production endpoints).
    ONEAPP_CLIENT_ID = "50e3b8b0-ced5-43b7-8a42-f86ac92fe50e"
    CCI_PACKAGE_ID = "com.genesis.oneapp.eu"
    ONEAPP_REDIRECT_URI = "https://oneapp.genesis.com/redirect"
    CCI_API_URL = "https://cci-api-eu.genesis.com"
    GSPA_BASE_URL = "https://gspa-ccs-eu.genesis.com/"
    LOGIN_FORM_HOST = "https://idpconnect-eu.genesis.com"
    CIPHER_BRAND = "genesis"
    REQUEST_ID_HEADER = "X-Request-Id"
    DEVICE_ID_HEADER = "X-Device-Id"
    SERVICE_HUB_BASE = "egw-svchub-ccs-g-eu.eu-central.hmgmobility.com:31010"
    CCSP_CLIENT_SERVICE_ID = "50e3b8b0-ced5-43b7-8a42-f86ac92fe50e"
    PUSH_PROVIDER_ID = "c1d0a320-600b-4f19-801a-e61babcc2c23"

    def get_push_credentials(self, token: Token) -> tuple[str, str] | None:
        """The Genesis broker serves the delivery ACL at subscribe time."""
        return None
