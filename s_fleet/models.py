from django.db import models
from django.contrib.auth.models import User

class Registration(models.Model):
    password = models.CharField(max_length=200, null=True)
    user_role = models.CharField(max_length=200, null=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True)

    