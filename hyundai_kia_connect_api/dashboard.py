"""Build a local HTML dashboard from a VehicleManager's vehicles."""

import dataclasses
import datetime as dt
import json
import logging
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any

from .exceptions import HyundaiKiaException
from .VehicleManager import VehicleManager

_LOGGER = logging.getLogger(__name__)

TEMPLATE = "dashboard.html"
DATA_PLACEHOLDER = "__DASHBOARD_DATA__"
REFRESH_PLACEHOLDER = "__DASHBOARD_REFRESH_URL__"
# Kia/Hyundai servers answer 5091 to frequent reads.
MIN_REFRESH_SECONDS = 60


class DashboardEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, (dt.date, dt.datetime, dt.time)):
            return o.isoformat()
        # Enums, timezones and other library values: the page only displays them.
        return str(o)


def _months_back(today: dt.date, count: int) -> list[str]:
    months, first = [], today.replace(day=1)
    for _ in range(count):
        months.append(first.strftime("%Y%m"))
        first = (first - dt.timedelta(days=1)).replace(day=1)
    return months


def collect_trips(
    vm: VehicleManager,
    vehicle_id: str,
    months: int,
    progress: Callable[[str], None] | None = None,
) -> list[Any]:
    """Month summaries with every day's trips, newest month first.

    Regions without trip info return an empty list.
    """
    vehicle = vm.get_vehicle(vehicle_id)
    history: list[Any] = []
    for yyyymm in _months_back(dt.date.today(), months):
        try:
            vm.update_month_trip_info(vehicle_id, yyyymm)
        except (NotImplementedError, HyundaiKiaException) as err:
            _LOGGER.info("Trip info unavailable for %s: %s", yyyymm, err)
            break
        month = vehicle.month_trip_info
        if month is None:
            continue
        days = []
        for n, day in enumerate(month.day_list, 1):
            if day.yyyymmdd is None:
                continue
            if progress:
                progress(
                    f"trips for {yyyymm[:4]}-{yyyymm[4:]}, day {n} of {len(month.day_list)}"
                )
            try:
                vm.update_day_trip_info(vehicle_id, day.yyyymmdd)
            except (NotImplementedError, HyundaiKiaException) as err:
                _LOGGER.info("Day trip info unavailable for %s: %s", day.yyyymmdd, err)
                continue
            if vehicle.day_trip_info is not None:
                days.append(vehicle.day_trip_info)
        history.append({"month": month, "days": days})
    return history


def _json(value: Any) -> str:
    # "</" inside the JSON would end the <script> element early.
    text = json.dumps(value, cls=DashboardEncoder, separators=(",", ":"))
    return text.replace("</", "<\\/")


def render(data: dict[str, Any], refresh_url: str | None = None) -> str:
    """Return the dashboard page with ``data`` embedded.

    With ``refresh_url`` the page's Refresh button posts there for new data.
    """
    template = files(__package__).joinpath(TEMPLATE).read_text(encoding="utf-8")
    return template.replace(DATA_PLACEHOLDER, _json(data)).replace(
        REFRESH_PLACEHOLDER, _json(refresh_url)
    )


def make_server(
    build: Callable[[], dict[str, Any]], port: int = 0
) -> tuple[ThreadingHTTPServer, str]:
    """A 127.0.0.1 server for the page; POST <url>refresh rebuilds the data.

    The URL carries a random token and other Host headers are refused, so
    other web pages can't read the data (DNS rebinding included).
    """
    token = secrets.token_urlsafe(16)
    lock = threading.Lock()
    state: dict[str, Any] = {"data": build(), "at": time.monotonic()}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, kind: str) -> None:
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _allowed(self, path: str) -> bool:
            host = f"127.0.0.1:{server.server_address[1]}"
            if self.headers.get("Host") != host or self.path != path:
                self._send(404, "Not found", "text/plain")
                return False
            return True

        def do_GET(self) -> None:
            if self._allowed(f"/{token}/"):
                page = render(state["data"], f"/{token}/refresh")
                self._send(200, page, "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if not self._allowed(f"/{token}/refresh"):
                return
            with lock:
                wait = MIN_REFRESH_SECONDS - (time.monotonic() - state["at"])
                error = None
                if wait <= 0:
                    try:
                        state["data"] = build()
                        state["at"] = time.monotonic()
                        wait = MIN_REFRESH_SECONDS
                    except Exception as err:  # vendor API: report, keep serving
                        _LOGGER.warning("Refresh failed: %s", err)
                        error = str(err) or type(err).__name__
                body = {"data": state["data"], "wait": round(wait), "error": error}
            self._send(200, _json(body), "application/json")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, f"http://127.0.0.1:{server.server_address[1]}/{token}/"


def serve(build: Callable[[], dict[str, Any]], open_browser: bool = True) -> None:
    """Serve the dashboard until Ctrl+C."""
    server, url = make_server(build)
    print(f"Roadbook is running at {url}")
    print("Keep this terminal open for Refresh to work. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()
