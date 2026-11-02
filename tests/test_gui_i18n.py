import string
import tempfile
import unittest
from pathlib import Path

from gui_i18n import LANGUAGES, TEXT
from gui import App, THEMES, load_language, load_preferences


class GuiTranslationsTests(unittest.TestCase):
    def test_languages_have_matching_keys_and_placeholders(self):
        reference = TEXT["ru"]
        formatter = string.Formatter()
        self.assertEqual(set(LANGUAGES.values()), set(TEXT))
        for language, messages in TEXT.items():
            with self.subTest(language=language):
                self.assertEqual(set(reference), set(messages))
                for key, message in messages.items():
                    fields = {name for _, name, _, _ in formatter.parse(message) if name}
                    expected = {name for _, name, _, _ in formatter.parse(reference[key]) if name}
                    self.assertEqual(fields, expected, f"{language}: {key}")

    def test_language_preference_handles_invalid_and_valid_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preferences.json"
            self.assertEqual(load_language(path), "Русский")
            path.write_text('{"language":"Türkçe"}', encoding="utf-8")
            self.assertEqual(load_language(path), "Türkçe")
            path.write_text('{"language":"English","theme":"dark"}', encoding="utf-8")
            self.assertEqual(load_preferences(path)["theme"], "dark")
            path.write_text("{invalid", encoding="utf-8")
            self.assertEqual(load_language(path), "Русский")

    def test_theme_palettes_have_matching_color_keys(self):
        self.assertEqual(set(THEMES), {"light", "dark", "sepia"})
        for palette in THEMES.values():
            self.assertEqual(set(palette), set(THEMES["light"]))

    def test_recognised_log_is_translated_and_unknown_error_preserved(self):
        class Translator:
            def tr(self, key, **kwargs):
                return TEXT["en"][key].format(**kwargs)

        app = Translator()
        saved = "2026-11-02 12:00:00,123 INFO Saved fragment 123:1:2 (3/10)\n"
        unknown = "2026-11-02 12:00:00,123 ERROR Something unexpected\n"
        self.assertIn("Saved fragment 123:1:2 (3/10)", App.translate_log(app, saved))
        self.assertEqual(App.translate_log(app, unknown), unknown)


if __name__ == "__main__":
    unittest.main()
