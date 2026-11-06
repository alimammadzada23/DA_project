"""Small offline Streamlit component; routing remains in Python session state."""
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

command_hub = components.declare_component('insightpilot_command_hub', path=str(Path(__file__).parent/'command_hub'))


def wheel(active, pages, *, presentation='home'):
    event = command_hub(active=active, presentation=presentation,
                        expanded=st.session_state.get('_hub-expanded',False),
                        key='command-hub', default=None)
    if not isinstance(event,dict):
        return
    sequence=event.get('sequence')
    if sequence is None or sequence==st.session_state.get('_hub-sequence'):
        return
    if event.get('action')=='toggle':
        st.session_state['_hub-sequence']=sequence
        st.session_state['_hub-expanded']=not st.session_state.get('_hub-expanded',False)
        st.rerun()
    if event.get('page') not in pages:
        return
    # Consume values once so ordinary reruns and native navigation cannot replay
    # a previous click. Expansion changes presentation, never analytical state.
    st.session_state['_hub-sequence']=sequence
    if event['page']!=active:
        st.session_state['_hub-expanded']=False
        st.session_state['pending-view-layout']={'page':event['page'],'presets':{}}
        st.rerun()
