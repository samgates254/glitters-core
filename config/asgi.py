"""ASGI entrypoint.

Channels is not installed. This module is the process boundary HTTP ASGI
servers use, and the place a later Redis channel layer will be mounted.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

application = get_asgi_application()
