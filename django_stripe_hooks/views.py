from typing import Any, cast

import stripe

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import View

from django_stripe_hooks.models import STRIPE_VERSION, StripeModel
from django_stripe_hooks.utils import StripeService, fetch


DJANGO_MODELS = {
  DjangoModel.__name__: DjangoModel
  for DjangoModel in StripeModel.__subclasses__()
}


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhooks(View):
  """Intercept Stripe webhooks and update local database."""

  stripe_client = stripe.StripeClient(
    settings.STRIPE_SECRET_KEY,
    stripe_version=STRIPE_VERSION,
  )

  @property
  def stripe_name(self) -> str:
    return cast(str, self.event.data.object['object'])  # mypy wants key access

  def resolve_django_model(self) -> type[StripeModel[Any]]:
    django_model_name = ''.join(map(
      lambda s: s.title(),
      self.stripe_name.split('_'),
    ))
    return DJANGO_MODELS[django_model_name]

  def post(self, request: HttpRequest) -> HttpResponse:
    # Construct the event
    self.event = self.stripe_client.construct_event(
      payload=request.body,
      sig_header=request.META['HTTP_STRIPE_SIGNATURE'],
      secret=settings.STRIPE_WEBHOOK_SECRET_KEY,
    )

    try:
      # Resolve the related Django Model
      DjangoModel = self.resolve_django_model()
    except KeyError as e:
      django_model_name = e.args[0]
      response = HttpResponse(
        f"[django-stripe-hooks] {django_model_name} not implemented.",
        status=500,
      )
    else:
      # Fetch the Stripe object, falling back on the event data
      service = getattr(self.stripe_client.v1, f'{self.stripe_name}s', None)
      if isinstance(service, StripeService):
        self.stripe_obj = fetch(
          service=service,
          id=self.event.data.object['id'],
        ) or cast(stripe.StripeObject, self.event.data.object)
      else:
        # Fallback on event data if no service exists for this object type
        self.stripe_obj = cast(stripe.StripeObject, self.event.data.object)

      # Convert to Django model instance
      self.django_obj = DjangoModel.objects.from_stripe(self.stripe_obj)
      response = HttpResponse(
        "[django-stripe-hooks] Success!",
        status=200,
      )

    finally:
      # Allow authors to hook in
      author_hook = self.event.type.replace('.', '_')
      if hasattr(self, author_hook):
        response = getattr(self, author_hook)() or response

    return response
