#!/usr/bin/env python3

"""Connects to the Bluelink API and query the vehicle."""

import argparse
import dataclasses
import datetime
import getpass
import json
import logging
import os
import sys
import textwrap
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hyundai_kia_connect_api
from hyundai_kia_connect_api import const


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        return super().default(o)


def print_vehicle(vehicle: hyundai_kia_connect_api.Vehicle) -> None:
    print("Identification")
    print("  id:", vehicle.id)
    print("  name:", vehicle.name)
    print("  model:", vehicle.model)
    print("  registration_date:", vehicle.registration_date)
    print("  year:", vehicle.year)
    print("  VIN:", vehicle.VIN)
    print("  key:", vehicle.key)
    print("General")
    print("  engine_type:", vehicle.engine_type)
    print("  ccu_ccs2_protocol_support:", vehicle.ccu_ccs2_protocol_support)
    print(
        "  total_driving_range:",
        vehicle.total_driving_range,
        vehicle.total_driving_range_unit,
    )
    print("  odometer:", vehicle.odometer, vehicle.odometer_unit)
    print("  geocode:", vehicle.geocode)
    print("  car_battery_percentage:", vehicle.car_battery_percentage)
    print("  engine_is_running:", vehicle.engine_is_running)
    print("  last_updated_at:", vehicle.last_updated_at)
    print("  timezone:", vehicle.timezone)
    print("  dtc_count:", vehicle.dtc_count)
    print("  dtc_descriptions:", vehicle.dtc_descriptions)
    print("  smart_key_battery_warning_is_on:", vehicle.smart_key_battery_warning_is_on)
    print("  washer_fluid_warning_is_on:", vehicle.washer_fluid_warning_is_on)
    print("  brake_fluid_warning_is_on:", vehicle.brake_fluid_warning_is_on)
    print("Climate")
    print("  air_temperature:", vehicle.air_temperature, vehicle._air_temperature_unit)
    print("  air_control_is_on:", vehicle.air_control_is_on)
    print("  defrost_is_on:", vehicle.defrost_is_on)
    print("  steering_wheel_heater_is_on:", vehicle.steering_wheel_heater_is_on)
    print("  back_window_heater_is_on:", vehicle.back_window_heater_is_on)
    print("  side_mirror_heater_is_on:", vehicle.side_mirror_heater_is_on)
    print("  front_left_seat_status:", vehicle.front_left_seat_status)
    print("  front_right_seat_status:", vehicle.front_right_seat_status)
    print("  rear_left_seat_status:", vehicle.rear_left_seat_status)
    print("  rear_right_seat_status:", vehicle.rear_right_seat_status)
    print("Doors")
    print("  is_locked:", vehicle.is_locked)
    print("  front_left_door_is_open:", vehicle.front_left_door_is_open)
    print("  front_right_door_is_open:", vehicle.front_right_door_is_open)
    print("  back_left_door_is_open:", vehicle.back_left_door_is_open)
    print("  back_right_door_is_open:", vehicle.back_right_door_is_open)
    print("  trunk_is_open:", vehicle.trunk_is_open)
    print("  hood_is_open:", vehicle.hood_is_open)
    print("Windows")
    print("  front_left_window_is_open:", vehicle.front_left_window_is_open)
    print("  front_right_window_is_open:", vehicle.front_right_window_is_open)
    print("  back_left_window_is_open:", vehicle.back_left_window_is_open)
    print("  back_right_window_is_open:", vehicle.back_right_window_is_open)
    print("Tire Pressure")
    print("  tire_pressure_all_warning_is_on:", vehicle.tire_pressure_all_warning_is_on)
    print(
        "  tire_pressure_rear_left_warning_is_on:",
        vehicle.tire_pressure_rear_left_warning_is_on,
    )
    print(
        "  tire_pressure_front_left_warning_is_on:",
        vehicle.tire_pressure_front_left_warning_is_on,
    )
    print(
        "  tire_pressure_front_right_warning_is_on:",
        vehicle.tire_pressure_front_right_warning_is_on,
    )
    print(
        "  tire_pressure_rear_right_warning_is_on:",
        vehicle.tire_pressure_rear_right_warning_is_on,
    )
    print("Service")
    print(
        "  next_service_distance:",
        vehicle.next_service_distance,
        vehicle._next_service_distance_unit,
    )
    print(
        "  last_service_distance:",
        vehicle.last_service_distance,
        vehicle._last_service_distance_unit,
    )
    print("Location")
    print("  location:", vehicle.location)
    print("  location_last_updated_at:", vehicle.location_last_updated_at)
    print("EV/PHEV")
    print("  charge_port_door_is_open:", vehicle.ev_charge_port_door_is_open)
    print("  charging_power:", vehicle.ev_charging_power)
    print("  charge_limits_dc:", vehicle.ev_charge_limits_dc)
    print("  charge_limits_ac:", vehicle.ev_charge_limits_ac)
    print("  charging_current:", vehicle.ev_charging_current)
    print("  v2l_discharge_limit:", vehicle.ev_v2l_discharge_limit)
    print("  total_power_consumed:", vehicle.total_power_consumed, "Wh")
    print("  total_power_regenerated:", vehicle.total_power_regenerated, "Wh")
    print("  power_consumption_30d:", vehicle.power_consumption_30d, "Wh")
    print("  battery_percentage:", vehicle.ev_battery_percentage)
    print("  battery_soh_percentage:", vehicle.ev_battery_soh_percentage)
    print("  battery_remain:", vehicle.ev_battery_remain)
    print("  battery_capacity:", vehicle.ev_battery_capacity)
    print("  battery_is_charging:", vehicle.ev_battery_is_charging)
    print("  battery_is_plugged_in:", vehicle.ev_battery_is_plugged_in)
    print("  driving_range:", vehicle.ev_driving_range, vehicle._ev_driving_range_unit)
    print(
        "  estimated_current_charge_duration:",
        vehicle.ev_estimated_current_charge_duration,
        vehicle._ev_estimated_current_charge_duration_unit,
    )
    print(
        "  estimated_fast_charge_duration:",
        vehicle.ev_estimated_fast_charge_duration,
        vehicle._ev_estimated_fast_charge_duration_unit,
    )
    print(
        "  estimated_portable_charge_duration:",
        vehicle.ev_estimated_portable_charge_duration,
        vehicle._ev_estimated_portable_charge_duration_unit,
    )
    print(
        "  estimated_station_charge_duration:",
        vehicle.ev_estimated_station_charge_duration,
        vehicle._ev_estimated_station_charge_duration_unit,
    )
    print(
        "  target_range_charge_AC:",
        vehicle.ev_target_range_charge_AC,
        vehicle._ev_target_range_charge_AC_unit,
    )
    print(
        "  target_range_charge_DC:",
        vehicle.ev_target_range_charge_DC,
        vehicle._ev_target_range_charge_DC_unit,
    )

    print("  first_departure_enabled:", vehicle.ev_first_departure_enabled)
    print(
        "  first_departure_climate_temperature:",
        vehicle.ev_first_departure_climate_temperature,
        vehicle._ev_first_departure_climate_temperature_unit,
    )
    print("  first_departure_days:", vehicle.ev_first_departure_days)
    print("  first_departure_time:", vehicle.ev_first_departure_time)
    print(
        "  first_departure_climate_enabled:", vehicle.ev_first_departure_climate_enabled
    )
    print(
        "  first_departure_climate_defrost:", vehicle.ev_first_departure_climate_defrost
    )
    print("  second_departure_enabled:", vehicle.ev_second_departure_enabled)
    print(
        "  second_departure_climate_temperature:",
        vehicle.ev_second_departure_climate_temperature,
        vehicle._ev_second_departure_climate_temperature_unit,
    )
    print("  second_departure_days:", vehicle.ev_second_departure_days)
    print("  second_departure_time:", vehicle.ev_second_departure_time)
    print(
        "  second_departure_climate_enabled:",
        vehicle.ev_second_departure_climate_enabled,
    )
    print(
        "  second_departure_climate_defrost:",
        vehicle.ev_second_departure_climate_defrost,
    )
    print("  off_peak_start_time:", vehicle.ev_off_peak_start_time)
    print("  off_peak_end_time:", vehicle.ev_off_peak_end_time)
    print("  off_peak_charge_only_enabled:", vehicle.ev_off_peak_charge_only_enabled)
    print("  schedule_charge_enabled:", vehicle.ev_schedule_charge_enabled)
    print("PHEV/HEV/IC")
    print(
        "  fuel_driving_range:",
        vehicle.fuel_driving_range,
        vehicle._fuel_driving_range_unit,
    )
    print("  fuel_level:", vehicle.fuel_level)
    print("  fuel_level_is_low:", vehicle.fuel_level_is_low)
    print("Trips")
    print("  daily_stats:", vehicle.daily_stats)
    print("  month_trip_info:", vehicle.month_trip_info)
    print("  day_trip_info:", vehicle.day_trip_info)
    print("Debug")
    print(
        textwrap.indent(
            json.dumps(vehicle.data, indent=2, sort_keys=True, cls=EnhancedJSONEncoder),
            " ",
        )
    )


