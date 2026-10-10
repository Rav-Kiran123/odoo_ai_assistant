import json
import math
import re
from datetime import date, datetime

from odoo import _, fields, release
from odoo.exceptions import AccessError, UserError, ValidationError

from .limits import is_restricted_model
from .navigation import _SENSITIVE_FIELD, accessible_apps, app_structure, screen_details

TOOLS = [
    {
        "name": "odoo_instance_info",
        "description": "Read the actual running Odoo server version and installed edition locally. "
                       "Use for instance version questions, never guess.",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "odoo_app_structure",
        "description": "Discover the actual apps and their accessible menus/screens in this Odoo instance. "
                       "Use for what is inside an app, what apps are installed/available, and navigation questions. "
                       "No hardcoded app list. Supply app='' to list apps, or an app name to inspect it. "
                       "Menu paths, record models and view types are displayed locally, not business records.",
        "inputSchema": {"type": "object", "properties": {
            "app": {"type": "string", "description": "App name; empty to list available apps"},
        }, "required": ["app"], "additionalProperties": False},
    },
    {
        "name": "odoo_screen_details",
        "description": "Inspect actual accessible form/list fields for a screen in ANY installed app, "
                       "including custom apps. Use when asked what a specific screen contains, its fields, "
                       "or how to use those fields. Supply app and screen/menu name from navigation context. "
                       "Returns permitted field labels/types, not record values, credentials or settings. "
                       "Do not confuse field descriptions with a request to list actual records.",
        "inputSchema": {"type": "object", "properties": {
            "app": {"type": "string"}, "screen": {"type": "string"},
        }, "required": ["app", "screen"], "additionalProperties": False},
    },
]
_TOOL = {tool["name"]: tool for tool in TOOLS}

CHART_TOOL = "odoo_chart"
_CHART_REQUEST = re.compile(r"\b(?:charts?|graphs?|plots?|visuali[sz](?:e|ation|ations))\b", re.IGNORECASE)
_CHART_TYPES = ("bar", "line", "pie", "doughnut", "scatter", "table")
_CHART_OPERATIONS = ("count", "sum", "avg")
_DATE_INTERVALS = ("day", "week", "month", "quarter", "year")
_GROUPABLE_TYPES = {"char", "selection", "boolean", "many2one", "many2many", "date", "datetime",
                    "integer", "float", "monetary"}
_NUMERIC_TYPES = {"integer", "float", "monetary"}
# Odoo's own graph view supports bar, line and pie; the chat's extra types map to the closest one.
_GRAPH_MODES = {"bar": "bar", "line": "line", "pie": "pie", "doughnut": "pie", "scatter": "line", "table": "bar"}


def chart_models(env):
    """Models the current user may chart: enabled for MCP reads, readable and not technical."""
    gates = env["mcp.enabled.model"].sudo()
    names = gates.search([("active", "=", True)]).mapped("model_name")
    return sorted(name for name in set(names)
                  if not is_restricted_model(env, name) and gates.check_model_operation_enabled(name, "read")
                  and env[name].has_access("read"))


def requested_chart_tool(history, tools):
    """The chart tool, when the latest user request asks for a chart and the tool is available."""
    prompt = next((message.get("content", "") for message in reversed(history)
                   if message.get("role") == "user" and isinstance(message.get("content"), str)), "")
    if not _CHART_REQUEST.search(prompt):
        return None
    return next((tool for tool in tools if tool.get("name") == CHART_TOOL), None)


