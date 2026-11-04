from django import forms
from django.utils import timezone


class PublicationDateForm(forms.Form):
    scheduled_at = forms.DateTimeField(
        label="Publication date and time",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "rounded-lg border border-stone-300 px-3 py-2 w-full"},
        ),
    )

    def clean_scheduled_at(self):
        value = self.cleaned_data["scheduled_at"]
        if value <= timezone.now():
            raise forms.ValidationError("Choose a future publication date and time.")
        return value
