from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup, name='signup'),
    path('manager/home/', views.manager_home, name='manager_home'),
    path('manager/vehicles/', views.vehicles_page, name='vehicles_page'),
    path('manager/drivers/', views.drivers_page, name='drivers_page'),
    path('manager/trips/', views.trips_page, name='trips_page'),
    path('manager/reports/completed-trips/', views.manager_completed_trips, name='manager_completed_trips'),
    path('manager/vehicles/create/', views.vehicle_create, name='vehicle_create'),
    path('manager/vehicles/<int:vehicle_id>/', views.vehicle_detail, name='vehicle_detail'),
    path('manager/vehicles/<int:vehicle_id>/edit/', views.vehicle_edit, name='vehicle_edit'),
    path('manager/vehicles/<int:vehicle_id>/delete/', views.vehicle_delete, name='vehicle_delete'),
    path('manager/drivers/<int:driver_id>/', views.driver_detail, name='driver_detail'),
    path('manager/drivers/<int:driver_id>/assign-vehicle/', views.assign_vehicle, name='assign_vehicle'),
    path('manager/drivers/<int:driver_id>/unassign-vehicle/', views.unassign_vehicle, name='unassign_vehicle'),
    path('manager/trips/create/', views.create_trip, name='create_trip'),
    path('driver/home/', views.driver_home, name='driver_home'),
    path('driver/trips/', views.driver_trips, name='driver_trips'),
    path('driver/trips/<int:trip_id>/', views.trip_detail, name='trip_detail'),
    path('driver/trips/<int:trip_id>/complete/', views.trip_complete_form, name='trip_complete_form'),
    path('driver/reports/completed-trips/', views.driver_completed_trips, name='driver_completed_trips'),
    path('driver/vehicle/', views.driver_vehicle_detail, name='driver_vehicle_detail'),
    path('driver/profile/', views.driver_profile, name='driver_profile'),
    path('logout/', views.logout_view, name='logout'),
]