def vehicle_to_dict(vehicle: hyundai_kia_connect_api.Vehicle) -> dict[str, Any]:
    return {
        "identification": {
            "id": vehicle.id,
            "name": vehicle.name,
            "model": vehicle.model,
            "registration_date": vehicle.registration_date,
            "year": vehicle.year,
            "VIN": vehicle.VIN,
            "key": vehicle.key,
        },
        "general": {
            "engine_type": str(vehicle.engine_type),
            "ccu_ccs2_protocol_support": vehicle.ccu_ccs2_protocol_support,
            "total_driving_range": [
                vehicle.total_driving_range,
                vehicle.total_driving_range_unit,
            ],
            "odometer": [vehicle.odometer, vehicle.odometer_unit],
            "geocode": vehicle.geocode,
            "car_battery_percentage": vehicle.car_battery_percentage,
            "engine_is_running": vehicle.engine_is_running,
            "last_updated_at": vehicle.last_updated_at,
            "timezone": vehicle.timezone,
            "dtc_count": vehicle.dtc_count,
            "dtc_descriptions": vehicle.dtc_descriptions,
            "smart_key_battery_warning_is_on": vehicle.smart_key_battery_warning_is_on,
            "washer_fluid_warning_is_on": vehicle.washer_fluid_warning_is_on,
            "brake_fluid_warning_is_on": vehicle.brake_fluid_warning_is_on,
        },
        "climate": {
            "air_temperature": [
                vehicle.air_temperature,
                vehicle._air_temperature_unit,
            ],
            "air_control_is_on": vehicle.air_control_is_on,
            "defrost_is_on": vehicle.defrost_is_on,
            "steering_wheel_heater_is_on": vehicle.steering_wheel_heater_is_on,
            "back_window_heater_is_on": vehicle.back_window_heater_is_on,
            "side_mirror_heater_is_on": vehicle.side_mirror_heater_is_on,
            "front_left_seat_status": vehicle.front_left_seat_status,
            "front_right_seat_status": vehicle.front_right_seat_status,
            "rear_left_seat_status": vehicle.rear_left_seat_status,
            "rear_right_seat_status": vehicle.rear_right_seat_status,
        },
        "doors": {
            "is_locked": vehicle.is_locked,
            "front_left_door_is_open": vehicle.front_left_door_is_open,
            "front_right_door_is_open": vehicle.front_right_door_is_open,
            "back_left_door_is_open": vehicle.back_left_door_is_open,
            "back_right_door_is_open": vehicle.back_right_door_is_open,
            "trunk_is_open": vehicle.trunk_is_open,
            "hood_is_open": vehicle.hood_is_open,
        },
        "windows": {
            "front_left_window_is_open": vehicle.front_left_window_is_open,
            "front_right_window_is_open": vehicle.front_right_window_is_open,
            "back_left_window_is_open": vehicle.back_left_window_is_open,
            "back_right_window_is_open": vehicle.back_right_window_is_open,
        },
        "tires": {
            "tire_pressure_all_warning_is_on": vehicle.tire_pressure_all_warning_is_on,
            "tire_pressure_rear_left_warning_is_on": vehicle.tire_pressure_rear_left_warning_is_on,
            "tire_pressure_front_left_warning_is_on": vehicle.tire_pressure_front_left_warning_is_on,
            "tire_pressure_front_right_warning_is_on": vehicle.tire_pressure_front_right_warning_is_on,
            "tire_pressure_rear_right_warning_is_on": vehicle.tire_pressure_rear_right_warning_is_on,
        },
        "service": {
            "next_service_distance": [
                vehicle.next_service_distance,
                vehicle._next_service_distance_unit,
            ],
            "last_service_distance": [
                vehicle.last_service_distance,
                vehicle._last_service_distance_unit,
            ],
        },
        "location": {
            "location": vehicle.location,
            "location_last_updated_at": vehicle.location_last_updated_at,
        },
        "electric": {
            "charge_port_door_is_open": vehicle.ev_charge_port_door_is_open,
            "charging_power": vehicle.ev_charging_power,
            "charge_limits_dc": vehicle.ev_charge_limits_dc,
            "charge_limits_ac": vehicle.ev_charge_limits_ac,
            "charging_current": vehicle.ev_charging_current,
            "v2l_discharge_limit": vehicle.ev_v2l_discharge_limit,
            "total_power_consumed": [vehicle.total_power_consumed, "Wh"],
            "total_power_regenerated": [vehicle.total_power_regenerated, "Wh"],
            "power_consumption_30d": [vehicle.power_consumption_30d, "Wh"],
            "battery_percentage": vehicle.ev_battery_percentage,
            "battery_soh_percentage": vehicle.ev_battery_soh_percentage,
            "battery_remain": vehicle.ev_battery_remain,
            "battery_capacity": vehicle.ev_battery_capacity,
            "battery_is_charging": vehicle.ev_battery_is_charging,
            "battery_is_plugged_in": vehicle.ev_battery_is_plugged_in,
            "driving_range": [
                vehicle.ev_driving_range,
                vehicle._ev_driving_range_unit,
            ],
            "estimated_current_charge_duration": [
                vehicle.ev_estimated_current_charge_duration,
                vehicle._ev_estimated_current_charge_duration_unit,
            ],
            "estimated_fast_charge_duration": [
                vehicle.ev_estimated_fast_charge_duration,
                vehicle._ev_estimated_fast_charge_duration_unit,
            ],
            "estimated_portable_charge_duration": [
                vehicle.ev_estimated_portable_charge_duration,
                vehicle._ev_estimated_portable_charge_duration_unit,
            ],
            "estimated_station_charge_duration": [
                vehicle.ev_estimated_station_charge_duration,
                vehicle._ev_estimated_station_charge_duration_unit,
            ],
            "target_range_charge_AC": [
                vehicle.ev_target_range_charge_AC,
                vehicle._ev_target_range_charge_AC_unit,
            ],
            "target_range_charge_DC": [
                vehicle.ev_target_range_charge_DC,
                vehicle._ev_target_range_charge_DC_unit,
            ],
            "first_departure_enabled": vehicle.ev_first_departure_enabled,
            "first_departure_climate_temperature": [
                vehicle.ev_first_departure_climate_temperature,
                vehicle._ev_first_departure_climate_temperature_unit,
            ],
            "first_departure_days": vehicle.ev_first_departure_days,
            "first_departure_time": vehicle.ev_first_departure_time,
            "first_departure_climate_enabled": vehicle.ev_first_departure_climate_enabled,
            "first_departure_climate_defrost": vehicle.ev_first_departure_climate_defrost,
            "second_departure_enabled": vehicle.ev_second_departure_enabled,
            "second_departure_climate_temperature": [
                vehicle.ev_second_departure_climate_temperature,
                vehicle._ev_second_departure_climate_temperature_unit,
            ],
            "second_departure_days": vehicle.ev_second_departure_days,
            "second_departure_time": vehicle.ev_second_departure_time,
            "second_departure_climate_enabled": vehicle.ev_second_departure_climate_enabled,
            "second_departure_climate_defrost": vehicle.ev_second_departure_climate_defrost,
            "off_peak_start_time": vehicle.ev_off_peak_start_time,
            "off_peak_end_time": vehicle.ev_off_peak_end_time,
            "off_peak_charge_only_enabled": vehicle.ev_off_peak_charge_only_enabled,
            "schedule_charge_enabled": vehicle.ev_schedule_charge_enabled,
        },
        "ic": {
            "fuel_driving_range": [
                vehicle.fuel_driving_range,
                vehicle._fuel_driving_range_unit,
            ],
            "fuel_level": vehicle.fuel_level,
            "fuel_level_is_low": vehicle.fuel_level_is_low,
        },
        "trips": {
            "daily_stats": vehicle.daily_stats,
            "month_trip_info": vehicle.month_trip_info,
            "day_trip_info": vehicle.day_trip_info,
        },
        "debug": vehicle.data,
    }


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()


