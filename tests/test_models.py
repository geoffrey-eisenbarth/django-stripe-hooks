import datetime as dt
from typing import Any

import pytest
import stripe

from django.conf import settings

from django_stripe_hooks.models import (
  Customer, ConfirmationToken, FundingInstructions,
)


@pytest.mark.django_db(transaction=True)
class TestSpecializedModels:

  @pytest.fixture(autouse=True)
  def setup_stripe(self) -> None:
    self.stripe_client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

  def test_confirmation_tokens(self) -> None:
    s_customer = self.stripe_client.v1.customers.create(params={
      'email': 'token@test.com',
      'name': 'ConfirmationToken Test',
    })
    d_customer = Customer.objects.from_stripe(s_customer)

    s_token = self.stripe_client.v1.test_helpers.confirmation_tokens.create(
      params={'payment_method': 'pm_card_visa'},
    )

    data: dict[str, Any] = {}
    for field_name in ['created', 'expires_at']:
      if value := getattr(s_token, field_name):
        data[field_name] = dt.datetime.fromtimestamp(value, dt.UTC)

    if (pm := s_token.payment_method_preview) is not None:
      data['payment_method_preview'] = dict(pm)
      if (card := pm.card) is not None:
        data.update({
          'card_brand': card.brand,
          'card_exp_month': card.exp_month,
          'card_exp_year': card.exp_year,
          'card_last4': card.last4,
        })
      if (billing_details := pm.billing_details) is not None:
        if (address := billing_details.address) is not None:
          data['zip_code'] = address.postal_code or ''

    ConfirmationToken.objects.create(customer=d_customer, **data)

  def test_funding_instructions_logic(self) -> None:
    s_customer = self.stripe_client.v1.customers.create(params={
      'email': 'funding@test.com',
      'name': 'FundingInstructions Test',
    })
    d_customer = Customer.objects.from_stripe(s_customer)

    s_fi = self.stripe_client.v1.customers.funding_instructions.create(
      s_customer.id,
      params={
        'funding_type': 'bank_transfer',
        'bank_transfer': {'type': 'us_bank_transfer'},
        'currency': 'usd',
      },
    )
    d_fi = FundingInstructions.from_stripe(d_customer, s_fi)

    assert d_fi.customer.id == d_customer.id