def available_tools(env):
    app_tool = _TOOL["odoo_app_structure"]
    tools = [_TOOL["odoo_instance_info"], {
        **app_tool, "description": app_tool["description"] + " Available apps: " + ", ".join(accessible_apps(env)),
    }]
    config = env["odoo.ai.assistant.config"]._configuration()
    if config.screen_metadata:
        tools.append(_TOOL["odoo_screen_details"])
    if not config.local_tools:
        return tools
    models = chart_models(env)
    if models:
        tools.append({
            "name": CHART_TOOL,
            # Domains are free-form Odoo filters, which OpenAI's strict mode cannot describe.
            "strict": False,
            "description": "Draw a chart or grouped table of any listed Odoo model. Odoo groups the records "
                           "by one field and counts them, or sums or averages a numeric field, with the "
                           "user's permissions; the result stays in Odoo. Use real field names (from the "
                           "field list or mcp_get_fields); dates group by the chosen interval.",
            "inputSchema": {"type": "object", "properties": {
                "model": {"type": "string", "enum": models},
                "group_by": {"type": "string", "description": "Field to group by"},
                "interval": {"type": "string", "enum": list(_DATE_INTERVALS),
                             "description": "Grouping for date fields; default month"},
                "operation": {"type": "string", "enum": list(_CHART_OPERATIONS)},
                "measure": {"type": "string", "description": "Numeric field for sum or avg; empty for count"},
                "domain": {"type": "array",
                           "description": "Odoo domain filter such as [[\"state\", \"=\", \"sale\"]]; [] for all"},
                "chart_type": {"type": "string", "enum": list(_CHART_TYPES)},
                "title": {"type": "string", "description": "Short title without record values"},
            }, "required": ["model", "group_by", "operation", "chart_type", "title"], "additionalProperties": False},
        })
    tools.extend(env["odoo.ai.assistant.native.mcp"]._provider_tools())
    return tools


def parse_tool_arguments(arguments):
    """Tool arguments arrive as an object or, from some providers, as JSON text."""
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError as error:
            raise ValidationError(_("Invalid MCP tool arguments.")) from error
    return arguments


def execute_tool(env, name, arguments):
    arguments = parse_tool_arguments(arguments)
    if not isinstance(arguments, dict):
        raise ValidationError(_("MCP arguments must be an object."))
    if name not in {tool["name"] for tool in available_tools(env)}:
        raise AccessError(_("This MCP tool is not available to your user."))
    if name == "odoo_app_structure":
        return json.dumps(app_structure(env, arguments), ensure_ascii=False)
    if name == "odoo_screen_details":
        return json.dumps(screen_details(env, arguments), ensure_ascii=False)
    if name == "odoo_instance_info":
        if arguments:
            raise ValidationError(_("Instance information does not accept arguments."))
        enterprise = bool(env["ir.module.module"].sudo().search_count([
            ("name", "=", "web_enterprise"), ("state", "=", "installed"),
        ]))
        return _("This instance is running Odoo %(version)s (%(edition)s Edition).") % {
            "version": release.version, "edition": "Enterprise" if enterprise else "Community",
        }
    raise UserError(_("Unknown MCP tool."))


