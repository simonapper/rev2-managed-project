# -*- coding: utf-8 -*-
# projects/urls.py
# Purpose:
# Notifications UI routes (prototype-safe GET actions)

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # ...
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
