from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from .models import Registration 
from django.contrib.auth.decorators import login_required


def home(request):
    return render(request, 'home.html')

def signup(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        role = request.POST.get('role')

        # Validation
        if password != confirm_password:
            messages.error(request, "❌ Passwords do not match")
            return render(request, 'signup.html')

        if User.objects.filter(username=username).exists():
            messages.error(request, "⚠️ Username already exists")
            return render(request, 'signup.html')

        # Create Django user
        user = User.objects.create_user(
            username=username,
            password=password
        )

        # Create Registration profile (custom model)
        Registration.objects.create(
            user=user,
            user_role=role
        )

        messages.success(request, "✅ Account created successfully! Please login.")
        return redirect('login')

    return render(request, 'signup.html')


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        # Authenticate user
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # Get role from Registration model
            reg = Registration.objects.filter(user=user).first()
            if not reg:
                messages.error(request, "⚠️ User role not assigned. Contact admin.")
                logout(request)
                return redirect('login')

            # Store registration id in session
            request.session['reg_id'] = reg.id

            # Role-based redirect
            if reg.user_role == 'manager':
                messages.success(request, f"✅ Welcome {user.username}, Fleet Manager Dashboard loaded.")
                return redirect('manager_home')
            elif reg.user_role == 'driver':
                messages.success(request, f"✅ Welcome {user.username}, Driver Dashboard loaded.")
                return redirect('driver_home')
            else:
                messages.error(request, "⚠️ Invalid role. Contact admin.")
                logout(request)
                return redirect('login')

        else:
            messages.error(request, "❌ Invalid username or password")
            return redirect('login')

    return render(request, 'login.html')



@login_required
def manager_home(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    return render(request, 'manager_home.html', {'reg': reg})

@login_required
def driver_home(request):
    reg_id = request.session.get('reg_id')
    reg = Registration.objects.filter(id=reg_id).first()
    return render(request, 'driver_home.html', {'reg': reg})

def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect('home')
