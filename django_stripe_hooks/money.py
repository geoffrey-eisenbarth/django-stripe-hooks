from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Currency:
  code: str
  name: Any          # gettext_lazy string
  symbol: str
  divisor: int
  symbol_suffix: bool = False


_CURRENCY_LIST: list[Currency] = [
  Currency('usd', _("US Dollar"),          '$',   100),
  Currency('cad', _("Canadian Dollar"),    '$',   100),
  Currency('aud', _("Australian Dollar"),  '$',   100),
  Currency('nzd', _("New Zealand Dollar"), '$',   100),
  Currency('sgd', _("Singapore Dollar"),   '$',   100),
  Currency('hkd', _("Hong Kong Dollar"),   '$',   100),
  Currency('eur', _("Euro"),               '€',   100),
  Currency('gbp', _("British Pound"),      '£',   100),
  Currency('chf', _("Swiss Franc"),        'Fr',  100),
  Currency('sek', _("Swedish Krona"),      'kr',  100, symbol_suffix=True),
  Currency('nok', _("Norwegian Krone"),    'kr',  100, symbol_suffix=True),
  Currency('dkk', _("Danish Krone"),       'kr',  100, symbol_suffix=True),
  Currency('clp', _("Chilean Peso"),       '$',   1),
  Currency('jpy', _("Japanese Yen"),       '¥',   1),
  Currency('krw', _("South Korean Won"),   '₩',   1),
  Currency('twd', _("New Taiwan Dollar"),  'NT$', 1),
  Currency('huf', _("Hungarian Forint"),   'Ft',  1,   symbol_suffix=True),
  Currency('isk', _("Icelandic Krona"),    'kr',  1,   symbol_suffix=True),
]

# Derive module-level dicts/tuples for use in Django field definitions.
CURRENCY_MAP: dict[str, Currency] = {
  c.code: c
  for c in _CURRENCY_LIST
}
CURRENCY_CHOICES = (('', _("N/A")),) + tuple(
  (c.code, c.name)
  for c in _CURRENCY_LIST
)
CURRENCY_SYMBOL: dict[str, str] = {
  c.code: c.symbol
  for c in _CURRENCY_LIST
}
CURRENCY_DIVISOR: dict[str, int] = {'': 1, **{
  c.code: c.divisor
  for c in _CURRENCY_LIST
}}

# Currencies sharing a symbol with another; ISO code must be shown.
_symbol_counts: dict[str, int] = {}
for _c in _CURRENCY_LIST:
  _symbol_counts[_c.symbol] = _symbol_counts.get(_c.symbol, 0) + 1
CURRENCY_CODE_REQUIRED: frozenset[str] = frozenset(
  c.code for c in _CURRENCY_LIST if _symbol_counts[c.symbol] > 1
)
del _symbol_counts, _c


class Money(Decimal):
  """A Decimal subclass that carries currency context."""

  currency: str
  is_balance: bool

  def __new__(
    cls,
    value: Decimal | int | str = 0,
    currency: str = '',
    is_balance: bool = False,
  ) -> 'Money':
    obj = super().__new__(cls, value)
    obj.currency = currency
    obj.is_balance = is_balance
    return obj

  def __str__(self) -> str:
    if currency := CURRENCY_MAP.get(self.currency):
      symbol = currency.symbol
      suffix = currency.symbol_suffix
    else:
      symbol = self.currency.upper()
      suffix = False

    if self.currency in CURRENCY_CODE_REQUIRED:
      code = f' {self.currency.upper()}'
    else:
      code = ''

    amount = abs(self)
    if self < 0:
      s = f'-{amount}{symbol}{code}' if suffix else f'-{symbol}{amount}{code}'
    elif self.is_balance and self > 0:
      s = f'+{amount}{symbol}{code}' if suffix else f'+{symbol}{amount}{code}'
    else:
      s = f'{amount}{symbol}{code}' if suffix else f'{symbol}{amount}{code}'
    return s.strip()

  def __repr__(self) -> str:
    return f'Money({Decimal.__str__(self)!r}, {self.currency!r})'

  def _check_currency(self, other: object) -> None:
    if isinstance(other, Money) and other.currency != self.currency:
      raise TypeError(
        f"Cannot combine Money with different currencies: "
        f"{self.currency!r} and {other.currency!r}"
      )

  def __add__(self, other: object) -> 'Money':
    self._check_currency(other)
    result = Decimal.__add__(self, other)  # type: ignore[operator]
    if result is NotImplemented:
      return NotImplemented
    return Money(result, self.currency, self.is_balance)

  def __radd__(self, other: object) -> 'Money':
    self._check_currency(other)
    result = Decimal.__radd__(self, other)  # type: ignore[operator]
    if result is NotImplemented:
      return NotImplemented
    return Money(result, self.currency, self.is_balance)

  def __sub__(self, other: object) -> 'Money':
    self._check_currency(other)
    result = Decimal.__sub__(self, other)  # type: ignore[operator]
    if result is NotImplemented:
      return NotImplemented
    return Money(result, self.currency, self.is_balance)

  def __rsub__(self, other: object) -> 'Money':
    self._check_currency(other)
    result = Decimal.__rsub__(self, other)  # type: ignore[operator]
    if result is NotImplemented:
      return NotImplemented
    return Money(result, self.currency, self.is_balance)

  def __mul__(self, other: object) -> 'Money':
    result = Decimal.__mul__(self, other)  # type: ignore[operator]
    if result is NotImplemented:
      return NotImplemented
    return Money(result, self.currency, self.is_balance)

  def __rmul__(self, other: object) -> 'Money':
    result = Decimal.__rmul__(self, other)  # type: ignore[operator]
    if result is NotImplemented:
      return NotImplemented
    return Money(result, self.currency, self.is_balance)

  def __truediv__(self, other: object) -> 'Money':
    result = Decimal.__truediv__(self, other)  # type: ignore[operator]
    if result is NotImplemented:
      return NotImplemented
    return Money(result, self.currency, self.is_balance)

  def __neg__(self) -> 'Money':
    return Money(Decimal.__neg__(self), self.currency, self.is_balance)

  def __abs__(self) -> 'Money':
    return Money(Decimal.__abs__(self), self.currency, self.is_balance)
