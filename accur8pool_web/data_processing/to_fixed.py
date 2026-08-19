# -*- coding: utf-8 -*-

import decimal


def to_fixed(amount, num=6):
    if amount is None:
        return
    fraction = decimal.Decimal("0.{}".format("0" * num))
    return decimal.Decimal(str(amount)).quantize(fraction, rounding="ROUND_HALF_UP")


if __name__ == '__main__':
    print(to_fixed(3.888239, 2))
