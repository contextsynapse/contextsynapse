"""
Agent Platform — task dispatch, auto-capture, notifications, profiles.
"""
from .dispatcher import dispatch_goal, get_agent_profile, DEFAULT_PROFILES
from .auto_capture import capture_tool_call
from .notifications import notify_agents, get_pending_notifications
