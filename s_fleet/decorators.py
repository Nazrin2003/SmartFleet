from django.shortcuts import redirect
from django.contrib import messages
from .models import Registration

def role_required(required_role):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            reg_id = request.session.get('reg_id')
            reg = Registration.objects.filter(id=reg_id).first()

            if reg and reg.user_role == required_role:
                return view_func(request, *args, **kwargs)
            else:
                messages.error(request, "⚠️ You do not have permission to access this page.")
                return redirect('home')
        return wrapper
    return decorator
