# -*- coding: utf-8 -*-
import decimal


def frequency(fre):
    try:
        fre = 12 / fre
        # return round_up(fre, 2)
        return to_fixed(fre, num=2)
    except ZeroDivisionError:
        pass


def to_fixed(amount, num=6):

    if amount is None:
        return
    fraction = decimal.Decimal("0.{}".format("0" * num))
    return decimal.Decimal(str(amount)).quantize(fraction, rounding="ROUND_HALF_UP")
