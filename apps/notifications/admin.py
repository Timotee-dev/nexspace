from django.contrib import admin

from .models import Notification, PushSubscription

admin.site.register(Notification)
admin.site.register(PushSubscription)
