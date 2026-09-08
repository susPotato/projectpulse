"""Machine learning, kept at arm's length.

One module, one model, and a hard rule: nothing under `app/intelligence/` may
import anything here. The intelligence layer produces findings by arithmetic
over dates a human typed, and that claim is only worth making if an estimate
cannot leak into it. `tests/test_ml.py` enforces the rule by walking imports
rather than trusting this docstring.
"""
