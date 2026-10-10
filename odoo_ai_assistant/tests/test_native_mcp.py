import base64
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models import native_mcp
from ..models.native_mcp import _BUILTINS
from ..models.providers import ProviderClient
from ..models.tools import available_tools


@tagged("post_install", "-at_install")
class TestActionIntent(TransactionCase):
    def test_requests_naming_accessible_menus_are_routed_locally_without_classifier(self):
        client = ProviderClient("openai", "not-a-real-key")
        vocabulary = ["Customers", "Invoices", "Reordering Rules", "Meetings", "Activities"]
        for prompt in (
            "Show me all customers from Spain",
            "Find unpaid invoices from last month",
            "List reordering rules below their minimum",
            "Schedule a meeting for tomorrow",
            "Show my activity list",
        ):
            history = [{"role": "user", "content": prompt}]
            with self.subTest(prompt=prompt), patch.object(ProviderClient, "reply") as reply:
                self.assertTrue(client.is_odoo_topic("test-model", history, vocabulary=vocabulary), prompt)
                reply.assert_not_called()
        # Without a matching accessible menu the request goes to the classifier.
        for vocabulary in (None, [], ["Employees"]):
            with self.subTest(vocabulary=vocabulary), patch.object(
                ProviderClient, "reply", return_value=('{"topic":"OUTSIDE"}', []),
            ) as reply:
                self.assertFalse(client.is_odoo_topic(
                    "test-model", [{"role": "user", "content": "Show me all customers from Spain"}],
                    vocabulary=vocabulary,
                ))
                reply.assert_called_once()

    def test_action_intent_is_semantic_for_every_provider(self):
        # Verbs alone must not route intent: "create" can request a chart or a record.
        cases = (
            ("Can u create a bar chart of different stages in CRM", "READ", False),
            ("Generate a pie chart of stages in CRM", "READ", False),
            ("Create a new CRM lead named Acme", "ACTION", True),
        )
        for provider in ("openai", "anthropic", "gemini", "openrouter"):
            client = ProviderClient(provider, "not-a-real-key")
            for prompt, label, expected in cases:
                text = json.dumps({"intent": label}) if provider == "openai" else label
                history = [{"role": "user", "content": prompt}]
                with self.subTest(provider=provider, prompt=prompt), patch.object(
                    ProviderClient, "reply", return_value=(text, []),
                ) as reply:
                    self.assertEqual(client.requires_action("test-model", history), expected)
                    reply.assert_called_once()
                    self.assertIn("chart", reply.call_args.kwargs["system_override"])

    def test_action_intent_separates_latest_request_from_earlier_dialogue(self):
        history = [
            {"role": "user", "content": "Can u create a bar chart of payment terms"},
            {"role": "assistant", "content": "Add an ir.ui.view record with a graph arch."},
            {"role": "user", "content": "Can u create a bar chart of payment terms"},
        ]
        for provider in ("openai", "anthropic", "gemini", "openrouter"):
            client = ProviderClient(provider, "not-a-real-key")
            text = json.dumps({"intent": "READ"}) if provider == "openai" else "READ"
            with self.subTest(provider=provider), patch.object(
                ProviderClient, "reply", return_value=(text, []),
            ) as reply:
                self.assertFalse(client.requires_action("test-model", history))
                payload = json.loads(reply.call_args.args[1][0]["content"])
                self.assertEqual(payload["latest_request"], history[-1]["content"])
                self.assertEqual(payload["earlier_dialogue"], history[:-1])
                self.assertIn("earlier_dialogue only to resolve", reply.call_args.kwargs["system_override"])

    def test_semantic_action_check_requires_an_explicit_valid_result(self):
        client = ProviderClient("openai", "not-a-real-key")
        history = [{"role": "user", "content": "Prepare a new Odoo record"}]
        with patch.object(ProviderClient, "reply", return_value=('{"intent": "ACTION"}', [])) as reply:
            self.assertTrue(client.requires_action("test-model", history))
        args, kwargs = reply.call_args
        self.assertEqual(kwargs["output_schema"]["properties"]["intent"]["enum"], ["ACTION", "READ"])
        # The dialogue is passed as data, not as a conversation the model should answer.
        self.assertEqual(len(args[1]), 1)
        self.assertEqual(json.loads(args[1][0]["content"]),
                         {"latest_request": "Prepare a new Odoo record", "earlier_dialogue": []})
        with patch.object(ProviderClient, "reply", return_value=('{"intent": "READ"}', [])):
            self.assertFalse(client.requires_action("test-model", history))
        for invalid in ("READ", "Maybe", '{"intent": "MAYBE"}', '{"intent": "READ", "x": 1}'):
            with patch.object(ProviderClient, "reply", return_value=(invalid, [])):
                with self.assertRaises(UserError):
                    client.requires_action("test-model", history)

    def test_semantic_action_check_text_protocol_for_other_providers(self):
        history = [{"role": "user", "content": "Which products cost the most?"}]
        for provider in ("anthropic", "gemini", "openrouter"):
            client = ProviderClient(provider, "not-a-real-key")
            for text, expected in (("ACTION", True), ("**READ**", False), (" `action`.\n", True), ("READ.", False)):
                with patch.object(ProviderClient, "reply", return_value=(text, [])) as reply:
                    self.assertEqual(client.requires_action("test-model", history), expected)
                self.assertNotIn("output_schema", reply.call_args.kwargs)
            for invalid in ("Maybe", "READ or ACTION", "Sure! Here are the products"):
                with patch.object(ProviderClient, "reply", return_value=(invalid, [])):
                    with self.assertRaises(UserError):
                        client.requires_action("test-model", history)
            with patch.object(ProviderClient, "reply", return_value=("READ", [{"name": "x", "arguments": {}}])):
                with self.assertRaises(UserError):
                    client.requires_action("test-model", history)

    def test_legacy_openai_action_check_uses_text_without_forced_tools(self):
        # Once a model is known to lack the Responses API, the check uses plain-text instructions.
        from ..models import providers
        providers._OPENAI_CHAT_COMPLETIONS.add("gpt-3.5-turbo")
        self.addCleanup(providers._OPENAI_CHAT_COMPLETIONS.discard, "gpt-3.5-turbo")
        client = ProviderClient("openai", "not-a-real-key")
        history = [{"role": "user", "content": "Prepare a new Odoo record"}]
        for text, expected in (("ACTION", True), ("READ", False)):
            with patch.object(ProviderClient, "reply", return_value=(text, [])) as reply:
                self.assertEqual(client.requires_action("gpt-3.5-turbo", history), expected)
            args, kwargs = reply.call_args
            self.assertEqual(args[2], [])
            self.assertEqual(json.loads(args[1][0]["content"]),
                             {"latest_request": "Prepare a new Odoo record", "earlier_dialogue": []})
            self.assertNotIn("tool_choice", kwargs)
            self.assertNotIn("output_schema", kwargs)
        with patch.object(ProviderClient, "reply", return_value=("Hi! How can I help?", [])):
            with self.assertRaises(UserError):
                client.requires_action("gpt-3.5-turbo", history)


