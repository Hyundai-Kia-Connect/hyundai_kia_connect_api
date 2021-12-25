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

