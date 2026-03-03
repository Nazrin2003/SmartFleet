from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
import csv
from .models import Alert, Driver, FuelLog, Registration, Trip, TripCompletion, Vehicle
from .decorators import role_required 


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def home(request):
    return render(request, 'home.html')


def signup(request):
    messages.info(request, 'Signup is disabled. Contact admin for account creation.')
    return redirect('login')

def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, 'Invalid username or password')
            return redirect('login')

        if not user.is_active:
            messages.error(request, 'Your account is blocked. Contact admin.')
            return redirect('login')

        login(request, user)

        reg = Registration.objects.filter(user=user).first()
        if not reg:
            messages.error(request, 'User role not assigned. Contact admin.')
            logout(request)
            return redirect('login')

        request.session['reg_id'] = reg.id

        if reg.user_role == 'admin':
            return redirect('admin_home')
        if reg.user_role == 'manager':
            return redirect('manager_home')
        if reg.user_role == 'driver':
            return redirect('driver_home')

        messages.error(request, 'Invalid role. Contact admin.')
        logout(request)
        return redirect('login')

    return render(request, 'login.html')



@login_required(login_url='login')
@role_required('admin')
def admin_home(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    context = {
        'reg': reg,
        'total_managers': Registration.objects.filter(user_role='manager').count(),
        'total_drivers': Registration.objects.filter(user_role='driver').count(),
        'total_vehicles': Vehicle.objects.count(),
        'total_trips': Trip.objects.count(),
    }
    return render(request, 'admin_home.html', context)


@login_required(login_url='login')
@role_required('admin')
def manager_list_adm(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    managers = Registration.objects.filter(user_role='manager').select_related('user').order_by('user__username')
    return render(request, 'manager_list_adm.html', {'reg': reg, 'managers': managers})


@login_required(login_url='login')
@role_required('admin')
def add_manager_adm(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')
        if not username or not password:
            messages.error(request, 'Username and password are required.')
            return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'add'})
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'add'})

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=(request.POST.get('first_name') or '').strip(),
            last_name=(request.POST.get('last_name') or '').strip(),
            is_active=True,
        )
        Registration.objects.create(user=user, user_role='manager')
        messages.success(request, 'Manager created successfully.')
        return redirect('manager_list_adm')

    return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'add'})


@login_required(login_url='login')
@role_required('admin')
def edit_manager_adm(request, reg_id):
    reg_session_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_session_id).first()
    manager_reg = get_object_or_404(Registration.objects.select_related('user'), id=reg_id, user_role='manager')
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        if not username:
            messages.error(request, 'Username is required.')
            return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'edit', 'manager': manager_reg})
        if User.objects.filter(username=username).exclude(id=manager_reg.user_id).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'edit', 'manager': manager_reg})

        user = manager_reg.user
        user.username = username
        user.first_name = (request.POST.get('first_name') or '').strip()
        user.last_name = (request.POST.get('last_name') or '').strip()
        new_password = request.POST.get('password')
        if new_password:
            user.set_password(new_password)
        user.save()
        messages.success(request, 'Manager updated.')
        return redirect('manager_list_adm')
    return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'edit', 'manager': manager_reg})


@login_required(login_url='login')
@role_required('admin')
def toggle_manager_adm(request, reg_id):
    manager_reg = get_object_or_404(Registration.objects.select_related('user'), id=reg_id, user_role='manager')
    manager_reg.user.is_active = not manager_reg.user.is_active
    manager_reg.user.save(update_fields=['is_active'])
    messages.success(request, 'Manager status updated.')
    return redirect('manager_list_adm')


@login_required(login_url='login')
@role_required('admin')
def delete_manager_adm(request, reg_id):
    manager_reg = get_object_or_404(Registration.objects.select_related('user'), id=reg_id, user_role='manager')
    manager_reg.user.delete()
    messages.success(request, 'Manager deleted.')
    return redirect('manager_list_adm')


