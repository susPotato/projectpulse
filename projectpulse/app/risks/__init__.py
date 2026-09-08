"""The risk register: a PM's own judgement, tracked and CRUD-edited.

Deliberately separate from `app.intelligence` - see `app/models/domain.py`'s
`Risk` docstring. Nothing here feeds a rule, and nothing in `intelligence/`
may import this package; there is no test walking that boundary the way
`tests/test_ml.py` walks the `app.ml` one, because nothing here has a reason
to be imported from there in the first place.
"""

from __future__ import annotations
