from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('register/', views.register, name='register'),
    # redirect_authenticated_user: kto jest już zalogowany, temu formularz
    # logowania nie ma czego pokazać — idzie prosto na LOGIN_REDIRECT_URL,
    # tak samo jak z gołej domeny.
    path('login/', auth_views.LoginView.as_view(template_name='login.html',
                                                redirect_authenticated_user=True),
         name='login')
]