@login_required(login_url='login')
@role_required('admin')
def driver_list_adm(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    drivers = Driver.objects.select_related('user', 'assigned_vehicle').order_by('user__username')
    return render(request, 'driver_list_adm.html', {'reg': reg, 'drivers': drivers})


@login_required(login_url='login')
@role_required('admin')
def add_driver_adm(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')
        license_number = (request.POST.get('license_number') or '').strip() or None
        license_expiry_date = (request.POST.get('license_expiry_date') or '').strip() or None
        phone_number = (request.POST.get('phone_number') or '').strip() or None
        address = (request.POST.get('address') or '').strip() or None
        years_of_experience = _to_int(request.POST.get('years_of_experience'))
        emergency_contact_name = (request.POST.get('emergency_contact_name') or '').strip() or None
        emergency_contact_phone = (request.POST.get('emergency_contact_phone') or '').strip() or None
        if not username or not password:
            messages.error(request, 'Username and password are required.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})
        if license_number and Driver.objects.filter(license_number=license_number).exists():
            messages.error(request, 'License number already exists.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=(request.POST.get('first_name') or '').strip(),
            last_name=(request.POST.get('last_name') or '').strip(),
            is_active=True,
        )
        Registration.objects.create(user=user, user_role='driver')
        Driver.objects.create(
            user=user,
            license_number=license_number,
            license_expiry_date=license_expiry_date,
            phone_number=phone_number,
            address=address,
            years_of_experience=years_of_experience,
            emergency_contact_name=emergency_contact_name,
            emergency_contact_phone=emergency_contact_phone,
        )
        messages.success(request, 'Driver created successfully.')
        return redirect('driver_list_adm')
    return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})


@login_required(login_url='login')
@role_required('admin')
def edit_driver_adm(request, driver_id):
    reg_session_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_session_id).first()
    driver = get_object_or_404(Driver.objects.select_related('user'), id=driver_id)
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        license_number = (request.POST.get('license_number') or '').strip() or None
        license_expiry_date = (request.POST.get('license_expiry_date') or '').strip() or None
        phone_number = (request.POST.get('phone_number') or '').strip() or None
        address = (request.POST.get('address') or '').strip() or None
        years_of_experience = _to_int(request.POST.get('years_of_experience'))
        emergency_contact_name = (request.POST.get('emergency_contact_name') or '').strip() or None
        emergency_contact_phone = (request.POST.get('emergency_contact_phone') or '').strip() or None
        if not username:
            messages.error(request, 'Username is required.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})
        if User.objects.filter(username=username).exclude(id=driver.user_id).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})
        if license_number and Driver.objects.filter(license_number=license_number).exclude(id=driver.id).exists():
            messages.error(request, 'License number already exists.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})

        user = driver.user
        user.username = username
        user.first_name = (request.POST.get('first_name') or '').strip()
        user.last_name = (request.POST.get('last_name') or '').strip()
        new_password = request.POST.get('password')
        if new_password:
            user.set_password(new_password)
        user.save()

        driver.license_number = license_number
        driver.license_expiry_date = license_expiry_date
        driver.phone_number = phone_number
        driver.address = address
        driver.years_of_experience = years_of_experience
        driver.emergency_contact_name = emergency_contact_name
        driver.emergency_contact_phone = emergency_contact_phone
        driver.save()
        messages.success(request, 'Driver updated.')
        return redirect('driver_list_adm')
    return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})


@login_required(login_url='login')
@role_required('admin')
def toggle_driver_adm(request, driver_id):
    driver = get_object_or_404(Driver.objects.select_related('user'), id=driver_id)
    driver.user.is_active = not driver.user.is_active
    driver.user.save(update_fields=['is_active'])
    messages.success(request, 'Driver status updated.')
    return redirect('driver_list_adm')


@login_required(login_url='login')
@role_required('admin')
def delete_driver_adm(request, driver_id):
    driver = get_object_or_404(Driver.objects.select_related('user'), id=driver_id)
    driver.user.delete()
    messages.success(request, 'Driver deleted.')
    return redirect('driver_list_adm')


