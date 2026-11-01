import json
from typing import Any

import django.contrib.admin.helpers as admin_helpers
import django.contrib.admin.templatetags.admin_list as admin_list
import django.contrib.admin.utils as admin_utils
from django.db import models
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .fields import MoneyField


MODULES_TO_PATCH = [admin_utils, admin_helpers, admin_list]


def address_format(address: dict[str, Any]) -> str:
  city = address.get('city')
  state = address.get('state')
  postal_code = address.get('postal_code')
  if city and state and postal_code:
    line3 = f"{city}, {state} {postal_code}"
  elif city and state:
    line3 = f"{city}, {state}"
  elif postal_code:
    line3 = postal_code
  else:
    line3 = None
  lines = '<br>'.join(filter(None, [
    address.get('line1'),
    address.get('line2'),
    line3,
    address.get('country'),
  ]))
  if lines:
    return format_html(
      '<address style="font-size: 0.8em;">{lines}</address>',
      lines=mark_safe(lines),
    )
  return mark_safe('-')


def json_format(value: Any) -> str:
  if isinstance(value, dict):
    return format_html(
      '<pre style="white-space:pre-wrap">{json}</pre>',
      json=json.dumps(value, indent=2, default=str),
    )
  if isinstance(value, list):
    return mark_safe(', '.join(str(v) for v in value))
  return mark_safe(str(value))


def patch() -> None:
  """Monkey-patch Django's display_for_field for custom fields."""

  original_display_for_field = admin_utils.display_for_field

  def display_for_field(
    value: Any,
    field: models.Field[Any, Any],
    *args: Any,
    **kwargs: Any,
  ) -> str:
    if isinstance(field, MoneyField):
      return str(value)
    if value and isinstance(field, models.DateTimeField):
      return date_format(value, 'SHORT_DATETIME_FORMAT')
    if field.name.endswith('address') and isinstance(value, dict):
      return address_format(value)
    if value and isinstance(field, models.JSONField):
      return json_format(value)
    if value and isinstance(field, models.DateField):
      return date_format(value, 'SHORT_DATE_FORMAT')
    return original_display_for_field(value, field, *args, **kwargs)

  for mod in MODULES_TO_PATCH:
    setattr(mod, "display_for_field", display_for_field)
