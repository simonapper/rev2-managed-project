#accounts/views.py

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

User = get_user_model()

def set_password_from_invite(request, uidb64, token):
    # Resolve user from the signed uid
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except Exception as exc:
        raise Http404("Invalid invite link") from exc

    # Validate one-time token
    if not default_token_generator.check_token(user, token):
        raise Http404("Invite link expired or invalid")

    if request.method == "POST":
        form = SetPasswordForm(user=user, data=request.POST)
        if form.is_valid():
            form.save()
            # Redirect to login page (admin or your app login)
            return redirect("/admin/login/")
    else:
        form = SetPasswordForm(user=user)

    return render(request, "accounts/set_password.html", {"form": form})
