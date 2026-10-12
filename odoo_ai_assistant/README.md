# Odoo AI Assistant

An AI chat assistant built into **Odoo 19 Community**. People ask questions in plain language, about
Odoo itself or about their own business data, and the assistant answers inside Odoo. It can show
records, draw charts, read files and prepare changes to records. Nothing changes until the user
clicks **Confirm**.

- Works with **OpenAI, Anthropic Claude, Google Gemini and OpenRouter**, with any model that
  supports tool calling.
- Uses **each user's own Odoo permissions**. Nobody can see or change more through the assistant
  than they can in Odoo.
- **Record values stay in Odoo.** The AI provider receives what users type, never the records the
  assistant reads, unless an administrator allows sharing and the user approves it each time.

---

## Contents

**For users**
1. [What you can ask](#1-what-you-can-ask)
2. [Using the chat](#2-using-the-chat)

**For administrators**

3. [Requirements](#3-requirements)
4. [Installation](#4-installation)
5. [Configuration](#5-configuration)
6. [Costs and choosing a model](#6-costs-and-choosing-a-model)
7. [Privacy and security](#7-privacy-and-security)
8. [Monitoring usage](#8-monitoring-usage)
9. [Troubleshooting](#9-troubleshooting)
10. [Limitations](#10-limitations)

**For developers**

11. [How it works](#11-how-it-works)
12. [Project structure](#12-project-structure)
13. [Changing and extending the module](#13-changing-and-extending-the-module)

[License](#license)

---

# For users

## 1. What you can ask

| You type | What happens |
|---|---|
| *What can I do in Sales?* | Explains the Sales app using the real menus of your database. |
| *Find quotations for Acme Corp* | Shows the matching records in a table, with **Next page** for more. |
| *How many leads are in each stage?* | Counts the records and shows the result. |
| *Bar chart of CRM leads by stage* | Draws the chart in the chat. **Open in Odoo** opens the same chart in Odoo's graph and pivot views. |
| *What's in this file?* (with a file attached) | Reads the file: text and PDF/Office documents as text, images as images. |
| *Create a company contact Acme in Pune* | Shows a preview of the new contact. Nothing is saved until you click **Confirm**. |
| *Move the lead "Website redesign" to Qualified* | Finds the lead and the stage by name, then shows a preview to confirm. |
| *Schedule a call on that lead next Monday* | Previews the activity, then creates it when you confirm. |

The assistant only helps with Odoo. Unrelated questions (general knowledge, jokes and so on) get a
polite refusal. Greetings get a short, friendly reply. Requests work in any language.

## 2. Using the chat

**Opening the chat**
- Click the **magic-wand icon** in the top bar to open the chat from any screen. You can drag its
  corner to resize it, minimise it, or expand it into Discuss.
- The **AI Assistant** app, and the **AI conversations** section in Discuss, show all your
  conversations.
- If you open the chat while viewing a record, the new conversation is named after that record.

**Previews, Confirm and Cancel**

When you ask for a change, the assistant shows a **preview** of exactly what it will do.
- **Confirm** applies it, using your own Odoo access rights.
- **Cancel** discards it. Nothing is changed.
- A preview expires after 30 minutes. If the record changed in the meantime, Odoo refuses to apply
  it and asks you to request a fresh preview.

**Results stay in Odoo**

Tables, charts and file contents are shown to you inside Odoo. The AI provider does not see them.
If your administrator allows it, a **Share and continue** button lets you send one specific result
to the AI so it can analyse or summarise it. You approve each share.

**Attaching a file**

Click the **paperclip** and choose a file. The file is kept in your conversation only and isn't
attached to any record. Then ask about it ("What's in this file?"), or ask the assistant to attach it
to a record, which needs your confirmation. Staged files are deleted after one day.

**Voice input**

Click the **microphone**, or press **Alt + V**. Your browser turns speech into text, which you can
edit before sending. This needs a browser that supports speech recognition, and Odoo opened over
HTTPS or on localhost.

---

# For administrators

## 3. Requirements

| Requirement | Notes |
|---|---|
| **Odoo 19 Community** | |
| **MCP Server** (`mcp_server`) | Free on the [Odoo Apps store](https://apps.odoo.com/apps/modules/19.0/mcp_server). It's a separate third-party module and **is not included** in this repository. |
| **Python packages** `markdown2`, `jsonschema` | Listed in `requirements.txt`. |
| **Python package** `pdfminer.six` *(optional)* | Needed to read text from PDF files. Odoo's official installers and Odoo.sh already include it; source installs usually don't. Without it, everything else works, but PDF text can't be shown. |
| **An API key** | For one of the supported AI providers. A paid plan is recommended; see [Costs](#6-costs-and-choosing-a-model). |

## 4. Installation

1. Copy the `odoo_ai_assistant` and `mcp_server` folders into a folder on your Odoo `addons_path`.
2. Install the Python packages, using the same Python environment that runs Odoo:

   ```bash
   pip install -r odoo_ai_assistant/requirements.txt
   ```

3. Restart the Odoo server.
4. In Odoo, open **Apps**, click **Update Apps List**, search for **Odoo AI Assistant** and click
   **Activate**.
   **MCP Server** and Odoo's **Attachments List and Document Indexation** module are installed
   automatically as dependencies.

**Upgrading to a new version:** replace the folder, restart Odoo, then upgrade the module from
**Apps** (or run `odoo-bin -u odoo_ai_assistant -d <database>`). Ask users to reload the page with
**Ctrl + Shift + R**.

## 5. Configuration

Open **AI Assistant → Configuration → Settings**. Only administrators can see this page.

| Step | Setting | What to do | Default |
|---|---|---|---|
| 1 | **Enable AI Assistant** | Turn the assistant on for the database. | Off |
| 2 | **AI provider**, **API Key**, **AI Model** | Choose a provider and paste its API key. The list of models loads from the provider; choose one, then **Save**. Use **Refresh available models** if the list is out of date. | OpenAI |
| 3 | **User access** | For each user: **Settings → Users →** *user* **→ Access Rights → AI → AI Assistant = Yes**. | No (for every user) |
| 4 | **Let the AI read Odoo data** | Lets the assistant search, count and chart records. Then click **Choose which Odoo data the AI may use** (see below). | On |
| 5 | **Let the AI prepare changes** | Lets the assistant prepare new or changed records, which users then confirm. **Also needed for attaching files in the chat.** | Off |
| 6 | **Let the AI see menu and screen names** | Lets the assistant explain apps, menus and screens. Menu names and field labels are sent to the provider, never record values. | Off |

**Choosing which data the AI may use**

**Choose which Odoo data the AI may use** opens MCP Server's list of models. It's also under
**Settings → Technical → MCP → MCP Available Models**. Add each model the assistant may work with,
and tick what it may do:

| Option | Allows |
|---|---|
| **Read** | Search, count, chart and read records of this model. |
| **Create**, **Update**, **Delete** | Prepare these changes (also needs setting 5). |
| **Method calls** | Run business actions such as *Archive* or *Confirm*. Off by default. Data-access and internal methods are always blocked. |

Each model's permissions stay limited to what the user can already do in Odoo. MCP Server's own
**external** connection (for outside AI apps) is a separate switch and can stay off. The assistant
doesn't need it.

**Advanced settings** are shown in developer mode:

| Setting | Purpose | Default | Allowed |
|---|---|---|---|
| **Allow sharing results with the AI provider** | Shows **Share and continue** on results, so users can send one approved result to the AI. | Off | |
| **AI data requests per user per minute** | Protects Odoo from runaway requests. | 60 | 1 – 600 |
| **Maximum answer length (tokens)** | Upper limit for each AI answer. | 2,048 | 256 – 65,536 |
| **Provider timeout (seconds)** | How long to wait for the provider. | 60 | 10 – 600 |

## 6. Costs and choosing a model

Choose a current model that supports **tool calling**. Small or older models misunderstand requests
more often.

**What one chat message costs**

| Provider call | When |
|---|---|
| Topic check (*is this about Odoo?*) | Most messages. It's skipped for greetings, for clear requests that name an app or menu, and for questions about an attached file. |
| Business action check (*read, chart or change?*) | Every message, when **Let the AI read Odoo data** is on. |
| Chat response | Every message. |
| Conversation title | Until a new conversation has a title, usually only its first message. |

The chat response is the largest call. Each one includes the assistant's instructions and the
descriptions of its tools, so in our tests it used about **10,000 input tokens**, plus a short
answer. Input tokens are the cheap kind, so on a paid plan a message usually costs a fraction of a
cent. Starting a **new conversation for each new task** keeps requests smaller, because earlier
messages are sent along with each new one.

> **Free plans run out quickly.** Some free plans allow only a few dozen requests a day, which is a
> handful of chat messages. When the limit is reached, the chat shows *"rate limit or usage quota
> was reached"*. Use a paid plan, with a budget alert at the provider, for everyday use.

## 7. Privacy and security

| Topic | What happens |
|---|---|
| **Sent to the AI provider** | The messages users type; the names of the models and fields the AI may use; menu and screen names if setting 6 is on. |
| **Never sent** | Record values, file contents and chart data, unless sharing is allowed in Advanced settings *and* the user clicks **Share and continue** on that result. |
| **Permissions** | Every read and change runs as the current user, with their access rights, record rules and active companies, and only for models and operations enabled in the MCP model list. |
| **Changes** | Always previewed. On **Confirm**, Odoo checks again that the records, permissions and tool are unchanged before applying. |
| **Always excluded** | Password, token, key and other credential fields, and technical or security models (users, groups, settings and similar). |
| **Conversations** | Private to their owner. No other people can be added. |
| **API key** | Stored in the database, readable only by administrators, and **not encrypted**. Protect database backups. |
| **Server logs** | Record request sizes only, never message content. |

Tell users not to type confidential information into the chat, because typed messages go to the
AI provider.

## 8. Monitoring usage

| Where | What it shows |
|---|---|
| **AI Assistant → Reporting → Usage** | Tokens, API calls, failed calls and active users for 7, 30 or 90 days or all time, with a daily chart and breakdowns by model, provider, user and request type. |
| **Detailed request log** (button on the Usage page) | One line per provider request: tokens, status, duration and HTTP status. |
| **AI Assistant → Reporting → Tool Requests** | Every preview, confirmation and data request, with its status. |
| **AI Assistant → Reporting → Tool Audit** | Every tool run, including failures and rate-limit refusals. |

Token counts come from the provider's responses. For actual charges, see the provider's billing
page.

## 9. Troubleshooting

| Message or symptom | Cause and fix |
|---|---|
| **The wand icon doesn't appear** | Check that the assistant is enabled (step 1) and that the user is set to **Yes** (step 3). Reload the page. |
| **"Select an available model in AI Settings first."** | No model is chosen, or it's no longer offered. Choose one in Settings (step 2) and save. |
| **HTTP 401 / 403** | The provider rejected the API key. Check it in Settings. |
| **HTTP 404 … model not found** | Your key can't use that model; some need a verified account or a higher usage tier. Choose another model. |
| **HTTP 429 … rate limit or usage quota** | Your provider plan's limit was reached. Wait a minute; if it continues, the daily quota is used up. See [Costs](#6-costs-and-choosing-a-model). |
| **"Native MCP rate limit reached"** | The user made more than the allowed AI data requests in one minute (Advanced settings). Wait a minute. |
| **"I can only assist with Odoo …"** for an Odoo question | Mention the app or menu name, for example "in CRM". |
| **"… is not available for charts"** | That model isn't enabled for reading. Enable **Read** for it (step 4). |
| **"Enable confirmed business actions before staging files."** | Attaching files needs **Let the AI prepare changes** (step 5). |
| **"No inline text could be extracted …"** for a PDF | `pdfminer.six` isn't installed on the server, or the PDF is a scanned image with no text. Install the package and restart Odoo; PDFs attached earlier are then read too. |
| **A page error after upgrading** | The browser is using old files. Reload with **Ctrl + Shift + R**. |

## 10. Limitations

- **Answer quality depends on the model.** Small or older models misunderstand requests more often.
- **Charts in the chat show up to 50 groups.** **Open in Odoo** shows all of them.
- **Money is only added up within one currency.** Filter to one currency first.
- **Shared results** can be up to 100,000 characters and four images.
- **Images** larger than 256 KB aren't shown inline.
- **Scanned PDFs** (images of text) can't be read as text.
- **No web lookup.** The assistant can't check information on the internet.

---

# For developers

## 11. How it works

**A chat message**

```
User message (systray chat, AI Assistant app or Discuss)
  │
  ├─ 1. Topic check ............ Is this about Odoo?                    → polite refusal if not
  ├─ 2. Business action check .. ACTION, CHART or READ?                 → decides which tools are offered
  ├─ 3. Chat response .......... The AI answers or picks a tool         (CHART forces the chart tool)
  ├─ 4. Odoo runs the tool ..... As the current user, through MCP Server, in the same process
  └─ 5. Result in the chat ..... Table, chart, file content or a preview with Confirm / Cancel
```

- **Reads** (search, count, read a file) run straight away. The result is stored locally and shown
  only in Odoo. Later messages to the provider contain a note that a result was shown, not the data.
- **Changes** are prepared as a *tool request*, which holds the arguments and a snapshot of the
  target records. On **Confirm**, Odoo checks that the request hasn't expired, the company selection
  is the same, the tool definition is unchanged and the records haven't changed. Only then does it
  run the tool.
- **Records by name.** The AI never sees record IDs. For changes, it refers to records with a local
  reference such as `{"$odoo_ref": {"model": "res.partner", "domain": [["name", "=", "Acme"]]}}`.
  Odoo resolves this to exactly one record the user can access, and refuses ambiguous names instead
  of guessing.
- **Self-correction.** If Odoo refuses a tool call (for example an invented field name), the reason
  is sent back to the AI once so it can try another way. Otherwise the user sees the reason.
- **Providers.** Each provider's API is called in its own request format. Older OpenAI models fall
  back to the Chat Completions API automatically. Requests that exceed a model's context window are
  shortened in steps and retried.

## 12. Project structure

```
odoo_ai_assistant/
├── models/
│   ├── conversation.py   Chat flow: topic and intent checks, tool dispatch, history, staged files
│   ├── providers.py      OpenAI / Claude / Gemini / OpenRouter clients, system prompts, classifiers
│   ├── native_mcp.py     MCP Server tools: catalogue, previews, Confirm, safety checks, audit
│   ├── tools.py          Chart tool and the app / screen / version tools
│   ├── navigation.py     Apps, menus and screens available to the current user
│   ├── mcp_bridge.py     Read-only grouping used by charts
│   ├── configuration.py  AI Settings, model catalogue, per-user Yes/No access
│   ├── usage.py          Usage log and dashboard data
│   ├── discuss.py        Keeps AI conversations private inside Discuss
│   ├── formatting.py     Safe Markdown rendering of AI answers
│   └── limits.py         All size limits, lifetimes, protected models and the credential-field filter
├── static/src/           Chat, Discuss integration, charts and the Usage dashboard (OWL)
├── views/                Settings, menus and the reporting screens
├── security/             The AI Assistant Yes/No access group, access rights and record rules
└── data/                 Default settings and the "Odoo AI" chat partner
```

## 13. Changing and extending the module

| To change | Look in |
|---|---|
| A size limit, lifetime or cap | `models/limits.py`. Every limit is defined there, once. |
| Which models or fields are always protected | `RESTRICTED_MODELS`, `RESTRICTED_MODEL_PREFIXES` and `CREDENTIAL_FIELD` in `models/limits.py`. |
| The assistant's instructions | `_reply_once()` in `models/providers.py` (main instructions), and `is_odoo_topic()` / `request_intent()` for the two classifiers. |
| Which MCP tools the assistant may use | `_BUILTINS` in `models/native_mcp.py`. Tools that change data automatically require Confirm. |
| How a tool request is displayed in the chat | `NativeToolRequest._payload()` in `models/native_mcp.py`, and `static/src/visualization.xml`. |
| Adding an AI provider | `PROVIDERS` and `BASE_URLS`, then the request and response handling in `ProviderClient._reply_once()`, `_tokens()` and `models()` in `models/providers.py`. |

**Development workflow**

```bash
# Upgrade after changing Python, XML or data files
odoo-bin -c odoo.conf -d <database> -u odoo_ai_assistant --stop-after-init
```

- Restart the server after changing Python code. Reload the browser with **Ctrl + Shift + R** after
  changing JavaScript or templates.
- Server logs show request sizes and timings for every provider call, never message content.
- **There's no automated test suite yet.** Test changes in a development database. Use the
  **Tool Requests** and **Tool Audit** reports to check what ran.
- The staged-file reader extends MCP Server's attachment reader through some of its internal
  methods (`_resolve_read_attachment_target`, `_load_target_bytes`). After upgrading MCP Server,
  check that reading attached files still works.

---

## License

LGPL-3. See [LICENSE](LICENSE). The required `mcp_server` module is distributed separately under its
own license.
