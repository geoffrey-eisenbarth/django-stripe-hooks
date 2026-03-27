from django.urls import path

from django_stripe_hooks.views import StripeWebhooks


urlpatterns = [
  path(
    'webhooks/',
    StripeWebhooks.as_view(),
    name='stripe_webhooks',
  ),
]
