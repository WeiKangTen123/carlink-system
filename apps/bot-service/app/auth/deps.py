"""FastAPI dependencies for the user AuthMiddleware already signed in."""
from fastapi import Depends, HTTPException, Request

from app.auth.core import CurrentUser


def current_user(request: Request) -> CurrentUser:
    user = getattr(request.state, "user", None)
    if user is None:
        # Only reachable on a path the middleware leaves public.
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can do this.")
    return user
