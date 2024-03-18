from hyundai_kia_connect_api import *
import os

login = os.getenv("BL_LOGIN")
pswd = os.getenv("BL_PSWD")
pin = os.getenv("BL_PIN")
i20_id = os.getenv("I20_ID")

vm = VehicleManager(region=6, brand=2, username=login, password=pswd, pin=pin)
vm.check_and_refresh_token()
vm.update_all_vehicles_with_cached_state()
# print(vm.vehicles)
i20 = vm.get_vehicle(i20_id)
# vm.update_day_trip_info(i20_id, '20231101')
print(i20)
# vm.force_refresh_vehicle_state(i20_id)
# i20 = vm.get_vehicle(i20_id)
# print(i20.location)
# vm.update_day_trip_info(i20_id, '20231103')
# print(i20.day_trip_info)
# vm.update_month_trip_info(i20_id, '202310')
