"""Single Streamlit entry. Compatibility services have no competing UI routes."""
import streamlit as st
from .presentation import SHELL
from .universal.ui import main as unified_main


def main():
    st.set_page_config(page_title='InsightPilot',page_icon='◈',layout='wide',initial_sidebar_state='expanded')
    st.markdown(SHELL,unsafe_allow_html=True)
    st.sidebar.markdown('<div class="brand"><span class="brand-mark">IP</span><span class="brand-name">InsightPilot</span></div>',unsafe_allow_html=True)
    st.sidebar.caption('From data to defensible decisions')
    unified_main()
