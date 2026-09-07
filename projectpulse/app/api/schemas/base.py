"""The base every response model uses.

Pydantic treats a field with a default as *not required* in JSON Schema, which is
correct for a request body and wrong for a response: `Field(default_factory=list)`
guarantees the key is present on every object we serialise. Generated TypeScript
therefore typed `bundle.steps` as `ForwardStep[] | undefined`, and the front end
had to litter `?? []` over fields that can never be absent.

`json_schema_serialization_defaults_required` fixes it where the mistake is - in
the schema - so the generated types describe what the server actually sends.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Response(BaseModel):
    """A model that is only ever serialised outward."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
