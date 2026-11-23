Code Maintainers Wanted
=======================

I no longer have a Kia or Hyundai so don't maintain this like I used to.  Others who are interested in jumping in are welcome to join the project! Even just pull requests are appreciated!

Introduction
============

This is a Kia UVO, Hyundai Bluelink, Genesis Connect(Canada Only) written in python.  It is primary consumed by home assistant.  If you are looking for a home assistant Kia / Hyundai implementation please look here: https://github.com/Hyundai-Kia-Connect/kia_uvo.  Much of this base code came from reading `bluelinky <https://github.com/Hacksore/bluelinky>`_ and contributions to the kia_uvo home assistant project.

Chat on discord:: |Discord|

.. |Discord| image:: https://img.shields.io/discord/652755205041029120
   :target: https://discord.gg/HwnG8sY
   :alt: Discord

API Usage
=========

This package is designed to simplify the complexity of using multiple regions.  It attempts to standardize the usage regardless of what brand or region the car is in.  That isn't always possible though, in particular some features differ from one to the next.

Europe username/password login is supported directly for Kia, Hyundai, and Genesis (EU).  No browser or manual token extraction is needed. The ``pycryptodome`` package (included as a dependency) is used for RSA password encryption during the EU login flow.

Europe (CCI/GSPA, region 9) specifics and command gates
+++++++++++++++++++++++++++++++++++++++++++++++++++++++

Region 9 (Europe (Beta)) vehicles use the CCI login flow and the GSPA REST layer
(``KiaCciApiEU`` / ``HyundaiCciApiEU`` / ``GenesisCciApiEU``), shared helper modules
``driving_insight_eu`` and ``cci_auth``.  Notes current on EU (2026-11):

- **Status reads:** the cached update reads the GSPA stored-status tree plus a
  small set of cheap server reads (software version, OTA, valet, and the Driving
  Insight family for vehicles whose ``supportedFeatures`` enable them).  The
  Driving Insight reads are once-per-cycle (force refresh re-reads); the tire
  ``tire/bound`` fallback for parked cars (stored-status carries the 255
  "no reading" sentinel) is likewise one-shot per cycle.
- **Tire pressure while parked:** the stored-status carries a 255 sentinel for
  all four tires when the car is parked.  Those become ``unknown``, and the
  last measured values come from the DI ``tire/bound`` read (display-unit
  values, firstUnit).  ``Vehicle.tire_pressure_last_updated_at`` marks when the
  DI record was measured.
- **Trips:** ``month_trip_info``/``day_trip_info`` for Kia EU CCI come from the
  Driving Insight API (the GSPA ``driving-info``/``driving-history`` endpoints
  answer 500 server-side on Kia).  Trip times come from the car; on long trips
  the reported start time can be the upload time.
- **Command gates:** every remote command is gated twice — at the brand/feature
  level (``GSPA_VERIFIED_ENDPOINTS`` per brand class; commands raise
  ``NotImplementedError`` until live-verified) and per-vehicle (the carinfo
  ``controlOption`` flags derived from ``option``/``serviceOption``, exposed as
  ``Vehicle.supports.<item>``).  A derived ``False`` raises
  ``UnsupportedControlError`` before the request is sent; ``None``/``True``
  proceed.
- **Error families seen live on EU CCI (2026-11):**

  - ``400-004`` duplicate request — the GSPA command queue holds one command;
    spacing under ~15 s (longer after ``update-status``) answers 400-004.
  - ``402-002`` — remote-control subscription/plan lapsed for the VIN (doors,
    lamp/hazard, horn answer this; the command queue still accepts valet).
  - ``404-002`` — the endpoint is not routable for this vehicle at all
    (capability-gated; e.g. climate on ICE/HEV family cars).
  - ``428-002`` — service not provisioned for the VIN on the push/RCS path.
  - ``500-000`` on the remote endpoints also answers for wire-shape errors
    (e.g. a boolean where the app sends an int).
- ``update-status`` replies with a bare ``{SID}`` acknowledgment — no command
  result travels on it; outcomes arrive via a fresh stored-status read or push.
- Full EU error-code reference with the app-side code names:
  ``EU_ERROR_CODE_TABLE.md`` in the repository root.

