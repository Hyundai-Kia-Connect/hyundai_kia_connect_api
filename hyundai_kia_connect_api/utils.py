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

    if value is not None:
        if (value.find('-') <= 0) and value.replace('-', '', 1).isdigit():
            value = int(value)
        elif (value.find('-') <= 0) and (value.count('.') < 2) and (value.replace('-', '', 1).replace('.', '', 1).isdigit()):
            value = float(value)
    return value


def get_hex_temp_into_index(value):
    if value is not None:
        value = value.replace("H", "")
        value = int(value, 16)
        return value
    else:
        return None


def get_index_into_hex_temp(value):
    if value is not None:
        value = hex(value).split("x")
        value = value[1] + "H"
        value = value.zfill(3).upper()
        return value
    else:
        return None
