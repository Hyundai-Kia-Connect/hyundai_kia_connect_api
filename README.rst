
Introduction
============
This is a Kia UVO and Hyundai Bluelink written in python.  It is primary consumed by home assistant.  If you are looking for a home assistant Kia / Hyundai implementation please look here: https://github.com/fuatakgun/kia_uvo.  Much of this base code came from reading bluelinky and contributions to the kia_uvo home assistant project. 


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

    REGIONS = {1: REGION_EUROPE, 2: REGION_CANADA, 3: REGION_USA}
    BRANDS = {1: BRAND_KIA, 2: BRAND_HYUNDAI}
    
Once this is done you can now make the following calls against the vehicle manager::

 #Checks the token is still valid and updates it if not.  Should be called before anything else.
 check_and_refresh_token(self)
 
 #Ideal fresh command. Checks if the car has been updated since the time in seconds provided.  If so does a cached update. If not force calls the car. 
 check_and_force_update_vehicles(self, force_refresh_interval) # Interval in seconds - consider API Rate Limits https://github.com/Hacksore/bluelinky/wiki/API-Rate-Limits

 #Used to return a specific vehicle object:
 get_vehicle(self, vehicle_id)
 
 #Updates all cars with what is cached in the cloud:
 update_all_vehicles_with_cached_state(self)
 
 #Updates a specific car with cached state:
 update_vehicle_with_cached_state(self, vehicle_id)
 
 #Force refreshes all cars:
 force_refresh_all_vehicles_states(self)
 
 #Force refreshes a single car:
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

