# Changelog

## 1.8
- Field detection on forms without lines: blank after "Label :", "du … au …", "… heures … semaines", "Le … à …", dotted leaders ("Je soussigné(e) ………").
- Large empty boxes under "Evaluation … :" become multi-line text fields.
- Checkbox labels placed after a tab ("❑ ⇥ DC1").
- Headings, paragraphs, lists and printed values are no longer taken for blanks; faster detection on drawing-heavy PDFs.

## 1.7
- Field detection: labels glued to their line ("Nom____"), dates "__/__/____" split into day / month / year with JJ / MM / AAAA hints, side-by-side fields on the same line.
- Fewer false fields: long separator lines and box borders are no longer taken for fields.

## 1.6
- "Réduire la taille": compress a copy of the PDF, with an optional maximum size (e.g. 2 Mo) and black & white option.
- Compact toolbar that fits 1280 px wide screens.

## 1.5
- French print window: printer, pages, colour / black & white (true grayscale rendering), copies, duplex.

## 1.4
- Field detection completes documents that already contain detected fields; "Chercher d'autres cases" button.

## 1.3
- Detection of Word-made forms: typed underscores, "□" glyph checkboxes, labels on the line above, continuation lines, split date groups, line-level section titles.

## 1.2
- Fill form fields directly on the page (Tab / Enter to move between fields, across pages). Sidebar becomes optional.
- Fix: emptying a field now works.

## 1.1
- Editable, movable, deletable text and check marks (stored as PDF annotations).
- Automatic detection of fields on flattened "print-only" forms.
- Combine dialog with thumbnails and page preview.
- One-click Windows installer (Inno Setup).

## 1.0
- First version: read, fill, sign, rotate / reorder / delete / add pages, combine, print, undo/redo, onboarding.