@login_required(login_url='login')
@role_required('admin')
def trips_admin(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    trips = Trip.objects.select_related('driver', 'driver__user', 'vehicle')

    trip_id = (request.GET.get('trip_id') or '').strip()
    driver_id = (request.GET.get('driver') or '').strip()
    vehicle_id = (request.GET.get('vehicle') or '').strip()
    status = (request.GET.get('status') or '').strip()
    date_from = (request.GET.get('date_from') or '').strip()
    date_to = (request.GET.get('date_to') or '').strip()
    sort = (request.GET.get('sort') or 'date_desc').strip()

    if trip_id.isdigit():
        trips = trips.filter(id=int(trip_id))
    if driver_id.isdigit():
        trips = trips.filter(driver_id=int(driver_id))
    if vehicle_id.isdigit():
        trips = trips.filter(vehicle_id=int(vehicle_id))
    if status in {Trip.STATUS_PENDING, Trip.STATUS_IN_PROGRESS, Trip.STATUS_COMPLETED, Trip.STATUS_REJECTED}:
        trips = trips.filter(status=status)
    if date_from:
        trips = trips.filter(created_at__date__gte=date_from)
    if date_to:
        trips = trips.filter(created_at__date__lte=date_to)
    if sort == 'date_asc':
        trips = trips.order_by('created_at')
    else:
        trips = trips.order_by('-created_at')

    drivers = Driver.objects.select_related('user').order_by('user__username')
    vehicles = Vehicle.objects.order_by('plate_number')
    return render(
        request,
        'trips_admin.html',
        {'reg': reg, 'trips': trips, 'drivers': drivers, 'vehicles': vehicles},
    )


@login_required(login_url='login')
@role_required('admin')
def trips_report_admin(request):
    trips = Trip.objects.select_related('driver', 'driver__user', 'vehicle')
    trip_id = (request.GET.get('trip_id') or '').strip()
    driver_id = (request.GET.get('driver') or '').strip()
    vehicle_id = (request.GET.get('vehicle') or '').strip()
    status = (request.GET.get('status') or '').strip()
    date_from = (request.GET.get('date_from') or '').strip()
    date_to = (request.GET.get('date_to') or '').strip()
    sort = (request.GET.get('sort') or 'date_desc').strip()

    if trip_id.isdigit():
        trips = trips.filter(id=int(trip_id))
    if driver_id.isdigit():
        trips = trips.filter(driver_id=int(driver_id))
    if vehicle_id.isdigit():
        trips = trips.filter(vehicle_id=int(vehicle_id))
    if status in {Trip.STATUS_PENDING, Trip.STATUS_IN_PROGRESS, Trip.STATUS_COMPLETED, Trip.STATUS_REJECTED}:
        trips = trips.filter(status=status)
    if date_from:
        trips = trips.filter(created_at__date__gte=date_from)
    if date_to:
        trips = trips.filter(created_at__date__lte=date_to)
    if sort == 'date_asc':
        trips = trips.order_by('created_at')
    else:
        trips = trips.order_by('-created_at')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="admin_trip_report.csv"'
    writer = csv.writer(response)
    writer.writerow(['Trip ID', 'Driver', 'Vehicle', 'Origin', 'Destination', 'Status', 'Created At'])
    for trip in trips:
        writer.writerow([
            trip.id,
            trip.driver.user.username,
            trip.vehicle.plate_number,
            trip.origin,
            trip.destination,
            trip.get_status_display(),
            trip.created_at,
        ])
    return response
@login_required(login_url='login')
@role_required('manager')
def manager_home(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    total_drivers = Registration.objects.filter(user_role='driver').count()
    total_vehicles = Vehicle.objects.count()
    active_trips = Trip.objects.filter(status=Trip.STATUS_IN_PROGRESS).count()
    pending_trips = Trip.objects.filter(status=Trip.STATUS_PENDING).count()
    vehicles_in_maintenance = Vehicle.objects.filter(
        status__in=[Vehicle.STATUS_MAINTENANCE, Vehicle.STATUS_MAINT_REQUIRED]
    ).count()
    fuel_alerts = FuelLog.objects.filter(is_anomaly=True).count()

    dashboard_data = {
        'total_vehicles': total_vehicles,
        'total_drivers': total_drivers,
        'active_trips': active_trips,
        'pending_trips': pending_trips,
        'vehicles_in_maintenance': vehicles_in_maintenance,
        'fuel_alerts': fuel_alerts,
    }
    return render(request, 'manager_home.html', {'reg': reg, **dashboard_data})


@login_required(login_url='login')
@role_required('manager')
def vehicles_page(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    vehicles = Vehicle.objects.select_related("assigned_driver", "assigned_driver__user").order_by("plate_number")
    return render(request, 'vehicles_list.html', {'reg': reg, 'vehicles': vehicles})


@login_required(login_url='login')
@role_required('manager')
def drivers_page(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver_users = User.objects.filter(registration__user_role='driver').order_by("username")
    for user in driver_users:
        Driver.objects.get_or_create(user=user)
    drivers = Driver.objects.select_related("user", "assigned_vehicle").order_by("user__username")
    return render(request, 'drivers_list.html', {'reg': reg, 'drivers': drivers})


@login_required(login_url='login')
@role_required('manager')
def trips_page(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    trips = Trip.objects.select_related("driver", "driver__user", "vehicle")

    trip_id = (request.GET.get("trip_id") or "").strip()
    driver_id = (request.GET.get("driver") or "").strip()
    vehicle_id = (request.GET.get("vehicle") or "").strip()
    status = (request.GET.get("status") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    sort = (request.GET.get("sort") or "date_desc").strip()

    if trip_id.isdigit():
        trips = trips.filter(id=int(trip_id))
    if driver_id.isdigit():
        trips = trips.filter(driver_id=int(driver_id))
    if vehicle_id.isdigit():
        trips = trips.filter(vehicle_id=int(vehicle_id))
    if status in {
        Trip.STATUS_PENDING,
        Trip.STATUS_IN_PROGRESS,
        Trip.STATUS_COMPLETED,
        Trip.STATUS_REJECTED,
    }:
        trips = trips.filter(status=status)
    if date_from:
        trips = trips.filter(created_at__date__gte=date_from)
    if date_to:
        trips = trips.filter(created_at__date__lte=date_to)

    if sort == "date_asc":
        trips = trips.order_by("created_at")
    else:
        trips = trips.order_by("-created_at")

    drivers = Driver.objects.select_related("user").order_by("user__username")
    vehicles = Vehicle.objects.order_by("plate_number")
    return render(
        request,
        'trips_list.html',
        {'reg': reg, 'trips': trips, 'drivers': drivers, 'vehicles': vehicles},
    )


@login_required(login_url='login')
@role_required('manager')
def manager_completed_trips(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    trips = Trip.objects.filter(status=Trip.STATUS_COMPLETED).select_related(
        "driver", "driver__user", "vehicle"
    )

    trip_id = (request.GET.get("trip_id") or "").strip()
    vehicle_id = (request.GET.get("vehicle") or "").strip()
    driver_id = (request.GET.get("driver") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    sort = (request.GET.get("sort") or "date_desc").strip()

    if trip_id.isdigit():
        trips = trips.filter(id=int(trip_id))
    if vehicle_id.isdigit():
        trips = trips.filter(vehicle_id=int(vehicle_id))
    if driver_id.isdigit():
        trips = trips.filter(driver_id=int(driver_id))
    if date_from:
        trips = trips.filter(completed_at__date__gte=date_from)
    if date_to:
        trips = trips.filter(completed_at__date__lte=date_to)

    if sort == "date_asc":
        trips = trips.order_by("completed_at")
    else:
        trips = trips.order_by("-completed_at")

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="manager_completed_trips.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Trip ID", "Driver", "Vehicle", "Origin", "Destination", "Date", "Fuel Used", "Status"]
        )
        for trip in trips:
            writer.writerow(
                [
                    trip.id,
                    trip.driver.user.username,
                    trip.vehicle.plate_number,
                    trip.origin,
                    trip.destination,
                    trip.completed_at,
                    trip.fuel_used_liters,
                    trip.get_status_display(),
                ]
            )
        return response

    vehicles = Vehicle.objects.order_by("plate_number")
    drivers = Driver.objects.select_related("user").order_by("user__username")
    return render(
        request,
        'manager_completed_trips.html',
        {'reg': reg, 'trips': trips, 'vehicles': vehicles, 'drivers': drivers},
    )


@login_required(login_url='login')
@role_required('manager')
def vehicle_create(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    if request.method == "POST":
        plate_number = (request.POST.get("plate_number") or "").strip()
        model_name = (request.POST.get("model_name") or "").strip()
        year_of_manufacture = _to_int(request.POST.get("year_of_manufacture"))
        vehicle_type = (request.POST.get("vehicle_type") or "").strip()
        load_capacity = _to_float(request.POST.get("load_capacity"))
        usage_hours = _to_float(request.POST.get("usage_hours"))
        battery_status = _to_float(request.POST.get("battery_status"))
        status = (request.POST.get("status") or Vehicle.STATUS_AVAILABLE).strip()

        if not plate_number or not model_name:
            messages.error(request, "Plate number and model are required.")
            return render(request, 'vehicle_form.html', {'reg': reg, 'mode': 'create'})

        if Vehicle.objects.filter(plate_number=plate_number).exists():
            messages.error(request, "Vehicle with this plate number already exists.")
            return render(request, 'vehicle_form.html', {'reg': reg, 'mode': 'create'})

        Vehicle.objects.create(
            plate_number=plate_number,
            model_name=model_name,
            year_of_manufacture=year_of_manufacture,
            vehicle_type=vehicle_type or None,
            load_capacity=load_capacity,
            usage_hours=usage_hours if usage_hours is not None else 0,
            battery_status=battery_status,
            status=status if status in dict(Vehicle.STATUS_CHOICES) else Vehicle.STATUS_AVAILABLE,
        )
        messages.success(request, "Vehicle created successfully.")
        return redirect("vehicles_page")

    return render(request, 'vehicle_form.html', {'reg': reg, 'mode': 'create'})


@login_required(login_url='login')
@role_required('manager')
def create_trip(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    eligible_drivers = Driver.objects.select_related("user", "assigned_vehicle").filter(
        assigned_vehicle__isnull=False
    ).exclude(
        status=Driver.STATUS_IN_TRIP
    ).exclude(
        assigned_vehicle__status__in=[Vehicle.STATUS_MAINTENANCE, Vehicle.STATUS_MAINT_REQUIRED]
    ).order_by("user__username")

    if request.method == "POST":
        driver_id = (request.POST.get("driver_id") or "").strip()
        origin = (request.POST.get("origin") or "").strip()
        destination = (request.POST.get("destination") or "").strip()
        load_details = (request.POST.get("load_details") or "").strip()
        estimated_distance_km = _to_float(request.POST.get("estimated_distance_km"))
        expected_fuel_liters = _to_float(request.POST.get("expected_fuel_liters"))
        estimated_time_hours = _to_float(request.POST.get("estimated_time_hours"))
        scheduled_date = (request.POST.get("scheduled_date") or "").strip() or None

        if not driver_id.isdigit():
            messages.error(request, "Please select a valid driver.")
            return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})
        if not origin or not destination:
            messages.error(request, "Origin and destination are required.")
            return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})

        driver = eligible_drivers.filter(id=int(driver_id)).first()
        if not driver:
            messages.error(
                request,
                "Selected driver is not eligible. Driver must have an assigned vehicle and no active trip.",
            )
            return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})

        Trip.objects.create(
            driver=driver,
            vehicle=driver.assigned_vehicle,
            origin=origin,
            destination=destination,
            load_details=load_details or None,
            estimated_distance_km=estimated_distance_km,
            expected_fuel_liters=expected_fuel_liters,
            estimated_time_hours=estimated_time_hours,
            scheduled_date=scheduled_date,
            status=Trip.STATUS_PENDING,
        )
        messages.success(request, "Trip created successfully and marked as Pending.")
        return redirect("trips_page")

    return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})


