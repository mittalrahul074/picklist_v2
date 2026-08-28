import streamlit as st
from streamlit_cookies_controller import CookieController
from database import get_pass


# -------------------------------------------------
# Cookie Manager
# -------------------------------------------------
def get_cookie_manager():

    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = CookieController()

    return st.session_state.cookie_manager


# -------------------------------------------------
# Authentication
# -------------------------------------------------
def authenticate_user(username, password):
    return get_pass(username) == password


# -------------------------------------------------
# Cookie helpers
# -------------------------------------------------
def set_cookie(name: str, value: str) -> bool:
    manager = get_cookie_manager()
    if not manager:
        return False

    manager[name] = value
    manager.save()
    return True


def get_cookie(name: str):
    manager = get_cookie_manager()
    if not manager:
        return None
    return manager.get(name)


def clear_cookie(name: str) -> None:
    manager = get_cookie_manager()
    if not manager:
        return

    manager[name] = ""
    manager.save()


# -------------------------------------------------
# Logout
# -------------------------------------------------
def logout_user():
    clear_cookie("logged_user")

    st.session_state.update({
        "authenticated": False,
        "user_role": None,
        "user_type": None,
        "party_filter": None,
        "page": "dashboard"
    })
    
    #delete all session state reated to anything
    for key in list(st.session_state.keys()):
        del st.session_state[key]

