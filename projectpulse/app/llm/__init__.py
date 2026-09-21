"""Where the language-model *configuration* lives, as opposed to the adapters.

`app/narration/providers.py` owns how to talk to each vendor. This package owns
the three questions that sit above that and used to have no home at all:

- which model does each feature use (`features.py`),
- where is the key kept and who may change it (`keys.py`, `admin.py`),
- what did it all cost (`usage.py`, `pricing.py`).

Before this package, the answer to the first was "whatever the single global
narration setting says", fanned out to four `ModelConfig(...)` call sites in
`app/api/main.py` that each re-derived it slightly differently; and the answer
to the third was nothing at all, because every adapter discarded
`response.usage`.
"""