China (Hyundai Bluelink / Kia UVO CN)
-------------------------------------

China support was rewritten in 2026 against the current China Bluelink iOS app (com.hyundai-motor.cn.bluelink).  Highlights and differences vs other regions:

- Login uses a five-step flow through the UARS service (uars-{k|h}.hmgmobility.com.cn); the OAuth authorization code is exchanged server-side by UARS and the tokens are returned inside the callback page. Only the Hyundai brand has been live-verified; Kia constants are included from the app binary but are untested.
- Access tokens live 6 hours.  There is no working refresh_token grant on the China servers, so ``refresh_access_token`` re-authenticates silently when possible and otherwise performs a full password login (same pattern as Canada).
- Remote control on non-CCS2 vehicles uses the plain access token; a PIN is only required for CCS2-style control commands.  ``pin`` should be passed to ``VehicleManager`` as usual.
- Push device registration uses ``pushType: APNS`` (not GCM) and requires a ``providerDeviceId`` field.
- Control-command latency is high (1-2 minutes to reach SUCCESS); consumers should use generous polling timeouts.

Python 3.13 or newer is required to use this package. Vehicle manager is the key class that is called to manage the vehicle lists.  One vehicle manager should be used per login. Key data points required to instantiate vehicle manager are::

    region: int
    brand: int,
    username: str
    password: str
    pin: str (required for CA, and potentially USA, otherwise pass a blank string)

    Optional parameters are::
    geocode_api_enable: bool
    geocode_api_use_email: bool
    geocode_provider: int
    geocode_api_key: str
    language: str

Key values for the int exist in the `const.py <https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/blob/master/hyundai_kia_connect_api/const.py>`_ file as::

    REGIONS = {1: REGION_EUROPE, 2: REGION_CANADA, 3: REGION_USA, 4: REGION_CHINA, 5: REGION_AUSTRALIA, 6: REGION_INDIA, 7: REGION_NZ, 8: REGION_BRAZIL, 9: REGION_EUROPE_CCI}
    BRANDS = {1: BRAND_KIA, 2: BRAND_HYUNDAI, 3: BRAND_GENESIS}
    GEO_LOCATION_PROVIDERS = {1: OPENSTREETMAP, 2: GOOGLE}


Once this is done you can now make the following calls against the vehicle manager::


 #login

 login(self)

 #OTP Details

 #Sent OTP
 send_otp(self, method)

 #Verify OTP
 verify_otp(self, otp_code)

 #Checks the token is still valid and updates it if not.  Should be called before anything else if the code has been running for any length of time.
 check_and_refresh_token(self)

 Ideal refresh command. Checks if the car has been updated since the time in seconds provided.  If so does a cached update. If not force calls the car.
 check_and_force_update_vehicles(self, force_refresh_interval) # Interval in seconds - consider API Rate Limits https://github.com/Hacksore/bluelinky/wiki/API-Rate-Limits

 Used to return a specific vehicle object:
 get_vehicle(self, vehicle_id)

 #Updates all cars with what is cached in the cloud:
 update_all_vehicles_with_cached_state(self)

 Updates a specific car with cached state:
 update_vehicle_with_cached_state(self, vehicle_id)

 Force refreshes all cars:
 force_refresh_all_vehicles_states(self)

 Force refreshes a single car:
 force_refresh_vehicles_states(self, vehicle_id)

An example call would be::

    from hyundai_kia_connect_api import *
    vm = VehicleManager(region=2, brand=1, username="username@gmail.com", password="password", pin="1234")
    vm.check_and_refresh_token()
    vm.update_all_vehicles_with_cached_state()
    print(vm.vehicles)

If geolocation is required you can also allow this by running::

    vm = VehicleManager(region=2, brand=1, username="username@gmail.com", password="password", pin="1234", geocode_api_enable=True, geocode_api_use_email=True)

This will populate the address of the vehicle in the vehicle instance.

Remote climate options
----------------------

``start_climate`` takes seat, steering wheel, heating and mirror values through
``ClimateRequestOptions``. Which body field carries the rear-window and mirror
heating depends on the vehicle path:

