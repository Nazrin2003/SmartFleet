from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup, name='signup'),
    path('manager/home/', views.manager_home, name='manager_home'),
    path('driver/home/', views.driver_home, name='driver_home'),
]
