"""Account status changes."""

from __future__ import annotations

from apps.identity.domain import transition_allowed
from apps.identity.errors import IdentityError


def change_status(user, new_status: str, *, force: bool = False):
    if not transition_allowed(user.status, new_status, force=force):
        raise IdentityError("This account status cannot change that way.", status_code=409)
    user.status = new_status
    user.save()
    return user
