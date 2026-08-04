from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm


class RegisterForm(UserCreationForm):
    username = forms.CharField(
        label="Nazwa użytkownika",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Wpisz login"
            }
        )
    )

    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "example@gmail.com"
            }
        )
    )

    password1 = forms.CharField(
        label="Hasło",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Hasło"
            }
        )
    )

    password2 = forms.CharField(
        label="Powtórz hasło",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Powtórz hasło"
            }
        )
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password1",
            "password2"
        ]
