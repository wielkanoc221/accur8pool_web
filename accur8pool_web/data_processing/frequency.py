# -*- coding: utf-8 -*-

from .to_fixed import to_fixed


def frequency(fre):

    try:
        fre = 12 / fre
        # return round_up(fre, 2)
        return to_fixed(fre, num=2)
    except ZeroDivisionError:
        pass


if __name__ == '__main__':
    print(frequency(3))
