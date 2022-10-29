class HyundaiKiaException(Exception):
    """Generic hyundaiKiaException exception.
    Attributes:
        stage: the stage that the exception occurred at
    """

    def __init__(self, stage: str) -> None:
        self.stage = stage

    pass
  
  
class AuthenticationError(HyundaiKiaException):
    """
    Raised upon receipt of an authentication error. 
    """

    pass
  
class InvalidAPIResponseError(HyundaiKiaException):
    """
    Raised upon receipt of an authentication error. 
    """

    pass
