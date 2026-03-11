from django.contrib import admin
from .models import (
    Alert,
    Driver,
    MLFeatureRecord,
    MaintenanceRecord,
    PredictionResult,
    Registration,
    Trip,
    TripCompletion,
    TripExpense,
    TripItem,
    TripPayment,
    Vehicle,
)

admin.site.register(Registration)
admin.site.register(Vehicle)
admin.site.register(Driver)
admin.site.register(Trip)
admin.site.register(TripCompletion)
admin.site.register(TripItem)
admin.site.register(TripExpense)
admin.site.register(TripPayment)
admin.site.register(MaintenanceRecord)
admin.site.register(MLFeatureRecord)
admin.site.register(PredictionResult)
admin.site.register(Alert)
