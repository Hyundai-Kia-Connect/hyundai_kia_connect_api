"""Device ID stress test — 20-minute continuous monitoring.

Runs mixed read/write operations against the real EU API and logs
every device_id change, DeviceIDError, and operation result.
After each control command, polls check_action_status to trigger
the known DeviceIDError pattern (server invalidates device_id after
failed push delivery mid-polling).

Usage:
    python3 tests/integration/device_id_stress_test.py

Requires .env file in tests/integration/ with HYUNDAI_* credentials.
"""

import datetime
import os
import sys
import time

import requests as requests_lib
from dotenv import load_dotenv

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.exceptions import (
    DeviceIDError,
    DuplicateRequestError,
    InvalidAPIResponseError,
)

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DURATION_MINUTES = 20
READ_INTERVAL = 30  # seconds between read-only calls
CONTROL_INTERVAL = 90  # seconds between control commands (lock/unlock)
ACTION_POLL_INTERVAL = 5  # seconds between check_action_status polls
ACTION_POLL_MAX = 10  # max polls per command before giving up
REQUEST_TIMEOUT = 120  # seconds — EU server can be slow but not infinite

# Monkey-patch requests to add timeout — library doesn't set one,
# so without this the stress test hangs on CLOSE_WAIT connections.
_original_request = requests_lib.Session.request


