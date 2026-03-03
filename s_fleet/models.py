from django.db import models
from django.contrib.auth.models import User


class Registration(models.Model):
    password = models.CharField(max_length=200, null=True)
    user_role = models.CharField(max_length=200, null=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True)

    def __str__(self):
        role = self.user_role or "unknown"
        username = self.user.username if self.user else "unassigned"
        return f"{username} ({role})"


class Vehicle(models.Model):
    STATUS_AVAILABLE = "available"
    STATUS_ASSIGNED = "assigned"
    STATUS_IN_TRIP = "in_trip"
    STATUS_MAINTENANCE = "maintenance"
    STATUS_MAINT_REQUIRED = "maintenance_required"
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Available"),
        (STATUS_ASSIGNED, "Assigned"),
        (STATUS_IN_TRIP, "In Trip"),
        (STATUS_MAINTENANCE, "Maintenance"),
        (STATUS_MAINT_REQUIRED, "Maintenance Required"),
    ]

    plate_number = models.CharField(max_length=30, unique=True)
    year_of_manufacture = models.PositiveIntegerField(blank=True, null=True)
    model_name = models.CharField(max_length=100)
    vehicle_type = models.CharField(max_length=80, blank=True, null=True)
    load_capacity = models.FloatField(blank=True, null=True)
    usage_hours = models.FloatField(default=0)
    battery_status = models.FloatField(blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.plate_number} - {self.model_name}"


class Driver(models.Model):
    RISK_LOW = "low"
    RISK_MEDIUM = "medium"
    RISK_HIGH = "high"
    RISK_CHOICES = [
        (RISK_LOW, "Low"),
        (RISK_MEDIUM, "Medium"),
        (RISK_HIGH, "High"),
    ]

    STATUS_AVAILABLE = "available"
    STATUS_ASSIGNED = "assigned"
    STATUS_IN_TRIP = "in_trip"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Available"),
        (STATUS_ASSIGNED, "Assigned"),
        (STATUS_IN_TRIP, "In Trip"),
        (STATUS_BLOCKED, "Blocked"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    license_number = models.CharField(max_length=80, unique=True, blank=True, null=True)
    license_expiry_date = models.DateField(blank=True, null=True)
    phone_number = models.CharField(max_length=30, blank=True, null=True)
    address = models.CharField(max_length=200, blank=True, null=True)
    years_of_experience = models.PositiveIntegerField(blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=120, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True, null=True)
    assigned_vehicle = models.OneToOneField(
        Vehicle,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="assigned_driver",
    )
    risk_level = models.CharField(max_length=15, choices=RISK_CHOICES, default=RISK_LOW)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username


class Trip(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_REJECTED, "Rejected"),
    ]

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="trips")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="trips")
    origin = models.CharField(max_length=120)
    destination = models.CharField(max_length=120)
    load_details = models.CharField(max_length=200, blank=True, null=True)
    actual_load = models.FloatField(blank=True, null=True)
    estimated_distance_km = models.FloatField(blank=True, null=True)
    expected_fuel_liters = models.FloatField(blank=True, null=True)
    estimated_time_hours = models.FloatField(blank=True, null=True)
    scheduled_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    fuel_used_liters = models.FloatField(blank=True, null=True)
    weather_conditions = models.CharField(max_length=80, blank=True, null=True)
    road_conditions = models.CharField(max_length=80, blank=True, null=True)
    delivery_times = models.FloatField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Trip #{self.id} - {self.origin} to {self.destination}"


class TripCompletion(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name="completion")
    actual_delivery_time = models.FloatField(blank=True, null=True)
    fuel_filled = models.FloatField(blank=True, null=True)
    fuel_cost = models.FloatField(blank=True, null=True)
    odometer_reading = models.FloatField(blank=True, null=True)
    engine_temp = models.FloatField(blank=True, null=True)
    oil_quality = models.FloatField(blank=True, null=True)
    brake_condition = models.FloatField(blank=True, null=True)
    weather = models.CharField(max_length=80, blank=True, null=True)
    road_condition = models.CharField(max_length=80, blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Completion for Trip #{self.trip_id}"


class FuelLog(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="fuel_logs")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, blank=True, null=True, related_name="fuel_logs")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, blank=True, null=True, related_name="fuel_logs")
    liters = models.FloatField()
    cost = models.FloatField(blank=True, null=True)
    fuel_consumption = models.FloatField(blank=True, null=True)
    is_anomaly = models.BooleanField(default=False)
    logged_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"FuelLog #{self.id} ({self.vehicle.plate_number})"