def cmd_info(
    vm: hyundai_kia_connect_api.VehicleManager, args: argparse.Namespace
) -> int:
    for vehicle in vm.vehicles.values():
        print_vehicle(vehicle)
    if args.json:
        data = {id: vehicle_to_dict(v) for id, v in vm.vehicles.items()}
        json.dump(data, args.json, separators=(",", ":"), cls=DateTimeEncoder, indent=4)
    return 0


def cmd_dashboard(
    vm: hyundai_kia_connect_api.VehicleManager, args: argparse.Namespace
) -> int:
    from . import dashboard

    def build() -> dict[str, Any]:
        if built:  # the first build reuses the state main() just fetched
            _progress("Refreshing status")
            vm.check_and_refresh_token()
            vm.update_all_vehicles_with_cached_state()
        built.append(True)
        data = {
            "generated_at": datetime.datetime.now(),
            "region": args.region,
            "brand": args.brand,
            "vehicles": [
                {
                    **vehicle_to_dict(v),
                    "trip_history": dashboard.collect_trips(
                        vm,
                        v.id,
                        args.months,
                        _car_progress(v.name),
                    ),
                }
                for v in vm.vehicles.values()
            ],
        }
        _progress("Done", end=True)
        return data

    built: list[bool] = []
    if not args.output:
        dashboard.serve(build, open_browser=args.open)
        return 0
    path = Path(args.output).resolve()
    path.write_text(dashboard.render(build()), encoding="utf-8")
    print(f"Dashboard written to {path}")
    print("It contains your VIN and locations. Don't share the file.")
    if args.open:
        webbrowser.open(path.as_uri())
    return 0


