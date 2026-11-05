"""Small offline Streamlit component; all routing remains in Python session state."""
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

command_hub = components.declare_component('insightpilot_command_hub', path=str(Path(__file__).parent/'command_hub'))


def wheel(active, pages):
    event = command_hub(active=active, key='command-hub', default=None)
    if not isinstance(event, dict) or event.get('page') not in pages:
        return
    # Component values survive reruns. Consume an event once, so native navigation
    # and quick actions are never overridden by an old wheel click.
    sequence = event.get('sequence')
    if sequence is None or sequence == st.session_state.get('_hub-sequence'):
        return
    st.session_state['_hub-sequence'] = sequence
    if event['page'] != active:
        st.session_state['pending-view-layout'] = {'page':event['page'], 'presets':{}}
        st.rerun()
