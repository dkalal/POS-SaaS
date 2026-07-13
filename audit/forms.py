from django import forms

from audit.models import AuditEvent


class AuditFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    action = forms.ChoiceField(required=False, choices=(("", "All actions"),) + tuple(AuditEvent.Action.choices))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["q"].widget.attrs.update({"placeholder": "Search actor, target, or note"})
        base_class = "app-field"
        self.fields["q"].widget.attrs.setdefault("class", base_class)
        self.fields["action"].widget.attrs.setdefault("class", base_class)
