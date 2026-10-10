# Odoo AI Assistant

An AI chat assistant built into **Odoo 19 Community**. Users ask questions in plain language about
Odoo and their own business data. The assistant answers, draws charts and prepares record changes,
which the user confirms with a button.

- Works with **OpenAI, Anthropic Claude, Google Gemini and OpenRouter**, with any model that
  supports tool calling.
- Reads data with the **user's own Odoo permissions**. Record values stay inside Odoo and are
  not sent to the AI provider.
- **Nothing changes in Odoo without a preview and a click on Confirm.**

---

## Contents

1. [What it can do](#what-it-can-do)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Using the assistant](#using-the-assistant)
6. [How it works](#how-it-works)
7. [Privacy and security](#privacy-and-security)
8. [Usage reporting](#usage-reporting)
9. [Project structure](#project-structure)
10. [Troubleshooting](#troubleshooting)
11. [Limitations](#limitations)
12. [License](#license)

---

## What it can do

| Area | What the user gets |
|---|---|
| **Chat everywhere** | A magic-wand icon in the top bar opens a resizable chat from any screen. The **AI Assistant** app and the **AI conversations** section in Discuss show the full history. |
| **Answers about Odoo** | Explains apps, menus and screens using the real menus of *your* database, including custom apps. |
| **Data questions** | "Show me open quotations for Acme", "How many leads are in Proposition?" The results are shown as tables in Odoo. |
| **Charts** | Bar, line, pie, doughnut, scatter or grouped tables of **any model enabled for the AI**. **Open in Odoo** opens the same chart in Odoo's own graph and pivot views. |
| **Confirmed actions** | Create, update or delete records, post notes, schedule activities, upload attachments and run business methods. Each one is previewed first and runs only after **Confirm**. |
| **Odoo-only scope** | Unrelated requests (general knowledge, jokes and so on) get a polite refusal. Greetings and small talk get a short friendly reply. |
| **Admin control** | One company-wide API key, per-user access (Yes/No), a choice of which data the AI may use, and a usage dashboard. |

---

## Requirements

- **Odoo 19 Community.**
- **MCP Server** (`mcp_server`): free on the
  [Odoo Apps store](https://apps.odoo.com/apps/modules/19.0/mcp_server). It's a separate
  third-party module and is **not included** in this repository.
- **Python packages:** `markdown2` and `jsonschema` (see `requirements.txt`).
- **An API key** for one of the supported AI providers.

---

## Installation

1. Copy `odoo_ai_assistant` and `mcp_server` into a folder on your Odoo `addons_path`.
2. Install the Python packages with the same Python that runs Odoo:

   ```bash
   pip install -r odoo_ai_assistant/requirements.txt
   ```

3. Restart Odoo, open **Apps**, click **Update Apps List**, then install **Odoo AI Assistant**.
   MCP Server is installed automatically as a dependency.

---

## Configuration

Everything is under **AI Assistant → Configuration → Settings**.

| Step | Where | What to do |
|---|---|---|
| 1. Turn it on | **Enable AI Assistant** | Tick the box. |
| 2. Choose the AI | **AI provider**, **API Key**, **AI Model** | Pick a provider and paste its API key. The model list loads from the provider; pick a model and **Save**. |
| 3. Give users access | **Settings → Users →** *(a user)* **→ Access Rights → AI → AI Assistant** | Set it to **Yes**. Access is off by default. |
| 4. Choose the data | **Let the AI read Odoo data → Choose which Odoo data the AI may use** (also under **Settings → Technical → MCP → MCP Available Models**) | Add each model the AI may use and tick the allowed operations (Read, Create, Update, Delete, Method calls). Every model enabled for reading can be searched and charted. |
| 5. Allow changes (optional) | **Let the AI prepare changes** | Lets users with access create or change records after previewing and confirming. |
| 6. Screen help (optional) | **Let the AI see menu and screen names** | Lets the AI explain apps and screens. Only menu and field *names* are shared, never record values. |

**Advanced settings** (visible in developer mode):
- sharing results with the provider (off by default);
- requests per user per minute (60);
- maximum answer length (2,048 tokens);
- provider timeout (60 seconds).

> **Tip:** use a model that supports tool calling, such as `gpt-4o`, `gpt-4o-mini`, Claude Sonnet
> or Gemini 2.5 Flash. Some models in a provider's list may not be available to your account; see
> [Troubleshooting](#troubleshooting).

---

## Using the assistant

Open the chat from the **magic-wand icon** in the top bar, or from the **AI Assistant** app.

Example requests:

| You type | What happens |
|---|---|
| *What can I do in Sales?* | Explains the Sales app using its real menus. |
| *Bar chart of CRM leads by stage* | Draws the chart in the chat, with **Open in Odoo**. |
| *Pie chart of invoices by payment status* | Groups invoices by status (money is only added up within one currency). |
| *Find quotations for Acme Corp* | Shows matching records in a table, with **Next page** for more. |
| *Create a company contact Acme in Pune* | Shows a preview; nothing is saved until **Confirm**. |
| *Move the lead "Website redesign" to Qualified* | Finds the lead and stage by name, then shows a preview to confirm. |
| *Schedule a call on that lead next Monday* | Previews the activity, then creates it on **Confirm**. |

The welcome screen suggests the apps where the AI can do the most, based on the data enabled for
it in step 4.

---

## How it works

```
User message
   │
   ├─ 1. Topic check ......... Is this about Odoo? (otherwise a polite refusal)
   ├─ 2. Intent check ........ Read data or change data?
   ├─ 3. AI picks a tool ..... chart, search, read, create, update, ...
   ├─ 4. Odoo runs the tool .. as the current user, through MCP Server, in-process
   └─ 5. Result in the chat .. table / chart / preview with Confirm
```

- **Permissions:** every tool runs with the user's own access rights, record rules and active
  companies, and only for models and operations enabled in MCP Server.
- **Local results:** tables and charts are shown in Odoo only. Later messages to the provider
  contain a note that a result was shown, never the data itself.
- **Names, not IDs:** the AI refers to records by name, and Odoo resolves each name to exactly one
  record. Ambiguous names are refused instead of guessed.
- **Safe confirmations:** on **Confirm**, Odoo re-checks that the records, permissions and tool are
  unchanged. Previews expire after 30 minutes.
- **Self-correction:** if Odoo refuses a tool call (for example a field that doesn't exist), the
  reason goes back to the AI once so it can try another way. Otherwise the user sees the reason.
- **Provider differences:**
  - Each provider's API is called in its own format.
  - Older OpenAI models fall back to the Chat Completions API automatically.
  - Requests that are too large for a model's context window are shortened and retried.

---

## Privacy and security

- **What reaches the AI provider:** the messages users type, plus model and field *names*, and menu
  names only if setting 6 is on. Record values are never sent.
- **Sharing results** with the provider happens only if an administrator enables it *and* the user
  clicks **Share and continue** on a specific result.
- **Sensitive fields** (passwords, tokens, keys) and technical or security models (users, groups,
  settings) are always excluded.
- **Conversations are private** to their owner; no other members can be added.
- **The API key** is stored in the database, readable only by administrators, and **not
  encrypted**. Protect your database backups.
- **Logs** record request sizes only, never message content.

---

## Usage reporting

- **AI Assistant → Reporting → Usage:** tokens, API calls and active users for 7, 30 or 90 days or
  all time, with a daily chart and a breakdown by model, provider, user and request type.
- **Detailed request log:** one line per provider request (tokens, status, duration).
- **Tool Requests / Tool Audit:** every preview, confirmation and tool run.

Token counts come from the provider's responses. For actual costs, see the provider's billing
page.

---

## Project structure

```
odoo_ai_assistant/
├── models/
│   ├── configuration.py   AI Settings, model catalogue, Yes/No user access
│   ├── conversation.py    Chat flow: topic and intent checks, tool calls, history
│   ├── providers.py       OpenAI / Claude / Gemini / OpenRouter clients and prompts
│   ├── tools.py           Generic chart tool and app/screen/version tools
│   ├── native_mcp.py      MCP Server tools, previews, Confirm, safety checks
│   ├── mcp_bridge.py      Read-only grouping used by charts
│   ├── navigation.py      Apps, menus and screens of the current user
│   ├── usage.py           Usage log and dashboard data
│   ├── discuss.py         Keeps AI conversations private inside Discuss
│   ├── formatting.py      Safe Markdown rendering of AI answers
│   └── limits.py          Size limits and the list of protected models
├── static/src/            Chat, Discuss integration, charts and Usage dashboard (OWL)
├── views/                 Settings, menus and admin screens
├── security/              Access group (AI Assistant: Yes/No), access rules
└── data/                  Default settings and the "Odoo AI" chat partner
```

---

## Troubleshooting

| Message or symptom | Cause and fix |
|---|---|
| **HTTP 404 … model_not_found** | Your API key can't use the selected model; some models need a verified organisation or a higher usage tier. Choose another model in Settings (for example `gpt-4o`). |
| **"… is not available for charts"** | That model isn't enabled for the AI. Enable **Read** for it under **Choose which Odoo data the AI may use** (step 4). |
| **"I can only assist with Odoo …"** for an Odoo question | Rephrase it with the app name (for example "in CRM"). |
| **Rate limit / quota reached** | The provider's limit for your plan was reached. Wait a minute or check your plan. |
| **Page error after updating the module** | The browser is still using old files. Reload with **Ctrl + Shift + R**. |
| **The wand icon doesn't appear** | Check that the assistant is enabled (step 1), the user is set to **Yes** (step 3), and reload the page. |

---

## Limitations

- **Model quality:** answers depend on the chosen AI model. Small or older models misunderstand
  requests more often.
- **Chart size:** charts in the chat show up to 50 groups. **Open in Odoo** shows all of them.
- **Sharing limit:** a shared result can be up to 100,000 characters and four images.
- **No web lookup:** the assistant can't check information on the internet, for example about
  other Odoo versions.

---

## License

LGPL-3. See [LICENSE](LICENSE). The required `mcp_server` module is distributed separately under
its own license.
