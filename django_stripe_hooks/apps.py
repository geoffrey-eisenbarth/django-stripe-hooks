from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _


class StripeConfig(AppConfig):
  name = 'django_stripe_hooks'
  verbose_name = _('Stripe Payments')
  required_settings = [
    'STRIPE_PUBLIC_KEY',
    'STRIPE_SECRET_KEY',
    'STRIPE_WEBHOOK_SECRET_KEY',
  ]

  def ready(self) -> None:
    """Validate that necessary settings have been defined."""
    for value in self.required_settings:
      if not hasattr(settings, value):
        message = _(
          f"[django-stripe-hooks] {value} must be defined in settings.py."
        )
        raise ImproperlyConfigured(message)
