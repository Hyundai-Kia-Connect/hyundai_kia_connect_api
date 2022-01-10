import datetime as dt
from dataclasses import dataclass


@dataclass
class Token:
    username: str = None
    password: str = None
    access_token: str = None
    refresh_token: str = None
    device_id: str = None
    valid_until: dt.datetime = dt.datetime.min
    stamp: str = None
    pin: str = None