- EU CCS2 and EU GSPA vehicles: the ``heating`` option (1, 2 or 4) sends
  ``sideRearMirrorHeating: 1``; 0 and 3 leave it off. Confirmed by single-field
  tests in issue #1398.
- Legacy EU (pre-CCS2) and other regions: the ``heating`` option sends
  ``heating1``, as before. On the legacy EU path an explicit
  ``side_rear_mirror_heating`` can also be sent in the same request.

An explicit ``side_rear_mirror_heating`` value takes precedence over the
``heating`` mapping.

MQTT push (Europe CCI/GSPA)
---------------------------

For Europe CCI/GSPA accounts the vehicle manager can receive server pushes over MQTT instead of polling. Push is receive-only: it never sends vehicle commands.

::

    vm = VehicleManager(region=9, brand=1, username="...", password="...", pin="...")
    vm.check_and_refresh_token()
    vm.initialize_vehicles()
    vm.set_push_connection_change_callback(lambda connected: print("push connected:", connected))
    vm.set_push_status_callback(lambda vehicle_id, topic_type: print(vm.get_vehicle(vehicle_id)))
    vm.start_push(vm.vehicles[vehicle_id].id)

- ``start_push`` returns False when push is unavailable (unsupported region or account); connection problems surface in the connection-change callback and the manager reconnects with capped backoff.
- The status callback fires per status push with ``(vehicle_id, topic_type)``.
- ``stop_push`` disconnects; the callbacks stay registered unless replaced.


The Bluelink App is reset to English for users who have set another language in the Bluelink App in Europe when using hyundai_kia_connect_api.
To avoid this, you can pass the optional parameter language (default is "en") to the constructor of VehicleManager, e.g. for Dutch::

    vm = VehicleManager(region=2, brand=1, username="username@gmail.com", password="password", pin="1234", language="nl")

Note: this is only implemented for Europe currently.

For a list of language codes, see here: https://www.science.co.il/language/Codes.php. Currently in Europe the Bluelink App shows the following languages::

- "en" English
- "de" German
- "fr" French
- "it" Italian
- "es" Spanish
- "sv" Swedish
- "nl" Dutch
- "no" Norwegian
- "cs" Czech
- "sk" Slovak
- "hu" Hungarian
- "da" Danish
- "pl" Polish
- "fi" Finnish
- "pt" Portuguese


In Europe and some other regions also trip info can be retrieved. For a month you can ask the days with trips. And you can ask for a specific day for all the trips of that specific day.::
- First call vm.update_month_trip_info(vehicle.id, yyyymm) before getting vehicle.month_trip_info for that month
- First call vm.update_day_trip_info(vehicle.id, day.yyyymmdd) before getting vehicle.day_trip_info for that day

Example of getting trip info of the current month and day (vm is VehicleManager instance)::

    now = datetime.now()
    yyyymm = now.strftime("%Y%m")
    yyyymmdd = now.strftime("%Y%m%d")
    vm.update_month_trip_info(vehicle.id, yyyymm)
    if vehicle.month_trip_info is not None:
        for day in vehicle.month_trip_info.day_list:  # ordered on day
            if yyyymmdd == day.yyyymmdd:  # in example only interested in current day
                vm.update_day_trip_info(vehicle.id, day.yyyymmdd)
                if vehicle.day_trip_info is not None:
                    for trip in reversed(vehicle.day_trip_info.trip_list):  # show oldest first
                        print(f"{day.yyyymmdd},{trip.hhmmss},{trip.drive_time},{trip.idle_time},{trip.distance},{trip.avg_speed},{trip.max_speed}")

In India the day trip list only carries start/end times, so each trip's figures come from a per-trip detail call (cached per trip). India trips also report ``trip.end_hhmmss`` and the driving-behaviour counters ``trip.hard_braking_count``, ``trip.rapid_acceleration_count`` and ``trip.excessive_speed_count``.

CLI Usage
=========

A tool `bluelink` is provided that enable querying the vehicles and save the
state to a JSON file. Example usage:

::

    bluelink --region Canada --brand Hyundai --username FOO --password BAR --pin 1234 info --json infos.json

Environment variables BLUELINK_XXX can be used to provide a default value for
the corresponding --xxx argument.