def _request_with_timeout(self, method, url, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = REQUEST_TIMEOUT
    return _original_request(self, method, url, **kwargs)


requests_lib.Session.request = _request_with_timeout


def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def timed_call(func, *args, **kwargs):
    """Call func and measure wall time. Returns (result, elapsed_ms)."""
    t0 = time.time()
    result = func(*args, **kwargs)
    elapsed = int((time.time() - t0) * 1000)
    return result, elapsed


def poll_action_status(api, token, vehicle, action_id, stats, start_time):
    """Poll check_action_status after a control command.

    This is the key test: community reports show device_id gets
    invalidated mid-polling (within 2-4 polls) because the server
    tries push delivery to the fake GCM token and fails.
    """
    device_id_before = token.device_id

    for i in range(ACTION_POLL_MAX):
        try:
            result, ms = timed_call(api.check_action_status, token, vehicle, action_id)
            poll_device_id = token.device_id[:8]

            if result:
                log(
                    f"  Poll {i + 1}/{ACTION_POLL_MAX}: COMPLETE {ms}ms "
                    f"(device_id={poll_device_id}...)"
                )
                return True
            else:
                log(
                    f"  Poll {i + 1}/{ACTION_POLL_MAX}: pending {ms}ms "
                    f"(device_id={poll_device_id}...)"
                )
        except DeviceIDError:
            stats["device_id_errors"] += 1
            stats["action_poll_errors"] += 1
            new_id = token.device_id[:8]
            old_id = device_id_before[:8]
            log(
                f"  Poll {i + 1}/{ACTION_POLL_MAX}: *** DeviceIDError! *** "
                f"device_id: {old_id}... -> {new_id}... (retry handled)"
            )
            if token.device_id != device_id_before:
                stats["action_poll_device_id_changes"] += 1
            return False
        except InvalidAPIResponseError as e:
            stats["action_poll_errors"] += 1
            log(
                f"  Poll {i + 1}/{ACTION_POLL_MAX}: InvalidAPIResponse: {e} "
                f"(device_id={token.device_id[:8]}...)"
            )
            return False
        except DuplicateRequestError:
            stats["duplicate_request_errors"] += 1
            log(f"  Poll {i + 1}/{ACTION_POLL_MAX}: DuplicateRequestError (rate limit)")
            return False
        except Exception as e:
            stats["action_poll_errors"] += 1
            log(f"  Poll {i + 1}/{ACTION_POLL_MAX}: {type(e).__name__}: {e}")
            return False

        time.sleep(ACTION_POLL_INTERVAL)

    log(f"  Gave up after {ACTION_POLL_MAX} polls")
    return False


def main():
    username = os.environ.get("HYUNDAI_USERNAME")
    password = os.environ.get("HYUNDAI_PASSWORD")
    pin = os.environ.get("HYUNDAI_PIN")

    if not all([username, password, pin]):
        print("Set HYUNDAI_USERNAME, HYUNDAI_PASSWORD, HYUNDAI_PIN in .env")
        sys.exit(1)

    log("Logging in...")
    api = KiaUvoApiEU(region=1, brand=2, language="en")
    token = api.login(username=username, password=password, pin=pin)
    log(f"Login OK - device_id: {token.device_id}")

    vehicles = api.get_vehicles(token)
    if not vehicles:
        print("No vehicles found")
        sys.exit(1)
    vehicle = vehicles[0]
    log(f"Vehicle: {vehicle.name} (id={vehicle.id})")

    # Stats
    stats = {
        "reads": 0,
        "read_errors": 0,
        "read_timeouts": 0,
        "device_id_errors": 0,
        "device_id_changes": [],
        "control_commands": 0,
        "control_errors": 0,
        "duplicate_request_errors": 0,
        "action_polls": 0,
        "action_poll_errors": 0,
        "action_poll_device_id_changes": 0,
        "action_poll_complete": 0,
    }
    device_id_log = []  # (timestamp, old_id, new_id, trigger)
    last_device_id = token.device_id
    start_time = time.time()
    end_time = start_time + DURATION_MINUTES * 60
    last_read = 0
    last_control = 0

    log(f"Starting {DURATION_MINUTES}-min stress test...")
    log(f"  Read interval: {READ_INTERVAL}s")
    log(f"  Control interval: {CONTROL_INTERVAL}s")
    log(f"  Action poll: {ACTION_POLL_INTERVAL}s x {ACTION_POLL_MAX} max")
    log(
        f"  Will end at: {datetime.datetime.fromtimestamp(end_time).strftime('%H:%M:%S')}"
    )
    print()

    try:
        while time.time() < end_time:
            now = time.time()
            elapsed = int(now - start_time)
            remaining = int(end_time - now)

            # Check device_id drift
            if token.device_id != last_device_id:
                old = last_device_id
                new = token.device_id
                log(f"*** DEVICE_ID CHANGED: {old[:8]}... -> {new[:8]}... ***")
                device_id_log.append((now, old, new, "drift-detected"))
                stats["device_id_changes"].append(now - start_time)
                last_device_id = token.device_id

            # Read operation
            if now - last_read >= READ_INTERVAL:
                last_read = now
                stats["reads"] += 1
                try:
                    _, ms = timed_call(
                        api.update_vehicle_with_cached_state,
                        token,
                        vehicle,
                    )
                    log(
                        f"[+{elapsed}s / -{remaining}s] Read OK {ms}ms "
                        f"(device_id={token.device_id[:8]}...)"
                    )
                except DeviceIDError:
                    stats["device_id_errors"] += 1
                    stats["read_errors"] += 1
                    log(
                        f"[+{elapsed}s / -{remaining}s] READ DeviceIDError! "
                        f"(retry handled)"
                    )
                    log(f"  device_id after retry: {token.device_id[:8]}...")
                    if token.device_id != last_device_id:
                        device_id_log.append(
                            (
                                now,
                                last_device_id,
                                token.device_id,
                                "DeviceIDError-retry",
                            )
                        )
                        stats["device_id_changes"].append(now - start_time)
                        last_device_id = token.device_id
                except requests_lib.exceptions.Timeout:
                    stats["read_timeouts"] += 1
                    stats["read_errors"] += 1
                    log(
                        f"[+{elapsed}s / -{remaining}s] READ TIMEOUT "
                        f"(> {REQUEST_TIMEOUT}s)"
                    )
                except Exception as e:
                    stats["read_errors"] += 1
                    log(
                        f"[+{elapsed}s / -{remaining}s] Read error: "
                        f"{type(e).__name__}: {e}"
                    )

            # Control command (only lock — safe and reversible)
            if now - last_control >= CONTROL_INTERVAL:
                last_control = now
                stats["control_commands"] += 1
                try:
                    action_id, ms = timed_call(
                        api.lock_action,
                        token,
                        vehicle,
                        VEHICLE_LOCK_ACTION.LOCK,
                    )
                    log(
                        f"[+{elapsed}s / -{remaining}s] Lock OK {ms}ms: "
                        f"actionId={action_id} "
                        f"(device_id={token.device_id[:8]}...)"
                    )

                    # Poll action status — this is where DeviceIDError
                    # is expected to manifest (mid-polling after push failure)
                    log(f"  Polling action status for {action_id}...")
                    completed = poll_action_status(
                        api, token, vehicle, action_id, stats, start_time
                    )
                    stats["action_polls"] += 1
                    if completed:
                        stats["action_poll_complete"] += 1

                    # Check if device_id changed during polling
                    if token.device_id != last_device_id:
                        device_id_log.append(
                            (
                                time.time(),
                                last_device_id,
                                token.device_id,
                                "action-poll",
                            )
                        )
                        stats["device_id_changes"].append(time.time() - start_time)
                        last_device_id = token.device_id

                except DeviceIDError:
                    stats["device_id_errors"] += 1
                    stats["control_errors"] += 1
                    log(f"[+{elapsed}s / -{remaining}s] LOCK DeviceIDError!")
                    log(f"  device_id after retry: {token.device_id[:8]}...")
                    if token.device_id != last_device_id:
                        device_id_log.append(
                            (
                                now,
                                last_device_id,
                                token.device_id,
                                "DeviceIDError-retry",
                            )
                        )
                        stats["device_id_changes"].append(now - start_time)
                        last_device_id = token.device_id
                except DuplicateRequestError:
                    stats["duplicate_request_errors"] += 1
                    log(
                        f"[+{elapsed}s / -{remaining}s] Lock "
                        f"DuplicateRequestError (rate limit, expected)"
                    )
                except requests_lib.exceptions.Timeout:
                    stats["control_errors"] += 1
                    log(
                        f"[+{elapsed}s / -{remaining}s] Lock TIMEOUT "
                        f"(> {REQUEST_TIMEOUT}s)"
                    )
                except Exception as e:
                    stats["control_errors"] += 1
                    log(
                        f"[+{elapsed}s / -{remaining}s] Lock error: "
                        f"{type(e).__name__}: {e}"
                    )

            time.sleep(5)

    except KeyboardInterrupt:
        log("Interrupted by user")

    # Final summary
    print()
    print("=" * 60)
    log("STRESS TEST SUMMARY")
    print("=" * 60)
    total_time = int(time.time() - start_time)
    log(f"Duration: {total_time // 60}m {total_time % 60}s")
    log(
        f"Read operations: {stats['reads']} "
        f"({stats['read_errors']} errors, {stats['read_timeouts']} timeouts)"
    )
    log(
        f"Control commands: {stats['control_commands']} "
        f"({stats['control_errors']} errors)"
    )
    log(
        f"Action status polls: {stats['action_polls']} "
        f"({stats['action_poll_complete']} complete, "
        f"{stats['action_poll_errors']} errors, "
        f"{stats['action_poll_device_id_changes']} device_id changes mid-poll)"
    )
    log(f"DeviceIDErrors total: {stats['device_id_errors']}")
    log(f"DuplicateRequestErrors: {stats['duplicate_request_errors']}")
    log(f"Device ID changes: {len(stats['device_id_changes'])}")
    if device_id_log:
        print()
        log("Device ID change timeline:")
        for ts, old, new, trigger in device_id_log:
            elapsed = int(ts - start_time)
            log(f"  +{elapsed}s: {old[:12]}... -> {new[:12]}... (trigger: {trigger})")
    else:
        log("Device ID was STABLE throughout the test!")

    log(f"Final device_id: {token.device_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