class MaintenanceRecord(models.Model):
    TYPE_PREVENTIVE = "preventive"
    TYPE_CORRECTIVE = "corrective"
    TYPE_PREDICTIVE = "predictive"
    TYPE_CHOICES = [
        (TYPE_PREVENTIVE, "Preventive"),
        (TYPE_CORRECTIVE, "Corrective"),
        (TYPE_PREDICTIVE, "Predictive"),
    ]

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="maintenance_history")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, blank=True, null=True, related_name="maintenance_records")
    maintenance_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_PREVENTIVE)
    maintenance_cost = models.FloatField(blank=True, null=True)
    engine_temperature = models.FloatField(blank=True, null=True)
    tire_pressure = models.FloatField(blank=True, null=True)
    vibration_levels = models.FloatField(blank=True, null=True)
    oil_quality = models.FloatField(blank=True, null=True)
    brake_condition = models.FloatField(blank=True, null=True)
    downtime_maintenance = models.FloatField(blank=True, null=True)
    impact_on_efficiency = models.FloatField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    risk_score = models.FloatField(blank=True, null=True)
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Maintenance #{self.id} ({self.vehicle.plate_number})"


class MLFeatureRecord(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="ml_records")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, blank=True, null=True, related_name="ml_records")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, blank=True, null=True, related_name="ml_records")

    # CSV-aligned feature fields
    make_and_model = models.IntegerField(blank=True, null=True)
    year_of_manufacture = models.IntegerField(blank=True, null=True)
    vehicle_type = models.IntegerField(blank=True, null=True)
    usage_hours = models.FloatField(blank=True, null=True)
    route_info = models.IntegerField(blank=True, null=True)
    load_capacity = models.FloatField(blank=True, null=True)
    actual_load = models.FloatField(blank=True, null=True)
    maintenance_type = models.IntegerField(blank=True, null=True)
    maintenance_cost = models.FloatField(blank=True, null=True)
    engine_temperature = models.FloatField(blank=True, null=True)
    tire_pressure = models.FloatField(blank=True, null=True)
    fuel_consumption = models.FloatField(blank=True, null=True)
    battery_status = models.FloatField(blank=True, null=True)
    vibration_levels = models.FloatField(blank=True, null=True)
    oil_quality = models.FloatField(blank=True, null=True)
    brake_condition = models.FloatField(blank=True, null=True)
    failure_history = models.IntegerField(blank=True, null=True)
    anomalies_detected = models.IntegerField(blank=True, null=True)
    predictive_score = models.FloatField(blank=True, null=True)
    maintenance_required = models.IntegerField(blank=True, null=True)
    weather_conditions = models.IntegerField(blank=True, null=True)
    road_conditions = models.IntegerField(blank=True, null=True)
    delivery_times = models.FloatField(blank=True, null=True)
    downtime_maintenance = models.FloatField(blank=True, null=True)
    impact_on_efficiency = models.FloatField(blank=True, null=True)
    maintenance_year = models.IntegerField(blank=True, null=True)
    maintenance_month = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ML features #{self.id} ({self.vehicle.plate_number})"


class PredictionResult(models.Model):
    record = models.ForeignKey(MLFeatureRecord, on_delete=models.CASCADE, related_name="predictions")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="predictions")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, blank=True, null=True, related_name="predictions")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, blank=True, null=True, related_name="predictions")
    model_name = models.CharField(max_length=120, default="maintenance_model")
    model_version = models.CharField(max_length=30, default="v1")
    predictive_score = models.FloatField()
    anomalies_detected = models.BooleanField(default=False)
    maintenance_required = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Prediction #{self.id} ({self.vehicle.plate_number})"


class Alert(models.Model):
    TYPE_MAINTENANCE = "maintenance"
    TYPE_FUEL = "fuel"
    TYPE_DRIVER_RISK = "driver_risk"
    TYPE_CHOICES = [
        (TYPE_MAINTENANCE, "Maintenance"),
        (TYPE_FUEL, "Fuel"),
        (TYPE_DRIVER_RISK, "Driver Risk"),
    ]

    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CHOICES = [
        (SEVERITY_LOW, "Low"),
        (SEVERITY_MEDIUM, "Medium"),
        (SEVERITY_HIGH, "High"),
    ]

    alert_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM)
    title = models.CharField(max_length=120)
    message = models.TextField()
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, blank=True, null=True, related_name="alerts")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, blank=True, null=True, related_name="alerts")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, blank=True, null=True, related_name="alerts")
    prediction = models.ForeignKey(
        PredictionResult,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="alerts",
    )
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.get_alert_type_display()} alert #{self.id}"
