import re

from lxml import etree

from odoo import _, release
from odoo.exceptions import AccessError, ValidationError

from .limits import is_restricted_model


_SENSITIVE_FIELD = re.compile(r"password|passwd|api.?key|token|secret|credential|private.?key", re.IGNORECASE)


def accessible_apps(env):
    menus = env["ir.ui.menu"].load_menus(False)
    return [menus[menu_id]["name"] for menu_id in menus["root"]["children"]]


def accessible_menu_names(env):
    """Names of every app and menu the current user can open."""
    menus = env["ir.ui.menu"].load_menus(False)
    return sorted({menu["name"] for menu_id, menu in menus.items()
                   if menu_id != "root" and isinstance(menu.get("name"), str)})


def screen_context(env, app=""):
    rows = []
    names = [app] if app else accessible_apps(env)
    for name in names:
        for row in app_structure(env, {"app": name})["rows"]:
            rows.append({key: row[key] for key in ("menu", "screen", "views")})
    return {
        "running_version": release.version, "menus": rows[:600],
        "total_screens": len(rows), "truncated": len(rows) > 600,
    }


def _app_menus(env, app):
    menus = env["ir.ui.menu"].load_menus(False)
    query = app.strip().casefold()
    roots = [menus[menu_id] for menu_id in menus["root"]["children"]]
    exact = [root for root in roots if root["name"].casefold() == query]
    matches = exact or [root for root in roots if query in root["name"].casefold()]
    if not matches:
        raise ValidationError(_("No accessible Odoo app matches that name. Ask to list available apps first."))
    entries = []

    def visit(menu, path):
        path = path + [menu["name"]]
        entries.append((menu, " / ".join(path)))
        for child_id in menu["children"]:
            visit(menus[child_id], path)

    for root in matches:
        if query:
            visit(root, [])
        else:
            entries.append((root, root["name"]))
    return entries


def app_structure(env, arguments):
    if (not isinstance(arguments, dict) or set(arguments) != {"app"}
            or not isinstance(arguments["app"], str) or len(arguments["app"]) > 200):
        raise ValidationError(_("App discovery requires an app name of at most 200 characters."))
    rows = []
    for menu, path in _app_menus(env, arguments["app"]):
        action_model = menu["action_model"]
        model, views = "", ""
        if action_model == "ir.actions.act_window":
            # Native menu loading already checked menu groups and target-model read access.
            action = env[action_model].sudo().browse(menu["action_id"])
            model, views = action.res_model, action.view_mode
        rows.append({
            "menu": path,
            "screen": (_("Record view") if model else _("App screen") if action_model else _("Menu group")),
            "model": model, "views": views,
        })
        if not arguments["app"].strip():
            rows[-1].update(screen=_("Available app"), model="", views="")
    return {
        "kind": "table",
        "title": (_("Accessible Odoo app screens (not business records)") if arguments["app"].strip()
                  else _("Available Odoo apps")),
        "columns": [
            {"key": "menu", "label": _("App / Menu")},
            {"key": "screen", "label": _("Screen type")},
            {"key": "model", "label": _("Record model")},
            {"key": "views", "label": _("Available views")},
        ],
        "rows": rows, "truncated": False,
    }


def screen_details(env, arguments):
    if not env["odoo.ai.assistant.config"]._configuration().screen_metadata:
        raise AccessError(_("Screen metadata sharing is disabled in AI Settings."))
    if (not isinstance(arguments, dict) or set(arguments) != {"app", "screen"}
            or any(not isinstance(arguments[key], str) or not arguments[key].strip()
                   or len(arguments[key]) > 200 for key in ("app", "screen"))):
        raise ValidationError(_("Screen discovery requires an app and a screen name of 1 to 200 characters."))
    entries = _app_menus(env, arguments["app"])
    query = arguments["screen"].strip().replace(" > ", " / ").casefold()
    exact = [(menu, path) for menu, path in entries if query in {menu["name"].casefold(), path.casefold()}]
    matches = exact or [(menu, path) for menu, path in entries if query in path.casefold()]
    if not matches:
        raise ValidationError(_("No accessible screen matches that name in this app."))
    if len(matches) > 8:
        raise ValidationError(_("Several screens match. Specify a more precise menu path."))
    screens = []
    for menu, path in matches:
        screen = {"menu": path, "views": [], "fields": [], "truncated": False}
        screens.append(screen)
        if menu["action_model"] != "ir.actions.act_window":
            screen["limitation"] = "This menu is a group or client screen; it has no model-backed form to inspect."
            continue
        action = env["ir.actions.act_window"].sudo().browse(menu["action_id"])
        model = env[action.res_model]
        model.check_access("read")
        if is_restricted_model(env, model._name):
            screen["limitation"] = "Field discovery excludes technical, user-security and settings screens."
            continue
        definitions = model.fields_get(attributes=["string", "type", "required"])
        view_specs = action.views
        selected = [spec for kind in ("form", "list", "kanban", "pivot", "graph")
                    for spec in view_specs if spec[1] == kind][:3]
        seen = {}
        for view_id, view_type in selected:
            view = model.get_view(view_id=view_id or None, view_type=view_type)
            arch = etree.fromstring(view["arch"].encode())
            screen["views"].append(view_type)
            for node in arch.xpath(".//field[not(ancestor::field)]"):
                name = node.get("name")
                definition = definitions.get(name)
                if not definition:
                    continue
                label = node.get("string") or definition["string"]
                if (_SENSITIVE_FIELD.search(name) or _SENSITIVE_FIELD.search(label)
                        or definition["type"] in {"binary", "html"}
                        or node.get("password") in {"1", "true", "True"}):
                    continue
                ancestors = [node, *node.iterancestors()]
                if any(element.get("invisible", "").strip() in {"1", "True", "true"}
                       or element.get("column_invisible", "").strip() in {"1", "True", "true"}
                       for element in ancestors):
                    continue
                if name in seen:
                    if view_type not in seen[name]["views"]:
                        seen[name]["views"].append(view_type)
                    continue
                field = {
                    "label": label, "type": definition["type"], "required": bool(definition["required"]),
                    "views": [view_type],
                    "visibility": "conditional" if any(element.get("invisible") or element.get("column_invisible")
                                                       for element in ancestors) else "visible",
                }
                seen[name] = field
                screen["fields"].append(field)
        if len(screen["fields"]) > 80:
            screen["fields"] = screen["fields"][:80]
            screen["truncated"] = True
    return {
        "screens": screens,
        "scope": "Accessible view field labels and types only; no record values or field options.",
    }
