from typing import Any

import stripe

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import View

from django_stripe_hooks.models import StripeModel


DJANGO_MODELS = {
  DjangoModel.__name__: DjangoModel
  for DjangoModel in StripeModel.__subclasses__()
}


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhooks(View):
  """Intercept Stripe webhooks and update local database."""

  def resolve_django_model(self, stripe_name: str) -> type[StripeModel[Any]]:
    django_model_name = ''.join(map(
      lambda s: s.title(),
      stripe_name.split('_'),
    ))
    return DJANGO_MODELS[django_model_name]

  def post(self, request: HttpRequest) -> HttpResponse:
    # Construct the event
    self.event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]  # noqa: E501
      payload=request.body,
      sig_header=request.META['HTTP_STRIPE_SIGNATURE'],
      secret=settings.STRIPE_WEBHOOK_SECRET_KEY,
    )

    try:
      # Resolve the related Django Model
      DjangoModel = self.resolve_django_model(self.event.data.object.object)
    except KeyError as e:
      django_model_name = e.args[0]
      response = HttpResponse(
        f"[django-stripe-hooks] {django_model_name} not implemented.",
        status=500,
      )
    else:
      # Refresh from Stripe and create/update locally
      StripeClass = getattr(stripe, DjangoModel.__name__)
      self.stripe_obj = StripeClass.retrieve(self.event.data.object.id)
      self.django_obj = DjangoModel.from_stripe(self.stripe_obj)
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
