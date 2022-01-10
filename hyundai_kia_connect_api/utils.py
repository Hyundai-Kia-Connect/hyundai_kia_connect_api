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

def get_index_into_hex_temp(value):
    value = hex(value).split("x")
    value = value[1] + "H"
    value = value.zfill(3).upper()
    return value