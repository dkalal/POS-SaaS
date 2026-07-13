from django import forms


class APIKeyForm(forms.Form):
    label = forms.CharField(max_length=150, label="Key label")
    can_view_cost = forms.BooleanField(required=False, initial=False, label="Allow cost price access")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["label"].widget.attrs.update(
            {
                "class": "app-field",
                "placeholder": "ERP bridge",
            }
        )
        self.fields["notes"].widget.attrs.update(
            {
                "class": "app-field app-textarea",
                "placeholder": "Optional notes about this integration",
            }
        )
