"""CV / CL Drafter dashboard. Run:  ./run.sh   (or  .venv/bin/streamlit run app.py)"""
from __future__ import annotations

import streamlit as st

from cvdrafter import config, memory
from ui.common import settings_sidebar

st.set_page_config(page_title="CV / CL Drafter", layout="wide", initial_sidebar_state="expanded")
memory.init_db()

connected = bool(config.backend())  # until an AI is connected, the app opens on Connect AI
nav = st.navigation({
    "": [
        st.Page("ui/chat.py", title="Apply", default=connected),
        st.Page("ui/home.py", title="Overview"),
        st.Page("ui/new_app.py", title="Detailed editor"),
        st.Page("ui/applications.py", title="Applications"),
        st.Page("ui/insights_page.py", title="Insights"),
    ],
    "Setup": [st.Page("ui/connect_page.py", title="Connect AI", default=not connected),
              st.Page("ui/files_page.py", title="Your profile")],
}, position="sidebar")  # the menu lives in the collapsible left sidebar
settings_sidebar()  # model settings live on the Apply page (bottom left)
nav.run()
