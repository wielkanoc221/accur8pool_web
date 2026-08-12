class Motion3DError(Exception):
    """Dane albo zakres nie pozwalają nic policzyć.

    To jest komunikat DLA UŻYTKOWNIKA — widok zamienia go na 422, nie na
    500. Wszystko, co ląduje w tym wyjątku, ma być zdaniem, po którym
    wiadomo, co zrobić z plikiem albo z zaznaczeniem.
    """
