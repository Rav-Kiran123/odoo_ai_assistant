import json
from unittest.mock import Mock, patch

from odoo import release
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged

from ..models.providers import ProviderClient
from ..models.formatting import format_reply
from ..models.limits import MAX_MESSAGE_CHARS, is_restricted_model
from ..models.native_mcp import MAX_UPLOAD_BYTES
from ..models.navigation import (
    accessible_apps, accessible_menu_names, app_structure, screen_context, screen_details,
)
from ..models.tools import TOOLS, available_tools, chart_models, execute_chart, execute_tool, requested_chart_tool


classify_odoo_topic = ProviderClient.is_odoo_topic
classify_title = ProviderClient.conversation_title


@tagged("post_install", "-at_install")
class TestAssistant(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login="ai_owner", groups="base.group_user", ai_assistant_enabled=True)
        cls.other = new_test_user(cls.env, login="ai_other", groups="base.group_user", ai_assistant_enabled=True)
        cls.config = cls.env.ref("odoo_ai_assistant.default_config")
        cls.model = cls.env["odoo.ai.assistant.provider.model"].create({
            "name": "Test model", "identifier": "test-model", "provider": "openai",
        })
        cls.config.write({
            "enabled": True, "api_key": "not-a-real-key", "model_id": cls.model.id,
            "provider": "openai", "local_tools": True,
            "screen_metadata": False, "native_actions": False, "business_data_sharing": False,
        })
        cls.conversations = cls.env["odoo.ai.assistant.conversation"].with_user(cls.owner)
        cls.env["mcp.enabled.model"].create({
            "model_id": cls.env["ir.model"]._get_id("res.partner"), "allow_read": True,
        })

    def chart_args(self, **overrides):
        """Arguments for the generic chart tool on contacts, as the AI sends them."""
        return {"model": "res.partner", "group_by": "city", "operation": "count", "domain": [],
                "chart_type": "bar", "title": "Contacts by city", **overrides}

    def test_supplied_mcp_registry_executes_and_denials_apply(self):
        bridge = self.env["odoo.ai.assistant.mcp"].with_user(self.owner)
        self.assertIn("ai_group_records", bridge._get_mcp_tools())
        original = type(bridge)._ai_group_records
        calls = []

        def traced_group(record, *args, **kwargs):
            calls.append(kwargs["model"])
            return original(record, *args, **kwargs)

        with patch.object(type(bridge), "_ai_group_records", traced_group):
            execute_chart(self.env(user=self.owner), self.chart_args())
        self.assertEqual(calls, ["res.partner"])
        # Direct calls outside the AI tools are refused, and MCP's read gate applies.
        with self.assertRaises(AccessError):
            bridge._ai_group_records("res.partner", [], "city", "__count")
        gate = self.env["mcp.enabled.model"].search([("model_name", "=", "res.partner")])
        gate.allow_read = False
        with self.assertRaises(AccessError):
            execute_chart(self.env(user=self.owner), self.chart_args())

    def test_fixed_business_tools_are_not_registered(self):
        names = {tool["name"] for tool in available_tools(self.env(user=self.owner))}
        for removed in ("search_contacts", "recent_orders", "odoo_unpaid_invoices", "odoo_reorder_point_shortfalls"):
            self.assertNotIn(removed, names)

    def test_ai_access_uses_access_rights_groups(self):
        chat = self.env.ref("odoo_ai_assistant.group_ai_user")
        # One group in its privilege, so the Access Rights tab shows No / Yes.
        self.assertEqual(chat.privilege_id.group_ids, chat)
        self.assertEqual((chat.privilege_id.name, chat.name), ("AI Assistant", "Yes"))
        self.assertFalse(self.env.ref("odoo_ai_assistant.group_ai_action", raise_if_not_found=False))
        # Its own "AI" section on the Access Rights tab, ordered right after Marketing.
        category = chat.privilege_id.category_id
        self.assertEqual(category, self.env.ref("odoo_ai_assistant.module_category_ai"))
        self.assertGreater(category.sequence, self.env.ref("base.module_category_marketing").sequence)
        user = new_test_user(self.env, login="ai_groups", groups="base.group_user")
        self.assertFalse(user.ai_assistant_enabled)
        user.group_ids = [(4, chat.id)]
        self.assertTrue(user.ai_assistant_enabled)
        user.ai_assistant_enabled = False
        self.assertNotIn(chat, user.all_group_ids)
        self.assertFalse(self.env["odoo.ai.assistant.conversation"].with_user(user).bootstrap()["enabled"])
        self.assertNotIn(self.env.ref("odoo_ai_assistant.assistant_menu").id,
                         self.env["ir.ui.menu"].with_user(user)._visible_menu_ids())

    def test_menus_follow_configuration_and_reporting_layout(self):
        menu = lambda xmlid: self.env.ref(f"odoo_ai_assistant.{xmlid}")
        self.assertFalse(self.env.ref("odoo_ai_assistant.assistant_users_menu", raise_if_not_found=False))
        for removed in ("assistant_pricing_menu", "assistant_sources_menu"):
            self.assertFalse(self.env.ref(f"odoo_ai_assistant.{removed}", raise_if_not_found=False))
        self.assertEqual(menu("assistant_settings_menu").parent_id, menu("assistant_configuration_menu"))
        for xmlid in ("assistant_usage_menu", "native_request_menu", "native_event_menu"):
            self.assertEqual(menu(xmlid).parent_id, menu("assistant_reporting_menu"))
        visible = self.env["ir.ui.menu"].with_user(self.owner)._visible_menu_ids()
        # The chat opens from the top-bar icon or the app itself; no separate Conversations entry.
        self.assertFalse(self.env.ref("odoo_ai_assistant.assistant_chat_menu", raise_if_not_found=False))
        self.assertIn(menu("assistant_menu").id, visible)
        self.assertNotIn(menu("assistant_reporting_menu").id, visible)
        self.assertNotIn(menu("assistant_configuration_menu").id, visible)

    def test_usage_dashboard_aggregates_and_is_admin_only(self):
        ledger = self.env["odoo.ai.assistant.usage"]
        ledger.search([]).unlink()
        ledger.create([
            {"user_id": self.owner.id, "provider": "openai", "model_name": "test-model", "purpose": "reply",
             "status": "received", "input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            {"user_id": self.other.id, "provider": "openai", "model_name": "test-model", "purpose": "scope",
             "status": "failed"},
        ])
        data = ledger.dashboard_data("7d")
        self.assertEqual(data["totals"]["calls"], 2)
        self.assertEqual(data["totals"]["tokens"], 120)
        self.assertEqual(data["totals"]["failed_calls"], 1)
        self.assertEqual(data["totals"]["active_users"], 2)
        self.assertEqual(len(data["daily"]), 7)
        self.assertEqual(sum(day["calls"] for day in data["daily"]), 2)
        self.assertEqual(data["by_model"][0]["label"], "test-model")
        self.assertEqual({row["label"] for row in data["by_user"]}, {self.owner.name, self.other.name})
        self.assertIsNotNone(data["previous"])
        self.assertIsNone(ledger.dashboard_data("all")["previous"])
        with self.assertRaises(ValidationError):
            ledger.dashboard_data("1y")
        with self.assertRaises(AccessError):
            ledger.with_user(self.owner).dashboard_data("30d")

    def test_context_overflow_is_retried_with_smaller_requests(self):
        from ..models import providers
        providers._CONTEXT_LEVELS.clear()
        self.addCleanup(providers._CONTEXT_LEVELS.clear)
        tools = [{"name": f"tool_{index}", "description": f"Tool number {index}",
                  "inputSchema": {"type": "object", "properties": {}}} for index in range(20)]
        tools.append({"name": "odoo_invoice_chart", "description": "Chart invoices by state",
                      "inputSchema": {"type": "object", "properties": {}}})
        history = [{"role": "user" if index % 2 == 0 else "assistant", "content": f"Earlier {index} " + "x" * 5000}
                   for index in range(8)] + [{"role": "user", "content": "Chart my invoices"}]
        screen = {"running_version": "19.0", "menus": [{"menu": "Sales"}] * 500, "total_screens": 500}
        overflow = providers.ProviderError("too long", kind="context", status=400)
        with patch.object(ProviderClient, "_reply_once", side_effect=[overflow, overflow, ("ok", [])]) as once:
            client = ProviderClient("openai", "dummy")
            self.assertEqual(client.reply("small-model", history, tools, screen_context=screen), ("ok", []))
        first, second, third = once.call_args_list
        self.assertEqual(len(first.args[2]), 21)
        self.assertIn("menus", first.kwargs["screen_context"])
        self.assertNotIn("menus", second.kwargs["screen_context"])
        self.assertLessEqual(len(second.args[1]), 5)
        self.assertEqual(second.args[1][-1]["content"], "Chart my invoices")
        self.assertTrue(all(len(entry["content"]) <= 2000 for entry in second.args[1][:-1]))
        self.assertEqual(len(third.args[2]), 8)
        self.assertIn("odoo_invoice_chart", [tool["name"] for tool in third.args[2]])
        self.assertEqual(third.args[1], [{"role": "user", "content": "Chart my invoices"}])
        # The working level is remembered, so the next request skips the failed attempts.
        with patch.object(ProviderClient, "_reply_once", return_value=("ok", [])) as once:
            client.reply("small-model", history, tools, screen_context=screen)
        self.assertEqual(once.call_count, 1)
        self.assertEqual(len(once.call_args.args[2]), 8)
        # Errors a smaller request cannot fix are raised immediately.
        with patch.object(ProviderClient, "_reply_once", side_effect=providers.ProviderError(
            "no credit", kind="billing", status=400,
        )) as once, self.assertRaisesRegex(UserError, "no credit"):
            client.reply("other-model", history, tools)
        self.assertEqual(once.call_count, 1)

    def test_provider_errors_explain_account_and_size_problems_without_echoing(self):
        cases = [
            (400, {"type": "invalid_request_error", "message": "Your credit balance is too low to access the API."},
             "billing", "no remaining credit"),
            (429, {"code": "insufficient_quota", "message": "You exceeded your current quota."}, "billing", "billing"),
            (400, {"code": "context_length_exceeded", "message": "This model's maximum context length is 16385."},
             "context", "larger context window"),
            (400, {"type": "invalid_request_error", "message": "prompt is too long: 250000 tokens"},
             "context", "larger context window"),
            (404, {"code": "model_not_found", "message": "secret"}, "model", "Refresh the models"),
            (429, {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded. Check your plan and billing details."},
             "rate", "usage quota"),
            (401, {"type": "authentication_error", "message": "secret"}, "auth", "API key"),
        ]
        for status, error, kind, hint in cases:
            response = Mock(status_code=status)
            response.json.return_value = {"error": error}
            with self.subTest(error=error), patch("requests.request", return_value=response), \
                    self.assertRaises(UserError) as raised:
                ProviderClient("anthropic", "dummy")._request("POST", "/messages", {})
            self.assertEqual(raised.exception.kind, kind)
            self.assertIn(hint, str(raised.exception))
            self.assertNotIn(error["message"], str(raised.exception))

    def test_request_size_log_accepts_schema_type_unions(self):
        # Native MCP tool schemas use JSON Schema type unions; logging must not crash on them.
        payload = {"model": "m", "messages": [{"role": "user", "content": "What can I do in Discuss?"}],
                   "tools": [{"type": "function", "function": {"name": "mcp_search_records", "description": "Search",
                              "parameters": {"type": "object", "properties": {
                                  "limit": {"type": ["integer", "null"]},
                                  "fields": {"type": ["array", "null"], "items": {"type": "string"}},
                              }}}}]}
        summary = ProviderClient._request_summary(payload)
        self.assertEqual(summary["tool_count"], 1)
        self.assertEqual(summary["image_count"], 0)

    def test_claude_required_tool_is_instructed_not_forced(self):
        chart = {"name": "odoo_chart", "description": "Chart", "inputSchema": {"type": "object", "properties": {}}}
        response = {"content": [{"type": "tool_use", "name": "odoo_chart", "input": {}}]}
        with patch.object(ProviderClient, "_request", return_value=response) as request:
            ProviderClient("anthropic", "dummy").reply(
                "claude-fable-5-1", [{"role": "user", "content": "Chart"}], [*TOOLS, chart], tool_choice="odoo_chart",
            )
        payload = request.call_args.args[2]
        self.assertNotIn("tool_choice", payload)
        self.assertEqual([tool["name"] for tool in payload["tools"]], ["odoo_chart"])
        self.assertIn("You must call the function odoo_chart", payload["system"])

    def test_restricted_models_share_one_policy(self):
        for model in ("res.users", "res.groups", "res.users.apikeys", "ir.model", "mail.message",
                      "mcp.enabled.model", "odoo.ai.assistant.config", "res.config.settings", "missing.model"):
            with self.subTest(model=model):
                self.assertTrue(is_restricted_model(self.env, model))
        self.assertFalse(is_restricted_model(self.env, "res.partner"))
        self.assertFalse(is_restricted_model(self.env, "ir.attachment", allowed={"ir.attachment"}))
        native = self.env["odoo.ai.assistant.native.mcp"]
        self.assertTrue(native._allowed_model("ir.attachment"))
        self.assertTrue(native._allowed_model("mail.activity"))
        self.assertFalse(native._allowed_model("res.groups"))
        # Charts follow the same policy, even when MCP reads are enabled for the model.
        self.env["mcp.enabled.model"].create({"model_id": self.env["ir.model"]._get_id("res.groups"), "allow_read": True})
        self.assertNotIn("res.groups", chart_models(self.env))

    def test_bootstrap_query_count_does_not_grow_with_conversations(self):
        def queries_for(count):
            for _index in range(count):
                conversation = self.new_conversation()
                conversation._store_answer("Answer %s" % _index)
            self.conversations.bootstrap()  # warm one-time caches (menus, groups) before measuring
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            data = self.conversations.bootstrap()
            return self.env.cr.sql_log_count - before, data

        few, _data = queries_for(3)
        many, data = queries_for(22)
        self.assertEqual(few, many)
        self.assertTrue(all(entry["preview"] for entry in data["conversations"]))

    def test_bootstrap_lists_every_ai_channel_for_the_discuss_sidebar(self):
        first, second = self.new_conversation(), self.new_conversation()
        other = self.env["odoo.ai.assistant.conversation"].with_user(self.other).start_conversation()
        with patch("odoo.addons.odoo_ai_assistant.models.conversation.MAX_LISTED_CONVERSATIONS", 1):
            data = self.conversations.bootstrap()
        self.assertEqual(len(data["conversations"]), 1)
        self.assertLessEqual({first.channel_id.id, second.channel_id.id}, set(data["channel_ids"]))
        self.assertNotIn(other["channel_id"][0], data["channel_ids"])

    def test_bootstrap_shares_assistant_name_limits_and_app_suggestions(self):
        data = self.conversations.bootstrap()
        self.assertEqual(data["assistant_name"], self.env.ref("odoo_ai_assistant.ai_partner").name)
        self.assertEqual(data["max_message_chars"], MAX_MESSAGE_CHARS)
        self.assertEqual(data["max_upload_bytes"], MAX_UPLOAD_BYTES)
        apps = accessible_apps(self.env(user=self.owner))
        self.assertEqual(len(data["suggestions"]), min(3, len(apps)))
        self.assertTrue(all(any(app in suggestion for app in apps) for suggestion in data["suggestions"]))
        self.owner.ai_assistant_enabled = False
        data = self.conversations.bootstrap()
        self.assertEqual(data["suggestions"], [])
        self.assertEqual(data["assistant_name"], self.env.ref("odoo_ai_assistant.ai_partner").name)

    def test_topic_check_receives_accessible_menu_vocabulary(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "is_odoo_topic", return_value=True) as classifier, patch.object(
            ProviderClient, "reply", return_value=("Odoo help.", []),
        ):
            conversation.send_message("Explain Odoo")
        self.assertEqual(classifier.call_args.kwargs["vocabulary"], accessible_menu_names(self.env(user=self.owner)))

    def test_configured_answer_length_and_timeout_reach_provider(self):
        self.config.model_id = False
        self.model.provider = "anthropic"
        self.config.write({
            "provider": "anthropic", "model_id": self.model.id,
            "max_output_tokens": 4096, "request_timeout": 120,
        })
        response = Mock(status_code=200)
        response.json.return_value = {"content": [{"type": "text", "text": "ok"}]}
        with patch("requests.request", return_value=response) as request, patch.object(
            type(self.env["odoo.ai.assistant.usage"]), "_observer", return_value=None,
        ):
            self.config._client("reply").reply("test-model", [{"role": "user", "content": "Hi"}], [])
        self.assertEqual(request.call_args.kwargs["json"]["max_tokens"], 4096)
        self.assertEqual(request.call_args.kwargs["timeout"], (10, 120))
        for values in ({"max_output_tokens": 100}, {"request_timeout": 5}):
            with self.subTest(values=values), self.cr.savepoint(), self.assertRaises(ValidationError):
                self.config.write(values)

    def new_conversation(self):
        result = self.conversations.start_conversation()
        return self.conversations.browse(result["id"])

    def setUp(self):
        super().setUp()
        classifier = patch.object(ProviderClient, "is_odoo_topic", return_value=True)
        classifier.start()
        self.addCleanup(classifier.stop)
        progress = patch.object(type(self.conversations), "_progress")
        progress.start()
        self.addCleanup(progress.stop)
        title = patch.object(ProviderClient, "conversation_title", return_value="")
        title.start()
        self.addCleanup(title.stop)
        # Questions in these tests are reads; action requests are covered in test_native_mcp.
        intent = patch.object(ProviderClient, "requires_action", return_value=False)
        intent.start()
        self.addCleanup(intent.stop)

    def test_topic_title_waits_for_meaningful_prompt_and_stays_stable(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", return_value=("Odoo help.", [])), patch.object(
            ProviderClient, "conversation_title", return_value="CRM Demo Opportunities",
        ) as title:
            conversation.send_message("Hi")
            self.assertEqual(conversation.name, "Odoo AI")
            title.assert_not_called()
            conversation.send_message("Create CRM opportunities in Odoo")
            self.assertEqual(conversation.name, "CRM Demo Opportunities")
            self.assertEqual(conversation.channel_id.name, conversation.name)
            self.assertEqual(title.call_args.args[1], ["Hi", "Create CRM opportunities in Odoo"])
            conversation.send_message("How to use Odoo contacts")
            self.assertEqual(title.call_count, 1)
        self.assertEqual(conversation.name, "CRM Demo Opportunities")

    def test_chart_request_forces_the_chart_tool(self):
        conversation = self.new_conversation()
        chart = next(tool for tool in available_tools(self.env(user=self.owner)) if tool["name"] == "odoo_chart")
        other = TOOLS[0]
        history = [{"role": "user", "content": "Create a bar chart of different stages in CRM"}]
        self.assertEqual(requested_chart_tool(history, [other, chart]), chart)
        self.assertIsNone(requested_chart_tool([{"role": "user", "content": "List the CRM stages"}], [other, chart]))
        self.assertIsNone(requested_chart_tool(history, [other]))
        calls = [{"name": "odoo_chart", "arguments": self.chart_args()}]
        with patch("odoo.addons.odoo_ai_assistant.models.conversation.available_tools",
                   return_value=[other, chart]), patch.object(
            ProviderClient, "reply", return_value=("", calls),
        ) as reply, patch.object(type(conversation), "_dispatch_calls") as dispatch:
            conversation.send_message(history[0]["content"])
        self.assertEqual(reply.call_args.kwargs["tool_choice"], "odoo_chart")
        self.assertEqual(reply.call_args.args[2], [chart])
        self.assertEqual(dispatch.call_args.args[3], calls)

    def test_ignored_chart_instruction_falls_back_to_all_tools(self):
        from ..models.providers import ProviderError
        conversation = self.new_conversation()
        view = {"name": "odoo_chart", "description": "Chart any listed model.",
                "inputSchema": {"type": "object", "properties": {}}}
        other = TOOLS[0]
        with patch("odoo.addons.odoo_ai_assistant.models.conversation.available_tools", return_value=[view, other]), \
                patch.object(ProviderClient, "reply", side_effect=[
                    ProviderError("did not call", kind="tool_not_called"), ("Here is a summary instead.", []),
                ]) as reply:
            messages = conversation.send_message("Create a bar chart of CRM stages")
        self.assertEqual(reply.call_args_list[0].kwargs["tool_choice"], "odoo_chart")
        self.assertNotIn("tool_choice", reply.call_args_list[1].kwargs)
        self.assertEqual(reply.call_args_list[1].args[2], [view, other])
        self.assertEqual(messages[-1]["content"], "Here is a summary instead.")

    def test_topic_title_uses_only_user_prompts_and_is_set_once(self):
        conversation = self.new_conversation()
        self.env["odoo.ai.assistant.message"].sudo().create([
            {"conversation_id": conversation.id, "role": "user", "content": "List Odoo contacts"},
            {"conversation_id": conversation.id, "role": "assistant", "content": "PRIVATE RESULT", "local_only": True},
        ])
        with patch.object(ProviderClient, "conversation_title", return_value="Contact List") as title:
            self.assertEqual(conversation._topic_title(self.config), "Contact List")
            self.assertEqual(title.call_args.args[1], ["List Odoo contacts"])
            conversation._apply_title("Contact List")
            self.assertEqual(conversation._topic_title(self.config), "")
            self.assertEqual(title.call_count, 1)
        self.assertEqual(conversation.name, "Contact List")
        self.assertEqual(conversation.channel_id.name, "Contact List")

    def test_provider_title_validates_output(self):
        client = ProviderClient("openai", "not-a-real-key")
        original = classify_title
        with patch.object(ProviderClient, "reply", return_value=('{"title":"CRM Opportunities"}', [])) as reply:
            self.assertEqual(original(client, "test-model", ["Create CRM opportunities"]), "CRM Opportunities")
            self.assertEqual(reply.call_args.args[2], [])
            self.assertIn("output_schema", reply.call_args.kwargs)
        for text, calls in [
            ("not json", []), ('{"title":3}', []), ('{"title":"Title","extra":1}', []),
            (json.dumps({"title": "x" * 61}), []), ('{"title":"<b>CRM</b>"}', []),
            ('{"title":"CRM"}', [{"name": "odoo_instance_info"}]),
        ]:
            with patch.object(ProviderClient, "reply", return_value=(text, calls)), self.assertRaises(UserError):
                original(client, "test-model", ["Create CRM opportunities"])

    def test_conversation_names_follow_launch_context_and_remain_stable(self):
        conversation = self.new_conversation()
        self.assertEqual(conversation.name, self.env.ref("odoo_ai_assistant.ai_partner").name)
        self.assertEqual(conversation.channel_id.name, conversation.name)
        contact = self.env["res.partner"].create({"name": "AI naming test contact"})
        result = self.conversations.start_conversation("res.partner", contact.id)
        contextual = self.conversations.browse(result["id"])
        self.assertEqual(contextual.name, "AI: AI naming test contact")
        self.assertEqual(contextual.channel_id.name, contextual.name)
        with patch.object(ProviderClient, "reply", return_value=("Odoo help.", [])):
            conversation.send_message("How to check the Odoo version")
            contextual.send_message("Tell me about Odoo contacts")
            self.assertEqual(conversation.name, self.env.ref("odoo_ai_assistant.ai_partner").name)
            self.assertEqual(conversation.channel_id.name, conversation.name)
            conversation.sudo().name = "Existing conversation title"
            self.assertFalse(conversation.title_pending)
            self.assertFalse(contextual.title_pending)
            with patch.object(ProviderClient, "conversation_title", return_value="Unwanted") as title:
                conversation.send_message("How to use Odoo CRM")
                contextual.send_message("How to use Odoo CRM")
            title.assert_not_called()
        self.assertEqual(conversation.name, "Existing conversation title")
        self.assertEqual(contextual.name, "AI: AI naming test contact")

    def test_conversation_record_naming_rejects_invalid_or_inaccessible_records(self):
        before = self.conversations.search_count([])
        for model, record_id in [
            ("missing.ai.model", 1), ("res.partner", True), ("res.partner", 0),
            ("res.partner", "1"), (None, 1), ("res.partner", None),
        ]:
            with self.assertRaises(ValidationError):
                self.conversations.start_conversation(model, record_id)
        contact = self.env["res.partner"].create({"name": "Deleted naming contact"})
        deleted_id = contact.id
        contact.unlink()
        with self.assertRaises(UserError):
            self.conversations.start_conversation("res.partner", deleted_id)
        with self.assertRaises(AccessError):
            self.conversations.start_conversation("odoo.ai.assistant.config", self.config.id)
        other_conversation = self.env["odoo.ai.assistant.conversation"].with_user(self.other).start_conversation()
        with self.assertRaises(AccessError):
            self.conversations.start_conversation("odoo.ai.assistant.conversation", other_conversation["id"])
        self.assertEqual(self.conversations.search_count([]), before)

    def test_app_discovery_is_dynamic_and_respects_native_visibility(self):
        menus = self.env["ir.ui.menu"]
        action = self.env["ir.actions.act_window"].create({
            "name": "Synthetic contacts screen", "res_model": "res.partner", "view_mode": "list,form",
        })
        root = menus.create({"name": "Synthetic Custom Workspace"})
        menus.create({"name": "Accessible Items", "parent_id": root.id,
                      "action": f"ir.actions.act_window,{action.id}"})
        menus.create({"name": "Hidden Items", "parent_id": root.id,
                      "group_ids": [(6, 0, self.env.ref("base.group_system").ids)],
                      "action": f"ir.actions.act_window,{action.id}"})
        denied_action = self.env["ir.actions.act_window"].create({
            "name": "Restricted screen", "res_model": "odoo.ai.assistant.config", "view_mode": "list,form",
        })
        menus.create({"name": "Restricted Items", "parent_id": root.id,
                      "action": f"ir.actions.act_window,{denied_action.id}"})
        hidden_root = menus.create({"name": "Hidden Custom Workspace",
                                   "group_ids": [(6, 0, self.env.ref("base.group_system").ids)]})
        menus.create({"name": "Orphaned Items", "parent_id": hidden_root.id,
                      "action": f"ir.actions.act_window,{action.id}"})
        env = self.env(user=self.owner)
        names = accessible_apps(env)
        self.assertIn(root.name, names)
        self.assertNotIn(hidden_root.name, names)
        payload = app_structure(env, {"app": root.name.lower()})
        self.assertEqual(payload["kind"], "table")
        paths = [row["menu"] for row in payload["rows"]]
        self.assertIn("Synthetic Custom Workspace / Accessible Items", paths)
        self.assertFalse(any("Hidden" in path or "Restricted" in path or "Orphaned" in path for path in paths))
        row = next(row for row in payload["rows"] if row["model"])
        self.assertEqual(row["model"], "res.partner")
        self.assertEqual(row["views"], "list,form")
        apps = app_structure(env, {"app": ""})
        self.assertIn(root.name, [row["menu"] for row in apps["rows"]])
        self.assertFalse(any(row["model"] for row in apps["rows"]))
        for args in [{"app": "Missing Synthetic App"}, {"app": 1}, {"app": "", "extra": True}, {}]:
            with self.assertRaises(ValidationError):
                app_structure(env, args)
        self.assertIn("odoo_app_structure", {tool["name"] for tool in available_tools(env)})

    def test_app_discovery_results_stay_local_and_classifier_gets_only_app_labels(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "is_odoo_topic", return_value=True) as classifier, patch.object(
            ProviderClient, "reply", return_value=("", [{"name": "odoo_app_structure", "arguments": {"app": ""}}]),
        ) as reply:
            result = conversation.send_message("Which apps are available?")[-1]
        self.assertTrue(result["local_only"])
        self.assertEqual(result["payload"]["kind"], "table")
        self.assertEqual(reply.call_count, 1)
        self.assertEqual(classifier.call_args.kwargs["app_names"], accessible_apps(self.env(user=self.owner)))
        self.assertTrue(all(isinstance(name, str) for name in classifier.call_args.kwargs["app_names"]))
        with patch.object(ProviderClient, "reply", return_value=("Odoo help.", [])) as reply:
            conversation.send_message("Explain Odoo")
        self.assertNotIn(result["payload"]["title"], json.dumps(reply.call_args.args[1]))

    def test_classifier_recognizes_dynamic_app_context_without_weakening_scope(self):
        with patch.object(ProviderClient, "reply", return_value=('{"topic":"ODOO"}', [])) as reply:
            self.assertTrue(classify_odoo_topic(
                ProviderClient("openai", "dummy"), "test",
                [{"role": "user", "content": "What is inside Dashboards App"}],
                app_names=["Dashboards", "Synthetic Custom Workspace"],
            ))
        data = json.loads(reply.call_args.args[1][0]["content"])
        self.assertEqual(data["available_apps"], ["Dashboards", "Synthetic Custom Workspace"])
        instructions = reply.call_args.kwargs["system_override"]
        self.assertIn("custom app", instructions)
        self.assertIn("Mixed requests with an unrelated part are OUTSIDE", instructions)

    def test_screen_explanations_opt_in_with_metadata_only_and_followups(self):
        conversation = self.new_conversation()
        self.config.screen_metadata = True
        private = self.env["res.partner"].create({"name": "PRIVATE CUSTOMER SENTINEL"})
        conversation._store_answer(private.name, local_only=True)
        with patch.object(ProviderClient, "reply", return_value=("Here is how these Odoo screens work.", [])) as reply:
            result = conversation.send_message("What is inside Contacts?")[-1]
        self.assertFalse(result["local_only"])
        self.assertFalse(result["payload"])
        metadata = reply.call_args.kwargs["screen_context"]
        self.assertEqual(metadata["running_version"], release.version)
        self.assertNotIn(private.name, json.dumps(reply.call_args))
        self.assertTrue(all(set(row) == {"menu", "screen", "views"} for row in metadata["menus"]))
        with patch.object(ProviderClient, "reply", return_value=("This screen helps review records.", [])) as reply:
            conversation.send_message("What does that screen do?")
        self.assertIn("Here is how these Odoo screens work.", json.dumps(reply.call_args.args[1]))
        self.assertIn("screen_context", reply.call_args.kwargs)
        self.config.screen_metadata = False
        with patch.object(ProviderClient, "reply", return_value=("Odoo help.", [])) as reply:
            conversation.send_message("Explain Odoo")
        # Without the opt-in no menus or version are sent; only the date and permitted model names remain.
        context = reply.call_args.kwargs["screen_context"]
        self.assertEqual(set(context), {"calendar", "native_schema"})

    def _field_discovery_screen(self):
        view = self.env["ir.ui.view"].create({
            "name": "AI synthetic field discovery", "model": "res.partner", "type": "form",
            "arch": """<form><sheet>
                <field name="name"/>
                <field name="email" string="Campaign Contact"/>
                <field name="phone" groups="base.group_system"/>
                <group invisible="1"><field name="street"/></group>
                <field name="zip" password="True"/>
                <field name="display_name" string="API Key"/>
                <field name="country_id" invisible="not is_company"/>
                <field name="is_company" invisible="1"/>
                <field name="image_1920"/>
            </sheet></form>""",
        })
        action = self.env["ir.actions.act_window"].create({
            "name": "Synthetic Screen", "res_model": "res.partner", "view_mode": "form", "view_id": view.id,
        })
        root = self.env["ir.ui.menu"].create({"name": "Synthetic Fields App"})
        self.env["ir.ui.menu"].create({
            "name": "Campaigns", "parent_id": root.id, "action": f"ir.actions.act_window,{action.id}",
        })
        return root

    def test_screen_field_discovery_is_view_based_and_excludes_sensitive_hidden_fields(self):
        self.config.screen_metadata = True
        root = self._field_discovery_screen()
        self.env["res.partner"].create({"name": "PRIVATE FIELD DISCOVERY VALUE", "email": "private@example.invalid"})
        result = screen_details(self.env(user=self.owner), {"app": root.name, "screen": "Campaigns"})
        fields = result["screens"][0]["fields"]
        labels = [field["label"] for field in fields]
        self.assertIn("Name", labels)
        self.assertIn("Campaign Contact", labels)
        for label in ["Phone", "Street", "Zip", "API Key", "Image"]:
            self.assertNotIn(label, labels)
        self.assertNotIn("PRIVATE FIELD DISCOVERY VALUE", json.dumps(result))
        self.assertNotIn("private@example.invalid", json.dumps(result))
        self.assertTrue(all(set(field) == {"label", "type", "required", "visibility", "views"} for field in fields))
        self.assertTrue(all(field["views"] == ["form"] for field in fields))
        self.assertTrue(any(field["visibility"] == "conditional" for field in fields))
        self.assertEqual(result["screens"][0]["views"], ["form"])
        for args in [{"app": root.name, "screen": "Missing Screen"}, {"app": "", "screen": "Campaigns"},
                     {"app": root.name, "screen": True}, {"app": root.name}, {"app": root.name, "screen": "Campaigns", "extra": 1}]:
            with self.assertRaises(ValidationError):
                screen_details(self.env(user=self.owner), args)
        self.config.screen_metadata = False
        with self.assertRaises(AccessError):
            screen_details(self.env(user=self.owner), {"app": root.name, "screen": "Campaigns"})
        self.assertNotIn("odoo_screen_details", {tool["name"] for tool in available_tools(self.env(user=self.owner))})

    def test_screen_field_explanation_is_general_and_business_results_stay_private(self):
        self.config.screen_metadata = True
        root = self._field_discovery_screen()
        conversation = self.new_conversation()
        conversation._store_answer("CONFIDENTIAL RECORD RESULT", local_only=True)
        with patch.object(ProviderClient, "reply", side_effect=[
            ("", [{"name": "odoo_screen_details", "arguments": {"app": root.name, "screen": "Campaigns"}}]),
            ("**Campaign Contact** stores a contact email.", []),
        ]) as reply:
            result = conversation.send_message("What does Campaigns contain in Synthetic Fields App?")[-1]
        self.assertFalse(result["local_only"])
        self.assertFalse(result["payload"])
        self.assertEqual(reply.call_args.args[2], [])
        self.assertNotIn("CONFIDENTIAL RECORD RESULT", str(reply.call_args_list))
        metadata = reply.call_args.kwargs["screen_context"]["screen_details"]
        self.assertIn("Campaign Contact", json.dumps(metadata))
        self.assertNotIn("API Key", json.dumps(metadata))
        self.assertIn("<strong>", result["content_html"])
        self.assertIn("odoo_screen_details", {tool["name"] for tool in available_tools(self.env(user=self.owner))})

    def test_screen_field_discovery_excludes_restricted_menus_and_settings_models(self):
        self.config.screen_metadata = True
        root = self._field_discovery_screen()
        menus = self.env["ir.ui.menu"]
        action = self.env["ir.actions.act_window"].create({
            "name": "Settings screen", "res_model": "res.config.settings", "view_mode": "form",
        })
        menus.create({"name": "Settings", "parent_id": root.id, "action": f"ir.actions.act_window,{action.id}"})
        menus.create({"name": "Hidden Settings", "parent_id": root.id,
                      "group_ids": [(6, 0, self.env.ref("base.group_system").ids)],
                      "action": f"ir.actions.act_window,{action.id}"})
        with self.assertRaises(ValidationError):
            screen_details(self.env(user=self.owner), {"app": root.name, "screen": "Hidden Settings"})
        result = screen_details(self.env, {"app": root.name, "screen": "Settings"})
        self.assertFalse(result["screens"][0]["fields"])
        self.assertIn("settings", result["screens"][0]["limitation"])

    def test_app_tool_can_explain_without_exporting_payload_or_records(self):
        conversation = self.new_conversation()
        self.config.screen_metadata = True
        with patch.object(ProviderClient, "reply", side_effect=[
            ("", [{"name": "odoo_app_structure", "arguments": {"app": ""}}]),
            ("**Available apps:** These are the screens you can use.", []),
        ]) as reply:
            result = conversation.send_message("Explain my apps")[-1]
        self.assertEqual(reply.call_count, 2)
        self.assertEqual(reply.call_args.args[2], [])
        self.assertFalse(result["local_only"])
        self.assertFalse(result["payload"])
        self.assertIn("<strong>", result["content_html"])
        self.assertEqual(reply.call_args.kwargs["screen_context"], screen_context(self.env(user=self.owner)))

    def test_version_tool_does_not_replace_comparison_with_only_runtime_version(self):
        conversation = self.new_conversation()
        self.config.screen_metadata = True
        with patch.object(ProviderClient, "reply", side_effect=[
            ("", [{"name": "odoo_instance_info", "arguments": {}}]),
            ("Odoo 19 is running here. I cannot verify Odoo 20 release features.", []),
        ]) as reply:
            result = conversation.send_message("Odoo19 vs Odoo20")[-1]
        self.assertFalse(result["local_only"])
        self.assertIn("cannot verify", result["content"])
        self.assertEqual(reply.call_count, 2)
        self.assertEqual(reply.call_args.args[2], [])
        self.assertNotIn("Local Odoo results", json.dumps(reply.call_args.args[1]))

    def test_provider_explanation_instructions_ground_menus_and_version_uncertainty(self):
        client = ProviderClient("openai", "dummy")
        response = {"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": "Odoo explanation"}],
        }]}
        with patch.object(ProviderClient, "_request", return_value=response) as request:
            client.reply("test", [{"role": "user", "content": "Explain Reporting"}], [],
                         screen_context={"menus": [{"menu": "Custom App / Reporting", "screen": "Menu group", "views": ""}]})
        instructions = request.call_args.args[2]["instructions"]
        self.assertIn("Custom App / Reporting", instructions)
        self.assertIn("never merely tell the user to reread", instructions)
        self.assertIn("Never invent release features", instructions)
        self.assertIn("not installed or accessible is still an Odoo topic", instructions)
        with patch.object(ProviderClient, "reply", return_value=('{"topic":"ODOO"}', [])) as reply:
            self.assertTrue(classify_odoo_topic(client, "test", [{"role": "user", "content": "Odoo19 vs Odoo20"}]))
        self.assertIn("Odoo19 vs Odoo20", reply.call_args.kwargs["system_override"])

    def test_reply_formatting_lists_commands_and_tables(self):
        from lxml import html
        text = (
            "## Check Odoo version\n\n"
            "- **Interface:** Open Settings or the **About Odoo** dialog.\n"
            "- **Command line:** Run `odoo --version`.\n\n"
            "```bash\nodoo --version\n./odoo-bin --version\n```\n\n"
            "[Documentation](https://www.odoo.com/documentation)\n\n"
            "| Method | Location |\n| --- | --- |\n| Logs | Startup |\n"
        )
        rendered = html.fromstring(format_reply(text))
        self.assertEqual(rendered.xpath("//h2/text()"), ["Check Odoo version"])
        self.assertEqual(len(rendered.xpath("//ul/li")), 2)
        self.assertIn("Interface:", rendered.xpath("//strong/text()"))
        self.assertIn("./odoo-bin --version", rendered.xpath("//pre")[0].text_content())
        self.assertEqual(len(rendered.xpath("//table/tbody/tr")), 1)
        self.assertEqual(rendered.xpath("//a/@rel"), ["noopener noreferrer"])

    def test_reply_formatting_escapes_html_and_blocks_remote_resources(self):
        from lxml import html
        text = (
            '<script>alert(1)</script><img src="https://example.invalid/leak" onerror="alert(2)">\n\n'
            '![tracking](https://example.invalid/pixel)\n\n'
            '[Unsafe](javascript:alert%281%29) [Data](data:text/html,evil) '
            '[Network](//example.invalid) [Local](/odoo/crm)\n\n'
            '```html\n<img src=x onerror=alert(3)>\n```\n'
        )
        rendered = html.fromstring(format_reply(text))
        self.assertFalse(rendered.xpath("//script | //img | //iframe | //style | //*[@onerror] | //*[@style]"))
        self.assertIn("tracking", rendered.text_content())
        self.assertIn("<img src=x onerror=alert(3)>", rendered.text_content())
        for href in rendered.xpath("//a/@href"):
            self.assertFalse(href.startswith(("javascript:", "data:", "//")))
        self.assertIn("/odoo/crm", rendered.xpath("//a/@href"))
        invalid = html.fromstring(format_reply("[Invalid](http://[)"))
        self.assertEqual(invalid.xpath("//a/@href"), ["#"])

    def test_chat_formats_saved_assistant_text_without_changing_history(self):
        conversation = self.new_conversation()
        text = "Use **About Odoo**.\n\n```bash\nodoo --version\n```"
        with patch.object(ProviderClient, "reply", return_value=(text, [])):
            result = conversation.send_message("How to check the Odoo version")[-1]
        self.assertEqual(result["content"], text)
        self.assertIn("<strong>About Odoo</strong>", result["content_html"])
        self.assertIn("<pre>", result["content_html"])
        stored = self.env["odoo.ai.assistant.message"].browse(result["id"])
        self.assertEqual(stored.content, text)
        self.assertTrue(result["create_date"])
        self.assertIn("<strong>About Odoo</strong>", conversation.channel_id.message_ids[0].body)
        reopened = conversation.get_messages()[-1]
        self.assertEqual(reopened["content_html"], result["content_html"])
        self.assertEqual(reopened["create_date"], result["create_date"])
        with patch.object(ProviderClient, "reply", return_value=("ok", [])) as reply:
            conversation.send_message("Thanks")
        self.assertEqual(reply.call_args.args[1][1]["content"], text)
        self.assertNotIn("content_html", str(reply.call_args))

    def test_user_and_local_results_are_not_markdown(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", return_value=("ok", [])):
            messages = conversation.send_message("**Odoo** <img src=x onerror=alert(1)>")
        self.assertNotIn("content_html", messages[0])
        conversation._store_answer("**Literal record** <img src=x>", local_only=True)
        result = conversation.get_messages()[-1]
        self.assertNotIn("content_html", result)
        self.assertEqual(result["content"], "**Literal record** <img src=x>")
        self.assertIn("&lt;img", conversation.channel_id.message_ids[0].body)

    def test_instance_info_uses_runtime_and_installed_edition(self):
        for installed in (0, 1):
            with patch.object(type(self.env["ir.module.module"]), "search_count", return_value=installed):
                result = execute_tool(self.env(user=self.owner), "odoo_instance_info", {})
            self.assertIn(f"Odoo {release.version}", result)
            self.assertIn("Enterprise Edition" if installed else "Community Edition", result)
        with self.assertRaises(ValidationError):
            execute_tool(self.env(user=self.owner), "odoo_instance_info", {"key": "no"})
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", return_value=("", [{
            "name": "odoo_instance_info", "arguments": {},
        }])):
            result = conversation.send_message("What is the Odoo version")[-1]
        self.assertIn(f"Odoo {release.version}", result["content"])
        self.assertEqual(result["execution_steps"][-1], "Reading running Odoo instance information")

    def test_unrelated_question_is_refused_without_reply_or_tools(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "is_odoo_topic", return_value=False), patch.object(
            ProviderClient, "reply",
        ) as reply, patch(
            "odoo.addons.odoo_ai_assistant.models.conversation.execute_tool",
        ) as tool:
            messages = conversation.send_message("Write a movie review")
        reply.assert_not_called()
        tool.assert_not_called()
        self.assertIn("I can only assist with Odoo", messages[-1]["content"])

    def test_topic_classifier_exact_protocol_and_payload(self):
        with patch.object(ProviderClient, "reply", return_value=("OUTSIDE", [])) as reply:
            self.assertFalse(classify_odoo_topic(ProviderClient("openrouter", "dummy"), "test", [
                {"role": "user", "content": "Tell a joke"},
            ]))
            self.assertIn("untrusted data", reply.call_args.kwargs["system_override"])
            self.assertEqual(reply.call_args.args[2], [])
        with patch.object(ProviderClient, "reply", return_value=("probably ODOO", [])), self.assertRaises(UserError):
            classify_odoo_topic(ProviderClient("openrouter", "dummy"), "test", [])

    def test_standalone_greetings_are_in_scope_but_mixed_requests_are_classified(self):
        client = ProviderClient("openrouter", "dummy")
        for greeting in ("Hi", "Hi how are you", "Good morning!", "What's up?"):
            with self.subTest(greeting=greeting), patch.object(ProviderClient, "reply") as reply:
                self.assertTrue(classify_odoo_topic(client, "test-model", [
                    {"role": "user", "content": "Tell me a joke"},
                    {"role": "assistant", "content": "I can only assist with Odoo."},
                    {"role": "user", "content": greeting},
                ]))
                reply.assert_not_called()
        with patch.object(ProviderClient, "reply", return_value=("OUTSIDE", [])) as reply:
            self.assertFalse(classify_odoo_topic(client, "test-model", [
                {"role": "user", "content": "Hi, tell me a joke"},
            ]))
        self.assertIn("Greetings and clarification", reply.call_args.kwargs["system_override"])

    def test_greeting_uses_small_request_without_history_tools_or_metadata(self):
        conversation = self.new_conversation()
        self.config.write({"screen_metadata": True})
        self.env["odoo.ai.assistant.message"].sudo().create({
            "conversation_id": conversation.id, "role": "user", "content": "x" * 12000,
        })
        with patch.object(ProviderClient, "reply", return_value=("Hello! How can I help with Odoo?", [])) as reply, \
                patch.object(ProviderClient, "requires_action") as action, \
                patch.object(ProviderClient, "conversation_title") as title, \
                patch("odoo.addons.odoo_ai_assistant.models.conversation.available_tools") as tools, \
                patch.object(type(conversation), "_reply_context") as context:
            messages = conversation.send_message("Hi")
        reply.assert_called_once()
        self.assertEqual(reply.call_args.args[1:], ([{"role": "user", "content": "Hi"}], []))
        self.assertEqual(set(reply.call_args.kwargs), {"system_override"})
        self.assertLess(len(reply.call_args.kwargs["system_override"]), 400)
        for unused in (action, title, tools, context):
            unused.assert_not_called()
        self.assertIn("How can I help with Odoo?", messages[-1]["content"])
        self.assertEqual(conversation.name, "Odoo AI")

    def test_greeting_request_failure_and_invalid_replies_are_explicit(self):
        for answer in (("", []), ("Unexpected", [{"name": "odoo_instance_info", "arguments": {}}])):
            with self.subTest(answer=answer), patch.object(ProviderClient, "reply", return_value=answer), \
                    self.assertRaisesRegex(UserError, "valid greeting"):
                self.new_conversation().send_message("Hello")
        with patch.object(ProviderClient, "reply", side_effect=UserError("Provider unavailable")), \
                self.assertRaisesRegex(UserError, "Provider unavailable"):
            self.new_conversation().send_message("Hi")

    def test_legacy_greeting_payload_stays_below_600_characters(self):
        from ..models import providers
        self.addCleanup(providers._OPENAI_CHAT_COMPLETIONS.discard, "gpt-3.5-turbo")
        self.model.identifier = "gpt-3.5-turbo"
        self.config.write({"screen_metadata": True})
        rejected = Mock(status_code=400)
        rejected.json.return_value = {"error": {"type": "invalid_request_error", "message": "unsupported"}}
        answered = Mock(status_code=200)
        answered.json.return_value = {"choices": [{"message": {"content": "Hello! How can I help with Odoo?"}}]}
        with patch("requests.request", side_effect=lambda method, url, **kw: (
            rejected if url.endswith("/responses") else answered
        )) as request, patch.object(type(self.env["odoo.ai.assistant.usage"]), "_observer", return_value=None):
            self.new_conversation().send_message("Hi")
        self.assertTrue(request.call_args.args[1].endswith("/chat/completions"))
        payload = request.call_args.kwargs["json"]
        self.assertLess(len(json.dumps(payload)), 600)
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": "Hi"})

    def test_openai_topic_classifier_structured_protocol(self):
        history = [
            {"role": "user", "content": "How do I configure CRM in Odoo?"},
            {"role": "assistant", "content": "You can configure CRM from Odoo Settings."},
            {"role": "user", "content": "How do I configure CRM in Odoo?"},
        ]
        for topic in ("ODOO", "OUTSIDE"):
            response = {"status": "completed", "output": [{
                "type": "message", "content": [{"type": "output_text", "text": json.dumps({"topic": topic})}],
            }]}
            with self.subTest(topic=topic), patch.object(ProviderClient, "_request", return_value=response) as request:
                self.assertEqual(
                    classify_odoo_topic(ProviderClient("openai", "dummy"), "test", history),
                    topic == "ODOO",
                )
                payload = request.call_args.args[2]
                self.assertEqual(json.loads(payload["input"][0]["content"]), {
                    "dialogue": history, "available_apps": [],
                })
                self.assertEqual(len(payload["input"]), 1)
                self.assertNotIn("tools", payload)
                self.assertFalse(payload["store"])
                output_format = payload["text"]["format"]
                self.assertEqual(output_format["type"], "json_schema")
                self.assertTrue(output_format["strict"])
                self.assertEqual(output_format["schema"]["properties"]["topic"]["enum"], ["ODOO", "OUTSIDE"])
        for text, calls in [
            ("ODOO", []), ("{}", []), ('{"topic": true}', []),
            ('{"topic": "probably ODOO"}', []), ('{"topic":"ODOO","extra":1}', []),
            ('{"topic":"ODOO"}', [{"name": "odoo_instance_info", "arguments": "{}"}]),
        ]:
            with self.subTest(text=text, calls=calls), patch.object(
                ProviderClient, "reply", return_value=(text, calls),
            ), self.assertRaises(UserError):
                classify_odoo_topic(ProviderClient("openai", "dummy"), "test", history)

    def test_usage_is_admin_only(self):
        with self.assertRaises(AccessError):
            self.env["odoo.ai.assistant.usage"].with_user(self.owner).search([])

    def test_provider_token_shapes(self):
        cases = [
            ("openai", {"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                 "prompt_tokens_details": {"cached_tokens": 30}}}, (100, 20, 30, 0, 120)),
            ("openrouter", {"usage": {"prompt_tokens": 100, "completion_tokens": 20}}, (100, 20, 0, 0, 120)),
            ("anthropic", {"usage": {"input_tokens": 100, "output_tokens": 20,
                                    "cache_read_input_tokens": 30, "cache_creation_input_tokens": 10}},
             (130, 20, 30, 10, 160)),
            ("gemini", {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20,
                                         "thoughtsTokenCount": 5, "cachedContentTokenCount": 30}},
             (100, 25, 30, 0, 125)),
        ]
        for provider, response, expected in cases:
            with self.subTest(provider=provider):
                client = ProviderClient(provider, "dummy")
                counts = client._tokens(response)
                self.assertEqual(tuple(counts[key] for key in (
                    "input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "total_tokens",
                )), expected)
                self.assertIsNone(client._tokens({}))
        self.assertIsNone(ProviderClient("openai", "dummy")._tokens({
            "usage": {"prompt_tokens": -1, "completion_tokens": 20},
        }))

    def test_usage_records_requests_and_missing_counts(self):
        ledger = self.env["odoo.ai.assistant.usage"]
        def persist(record, values, usage_id=None):
            if usage_id:
                record.sudo().browse(usage_id).write(values)
                return usage_id
            return record.sudo().create(values).id
        with patch.object(type(ledger), "_persist", persist), patch("requests.request") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                          "prompt_tokens_details": {"cached_tokens": 30}},
            }
            client = ProviderClient("openai", "dummy", ledger._observer("openai", "test-model", "reply"))
            client._request("POST", "/chat/completions", {})
            row = ledger.search([], limit=1)
            self.assertEqual(row.total_tokens, 120)
            self.assertTrue(row.tokens_known)
            self.assertEqual(row.api_calls, 1)
            request.return_value.json.return_value = {}
            client._request("POST", "/chat/completions", {})
            row = ledger.search([], limit=1)
            self.assertFalse(row.tokens_known)
            request.return_value.status_code = 401
            try:
                client._request("POST", "/chat/completions", {})
            except UserError:
                self.assertEqual(ledger.search([], limit=1).status, "failed")
            else:
                self.fail("Expected an explicit provider HTTP error")

    def test_per_user_gate_and_secret_restriction(self):
        self.owner.ai_assistant_enabled = False
        self.assertFalse(self.conversations.bootstrap()["enabled"])
        with self.assertRaises(AccessError):
            self.conversations.start_conversation()
        with self.assertRaises(AccessError):
            self.owner.with_user(self.owner).write({"ai_assistant_enabled": True})
        with self.assertRaises(AccessError):
            self.config.with_user(self.owner).read(["api_key"])

    def test_owner_isolation(self):
        conversation = self.new_conversation()
        with self.assertRaises(AccessError):
            conversation.with_user(self.other).get_messages()
        with self.assertRaises(AccessError):
            conversation.with_user(self.other).send_message("Hello")
        with self.assertRaises(AccessError):
            conversation.with_user(self.other).unlink()
        self.assertFalse(conversation.with_user(self.other).search([]))
        with self.assertRaises(AccessError):
            conversation.write({"user_id": self.other.id})

    def test_local_tool_result_is_not_sent_back_to_provider(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", return_value=("", [{
            "name": "odoo_chart", "arguments": self.chart_args(),
        }])) as reply:
            messages = conversation.send_message("Chart contacts by city")
        reply.assert_called_once()
        self.assertTrue(messages[-1]["local_only"])
        self.assertEqual(conversation.channel_id.message_ids[0].author_id, self.env.ref("odoo_ai_assistant.ai_partner"))

    def test_unread_clears_when_conversation_is_opened(self):
        conversation = self.new_conversation()
        conversation._store_answer("New answer")
        entry = next(c for c in self.conversations.bootstrap()["conversations"] if c["id"] == conversation.id)
        self.assertTrue(entry["unread"])
        self.assertEqual(entry["preview"], "New answer")
        self.assertTrue(entry["date"])
        conversation.get_messages()
        entry = next(c for c in self.conversations.bootstrap()["conversations"] if c["id"] == conversation.id)
        self.assertFalse(entry["unread"])
        self.assertEqual(conversation.channel_id.self_member_id.seen_message_id, conversation.channel_id.message_ids[0])
        with self.assertRaises(AccessError):
            conversation.with_user(self.other).get_messages()

    def test_local_results_excluded_from_later_history(self):
        conversation = self.new_conversation()
        contact = self.env["res.partner"].create({"name": "Confidential test contact", "city": "Private city"})
        payload = execute_chart(self.env(user=self.owner), self.chart_args(domain=[["id", "=", contact.id]]))
        conversation._store_answer(json.dumps(payload), local_only=True, payload=payload)
        with patch.object(ProviderClient, "reply", return_value=("Hello!", [])) as reply:
            messages = conversation.send_message("Hello")
        history = reply.call_args.args[1]
        self.assertNotIn("Private city", json.dumps(history))
        self.assertEqual(history, [{"role": "user", "content": "Hello"}])
        self.assertEqual(messages[-1]["content"], "Hello!")

    def test_reading_history_preserves_latest_message_order(self):
        older = self.new_conversation()
        newer = self.new_conversation()
        older._store_answer("Older answer")
        newer._store_answer("Newer answer")
        self.env["odoo.ai.assistant.message"].search([
            ("conversation_id", "=", older.id),
        ]).write({"create_date": "2020-01-01 00:00:00"})
        self.env["odoo.ai.assistant.message"].search([
            ("conversation_id", "=", newer.id),
        ]).write({"create_date": "2021-01-01 00:00:00"})
        older.get_messages()
        entries = self.conversations.bootstrap()["conversations"]
        self.assertEqual([entry["id"] for entry in entries], [newer.id, older.id])

    def test_automatic_tools_execute_once_without_external_results(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", return_value=("", [{
            "name": "odoo_chart", "arguments": json.dumps(self.chart_args()),
        }])) as reply:
            messages = conversation.send_message("Chart contacts by city")
        self.assertEqual(reply.call_count, 1)
        self.assertTrue(messages[-1]["local_only"])

    def test_disabled_tools_are_rejected(self):
        conversation = self.new_conversation()
        self.config.local_tools = False
        # A tool call while tools are disabled is refused: nothing runs, and after one retry the
        # user gets the reason as an answer instead of an error.
        with patch.object(ProviderClient, "reply", return_value=("", [{
            "name": "odoo_chart", "arguments": self.chart_args(),
        }])) as reply, patch("odoo.addons.odoo_ai_assistant.models.conversation.execute_chart") as tool:
            messages = conversation.send_message("Chart contacts")
        tool.assert_not_called()
        self.assertEqual(reply.call_count, 2)
        self.assertTrue(messages[-1]["local_only"])
        self.assertIn("couldn't complete this request", messages[-1]["content"])

    def test_invented_field_gets_one_retry_then_a_plain_answer(self):
        conversation = self.new_conversation()
        with patch.object(type(conversation), "_dispatch_calls", side_effect=ValueError(
            "Invalid field product.product.reorder_minimum",
        )), patch.object(ProviderClient, "reply", return_value=("", [{"name": "odoo_instance_info", "arguments": {}}])):
            messages = conversation.send_message("Which products are below their reorder point?")
        self.assertIn("reorder_minimum", messages[-1]["content"])
        self.assertTrue(messages[-1]["local_only"])

    def test_invented_field_retry_lists_real_field_names(self):
        conversation = self.new_conversation()
        hint = conversation._field_hint("Invalid field res.partner.no_such_field in condition")
        self.assertIn("Fields available on res.partner", hint)
        self.assertIn("name", hint)
        self.assertEqual(conversation._field_hint("Invalid field res.users.login"), "")  # restricted model
        self.assertEqual(conversation._field_hint("Some other error"), "")

    def test_refused_tool_call_is_retried_with_the_reason(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", side_effect=[
            ("", [{"name": "mcp_call_model_method", "arguments": {}}]),
            ("Here is what I can tell you about Odoo invoices.", []),
        ]) as reply:
            messages = conversation.send_message("Which invoices are unpaid?")
        retry = reply.call_args_list[1].args[1]
        self.assertIn("could not run", retry[-1]["content"])
        self.assertEqual(messages[-1]["content"], "Here is what I can tell you about Odoo invoices.")

    def test_private_channel_rejects_extra_members(self):
        conversation = self.new_conversation()
        with self.cr.savepoint(), self.assertRaises(AccessError):
            self.env["discuss.channel.member"].create({
                "channel_id": conversation.channel_id.id, "partner_id": self.other.partner_id.id,
            })

    def test_discuss_messages_receive_reply(self):
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", return_value=("Discuss reply", [])):
            conversation.channel_id.message_post(
                body="Hello from Discuss", message_type="comment", subtype_xmlid="mail.mt_comment",
            )
        self.assertEqual(conversation.get_messages()[-1]["content"], "Discuss reply")

    def test_discuss_cannot_spoof_bot_or_change_privacy(self):
        conversation = self.new_conversation()
        with self.assertRaises(AccessError):
            conversation.channel_id.message_post(
                body="Forged answer", author_id=self.env.ref("odoo_ai_assistant.ai_partner").id,
                message_type="comment",
            )
        with self.assertRaises(AccessError):
            conversation.channel_id.sudo().write({"channel_type": "channel"})
        with self.assertRaises(AccessError):
            conversation.channel_id.channel_member_ids.sudo().unlink()

    def test_discuss_rejects_attachments(self):
        conversation = self.new_conversation()
        with self.assertRaises(UserError):
            conversation.channel_id.message_post(
                body="Read this file", attachments=[("test.txt", b"test")], message_type="comment",
            )

    def test_provider_tool_formats(self):
        call = {"name": "odoo_app_structure", "arguments": {"app": "Example"}}
        responses = {
            "openai": {"status": "completed", "output": [{
                "type": "function_call", "name": call["name"], "arguments": json.dumps(call["arguments"]),
            }]},
            "openrouter": {"choices": [{"message": {"tool_calls": [{
                "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
            }]}}]},
            "anthropic": {"content": [{"type": "tool_use", "name": call["name"],
                                      "input": call["arguments"]}]},
            "gemini": {"candidates": [{"content": {"parts": [{
                "functionCall": {"name": call["name"], "args": call["arguments"]},
            }]}}]},
        }
        for provider, response in responses.items():
            with self.subTest(provider=provider), patch.object(ProviderClient, "_request", return_value=response):
                _, calls = ProviderClient(provider, "dummy").reply(
                    "test-model", [{"role": "user", "content": "Find Example"}], TOOLS,
                )
                self.assertEqual(calls[0]["name"], call["name"])
                arguments = calls[0]["arguments"]
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                self.assertEqual(arguments, call["arguments"])

    def test_tool_argument_validation(self):
        env = self.env(user=self.owner)
        with self.assertRaises(ValidationError):
            execute_tool(env, "odoo_app_structure", {"app": "", "domain": []})
        with self.assertRaises(ValidationError):
            execute_tool(env, "odoo_app_structure", '{"app":')
        with self.assertRaises(AccessError):
            execute_tool(env, "delete_records", {})

    def test_record_rules_apply_to_tools(self):
        private = self.env["res.partner"].create({"name": "AI inaccessible record", "city": "Hidden city"})
        self.env["res.partner"].create({"name": "AI accessible record", "city": "Visible city"})
        self.env["ir.rule"].create({
            "name": "Test inaccessible contact",
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "domain_force": "[('id', '!=', %s)]" % private.id,
        })
        payload = execute_chart(self.env(user=self.owner), self.chart_args(domain=[["name", "like", "AI "]]))
        self.assertIn("Visible city", payload["labels"])
        self.assertNotIn("Hidden city", payload["labels"])

    def test_provider_formats(self):
        responses = {
            "openai": {"status": "completed", "output": [{
                "type": "message", "content": [{"type": "output_text", "text": "ok"}],
            }]},
            "openrouter": {"choices": [{"message": {"content": "ok"}}]},
            "anthropic": {"content": [{"type": "text", "text": "ok"}]},
            "gemini": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        }
        for provider, response in responses.items():
            with self.subTest(provider=provider), patch.object(
                ProviderClient, "_request", return_value=response,
            ) as request:
                answer, calls = ProviderClient(provider, "dummy").reply(
                    "test-model", [{"role": "user", "content": "hello"}], TOOLS,
                )
                self.assertEqual(answer, "ok")
                self.assertEqual(calls, [])
                self.assertIn("tools", request.call_args.args[2])
                if provider == "openai":
                    self.assertEqual(request.call_args.args[1], "/responses")
                    self.assertFalse(request.call_args.args[2]["store"])
                    self.assertTrue(request.call_args.args[2]["tools"][0]["strict"])
                ProviderClient(provider, "dummy").reply(
                    "test-model", [{"role": "user", "content": "hello"}], [],
                )
                self.assertNotIn("tools", request.call_args.args[2])

    def test_gemini_selected_chart_uses_auto_mode_and_preserves_json_schema(self):
        schema = {
            "type": "object", "properties": {
                "group_by": {"type": "string", "enum": ["", "stage_id"]},
                "filters": {"type": "array", "maxItems": 8, "items": {
                    "type": "object", "properties": {
                        "value": {"anyOf": [{"type": "string"}, {"type": "number"}]},
                    }, "required": ["value"], "additionalProperties": False,
                }},
            }, "required": ["group_by", "filters"], "additionalProperties": False,
        }
        chart = {"name": "odoo_chart", "description": "Chart CRM stages.", "inputSchema": schema}
        arguments = {"group_by": "stage_id", "filters": []}
        response = {"candidates": [{"content": {"parts": [{
            "functionCall": {"name": chart["name"], "args": arguments},
        }]}}]}
        with patch.object(ProviderClient, "_request", return_value=response) as request:
            text, calls = ProviderClient("gemini", "dummy").reply(
                "test-model", [{"role": "user", "content": "Bar chart of CRM stages"}],
                [*TOOLS, chart], tool_choice=chart["name"],
            )
        payload = request.call_args.args[2]
        self.assertNotIn("toolConfig", payload)
        declarations = payload["tools"][0]["functionDeclarations"]
        self.assertEqual([tool["name"] for tool in declarations], [chart["name"]])
        self.assertEqual(declarations[0]["parametersJsonSchema"], schema)
        self.assertNotIn("parameters", declarations[0])
        self.assertIn("You must call the function odoo_chart",
                      payload["systemInstruction"]["parts"][0]["text"])
        self.assertEqual((text, calls), ("", [{"name": chart["name"], "arguments": arguments}]))

    def test_gemini_selected_tool_rejects_missing_or_wrong_calls(self):
        for parts in (
            [{"text": "Here is your chart."}],
            [{"functionCall": {"name": "odoo_instance_info", "args": {}}}],
            [{"functionCall": {"name": "odoo_app_structure", "args": {"app": ""}}},
             {"functionCall": {"name": "odoo_instance_info", "args": {}}}],
        ):
            with self.subTest(parts=parts), patch.object(ProviderClient, "_request", return_value={
                "candidates": [{"content": {"parts": parts}}],
            }), self.assertRaisesRegex(UserError, "did not run the required local chart tool"):
                ProviderClient("gemini", "dummy").reply(
                    "test-model", [{"role": "user", "content": "Find Odoo contacts"}],
                    TOOLS, tool_choice="odoo_app_structure",
                )
        with patch.object(ProviderClient, "_request") as request, self.assertRaisesRegex(
            UserError, "required local tool is not available",
        ):
            ProviderClient("gemini", "dummy").reply(
                "test-model", [{"role": "user", "content": "Find Odoo contacts"}],
                TOOLS, tool_choice="missing_tool",
            )
        request.assert_not_called()

    def test_openai_responses_usage_and_invalid_output(self):
        client = ProviderClient("openai", "dummy")
        counts = client._tokens({"usage": {
            "input_tokens": 100, "output_tokens": 25,
            "input_tokens_details": {"cached_tokens": 30},
            "output_tokens_details": {"reasoning_tokens": 5},
        }})
        self.assertEqual(counts["total_tokens"], 125)
        self.assertEqual(counts["cached_tokens"], 30)
        cases = [
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": "invalid"},
            {"status": "completed", "output": [{"type": "function_call", "name": "bad", "arguments": {}}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
        ]
        for response in cases:
            with self.subTest(response=response), patch.object(
                ProviderClient, "_request", return_value=response,
            ), self.assertRaises(UserError):
                client.reply("test-model", [{"role": "user", "content": "Odoo help"}], [])

    def _openai_fake(self, rejected_paths):
        """A fake _request where the given OpenAI paths fail as an unsupported (400) request."""
        from ..models.providers import ProviderError

        def fake(client, method, path, payload=None):
            if path in rejected_paths:
                raise ProviderError("unsupported", kind="request", status=400)
            if path == "/chat/completions":
                return {"choices": [{"message": {"content": "ok"}}]}
            return {"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]},
            ]}
        return fake

    def test_openai_models_without_responses_api_fall_back_and_are_remembered(self):
        from ..models import providers
        for model in ("gpt-3.5-turbo", "gpt-4"):
            self.addCleanup(providers._OPENAI_CHAT_COMPLETIONS.discard, model)
            client = ProviderClient("openai", "dummy")
            with self.subTest(model=model), patch.object(
                ProviderClient, "_request", autospec=True, side_effect=self._openai_fake({"/responses"}),
            ) as request:
                self.assertEqual(client.reply(model, [{"role": "user", "content": "hello"}], []), ("ok", []))
                self.assertEqual([call.args[2] for call in request.call_args_list], ["/responses", "/chat/completions"])
                system = request.call_args.args[3]["messages"][0]
                self.assertEqual(system["role"], "system")
                self.assertIn("For a standalone greeting, ignore earlier scope refusals", system["content"])
                # Remembered: the next request goes straight to Chat Completions, and the
                # classifiers stop asking this model for a JSON schema.
                request.reset_mock()
                client.reply(model, [{"role": "user", "content": "again"}], [])
                self.assertEqual([call.args[2] for call in request.call_args_list], ["/chat/completions"])
                self.assertFalse(client._structured_output(model))

    def test_modern_openai_models_stay_on_responses_api(self):
        from ..models import providers
        client = ProviderClient("openai", "dummy")
        for model in ("gpt-4o-mini", "gpt-5.4", "o4-mini"):
            with self.subTest(model=model), patch.object(
                ProviderClient, "_request", autospec=True, side_effect=self._openai_fake(set()),
            ) as request:
                client.reply(model, [{"role": "user", "content": "hello"}], [])
                self.assertEqual([call.args[2] for call in request.call_args_list], ["/responses"])
                self.assertTrue(client._structured_output(model))
                self.assertNotIn(model, providers._OPENAI_CHAT_COMPLETIONS)

    def test_answer_length_limit_applies_to_openai_and_openrouter(self):
        from ..models import providers
        from ..models.providers import ProviderError
        self.addCleanup(providers._OPENAI_CHAT_COMPLETIONS.discard, "gpt-4")
        providers._OPENAI_CHAT_COMPLETIONS.add("gpt-4")
        for provider, model, path, key in (
            ("openai", "gpt-4o", "/responses", "max_output_tokens"),
            ("openai", "gpt-4", "/chat/completions", "max_completion_tokens"),
            ("openrouter", "openai/gpt-4o", "/chat/completions", "max_tokens"),
        ):
            client = ProviderClient(provider, "dummy", max_output_tokens=777)
            with self.subTest(provider=provider, model=model), patch.object(
                ProviderClient, "_request", autospec=True, side_effect=self._openai_fake(set()),
            ) as request:
                client.reply(model, [{"role": "user", "content": "hello"}], [])
                self.assertEqual(request.call_args.args[2], path)
                self.assertEqual(request.call_args.args[3][key], 777)
        # Hitting the limit with nothing usable explains the setting instead of a generic failure.
        client = ProviderClient("openai", "dummy")
        for model, response in (
            ("gpt-4o", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []}),
            ("gpt-4", {"choices": [{"finish_reason": "length", "message": {"content": "", "tool_calls": []}}]}),
        ):
            with self.subTest(model=model), patch.object(ProviderClient, "_request", return_value=response), \
                    self.assertRaises(ProviderError) as caught:
                client.reply(model, [{"role": "user", "content": "hello"}], [])
            self.assertEqual(caught.exception.kind, "length")

    def test_openai_fallback_ignores_unrelated_errors_and_failed_retries(self):
        from ..models import providers
        from ..models.providers import ProviderError
        client = ProviderClient("openai", "dummy")
        # Account or size problems are not a missing feature: no Chat Completions retry.
        for kind, status in (("billing", 400), ("context", 400), ("auth", 401), ("rate", 429)):
            with self.subTest(kind=kind), patch.object(ProviderClient, "_request", side_effect=ProviderError(
                kind, kind=kind, status=status,
            )) as request, self.assertRaises(ProviderError):
                client.reply("gpt-4o", [{"role": "user", "content": "hello"}], [])
            # Only the Responses API was tried (context errors still shrink and retry on the same API).
            self.assertTrue(all(call.args[1] == "/responses" for call in request.call_args_list))
            if kind != "context":
                self.assertEqual(request.call_count, 1)
        # A rejected retry keeps the original error and does not downgrade the model.
        with patch.object(ProviderClient, "_request", autospec=True,
                          side_effect=self._openai_fake({"/responses", "/chat/completions"})), \
                self.assertRaisesRegex(ProviderError, "unsupported"):
            client.reply("gpt-4.1", [{"role": "user", "content": "hello"}], [])
        self.assertNotIn("gpt-4.1", providers._OPENAI_CHAT_COMPLETIONS)

    def test_empty_provider_answer_is_retried_once(self):
        from ..models.providers import ProviderError
        empty = ProviderError("empty", kind="empty")
        with patch.object(ProviderClient, "_reply_once", side_effect=[empty, ("ok", [])]) as once:
            self.assertEqual(ProviderClient("gemini", "dummy").reply("m", [{"role": "user", "content": "hi"}], []),
                             ("ok", []))
        self.assertEqual(once.call_count, 2)
        with patch.object(ProviderClient, "_reply_once", side_effect=[empty, empty]),                 self.assertRaisesRegex(UserError, "empty"):
            ProviderClient("gemini", "dummy").reply("m", [{"role": "user", "content": "hi"}], [])

    def test_classifier_json_tolerates_code_fences(self):
        self.assertEqual(ProviderClient._json_object('```json\n{"topic": "ODOO"}\n```'), {"topic": "ODOO"})
        self.assertEqual(ProviderClient._json_object('{"intent":"READ"}'), {"intent": "READ"})

    def test_provider_error_is_actionable_without_echoing_body(self):
        response = Mock()
        response.status_code = 400
        response.json.return_value = {"error": {
            "code": "context_length_exceeded", "message": "private prompt text",
        }}
        with patch("requests.request", return_value=response):
            with self.assertRaises(UserError) as error:
                ProviderClient("openai", "dummy").models()
        self.assertIn("HTTP 400", str(error.exception))
        self.assertIn("context_length_exceeded", str(error.exception))
        self.assertIn("larger context window", str(error.exception))
        self.assertNotIn("private prompt text", str(error.exception))

    def test_google_provider_error_exposes_only_safe_status_identifier(self):
        response = Mock()
        response.status_code = 400
        response.json.return_value = {"error": {
            "code": 400, "status": "INVALID_ARGUMENT", "message": "private prompt text",
        }}
        with patch("requests.request", return_value=response):
            with self.assertRaises(UserError) as error:
                ProviderClient("gemini", "dummy").models()
        self.assertIn("INVALID_ARGUMENT", str(error.exception))
        self.assertNotIn("private prompt text", str(error.exception))

    def test_history_window_starts_with_user(self):
        conversation = self.new_conversation()
        self.env["odoo.ai.assistant.message"].create([
            {"conversation_id": conversation.id, "role": "user" if index % 2 == 0 else "assistant",
             "content": f"History {index}"}
            for index in range(40)
        ])
        with patch.object(ProviderClient, "reply", return_value=("ok", [])) as reply:
            conversation.send_message("Latest question")
        history = reply.call_args.args[1]
        self.assertEqual(len(history), 29)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[-1]["content"], "Latest question")
        self.assertNotIn("History 0", [m["content"] for m in history])

    def test_provider_errors_are_explicit(self):
        with patch("requests.request") as request:
            request.return_value.status_code = 401
            with self.assertRaises(UserError):
                ProviderClient("openai", "dummy").models()

    def test_catalog_refresh(self):
        with patch.object(ProviderClient, "models", return_value=[("fresh", "Fresh model")]):
            self.env["odoo.ai.assistant.provider.model"]._refresh("openai", "dummy")
        self.assertFalse(self.model.active)
        self.assertTrue(self.env["odoo.ai.assistant.provider.model"].search([("identifier", "=", "fresh")]))

    def test_settings_open_preserves_key_and_provider_change_clears_it(self):
        with patch.object(ProviderClient, "models") as catalogue:
            settings = Form(self.env["res.config.settings"])
            self.assertEqual(settings.ai_assistant_api_key, "not-a-real-key")
            self.assertEqual(settings.ai_assistant_model_id, self.model)
            catalogue.assert_not_called()
            settings.ai_assistant_provider = "anthropic"
            self.assertFalse(settings.ai_assistant_api_key)
            self.assertFalse(settings.ai_assistant_model_id)
            catalogue.assert_not_called()

    def test_delete_removes_discuss_history(self):
        conversation = self.new_conversation()
        channel = conversation.channel_id
        conversation.unlink()
        self.assertFalse(channel.exists())

    def test_demo_creation_feature_is_not_registered(self):
        self.assertNotIn("odoo.ai.assistant.demo.batch", self.env)
        self.assertNotIn("demo_creation", self.config._fields)
        self.assertNotIn("ai_assistant_demo_access", self.owner._fields)
        self.assertNotIn("plan_demo_data", {
            tool["name"] for tool in available_tools(self.env(user=self.owner))
        })

    def test_chart_any_enabled_model_types_and_open_in_odoo(self):
        env = self.env(user=self.owner)
        self.env["res.partner"].create([
            {"name": "AI chart fixture one", "city": "Alpha", "color": 3},
            {"name": "AI chart fixture two", "city": "Alpha", "color": 7},
            {"name": "AI chart fixture three", "city": "Beta", "color": 5},
        ])
        fixtures = [["name", "like", "AI chart fixture"]]
        for chart in ("bar", "line", "pie", "doughnut", "table"):
            with self.subTest(chart=chart):
                payload = execute_chart(env, self.chart_args(domain=fixtures, chart_type=chart))
                if chart == "table":
                    self.assertEqual(payload["rows"], [{"label": "Alpha", "value": 2}, {"label": "Beta", "value": 1}])
                else:
                    self.assertEqual(dict(zip(payload["labels"], payload["values"])), {"Alpha": 2, "Beta": 1})
        totals = execute_chart(env, self.chart_args(domain=fixtures, operation="sum", measure="color"))
        self.assertEqual(dict(zip(totals["labels"], totals["values"])), {"Alpha": 10, "Beta": 5})
        averages = execute_chart(env, self.chart_args(domain=fixtures, operation="avg", measure="color"))
        self.assertEqual(dict(zip(averages["labels"], averages["values"])), {"Alpha": 5, "Beta": 5})
        self.assertEqual(totals["open"], {"model": "res.partner", "domain": fixtures, "group_by": "city",
                                          "measure": "color", "mode": "bar"})
        scatter = execute_chart(env, self.chart_args(domain=fixtures, group_by="color", chart_type="scatter"))
        self.assertEqual(scatter["open"]["mode"], "line")
        by_month = execute_chart(env, self.chart_args(domain=fixtures, group_by="create_date", chart_type="line"))
        self.assertEqual(sum(by_month["values"]), 3)
        self.assertEqual(by_month["open"]["group_by"], "create_date:month")
        self.assertNotIn(":", by_month["labels"][0])  # "October 2026", as in Odoo's graph view
        json.dumps(by_month, allow_nan=False)
        # Omitted optional arguments use their defaults; invented ones are ignored.
        minimal = {key: value for key, value in self.chart_args().items() if key != "domain"}
        self.assertEqual(execute_chart(env, {**minimal, "limit": 5})["kind"], "chart")

    def test_chart_validates_fields_from_odoo_metadata(self):
        env = self.env(user=self.owner)
        with self.assertRaisesRegex(ValidationError, "Groupable fields: .*city"):
            execute_chart(env, self.chart_args(group_by="favourite_colour"))
        with self.assertRaisesRegex(ValidationError, "Numeric fields: .*color"):
            execute_chart(env, self.chart_args(operation="sum", measure="name"))
        with self.assertRaises(ValidationError):
            execute_chart(env, self.chart_args(measure="color"))
        with self.assertRaises(ValidationError):
            execute_chart(env, self.chart_args(group_by="city", chart_type="scatter"))
        for invalid in ({"chart_type": "radar"}, {"operation": "max"}, {"title": ""}, {"domain": "all"},
                        {"interval": "hour"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                execute_chart(env, self.chart_args(**invalid))
        with self.assertRaises(AccessError):
            execute_chart(env, self.chart_args(domain=[["api_key", "=", "x"]]))
        # Only models enabled for MCP reads, and never technical or user-security models.
        self.env["mcp.enabled.model"].create({"model_id": self.env["ir.model"]._get_id("res.users"), "allow_read": True})
        for model in ("res.country", "res.users"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                execute_chart(env, self.chart_args(model=model, group_by="name"))
        chart = next(tool for tool in available_tools(env) if tool["name"] == "odoo_chart")
        self.assertEqual(chart["inputSchema"]["properties"]["model"]["enum"], ["res.partner"])
        self.assertFalse(chart["strict"])

    def test_chart_rules_limits_and_local_history(self):
        rows = self.env["res.partner"].create([
            {"name": f"AI chart limit {index}", "city": f"Group {index}"} for index in range(55)
        ])
        self.env["ir.rule"].create({
            "name": "AI chart record rule", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "domain_force": "[('id', '!=', %s)]" % rows[0].id,
        })
        args = self.chart_args(domain=[["name", "like", "AI chart limit"]])
        payload = execute_chart(self.env(user=self.owner), args)
        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["values"]), 50)
        self.assertNotIn("Group 0", payload["labels"])
        conversation = self.new_conversation()
        with patch.object(ProviderClient, "reply", return_value=("", [{"name": "odoo_chart", "arguments": args}])):
            messages = conversation.send_message("Make a chart")
        self.assertTrue(messages[-1]["local_only"])
        self.assertEqual(messages[-1]["payload"]["kind"], "chart")
        with patch.object(ProviderClient, "reply", return_value=("Odoo help", [])) as reply:
            conversation.send_message("Explain Odoo")
        self.assertNotIn("Group 1", json.dumps(reply.call_args.args[1]))

    def test_monetary_chart_sums_in_one_currency(self):
        if "account.move" not in self.env:
            self.skipTest("Accounting is not installed")
        self.env["mcp.enabled.model"].sudo().search([("model_name", "=", "account.move")]).unlink()
        self.env["mcp.enabled.model"].create({"model_id": self.env["ir.model"]._get_id("account.move"), "allow_read": True})
        self.owner.group_ids = [(4, self.env.ref("account.group_account_readonly").id)]
        payload = execute_chart(self.env(user=self.owner), self.chart_args(
            model="account.move", group_by="state", operation="sum", measure="amount_total",
        ))
        self.assertEqual(payload["kind"], "chart")
