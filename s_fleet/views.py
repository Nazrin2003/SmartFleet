from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Prefetch
from django.http import HttpResponse
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import csv
import json
import math
import base64
import hmac
import hashlib
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .models import (
    Alert,
    Driver,
    MLFeatureRecord,
    PredictionResult,
    Registration,
    Trip,
    TripCompletion,
    TripExpense,
    TripItem,
    TripPayment,
    Vehicle,
)
from .ml.predictive_maintenance import predict_maintenance_for_trip
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


def _haversine_km(lat1, lng1, lat2, lng2):
    earth_radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(earth_radius_km * c, 3)


def _reverse_geocode(lat, lng):
    try:
        params = urlencode({"lat": lat, "lon": lng, "format": "jsonv2"})
        url = f"https://nominatim.openstreetmap.org/reverse?{params}"
        req = Request(url, headers={"User-Agent": "smrt_fleet/1.0"})
        with urlopen(req, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("display_name")
    except Exception:
        return None


def _normalize_brake_condition(raw_value):
    if raw_value in (None, ""):
        return None
    numeric = _to_float(raw_value)
    if numeric is not None:
        return numeric
    mapped = {
        "good": 80.0,
        "moderate": 55.0,
        "fair": 55.0,
        "poor": 30.0,
        "bad": 30.0,
    }
    return mapped.get(str(raw_value).strip().lower())


def _encode_maintenance_type(value):
    mapping = {"preventive": 0, "corrective": 1, "predictive": 2}
    return mapping.get((value or "").strip().lower(), 0)


def _encode_weather(value):
    mapping = {"clear": 0, "sunny": 0, "rainy": 1, "snowy": 2, "windy": 3}
    return mapping.get((value or "").strip().lower(), 0)


def _encode_road(value):
    mapping = {"highway": 0, "urban": 1, "rural": 2}
    return mapping.get((value or "").strip().lower(), 0)


def _encode_vehicle_type(value):
    mapping = {"van": 0, "truck": 1, "bus": 2, "car": 3}
    return mapping.get((value or "").strip().lower(), 0)


def _encode_route_info(origin, destination):
    text = f"{origin or ''} {destination or ''}".lower()
    if "urban" in text:
        return 1
    if "rural" in text:
        return 2
    return 0


def _format_location(address, lat, lng):
    if address:
        return address
    if lat is not None and lng is not None:
        return f"{lat}, {lng}"
    return "-"


def _razorpay_create_order(amount_paise, receipt):
    key_id = settings.RAZORPAY_KEY_ID
    key_secret = settings.RAZORPAY_KEY_SECRET
    if not key_id or not key_secret:
        return None, "Razorpay keys are not configured."

    payload = json.dumps(
        {
            "amount": int(amount_paise),
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        }
    ).encode("utf-8")

    auth = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("utf-8")
    req = Request("https://api.razorpay.com/v1/orders", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data, None
    except Exception as exc:
        return None, f"Razorpay order creation failed: {exc}"


def _razorpay_verify_signature(order_id, payment_id, signature):
    key_secret = settings.RAZORPAY_KEY_SECRET or ""
    payload = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(key_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _is_digits(value):
    return value.isdigit() if value else False


def _valid_phone(value):
    return _is_digits(value) and len(value) == 10


def _valid_year(value):
    if value is None:
        return True
    current_year = timezone.now().year
    return 1980 <= value <= current_year + 1


def _valid_range(value, low=None, high=None):
    if value is None:
        return True
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def _is_profile_complete(driver):
    if driver.license_expiry_date:
        expiry = driver.license_expiry_date
        if isinstance(expiry, str):
            try:
                expiry = timezone.datetime.strptime(expiry, "%Y-%m-%d").date()
            except ValueError:
                expiry = None
        if expiry:
            soon_threshold = timezone.now().date() + timedelta(days=30)
            if expiry <= soon_threshold:
                return False
    required_fields = [
        "license_number",
        "license_expiry_date",
        "phone_number",
        "years_of_experience",
        "emergency_contact_name",
        "emergency_contact_phone",
    ]
    for field in required_fields:
        value = getattr(driver, field, None)
        if value in (None, ""):
            return False
    return True


PAY_RATE_PER_KM = 12.0
BASE_TRIP_PAY = 0.0


def home(request):
    return render(request, 'home.html')


def signup(request):
    messages.info(request, 'Signup is disabled. Contact admin for account creation.')
    return redirect('login')

def login_view(request):
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')

        existing_user = User.objects.filter(username=username).first()
        if existing_user and not existing_user.is_active:
            messages.error(request, 'Your account has been blocked by the administrator. Please contact admin.')
            return redirect('login')

        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, 'Invalid username or password')
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
        'total_drivers': Driver.objects.count(),
        'total_vehicles': Vehicle.objects.count(),
        'total_trips': Trip.objects.count(),
    }
    return render(request, 'admin_home.html', context)


@login_required(login_url='login')
@role_required('admin')
def manager_list_adm(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    managers = Registration.objects.filter(user_role='manager').select_related('user')
    q = (request.GET.get('q') or '').strip()
    if q:
        managers = managers.filter(
            Q(user__username__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q)
        )
    managers = managers.order_by('user__username')
    return render(request, 'manager_list_adm.html', {'reg': reg, 'managers': managers, 'q': q})


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
        if len(username) < 3 or " " in username:
            messages.error(request, 'Username must be at least 3 characters and contain no spaces.')
            return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'add'})
        try:
            validate_password(password)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
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
        if len(username) < 3 or " " in username:
            messages.error(request, 'Username must be at least 3 characters and contain no spaces.')
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
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return render(request, 'manager_form_adm.html', {'reg': reg, 'mode': 'edit', 'manager': manager_reg})
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
    drivers = Driver.objects.select_related('user', 'assigned_vehicle')
    q = (request.GET.get('q') or '').strip()
    if q:
        drivers = drivers.filter(
            Q(user__username__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(license_number__icontains=q) |
            Q(phone_number__icontains=q)
        )
    drivers = drivers.order_by('user__username')
    for item in drivers:
        item.profile_complete = _is_profile_complete(item)
    return render(request, 'driver_list_adm.html', {'reg': reg, 'drivers': drivers, 'q': q})


@login_required(login_url='login')
@role_required('admin')
def add_driver_adm(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')
        if not username or not password:
            messages.error(request, 'Username and password are required.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})
        if len(username) < 3 or " " in username:
            messages.error(request, 'Username must be at least 3 characters and contain no spaces.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})
        try:
            validate_password(password)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'add'})

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=(request.POST.get('first_name') or '').strip(),
            last_name=(request.POST.get('last_name') or '').strip(),
            is_active=True,
        )
        Registration.objects.create(user=user, user_role='driver')
        Driver.objects.create(user=user)
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
        if not username:
            messages.error(request, 'Username is required.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})
        if len(username) < 3 or " " in username:
            messages.error(request, 'Username must be at least 3 characters and contain no spaces.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})
        if User.objects.filter(username=username).exclude(id=driver.user_id).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})

        user = driver.user
        user.username = username
        user.first_name = (request.POST.get('first_name') or '').strip()
        user.last_name = (request.POST.get('last_name') or '').strip()
        new_password = request.POST.get('password')
        if new_password:
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return render(request, 'driver_form_adm.html', {'reg': reg, 'mode': 'edit', 'driver_obj': driver})
            user.set_password(new_password)
        user.save()
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
    writer.writerow(['Trip ID', 'Driver', 'Vehicle', 'Status', 'Actual Start', 'Actual End', 'Created At'])
    for trip in trips:
        writer.writerow([
            trip.id,
            trip.driver.user.username,
            trip.vehicle.plate_number,
            trip.get_status_display(),
            _format_location(trip.start_address, trip.start_lat, trip.start_lng),
            _format_location(trip.end_address, trip.end_lat, trip.end_lng),
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
    maintenance_statuses = [Vehicle.STATUS_MAINTENANCE, Vehicle.STATUS_MAINT_REQUIRED]
    maintenance_alerts = Alert.objects.filter(
        alert_type=Alert.TYPE_MAINTENANCE
    ).order_by("-created_at")
    maintenance_vehicles = (
        Vehicle.objects.filter(status__in=maintenance_statuses)
        .select_related("assigned_driver", "assigned_driver__user")
        .prefetch_related(Prefetch("alerts", queryset=maintenance_alerts, to_attr="maintenance_alerts"))
        .order_by("plate_number")
    )
    vehicles_in_maintenance = maintenance_vehicles.count()
    dashboard_data = {
        'total_vehicles': total_vehicles,
        'total_drivers': total_drivers,
        'active_trips': active_trips,
        'pending_trips': pending_trips,
        'vehicles_in_maintenance': vehicles_in_maintenance,
        'maintenance_vehicles': maintenance_vehicles,
    }
    return render(request, 'manager_home.html', {'reg': reg, **dashboard_data})


@login_required(login_url='login')
@role_required('manager')
def vehicles_page(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    vehicles = Vehicle.objects.select_related("assigned_driver", "assigned_driver__user").order_by("plate_number")
    if q:
        vehicles = vehicles.filter(
            Q(plate_number__icontains=q)
            | Q(model_name__icontains=q)
            | Q(vehicle_type__icontains=q)
            | Q(assigned_driver__user__username__icontains=q)
        )
    if status in dict(Vehicle.STATUS_CHOICES):
        vehicles = vehicles.filter(status=status)
    return render(request, 'vehicles_list.html', {'reg': reg, 'vehicles': vehicles, 'q': q, 'status': status})


@login_required(login_url='login')
@role_required('manager')
def drivers_page(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    q = (request.GET.get("q") or "").strip()
    assignment_status = (request.GET.get("assignment_status") or "").strip()
    driver_users = User.objects.filter(registration__user_role='driver').order_by("username")
    for user in driver_users:
        Driver.objects.get_or_create(user=user)
    drivers = Driver.objects.select_related("user", "assigned_vehicle").order_by("user__username")
    if q:
        drivers = drivers.filter(
            Q(user__username__icontains=q)
            | Q(license_number__icontains=q)
            | Q(phone_number__icontains=q)
            | Q(assigned_vehicle__plate_number__icontains=q)
        )
    if assignment_status == "assigned":
        drivers = drivers.filter(assigned_vehicle__isnull=False)
    elif assignment_status == "not_assigned":
        drivers = drivers.filter(assigned_vehicle__isnull=True)
    return render(
        request,
        'drivers_list.html',
        {'reg': reg, 'drivers': drivers, 'q': q, 'assignment_status': assignment_status},
    )


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
            ["Trip ID", "Driver", "Vehicle", "Actual Start", "Actual End", "Actual Distance (km)", "Date"]
        )
        for trip in trips:
            writer.writerow(
                [
                    trip.id,
                    trip.driver.user.username,
                    trip.vehicle.plate_number,
                    _format_location(trip.start_address, trip.start_lat, trip.start_lng),
                    _format_location(trip.end_address, trip.end_lat, trip.end_lng),
                    trip.actual_distance_km,
                    trip.completed_at,
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
        if not _valid_year(year_of_manufacture):
            messages.error(request, "Year of manufacture is not valid.")
            return render(request, 'vehicle_form.html', {'reg': reg, 'mode': 'create'})
        if not _valid_range(load_capacity, 0):
            messages.error(request, "Load capacity must be zero or higher.")
            return render(request, 'vehicle_form.html', {'reg': reg, 'mode': 'create'})
        if not _valid_range(usage_hours, 0):
            messages.error(request, "Usage hours must be zero or higher.")
            return render(request, 'vehicle_form.html', {'reg': reg, 'mode': 'create'})
        if not _valid_range(battery_status, 0, 100):
            messages.error(request, "Battery status must be between 0 and 100.")
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
        planned_origin_lat = _to_float(request.POST.get("planned_origin_lat"))
        planned_origin_lng = _to_float(request.POST.get("planned_origin_lng"))
        planned_destination_lat = _to_float(request.POST.get("planned_destination_lat"))
        planned_destination_lng = _to_float(request.POST.get("planned_destination_lng"))
        load_details = (request.POST.get("load_details") or "").strip()
        estimated_distance_km = _to_float(request.POST.get("estimated_distance_km"))
        estimated_time_hours = _to_float(request.POST.get("estimated_time_hours"))
        scheduled_date = (request.POST.get("scheduled_date") or "").strip() or None
        item_names = request.POST.getlist("item_name[]")
        item_quantities = request.POST.getlist("item_quantity[]")
        item_unit_weights = request.POST.getlist("item_unit_weight[]")
        item_fragile_flags = request.POST.getlist("item_fragile[]")

        if not driver_id.isdigit():
            messages.error(request, "Please select a valid driver.")
            return render(
                request,
                'trip_create.html',
                {'reg': reg, 'drivers': eligible_drivers},
            )
        if not origin or not destination:
            messages.error(request, "Origin and destination are required.")
            return render(
                request,
                'trip_create.html',
                {'reg': reg, 'drivers': eligible_drivers},
            )
        if (planned_origin_lat is None) != (planned_origin_lng is None):
            messages.error(request, "Origin latitude/longitude must both be set.")
            return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})
        if (planned_destination_lat is None) != (planned_destination_lng is None):
            messages.error(request, "Destination latitude/longitude must both be set.")
            return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})
        if not _valid_range(estimated_distance_km, 0):
            messages.error(request, "Estimated distance must be zero or higher.")
            return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})
        if not _valid_range(estimated_time_hours, 0):
            messages.error(request, "Estimated time must be zero or higher.")
            return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})
        if not any((name or "").strip() for name in item_names):
            messages.error(request, "Please add at least one delivery item.")
            return render(
                request,
                'trip_create.html',
                {'reg': reg, 'drivers': eligible_drivers},
            )
        for idx, name in enumerate(item_names):
            item_name = (name or "").strip()
            if not item_name:
                continue
            quantity = _to_int(item_quantities[idx]) if idx < len(item_quantities) else None
            unit_weight = _to_float(item_unit_weights[idx]) if idx < len(item_unit_weights) else None
            if quantity is not None and quantity <= 0:
                messages.error(request, "Item quantity must be at least 1.")
                return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})
            if unit_weight is not None and unit_weight < 0:
                messages.error(request, "Item unit weight must be zero or higher.")
                return render(request, 'trip_create.html', {'reg': reg, 'drivers': eligible_drivers})

        driver = eligible_drivers.filter(id=int(driver_id)).first()
        if not driver:
            messages.error(
                request,
                "Selected driver is not eligible. Driver must have an assigned vehicle and no active trip.",
            )
            return render(
                request,
                'trip_create.html',
                {'reg': reg, 'drivers': eligible_drivers},
            )

        with transaction.atomic():
            trip = Trip.objects.create(
                driver=driver,
                vehicle=driver.assigned_vehicle,
                planned_origin=origin,
                planned_destination=destination,
                planned_origin_lat=planned_origin_lat,
                planned_origin_lng=planned_origin_lng,
                planned_destination_lat=planned_destination_lat,
                planned_destination_lng=planned_destination_lng,
                origin=origin,
                destination=destination,
                load_details=load_details or None,
                estimated_distance_km=estimated_distance_km,
                estimated_time_hours=estimated_time_hours,
                scheduled_date=scheduled_date,
                status=Trip.STATUS_PENDING,
            )
            for idx, name in enumerate(item_names):
                item_name = (name or "").strip()
                if not item_name:
                    continue
                quantity = _to_int(item_quantities[idx]) if idx < len(item_quantities) else None
                unit_weight = _to_float(item_unit_weights[idx]) if idx < len(item_unit_weights) else None
                total_weight = None
                if quantity is not None and unit_weight is not None:
                    total_weight = round(quantity * unit_weight, 3)
                TripItem.objects.create(
                    trip=trip,
                    item_name=item_name,
                    quantity=quantity if quantity is not None else 1,
                    unit_weight=unit_weight,
                    total_weight=total_weight,
                    is_fragile=(item_fragile_flags[idx] == "yes") if idx < len(item_fragile_flags) else False,
                )
        Alert.objects.create(
            alert_type=Alert.TYPE_TRIP,
            severity=Alert.SEVERITY_LOW,
            title="New Trip Assigned",
            message=(
                f"Trip #{trip.id} assigned: {origin} to {destination}. "
                f"Open My Trips to accept or reject."
            ),
            vehicle=driver.assigned_vehicle,
            driver=driver,
            trip=trip,
        )
        messages.success(request, "Trip created successfully and marked as Pending.")
        return redirect("trips_page")

    return render(
        request,
        'trip_create.html',
        {'reg': reg, 'drivers': eligible_drivers},
    )


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
    return render(
        request,
        "vehicle_detail.html",
        {
            "reg": reg,
            "vehicle": vehicle,
            "maintenance_history": maintenance_history,
            "trip_history": trip_history,
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
        if not _valid_year(year_of_manufacture):
            messages.error(request, "Year of manufacture is not valid.")
            return render(
                request, "vehicle_form.html", {"reg": reg, "mode": "edit", "vehicle": vehicle}
            )
        if not _valid_range(load_capacity, 0):
            messages.error(request, "Load capacity must be zero or higher.")
            return render(
                request, "vehicle_form.html", {"reg": reg, "mode": "edit", "vehicle": vehicle}
            )
        if not _valid_range(usage_hours, 0):
            messages.error(request, "Usage hours must be zero or higher.")
            return render(
                request, "vehicle_form.html", {"reg": reg, "mode": "edit", "vehicle": vehicle}
            )
        if not _valid_range(battery_status, 0, 100):
            messages.error(request, "Battery status must be between 0 and 100.")
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
    if not _is_profile_complete(driver):
        messages.error(
            request,
            "Driver profile is incomplete. Ask the driver to complete their profile before assignment.",
        )
        return redirect("driver_detail", driver_id=driver.id)

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
    total_drivers = Registration.objects.filter(user_role='driver').count()
    active_trips = Trip.objects.filter(driver=driver, status=Trip.STATUS_IN_PROGRESS).count()
    completed_trips = Trip.objects.filter(driver=driver, status=Trip.STATUS_COMPLETED).count()
    notifications = driver.alerts.filter(is_resolved=False).exclude(
        alert_type=Alert.TYPE_MAINTENANCE
    ).order_by("-created_at")
    return render(
        request,
        'driver_home.html',
        {
            'reg': reg,
            'driver': driver,
            'assigned_vehicle': driver.assigned_vehicle,
            'total_drivers': total_drivers,
            'active_trips': active_trips,
            'completed_trips': completed_trips,
            'profile_complete': _is_profile_complete(driver),
            'notifications': notifications[:5],
            'notifications_count': notifications.count(),
        },
    )


@login_required(login_url='login')
@role_required('driver')
def driver_profile(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        first_name = (request.POST.get("first_name") or "").strip()
        last_name = (request.POST.get("last_name") or "").strip()
        license_number = (request.POST.get("license_number") or "").strip() or None
        license_expiry_date = (request.POST.get("license_expiry_date") or "").strip() or None
        phone_number = (request.POST.get("phone_number") or "").strip() or None
        address = (request.POST.get("address") or "").strip() or None
        years_of_experience = _to_int(request.POST.get("years_of_experience"))
        emergency_contact_name = (request.POST.get("emergency_contact_name") or "").strip() or None
        emergency_contact_phone = (request.POST.get("emergency_contact_phone") or "").strip() or None

        if not username:
            messages.error(request, "Username is required.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )
        if len(username) < 3 or " " in username:
            messages.error(request, "Username must be at least 3 characters and contain no spaces.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )
        if User.objects.filter(username=username).exclude(id=request.user.id).exists():
            messages.error(request, "Username already exists.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )

        if (
            not license_number
            or not license_expiry_date
            or not phone_number
            or years_of_experience is None
            or not emergency_contact_name
            or not emergency_contact_phone
        ):
            messages.error(request, "Please complete all required fields.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )
        if not _valid_phone(phone_number):
            messages.error(request, "Phone number must be 10 digits.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )
        if not _valid_phone(emergency_contact_phone):
            messages.error(request, "Emergency contact phone must be 10 digits.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )
        if years_of_experience is not None and years_of_experience < 0:
            messages.error(request, "Years of experience must be zero or higher.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )
        try:
            exp_date = timezone.datetime.strptime(license_expiry_date, "%Y-%m-%d").date()
            if exp_date <= timezone.now().date():
                messages.error(request, "License expiry date must be in the future.")
                return render(
                    request,
                    "driver_profile.html",
                    {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
                )
        except ValueError:
            messages.error(request, "License expiry date format is invalid.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )
        if Driver.objects.filter(license_number=license_number).exclude(id=driver.id).exists():
            messages.error(request, "License number already exists.")
            return render(
                request,
                "driver_profile.html",
                {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
            )

        request.user.username = username
        request.user.first_name = first_name
        request.user.last_name = last_name
        request.user.save(update_fields=["username", "first_name", "last_name"])

        driver.license_number = license_number
        driver.license_expiry_date = license_expiry_date
        driver.phone_number = phone_number
        driver.address = address
        driver.years_of_experience = years_of_experience
        driver.emergency_contact_name = emergency_contact_name
        driver.emergency_contact_phone = emergency_contact_phone
        driver.save()
        messages.success(request, "Profile updated.")

    return render(
        request,
        "driver_profile.html",
        {"reg": reg, "driver": driver, "profile_complete": _is_profile_complete(driver)},
    )


@login_required(login_url='login')
@role_required('admin')
def driver_profile_adm(request, driver_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    driver = get_object_or_404(Driver.objects.select_related("user"), id=driver_id)
    return render(request, "driver_profile_adm.html", {"reg": reg, "driver": driver})


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
            start_lat = _to_float(request.POST.get("start_lat"))
            start_lng = _to_float(request.POST.get("start_lng"))
            if start_lat is None or start_lng is None:
                messages.error(request, "Unable to capture GPS location. Allow location access and try again.")
                return redirect("trip_detail", trip_id=trip.id)
            if trip.vehicle.status in [Vehicle.STATUS_MAINTENANCE, Vehicle.STATUS_MAINT_REQUIRED]:
                messages.error(request, "Vehicle is under maintenance and cannot start trip.")
            else:
                start_address = _reverse_geocode(start_lat, start_lng)
                trip.status = Trip.STATUS_IN_PROGRESS
                trip.started_at = timezone.now()
                trip.start_lat = start_lat
                trip.start_lng = start_lng
                trip.start_address = start_address
                trip.save(update_fields=["status", "started_at", "start_lat", "start_lng", "start_address"])
                driver.status = Driver.STATUS_IN_TRIP
                driver.save(update_fields=["status"])
                trip.vehicle.status = Vehicle.STATUS_IN_TRIP
                trip.vehicle.save(update_fields=["status", "updated_at"])
                messages.success(request, "Trip accepted. Status changed to In Progress.")
                Alert.objects.filter(
                    alert_type=Alert.TYPE_TRIP,
                    driver=driver,
                    trip=trip,
                    is_resolved=False,
                ).update(is_resolved=True, resolved_at=timezone.now())
            return redirect("trip_detail", trip_id=trip.id)

        if action == "reject" and trip.status == Trip.STATUS_PENDING:
            trip.status = Trip.STATUS_REJECTED
            trip.save(update_fields=["status"])
            Alert.objects.filter(
                alert_type=Alert.TYPE_TRIP,
                driver=driver,
                trip=trip,
                is_resolved=False,
            ).update(is_resolved=True, resolved_at=timezone.now())
            messages.info(request, "Trip rejected.")
            return redirect("driver_trips")

        if action == "complete" and trip.status == Trip.STATUS_IN_PROGRESS:
            return redirect("trip_complete_form", trip_id=trip.id)

    return render(
        request,
        "trip_detail.html",
        {
            "reg": reg,
            "trip": trip,
            "show_actions": True,
        },
    )


@login_required(login_url='login')
@role_required('manager')
def trip_detail_manager(request, trip_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    trip = Trip.objects.select_related("vehicle", "driver", "driver__user").filter(id=trip_id).first()
    if not trip:
        messages.error(request, "Trip not found.")
        return redirect("trips_page")
    return render(request, "trip_detail.html", {"reg": reg, "trip": trip, "show_actions": False})


@login_required(login_url='login')
@role_required('admin')
def trip_detail_admin(request, trip_id):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    trip = Trip.objects.select_related("vehicle", "driver", "driver__user").filter(id=trip_id).first()
    if not trip:
        messages.error(request, "Trip not found.")
        return redirect("trips_admin")
    return render(request, "trip_detail.html", {"reg": reg, "trip": trip, "show_actions": False})


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
        engine_temp = _to_float(request.POST.get("engine_temp"))
        oil_quality = _to_float(request.POST.get("oil_quality"))
        brake_condition = _normalize_brake_condition(request.POST.get("brake_condition"))
        weather = (request.POST.get("weather") or "").strip() or None
        road_condition = (request.POST.get("road_condition") or "").strip() or None
        end_lat = _to_float(request.POST.get("end_lat"))
        end_lng = _to_float(request.POST.get("end_lng"))
        expense_descs = request.POST.getlist("expense_desc[]")
        expense_amounts = request.POST.getlist("expense_amount[]")

        if engine_temp is None or oil_quality is None or brake_condition is None:
            messages.error(
                request,
                "Engine temperature, oil quality, and brake condition are required.",
            )
            return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})
        if not _valid_range(engine_temp, 0, 200):
            messages.error(request, "Engine temperature must be between 0 and 200.")
            return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})
        if not _valid_range(oil_quality, 0, 100):
            messages.error(request, "Oil quality must be between 0 and 100.")
            return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})
        if not _valid_range(brake_condition, 0, 100):
            messages.error(request, "Brake condition must be between 0 and 100.")
            return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})
        if end_lat is None or end_lng is None:
            messages.error(request, "Unable to capture end GPS location. Allow location access and submit again.")
            return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})
        for idx, desc in enumerate(expense_descs):
            description = (desc or "").strip()
            if not description:
                continue
            amount = _to_float(expense_amounts[idx]) if idx < len(expense_amounts) else None
            if amount is None:
                messages.error(request, "Expense amount must be a valid number.")
                return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})
            if amount < 0:
                messages.error(request, "Expense amount cannot be negative.")
                return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})

        completion, _ = TripCompletion.objects.get_or_create(trip=trip)
        if trip.started_at:
            duration_seconds = (timezone.now() - trip.started_at).total_seconds()
            completion.actual_delivery_time = round(duration_seconds / 3600, 2)
        completion.engine_temp = engine_temp
        completion.oil_quality = oil_quality
        completion.brake_condition = brake_condition
        completion.weather = weather
        completion.road_condition = road_condition
        completion.save()
        trip.status = Trip.STATUS_COMPLETED
        trip.completed_at = timezone.now()
        trip.weather_conditions = weather
        trip.road_conditions = road_condition
        trip.end_lat = end_lat
        trip.end_lng = end_lng
        trip.end_address = _reverse_geocode(end_lat, end_lng)
        if trip.start_lat is not None and trip.start_lng is not None:
            trip.actual_distance_km = _haversine_km(trip.start_lat, trip.start_lng, end_lat, end_lng)
        trip.save(
            update_fields=[
                "status",
                "completed_at",
                "weather_conditions",
                "road_conditions",
                "end_lat",
                "end_lng",
                "end_address",
                "actual_distance_km",
            ]
        )

        prediction = predict_maintenance_for_trip(
            trip=trip,
            completion=completion,
        )
        maintenance_required = bool(prediction["maintenance_required"])
        predictive_score = _to_float(prediction.get("predictive_score")) or 0.0

        latest_maintenance = trip.vehicle.maintenance_history.order_by("-created_at").first()
        ml_record = MLFeatureRecord.objects.create(
            vehicle=trip.vehicle,
            driver=driver,
            trip=trip,
            year_of_manufacture=trip.vehicle.year_of_manufacture,
            vehicle_type=_encode_vehicle_type(trip.vehicle.vehicle_type),
            usage_hours=trip.vehicle.usage_hours,
            route_info=_encode_route_info(
                trip.planned_origin or trip.origin,
                trip.planned_destination or trip.destination,
            ),
            load_capacity=trip.vehicle.load_capacity,
            actual_load=trip.actual_load,
            maintenance_type=_encode_maintenance_type(getattr(latest_maintenance, "maintenance_type", None)),
            maintenance_cost=getattr(latest_maintenance, "maintenance_cost", None),
            engine_temperature=engine_temp,
            tire_pressure=getattr(latest_maintenance, "tire_pressure", None),
            battery_status=trip.vehicle.battery_status,
            vibration_levels=getattr(latest_maintenance, "vibration_levels", None),
            oil_quality=oil_quality,
            brake_condition=brake_condition,
            failure_history=trip.vehicle.maintenance_history.filter(is_resolved=False).count(),
            anomalies_detected=0,
            predictive_score=predictive_score,
            maintenance_required=1 if maintenance_required else 0,
            weather_conditions=_encode_weather(weather),
            road_conditions=_encode_road(road_condition),
            delivery_times=completion.actual_delivery_time,
            downtime_maintenance=getattr(latest_maintenance, "downtime_maintenance", None),
            impact_on_efficiency=getattr(latest_maintenance, "impact_on_efficiency", None),
            maintenance_year=timezone.now().year,
            maintenance_month=timezone.now().month,
        )

        prediction_result = PredictionResult.objects.create(
            record=ml_record,
            vehicle=trip.vehicle,
            driver=driver,
            trip=trip,
            model_name=prediction.get("model_name") or "maintenance_model",
            model_version=prediction.get("model_version") or "v1",
            predictive_score=predictive_score,
            anomalies_detected=False,
            maintenance_required=maintenance_required,
        )

        # Dispatch model: after each completed trip, release driver and vehicle assignment.
        completed_vehicle = trip.vehicle
        with transaction.atomic():
            driver.assigned_vehicle = None
            driver.status = Driver.STATUS_AVAILABLE
            driver.save(update_fields=["assigned_vehicle", "status"])

            completed_vehicle.status = (
                Vehicle.STATUS_MAINT_REQUIRED if maintenance_required else Vehicle.STATUS_AVAILABLE
            )
            completed_vehicle.save(update_fields=["status", "updated_at"])

        if maintenance_required:
            Alert.objects.create(
                alert_type=Alert.TYPE_MAINTENANCE,
                severity=Alert.SEVERITY_HIGH,
                title="Predictive Maintenance Alert",
                message=(
                    f"Vehicle {trip.vehicle.plate_number} flagged after trip #{trip.id}. "
                    f"Predictive score: {predictive_score:.3f}."
                ),
                vehicle=completed_vehicle,
                driver=None,
                trip=trip,
                prediction=prediction_result,
            )

        expense_total = 0.0
        TripExpense.objects.filter(trip=trip).delete()
        for idx, desc in enumerate(expense_descs):
            description = (desc or "").strip()
            if not description:
                continue
            amount = _to_float(expense_amounts[idx]) if idx < len(expense_amounts) else None
            if amount is None:
                continue
            TripExpense.objects.create(trip=trip, description=description, amount=amount)
            expense_total += float(amount)

        distance_km = trip.actual_distance_km
        if distance_km is None:
            distance_km = trip.estimated_distance_km or 0.0
        base_amount = BASE_TRIP_PAY + (float(distance_km) * PAY_RATE_PER_KM)
        total_amount = base_amount + expense_total
        payment, created = TripPayment.objects.get_or_create(
            trip=trip,
            defaults={
                "driver": driver,
                "base_amount": base_amount,
                "expense_total": expense_total,
                "total_amount": total_amount,
                "status": TripPayment.STATUS_PENDING,
            },
        )
        if not created and payment.status != TripPayment.STATUS_PAID:
            payment.driver = driver
            payment.base_amount = base_amount
            payment.expense_total = expense_total
            payment.total_amount = total_amount
            payment.status = TripPayment.STATUS_PENDING
            payment.approved_by = None
            payment.approved_at = None
            payment.paid_at = None
            payment.save(
                update_fields=[
                    "driver",
                    "base_amount",
                    "expense_total",
                    "total_amount",
                    "status",
                    "approved_by",
                    "approved_at",
                    "paid_at",
                ]
            )

        Alert.objects.filter(
            alert_type=Alert.TYPE_TRIP,
            driver=driver,
            trip=trip,
            is_resolved=False,
        ).update(is_resolved=True, resolved_at=timezone.now())

        messages.success(request, "Trip completed successfully.")
        return redirect("driver_trips")

    return render(request, "trip_complete_form.html", {"reg": reg, "trip": trip})


@login_required(login_url="login")
@role_required("manager")
def manager_payments(request):
    reg_id = request.session.get("reg_id")
    reg = Registration.objects.filter(id=reg_id).first()
    status = (request.GET.get("status") or "").strip()
    payments = TripPayment.objects.select_related("trip", "driver", "driver__user")
    if status in dict(TripPayment.STATUS_CHOICES):
        payments = payments.filter(status=status)
    payments = payments.order_by("-created_at")
    return render(
        request,
        "manager_payments.html",
        {
            "reg": reg,
            "payments": payments,
            "status": status,
            "status_choices": TripPayment.STATUS_CHOICES,
        },
    )


@login_required(login_url="login")
@role_required("manager")
def approve_payment(request, payment_id):
    payment = TripPayment.objects.select_related("trip", "driver").filter(id=payment_id).first()
    if not payment:
        messages.error(request, "Payment not found.")
        return redirect("manager_payments")
    if payment.status == TripPayment.STATUS_PAID:
        messages.info(request, "Payment already marked as paid.")
        return redirect("manager_payments")
    payment.status = TripPayment.STATUS_APPROVED
    payment.approved_by = request.user
    payment.approved_at = timezone.now()
    payment.save(update_fields=["status", "approved_by", "approved_at"])
    messages.success(request, "Payment approved.")
    return redirect("manager_payments")


@login_required(login_url="login")
@role_required("manager")
def pay_driver(request, payment_id):
    payment = TripPayment.objects.select_related("trip", "driver").filter(id=payment_id).first()
    if not payment:
        messages.error(request, "Payment not found.")
        return redirect("manager_payments")
    payment.status = TripPayment.STATUS_PAID
    if payment.approved_by is None:
        payment.approved_by = request.user
        payment.approved_at = timezone.now()
    payment.paid_at = timezone.now()
    payment.save(update_fields=["status", "approved_by", "approved_at", "paid_at"])
    messages.success(request, "Payment marked as paid.")
    return redirect("manager_payments")


@login_required(login_url="login")
@role_required("manager")
def pay_driver_razorpay(request, payment_id):
    payment = TripPayment.objects.select_related("trip", "driver", "driver__user").filter(id=payment_id).first()
    if not payment:
        messages.error(request, "Payment not found.")
        return redirect("manager_payments")
    if payment.status == TripPayment.STATUS_PAID:
        messages.info(request, "Payment already marked as paid.")
        return redirect("manager_payments")

    amount = float(payment.total_amount or 0.0)
    if amount <= 0:
        messages.error(request, "Payment amount must be greater than zero.")
        return redirect("manager_payments")

    order_data, error = _razorpay_create_order(
        amount_paise=int(amount * 100),
        receipt=f"trip-{payment.trip_id}-payment-{payment.id}",
    )
    if error:
        messages.error(request, error)
        return redirect("manager_payments")

    payment.payment_gateway = "razorpay"
    payment.gateway_order_id = order_data.get("id")
    if payment.status == TripPayment.STATUS_PENDING:
        payment.status = TripPayment.STATUS_APPROVED
        payment.approved_by = request.user
        payment.approved_at = timezone.now()
    payment.save(update_fields=["payment_gateway", "gateway_order_id", "status", "approved_by", "approved_at"])

    context = {
        "reg": Registration.objects.filter(id=request.session.get("reg_id")).first(),
        "payment": payment,
        "razorpay_key": settings.RAZORPAY_KEY_ID,
        "order_id": order_data.get("id"),
        "amount": order_data.get("amount"),
        "currency": order_data.get("currency"),
        "callback_url": request.build_absolute_uri("/manager/payments/razorpay/callback/"),
    }
    return render(request, "razorpay_checkout.html", context)


@login_required(login_url="login")
@role_required("manager")
def pay_driver_dummy(request, payment_id):
    payment = TripPayment.objects.select_related("trip", "driver", "driver__user").filter(id=payment_id).first()
    if not payment:
        messages.error(request, "Payment not found.")
        return redirect("manager_payments")
    if payment.status == TripPayment.STATUS_PAID:
        messages.info(request, "Payment already marked as paid.")
        return redirect("manager_payments")

    if request.method == "POST":
        payment.payment_gateway = "dummy"
        payment.status = TripPayment.STATUS_PAID
        if payment.approved_by is None:
            payment.approved_by = request.user
            payment.approved_at = timezone.now()
        payment.paid_at = timezone.now()
        payment.save(
            update_fields=[
                "payment_gateway",
                "status",
                "approved_by",
                "approved_at",
                "paid_at",
            ]
        )
        messages.success(request, "Payment processed.")
        return redirect("manager_payments")

    context = {
        "reg": Registration.objects.filter(id=request.session.get("reg_id")).first(),
        "payment": payment,
    }
    return render(request, "dummy_payment.html", context)


@csrf_exempt
@login_required(login_url="login")
@role_required("manager")
def razorpay_callback(request):
    if request.method != "POST":
        messages.error(request, "Invalid payment callback.")
        return redirect("manager_payments")

    order_id = request.POST.get("razorpay_order_id")
    payment_id = request.POST.get("razorpay_payment_id")
    signature = request.POST.get("razorpay_signature")

    if not order_id or not payment_id or not signature:
        messages.error(request, "Payment confirmation incomplete.")
        return redirect("manager_payments")

    payment = TripPayment.objects.filter(gateway_order_id=order_id).first()
    if not payment:
        messages.error(request, "Payment record not found.")
        return redirect("manager_payments")

    if not _razorpay_verify_signature(order_id, payment_id, signature):
        messages.error(request, "Payment signature verification failed.")
        return redirect("manager_payments")

    payment.gateway_payment_id = payment_id
    payment.gateway_signature = signature
    payment.status = TripPayment.STATUS_PAID
    if payment.approved_by is None:
        payment.approved_by = request.user
        payment.approved_at = timezone.now()
    payment.paid_at = timezone.now()
    payment.save(
        update_fields=[
            "gateway_payment_id",
            "gateway_signature",
            "status",
            "approved_by",
            "approved_at",
            "paid_at",
        ]
    )

    messages.success(request, "Payment successful via Razorpay.")
    return redirect("manager_payments")


@login_required(login_url="login")
@role_required("driver")
def driver_payments(request):
    reg_id = request.session.get("reg_id")
    reg = Registration.objects.filter(id=reg_id).first()
    driver, _ = Driver.objects.get_or_create(user=request.user)
    payments = TripPayment.objects.filter(driver=driver).select_related("trip").order_by("-created_at")
    return render(request, "driver_payments.html", {"reg": reg, "payments": payments})


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
        writer.writerow(
            ["Trip ID", "Vehicle", "Actual Start", "Actual End", "Actual Distance (km)", "Date", "Delivery Time"]
        )
        for trip in trips:
            writer.writerow(
                [
                    trip.id,
                    trip.vehicle.plate_number,
                    _format_location(trip.start_address, trip.start_lat, trip.start_lng),
                    _format_location(trip.end_address, trip.end_lat, trip.end_lng),
                    trip.actual_distance_km,
                    trip.completed_at,
                    trip.delivery_times,
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


@login_required(login_url='login')
def change_password_view(request):
    reg = Registration.objects.filter(user=request.user).first()
    if not reg:
        messages.error(request, "User role not found. Contact admin.")
        return redirect("login")

    role_to_base = {
        "admin": "base_admin.html",
        "manager": "base_manager.html",
        "driver": "base_driver.html",
    }
    role_to_home = {
        "admin": "admin_home",
        "manager": "manager_home",
        "driver": "driver_home",
    }
    base_template = role_to_base.get(reg.user_role, "base_home.html")
    home_url_name = role_to_home.get(reg.user_role, "home")

    if request.method == "POST":
        current_password = request.POST.get("current_password") or ""
        new_password = request.POST.get("new_password") or ""
        confirm_password = request.POST.get("confirm_password") or ""

        if not request.user.check_password(current_password):
            messages.error(request, "Current password is incorrect.")
            return render(
                request,
                "change_password.html",
                {"reg": reg, "base_template": base_template, "home_url_name": home_url_name},
            )
        if not new_password:
            messages.error(request, "New password is required.")
            return render(
                request,
                "change_password.html",
                {"reg": reg, "base_template": base_template, "home_url_name": home_url_name},
            )
        if new_password != confirm_password:
            messages.error(request, "New password and confirm password do not match.")
            return render(
                request,
                "change_password.html",
                {"reg": reg, "base_template": base_template, "home_url_name": home_url_name},
            )
        try:
            validate_password(new_password, user=request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(
                request,
                "change_password.html",
                {"reg": reg, "base_template": base_template, "home_url_name": home_url_name},
            )

        request.user.set_password(new_password)
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        messages.success(request, "Password updated successfully.")
        return redirect("change_password")

    return render(
        request,
        "change_password.html",
        {"reg": reg, "base_template": base_template, "home_url_name": home_url_name},
    )


def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect('home')





