def nexspace(request):
    user = getattr(request, "user", None)
    theme = "system"
    is_moderator = False
    is_dept_admin = False
    unread = 0
    if user is not None and user.is_authenticated:
        if hasattr(user, "profile"):
            theme = user.profile.theme
        if user.department_id:
            is_moderator = user.can_moderate(user.department)
            is_dept_admin = user.has_role("department_admin", department=user.department)
        from apps.notifications.services import unread_count

        unread = unread_count(user)
    from django.conf import settings

    return {"nexai_enabled": settings.NEXAI_ENABLED, "theme_preference": theme, "is_moderator": is_moderator, "is_dept_admin": is_dept_admin,
            "unread_notifications": unread}
