import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from odoo.exceptions import UserError, ValidationError

from ..models.conversation import AIConversation
from ..models.native_mcp import NativeMCP, _check_schema
from ..models.providers import ProviderClient, _prompt_json
from ..models import tools


class TestModelHelpers(TestCase):
    def test_provider_catalogues_keep_only_conversation_models(self):
        cases = {
            "openai": (
                ["gpt-3.5-turbo", "gpt-3.5-turbo-0125", "gpt-4o", "gpt-4.1-mini",
                 "gpt-5-pro", "o3", "o4-mini", "codex-mini-latest", "ft:gpt-4o-mini:org:custom:123"],
                ["text-embedding-3-small", "whisper-1", "tts-1", "dall-e-3", "gpt-image-1",
                 "gpt-4o-realtime-preview", "gpt-4o-audio-preview", "gpt-4o-mini-transcribe",
                 "gpt-4o-search-preview", "gpt-3.5-turbo-instruct", "babbage-002",
                 "davinci-002", "omni-moderation-latest", "sora-2", "unknown-model"],
            ),
            "anthropic": (
                ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5-20251001"],
                ["embedding-model", "unknown-model"],
            ),
            "gemini": (
                ["gemini-2.5-flash", "gemini-3.6-flash", "gemma-3-27b-it",
                 "gemini-3.1-pro-preview-customtools"],
                ["gemini-embedding-001", "gemini-2.5-flash-image",
                 "gemini-2.5-flash-preview-tts", "gemini-3.8-flash-lite-tts",
                 "gemini-3.5-transcribe", "gemini-omni-1.1-flash", "gemini-omni-flash-preview",
                 "gemini-2.5-computer-use-preview-10-2025", "gemini-nano-banana-2.1",
                 "gemini-live-2.5-flash", "imagen-4.0-generate"],
            ),
        }
        for provider, (accepted, rejected) in cases.items():
            items = [{"id": name, "name": name} for name in accepted + rejected]
            if provider == "gemini":
                items = [{"name": "models/" + name, "displayName": name,
                          "supportedGenerationMethods": ["generateContent"]}
                         for name in accepted + rejected]
                items.append({"name": "models/gemini-no-generation",
                              "supportedGenerationMethods": ["embedContent"]})
            key = "models" if provider == "gemini" else "data"
            with self.subTest(provider=provider), patch.object(
                ProviderClient, "_request", return_value={key: items},
            ):
                self.assertEqual(ProviderClient(provider, "dummy").models(),
                                 [(name, name) for name in accepted])

    def test_openrouter_catalogue_requires_text_input_and_output(self):
        items = [
            {"id": "vendor/chat", "name": "Chat", "architecture": {
                "input_modalities": ["text", "image"], "output_modalities": ["text"]}},
            {"id": "vendor/image-generator", "name": "Image generator", "architecture": {
                "input_modalities": ["text"], "output_modalities": ["text", "image"]}},
            {"id": "vendor/music", "architecture": {
                "input_modalities": ["text"], "output_modalities": ["text", "audio"]}},
            {"id": "openrouter/auto", "name": "Auto Router", "architecture": {
                "input_modalities": ["text", "image"], "output_modalities": ["text", "image"]}},
            {"id": "vendor/image", "architecture": {
                "input_modalities": ["text"], "output_modalities": ["image"]}},
            {"id": "vendor/speech", "architecture": {
                "input_modalities": ["audio"], "output_modalities": ["text"]}},
            {"id": "vendor/embedding", "architecture": {
                "input_modalities": ["text"], "output_modalities": ["embeddings"]}},
            {"id": "vendor/chat:batch", "architecture": {
                "input_modalities": ["text"], "output_modalities": ["text"]}},
            *[{"id": name, "architecture": {
                "input_modalities": ["text"], "output_modalities": ["text"]}}
              for name in ("openai/gpt-3.5-turbo-instruct", "meta-llama/llama-guard-4-12b",
                           "nvidia/nemotron-3.5-content-safety:free", "openai/gpt-oss-safeguard-20b",
                           "tencent/hy-mt2-7b", "morph/morph-v3-fast", "relace/relace-apply-3",
                           "relace/relace-search", "inference-net/schematron-v2-small",
                           "bytedance/ui-tars-1.5-7b", "openrouter/bodybuilder")],
            {"id": "vendor/unknown"},
        ]
        with patch.object(ProviderClient, "_request", return_value={"data": items}):
            self.assertEqual(ProviderClient("openrouter", "dummy").models(),
                             [("vendor/chat", "Chat"), ("openrouter/auto", "Auto Router")])

    def test_model_catalogue_invalid_entries_fail_explicitly(self):
        for provider, item in (("openai", None), ("openai", {"id": 42}),
                               ("gemini", {"name": 42})):
            with self.subTest(provider=provider, item=item), patch.object(
                ProviderClient, "_request",
                return_value={"models" if provider == "gemini" else "data": [item]},
            ), self.assertRaises(UserError):
                ProviderClient(provider, "dummy").models()

    def test_prompt_json_is_smaller_without_changing_values(self):
        value = {"menus": [{"name": "Caf\u00e9", "fields": ["name", "stage_id"]}],
                 "approved": {"note": "Keep spaces inside this string", "amount": 15.5}}
        compact = _prompt_json(value)
        self.assertEqual(json.loads(compact), value)
        self.assertLess(len(compact), len(json.dumps(value, ensure_ascii=False)))
        self.assertIn("Keep spaces inside this string", compact)
        self.assertNotIn(": ", compact)

    def test_request_summaries_redact_all_values_and_cover_provider_shapes(self):
        secret = "PRIVATE_CUSTOMER_KEY"
        schema = {"type": "object", "properties": {secret: {"type": "string"}}}
        tool = {"name": secret, "description": secret, "parameters": schema}
        image = {"type": "input_image", "image_url": secret}
        shapes = {
            "responses": {"instructions": secret, "input": [
                {"role": "user", "content": [{"type": "input_text", "text": secret}, image]},
            ], "tools": [tool]},
            "chat": {"messages": [
                {"role": "system", "content": secret}, {"role": "user", "content": secret},
            ], "tools": [{"type": "function", "function": tool}]},
            "anthropic": {"system": secret, "messages": [{"role": "user", "content": secret}],
                          "tools": [{"name": secret, "description": secret, "input_schema": schema}]},
            "gemini": {"systemInstruction": {"parts": [{"text": secret}]},
                       "contents": [{"role": "user", "parts": [{"text": secret}]}],
                       "tools": [{"functionDeclarations": [{
                           "name": secret, "description": secret, "parametersJsonSchema": schema,
                       }]}]},
        }
        for name, payload in shapes.items():
            payload["model"] = secret
            with self.subTest(name=name):
                summary = ProviderClient._request_summary(payload)
                self.assertNotIn(secret, json.dumps(summary))
                self.assertEqual(summary["instructions_chars"], len(secret))
                self.assertEqual(summary["dialogue_chars"], len(secret))
                self.assertEqual(summary["tool_count"], 1)
                self.assertEqual(summary["tools"][0]["description_chars"], len(secret))
                self.assertEqual(summary["tools"][0]["schema_json_chars"], len(_prompt_json(schema)))
                self.assertEqual(summary["payload_json_chars"], len(_prompt_json(payload)))
                self.assertEqual(summary["image_count"], int(name == "responses"))
        self.assertEqual(ProviderClient._request_summary(None)["tool_count"], 0)

    def test_request_diagnostic_is_logged_before_http_call_without_secrets(self):
        secret = "PRIVATE_CUSTOMER_KEY"
        response = Mock(status_code=200)
        response.json.return_value = {}
        with patch("odoo.addons.odoo_ai_assistant.models.providers._logger") as logger:
            def send(*args, **kwargs):
                logger.info.assert_called_once()
                self.assertNotIn(secret, str(logger.info.call_args))
                self.assertEqual(kwargs["json"]["messages"][0]["content"], secret)
                return response
            with patch("requests.request", side_effect=send):
                ProviderClient("openai", secret)._request("POST", "/chat/completions", {
                    "model": secret, "messages": [{"role": "user", "content": secret}],
                })

    def test_schema_error_names_unexpected_property(self):
        schema = {
            "type": "object", "properties": {"model": {"type": "string"}, "groupby": {"type": "array"}},
            "required": ["model", "groupby"], "additionalProperties": False,
        }
        with self.assertRaises(ValidationError) as caught:
            _check_schema(schema, {"model": "crm.lead", "groupby": ["stage_id"], "group_by": "stage_id"})
        self.assertIn("'group_by' was unexpected", str(caught.exception))

    def test_conversation_lock_handles_missing_and_denied_results(self):
        for result in (None, (), (False,)):
            cursor = Mock()
            cursor.fetchone.return_value = result
            conversation = SimpleNamespace(env=SimpleNamespace(cr=cursor), id=42)
            with self.subTest(result=result), self.assertRaises(UserError):
                AIConversation._lock_conversation(conversation, "Conversation is busy.")
            cursor.execute.assert_called_once_with(
                "SELECT pg_try_advisory_xact_lock(%s, %s)", (190019, 42),
            )

    def test_conversation_lock_accepts_acquired_lock(self):
        cursor = Mock()
        cursor.fetchone.return_value = (True,)
        conversation = SimpleNamespace(env=SimpleNamespace(cr=cursor), id=42)
        AIConversation._lock_conversation(conversation, "Conversation is busy.")

    def test_model_entries_preserve_valid_metadata(self):
        entries = [{"model": "res.partner", "name": "Contact"}]
        executor = SimpleNamespace(list_models=Mock(return_value={"structuredContent": {"models": entries}}))
        self.assertIs(NativeMCP._model_entries(executor), entries)

    def test_model_entries_reject_invalid_metadata(self):
        for result in ({}, {"structuredContent": []}, {"structuredContent": {"models": None}}):
            executor = SimpleNamespace(list_models=Mock(return_value=result))
            with self.subTest(result=result), self.assertRaises(UserError):
                NativeMCP._model_entries(executor)

    def test_numeric_and_monetary_chart_labels(self):
        for measure_type, currency_rows, expected_label in (
            ("float", [], "Amount"),
            ("monetary", [], "Amount"),
            ("monetary", [(SimpleNamespace(name="USD"), 1)], "Amount (USD)"),
        ):
            model = Mock()
            model.fields_get.return_value = {
                "city": {"string": "City", "type": "char", "groupable": True},
                "amount": {"string": "Amount", "type": measure_type, "groupable": True, "aggregator": "sum"},
            }
            model._fields = {"amount": Mock(**{"get_currency_field.return_value": "currency_id"})}
            model._read_group.return_value = currency_rows
            bridge = Mock()
            # Odoo returns False for a group with nothing to add up; it is drawn as zero.
            bridge._execute.return_value = {"rows": [{"label": "Alpha", "value": 10}, {"label": "Beta", "value": False}],
                                            "truncated": False}
            env = {"res.partner": model, "odoo.ai.assistant.mcp": bridge}
            with self.subTest(measure_type=measure_type, currency_rows=currency_rows), patch.object(
                tools, "chart_models", return_value=["res.partner"],
            ):
                payload = tools.execute_chart(env, {
                    "model": "res.partner", "group_by": "city", "operation": "sum", "measure": "amount",
                    "chart_type": "bar", "title": "Totals",
                })
            self.assertEqual(payload["value_label"], expected_label)
            self.assertEqual(payload["values"], [10, 0])
            self.assertEqual(bridge._execute.call_args.args[1]["measure"], "amount:sum")
