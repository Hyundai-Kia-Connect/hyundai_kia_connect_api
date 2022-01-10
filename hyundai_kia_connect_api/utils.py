def get_child_value(data, key):
    value = data
    for x in key.split("."):
        try:
            value = value[x]
        except:
            try:
                value = value[int(x)]
            except:
                value = None
    return value

def get_hex_temp_into_index(value):
    value = value.replace("H", "")
    value = int(value, 16)
    return value

def convert_int_to_distance_unit(value: int):
    if value == 1:
        return "km"
    #Unknown if 2 is miles at this point.  I am assuming. 
    elif value == 2:
        return "mi"


