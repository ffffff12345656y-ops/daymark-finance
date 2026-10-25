from functools import wraps

from flask import g, redirect, request, session, url_for


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return {"error": "กรุณาเข้าสู่ระบบก่อน", "loginUrl": url_for("login")}, 401
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        return view(*args, **kwargs)

    return wrapped


def current_user_id():
    return session["user_id"]


def load_current_user():
    return getattr(g, "current_user", None)
