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
        connections.close_all()
        return self.get_response(request)
