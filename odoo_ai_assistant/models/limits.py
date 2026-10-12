"""Shared limits and model policy for the AI assistant."""

import re
from datetime import timedelta

# Chat sizes, shared with the web client through bootstrap().
MAX_MESSAGE_CHARS = 12000
MAX_HISTORY_MESSAGES = 30
MAX_HISTORY_CHARS = 12000
MAX_LISTED_CONVERSATIONS = 100
MAX_LOADED_MESSAGES = 500
MAX_TITLE_PROMPTS = 20
WELCOME_SUGGESTIONS = 3

# pg advisory lock namespaces: replies within one conversation, native tool rate limit per user.
CONVERSATION_LOCK_NAMESPACE = 190019
NATIVE_RATE_LOCK_NAMESPACE = 190020

# Native MCP payload sizes (characters).
MAX_TOOL_ARGUMENT_CHARS = 200000
MAX_ATTACHMENT_TEXT_CHARS = 100000
MAX_SHARED_EVIDENCE_CHARS = 100000
MAX_REQUEST_DETAIL_CHARS = 120000
# Images shown on a tool request card or shared with the provider.
MAX_SHARED_IMAGES = 4
MAX_IMAGE_CHARS = 350000

# Earlier messages are shortened to this length when a request must shrink to fit the model's context.
COMPACT_MESSAGE_CHARS = 2000
# Messages kept at the first shortening level.
COMPACT_RECENT_MESSAGES = 5
# Characters of the latest message shown under a conversation in the chat list.
CONVERSATION_PREVIEW_CHARS = 160

# Tool calls in one provider answer, and follow-up rounds after schema discovery.
MAX_TOOL_CALLS = 5
MAX_DISCOVERY_ROUNDS = 3

# Charts drawn in the chat (Open in Odoo shows every group).
MAX_CHART_GROUPS = 50
MAX_CHART_TITLE_CHARS = 120

# App and screen discovery: names the AI may look up, screens and fields it receives.
MAX_LOOKUP_NAME_CHARS = 200
MAX_CONTEXT_SCREENS = 600
MAX_SCREEN_MATCHES = 8
MAX_SCREEN_FIELDS = 80

# Field names listed back to the AI after it used an unknown or unsuitable field.
MAX_FIELD_SUGGESTIONS = 80
MAX_FIELD_HINT_NAMES = 150
MAX_LINE_FIELD_EXAMPLES = 40

# Native tool requests: preview lifetime, staged-file lifetime and plan validation.
TOOL_REQUEST_LIFETIME = timedelta(minutes=30)
STAGED_FILE_LIFETIME = timedelta(days=1)
MAX_PLAN_DEPTH = 20
MAX_REFERENCE_TERMS = 20
MAX_SCHEMA_ERROR_CHARS = 300
MAX_AUDIT_TOOL_NAME_CHARS = 200
MAX_FILE_NAME_CHARS = 255
MAX_MIMETYPE_CHARS = 120

# Provider request defaults; administrators can override them in AI Settings.
DEFAULT_MAX_OUTPUT_TOKENS = 2048
DEFAULT_REQUEST_TIMEOUT = 60
PROVIDER_CONNECT_TIMEOUT = 10
# Allowed (minimum, maximum) values for the AI Settings above and the native tool rate limit.
MAX_OUTPUT_TOKENS_RANGE = (256, 65536)
REQUEST_TIMEOUT_RANGE = (10, 600)
NATIVE_RATE_LIMIT_RANGE = (1, 600)

# Field names AI tools never read, change, filter or group by.
CREDENTIAL_FIELD = re.compile(r"password|passwd|api.?key|token|secret|credential|private.?key", re.IGNORECASE)

# Technical, security and credential models never exposed to AI tools.
RESTRICTED_MODEL_PREFIXES = (
    "ir.", "mail.", "discuss.", "bus.", "mcp.", "odoo.ai.", "auth.", "payment.", "res.users.",
)
RESTRICTED_MODELS = frozenset({
    "res.users", "res.users.apikeys", "res.groups", "res.config", "res.config.settings",
})


def is_restricted_model(env, model, allowed=()):
    """Return True for models that AI tools must not read or change.

    `allowed` lists explicit exceptions (for example attachments and activities
    used by native MCP tools)."""
    if not isinstance(model, str) or model not in env or env[model]._transient:
        return True
    if model in allowed:
        return False
    return model in RESTRICTED_MODELS or model.startswith(RESTRICTED_MODEL_PREFIXES)
