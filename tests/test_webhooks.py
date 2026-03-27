import time

import stripe
import pytest

from django.conf import settings

from django_stripe_hooks.models import Product
from pytest_django.live_server import LiveServer


@pytest.mark.django_db
def test_stripe_product_created_webhook(live_server: LiveServer) -> None:
  # 1. Setup Stripe API Key using your preferred naming
  stripe.api_key = settings.STRIPE_SECRET_KEY

  unique_name = f'Test Product {int(time.time())}'

  # 2. Create the product via Stripe's API
  stripe_product = stripe.Product.create(
    name=unique_name,
    description='Created during automated integration test',
  )

  # 3. Polling Logic
  product_exists = False
  retries = 20
  while retries > 0:
    if Product.objects.filter(id=stripe_product.id).exists():  # type: ignore
      product_exists = True
      break
    time.sleep(0.5)
    retries -= 1

  # 4. Assertions
  assert product_exists, f'Product {stripe_product.id} was not created'

  local_product = Product.objects.get(id=stripe_product.id)  # type: ignore
  assert local_product.name == unique_name
