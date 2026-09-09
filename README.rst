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

Europe Hyundai and Kia login logic is based on the `bluelink-refresh-token <https://github.com/TMA84/bluelink-refresh-token>`_ project.  Username/password login is supported directly for Kia, Hyundai, and Genesis (EU) — no browser or manual token extraction needed. The ``pycryptodome`` package (included as a dependency) is used for RSA password encryption during the EU login flow.

China (Hyundai Bluelink / Kia UVO CN)
-------------------------------------

China support was rewritten in 2026 against the current China Bluelink iOS app (com.hyundai-motor.cn.bluelink).  Highlights and differences vs other regions:

- Login uses a five-step flow through the UARS service (uars-{k|h}.hmgmobility.com.cn); the OAuth authorization code is exchanged server-side by UARS and the tokens are returned inside the callback page. Only the Hyundai brand has been live-verified; Kia constants are included from the app binary but are untested.
- Access tokens live 6 hours.  There is no working refresh_token grant on the China servers, so ``refresh_access_token`` re-authenticates silently when possible and otherwise performs a full password login (same pattern as Canada).
- Remote control on non-CCS2 vehicles uses the plain access token; a PIN is only required for CCS2-style control commands.  ``pin`` should be passed to ``VehicleManager`` as usual.
- Push device registration uses ``pushType: APNS`` (not GCM) and requires a ``providerDeviceId`` field.
- Control-command latency is high (1-2 minutes to reach SUCCESS); consumers should use generous polling timeouts.

Korea (Hyundai MyHyundai)
-------------------------

Hyundai Korea is available as region ``10`` (``REGION_KOREA``). It uses the
current unified MyHyundai account service and the Korean domestic connected-car
API. Pleos accounts may require interactive browser authentication because its
sign-in service rejects scripted password submissions. The browser exchange,
vehicle discovery, cached and forced CCS2 status, door lock/unlock, GEN2 remote
engine/climate start and stop, front-seat heat and ventilation, and heated
steering-wheel control have been live-tested on a Korean ICE vehicle. Rear-seat
climate settings are implemented from the same current MyHyundai request model,
but the live-test vehicle did not advertise rear-seat support.
Korea support should still be considered experimental until it receives broader
live-account testing. EV charge controls, Kia Korea, and Genesis Korea are not
yet supported.

Pleos browser login
~~~~~~~~~~~~~~~~~~~

Pleos login is interactive the first time. This flow does not store the account
password. The callback page immediately forwards desktop browsers to the
Hyundai website, so capture its URL from the browser network log:

1. Open browser developer tools, select **Network**, and enable **Preserve log**.
2. Run the wizard and sign in normally in the browser it opens.
3. After the browser reaches the Hyundai website, filter the preserved requests
   for ``oneapp.hyundai.com/redirect``.
4. Select that document request, copy its complete **Request URL**, and paste it
   into the wizard.

::

    ./scripts/myhyundai_kr_login.sh

The wizard is the simplest setup path. It saves renewable credentials under
``~/.local/state/hyundai-kia-connect-api/`` and verifies them with a read-only
status query. The equivalent Python flow is shown below::

    import getpass
    import webbrowser
    from pathlib import Path

    from hyundai_kia_connect_api import VehicleManager

    pin = getpass.getpass("MyHyundai PIN: ")
    manager = VehicleManager(
        region=10,
        brand=2,
        username="",
        password="",
        pin=pin,
        language="ko",
    )

    login_url = manager.get_authorization_url()
    print("If the browser does not open, visit:", login_url)
    webbrowser.open(login_url)
    redirect_url = input("Paste the final redirect URL: ").strip()
    manager.login_with_redirect_url(redirect_url)

    # Save all refresh credentials atomically. Password, PIN, and the temporary
    # vehicle-control token are omitted. The file is created with mode 0600.
    token_file = (
        Path.home()
        / ".local/state/hyundai-kia-connect-api/myhyundai-kr.json"
    )
    manager.token.save(token_file)

For later API sessions, load the saved refresh credentials and call
``check_and_refresh_token()``. A browser is only needed again if Hyundai expires
or revokes the refresh credentials::

    import getpass
    from pathlib import Path

    from hyundai_kia_connect_api import Token, VehicleManager

    token_file = (
        Path.home()
        / ".local/state/hyundai-kia-connect-api/myhyundai-kr.json"
    )
    token = Token.load(token_file)
    pin = getpass.getpass("MyHyundai PIN: ")
    token.pin = pin
    manager = VehicleManager(
        region=10,
        brand=2,
        username="",
        password="",
        pin=pin,
        token=token,
        language="ko",
    )
    manager.check_and_refresh_token()
    # Hyundai may rotate refresh credentials, so persist the current set after
    # every successful refresh or API session.
    manager.token.save(token_file)

The saved refresh credentials are bearer secrets. Keep the file private and do
not commit, copy, or log it. This avoids repeated Pleos browser login while the
refresh credentials remain valid. If Hyundai revokes or expires them, repeat
the interactive login once and overwrite the saved session.

GEN2 climate options
~~~~~~~~~~~~~~~~~~~~

``get_vehicle_capabilities(vehicle_id)`` populates normalized fields such as
``supports_remote_start``, ``remote_control_generation``, the four
``*_seat_climate_capability`` values, and
``supports_steering_wheel_heater``. ``start_climate`` checks those capabilities
before sending a command. For seat requests, use ``2`` for off, ``3``/``4``/``5``
for low/medium/high ventilation, and ``6``/``7``/``8`` for low/medium/high heat.
Not every seat supports every state. Heated steering-wheel values are ``0``
(off), ``1`` (on/low), and ``2`` (high when
``steering_wheel_heater_option == 2``)::

    from hyundai_kia_connect_api import ClimateRequestOptions

    vehicle_id = next(iter(manager.vehicles))
    capabilities = manager.get_vehicle_capabilities(vehicle_id)
    print(capabilities["appMode"], manager.get_vehicle(vehicle_id).supports_remote_start)

    # This call sends the command immediately.
    request_id = manager.start_climate(
        vehicle_id,
        ClimateRequestOptions(
            set_temp=21.5,
            duration=10,
            defrost=False,
            climate=True,
            front_left_seat=6,
            front_right_seat=6,
            steering_wheel=1,
        ),
    )

Python 3.12 or newer is required to use this package. Vehicle manager is the key class that is called to manage the vehicle lists.  One vehicle manager should be used per login. Key data points required to instantiate vehicle manager are::

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

    REGIONS = {1: REGION_EUROPE, 2: REGION_CANADA, 3: REGION_USA, 4: REGION_CHINA, 5: REGION_AUSTRALIA, 6: REGION_INDIA, 7: REGION_NZ, 8: REGION_BRAZIL, 9: REGION_EUROPE_CCI, 10: REGION_KOREA}
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

CLI Usage
=========

A tool `bluelink` is provided that enable querying the vehicles and save the
state to a JSON file. Example usage:

::

    bluelink --region Canada --brand Hyundai --username FOO --password BAR --pin 1234 info --json infos.json

Environment variables BLUELINK_XXX can be used to provide a default value for
the corresponding --xxx argument.