def execute_chart(env, arguments):
    """Group any chartable model by one field with Odoo's read_group and return a local chart."""
    arguments = parse_tool_arguments(arguments)
    if not isinstance(arguments, dict):
        raise ValidationError(_("Chart arguments must be an object."))
    # Only the documented arguments are used; omitted optional ones take their documented defaults.
    arguments = {"interval": "month", "measure": "", "domain": [], **{
        key: value for key, value in arguments.items()
        if key in {"model", "group_by", "interval", "operation", "measure", "domain", "chart_type", "title"}
    }}
    name, group, interval = arguments.get("model"), arguments.get("group_by"), arguments["interval"]
    operation, measure, domain = arguments.get("operation"), arguments["measure"], arguments["domain"]
    chart, title = arguments.get("chart_type"), arguments.get("title")
    if name not in chart_models(env):
        raise AccessError(_("This model is not available for charts. Enable reads for it in MCP Server."))
    if (not all(isinstance(value, str) for value in (group, interval, operation, measure, chart, title))
            or not isinstance(domain, list) or operation not in _CHART_OPERATIONS or chart not in _CHART_TYPES
            or interval not in _DATE_INTERVALS or not 1 <= len(title) <= 120):
        raise ValidationError(_("Choose a model, a grouping field, count, sum or avg, a supported chart type "
                                "and a short title."))
    if any(isinstance(term, (list, tuple)) and term and isinstance(term[0], str) and _SENSITIVE_FIELD.search(term[0])
           for term in domain):
        raise AccessError(_("Credential fields cannot be used in chart filters."))
    model = env[name]
    definitions = model.fields_get(attributes=["string", "type", "groupable", "aggregator"])

    def groupable(field):
        definition = definitions.get(field) or {}
        return (definition.get("groupable") and definition.get("type") in _GROUPABLE_TYPES
                and not _SENSITIVE_FIELD.search(field))

    if not groupable(group):
        options = sorted(field for field in definitions if groupable(field))
        raise ValidationError(_("'%(field)s' cannot group a chart of %(model)s. Groupable fields: %(fields)s.") % {
            "field": group, "model": name, "fields": ", ".join(options[:80])})
    group_type = definitions[group]["type"]
    if chart == "scatter" and group_type not in _NUMERIC_TYPES:
        raise ValidationError(_("Scatter charts require a numeric grouping field."))
    value_label = _("Record count")
    if operation == "count":
        if measure:
            raise ValidationError(_("Leave measure empty for a count chart."))
        aggregate = "__count"
    else:
        definition = definitions.get(measure) or {}
        if definition.get("type") not in _NUMERIC_TYPES or not definition.get("aggregator") \
                or _SENSITIVE_FIELD.search(measure):
            options = sorted(field for field, item in definitions.items()
                             if item.get("type") in _NUMERIC_TYPES and item.get("aggregator"))
            raise ValidationError(_("'%(field)s' is not a numeric field of %(model)s that can be summed. "
                                    "Numeric fields: %(fields)s.") % {
                                        "field": measure, "model": name, "fields": ", ".join(options[:80])})
        aggregate = f"{measure}:{operation}"
        value_label = definition["string"] if operation == "sum" else _("Average %s") % definition["string"]
        # Odoo leaves currency_field empty when it defaults to currency_id; resolve it the ORM's way.
        currency = model._fields[measure].get_currency_field(model) if definition["type"] == "monetary" else None
        if currency:
            currencies = model._read_group(domain, groupby=[currency], aggregates=["__count"], limit=2)
            if len(currencies) > 1:
                raise ValidationError(_("Filter to one currency before adding up monetary values."))
            if currencies and currencies[0][0]:
                value_label += f" ({currencies[0][0].name})"
    grouping = f"{group}:{interval}" if group_type in {"date", "datetime"} else group
    result = env["odoo.ai.assistant.mcp"]._execute("ai_group_records", {
        "model": name, "domain": domain, "group": grouping, "measure": aggregate,
    })
    # A group with nothing to add up comes back from Odoo as False (or None); it counts as zero.
    values = [0 if row["value"] is None or row["value"] is False else row["value"] for row in result["rows"]]
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise UserError(_("The chart contains invalid numeric values."))
    if chart in {"pie", "doughnut"} and any(value < 0 for value in values):
        raise ValidationError(_("Pie and doughnut charts cannot display negative values."))
    labels = [row["label"] for row in result["rows"]]
    # Everything Odoo's own graph and pivot views need to reopen this chart for deeper analysis.
    open_in_odoo = {"model": name, "domain": domain, "group_by": grouping, "measure": aggregate.split(":")[0],
                    "mode": _GRAPH_MODES[chart]}
    if chart == "table":
        return {"kind": "table", "title": title, "columns": [
            {"key": "label", "label": definitions[group]["string"]},
            {"key": "value", "label": value_label},
        ], "rows": [{"label": label, "value": value} for label, value in zip(labels, values)],
            "truncated": result["truncated"], "open": open_in_odoo}
    return {"kind": "chart", "title": title, "chart_type": chart, "operation": operation, "labels": labels,
            "values": values, "value_label": value_label, "truncated": result["truncated"], "open": open_in_odoo}


def cell_value(model, name, value):
    """Odoo reads an empty field as False; show it empty unless the field is a checkbox."""
    field = model._fields.get(name)
    if value is False and field and field.type != "boolean":
        return None
    return table_value(value)


def table_value(value):
    if isinstance(value, datetime):
        return fields.Datetime.to_string(value)
    if isinstance(value, date):
        return fields.Date.to_string(value)
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [table_value(item) for item in value]
    raise UserError(_("The local table contains an unsupported or invalid value."))
