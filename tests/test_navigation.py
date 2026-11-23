from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from sketchpad.model import Document, Paper, save_document, load_document
from sketchpad.navigation import move_node, normalized_navigation, locate


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document()
        self.doc.papers += [Paper(name='Второй'), Paper(name='Третий')]
        self.doc.navigation = normalized_navigation([], self.doc.papers)
        self.doc.navigation.append({'id': 'folder-a', 'type': 'folder', 'name': 'Эскизы', 'children': [
            {'id': 'folder-b', 'type': 'folder', 'name': 'Варианты', 'children': []}]})

    def test_move_and_roundtrip_preserve_stack_and_folder_order(self):
        ids = [p.id for p in self.doc.papers]
        self.assertTrue(move_node(self.doc.navigation, ids[1], 'folder-b'))
        self.assertTrue(move_node(self.doc.navigation, ids[2], None, 0))
        self.assertEqual(ids, [p.id for p in self.doc.papers])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'folders.atelier'
            save_document(self.doc, path)
            loaded, _ = load_document(path)
        self.assertEqual(self.doc.metadata(), loaded.metadata())
        self.assertEqual(locate(loaded.navigation, 'folder-b')[2]['children'][0]['id'], ids[1])

    def test_folder_cannot_move_into_itself_or_its_descendant(self):
        before = deepcopy(self.doc.navigation)
        self.assertFalse(move_node(self.doc.navigation, 'folder-a', 'folder-a'))
        self.assertFalse(move_node(self.doc.navigation, 'folder-a', 'folder-b'))
        self.assertEqual(before, self.doc.navigation)

    def test_new_sheets_appear_without_losing_undo_tombstone(self):
        paper = self.doc.papers.pop(1)
        self.doc.navigation = normalized_navigation(self.doc.navigation, self.doc.papers)
        self.doc.papers.append(paper)
        self.doc.navigation = normalized_navigation(self.doc.navigation, self.doc.papers)
        self.assertIsNotNone(locate(self.doc.navigation, paper.id))
        self.assertEqual(sum(n['id'] == paper.id for n in self.doc.navigation), 1)