@login_required(login_url='login')
@role_required('manager')
def vehicle_detail(request, vehicle_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    vehicle = Vehicle.objects.select_related("assigned_driver", "assigned_driver__user").filter(id=vehicle_id).first()
    if not vehicle:
        messages.error(request, "Vehicle not found.")
        return redirect("vehicles_page")

    maintenance_history = vehicle.maintenance_history.order_by("-created_at")
    trip_history = vehicle.trips.select_related("driver", "driver__user").order_by("-created_at")
    fuel_logs = vehicle.fuel_logs.select_related("driver", "driver__user").order_by("-logged_at")
    return render(
        request,
        "vehicle_detail.html",
        {
            "reg": reg,
            "vehicle": vehicle,
            "maintenance_history": maintenance_history,
            "trip_history": trip_history,
            "fuel_logs": fuel_logs,
        },
    )


@login_required(login_url='login')
@role_required('manager')
def vehicle_edit(request, vehicle_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    vehicle = Vehicle.objects.filter(id=vehicle_id).first()
    if not vehicle:
        messages.error(request, "Vehicle not found.")
        return redirect("vehicles_page")

    if request.method == "POST":
        plate_number = (request.POST.get("plate_number") or "").strip()
        model_name = (request.POST.get("model_name") or "").strip()
        year_of_manufacture = _to_int(request.POST.get("year_of_manufacture"))
        vehicle_type = (request.POST.get("vehicle_type") or "").strip()
        load_capacity = _to_float(request.POST.get("load_capacity"))
        usage_hours = _to_float(request.POST.get("usage_hours"))
        battery_status = _to_float(request.POST.get("battery_status"))
        status = (request.POST.get("status") or Vehicle.STATUS_AVAILABLE).strip()

        if not plate_number or not model_name:
            messages.error(request, "Plate number and model are required.")
            return render(
                request, "vehicle_form.html", {"reg": reg, "mode": "edit", "vehicle": vehicle}
            )

        duplicate = Vehicle.objects.filter(plate_number=plate_number).exclude(id=vehicle.id).exists()
        if duplicate:
            messages.error(request, "Another vehicle already uses this plate number.")
            return render(
                request, "vehicle_form.html", {"reg": reg, "mode": "edit", "vehicle": vehicle}
            )

        vehicle.plate_number = plate_number
        vehicle.model_name = model_name
        vehicle.year_of_manufacture = year_of_manufacture
        vehicle.vehicle_type = vehicle_type or None
        vehicle.load_capacity = load_capacity
        vehicle.usage_hours = usage_hours if usage_hours is not None else 0
        vehicle.battery_status = battery_status
        vehicle.status = status if status in dict(Vehicle.STATUS_CHOICES) else Vehicle.STATUS_AVAILABLE
        vehicle.save()
        messages.success(request, "Vehicle updated successfully.")
        return redirect("vehicles_page")

    return render(request, "vehicle_form.html", {"reg": reg, "mode": "edit", "vehicle": vehicle})


@login_required(login_url='login')
@role_required('manager')
def vehicle_delete(request, vehicle_id):
    vehicle = Vehicle.objects.filter(id=vehicle_id).first()
    if not vehicle:
        messages.error(request, "Vehicle not found.")
        return redirect("vehicles_page")

    vehicle_label = vehicle.plate_number
    vehicle.delete()
    messages.success(request, f"Vehicle {vehicle_label} deleted.")
    return redirect("vehicles_page")


@login_required(login_url='login')
@role_required('manager')
def driver_detail(request, driver_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver = Driver.objects.select_related("user", "assigned_vehicle").filter(id=driver_id).first()
    if not driver:
        messages.error(request, "Driver not found.")
        return redirect("drivers_page")

    trips = Trip.objects.filter(driver=driver).select_related("vehicle").order_by("-created_at")
    fuel_logs = FuelLog.objects.filter(driver=driver).select_related("vehicle").order_by("-logged_at")
    maintenance_alerts = driver.alerts.filter(
        alert_type="maintenance"
    ).select_related("vehicle").order_by("-created_at")
    return render(
        request,
        "driver_detail.html",
        {
            "reg": reg,
            "driver": driver,
            "trips": trips,
            "fuel_logs": fuel_logs,
            "maintenance_alerts": maintenance_alerts,
        },
    )


@login_required(login_url='login')
@role_required('manager')
def assign_vehicle(request, driver_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver = Driver.objects.select_related("user", "assigned_vehicle").filter(id=driver_id).first()
    if not driver:
        messages.error(request, "Driver not found.")
        return redirect("drivers_page")

    if request.method == "POST":
        vehicle_id = request.POST.get("vehicle_id")
        vehicle = Vehicle.objects.filter(id=vehicle_id, status=Vehicle.STATUS_AVAILABLE).first()
        if not vehicle:
            messages.error(request, "Selected vehicle is no longer available.")
            return redirect("assign_vehicle", driver_id=driver.id)

        with transaction.atomic():
            old_vehicle = driver.assigned_vehicle
            if old_vehicle and old_vehicle.status == Vehicle.STATUS_ASSIGNED:
                old_vehicle.status = Vehicle.STATUS_AVAILABLE
                old_vehicle.save(update_fields=["status", "updated_at"])

            driver.assigned_vehicle = vehicle
            driver.status = Driver.STATUS_ASSIGNED
            driver.save(update_fields=["assigned_vehicle", "status"])

            vehicle.status = Vehicle.STATUS_ASSIGNED
            vehicle.save(update_fields=["status", "updated_at"])

        messages.success(request, f"Vehicle {vehicle.plate_number} assigned to {driver.user.username}.")
        return redirect("driver_detail", driver_id=driver.id)

    vehicles = Vehicle.objects.filter(status=Vehicle.STATUS_AVAILABLE).order_by("plate_number")
    return render(
        request,
        "assign_vehicle.html",
        {"reg": reg, "driver": driver, "vehicles": vehicles},
    )


@login_required(login_url='login')
@role_required('manager')
def unassign_vehicle(request, driver_id):
    driver = Driver.objects.select_related("user", "assigned_vehicle").filter(id=driver_id).first()
    if not driver:
        messages.error(request, "Driver not found.")
        return redirect("drivers_page")
    if not driver.assigned_vehicle:
        messages.info(request, "Driver has no assigned vehicle.")
        return redirect("driver_detail", driver_id=driver.id)

    active_trip_exists = Trip.objects.filter(driver=driver, status=Trip.STATUS_IN_PROGRESS).exists()
    if active_trip_exists:
        messages.error(request, "Cannot unassign while driver has an in-progress trip.")
        return redirect("driver_detail", driver_id=driver.id)

    with transaction.atomic():
        vehicle = driver.assigned_vehicle
        driver.assigned_vehicle = None
        driver.status = Driver.STATUS_AVAILABLE
        driver.save(update_fields=["assigned_vehicle", "status"])

        if vehicle.status == Vehicle.STATUS_ASSIGNED:
            vehicle.status = Vehicle.STATUS_AVAILABLE
            vehicle.save(update_fields=["status", "updated_at"])

    messages.success(request, f"Vehicle unassigned from {driver.user.username}.")
    return redirect("driver_detail", driver_id=driver.id)


@login_required(login_url='login')
@role_required('driver')
def driver_home(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)
    active_trips = Trip.objects.filter(driver=driver, status=Trip.STATUS_IN_PROGRESS).count()
    completed_trips = Trip.objects.filter(driver=driver, status=Trip.STATUS_COMPLETED).count()
    notifications = driver.alerts.filter(is_resolved=False).order_by("-created_at")
    return render(
        request,
        'driver_home.html',
        {
            'reg': reg,
            'driver': driver,
            'assigned_vehicle': driver.assigned_vehicle,
            'active_trips': active_trips,
            'completed_trips': completed_trips,
            'notifications': notifications[:5],
            'notifications_count': notifications.count(),
        },
    )


@login_required(login_url='login')
@role_required('driver')
def driver_trips(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)
    trips = Trip.objects.filter(driver=driver).select_related("vehicle").order_by("-created_at")
    return render(request, "driver_trips.html", {"reg": reg, "trips": trips})


@login_required(login_url='login')
@role_required('driver')
def trip_detail(request, trip_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)
    trip = Trip.objects.select_related("vehicle", "driver").filter(id=trip_id, driver=driver).first()
    if not trip:
        messages.error(request, "Trip not found.")
        return redirect("driver_trips")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "accept" and trip.status == Trip.STATUS_PENDING:
            if trip.vehicle.status in [Vehicle.STATUS_MAINTENANCE, Vehicle.STATUS_MAINT_REQUIRED]:
                messages.error(request, "Vehicle is under maintenance and cannot start trip.")
            else:
                trip.status = Trip.STATUS_IN_PROGRESS
                trip.started_at = timezone.now()
                trip.save(update_fields=["status", "started_at"])
                driver.status = Driver.STATUS_IN_TRIP
                driver.save(update_fields=["status"])
                trip.vehicle.status = Vehicle.STATUS_IN_TRIP
                trip.vehicle.save(update_fields=["status", "updated_at"])
                messages.success(request, "Trip accepted. Status changed to In Progress.")
            return redirect("trip_detail", trip_id=trip.id)

        if action == "reject" and trip.status == Trip.STATUS_PENDING:
            trip.status = Trip.STATUS_REJECTED
            trip.save(update_fields=["status"])
            messages.info(request, "Trip rejected.")
            return redirect("driver_trips")

        if action == "complete" and trip.status == Trip.STATUS_IN_PROGRESS:
            return redirect("trip_complete_form", trip_id=trip.id)

    return render(request, "trip_detail.html", {"reg": reg, "trip": trip})


@login_required(login_url='login')
@role_required('driver')
def trip_complete_form(request, trip_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)
    trip = Trip.objects.select_related("vehicle", "driver").filter(
        id=trip_id, driver=driver, status=Trip.STATUS_IN_PROGRESS
    ).first()
    if not trip:
        messages.error(request, "Only in-progress trips can be completed.")
        return redirect("driver_trips")

    if request.method == "POST":
        actual_delivery_time = _to_float(request.POST.get("actual_delivery_time"))
        fuel_filled = _to_float(request.POST.get("fuel_filled_liters"))
        fuel_cost = _to_float(request.POST.get("fuel_cost"))
        odometer_reading = _to_float(request.POST.get("odometer_reading"))
        engine_temp = _to_float(request.POST.get("engine_temp"))
        oil_quality = _to_float(request.POST.get("oil_quality"))
        brake_condition = _to_float(request.POST.get("brake_condition"))
        weather = (request.POST.get("weather") or "").strip()
        road_condition = (request.POST.get("road_condition") or "").strip()

        if actual_delivery_time is None or fuel_filled is None:
            messages.error(request, "Actual delivery time and fuel filled are required.")
            return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})

        completion, _ = TripCompletion.objects.get_or_create(trip=trip)
        completion.actual_delivery_time = actual_delivery_time
        completion.fuel_filled = fuel_filled
        completion.fuel_cost = fuel_cost
        completion.odometer_reading = odometer_reading
        completion.engine_temp = engine_temp
        completion.oil_quality = oil_quality
        completion.brake_condition = brake_condition
        completion.weather = weather or None
        completion.road_condition = road_condition or None
        completion.save()

        FuelLog.objects.create(
            vehicle=trip.vehicle,
            driver=driver,
            trip=trip,
            liters=fuel_filled,
            cost=fuel_cost,
            fuel_consumption=fuel_filled,
            is_anomaly=False,
        )

        trip.fuel_used_liters = fuel_filled
        trip.delivery_times = actual_delivery_time
        trip.weather_conditions = weather or None
        trip.road_conditions = road_condition or None
        trip.status = Trip.STATUS_COMPLETED
        trip.completed_at = timezone.now()
        trip.save(
            update_fields=[
                "fuel_used_liters",
                "delivery_times",
                "weather_conditions",
                "road_conditions",
                "status",
                "completed_at",
            ]
        )

        maintenance_required = False
        if (engine_temp is not None and engine_temp >= 110) or (
            brake_condition is not None and brake_condition < 40
        ) or (oil_quality is not None and oil_quality < 50):
            maintenance_required = True

        driver.status = Driver.STATUS_ASSIGNED if driver.assigned_vehicle else Driver.STATUS_AVAILABLE
        driver.save(update_fields=["status"])

        trip.vehicle.status = (
            Vehicle.STATUS_MAINT_REQUIRED if maintenance_required else Vehicle.STATUS_ASSIGNED
        )
        trip.vehicle.save(update_fields=["status", "updated_at"])

        if maintenance_required:
            Alert.objects.create(
                alert_type=Alert.TYPE_MAINTENANCE,
                severity=Alert.SEVERITY_HIGH,
                title="Predictive Maintenance Alert",
                message=f"Vehicle {trip.vehicle.plate_number} flagged after trip #{trip.id}.",
                vehicle=trip.vehicle,
                driver=driver,
                trip=trip,
            )

        messages.success(request, "Trip completed successfully.")
        return redirect("driver_trips")

    return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})


