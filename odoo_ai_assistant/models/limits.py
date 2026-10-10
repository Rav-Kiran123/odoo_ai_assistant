"""Shared limits and model policy for the AI assistant."""

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

# Provider request defaults; administrators can override them in AI Settings.
DEFAULT_MAX_OUTPUT_TOKENS = 2048
DEFAULT_REQUEST_TIMEOUT = 60
PROVIDER_CONNECT_TIMEOUT = 10

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
