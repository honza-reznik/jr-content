from django import forms


class ChannelDetailsForm(forms.Form):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)

    name = forms.CharField(label="Channel name", max_length=100)
    profile_url = forms.URLField(
        label="Profile link (optional)",
        max_length=2000,
        required=False,
        assume_scheme="https",
    )

    def clean_profile_url(self):
        from urllib.parse import urlsplit

        value = self.cleaned_data["profile_url"]
        if value and urlsplit(value).scheme not in {"http", "https"}:
            raise forms.ValidationError("Use a link starting with https:// or http://.")
        return value


class AddChannelForm(ChannelDetailsForm):
    field_order = ["platform", "name", "profile_url"]
    platform = forms.ChoiceField(
        label="Channel type",
        choices=[
            ("instagram", "Instagram"),
            ("facebook", "Facebook"),
            ("linkedin_personal", "LinkedIn · Personal"),
            ("linkedin_company", "LinkedIn · Company"),
            ("tiktok", "TikTok"),
            ("youtube", "YouTube"),
            ("custom", "Other (e.g. Forendors)"),
        ],
    )
