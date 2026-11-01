from decimal import Decimal
from operator import attrgetter
from typing import Any

from django.db import models

from django_stripe_hooks.money import Money


class MoneyDescriptor:
  """Descriptor that wraps a raw Decimal DB value in a Money instance."""

  def __init__(self, field: 'MoneyField') -> None:
    self.field = field

  def __get__(self, obj: Any, objtype: type | None = None) -> Money | None:
    if obj is None:
      return self  # type: ignore[return-value]
    value = obj.__dict__.get(self.field.attname)
    if value is None:
      return None
    currency_getter = attrgetter(self.field.currency_field.replace('__', '.'))
    try:
      currency: str = currency_getter(obj) or ''
    except AttributeError:
      currency = ''
    return Money(value, currency, self.field.is_balance)

  def __set__(self, obj: Any, value: Any) -> None:
    if isinstance(value, Money):
      obj.__dict__[self.field.attname] = Decimal(value)
    else:
      obj.__dict__[self.field.attname] = value


class MoneyField(models.DecimalField):  # type: ignore[type-arg]
  """A DecimalField whose Python representation is a Money instance.

  Parameters
  ----------
  currency_field:
      Name of the sibling field (or FK traversal using ``__``) that holds
      the ISO 4217 currency code, e.g. ``'currency'`` or ``'price__currency'``.
  is_balance:
      When ``True``, positive values are displayed with a ``+`` prefix.
  """

  def __init__(
    self,
    *args: Any,
    currency_field: str = 'currency',
    is_balance: bool = False,
    **kwargs: Any,
  ) -> None:
    self.currency_field = currency_field
    self.is_balance = is_balance
    super().__init__(*args, **kwargs)

  def contribute_to_class(
    self,
    cls: type[models.Model],
    name: str,
    private_only: bool = False,
  ) -> None:
    super().contribute_to_class(cls, name, private_only=private_only)
    setattr(cls, name, MoneyDescriptor(self))

  def deconstruct(self) -> tuple[str, str, list[Any], dict[str, Any]]:
    name, path, args, kwargs = super().deconstruct()
    if self.currency_field != 'currency':
      kwargs['currency_field'] = self.currency_field
    if self.is_balance:
      kwargs['is_balance'] = self.is_balance
    return name, path, list(args), kwargs
