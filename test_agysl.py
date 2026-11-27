#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agysl


class AgyslTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = os.environ.copy()
        os.environ["AGYSL_NO_COLOR"] = "1"
        os.environ["AGYSL_WIDTH"] = "0"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

    def test_full_payload_rendering(self) -> None:
        payload = {
            "cycle_mode": "accept-edits",
            "model": {
                "id": "gemini-4-hydrogen-high",
                "display_name": "Gemini 4 Hydrogen (High)",
                "effort": "high",
            },
            "context_window": {
                "context_window_size": 1000000,
                "used_percentage": 42.0,
                "total_input_tokens": 30000,
                "total_output_tokens": 4500,
                "current_usage": {
                    "input_tokens": 20000,
                    "cache_read_input_tokens": 180000,
                    "output_tokens": 1200,
                },
            },
            "quota": {
                "gemini-5h": {"remaining_fraction": 0.80, "reset_in_seconds": 12000},
                "gemini-weekly": {"remaining_fraction": 0.60, "reset_in_seconds": 200000},
            },
            "cost": {"total_usd": 0.1234, "estimated": True},
        }
        out = agysl.format_statusline(payload)
        self.assertTrue(out.startswith("ctx "))
        self.assertNotIn("Gemini 4 Hydrogen", out)
        self.assertNotIn("accept-edits", out)
        self.assertIn("42% (200.0k/1.00M)", out)
        self.assertIn("usage 5h/wk", out)
        self.assertIn("80%(3h)/60%(56h)", out)
        self.assertIn("cache ", out)
        self.assertIn("90%", out)
        self.assertIn("day token ", out)

    def test_quota_claude_bucket(self) -> None:
        payload = {
            "mode": "plan",
            "model": {"id": "claude-sonnet", "display_name": "Claude Sonnet", "effort": "medium"},
            "quota": {
                "gemini-5h": {"remaining_fraction": 0.90},
                "gemini-weekly": {"remaining_fraction": 0.90},
                "claude-5h": {"remaining_fraction": 0.50, "reset_in_seconds": 7380},
                "claude-weekly": {"remaining_fraction": 0.25, "reset_in_seconds": 330240},
            },
        }
        out = agysl.format_statusline(payload)
        self.assertIn("50%(2h)/25%(92h)", out)

    def test_quota_remaining_with_reset_time_and_minutes(self) -> None:
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
        payload = {
            "model": {"id": "gemini-4-hydrogen-high", "display_name": "Gemini 4 Hydrogen (High)"},
            "quota": {
                "disabled-5h": {"remaining_fraction": 0.0, "reset_in_seconds": 100, "disabled": True},
                "five_hour": {"remaining_fraction": 0.7196, "reset_in_seconds": 7380},
                "gemini-weekly": {
                    "remaining_fraction": 0.2157,
                    "reset_time": (now + timedelta(hours=91, minutes=44)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        }
        out = agysl.format_statusline(payload, now=now)
        self.assertIn("72%(2h)/22%(92h)", out)

    def test_quota_fallback_by_reset_seconds_and_subhour_minutes(self) -> None:
        payload = {
            "quota": {
                "bucket_a": {"remaining_fraction": 0.65, "reset_in_seconds": 2700},
                "bucket_b": {"remaining_fraction": 0.40, "reset_in_seconds": 86400},
            },
        }
        out = agysl.format_statusline(payload)
        self.assertIn("65%(45m)/40%(24h)", out)

    def test_cache_hit_zero_when_current_usage_has_no_cache_read(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
            tf.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "model": "gemini-4-hydrogen-high",
                "input_tokens": 1000,
                "cache_read_tokens": 9000,
                "output_tokens": 300,
            }) + "\n")
            tpath = tf.name
        try:
            payload = {
                "transcript_path": tpath,
                "context_window": {
                    "context_window_size": 100000,
                    "current_usage": {"input_tokens": 5000, "cache_read_input_tokens": 0},
                },
            }
            out = agysl.format_statusline(payload)
            self.assertRegex(out, r"cache .* 0% │")
        finally:
            os.unlink(tpath)

    def test_find_transcript_does_not_leak_other_session_when_cid_given(self) -> None:
        self.assertIsNone(agysl.find_transcript({"conversation_id": "nonexistent-session-id-xyz"}))
        # Live agy payload with empty conversation_id on brand-new session must not fall back to another session
        self.assertIsNone(agysl.find_transcript({"conversation_id": "", "context_window": {"context_window_size": 1048576}}))

    def test_quota_prefers_generic_gemini_weekly_over_argon_wtu_for_hydrogen(self) -> None:
        payload = {
            "model": {"id": "Gemini 4 Hydrogen (High)", "display_name": "Gemini 4 Hydrogen (High)"},
            "quota": {
                "3p-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 17823},
                "3p-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 604623},
                "gemini-5h": {"remaining_fraction": 0.5729523, "reset_in_seconds": 6008},
                "gemini-argon-wtu-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 604623},
                "gemini-weekly": {"remaining_fraction": 0.16663404, "reset_in_seconds": 328868},
            },
        }
        out = agysl.format_statusline(payload)
        self.assertIn("57%(2h)/17%(91h)", out)

        argon_payload = dict(payload, model={"id": "Gemini 4 Argon (High)", "display_name": "Gemini 4 Argon (High)"})
        argon_out = agysl.format_statusline(argon_payload)
        self.assertIn("57%(2h)/100%(168h)", argon_out)

    def test_quota_orchestrator_gemini_variants(self) -> None:
        base_quota = {
            "3p-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 17823},
            "3p-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 604623},
            "gemini-5h": {"remaining_fraction": 0.5729523, "reset_in_seconds": 6008},
            "gemini-argon-wtu-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 604623},
            "gemini-weekly": {"remaining_fraction": 0.16663404, "reset_in_seconds": 328868},
        }
        for label in ("Orchestrator (Neon)", "Orchestrator (Hydrogen)"):
            out = agysl.format_statusline({
                "model": {"id": label, "display_name": label},
                "quota": base_quota,
            })
            self.assertIn("57%(2h)/17%(91h)", out)

        argon_out = agysl.format_statusline({
            "model": {"id": "Orchestrator (Argon)", "display_name": "Orchestrator (Argon)"},
            "quota": base_quota,
        })
        self.assertIn("57%(2h)/100%(168h)", argon_out)

    def test_quota_orchestrator_3p_variants(self) -> None:
        base_quota = {
            "3p-5h": {"remaining_fraction": 0.42, "reset_in_seconds": 3600},
            "3p-weekly": {"remaining_fraction": 0.33, "reset_in_seconds": 172800},
            "gemini-5h": {"remaining_fraction": 0.90, "reset_in_seconds": 7200},
            "gemini-weekly": {"remaining_fraction": 0.80, "reset_in_seconds": 259200},
        }
        for label in ("Orchestrator (Opus)", "Orchestrator (GPT-5)", "Orchestrator (o3)"):
            out = agysl.format_statusline({
                "model": {"id": label, "display_name": label},
                "quota": base_quota,
            })
            self.assertIn("42%(1h)/33%(48h)", out)

    def test_quota_orchestrator_generic_label_fallbacks(self) -> None:
        base_quota = {
            "3p-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 17823},
            "3p-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 604623},
            "gemini-5h": {"remaining_fraction": 0.5729523, "reset_in_seconds": 6008},
            "gemini-argon-wtu-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 604623},
            "gemini-weekly": {"remaining_fraction": 0.16663404, "reset_in_seconds": 328868},
        }
        # 1. Fallback to model.id when display_name is generic "Orchestrator"
        out_via_id = agysl.format_statusline({
            "model": {"display_name": "Orchestrator", "id": "gemini-4-neon-high"},
            "quota": base_quota,
        })
        self.assertIn("57%(2h)/17%(91h)", out_via_id)

        # 2. Fallback to settings.json orchestrator config
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = os.path.join(tmpdir, "settings.json")
            with open(settings_path, "w", encoding="utf-8") as sf:
                json.dump({
                    "orchestrator": {
                        "planner": "Gemini 4 Neon (High)",
                        "executor": "Gemini 4 Hydrogen (High)",
                    }
                }, sf)
            os.environ["AGY_SETTINGS_FILE"] = settings_path
            out_via_settings = agysl.format_statusline({
                "model": {"display_name": "Orchestrator", "id": "Orchestrator"},
                "quota": base_quota,
            })
            self.assertIn("57%(2h)/17%(91h)", out_via_settings)

            # 3. Fallback to transcript.jsonl when settings file is absent
            os.environ["AGY_SETTINGS_FILE"] = os.path.join(tmpdir, "missing.json")
            tpath = os.path.join(tmpdir, "transcript.jsonl")
            with open(tpath, "w", encoding="utf-8") as tf:
                tf.write(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "model": "gemini-4-neon-high",
                    "input_tokens": 100,
                    "cache_read_tokens": 900,
                    "output_tokens": 50,
                }) + "\n")
            out_via_transcript = agysl.format_statusline({
                "model": {"display_name": "Orchestrator", "id": "Orchestrator"},
                "transcript_path": tpath,
                "quota": base_quota,
            })
            self.assertIn("57%(2h)/17%(91h)", out_via_transcript)

    def test_model_config_registry_pricing_and_family(self) -> None:
        # Pricing lookups across Gemini, Claude, OpenAI, display names, and Orchestrator labels
        self.assertEqual(agysl.resolve_model_price("gemini-4-neon-high"), {"input": 3.00, "cache": 0.15, "output": 15.00})
        self.assertEqual(agysl.resolve_model_price("Gemini 4 Carbon (High)"), {"input": 2.00, "cache": 0.10, "output": 10.00})
        self.assertEqual(agysl.resolve_model_price("Orchestrator (Neon)"), {"input": 3.00, "cache": 0.15, "output": 15.00})
        self.assertEqual(agysl.resolve_model_price("gemini-3.5-flash-lite"), {"input": 0.30, "cache": 0.03, "output": 2.50})
        self.assertEqual(agysl.resolve_model_price("Claude Opus 4 (Thinking)"), {"input": 15.00, "cache": 1.50, "output": 75.00})
        self.assertEqual(agysl.resolve_model_price("claude-sonnet-5"), {"input": 2.00, "cache": 0.20, "output": 10.00})
        self.assertEqual(agysl.resolve_model_price("gpt-5"), {"input": 1.25, "cache": 0.125, "output": 10.00})
        self.assertEqual(agysl.resolve_model_price("o3-pro"), {"input": 2.00, "cache": 0.50, "output": 8.00})
        self.assertEqual(agysl.resolve_model_price("unknown-future-model"), agysl.DEFAULT_MODEL_PRICE)

        # Quota family resolution including precedence ("o3-pro" -> openai, NOT gemini "pro")
        self.assertEqual(agysl.infer_preferred_quota_prefixes("o3-pro"), ["gpt", "openai", "claude", "3p"])
        self.assertEqual(agysl.infer_preferred_quota_prefixes("Orchestrator (Pro)"), ["gemini"])
        self.assertEqual(agysl.infer_preferred_quota_prefixes("Claude Sonnet 4"), ["claude", "anthropic", "3p"])
        # Unlisted future models fall back cleanly via QUOTA_FAMILIES
        self.assertEqual(agysl.infer_preferred_quota_prefixes("Gemini 5 Xenon"), ["gemini"])
        self.assertEqual(agysl.infer_preferred_quota_prefixes("Claude 6 Ultra"), ["claude", "anthropic", "3p"])
        self.assertEqual(agysl.infer_preferred_quota_prefixes("GPT-6"), ["gpt", "openai", "claude", "3p"])

    def test_adding_single_model_config_entry_enables_pricing_and_quota(self) -> None:
        agysl.MODEL_CONFIGS["gemini-5-krypton"] = {
            "match": ("gemini-5-krypton",),
            "tokens": ("krypton",),
            "family": "gemini",
            "quota_variants": ("krypton",),
            "price": {"input": 4.00, "cache": 0.20, "output": 20.00},
        }
        try:
            cost = agysl.calc_cost_usd(1_000_000, 1_000_000, 1_000_000, "gemini-5-krypton-high")
            self.assertAlmostEqual(cost, 24.20, places=4)

            out = agysl.format_statusline({
                "model": {"id": "Orchestrator (Krypton)", "display_name": "Orchestrator (Krypton)"},
                "quota": {
                    "3p-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 3600},
                    "3p-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 86400},
                    "gemini-5h": {"remaining_fraction": 0.55, "reset_in_seconds": 3600},
                    "gemini-weekly": {"remaining_fraction": 0.44, "reset_in_seconds": 86400},
                    "gemini-krypton-wtu-weekly": {"remaining_fraction": 0.88, "reset_in_seconds": 172800},
                },
            })
            self.assertIn("55%(1h)/88%(48h)", out)
        finally:
            agysl.MODEL_CONFIGS.pop("gemini-5-krypton", None)

    def test_transcript_fallback_for_cache_hit_and_context(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
            tf.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "model": "gemini-4-hydrogen-high",
                "input_tokens": 1000,
                "cache_read_tokens": 9000,
                "output_tokens": 300,
            }) + "\n")
            tpath = tf.name
        try:
            os.environ["AGYSL_SHOW_TOKENS"] = "1"
            payload = {
                "transcript_path": tpath,
                "context_window": {"context_window_size": 100000},
            }
            out = agysl.format_statusline(payload)
            self.assertTrue(out.startswith("ctx "))
            self.assertIn("10% (10.0k/100.0k)", out)
            self.assertIn("cache ", out)
            self.assertIn("90%", out)
            self.assertIn("day token ", out)
            self.assertIn("tok 1.0k+9.0k/300", out)
        finally:
            os.unlink(tpath)

    def test_fmt_daily_tokens_m_and_b(self) -> None:
        self.assertEqual(agysl.fmt_daily_tokens(0), "0.00m")
        self.assertEqual(agysl.fmt_daily_tokens(240_000), "0.24m")
        self.assertEqual(agysl.fmt_daily_tokens(12_340_000), "12.3m")
        self.assertEqual(agysl.fmt_daily_tokens(250_000_000), "250m")
        self.assertEqual(agysl.fmt_daily_tokens(999_000_000), "999m")
        self.assertEqual(agysl.fmt_daily_tokens(1_000_000_000), "1.00b")
        self.assertEqual(agysl.fmt_daily_tokens(1_250_000_000), "1.25b")
        self.assertEqual(agysl.fmt_daily_tokens(12_500_000_000), "12.5b")

    def test_compute_daily_tokens_filters_today_and_renders_after_hit(self) -> None:
        now = datetime.now().astimezone()
        today_utc = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        yesterday_utc = (now - timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = os.path.join(tmpdir, "s1", ".system_generated", "logs")
            s2 = os.path.join(tmpdir, "s2", ".system_generated", "logs")
            os.makedirs(s1)
            os.makedirs(s2)
            t1 = os.path.join(s1, "transcript.jsonl")
            t2 = os.path.join(s2, "transcript.jsonl")

            with open(t1, "w", encoding="utf-8") as f:
                # Yesterday entry should be ignored
                f.write(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "created_at": yesterday_utc,
                    "input_tokens": 9_000_000,
                    "cache_read_tokens": 0,
                    "output_tokens": 0,
                }) + "\n")
                # Today entry: 600M tokens (gemini-4-hydrogen-high: 100M*1.50 + 490M*0.075 + 10M*7.50 = 261.75$)
                f.write(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "model": "gemini-4-hydrogen-high",
                    "created_at": today_utc,
                    "input_tokens": 100_000_000,
                    "cache_read_tokens": 490_000_000,
                    "output_tokens": 10_000_000,
                }) + "\n")

            with open(t2, "w", encoding="utf-8") as f:
                # Today entry in another session: 500M tokens -> total 1.10B
                # (50M*1.50 + 440M*0.075 + 10M*7.50 = 183.00$, total = 444.75$)
                f.write(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "model": "gemini-4-hydrogen-high",
                    "created_at": today_utc,
                    "input_tokens": 50_000_000,
                    "cache_read_tokens": 440_000_000,
                    "output_tokens": 10_000_000,
                }) + "\n")

            os.environ["AGYSL_BRAIN_GLOB"] = os.path.join(tmpdir, "*", ".system_generated", "logs", "transcript.jsonl")
            cache_file = os.path.join(tmpdir, "cache.json")
            total, cost_usd = agysl.compute_daily_usage(now=now, cache_path=cache_file)
            self.assertEqual(total, 1_100_000_000)
            self.assertAlmostEqual(cost_usd, 444.75, places=2)

            payload = {
                "transcript_path": t1,
                "context_window": {
                    "context_window_size": 1000000,
                    "used_percentage": 50,
                    "current_usage": {"input_tokens": 1000, "cache_read_input_tokens": 9000},
                },
            }
            out = agysl.format_statusline(payload, now=now)
            self.assertRegex(out, r"cache .* 90% │ day token 1\.10b\(444\.75\$\)")

    def test_four_sections_and_safe_colors(self) -> None:
        os.environ.pop("AGYSL_NO_COLOR", None)
        os.environ.pop("NO_COLOR", None)
        payload = {
            "context_window": {
                "context_window_size": 1000000,
                "used_percentage": 50,
                "current_usage": {"input_tokens": 1000, "cache_read_input_tokens": 9000},
            },
            "quota": {
                "gemini-5h": {"remaining_fraction": 0.75},
                "gemini-weekly": {"remaining_fraction": 0.50},
            },
        }
        out = agysl.format_statusline(payload)
        self.assertEqual(out.count("│"), 3)
        for forbidden in ("\033[30m", "\033[90m", "\033[35m", "\033[95m", "\033[32m", "\033[92m"):
            self.assertNotIn(forbidden, out)
        self.assertIn(agysl.CTX_COLOR, out)
        self.assertIn(agysl.USAGE_COLOR, out)
        self.assertIn(agysl.CACHE_COLOR, out)
        self.assertIn(agysl.DAY_COLOR, out)

    def test_cli_empty_stdin_exits_zero(self) -> None:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agysl.py")
        proc = subprocess.run(
            [sys.executable, script],
            input="",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.strip())

    def test_terminal_width_even_distribution_and_narrow_fit(self) -> None:
        os.environ.pop("AGYSL_WIDTH", None)
        payload = {
            "terminal_width": 130,
            "context_window": {
                "context_window_size": 1048576,
                "used_percentage": 16.0,
                "current_usage": {"input_tokens": 7743, "cache_read_input_tokens": 155681},
            },
            "quota": {
                "gemini-5h": {"remaining_fraction": 0.7196, "reset_in_seconds": 7380},
                "gemini-weekly": {"remaining_fraction": 0.2157, "reset_in_seconds": 330240},
            },
        }
        wide_out = agysl.format_statusline(payload)
        self.assertNotIn("\n", wide_out)
        self.assertEqual(agysl.visible_width(wide_out), 130)
        # Separators should have expanded spacing to distribute the 4 sections evenly
        self.assertIn("   │   ", wide_out)

        narrow_payload = dict(payload, terminal_width=88)
        narrow_out = agysl.format_statusline(narrow_payload)
        self.assertNotIn("\n", narrow_out)
        self.assertLessEqual(agysl.visible_width(narrow_out), 88)
        self.assertEqual(narrow_out.count("│"), 3)

    def test_install_script_copies_single_file_and_updates_existing(self) -> None:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        install_sh = os.path.join(project_dir, "install.sh")
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = os.path.join(tmpdir, "settings.json")
            installed_script = os.path.join(tmpdir, "agysl.py")
            with open(installed_script, "w", encoding="utf-8") as f:
                f.write("# stale version\n")

            env = os.environ.copy()
            env["AGY_SETTINGS_FILE"] = settings_path
            proc = subprocess.run(["bash", install_sh], env=env, text=True, capture_output=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(os.path.isfile(installed_script))

            with open(installed_script, "r", encoding="utf-8") as f1, open(
                os.path.join(project_dir, "agysl.py"), "r", encoding="utf-8"
            ) as f2:
                self.assertEqual(f1.read(), f2.read())

            with open(settings_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg["statusLine"]["command"], f"python3 {installed_script}")
            self.assertTrue(cfg["statusLine"]["stack_with_default"])

            # Verify uninstall removes both statusLine and copied script
            proc_un = subprocess.run(
                ["bash", install_sh, "--uninstall"], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(proc_un.returncode, 0, proc_un.stderr)
            self.assertFalse(os.path.exists(installed_script))
            with open(settings_path, "r", encoding="utf-8") as f:
                cfg_after = json.load(f)
            self.assertNotIn("statusLine", cfg_after)


if __name__ == "__main__":
    unittest.main()
