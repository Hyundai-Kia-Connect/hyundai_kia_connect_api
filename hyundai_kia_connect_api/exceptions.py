"""exceptions.py"""

# pylint:disable=unnecessary-pass


class HyundaiKiaException(Exception):
    """
    Generic hyundaiKiaException exception.
    """


class PINMissingError(HyundaiKiaException):
    """
    Raised upon receipt of an authentication error.
    """


class AuthenticationError(HyundaiKiaException):
    """
    Raised upon receipt of an authentication error.
    """


class AuthenticationOTPRequired(AuthenticationError):
    """
    Raised when OTP is required for authentication.
    """


class APIError(HyundaiKiaException):
    """
    Generic API error
    """


class DeviceIDError(APIError):
    """
    Raised upon receipt of an Invalid Device ID error.
    """


class RateLimitingError(APIError):
    """
    Raised when we get rate limited by the server
    """


class NoDataFound(APIError):
    """
    Raised when the API doesn't have data for that car.
    Disabling the car is the solution.
    """


class ServiceTemporaryUnavailable(APIError):
    """
    Raised when Service Temporary Unavailable
    """


class DuplicateRequestError(APIError):
    """
    Raised when (supposedly) a previous request is already queued server-side
    and the server temporarily rejects requests.
    """


class UnsupportedControlError(APIError):
    """
    Raised when the vehicle does not support the requested control action.
    API returns resCode 4005 for unsupported actions (e.g. stop_climate on HEV).
    """


class RequestTimeoutError(APIError):
    """
    Raised when (supposedly) the server fails to establish a connection with the car.
    """


class InvalidAPIResponseError(APIError):
    """
    Raised upon receipt of an invalid API response.
    """


class ConsentRequiredError(AuthenticationError):
    """
    Raised when the OAuth signin succeeds but the user must accept
    a consent/authorization page before tokens can be issued.
    This typically means the user needs to log in via a browser once
    to accept terms, then use the refresh token going forward.
    """
