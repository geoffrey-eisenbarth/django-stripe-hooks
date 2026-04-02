import threading

from django.db import connections
from django.http import HttpRequest, HttpResponse

from typing import Callable


class ResetDBConnectionsMiddleware:
  """
  Close any inherited DB connections at the start of each request thread.

  Python 3.13+ copies context variables into child threads, which causes
  Django's DatabaseWrapper objects to be inherited by the live_server's
  request threads. Multiple concurrent requests then share the same SQLite
  connection, causing savepoint conflicts.

  This middleware ensures each request thread starts with a fresh connection.
  For use in test settings only.
  """

  def __init__(
    self,
    get_response: Callable[[HttpRequest], HttpResponse],
  ) -> None:
      self.get_response = get_response

  def __call__(self, request: HttpRequest) -> HttpResponse:
    current_thread = threading.get_ident()
    for alias in connections:
      conn = connections[alias]
      # Skip connections that belong to this thread.
      if getattr(conn, '_thread_ident', current_thread) == current_thread:
        continue

      # This connection was created in a different thread (inherited via
      # pytest-django's shared connection handler). Close it so this request
      # thread opens a fresh SQLite connection of its own.
      #
      # Steps required to close safely across threads in Django 6:
      #   1. inc_thread_sharing() — allow_thread_sharing is a read-only
      #      property backed by a counter; close() calls
      #      validate_thread_sharing() internally.
      #   2. Clear savepoint_ids / needs_rollback — close() leaves these
      #      intact when in_atomic_block is True (pytest-django registers
      #      savepoints for test isolation), which would cause
      #      TransactionManagementError in subsequent atomic() calls.
      #   3. close() — closes the underlying DB connection.
      #   4. Restore _thread_ident — preserve the original owner's ident so
      #      Django's own close_request teardown (close_all) also passes
      #      validate_thread_sharing via allow_thread_sharing=True.
      original_ident = getattr(conn, '_thread_ident', current_thread)
      conn.inc_thread_sharing()
      conn.savepoint_ids = []
      conn.needs_rollback = False
      conn.close()
      setattr(conn, '_thread_ident', original_ident)
    return self.get_response(request)
