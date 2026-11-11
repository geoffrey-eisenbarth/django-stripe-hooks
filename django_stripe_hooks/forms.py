from decimal import Decimal
from typing import Any

from django import forms

from django_stripe_hooks.money import Money


class MoneyFormField(forms.DecimalField):
  """A DecimalField that renders Money as a plain numeric value.

  Money.__str__ is presentational (e.g. ``"$9.50 USD"``), so a bound Money
  value would otherwise render into the widget as non-numeric text.
  """

  def prepare_value(self, value: Any) -> Any:
    if isinstance(value, Money):
      return Decimal(value)
    return value
