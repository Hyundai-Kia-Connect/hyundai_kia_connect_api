"""exceptions.py"""
# pylint:disable=unnecessary-pass


class HyundaiKiaException(Exception):
    """
    Generic hyundaiKiaException exception.
    """

    pass


class AuthenticationError(HyundaiKiaException):
    """
    Raised upon receipt of an authentication error.
    """

    pass


class DeviceIDError(AuthenticationError):
    """
    Raised upon receipt of an Invalid Device ID error.
    """

    pass


class APIError(HyundaiKiaException):
    """
    Generic API error
    """

    pass


class RateLimitingError(APIError):
    """
    Raised when we get rate limited by the server
    """

    pass


class NoDataFound(APIError):
    """
    Raised when the API doesn't have data for that car.
    Disabling the car is the solution.
    """

    pass


class ServiceTemporaryUnavailable(APIError):
    """
    Raised when Service Temporary Unavailable
    """

    pass


class DuplicateRequestError(APIError):
    """
    Raised when (supposedly) a previous request is already queued server-side
    and the server temporarily rejects requests.
    """

    pass


class RequestTimeoutError(APIError):
    """
    Raised when (supposedly) the server fails to establish a connection with the car.
    """

    pass


class InvalidAPIResponseError(APIError):
    """
    Raised upon receipt of an invalid API response.
    """

    pass
