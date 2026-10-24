from django.db.models.signals import pre_save
from django.dispatch import receiver


@receiver(pre_save, sender="accounts.User")
def end_app_sessions_on_password_change(sender, instance, **kwargs):
    """A new password signs the account out of every phone."""
    if not instance.pk:
        return
    old = sender.objects.filter(pk=instance.pk).values_list("password", flat=True).first()
    if old is not None and old != instance.password:
        from .models import DeviceToken

        DeviceToken.objects.filter(user_id=instance.pk).delete()