@login_required(login_url='login')
@role_required('driver')
def driver_completed_trips(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)
    trips = Trip.objects.filter(driver=driver, status=Trip.STATUS_COMPLETED).select_related("vehicle")

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    sort = (request.GET.get("sort") or "date_desc").strip()
    if date_from:
        trips = trips.filter(completed_at__date__gte=date_from)
    if date_to:
        trips = trips.filter(completed_at__date__lte=date_to)
    if sort == "date_asc":
        trips = trips.order_by("completed_at")
    else:
        trips = trips.order_by("-completed_at")

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="driver_completed_trips.csv"'
        writer = csv.writer(response)
        writer.writerow(["Trip ID", "Vehicle", "Date", "Fuel Used", "Delivery Time", "Status"])
        for trip in trips:
            writer.writerow(
                [
                    trip.id,
                    trip.vehicle.plate_number,
                    trip.completed_at,
                    trip.fuel_used_liters,
                    trip.delivery_times,
                    trip.get_status_display(),
                ]
            )
        return response

    return render(request, "driver_completed_trips.html", {"reg": reg, "trips": trips})


@login_required(login_url='login')
@role_required('driver')
def driver_vehicle_detail(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)
    return render(request, "driver_vehicle_detail.html", {"reg": reg, "vehicle": driver.assigned_vehicle})


def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect('home')





