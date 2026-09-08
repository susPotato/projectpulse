"""Files the product hands back: a blank input template, and a status report.

Both are generated from things that already exist - the sheet contracts and the
`InsightBundle` - rather than from a second description of them. Neither module
formats a number: the report renders `Finding.headline`, which the assembler
already substituted, so invariant 1 holds across a .docx as it does on a page.
"""
