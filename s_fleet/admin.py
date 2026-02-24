from django.contrib import admin
from .models import (
    Alert,
    Driver,
    FuelLog,
    MLFeatureRecord,
    MaintenanceRecord,
    PredictionResult,
    Registration,
    Trip,
    TripCompletion,
    Vehicle,
)

admin.site.register(Registration)
admin.site.register(Vehicle)
admin.site.register(Driver)
admin.site.register(Trip)
admin.site.register(TripCompletion)
admin.site.register(FuelLog)
admin.site.register(MaintenanceRecord)
admin.site.register(MLFeatureRecord)
admin.site.register(PredictionResult)
admin.site.register(Alert)
