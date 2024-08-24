Code Maintainers Wanted
=======================

I no longer have a Kia or Hyundai so don't maintain this like I used to.  Others who are interested in jumping in are welcome to join the project!   Even just pull requests are appreciated!

Introduction
============

This is a Kia UVO, Hyundai Bluelink, Genesis Connect(Canada Only) written in python.  It is primary consumed by home assistant.  If you are looking for a home assistant Kia / Hyundai implementation please look here: https://github.com/fuatakgun/kia_uvo.  Much of this base code came from reading bluelinky and contributions to the kia_uvo home assistant project.

Usage
=====

This package is designed to simplify the complexity of using multiple regions.  It attempts to standardize the usage regardless of what brand or region the car is in.  That isn't always possible though, in particular some features differ from one to the next.

Python 3.9 or newer is required to use this package. Vehicle manager is the key class that is called to manage the vehicle lists.  One vehicle manager should be used per login. Key data points required to instantiate vehicle manager are::

    region: int
    brand: int,
    username: str
    password: str
    pin: str (required for CA, and potentially USA, otherwise pass a blank string)

Key values for the int exist in the constant(https://github.com/fuatakgun/hyundai_kia_connect_api/blob/master/hyundai_kia_connect_api/const.py) file as::

    REGIONS = {1: REGION_EUROPE, 2: REGION_CANADA, 3: REGION_USA, 4: REGION_CHINA, 5: REGION_AUSTRALIA}
    BRANDS = {1: BRAND_KIA, 2: BRAND_HYUNDAI, 3: BRAND_GENESIS}

Once this is done you can now make the following calls against the vehicle manager::

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
