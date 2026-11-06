"""Windows-only UI construction/settings checks; dialogs and browser are not opened."""
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

@unittest.skipUnless(sys.platform=='win32','Desktop smoke checks require Windows with Tk')
class DesktopTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from src.app import SalesApp
        self.root=tk.Tk();self.root.withdraw();self.app=SalesApp(self.root)
    def tearDown(self):self.root.destroy()
    def test_human_readable_defaults_and_review_reset(self):
        settings=self.app.gather()
        self.assertEqual(settings.date_format,'%Y-%m-%d');self.assertEqual(settings.discount_unit,'fraction')
        self.assertEqual(settings.invalid_rows,'stop')
        self.app.reviewed.set(True);self.app.settings['decimal'].set(',')
        self.assertFalse(self.app.reviewed.get())
    def test_mapping_change_invalidates_review(self):
        self.app.reviewed.set(True);self.app.mapping['sales'][0].set('Revenue')
        self.assertFalse(self.app.reviewed.get())
    def test_loading_mapping_does_not_add_unreviewed_suggestions(self):
        from src.importing import ImportConfig
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'data.csv';source.write_text('Sales,Profit\n10,2\n',encoding='utf-8')
            mapping=root/'mapping.json';ImportConfig(mapping={'sales':'Sales'},mapping_reviewed=True).save(mapping)
            self.app.source=source
            with patch('src.app.filedialog.askopenfilename',return_value=str(mapping)):
                self.app.load_config()
            self.assertEqual(self.app.gather().mapping,{'sales':'Sales'})
            self.assertTrue(self.app.reviewed.get());self.assertEqual(self.app.mapping['profit'][0].get(),'')

if __name__=='__main__':unittest.main()
