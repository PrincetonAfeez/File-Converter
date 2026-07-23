# "Upload and conversion option forms."
from __future__ import annotations

import uuid

from django import forms

from apps.converters.registry import registry


def _collect_option_fields():
    """Union of every registered converter's option schema, de-duplicated by name.

    Surfaces converter-specific options (image quality, audio bitrate, …) in the upload
    form. Options irrelevant to the chosen converter are dropped by that converter's
    ``option_schema().validate()`` at submit time.
    """
    seen: dict[str, object] = {}
    for registered in registry.all_pairs():
        for field in registered.converter.option_schema().fields:
            seen.setdefault(field.name, field)
    return seen


def conversion_format_maps() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (source -> valid targets, target -> applicable option field names).

    Consumed by the dashboard JS to constrain the target dropdown to the uploaded file's
    type and to show only the options relevant to the chosen target.
    """
    format_targets: dict[str, list[str]] = {}
    target_options: dict[str, list[str]] = {}
    for registered in registry.all_pairs():
        targets = format_targets.setdefault(registered.source, [])
        if registered.target not in targets:
            targets.append(registered.target)
        if registered.target not in target_options:
            target_options[registered.target] = [
                field.name for field in registered.converter.option_schema().fields
            ]
    return {src: sorted(t) for src, t in format_targets.items()}, target_options


class ConversionUploadForm(forms.Form):
    file = forms.FileField()
    target_format = forms.ChoiceField()
    idempotency_key = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        targets = sorted({pair.target for pair in registry.all_pairs()})
        self.fields["target_format"].choices = [(target, target.upper()) for target in targets]

        self._option_names: list[str] = []
        for name, field_def in _collect_option_fields().items():
            self._option_names.append(name)
            self.fields[name] = self._build_field(field_def)

        if not self.initial.get("idempotency_key"):
            self.initial["idempotency_key"] = uuid.uuid4().hex

    @staticmethod
    def _build_field(field_def) -> forms.Field:
        common = {"label": field_def.label, "required": False, "initial": field_def.default}
        if field_def.type == "integer":
            return forms.IntegerField(
                min_value=field_def.minimum, max_value=field_def.maximum, **common
            )
        if field_def.type == "boolean":
            return forms.BooleanField(**common)
        if field_def.type == "choice":
            return forms.ChoiceField(
                choices=[(c, c) for c in field_def.choices], **common
            )
        return forms.CharField(**common)

    @property
    def option_bound_fields(self):
        return [self[name] for name in self._option_names]

    def option_payload(self) -> dict:
        payload: dict = {}
        for name in self._option_names:
            value = self.cleaned_data.get(name)
            if value not in (None, ""):
                payload[name] = value
        return payload
