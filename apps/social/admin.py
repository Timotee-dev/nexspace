from django.contrib import admin

from .models import TopicFollow, UserFollow

admin.site.register(UserFollow)
admin.site.register(TopicFollow)