_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_spin_step = [0]


def _progress(msg: str, end: bool = False) -> None:
    """One status line that updates in place in a terminal."""
    if not sys.stdout.isatty():
        print(msg, flush=True)
        return
    _spin_step[0] += 1
    mark = "✓" if end else _SPIN[_spin_step[0] % len(_SPIN)]
    print(f"\r\033[K{mark} {msg}", end="\n" if end else "", flush=True)


def _car_progress(name: str | None) -> Callable[[str], None]:
    return lambda msg: _progress(f"{name}: {msg}")


def _ask_choice(label: str, choices: list[str]) -> str:
    for i, choice in enumerate(choices, 1):
        print(f"  {i}. {choice}")
    while True:
        answer = input(f"{label} [1-{len(choices)}]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]


def _ask_missing(args: argparse.Namespace) -> None:
    """Prompt in a terminal for whatever the flags and env vars didn't give."""
    interactive = args.func is cmd_dashboard and sys.stdin.isatty()
    args.prompted = False
    if interactive:
        from . import saved_login

        saved = saved_login.load()
        if saved and args.username in (None, "", saved.get("username")):
            for name in ("region", "brand", "username", "password", "pin"):
                if not getattr(args, name) and saved.get(name):
                    setattr(args, name, saved[name])
            if saved.get("password"):
                print(
                    f"Using the saved login for {saved['username']} (roadbook --forget to clear it)"
                )
    if args.region is None:
        regions = sorted(const.REGIONS.values())
        args.region = (
            _ask_choice("Region", regions) if interactive else const.REGION_CANADA
        )
    if args.brand is None:
        brands = sorted(const.BRANDS.values())
        args.brand = (
            _ask_choice("Brand", brands) if interactive else const.BRAND_HYUNDAI
        )
    if not interactive:
        return
    if not args.username:
        args.username = input("Username (email or phone): ").strip()
        args.prompted = True
    if not args.password:
        args.password = getpass.getpass("Password: ")
        args.prompted = True
    if not args.pin:
        args.pin = getpass.getpass("PIN: ").strip()
        args.prompted = True


def _offer_to_save(args: argparse.Namespace) -> None:
    """After a successful sign-in, offer to skip the questions next time."""
    if not getattr(args, "prompted", False):
        return
    from . import saved_login

    answer = input("Save these details for next time? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        return
    secret_saved = saved_login.save(
        args.region, args.brand, args.username, args.password, args.pin
    )
    where = saved_login.path()
    if secret_saved:
        print(
            f"Saved. Password and PIN are in your system's password store; the rest is in {where}."
        )
    else:
        print(f"Saved region, brand and username in {where}.")
        print("To save the password and PIN too, install keyring: pip install keyring")


def main(argv: list[str] | None = None) -> int:
    default_username = os.environ.get("BLUELINK_USERNAME", "")
    default_password = os.environ.get("BLUELINK_PASSWORD", "")
    default_pin = None
    if os.environ.get("BLUELINK_PIN", ""):
        try:
            default_pin = str(os.environ["BLUELINK_PIN"])
        except ValueError:
            print("Invalid BLUELINK_PIN environment variable", file=sys.stderr)
            return 1

    def add_login_args(p: argparse.ArgumentParser, defaults: bool) -> None:
        def d(value: Any) -> Any:
            # Subcommand copies stay unset so they don't override the top-level values.
            return value if defaults else argparse.SUPPRESS

        p.add_argument(
            "--region",
            default=d(os.environ.get("BLUELINK_REGION")),
            choices=sorted(const.REGIONS.values()),
            help="Car's region, use env var BLUELINK_REGION",
        )
        p.add_argument(
            "--brand",
            default=d(os.environ.get("BLUELINK_BRAND")),
            choices=sorted(const.BRANDS.values()),
            help="Car's brand, use env var BLUELINK_BRAND",
        )
        p.add_argument(
            "--username",
            default=d(default_username),
            help="Bluelink account username, use env var BLUELINK_USERNAME",
        )
        p.add_argument(
            "--password",
            default=d(default_password),
            help="Bluelink account password, use env var BLUELINK_PASSWORD",
        )
        p.add_argument(
            "--pin",
            type=str,
            default=d(default_pin),
            help="Bluelink account pin, use env var BLUELINK_PIN",
        )

    parser = argparse.ArgumentParser(description=sys.modules[__name__].__doc__)
    add_login_args(parser, defaults=True)
    parser.add_argument("-v", "--verbose", action=argparse.BooleanOptionalAction)
    subparsers = parser.add_subparsers(help="Commands", required=True)
    parser_info = subparsers.add_parser(
        "info", help="Prints infos about the cars found"
    )
    parser_info.set_defaults(func=cmd_info)
    parser_info.add_argument(
        "--json",
        type=argparse.FileType("w", encoding="UTF-8"),
        help="Save data to file as JSON",
    )

    parser_dashboard = subparsers.add_parser(
        "dashboard",
        help="Builds an HTML dashboard of your cars and trips and opens it. "
        "Asks for any login details not given.",
    )
    parser_dashboard.set_defaults(func=cmd_dashboard)
    add_login_args(parser_dashboard, defaults=False)
    parser_dashboard.add_argument(
        "--output",
        help="Write the page to this HTML file and exit, instead of serving it "
        "locally with a working Refresh button",
    )
    parser_dashboard.add_argument(
        "--months", type=int, default=3, help="Months of trip history to fetch"
    )
    parser_dashboard.add_argument(
        "--forget", action="store_true", help="Delete the saved login and exit"
    )
    parser_dashboard.add_argument(
        "--open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Open the dashboard in a browser",
    )

    args = parser.parse_args(argv)
    if getattr(args, "forget", False):
        from . import saved_login

        print("Saved login deleted." if saved_login.forget() else "No saved login.")
        return 0
    _ask_missing(args)
    for name in ("username", "password", "pin"):
        if not getattr(args, name):
            parser.error(f"--{name} is required (or set BLUELINK_{name.upper()})")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.ERROR)

    # Reverse lookup.
    region = next(k for k, v in const.REGIONS.items() if v == args.region)
    brand = next(k for k, v in const.BRANDS.items() if v == args.brand)

    vm = hyundai_kia_connect_api.VehicleManager(
        region=region,
        brand=brand,
        username=args.username,
        password=args.password,
        pin=args.pin,
        geocode_api_enable=True,
        geocode_api_use_email=True,
    )
    dashboard_run = args.func is cmd_dashboard
    if dashboard_run:
        _progress(f"Signing in to {args.brand} ({args.region})")
    vm.check_and_refresh_token()
    if dashboard_run:
        print("\r\033[K" if sys.stdout.isatty() else "", end="")
        _offer_to_save(args)
        _progress("Reading your cars' latest status")
    vm.update_all_vehicles_with_cached_state()
    result: int = args.func(vm, args)
    return result


def roadbook() -> int:
    """`roadbook [options]`: the same as `bluelink dashboard [options]`."""
    return main(["dashboard", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
