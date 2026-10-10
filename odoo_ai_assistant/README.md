# Odoo AI Assistant

An AI chat assistant for **Odoo 19 Community**. Users ask questions about Odoo and their own data
in a pop-up chat, in a full-screen app, or in Discuss. Administrators choose the AI provider and
control who can use it. They also see how much it is used.

The assistant reads Odoo data with the user's own permissions and keeps the results inside Odoo.
It can also prepare changes such as creating a lead, posting a note or scheduling an activity.
Nothing changes until the user clicks **Confirm**.

## Features

- **Providers:** OpenAI, Anthropic Claude, Google Gemini and OpenRouter. The model list is loaded
  live from the provider when you enter an API key.
- **Chat everywhere:** a magic-wand icon in the top bar opens a resizable chat. The **AI
  Assistant** app and the **AI conversations** section of Discuss show the full history.
- **Odoo-only scope:** every message passes a topic check first. Unrelated requests get a polite
  refusal.
- **Reads, tables and charts:** the AI picks the query; Odoo runs it and shows the result locally.
  Any model you enable in MCP Server can be charted (bar, line, pie, doughnut, scatter or a grouped
  table). **Open in Odoo** opens the same chart in Odoo's own graph and pivot views.
- **Confirmed actions:**
  - the AI can prepare creates, updates, deletions, messages, activities, attachment uploads and
    business methods;
  - each one is shown as a preview and runs only after the user confirms.
- **Usage dashboard:** tokens, API calls and active users per model, provider, user and request
  type.
- **Access control:** one company-wide API key; access is granted per user on their Access
  Rights tab.

## Requirements

- Odoo 19 Community.
- The **MCP Server** module (`mcp_server`), available from the
  [Odoo Apps store](https://apps.odoo.com/apps/modules/19.0/mcp_server). It is a separate,
  third-party module and is not included in this repository.
- The Python packages in `requirements.txt` (`markdown2`, `jsonschema`).
- An API key for one of the supported providers.

## Installation

1. Put `odoo_ai_assistant` and `mcp_server` in a folder on your `addons_path`.
2. Install the Python dependencies with the interpreter that runs Odoo:

   ```bash
   pip install -r odoo_ai_assistant/requirements.txt
   ```

3. Restart Odoo, update the apps list and install **Odoo AI Assistant**.

## Configuration

1. **Settings:** open **AI Assistant → Configuration → Settings**.
   - Enable the assistant.
   - Choose a provider, enter its API key and select a model.
   - Use **Refresh available models** to reload the list later.
2. **User access:** on each user's **Access Rights** tab, under **AI**, set **AI Assistant** to
   **Yes**. Access is off by default. Users with access can also confirm the changes the AI prepares,
   when **Let the AI prepare changes** is on and within their own Odoo access rights.
3. **Which data the AI may use:** under **Let the AI read Odoo data → Choose which Odoo data the AI
   may use**, enable each model in the MCP Server's model list, with the operations you allow (read,
   create, update, delete, method calls). Every model enabled for reading can also be charted.
4. **Changes (optional):** turn on **Let the AI prepare changes** to allow confirmed actions.

The **Advanced** section (developer mode) has these settings:
- sharing results with the provider;
- a rate limit per user (60 per minute by default);
- the maximum answer length (2,048 tokens by default);
- the provider timeout (60 seconds by default).

## How it works

1. **Topic check:** decides whether the message is about Odoo.
2. **Intent check:** decides whether the user asks to read data or to change it. Questions only
   get read tools; change requests only get the action tools.
3. **Tool calls:** the AI calls tools through the MCP Server module, in-process.
   - Every call runs as the current user, with their access rights, record rules and active
     companies.
   - It also needs the operation to be enabled for that model in MCP Server.
4. **Local results:** records, tables and charts are shown in Odoo and are **not** sent back to
   the provider. Later messages to the provider replace them with a note that a result was shown.
5. **Previews:**
   - **Names instead of IDs:** a change refers to records by name (for example
     `{"$odoo_ref": {"model": "res.partner", "domain": [["name", "ilike", "Acme"]]}}`). Odoo
     resolves that to exactly one record, so the AI never needs to know or guess record IDs.
   - **Confirm:** on Confirm, Odoo checks again that the records, permissions and tool definition
     are unchanged. Previews expire after 30 minutes.

If Odoo refuses a tool call, for example because of a field that doesn't exist, the reason is
sent back to the AI once so it can try another way. Otherwise the user sees the reason.

## Privacy and security

- **What reaches the provider:**
  - the messages users type;
  - model and field names (never record values);
  - menu and screen names, only if **Let the AI see menu and screen names** is on.
- **Sharing results:** Odoo results reach the provider only when an administrator enables sharing
  *and* the user clicks **Share and continue** on a specific result.
- **API key:** stored in the database, readable only by administrators and **not encrypted**.
  Protect your backups.
- **Private conversations:** only their owner can see them, and no other members can be added.
- **Credential fields:** fields that look like passwords, tokens or keys are always excluded.
  Technical models are excluded too, except attachments and activities.
- **Logs:** each provider request writes one INFO log line with request sizes only, never content.

## Usage

**AI Assistant → Reporting → Usage** shows totals and daily charts for 7, 30 or 90 days or all
time, with comparisons to the previous period. Token counts come from the provider's response;
check the provider's own billing page for costs. **Tool Requests** and **Tool Audit** list every
preview and tool run.

## Limitations

- Results depend on the chosen model's quality and its support for tool calling. Small or old
  models may fail more often.
- Charts in the chat show at most 50 groups (Open in Odoo shows all); record lists load page by page.
- Sharing is limited to 100,000 characters and four images per result.
- There is no live web lookup, so the assistant cannot verify information about other Odoo
  releases.

## Running the tests

```bash
odoo-bin -c odoo.conf -d test_db -i odoo_ai_assistant \
    --test-enable --test-tags /odoo_ai_assistant --stop-after-init --http-port 8079
```

All provider calls are mocked; no API key or network access is needed. Use a free
`--http-port` so the test run doesn't conflict with a running server.

## License

LGPL-3. See [LICENSE](LICENSE). The required `mcp_server` module has its own license.