@tagged("post_install", "-at_install")
class TestNativeMCP(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login="native_owner", groups="base.group_user,base.group_partner_manager",
                                  ai_assistant_enabled=True)
        cls.other = new_test_user(cls.env, login="native_other", groups="base.group_user",
                                  ai_assistant_enabled=True)
        cls.config = cls.env.ref("odoo_ai_assistant.default_config")
        cls.model = cls.env["odoo.ai.assistant.provider.model"].create({
            "name": "Native test model", "identifier": "native-test", "provider": "openai",
        })
        cls.config.write({
            "enabled": True, "provider": "openai", "api_key": "not-a-real-key",
            "model_id": cls.model.id, "local_tools": True,
            "native_actions": True, "business_data_sharing": False,
            "screen_metadata": False, "native_rate_limit": 600,
        })
        cls.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")
        gates = cls.env["mcp.enabled.model"]
        for model in (
            "res.partner", "ir.attachment", "account.move", "stock.warehouse.orderpoint",
        ):
            if model not in cls.env:
                continue
            gate = gates.with_context(active_test=False).search([("model_name", "=", model)])
            values = {"active": True, "allow_read": True, "allow_create": True,
                      "allow_write": True, "allow_unlink": True, "allow_method_calls": True}
            if gate:
                gate.write(values)
            else:
                gates.create({"model_id": cls.env["ir.model"]._get_id(model), **values})
        cls.gate = gates.search([("model_name", "=", "res.partner")])
        cls.conversations = cls.env["odoo.ai.assistant.conversation"].with_user(cls.owner)
        cls.partner = cls.env["res.partner"].create({"name": "Native private customer", "phone": "123"})

    def setUp(self):
        super().setUp()
        self.chat = self.conversations.browse(self.conversations.start_conversation()["id"])
        self.bridge = self.env["odoo.ai.assistant.native.mcp"].with_user(self.owner)
        self.requests = self.env["odoo.ai.assistant.tool.request"].with_user(self.owner)
        progress = patch.object(type(self.chat), "_progress")
        progress.start()
        self.addCleanup(progress.stop)
        intent = patch.object(ProviderClient, "requires_action", return_value=False)
        intent.start()
        self.addCleanup(intent.stop)

    def prepare(self, name, arguments):
        return self.requests._prepare(self.chat, name, {"arguments_json": json.dumps(arguments)})

    def test_complete_catalogue_without_external_endpoint(self):
        tools = {tool["name"] for tool in available_tools(self.env(user=self.owner))}
        self.assertTrue({"mcp_" + name for name in _BUILTINS} <= tools)
        self.assertFalse(self.env["mcp.enabled.model"]._get_mcp_enabled())
        result = self.prepare("mcp_list_models", {}).result
        self.assertIn("res.partner", [entry["model"] for entry in result["structuredContent"]["models"]])
        templates = self.prepare("mcp_list_resource_templates", {}).result
        self.assertIn("res.partner", templates["structuredContent"]["enabled_models"])
        self.assertIn("content", self.prepare("mcp_get_current_context", {}).result)

    def test_native_tool_descriptions_do_not_duplicate_input_schemas(self):
        catalogue = self.bridge._catalogue()
        tools = self.bridge._provider_tools()
        self.assertTrue(tools)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                schema = catalogue[tool["name"]]["schema"]
                self.assertEqual(tool["inputSchema"], schema)
                self.assertNotIn(json.dumps(schema, ensure_ascii=True), tool["description"])
                self.assertIn("matching the input schema", tool["description"])
        for method, args in [
            ("list_models", ()), ("get_current_context", ()), ("list_resource_templates", ()),
            ("search_records", ("res.partner",)),
        ]:
            with self.assertRaises(AccessError):
                getattr(self.bridge, method)(*args)

    def test_read_fields_search_and_aggregation(self):
        request = self.prepare("mcp_get_record", {
            "model": "res.partner", "record_id": self.partner.id, "fields": ["name", "phone"],
        })
        self.assertEqual(request.state, "done")
        self.assertIn(self.partner.name, request._payload()["details"])
        self.assertEqual(self.prepare("mcp_get_fields", {
            "model": "res.partner", "field_names": ["name"],
        }).result["structuredContent"]["fields"][0]["name"], "name")
        result = self.prepare("mcp_search_records", {
            "model": "res.partner", "domain": [["id", "=", self.partner.id]], "fields": ["name"],
        }).result
        self.assertEqual(result["structuredContent"]["records"][0]["name"], self.partner.name)
        self.assertIn("structuredContent", self.prepare("mcp_aggregate_records", {
            "model": "res.partner", "domain": [["id", "=", self.partner.id]], "groupby": ["is_company"],
        }).result)

    def test_real_create_and_batch_create_require_confirmation(self):
        request = self.prepare("mcp_create_record", {"model": "res.partner", "values": {"name": "Native real creation"}})
        self.assertEqual(request.state, "pending")
        self.assertFalse(self.env["res.partner"].search_count([("name", "=", "Native real creation")]))
        self.chat.confirm_tool(request.id)
        self.assertEqual(request.state, "done")
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Native real creation")]), 1)
        with self.assertRaises(UserError):
            self.chat.confirm_tool(request.id)
        batch = self.prepare("mcp_create_records", {
            "model": "res.partner", "records": [{"name": "Native batch A"}, {"name": "Native batch B"}],
        })
        self.chat.confirm_tool(batch.id)
        self.assertEqual(batch.result["structuredContent"]["count"], 2)

    def test_updates_and_deletion(self):
        request = self.prepare("mcp_update_record", {
            "model": "res.partner", "record_id": self.partner.id, "values": {"phone": "456"},
        })
        self.assertEqual(self.partner.phone, "123")
        self.chat.confirm_tool(request.id)
        self.assertEqual(self.partner.phone, "456")
        request = self.prepare("mcp_update_records", {
            "model": "res.partner", "updates": [{"id": self.partner.id, "values": {"phone": "789"}}],
        })
        self.chat.confirm_tool(request.id)
        self.assertEqual(self.partner.phone, "789")
        removable = self.env["res.partner"].create({"name": "Native deletion"})
        request = self.prepare("mcp_delete_record", {"model": "res.partner", "record_id": removable.id})
        self.assertTrue(removable.exists())
        self.chat.confirm_tool(request.id)
        self.assertFalse(removable.exists())

    def test_stale_records_permissions_expiry_and_ownership(self):
        request = self.prepare("mcp_update_record", {
            "model": "res.partner", "record_id": self.partner.id, "values": {"phone": "456"},
        })
        with self.assertRaises(AccessError):
            self.chat.with_user(self.other).confirm_tool(request.id)
        self.gate.allow_write = False
        with self.assertRaises(AccessError):
            self.chat.confirm_tool(request.id)
        self.gate.allow_write = True
        self.partner.write({"phone": "another edit"})
        # Tests share one transaction timestamp; force an observable different version.
        self.partner.write_date = fields.Datetime.now() + timedelta(seconds=2)
        with self.assertRaises(UserError):
            self.chat.confirm_tool(request.id)
        request = self.prepare("mcp_update_record", {
            "model": "res.partner", "record_id": self.partner.id, "values": {"phone": "456"},
        })
        request.expires_at = fields.Datetime.now() - timedelta(seconds=1)
        with self.assertRaises(UserError):
            self.chat.confirm_tool(request.id)

    def test_disabled_actions_access_changes_and_cancellation(self):
        request = self.prepare("mcp_create_record", {"model": "res.partner", "values": {"name": "Cancelled creation"}})
        self.chat.cancel_tool(request.id)
        with self.assertRaises(UserError):
            self.chat.confirm_tool(request.id)
        self.assertFalse(self.env["res.partner"].search_count([("name", "=", "Cancelled creation")]))
        # Users cannot change their own AI access; AI changes follow the AI Settings switch.
        with self.assertRaises(AccessError):
            self.owner.with_user(self.owner).write({"ai_assistant_enabled": False})
        self.config.native_actions = False
        self.assertNotIn("mcp_create_record", {tool["name"] for tool in available_tools(self.env(user=self.owner))})
        with self.assertRaises(AccessError):
            self.prepare("mcp_create_record", {"model": "res.partner", "values": {"name": "Denied"}})

    def test_schema_credential_and_technical_denials(self):
        for arguments in [
            {"model": "res.partner", "record_id": True},
            {"model": "res.partner", "record_id": self.partner.id, "extra": 1},
        ]:
            with self.assertRaises(ValidationError):
                self.prepare("mcp_get_record", arguments)
        for arguments in [
            {"model": "res.users", "record_id": self.owner.id},
            {"model": "res.partner", "record_id": self.partner.id, "fields": ["api_key"]},
        ]:
            with self.assertRaises(AccessError):
                self.prepare("mcp_get_record", arguments)
        with self.assertRaises(ValidationError):
            self.requests._prepare(self.chat, "mcp_list_models", {"arguments_json": "not json"})

    def test_business_methods_and_chatter(self):
        request = self.prepare("mcp_call_model_method", {
            "model": "res.partner", "method": "toggle_active", "record_ids": [self.partner.id],
        })
        self.chat.confirm_tool(request.id)
        self.assertFalse(self.partner.active)
        for method in ("sudo", "write", "_write", "web_save", "run"):
            with self.assertRaises(UserError):
                self.prepare("mcp_call_model_method", {
                    "model": "res.partner", "method": method, "record_ids": [self.partner.id],
                })
        request = self.prepare("mcp_post_message", {
            "model": "res.partner", "record_id": self.partner.id, "body": "Native confirmed note",
        })
        self.chat.confirm_tool(request.id)
        self.assertIn("Native confirmed note", "".join(self.partner.message_ids.mapped("body")))

    def test_custom_tool_confirmation_schema_and_revocation(self):
        action = self.env["ir.actions.server"].create({
            "name": "Native curated action", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "state": "code", "code": "mcp['result'] = {'uid': env.uid, 'answer': mcp['args']['value']}",
            "group_ids": [fields.Command.link(self.env.ref("base.group_user").id)],
        })
        custom = self.env["mcp.custom.tool"].create({
            "name": "native_curated_test", "description": "Curated native test", "action_id": action.id,
            "is_readonly": True,
            "input_schema": json.dumps({"type": "object", "properties": {"value": {"type": "integer"}},
                                       "required": ["value"], "additionalProperties": False}),
        })
        name = "mcp_custom_%s" % custom.id
        request = self.prepare(name, {"value": 7})
        self.assertTrue(request.mutation)
        self.chat.confirm_tool(request.id)
        self.assertEqual(request.result["structuredContent"]["result"], {"uid": self.owner.id, "answer": 7})
        with self.assertRaises(ValidationError):
            self.prepare(name, {"value": "wrong"})
        request = self.prepare(name, {"value": 8})
        custom.active = False
        with self.assertRaises(AccessError):
            self.chat.confirm_tool(request.id)

    def test_attachment_staging_upload_read_and_listing(self):
        data = base64.b64encode(b"Private attachment text").decode()
        staged = self.chat.stage_upload("native.txt", data, "text/plain")
        request = self.prepare("mcp_upload_attachment", {
            "name": "native.txt", "data": staged["reference"], "model": "res.partner",
            "record_id": self.partner.id, "mimetype": "text/plain",
        })
        self.assertFalse(self.env["ir.attachment"].search_count([("name", "=", "native.txt")]))
        self.chat.confirm_tool(request.id)
        attachment_id = request.result["structuredContent"]["attachment_id"]
        read = self.prepare("mcp_read_attachment", {"attachment_id": attachment_id})
        self.assertIn("Private attachment text", read._payload()["details"])
        linked = self.prepare("mcp_read_attachment", {"attachment_id": attachment_id, "format": "link"})
        self.assertNotIn("access_token", json.dumps(linked.result))
        self.assertTrue(linked.result["structuredContent"]["requires_login"])
        listed = self.prepare("mcp_list_record_attachments", {
            "model": "res.partner", "record_id": self.partner.id,
        })
        self.assertIn("native.txt", json.dumps(listed.result))
        staged = self.chat.stage_upload("standalone.txt", data, "text/plain")
        request = self.prepare("mcp_upload_attachment", {"name": "standalone.txt", "data": staged["reference"]})
        self.chat.confirm_tool(request.id)
        self.assertTrue(request.result["structuredContent"]["attachment_id"])

    def test_upload_reference_ownership_and_invalid_files(self):
        staged = self.chat.stage_upload("owner.txt", base64.b64encode(b"abc").decode())
        other_chat = self.conversations.browse(self.conversations.start_conversation()["id"])
        with self.assertRaises(AccessError):
            self.requests._prepare(other_chat, "mcp_upload_attachment", {"arguments_json": json.dumps({
                "name": "owner.txt", "data": staged["reference"],
            })})
        with self.assertRaises(ValidationError):
            self.chat.stage_upload("bad.txt", "invalid!")
        with self.assertRaises(ValidationError):
            self.prepare("mcp_upload_attachment", {"name": "invented.txt", "data": "YWJj"})

    def test_sharing_requires_opt_in_and_explicit_approval_and_masks_history(self):
        self.env["odoo.ai.assistant.message"].sudo().create({
            "conversation_id": self.chat.id, "role": "user", "content": "Analyze this Odoo contact",
        })
        request = self.chat._native_request("mcp_get_record", {"arguments_json": json.dumps({
            "model": "res.partner", "record_id": self.partner.id, "fields": ["name", "phone"],
        })})
        with patch.object(ProviderClient, "reply", return_value=("A grounded answer.", [])) as reply:
            with self.assertRaises(AccessError):
                self.chat.share_tool_result(request.id)
            reply.assert_not_called()
            self.config.business_data_sharing = True
            self.chat.share_tool_result(request.id)
            self.assertIn(self.partner.name, json.dumps(reply.call_args.kwargs["tool_context"]))
            self.assertEqual(request.state, "shared")
        self.assertNotIn("A grounded answer.", json.dumps(self.chat._provider_history()))
        self.config.business_data_sharing = False
        self.assertNotIn(self.partner.name, json.dumps(self.chat._provider_history()))
        with self.assertRaises(UserError):
            self.chat.share_tool_result(request.id)

    def test_sharing_rechecks_permissions_company_provider_and_result_changes(self):
        request = self.prepare("mcp_get_record", {
            "model": "res.partner", "record_id": self.partner.id, "fields": ["name"],
        })
        self.config.business_data_sharing = True
        self.gate.allow_read = False
        with self.assertRaises(AccessError):
            request._sharing_context(self.chat)
        self.gate.allow_read = True
        request.provider = "anthropic"
        with self.assertRaises(AccessError):
            request._sharing_context(self.chat)
        request.provider = "openai"
        self.partner.name = "Changed after read"
        self.partner.write_date = fields.Datetime.now() + timedelta(seconds=1)
        with self.assertRaises(UserError):
            request._sharing_context(self.chat)
        company = self.env["res.company"].create({"name": "Native other company"})
        self.owner.company_ids = [fields.Command.link(company.id)]
        with self.assertRaises(AccessError):
            request._sharing_context(self.chat.with_context(allowed_company_ids=[company.id]))

    def test_atomic_batch_failure_and_audit(self):
        before = self.env["res.partner"].search_count([("name", "=", "Native atomic")])
        request = self.prepare("mcp_create_records", {
            "model": "res.partner", "records": [{"name": "Native atomic"}, {"nonexistent_field": "fail"}],
        })
        with self.assertRaises(ValueError):
            self.chat.confirm_tool(request.id)
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Native atomic")]), before)
        self.assertEqual(request.state, "pending")
        # Outside a confirmation savepoint a failed attempt remains in this test's audit.
        try:
            self.bridge._execute_native("mcp_update_record", {
                "model": "res.partner", "record_id": self.partner.id, "values": {"phone": "999"},
            })
        except AccessError:
            pass
        else:
            self.fail("An unconfirmed update was allowed.")
        ledger = self.env["odoo.ai.assistant.tool.event"].sudo().search([("user_id", "=", self.owner.id)])
        self.assertIn("failed", ledger.mapped("state"))
        self.assertNotIn("Native private customer", str(ledger.read(["tool", "state"])))

    def test_rate_limit_and_configuration_validation(self):
        self.config.native_rate_limit = 1
        ledger = self.env["odoo.ai.assistant.tool.event"].with_user(self.owner)
        ledger._begin("test_attempt")
        event, denied = ledger._insert_attempt(self.env(user=self.owner), "denied_attempt", "started", 1)
        self.assertTrue(denied)
        self.assertEqual(ledger.sudo().browse(event).state, "denied")
        with self.assertRaises(UserError):
            ledger._begin("another_denied_attempt")
        with self.assertRaises(ValidationError):
            self.config.native_rate_limit = 0

    def test_actual_provider_request_receives_only_explicit_approved_context(self):
        client = ProviderClient("openai", "not-a-real-key")
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Answer"},
        ]}]}
        with patch.object(ProviderClient, "_request", return_value=response) as request:
            client.reply("native-test", [{"role": "user", "content": "Odoo contact"}], [],
                         native_tools=True, tool_context={"name": "Explicitly approved name"},
                         tool_images=[{"mimeType": "image/png", "data": "YWJj"}])
            payload = request.call_args.args[2]
            self.assertIn("Explicitly approved name", payload["instructions"])
            self.assertEqual(payload["input"][-1]["content"][1]["type"], "input_image")
            self.assertFalse(payload["store"])

    def test_gemini_uses_json_schema_field_for_mcp_tool_unions(self):
        client = ProviderClient("gemini", "not-a-real-key")
        tool = {
            "name": "mcp_example",
            "description": "Test nullable and union argument types.",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": ["string", "null"]}},
                "additionalProperties": False,
            },
        }
        with patch.object(ProviderClient, "_request", return_value={
            "candidates": [{"content": {"parts": [{"text": "Answer"}]}}],
        }) as request:
            client.reply("gemini-test", [{"role": "user", "content": "Hello"}], [tool])
        declaration = request.call_args.args[2]["tools"][0]["functionDeclarations"][0]
        self.assertEqual(declaration["parametersJsonSchema"], tool["inputSchema"])
        self.assertNotIn("parameters", declaration)

    def test_required_visualization_tool_is_translated_for_every_provider(self):
        tool = {
            "name": "odoo_chart", "description": "Chart records.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        }
        history = [{"role": "user", "content": "Create the requested chart"}]
        responses = {
            "openai": {"status": "completed", "output": [{
                "type": "function_call", "name": tool["name"], "arguments": "{}",
            }]},
            "anthropic": {"content": [{
                "type": "tool_use", "name": tool["name"], "input": {},
            }]},
            "gemini": {"candidates": [{"content": {"parts": [{
                "functionCall": {"name": tool["name"], "args": {}},
            }]}}]},
            "openrouter": {"choices": [{"message": {"tool_calls": [{
                "function": {"name": tool["name"], "arguments": "{}"},
            }]}}]},
        }
        for provider, response in responses.items():
            with self.subTest(provider=provider), patch.object(
                ProviderClient, "_request", return_value=response,
            ) as request:
                client = ProviderClient(provider, "not-a-real-key")
                text, calls = client.reply("provider-test", history, [tool], tool_choice=tool["name"])
                self.assertEqual(calls[0]["name"], tool["name"])
                payload = request.call_args.args[2]
                if provider == "openai":
                    self.assertEqual(payload["tool_choice"], {"type": "function", "name": tool["name"]})
                elif provider in ("anthropic", "gemini"):
                    # Forced tool choice is rejected by newer Claude models and by some Gemini
                    # schemas: only the required tool is offered and the call is instructed.
                    self.assertNotIn("tool_choice", payload)
                    self.assertNotIn("toolConfig", payload)
                    instructions = payload["system"] if provider == "anthropic" else                         payload["systemInstruction"]["parts"][0]["text"]
                    self.assertIn("You must call the function " + tool["name"], instructions)
                else:
                    self.assertEqual(payload["tool_choice"], {
                        "type": "function", "function": {"name": tool["name"]},
                    })

    def test_required_tool_rejects_provider_text_only_fallback(self):
        tool = {"name": "odoo_chart", "description": "Chart records.",
                "inputSchema": {"type": "object", "properties": {}, "required": []}}
        text_only = {
            "openai": {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "No chart tool is available."},
            ]}]},
            "anthropic": {"content": [{"type": "text", "text": "No chart tool is available."}]},
            "gemini": {"candidates": [{"content": {"parts": [{"text": "No chart tool is available."}]}}]},
            "openrouter": {"choices": [{"message": {"content": "No chart tool is available."}}]},
        }
        for provider, response in text_only.items():
            with self.subTest(provider=provider), patch.object(ProviderClient, "_request", return_value=response):
                with self.assertRaisesRegex(UserError, "did not (run|call) the (required local chart|selected Odoo) tool"):
                    ProviderClient(provider, "not-a-real-key").reply(
                        "provider-test", [{"role": "user", "content": "Create chart"}], [tool],
                        tool_choice=tool["name"],
                    )

    def test_chat_dispatch_previews_and_confirmation_without_provider_calls(self):
        with patch.object(ProviderClient, "is_odoo_topic", return_value=True), patch.object(
            ProviderClient, "conversation_title", return_value="Native Action",
        ), patch.object(ProviderClient, "reply", return_value=("", [{
            "name": "mcp_update_record", "arguments": {
                # The AI cannot know record IDs; it names the record with a local reference.
                "arguments_json": json.dumps({"model": "res.partner", "record_id": {"$odoo_ref": {
                    "model": "res.partner", "domain": [["name", "=", self.partner.name]]}},
                                               "values": {"phone": "preview change"}}),
            },
        }])) as reply:
            messages = self.chat.send_message("Update this Odoo contact's phone after my confirmation")
            preview = messages[-1]["payload"]
            self.assertEqual(preview["kind"], "mcp_request")
            self.assertEqual(preview["state"], "pending")
            self.assertEqual(self.partner.phone, "123")
            messages = self.chat.confirm_tool(preview["request_id"])
            self.assertEqual(messages[-1]["payload"]["state"], "done")
            self.assertEqual(self.partner.phone, "preview change")
            self.assertEqual(reply.call_count, 1)

    def test_discovery_continues_to_local_records_without_sharing(self):
        self.config.screen_metadata = True
        responses = [
            ("", [{"name": "mcp_list_models", "arguments": {"arguments_json": "{}"}}]),
            ("", [{"name": "mcp_get_fields", "arguments": {
                "arguments_json": json.dumps({"model": "res.partner", "field_names": ["name", "phone"]}),
            }}]),
            ("", [{"name": "mcp_search_records", "arguments": {"arguments_json": json.dumps({
                "model": "res.partner", "fields": ["name", "phone"],
                "domain": [["id", "=", self.partner.id]],
            })}}]),
        ]
        with patch.object(ProviderClient, "is_odoo_topic", return_value=True), patch.object(
            ProviderClient, "conversation_title", return_value="Local contact query",
        ), patch.object(ProviderClient, "reply", side_effect=responses) as reply:
            messages = self.chat.send_message("Show the matching Odoo contacts")
        self.assertEqual(reply.call_count, 3)
        result = messages[-1]["payload"]
        self.assertEqual(result["table"]["rows"][0]["name"], self.partner.name)
        self.assertFalse(result["sharing_allowed"])
        self.assertEqual(len([message for message in messages if message["role"] == "assistant"]), 1)
        for call in reply.call_args_list:
            outgoing = json.dumps({"history": call.args[1], "context": call.kwargs})
            self.assertNotIn(self.partner.name, outgoing)
            self.assertNotIn("123", outgoing)
            self.assertNotIn("tool_context", call.kwargs)
            self.assertEqual(call.kwargs["screen_context"]["calendar"]["today"],
                             fields.Date.to_string(fields.Date.context_today(self.chat)))
        schema = reply.call_args.kwargs["screen_context"]["discovered_schemas"][0]["schema"]
        self.assertEqual({field["name"] for field in schema["fields"]}, {"name", "phone"})

    def test_discovery_is_bounded(self):
        # Schema discovery (model and field names only) no longer needs screen names, but stays bounded.
        response = ("", [{"name": "mcp_list_models", "arguments": {"arguments_json": "{}"}}])
        client = ProviderClient("openai", "not-a-real-key")
        with patch.object(ProviderClient, "reply", return_value=response) as reply:
            with self.assertRaises(UserError):
                self.chat._dispatch_calls(client, self.config, [], response[1], [])
            self.assertEqual(reply.call_count, 3)
        metadata = self.bridge._planning_context()
        self.assertNotIn(self.partner.name, json.dumps(metadata))
        self.gate.allow_read = False
        request = self.requests.sudo().create({
            "conversation_id": self.chat.id, "user_id": self.owner.id,
            "company_id": self.env.company.id, "allowed_company_ids": self.env.companies.ids,
            "tool": "mcp_get_fields", "arguments": {"model": "res.partner"},
            "definition_digest": "test", "expires_at": fields.Datetime.now(),
        })
        with self.assertRaises(AccessError):
            self.bridge._planning_context(request)

    def test_oversized_request_images_are_left_out_of_the_card(self):
        from ..models.limits import MAX_IMAGE_CHARS
        request = self.requests.sudo().create({
            "conversation_id": self.chat.id, "user_id": self.owner.id,
            "company_id": self.env.company.id, "allowed_company_ids": self.env.companies.ids,
            "tool": "mcp_get_fields", "arguments": {"model": "res.partner"}, "state": "done",
            "definition_digest": "test", "expires_at": fields.Datetime.now(),
            "result": {"content": [
                {"type": "image", "mimeType": "image/png", "data": "QQ=="},
                {"type": "image", "mimeType": "image/png", "data": "A" * (MAX_IMAGE_CHARS + 4)},
            ]},
        })
        self.assertEqual([image["data"] for image in request._payload()["images"]], ["QQ=="])

    def test_native_record_tables_empty_results_and_pagination(self):
        second = self.env["res.partner"].create({"name": "Native second customer"})
        request = self.chat._native_request("mcp_search_records", {"arguments_json": json.dumps({
            "model": "res.partner", "domain": [["id", "in", [self.partner.id, second.id]]],
            "fields": ["name"], "limit": 1, "order": "id",
        })})
        first = self.chat.get_messages()[-1]
        self.assertTrue(first["payload"]["table"]["has_more"])
        messages = self.chat.load_more_records(first["id"])
        self.assertEqual(messages[-1]["payload"]["table"]["offset"], 1)
        self.assertEqual(messages[-1]["payload"]["table"]["rows"][0]["name"], second.name)
        self.assertFalse(messages[-1]["payload"]["table"]["has_more"])
        self.assertEqual(request.arguments["offset"] if "offset" in request.arguments else 0, 0)
        self.gate.allow_read = False
        with self.assertRaises(AccessError):
            self.chat.load_more_records(first["id"])
        self.gate.allow_read = True
        empty = self.prepare("mcp_search_records", {
            "model": "res.partner", "domain": [["id", "=", 0]], "fields": ["name"],
        })._payload()["table"]
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["total"], 0)
        self.assertEqual(empty["columns"][0]["key"], "name")
        # Empty fields show blank, not "false"; checkboxes keep their value.
        row = self.prepare("mcp_search_records", {
            "model": "res.partner", "domain": [["id", "=", second.id]], "fields": ["name", "city", "is_company"],
        })._payload()["table"]["rows"][0]
        self.assertIsNone(row["city"])
        self.assertIs(row["is_company"], False)

    def test_empty_batch_action_is_rejected_by_the_tool_schema(self):
        self.config.screen_metadata = True
        calls = [{"name": "mcp_create_records", "arguments": {
            "model": "res.partner", "records": [],
        }}]
        client = ProviderClient("openai", "not-a-real-key")
        partners = self.env["res.partner"].search_count([])
        requests = self.env["odoo.ai.assistant.tool.request"].sudo().search_count([])
        with self.assertRaises(ValidationError):
            self.chat._dispatch_calls(client, self.config, [], calls, [], action_request=True)
        self.assertEqual(self.env["res.partner"].search_count([]), partners)
        self.assertEqual(self.env["odoo.ai.assistant.tool.request"].sudo().search_count([]), requests)

    def test_provider_plans_reject_guessed_ids_derived_fields_and_bad_options(self):
        conversation = self.chat.with_context(odoo_ai_provider_plan=True)

        def prepare(name, arguments):
            return self.requests._prepare(conversation, name, {"arguments_json": json.dumps(arguments)})

        with self.assertRaisesRegex(ValidationError, "numeric record ID"):
            prepare("mcp_update_record", {"model": "res.partner", "record_id": self.partner.id,
                                          "values": {"phone": "guessed"}})
        with self.assertRaisesRegex(ValidationError, "numeric record ID"):
            prepare("mcp_create_record", {"model": "res.partner", "values": {
                "name": "Guessed parent", "parent_id": self.partner.id}})
        # Reads may use IDs: their results stay local and change nothing.
        self.assertTrue(prepare("mcp_get_record", {"model": "res.partner", "record_id": self.partner.id,
                                                    "fields": ["name"]}))
        # Derived (related, read-only) fields are rejected with the field to set instead.
        related = next((name for name, field in self.env["res.partner"]._fields.items()
                        if field.related and field.readonly and field.store and field.type in {"char", "selection"}),
                       None)
        if related:
            with self.assertRaisesRegex(ValidationError, "cannot be set directly"):
                prepare("mcp_create_record", {"model": "res.partner", "values": {"name": "Derived", related: "x"}})
        # Selection values are checked against the real options; stray punctuation is trimmed.
        key = self.env["res.partner"].fields_get(["type"])["type"]["selection"][0][0]
        request = prepare("mcp_create_record", {"model": "res.partner", "values": {"name": "Typed", "type": key + ","}})
        self.assertEqual(request.arguments["values"]["type"], key)
        with self.assertRaisesRegex(ValidationError, "Valid options"):
            prepare("mcp_create_record", {"model": "res.partner", "values": {"name": "Typed", "type": "no_such"}})
        # A plain list of records for an x2many field becomes create commands.
        request = prepare("mcp_create_record", {"model": "res.partner", "values": {
            "name": "Parent with child", "child_ids": [{"name": "Plain child"}]}})
        self.assertEqual(request.arguments["values"]["child_ids"], [[0, 0, {"name": "Plain child"}]])
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            prepare("mcp_create_record", {"model": "res.partner", "values": {
                "name": "Bad lines", "child_ids": [{"command": 0, "values": [None, None]}]}})
        # Confirm re-resolves the plan the same way and creates the lines.
        self.chat.confirm_tool(request.id)
        parent = self.env["res.partner"].search([("name", "=", "Parent with child")])
        self.assertEqual(parent.child_ids.mapped("name"), ["Plain child"])
        # A link to the model registry accepts a business model's technical name, never a restricted one.
        gate = self.env["mcp.enabled.model"].search([("model_name", "=", "mail.activity")])
        if not gate:
            gate = self.env["mcp.enabled.model"].create({"model_id": self.env["ir.model"]._get_id("mail.activity")})
        gate.write({"allow_read": True, "allow_create": True})
        reference = {"$odoo_ref": {"model": "res.partner", "domain": [["name", "=", self.partner.name]]}}
        request = prepare("mcp_create_record", {"model": "mail.activity", "values": {
            "res_model_id": "res.partner", "res_id": reference, "summary": "Call back"}})
        self.assertEqual(request.arguments["values"]["res_model_id"], self.env["ir.model"]._get_id("res.partner"))
        self.assertEqual(request.arguments["values"]["res_id"], self.partner.id)
        with self.assertRaises(ValidationError):  # restricted model names are not resolved
            prepare("mcp_create_record", {"model": "mail.activity", "values": {
                "res_model_id": "res.users", "res_id": reference, "summary": "Call back"}})
        with self.assertRaisesRegex(ValidationError, "technical name"):
            prepare("mcp_create_record", {"model": "mail.activity", "values": {
                "res_model_id": reference, "res_id": reference, "summary": "Call back"}})
        registry_ref = {"$odoo_ref": {"model": "ir.model", "domain": [["model", "=", "res.partner"]]}}
        request = prepare("mcp_create_record", {"model": "mail.activity", "values": {
            "res_model_id": registry_ref, "res_id": reference, "summary": "Call back"}})
        self.assertEqual(request.arguments["values"]["res_model_id"], self.env["ir.model"]._get_id("res.partner"))
        with self.assertRaisesRegex(ValidationError, "technical name"):
            prepare("mcp_create_record", {"model": "mail.activity", "values": {"summary": "x", "res_id": reference,
                "res_model_id": {"$odoo_ref": {"model": "ir.model", "domain": [["model", "=", "res.users"]]}}}})
        with self.assertRaisesRegex(ValidationError, "technical name as text"):
            prepare("mcp_create_record", {"model": "mail.activity", "values": {
                "res_model_id": {"name": "res.partner"}, "res_id": reference, "summary": "Call back"}})
        # With result sharing enabled the provider may have seen IDs, so they are allowed.
        self.config.business_data_sharing = True
        self.assertTrue(prepare("mcp_update_record", {"model": "res.partner", "record_id": self.partner.id,
                                                       "values": {"phone": "shared"}}))

    def test_references_sent_as_json_text_and_many2one_labels_resolve(self):
        # Some providers (e.g. Gemini) send the markers as JSON text instead of objects.
        plan = {"model": "res.partner", "values": {
            "name": "Native text-reference contact",
            "parent_id": json.dumps({"$odoo_ref": {"model": "res.partner",
                                                   "domain": [["name", "=", self.partner.name]]}}),
        }}
        request = self.prepare("mcp_create_record", plan)
        self.assertEqual(request.arguments["values"]["parent_id"], self.partner.id)
        # A label on a many2one resolves by exact name to exactly one record.
        request = self.prepare("mcp_create_record", {"model": "res.partner", "values": {
            "name": "Native label contact", "parent_id": {"$odoo_selection": self.partner.name},
        }})
        self.assertEqual(request.arguments["values"]["parent_id"], self.partner.id)
        self.assertEqual(request.resolution_snapshot[0]["name"], self.partner.name)
        with self.assertRaises(UserError):
            self.prepare("mcp_create_record", {"model": "res.partner", "values": {
                "name": "Native missing label", "parent_id": {"$odoo_selection": "No such partner name"},
            }})
        # Ordinary text that only mentions a marker is left unchanged.
        self.assertEqual(native_mcp._decoded_marker("see $odoo_ref docs"), "see $odoo_ref docs")

    def test_chart_groups_show_selection_labels(self):
        label_by_key = dict(self.env["res.partner"].fields_get(["type"])["type"]["selection"])
        rows = self.env["odoo.ai.assistant.mcp"].with_user(self.owner)._execute("ai_group_records", {
            "model": "res.partner", "domain": [], "group": "type", "measure": "__count",
        })["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(row["label"] in label_by_key.values() or row["label"] == "Unassigned" for row in rows))

    def test_local_references_and_selection_labels_prepare_generic_action(self):
        label = self.env["res.partner"].fields_get(["type"])["type"]["selection"][0]
        plan = {"model": "res.partner", "values": {
            "name": "Native related contact", "parent_id": {"$odoo_ref": {
                "model": "res.partner", "domain": [["name", "=", self.partner.name]],
            }}, "type": {"$odoo_selection": label[1]},
        }}
        request = self.prepare("mcp_create_record", plan)
        self.assertEqual(request.arguments["values"]["parent_id"], self.partner.id)
        self.assertEqual(request.arguments["values"]["type"], label[0])
        self.assertEqual(request.resolution_snapshot[0]["name"], self.partner.name)
        payload = request._payload()
        self.assertIn(self.partner.name, payload["details"])
        self.assertIn(label[1], payload["details"])
        self.assertEqual(payload["summary"]["headline"], "Create Contact")
        proposed = {item["label"]: item["value"] for group in payload["summary"]["groups"]
                    for item in group["items"]}
        partner_fields = self.env["res.partner"].with_user(self.owner).fields_get(
            ["name", "parent_id", "type"], attributes=["string"],
        )
        self.assertEqual(proposed[partner_fields["name"]["string"]], "Native related contact")
        self.assertEqual(proposed[partner_fields["parent_id"]["string"]], self.partner.name)
        self.assertEqual(proposed[partner_fields["type"]["string"]], label[1])
        self.assertEqual(request.state, "pending")
        self.assertFalse(self.env["res.partner"].search_count([("name", "=", "Native related contact")]))
        self.chat.confirm_tool(request.id)
        created = self.env["res.partner"].search([("name", "=", "Native related contact")])
        self.assertEqual(created.parent_id, self.partner)
        self.assertEqual(created.type, label[0])
        self.assertNotIn(self.partner.name, json.dumps(self.chat._provider_history()))

    def test_local_references_block_ambiguity_staleness_and_permission_changes(self):
        plan = {"model": "res.partner", "record_id": {"$odoo_ref": {
            "model": "res.partner", "domain": [["name", "=", self.partner.name]],
        }}, "values": {"phone": "new phone"}}
        request = self.prepare("mcp_update_record", plan)
        self.assertEqual(request.arguments["record_id"], self.partner.id)
        self.partner.phone = "changed after preview"
        with self.assertRaises(UserError):
            self.chat.confirm_tool(request.id)
        request = self.prepare("mcp_update_record", plan)
        duplicate = self.env["res.partner"].create({"name": self.partner.name})
        with self.assertRaises(UserError):
            self.chat.confirm_tool(request.id)
        with self.assertRaises(UserError):
            self.prepare("mcp_update_record", plan)
        duplicate.unlink()
        request = self.prepare("mcp_update_record", plan)
        self.gate.allow_read = False
        with self.assertRaises(AccessError):
            self.chat.confirm_tool(request.id)
        self.gate.allow_read = True
        missing = {"model": "res.partner", "record_id": {"$odoo_ref": {
            "model": "res.partner", "domain": [["id", "=", 0]],
        }}, "values": {"phone": "unreachable"}}
        with self.assertRaises(UserError):
            self.prepare("mcp_update_record", missing)

    def test_local_reference_validation_and_no_legacy_native_shortcuts(self):
        self.assertFalse({"search_contacts", "recent_orders"} & {
            tool["name"] for tool in available_tools(self.env(user=self.owner))
        })
        for value in [{"$odoo_selection": "not an option"}, {"$odoo_ref": {
            "model": "res.partner", "domain": [],
        }}]:
            with self.assertRaises(ValidationError):
                self.prepare("mcp_create_record", {"model": "res.partner", "values": {"type": value}})
        with self.assertRaises(ValidationError):
            self.prepare("mcp_create_record", {"model": "res.partner", "values": {"company_id": {"$odoo_ref": {
                "model": "res.partner", "domain": [["id", "=", self.partner.id]],
            }}}})
        with self.assertRaises(AccessError):
            self.prepare("mcp_create_record", {"model": "res.partner", "values": {"parent_id": {"$odoo_ref": {
                "model": "res.partner", "domain": [["password", "=", "test"]],
            }}}})
        with self.assertRaises(ValidationError):
            self.prepare("mcp_get_record", {"model": "res.partner", "record_id": {"$odoo_ref": {
                "model": "res.partner", "domain": [["id", "=", self.partner.id]],
            }}})

    def test_local_reference_batches_and_provider_flow(self):
        reference = {"$odoo_ref": {"model": "res.partner", "domain": [["name", "=", self.partner.name]]}}
        request = self.prepare("mcp_update_records", {
            "model": "res.partner", "updates": [{"id": reference, "values": {"phone": "batch local phone"}}],
        })
        self.assertEqual(request.arguments["updates"][0]["id"], self.partner.id)
        self.chat.confirm_tool(request.id)
        self.assertEqual(self.partner.phone, "batch local phone")
        values = {"name": "Native provider linked contact", "parent_id": reference}
        response = ("", [{"name": "mcp_create_record", "arguments": {
            "arguments_json": json.dumps({"model": "res.partner", "values": values}),
        }}])
        self.config.screen_metadata = True
        with patch.object(ProviderClient, "is_odoo_topic", return_value=True), patch.object(
            ProviderClient, "conversation_title", return_value="Linked contact",
        ), patch.object(ProviderClient, "reply", return_value=response) as reply:
            messages = self.chat.send_message("Create a contact under the named Odoo customer")
        self.assertEqual(reply.call_count, 1)
        self.assertEqual(messages[-1]["payload"]["state"], "pending")
        self.assertIn(self.partner.name, messages[-1]["payload"]["details"])
        self.assertNotIn(self.partner.name, json.dumps(reply.call_args.kwargs))
        self.assertNotIn("batch local phone", json.dumps(reply.call_args.kwargs))
        self.assertFalse(self.env["res.partner"].search_count([("name", "=", values["name"])]))

    def test_action_routing_offers_confirmed_write_tools_without_discovery_loop(self):
        self.config.screen_metadata = True
        response = ("", [{"name": "mcp_create_record", "arguments": {"arguments_json": json.dumps({
                "model": "res.partner", "values": {"name": "Routed related contact", "parent_id": {
                    "$odoo_ref": {"model": "res.partner", "domain": [["name", "=", self.partner.name]]},
                }},
            })}}])
        with patch.object(ProviderClient, "requires_action", return_value=True), patch.object(
            ProviderClient, "is_odoo_topic", return_value=True,
        ), patch.object(ProviderClient, "conversation_title", return_value="Routed action"), patch.object(
            ProviderClient, "reply", return_value=response,
        ) as reply:
            messages = self.chat.send_message("Create a related contact under my named customer")
        self.assertEqual(messages[-1]["payload"]["state"], "pending")
        names = {tool["name"] for tool in reply.call_args.args[2]}
        self.assertIn("mcp_create_record", names)
        self.assertNotIn("mcp_get_fields", names)
        self.assertNotIn("mcp_search_records", names)
        self.assertNotIn("odoo_chart", names)
        self.assertNotIn(self.partner.name, json.dumps(reply.call_args.kwargs))
        client = ProviderClient("openai", "not-a-real-key")
        with self.assertRaises(AccessError):
            self.chat._dispatch_calls(client, self.config, [], [{
                "name": "mcp_search_records", "arguments": {"arguments_json": '{"model":"res.partner"}'},
            }], [], action_request=True)

    def test_action_planning_gets_schema_without_screen_names(self):
        # Screen names are optional; action planning still receives model and field names
        # (never record values) so the AI does not have to guess field names.
        self.config.screen_metadata = False
        with patch.object(ProviderClient, "requires_action", return_value=True), patch.object(
            ProviderClient, "is_odoo_topic", return_value=True,
        ), patch.object(ProviderClient, "reply", return_value=("Which fields should I set?", [])) as reply:
            self.chat.send_message("Create an Odoo contact")
        planning = next(call for call in reply.call_args_list if "screen_context" in call.kwargs)
        context = planning.kwargs["screen_context"]
        self.assertIn("native_schema", context)
        self.assertNotIn("menus", context)
        self.assertTrue(all(self.env["odoo.ai.assistant.native.mcp"].with_user(self.owner)._catalogue()[tool["name"]]["mutation"]
                            for tool in planning.args[2]))
        self.assertNotIn(self.partner.name, json.dumps(reply.call_args.kwargs))

    def test_sharing_flag_refreshes_saved_cards_and_native_continuation(self):
        self.env["odoo.ai.assistant.message"].sudo().create({
            "conversation_id": self.chat.id, "role": "user", "content": "Find this Odoo contact",
        })
        request = self.chat._native_request("mcp_get_record", {"arguments_json": json.dumps({
            "model": "res.partner", "record_id": self.partner.id, "fields": ["name"],
        })})
        self.assertFalse(self.chat.get_messages()[-1]["payload"]["sharing_allowed"])
        self.config.business_data_sharing = True
        self.assertTrue(self.chat.get_messages()[-1]["payload"]["sharing_allowed"])
        with patch.object(ProviderClient, "reply", return_value=("", [{
            "name": "mcp_update_record", "arguments": {"arguments_json": json.dumps({
                "model": "res.partner", "record_id": self.partner.id, "values": {"phone": "not yet confirmed"},
            })},
        }])) as reply:
            messages = self.chat.share_tool_result(request.id)
            self.assertEqual(messages[-1]["payload"]["state"], "pending")
            self.assertEqual(self.partner.phone, "123")
            self.assertTrue(all(tool["name"].startswith("mcp_") for tool in reply.call_args.args[2]))
        self.config.business_data_sharing = False
        self.assertFalse(self.chat.get_messages()[-1]["payload"]["sharing_allowed"])

    def test_image_transport_for_all_providers(self):
        responses = {
            "openai": {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "Answer"},
            ]}]},
            "anthropic": {"content": [{"type": "text", "text": "Answer"}]},
            "gemini": {"candidates": [{"content": {"parts": [{"text": "Answer"}]}}]},
            "openrouter": {"choices": [{"message": {"content": "Answer"}}]},
        }
        for provider, response in responses.items():
            with self.subTest(provider=provider), patch.object(
                ProviderClient, "_request", return_value=response,
            ) as request:
                client = ProviderClient(provider, "not-a-real-key")
                client.reply("native-test", [{"role": "user", "content": "Odoo attachment"}], [],
                             native_tools=True, tool_images=[{"mimeType": "image/png", "data": "YWJj"}])
                payload = json.dumps(request.call_args.args[2])
                self.assertIn("YWJj", payload)
                self.assertIn("image/png", payload)

    def test_record_rules_are_rechecked_before_read_and_confirmation(self):
        request = self.prepare("mcp_update_record", {
            "model": "res.partner", "record_id": self.partner.id, "values": {"phone": "denied by rule"},
        })
        self.env["ir.rule"].create({
            "name": "Native test hide target", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "domain_force": "[('id', '!=', %s)]" % self.partner.id,
            "perm_read": True, "perm_write": True, "perm_create": False, "perm_unlink": False,
        })
        with self.assertRaises(AccessError):
            self.prepare("mcp_get_record", {"model": "res.partner", "record_id": self.partner.id})
        with self.assertRaises(AccessError):
            self.chat.confirm_tool(request.id)
        self.assertEqual(self.partner.phone, "123")

    def test_created_result_cannot_be_shared_after_read_access_revocation(self):
        request = self.prepare("mcp_create_record", {
            "model": "res.partner", "values": {"name": "Native restricted creation"},
        })
        self.chat.confirm_tool(request.id)
        self.config.business_data_sharing = True
        self.gate.allow_read = False
        with self.assertRaises(AccessError):
            request._sharing_context(self.chat)
        self.gate.allow_read = True
        record_id = request.result["structuredContent"]["record"]["id"]
        self.env["ir.rule"].create({
            "name": "Native hide created result", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "domain_force": "[('id', '!=', %s)]" % record_id,
            "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
        })
        with self.assertRaises(AccessError):
            request._sharing_context(self.chat)

    def test_activity_creation_uses_generic_tools_with_confirmation(self):
        self.env["mcp.enabled.model"].create({
            "model_id": self.env["ir.model"]._get_id("mail.activity"), "allow_read": True,
            "allow_create": True,
        })
        request = self.prepare("mcp_create_record", {
            "model": "mail.activity", "values": {
                "res_model_id": self.env["ir.model"]._get_id("res.partner"),
                "res_id": self.partner.id, "user_id": self.owner.id,
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "Native confirmed follow-up",
                "date_deadline": (fields.Date.today() + timedelta(days=1)).isoformat(),
            },
        })
        self.assertFalse(self.env["mail.activity"].search_count([("summary", "=", "Native confirmed follow-up")]))
        self.chat.confirm_tool(request.id)
        self.assertEqual(self.env["mail.activity"].search_count([("summary", "=", "Native confirmed follow-up")]), 1)
